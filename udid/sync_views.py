"""
Vistas para sincronizar datos desde PanAccess.

Endpoints que ejecutan procesos de sincronización completos de suscriptores, smartcards y credenciales de login.
"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from .utils.panaccess.subscriber import (
    sync_subscribers,
    fetch_all_subscribers,
    download_subscribers_since_last,
    compare_and_update_all_subscribers,
    DataBaseEmpty,
    LastSubscriber
)
from .utils.panaccess.smartcard import (
    sync_smartcards,
    fetch_all_smartcards,
    download_smartcards_since_last,
    compare_and_update_all_smartcards,
    DataBaseEmpty as SmartcardsDataBaseEmpty,
    LastSmartcard
)
from .utils.panaccess.login import (
    sync_subscriber_logins,
    fetch_all_logins_from_panaccess,
    fetch_new_logins_from_panaccess,
    compare_and_update_all_existing,
    DataBaseEmpty as LoginsDataBaseEmpty,
    LastSubscriberLoginInfo
)
from .utils.panaccess.exceptions import PanaccessException

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def sync_subscribers_view(request):
    """
    Vista para sincronizar suscriptores desde PanAccess.
    
    Parámetros opcionales (GET o POST):
    - mode: 'full' (descarga completa), 'incremental' (solo nuevos), 
            'update' (solo actualizar existentes), 'sync' (completo - default)
    - limit: Cantidad de registros por página (default: 100)
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            mode = request.query_params.get('mode', 'sync')
            limit = int(request.query_params.get('limit', 100))
        else:
            mode = request.data.get('mode', 'sync')
            limit = int(request.data.get('limit', 100))
        
        # Validar limit
        if limit > 1000:
            limit = 1000
            logger.warning("Limit ajustado a 1000 (máximo permitido)")
        
        logger.info(f"🔄 Iniciando sincronización de suscriptores - Modo: {mode}, Limit: {limit}")
        
        # Ejecutar según el modo
        if mode == 'full':
            logger.info("📥 Modo: Descarga completa")
            result = fetch_all_subscribers(session_id=None, limit=limit)
            message = "Descarga completa de suscriptores completada"
            
        elif mode == 'incremental':
            logger.info("📥 Modo: Descarga incremental (solo nuevos)")
            if DataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. Use mode=full para descarga completa.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            result = download_subscribers_since_last(session_id=None, limit=limit)
            message = "Descarga incremental de suscriptores completada"
            
        elif mode == 'update':
            logger.info("🔄 Modo: Actualización de existentes")
            if DataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. No hay registros para actualizar.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            compare_and_update_all_subscribers(session_id=None, limit=limit)
            result = None
            message = "Actualización de suscriptores existentes completada"
            
        else:  # mode == 'sync' (default)
            logger.info("🔄 Modo: Sincronización completa (nuevos + actualización)")
            result = sync_subscribers(session_id=None, limit=limit)
            message = "Sincronización completa de suscriptores completada"
        
        # Obtener estadísticas
        last_subscriber = LastSubscriber()
        last_code = last_subscriber.code if last_subscriber else None
        
        logger.info(f"✅ {message}")
        
        return Response({
            'success': True,
            'message': message,
            'mode': mode,
            'limit_used': limit,
            'last_subscriber_code': last_code,
            'database_empty': DataBaseEmpty(),
            'result': result if result is not None else 'update_completed'
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        error_msg = f"Error de PanAccess: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        error_msg = f"Error inesperado: {str(e)}"
        logger.error(f"💥 {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def sync_smartcards_view(request):
    """
    Vista para sincronizar smartcards desde PanAccess.
    
    Parámetros opcionales (GET o POST):
    - mode: 'full' (descarga completa), 'incremental' (solo nuevos), 
            'update' (solo actualizar existentes), 'sync' (completo - default)
    - limit: Cantidad de registros por página (default: 100)
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            mode = request.query_params.get('mode', 'sync')
            limit = int(request.query_params.get('limit', 100))
        else:
            mode = request.data.get('mode', 'sync')
            limit = int(request.data.get('limit', 100))
        
        # Validar limit
        if limit > 1000:
            limit = 1000
            logger.warning("Limit ajustado a 1000 (máximo permitido)")
        
        logger.info(f"🔄 Iniciando sincronización de smartcards - Modo: {mode}, Limit: {limit}")
        
        # Ejecutar según el modo
        if mode == 'full':
            logger.info("📥 Modo: Descarga completa")
            result = fetch_all_smartcards(session_id=None, limit=limit)
            message = "Descarga completa de smartcards completada"
            
        elif mode == 'incremental':
            logger.info("📥 Modo: Descarga incremental (solo nuevos)")
            if SmartcardsDataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. Use mode=full para descarga completa.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            result = download_smartcards_since_last(session_id=None, limit=limit)
            message = "Descarga incremental de smartcards completada"
            
        elif mode == 'update':
            logger.info("🔄 Modo: Actualización de existentes")
            if SmartcardsDataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. No hay registros para actualizar.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            compare_and_update_all_smartcards(session_id=None, limit=limit)
            result = None
            message = "Actualización de smartcards existentes completada"
            
        else:  # mode == 'sync' (default)
            logger.info("🔄 Modo: Sincronización completa (nuevos + actualización)")
            result = sync_smartcards(session_id=None, limit=limit)
            message = "Sincronización completa de smartcards completada"
        
        # Obtener estadísticas
        last_smartcard = LastSmartcard()
        last_sn = last_smartcard.sn if last_smartcard else None
        
        logger.info(f"✅ {message}")
        
        return Response({
            'success': True,
            'message': message,
            'mode': mode,
            'limit_used': limit,
            'last_smartcard_sn': last_sn,
            'database_empty': SmartcardsDataBaseEmpty(),
            'result': result if result is not None else 'update_completed'
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        error_msg = f"Error de PanAccess: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        error_msg = f"Error inesperado: {str(e)}"
        logger.error(f"💥 {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def sync_logins_view(request):
    """
    Vista para sincronizar credenciales de login de suscriptores desde PanAccess.
    
    Parámetros opcionales (GET o POST):
    - mode: 'full' (descarga completa), 'incremental' (solo nuevos), 
            'update' (solo actualizar existentes), 'sync' (completo - default)
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            mode = request.query_params.get('mode', 'sync')
        else:
            mode = request.data.get('mode', 'sync')
        
        logger.info(f"🔄 Iniciando sincronización de credenciales de login - Modo: {mode}")
        
        # Ejecutar según el modo
        if mode == 'full':
            logger.info("📥 Modo: Descarga completa")
            result = fetch_all_logins_from_panaccess(session_id=None)
            message = "Descarga completa de credenciales de login completada"
            
        elif mode == 'incremental':
            logger.info("📥 Modo: Descarga incremental (solo nuevos)")
            if LoginsDataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. Use mode=full para descarga completa.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            result = fetch_new_logins_from_panaccess(session_id=None)
            message = "Descarga incremental de credenciales de login completada"
            
        elif mode == 'update':
            logger.info("🔄 Modo: Actualización de existentes")
            if LoginsDataBaseEmpty():
                return Response({
                    'success': False,
                    'message': 'La base de datos está vacía. No hay registros para actualizar.',
                    'suggestion': 'Use ?mode=full para realizar una descarga completa primero'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            result = compare_and_update_all_existing(session_id=None)
            message = "Actualización de credenciales de login existentes completada"
            
        else:  # mode == 'sync' (default)
            logger.info("🔄 Modo: Sincronización completa (nuevos + actualización)")
            result = sync_subscriber_logins(session_id=None)
            message = "Sincronización completa de credenciales de login completada"
        
        # Obtener estadísticas
        last_login = LastSubscriberLoginInfo()
        last_code = last_login.subscriberCode if last_login else None
        
        logger.info(f"✅ {message}")
        
        return Response({
            'success': True,
            'message': message,
            'mode': mode,
            'last_subscriber_code': last_code,
            'database_empty': LoginsDataBaseEmpty(),
            'result': result if result is not None else 'update_completed'
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        error_msg = f"Error de PanAccess: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        error_msg = f"Error inesperado: {str(e)}"
        logger.error(f"💥 {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def sync_subscriberinfo_view(request):
    """
    Vista para sincronizar y consolidar información de suscriptores en SubscriberInfo.
    
    Este endpoint busca información en las tablas base:
    - ListOfSubscriber (información básica de suscriptores)
    - ListOfSmartcards (información de smartcards)
    - SubscriberLoginInfo (credenciales de login)
    
    Y consolida todo en la tabla SubscriberInfo (tabla consolidada).
    
    Parámetros opcionales (GET o POST):
    - mode: 'full' (merge completo), 'sync' (automático - default)
    
    Returns:
        Respuesta con estadísticas de la consolidación
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            mode = request.query_params.get('mode', 'sync')
        else:
            mode = request.data.get('mode', 'sync')
        
        logger.info(f"🔄 Iniciando consolidación de información en SubscriberInfo - Modo: {mode}")
        
        # Importar función de consolidación
        from .utils.panaccess.subscriberinfo import (
            sync_merge_all_subscribers,
            subscriber_info_empty,
            last_subscriber_info,
            get_all_subscriber_codes
        )
        
        # Ejecutar según el modo
        if mode == 'full':
            logger.info("📥 Modo: Consolidación completa (fuerza merge de todos)")
            # Obtener todos los códigos y hacer merge completo
            codes = sorted(get_all_subscriber_codes())
            logger.info(f"📊 Total de códigos a procesar: {len(codes)}")
            
            from .utils.panaccess.subscriberinfo import merge_subscriber_data
            total_processed = 0
            for code in codes:
                merge_subscriber_data(code)
                total_processed += 1
            
            message = f"Consolidación completa de {total_processed} suscriptores en SubscriberInfo completada"
            result = {'total_processed': total_processed, 'mode': 'full'}
            
        else:  # mode == 'sync' (default)
            logger.info("🔄 Modo: Sincronización automática (nuevos + actualización)")
            # Usar la función que evalúa automáticamente
            sync_merge_all_subscribers()
            message = "Sincronización automática de SubscriberInfo completada"
            result = {'mode': 'sync', 'automatic': True}
        
        # Obtener estadísticas
        last_info = last_subscriber_info()
        last_code = last_info.subscriber_code if last_info else None
        total_codes = len(get_all_subscriber_codes())
        
        logger.info(f"✅ {message}")
        
        return Response({
            'success': True,
            'message': message,
            'mode': mode,
            'last_subscriber_code': last_code,
            'total_subscriber_codes': total_codes,
            'database_empty': subscriber_info_empty(),
            'result': result
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        error_msg = f"Error de PanAccess: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        error_msg = f"Error inesperado: {str(e)}"
        logger.error(f"💥 {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)