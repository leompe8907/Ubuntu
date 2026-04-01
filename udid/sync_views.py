"""
Vistas para sincronizar datos desde PanAccess.

Endpoints que ejecutan procesos de sincronización completos de suscriptores, smartcards y credenciales de login.
"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status

from .api_errors import handle_view_exception, response_upstream_unavailable
from .utils.panaccess.subscriber import (
    sync_subscribers,
    DataBaseEmpty,
    LastSubscriber
)
from .utils.panaccess.smartcard import (
    sync_smartcards,
    DataBaseEmpty as SmartcardsDataBaseEmpty,
    LastSmartcard
)
from .utils.panaccess.login import (
    sync_subscriber_logins,
    DataBaseEmpty as LoginsDataBaseEmpty,
    LastSubscriberLoginInfo
)
from .utils.panaccess.exceptions import PanaccessException

logger = logging.getLogger(__name__)


def _with_success_false(response):
    """Añade success=False al JSON sin mutar el objeto Response de forma frágil."""
    data = response.data
    payload = dict(data) if isinstance(data, dict) else {"detail": data}
    payload["success"] = False
    return Response(payload, status=response.status_code)


@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def sync_subscribers_view(request):
    """
    Vista para sincronizar suscriptores desde PanAccess.
    Usa lógica automática basada en el estado de la base de datos.
    
    Parámetros opcionales (GET o POST):
    - limit: Cantidad de registros por página (default: 100)
    
    Lógica automática:
    - Si BD vacía → descarga completa desde cero
    - Si BD tiene registros → descarga nuevos desde último registro + actualiza existentes
    - Si hay error/interrupción → los reintentos están implementados
    - Si reintentos fallan → al llamar de nuevo, detecta registros y continúa desde último
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            limit = int(request.query_params.get('limit', 100))
        else:
            limit = int(request.data.get('limit', 100))
        
        # Validar limit
        if limit > 1000:
            limit = 1000
            logger.warning("Limit ajustado a 1000 (máximo permitido)")
        
        logger.info(f"🔄 Iniciando sincronización automática de suscriptores (limit: {limit})")
        
        # Usar la lógica automática que ya existe
        # - Si BD vacía → descarga completa
        # - Si BD tiene registros → descarga nuevos + actualiza existentes
        result = sync_subscribers(session_id=None, limit=limit)
        
        # Obtener estadísticas
        last_subscriber = LastSubscriber()
        last_code = last_subscriber.code if last_subscriber else None
        
        logger.info(f"✅ Sincronización de suscriptores completada")
        
        return Response({
            'success': True,
            'message': 'Sincronización automática de suscriptores completada',
            'limit_used': limit,
            'last_subscriber_code': last_code,
            'database_empty': DataBaseEmpty(),
            'result': result
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        return _with_success_false(
            response_upstream_unavailable(
                "sync_subscribers_view",
                message="PanAccess no disponible o rechazó la sincronización de suscriptores.",
                exc=e,
            )
        )

    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        return _with_success_false(handle_view_exception("sync_subscribers_view", e))

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def sync_smartcards_view(request):
    """
    Vista para sincronizar smartcards desde PanAccess.
    Usa lógica automática basada en el estado de la base de datos.
    
    Parámetros opcionales (GET o POST):
    - limit: Cantidad de registros por página (default: 100)
    
    Lógica automática:
    - Si BD vacía → descarga completa desde cero
    - Si BD tiene registros → descarga nuevos desde último registro + actualiza existentes
    - Si hay error/interrupción → los reintentos están implementados
    - Si reintentos fallan → al llamar de nuevo, detecta registros y continúa desde último
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        # Obtener parámetros
        if request.method == 'GET':
            limit = int(request.query_params.get('limit', 100))
        else:
            limit = int(request.data.get('limit', 100))
        
        # Validar limit
        if limit > 1000:
            limit = 1000
            logger.warning("Limit ajustado a 1000 (máximo permitido)")
        
        logger.info(f"🔄 Iniciando sincronización automática de smartcards (limit: {limit})")
        
        # Usar la lógica automática que ya existe
        # - Si BD vacía → descarga completa
        # - Si BD tiene registros → descarga nuevos + actualiza existentes
        result = sync_smartcards(session_id=None, limit=limit)
        
        # Obtener estadísticas
        last_smartcard = LastSmartcard()
        last_sn = last_smartcard.sn if last_smartcard else None
        
        logger.info(f"✅ Sincronización de smartcards completada")
        
        return Response({
            'success': True,
            'message': 'Sincronización automática de smartcards completada',
            'limit_used': limit,
            'last_smartcard_sn': last_sn,
            'database_empty': SmartcardsDataBaseEmpty(),
            'result': result
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        return _with_success_false(
            response_upstream_unavailable(
                "sync_smartcards_view",
                message="PanAccess no disponible o rechazó la sincronización de smartcards.",
                exc=e,
            )
        )

    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        return _with_success_false(handle_view_exception("sync_smartcards_view", e))

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
def sync_logins_view(request):
    """
    Vista para sincronizar credenciales de login de suscriptores desde PanAccess.
    Usa lógica automática basada en el estado de la base de datos.
    
    Parámetros opcionales (GET o POST):
    - limit: Cantidad de registros por página (default: 100) - No aplica para logins
    
    Lógica automática:
    - Si BD vacía → descarga completa desde cero
    - Si BD tiene registros → descarga nuevos desde último registro + actualiza existentes
    - Si hay error/interrupción → los reintentos están implementados
    - Si reintentos fallan → al llamar de nuevo, detecta registros y continúa desde último
    
    Returns:
        Respuesta con estadísticas de la sincronización
    """
    try:
        logger.info(f"🔄 Iniciando sincronización automática de credenciales de login")
        
        # Usar la lógica automática que ya existe en sync_subscriber_logins()
        # - Si BD vacía → fetch_all_logins_from_panaccess()
        # - Si BD tiene registros → fetch_new_logins_from_panaccess() + compare_and_update_all_existing()
        result = sync_subscriber_logins(session_id=None)
        
        # Obtener estadísticas
        last_login = LastSubscriberLoginInfo()
        last_code = last_login.subscriberCode if last_login else None
        
        logger.info(f"✅ Sincronización de credenciales de login completada")
        
        return Response({
            'success': True,
            'message': 'Sincronización automática de credenciales de login completada',
            'last_subscriber_code': last_code,
            'database_empty': LoginsDataBaseEmpty(),
            'result': result
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        return _with_success_false(
            response_upstream_unavailable(
                "sync_logins_view",
                message="PanAccess no disponible o rechazó la sincronización de logins.",
                exc=e,
            )
        )

    except Exception as e:
        return _with_success_false(handle_view_exception("sync_logins_view", e))

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
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
        return _with_success_false(
            response_upstream_unavailable(
                "sync_subscriberinfo_view",
                message="PanAccess o consolidación de SubscriberInfo falló.",
                exc=e,
            )
        )

    except ValueError as e:
        error_msg = f"Error de parámetros: {str(e)}"
        logger.error(f"❌ {error_msg}")
        
        return Response({
            'success': False,
            'error_type': 'ValueError',
            'message': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        return _with_success_false(handle_view_exception("sync_subscriberinfo_view", e))