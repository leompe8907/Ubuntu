"""
Tareas de Celery para sincronización de datos desde Panaccess.

Este módulo contiene todas las tareas asíncronas que se ejecutan en background
usando Celery. Las tareas se pueden ejecutar de forma periódica (con celery-beat)
o bajo demanda.
"""
import logging
from celery import shared_task
# from celery.exceptions import Retry  # No usado actualmente

from .utils.panaccess import (
    sync_merge_all_subscribers,
)
from .utils.panaccess.exceptions import (
    PanaccessException,
    PanaccessAuthenticationError,
    PanaccessConnectionError,
    PanaccessTimeoutError,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='udid.tasks.initial_sync_all_data',
    max_retries=3,
    default_retry_delay=3,  # 3 segundos entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=3600,  # Máximo 1 hora de delay
    retry_jitter=True,
)
def initial_sync_all_data(self):
    """
    Tarea de inicialización completa que descarga todos los datos desde Panaccess.
    
    Esta tarea está diseñada para ejecutarse UNA SOLA VEZ cuando se necesita
    inicializar la base de datos con todos los datos de Panaccess.
    
    QUÉ HACE:
    - Descarga TODOS los suscriptores desde Panaccess
    - Descarga TODAS las smartcards desde Panaccess
    - Descarga TODA la información de login de suscriptores
    - Crea los registros en las tablas correspondientes:
      * ListOfSubscriber
      * ListOfSmartcards
      * SubscriberLoginInfo
      * SubscriberInfo (tabla consolidada)
    
    CÓMO LO HACE:
    - Usa el singleton de Panaccess para autenticación automática
    - Fuerza descarga completa (modo 'full') para asegurar que descargue todo
    - Ejecuta las sincronizaciones en orden:
      1. Suscriptores
      2. Smartcards
      3. Credenciales de login
      4. Merge en SubscriberInfo
    
    IMPORTANTE:
    - Esta tarea puede tomar varias horas si hay muchos registros (ej: 10,000 smartcards)
    - Se recomienda ejecutarla cuando la base de datos está vacía o se necesita
      una sincronización completa inicial
    - No está diseñada para ejecutarse periódicamente
    
    Returns:
        dict: Resultado de la sincronización con información detallada de cada paso
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    logger.info("🚀 [INITIAL_SYNC] Iniciando sincronización inicial completa de datos desde Panaccess")
    
    result = {
        'success': False,
        'message': '',
        'steps': {
            'subscribers': {'success': False, 'message': '', 'count': 0},
            'smartcards': {'success': False, 'message': '', 'count': 0},
            'subscriber_logins': {'success': False, 'message': '', 'count': 0},
            'merge_subscribers': {'success': False, 'message': '', 'count': 0},
        },
        'total_time_seconds': 0,
    }
    
    import time
    start_time = time.time()
    
    try:
        # ========================================================================
        # PASO 1: SINCRONIZACIÓN DE SUSCRIPTORES (FORZAR MODO FULL)
        # ========================================================================
        logger.info("📥 [INITIAL_SYNC] Paso 1/4: Descargando todos los suscriptores...")
        try:
            # Usar fetch_all_subscribers directamente para forzar descarga completa
            from .utils.panaccess.subscriber import fetch_all_subscribers
            subscribers_result = fetch_all_subscribers(session_id=None, limit=100)
            result['steps']['subscribers'] = {
                'success': True,
                'message': 'Suscriptores descargados correctamente',
                'count': len(subscribers_result) if isinstance(subscribers_result, list) else 'N/A'
            }
            logger.info(f"✅ [INITIAL_SYNC] Suscriptores descargados: {result['steps']['subscribers']['count']}")
        except Exception as e:
            error_msg = f"Error descargando suscriptores: {str(e)}"
            logger.error(f"❌ [INITIAL_SYNC] {error_msg}", exc_info=True)
            result['steps']['subscribers'] = {
                'success': False,
                'message': error_msg,
                'count': 0
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 2: SINCRONIZACIÓN DE SMARTCARDS (FORZAR MODO FULL)
        # ========================================================================
        logger.info("📥 [INITIAL_SYNC] Paso 2/4: Descargando todas las smartcards...")
        try:
            # Usar fetch_all_smartcards directamente para forzar descarga completa
            # timeout=None para deshabilitar timeout (puede tardar horas con muchos registros)
            from .utils.panaccess.smartcard import fetch_all_smartcards
            smartcards_result = fetch_all_smartcards(session_id=None, limit=100, timeout=None)
            result['steps']['smartcards'] = {
                'success': True,
                'message': 'Smartcards descargadas correctamente',
                'count': len(smartcards_result) if isinstance(smartcards_result, list) else 'N/A'
            }
            logger.info(f"✅ [INITIAL_SYNC] Smartcards descargadas: {result['steps']['smartcards']['count']}")
        except Exception as e:
            error_msg = f"Error descargando smartcards: {str(e)}"
            logger.error(f"❌ [INITIAL_SYNC] {error_msg}", exc_info=True)
            result['steps']['smartcards'] = {
                'success': False,
                'message': error_msg,
                'count': 0
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 3: SINCRONIZACIÓN DE CREDENCIALES DE LOGIN (FORZAR MODO FULL)
        # ========================================================================
        logger.info("📥 [INITIAL_SYNC] Paso 3/4: Descargando todas las credenciales de login...")
        try:
            # Usar fetch_all_logins_from_panaccess directamente para forzar descarga completa
            from .utils.panaccess.login import fetch_all_logins_from_panaccess
            logins_result = fetch_all_logins_from_panaccess(session_id=None)
            result['steps']['subscriber_logins'] = {
                'success': True,
                'message': 'Credenciales de login descargadas correctamente',
                'count': logins_result if isinstance(logins_result, int) else 'N/A'
            }
            logger.info(f"✅ [INITIAL_SYNC] Credenciales descargadas: {result['steps']['subscriber_logins']['count']}")
        except Exception as e:
            error_msg = f"Error descargando credenciales: {str(e)}"
            logger.error(f"❌ [INITIAL_SYNC] {error_msg}", exc_info=True)
            result['steps']['subscriber_logins'] = {
                'success': False,
                'message': error_msg,
                'count': 0
            }
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 4: MERGE EN SUBSCRIBERINFO (TABLA CONSOLIDADA)
        # ========================================================================
        logger.info("📥 [INITIAL_SYNC] Paso 4/4: Consolidando información en SubscriberInfo...")
        try:
            sync_merge_all_subscribers()
            result['steps']['merge_subscribers'] = {
                'success': True,
                'message': 'Información consolidada correctamente',
                'count': 'N/A'
            }
            logger.info("✅ [INITIAL_SYNC] Información consolidada en SubscriberInfo")
        except Exception as e:
            error_msg = f"Error consolidando información: {str(e)}"
            logger.error(f"❌ [INITIAL_SYNC] {error_msg}", exc_info=True)
            result['steps']['merge_subscribers'] = {
                'success': False,
                'message': error_msg,
                'count': 0
            }
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        # Verificar si todas las tareas se completaron exitosamente
        all_success = all(step['success'] for step in result['steps'].values())
        result['success'] = all_success
        
        if all_success:
            result['message'] = f'Sincronización inicial completada exitosamente en {elapsed_time:.2f} segundos'
            logger.info(f"✅ [INITIAL_SYNC] {result['message']}")
        else:
            failed_steps = [name for name, step in result['steps'].items() if not step['success']]
            result['message'] = f'Sincronización inicial completada con errores en: {", ".join(failed_steps)}'
            logger.warning(f"⚠️ [INITIAL_SYNC] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [INITIAL_SYNC] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [INITIAL_SYNC] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [INITIAL_SYNC] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise


@shared_task(
    bind=True,
    name='udid.tasks.download_new_subscribers',
    max_retries=3,
    default_retry_delay=60,  # 1 minuto entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,  # Máximo 10 minutos de delay
    retry_jitter=True,
)
def download_new_subscribers(self):
    """
    Tarea que descarga solo los suscriptores nuevos desde Panaccess.
    
    Esta tarea está diseñada para ejecutarse periódicamente o bajo demanda
    para mantener la base de datos actualizada con nuevos suscriptores.
    
    QUÉ HACE:
    - Descarga SOLO los suscriptores nuevos (posteriores al último registrado)
    - Descarga las credenciales de login de los nuevos suscriptores
    - Crea los registros en las tablas:
      * ListOfSubscriber (información del suscriptor)
      * SubscriberLoginInfo (credenciales de login)
    - Actualiza la tabla consolidada SubscriberInfo con los nuevos datos
    - No descarga todos los suscriptores, solo los nuevos
    
    CÓMO LO HACE:
    - Usa el singleton de Panaccess para autenticación automática
    - Busca el último suscriptor registrado en la base de datos
    - Descarga solo los suscriptores con código mayor al último registrado
    - Almacena los nuevos registros en la base de datos
    
    IMPORTANTE:
    - Si la base de datos está vacía, retorna sin descargar nada
    - Se recomienda usar initial_sync_all_data primero si la BD está vacía
    - Esta tarea es rápida (segundos/minutos) ya que solo descarga nuevos registros
    
    Returns:
        dict: Resultado de la descarga con información detallada
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    logger.info("📥 [NEW_SUBSCRIBERS] Iniciando descarga de suscriptores nuevos desde Panaccess")
    
    result = {
        'success': False,
        'message': '',
        'subscribers_downloaded': 0,
        'credentials_downloaded': 0,
        'last_subscriber_code': None,
        'total_time_seconds': 0,
    }
    
    import time
    start_time = time.time()
    
    try:
        # Verificar si hay suscriptores en la base de datos
        from .utils.panaccess.subscriber import LastSubscriber, download_subscribers_since_last
        
        last_subscriber = LastSubscriber()
        if not last_subscriber:
            result['message'] = 'No hay suscriptores registrados. Use initial_sync_all_data primero para descargar todos los suscriptores.'
            result['success'] = False
            logger.warning("⚠️ [NEW_SUBSCRIBERS] No hay suscriptores registrados. Se requiere descarga completa primero.")
            return result
        
        last_code = last_subscriber.code
        result['last_subscriber_code'] = last_code
        logger.info(f"📥 [NEW_SUBSCRIBERS] Último suscriptor registrado: {last_code}")
        
        # ========================================================================
        # PASO 1: DESCARGAR SUSCRIPTORES NUEVOS
        # ========================================================================
        logger.info("📥 [NEW_SUBSCRIBERS] Paso 1/2: Descargando suscriptores nuevos...")
        try:
            new_subscribers_result = download_subscribers_since_last(session_id=None, limit=100)
            
            # Contar cuántos suscriptores se descargaron
            count = len(new_subscribers_result) if isinstance(new_subscribers_result, list) else 0
            result['subscribers_downloaded'] = count
            
            if count > 0:
                logger.info(f"✅ [NEW_SUBSCRIBERS] {count} suscriptores descargados correctamente")
            else:
                logger.info(f"ℹ️ [NEW_SUBSCRIBERS] No hay suscriptores nuevos para descargar")
                
        except Exception as e:
            error_msg = f"Error descargando suscriptores nuevos: {str(e)}"
            logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['message'] = error_msg
            result['success'] = False
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 2: DESCARGAR CREDENCIALES DE LOGIN DE LOS NUEVOS SUSCRIPTORES
        # ========================================================================
        logger.info("📥 [NEW_SUBSCRIBERS] Paso 2/3: Descargando credenciales de login de nuevos suscriptores...")
        try:
            from .utils.panaccess.login import fetch_new_logins_from_panaccess
            credentials_count = fetch_new_logins_from_panaccess(session_id=None)
            
            result['credentials_downloaded'] = credentials_count if isinstance(credentials_count, int) else 0
            
            if result['credentials_downloaded'] > 0:
                logger.info(f"✅ [NEW_SUBSCRIBERS] {result['credentials_downloaded']} credenciales descargadas correctamente")
            else:
                logger.info(f"ℹ️ [NEW_SUBSCRIBERS] No hay credenciales nuevas para descargar")
                
        except Exception as e:
            error_msg = f"Error descargando credenciales: {str(e)}"
            logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}", exc_info=True)
            # No marcar como fallo total si solo falla la descarga de credenciales
            result['credentials_downloaded'] = 0
        
        # ========================================================================
        # PASO 3: ACTUALIZAR TABLA CONSOLIDADA (SUBSCRIBERINFO)
        # ========================================================================
        logger.info("📥 [NEW_SUBSCRIBERS] Paso 3/3: Actualizando tabla consolidada SubscriberInfo...")
        try:
            from .utils.panaccess.subscriberinfo import sync_merge_all_subscribers
            sync_merge_all_subscribers()
            logger.info("✅ [NEW_SUBSCRIBERS] Tabla consolidada actualizada correctamente")
        except Exception as e:
            error_msg = f"Error actualizando tabla consolidada: {str(e)}"
            logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}", exc_info=True)
            # No marcar como fallo total si solo falla la actualización de la tabla consolidada
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        # Considerar éxito si se descargaron suscriptores (las credenciales son opcionales)
        if result['subscribers_downloaded'] > 0:
            result['success'] = True
            result['message'] = f'Se descargaron {result["subscribers_downloaded"]} suscriptores nuevos'
            if result['credentials_downloaded'] > 0:
                result['message'] += f' y {result["credentials_downloaded"]} credenciales'
            result['message'] += ' correctamente'
        elif result['subscribers_downloaded'] == 0:
            result['success'] = True
            result['message'] = 'No hay suscriptores nuevos para descargar'
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        logger.info(f"✅ [NEW_SUBSCRIBERS] Descarga completada en {elapsed_time:.2f} segundos")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante descarga de suscriptores nuevos: {str(e)}"
        logger.error(f"❌ [NEW_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise


@shared_task(
    bind=True,
    name='udid.tasks.update_all_subscribers',
    max_retries=3,
    default_retry_delay=60,  # 1 minuto entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,  # Máximo 10 minutos de delay
    retry_jitter=True,
)
def update_all_subscribers(self):
    """
    Tarea que actualiza todos los suscriptores existentes y su información desde Panaccess.
    
    Esta tarea está diseñada para ejecutarse periódicamente o bajo demanda
    para mantener la base de datos actualizada con los cambios en Panaccess.
    
    QUÉ HACE:
    - Actualiza TODOS los suscriptores existentes (compara y actualiza campos que hayan cambiado)
    - Actualiza TODAS las credenciales de login existentes
    - Actualiza la información consolidada en SubscriberInfo (tabla consolidada)
    - NO descarga nuevos registros, solo actualiza los existentes
    
    CÓMO LO HACE:
    - Usa el singleton de Panaccess para autenticación automática
    - Compara cada registro local con los datos remotos de Panaccess
    - Solo actualiza campos que hayan cambiado (optimización)
    - Procesa en 3 pasos:
      1. Actualiza suscriptores en ListOfSubscriber
      2. Actualiza credenciales en SubscriberLoginInfo
      3. Actualiza información consolidada en SubscriberInfo
    
    IMPORTANTE:
    - Esta tarea puede tomar tiempo si hay muchos suscriptores (ej: 10,000)
    - Solo actualiza registros existentes, no crea nuevos
    - Se recomienda ejecutarla periódicamente (ej: diariamente) para mantener datos actualizados
    
    Returns:
        dict: Resultado de la actualización con información detallada de cada paso
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    logger.info("🔄 [UPDATE_ALL] Iniciando actualización de todos los suscriptores desde Panaccess")
    
    result = {
        'success': False,
        'message': '',
        'steps': {
            'subscribers': {'success': False, 'message': '', 'updated': 0},
            'credentials': {'success': False, 'message': '', 'updated': 0},
            'subscriber_info': {'success': False, 'message': '', 'updated': 0},
        },
        'total_time_seconds': 0,
    }
    
    import time
    start_time = time.time()
    
    try:
        # ========================================================================
        # PASO 1: ACTUALIZAR SUSCRIPTORES (ListOfSubscriber)
        # ========================================================================
        logger.info("🔄 [UPDATE_ALL] Paso 1/3: Actualizando suscriptores...")
        try:
            from .utils.panaccess.subscriber import compare_and_update_all_subscribers
            compare_and_update_all_subscribers(session_id=None, limit=100)
            # La función no retorna el conteo, pero registra en logs
            result['steps']['subscribers'] = {
                'success': True,
                'message': 'Suscriptores actualizados correctamente',
                'updated': 'N/A'  # La función no retorna conteo
            }
            logger.info("✅ [UPDATE_ALL] Suscriptores actualizados correctamente")
        except Exception as e:
            error_msg = f"Error actualizando suscriptores: {str(e)}"
            logger.error(f"❌ [UPDATE_ALL] {error_msg}", exc_info=True)
            result['steps']['subscribers'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 2: ACTUALIZAR CREDENCIALES DE LOGIN (SubscriberLoginInfo)
        # ========================================================================
        logger.info("🔄 [UPDATE_ALL] Paso 2/3: Actualizando credenciales de login...")
        try:
            from .utils.panaccess.login import compare_and_update_all_existing
            # La función retorna el total actualizado pero no lo expone directamente
            compare_and_update_all_existing(session_id=None)
            result['steps']['credentials'] = {
                'success': True,
                'message': 'Credenciales actualizadas correctamente',
                'updated': 'N/A'  # La función registra en logs pero no retorna conteo
            }
            logger.info("✅ [UPDATE_ALL] Credenciales actualizadas correctamente")
        except Exception as e:
            error_msg = f"Error actualizando credenciales: {str(e)}"
            logger.error(f"❌ [UPDATE_ALL] {error_msg}", exc_info=True)
            result['steps']['credentials'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 3: ACTUALIZAR INFORMACIÓN CONSOLIDADA (SubscriberInfo)
        # ========================================================================
        logger.info("🔄 [UPDATE_ALL] Paso 3/3: Actualizando información consolidada...")
        try:
            from .utils.panaccess.subscriberinfo import sync_merge_all_subscribers
            sync_merge_all_subscribers()
            result['steps']['subscriber_info'] = {
                'success': True,
                'message': 'Información consolidada actualizada correctamente',
                'updated': 'N/A'  # La función registra en logs pero no retorna conteo
            }
            logger.info("✅ [UPDATE_ALL] Información consolidada actualizada correctamente")
        except Exception as e:
            error_msg = f"Error actualizando información consolidada: {str(e)}"
            logger.error(f"❌ [UPDATE_ALL] {error_msg}", exc_info=True)
            result['steps']['subscriber_info'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        # Verificar si todas las tareas se completaron exitosamente
        all_success = all(step['success'] for step in result['steps'].values())
        result['success'] = all_success
        
        if all_success:
            result['message'] = f'Actualización completada exitosamente en {elapsed_time:.2f} segundos'
            logger.info(f"✅ [UPDATE_ALL] {result['message']}")
        else:
            failed_steps = [name for name, step in result['steps'].items() if not step['success']]
            result['message'] = f'Actualización completada con errores en: {", ".join(failed_steps)}'
            logger.warning(f"⚠️ [UPDATE_ALL] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [UPDATE_ALL] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [UPDATE_ALL] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [UPDATE_ALL] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante actualización de suscriptores: {str(e)}"
        logger.error(f"❌ [UPDATE_ALL] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise


@shared_task(
    bind=True,
    name='udid.tasks.update_smartcards_from_subscribers',
    max_retries=3,
    default_retry_delay=60,  # 1 minuto entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,  # Máximo 10 minutos de delay
    retry_jitter=True,
)
def update_smartcards_from_subscribers(self):
    """
    Tarea que actualiza el modelo de smartcards basándose en la información del modelo de suscriptores.
    
    Esta tarea está diseñada para ejecutarse periódicamente o bajo demanda
    para mantener la tabla ListOfSmartcards sincronizada con los datos de suscriptores.
    
    QUÉ HACE:
    - Lee todos los suscriptores de la tabla ListOfSubscriber
    - Extrae las SNs (Serial Numbers) del campo smartcards (JSON) de cada suscriptor
    - Actualiza o crea registros en ListOfSmartcards con información del suscriptor:
      * subscriberCode (código del suscriptor)
      * lastName (apellido del suscriptor)
      * firstName (nombre del suscriptor)
      * hcId (ID del headend)
    - Actualiza la tabla consolidada SubscriberInfo con los cambios realizados
    - Solo actualiza campos que hayan cambiado (optimización)
    
    CÓMO LO HACE:
    - Procesa todos los suscriptores en la base de datos
    - Para cada suscriptor, extrae las SNs del campo JSON smartcards
    - Para cada SN:
      * Si la smartcard ya existe: actualiza solo los campos que cambiaron
      * Si la smartcard no existe: crea un nuevo registro
    - Usa transacciones para garantizar consistencia
    
    IMPORTANTE:
    - Esta tarea NO requiere conexión a Panaccess (trabaja solo con datos locales)
    - Puede tomar tiempo si hay muchos suscriptores (ej: 10,000)
    - Solo actualiza campos básicos del suscriptor, no sobrescribe datos específicos de smartcard
      como PIN, productos, paquetes, etc.
    - Se recomienda ejecutarla después de actualizar suscriptores desde Panaccess
    
    Returns:
        dict: Resultado de la actualización con estadísticas detalladas:
            - total_subscribers_processed: Cantidad de suscriptores procesados
            - total_sns_found: Total de SNs encontradas en todos los suscriptores
            - total_smartcards_created: Cantidad de smartcards nuevas creadas
            - total_smartcards_updated: Cantidad de smartcards existentes actualizadas
            - total_errors: Cantidad de errores encontrados durante el proceso
        
    Raises:
        Exception: Si hay errores críticos durante el proceso
    """
    logger.info("🔄 [UPDATE_SMARTCARDS] Iniciando actualización de smartcards desde suscriptores")
    
    result = {
        'success': False,
        'message': '',
        'total_subscribers_processed': 0,
        'total_sns_found': 0,
        'total_smartcards_created': 0,
        'total_smartcards_updated': 0,
        'total_errors': 0,
        'total_time_seconds': 0,
    }
    
    import time
    start_time = time.time()
    
    try:
        # Llamar a la función que actualiza smartcards desde suscriptores
        from .utils.panaccess.smartcard import update_smartcards_from_subscribers as update_function
        
        logger.info("🔄 [UPDATE_SMARTCARDS] Ejecutando actualización...")
        update_result = update_function()
        
        # Copiar los resultados de la función
        result['total_subscribers_processed'] = update_result.get('total_subscribers_processed', 0)
        result['total_sns_found'] = update_result.get('total_sns_found', 0)
        result['total_smartcards_created'] = update_result.get('total_smartcards_created', 0)
        result['total_smartcards_updated'] = update_result.get('total_smartcards_updated', 0)
        result['total_errors'] = update_result.get('total_errors', 0)
        
        # Determinar si fue exitoso
        result['success'] = result['total_errors'] == 0
        
        # Crear mensaje descriptivo
        if result['success']:
            result['message'] = (
                f'Actualización completada: {result["total_subscribers_processed"]} suscriptores procesados, '
                f'{result["total_sns_found"]} SNs encontradas, '
                f'{result["total_smartcards_created"]} smartcards creadas, '
                f'{result["total_smartcards_updated"]} smartcards actualizadas'
            )
        else:
            result['message'] = (
                f'Actualización completada con {result["total_errors"]} errores: '
                f'{result["total_subscribers_processed"]} suscriptores procesados, '
                f'{result["total_smartcards_created"]} smartcards creadas, '
                f'{result["total_smartcards_updated"]} smartcards actualizadas'
            )
        
        logger.info(f"✅ [UPDATE_SMARTCARDS] {result['message']}")
        
        # ========================================================================
        # PASO ADICIONAL: ACTUALIZAR TABLA CONSOLIDADA (SUBSCRIBERINFO)
        # ========================================================================
        logger.info("🔄 [UPDATE_SMARTCARDS] Actualizando tabla consolidada SubscriberInfo...")
        try:
            from .utils.panaccess.subscriberinfo import sync_merge_all_subscribers
            sync_merge_all_subscribers()
            logger.info("✅ [UPDATE_SMARTCARDS] Tabla consolidada actualizada correctamente")
        except Exception as e:
            error_msg = f"Error actualizando tabla consolidada: {str(e)}"
            logger.error(f"❌ [UPDATE_SMARTCARDS] {error_msg}", exc_info=True)
            # No marcar como fallo total si solo falla la actualización de la tabla consolidada
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        logger.info(f"✅ [UPDATE_SMARTCARDS] Proceso completado en {elapsed_time:.2f} segundos")
        
        return result
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante actualización de smartcards desde suscriptores: {str(e)}"
        logger.error(f"❌ [UPDATE_SMARTCARDS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        
        # Calcular tiempo transcurrido antes de lanzar excepción
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        raise


@shared_task(
    bind=True,
    name='udid.tasks.validate_and_fix_all_data',
    max_retries=3,
    default_retry_delay=300,  # 5 minutos entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=3600,  # Máximo 1 hora de delay
    retry_jitter=True,
)
def validate_and_fix_all_data(self):
    """
    Tarea de validación y corrección completa que sincroniza y valida todos los datos desde Panaccess.
    
    Esta tarea está diseñada para ejecutarse a una hora específica (configurada con Celery Beat)
    para mantener la integridad y consistencia de todos los datos en la base de datos.
    
    QUÉ HACE:
    - Sincroniza TODOS los suscriptores desde Panaccess (descarga nuevos y actualiza existentes)
    - Sincroniza TODAS las credenciales de login desde Panaccess
    - Sincroniza TODAS las smartcards desde Panaccess
    - Valida la consistencia de los datos entre todas las tablas
    - Corrige errores encontrados:
      * Actualiza smartcards con información de suscriptores
      * Sincroniza la tabla consolidada SubscriberInfo
      * Asegura que todos los datos estén actualizados
    
    CÓMO LO HACE:
    - Usa el singleton de Panaccess para autenticación automática
    - Ejecuta sincronizaciones completas desde Panaccess
    - Valida y corrige inconsistencias entre tablas
    - Procesa en 5 pasos:
      1. Sincronizar suscriptores desde Panaccess
      2. Sincronizar credenciales desde Panaccess
      3. Sincronizar smartcards desde Panaccess
      4. Actualizar smartcards con información de suscriptores (corrección)
      5. Sincronizar tabla consolidada SubscriberInfo
    
    IMPORTANTE:
    - Esta tarea puede tomar mucho tiempo si hay muchos registros (ej: 10,000+)
    - Se recomienda ejecutarla en horarios de bajo tráfico (ej: 2:00 AM)
    - Esta tarea es completa y exhaustiva, asegura que todos los datos estén correctos
    - Detecta y corrige automáticamente inconsistencias entre tablas
    
    Returns:
        dict: Resultado de la validación y corrección con información detallada de cada paso
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    logger.info("🔍 [VALIDATE_FIX] Iniciando validación y corrección completa de datos desde Panaccess")
    
    result = {
        'success': False,
        'message': '',
        'steps': {
            'sync_subscribers': {'success': False, 'message': '', 'details': {}},
            'sync_credentials': {'success': False, 'message': '', 'details': {}},
            'sync_smartcards': {'success': False, 'message': '', 'details': {}},
            'fix_smartcards_from_subscribers': {'success': False, 'message': '', 'details': {}},
            'sync_consolidated': {'success': False, 'message': '', 'details': {}},
        },
        'total_time_seconds': 0,
    }
    
    import time
    start_time = time.time()
    
    try:
        # ========================================================================
        # PASO 1: SINCRONIZAR SUSCRIPTORES DESDE PANACCESS
        # ========================================================================
        logger.info("🔍 [VALIDATE_FIX] Paso 1/5: Sincronizando suscriptores desde Panaccess...")
        try:
            from .utils.panaccess.subscriber import sync_subscribers
            sync_result = sync_subscribers(session_id=None, limit=100)
            result['steps']['sync_subscribers'] = {
                'success': True,
                'message': 'Suscriptores sincronizados correctamente',
                'details': {'result': str(sync_result) if sync_result else 'N/A'}
            }
            logger.info("✅ [VALIDATE_FIX] Suscriptores sincronizados correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando suscriptores: {str(e)}"
            logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
            result['steps']['sync_subscribers'] = {
                'success': False,
                'message': error_msg,
                'details': {}
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 2: SINCRONIZAR CREDENCIALES DESDE PANACCESS
        # ========================================================================
        logger.info("🔍 [VALIDATE_FIX] Paso 2/5: Sincronizando credenciales desde Panaccess...")
        try:
            from .utils.panaccess.login import sync_subscriber_logins
            credentials_result = sync_subscriber_logins(session_id=None)
            result['steps']['sync_credentials'] = {
                'success': True,
                'message': 'Credenciales sincronizadas correctamente',
                'details': {'result': str(credentials_result) if credentials_result else 'N/A'}
            }
            logger.info("✅ [VALIDATE_FIX] Credenciales sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando credenciales: {str(e)}"
            logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
            result['steps']['sync_credentials'] = {
                'success': False,
                'message': error_msg,
                'details': {}
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 3: SINCRONIZAR SMARTCARDS DESDE PANACCESS
        # ========================================================================
        logger.info("🔍 [VALIDATE_FIX] Paso 3/5: Sincronizando smartcards desde Panaccess...")
        try:
            from .utils.panaccess.smartcard import sync_smartcards
            smartcards_result = sync_smartcards(session_id=None, limit=100)
            result['steps']['sync_smartcards'] = {
                'success': True,
                'message': 'Smartcards sincronizadas correctamente',
                'details': {'result': str(smartcards_result) if smartcards_result else 'N/A'}
            }
            logger.info("✅ [VALIDATE_FIX] Smartcards sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando smartcards: {str(e)}"
            logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
            result['steps']['sync_smartcards'] = {
                'success': False,
                'message': error_msg,
                'details': {}
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 4: CORREGIR SMARTCARDS CON INFORMACIÓN DE SUSCRIPTORES
        # ========================================================================
        logger.info("🔍 [VALIDATE_FIX] Paso 4/5: Corrigiendo smartcards con información de suscriptores...")
        try:
            from .utils.panaccess.smartcard import update_smartcards_from_subscribers
            fix_result = update_smartcards_from_subscribers()
            result['steps']['fix_smartcards_from_subscribers'] = {
                'success': True,
                'message': 'Smartcards corregidas correctamente',
                'details': {
                    'subscribers_processed': fix_result.get('total_subscribers_processed', 0),
                    'sns_found': fix_result.get('total_sns_found', 0),
                    'smartcards_created': fix_result.get('total_smartcards_created', 0),
                    'smartcards_updated': fix_result.get('total_smartcards_updated', 0),
                    'errors': fix_result.get('total_errors', 0),
                }
            }
            logger.info("✅ [VALIDATE_FIX] Smartcards corregidas correctamente")
        except Exception as e:
            error_msg = f"Error corrigiendo smartcards: {str(e)}"
            logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
            result['steps']['fix_smartcards_from_subscribers'] = {
                'success': False,
                'message': error_msg,
                'details': {}
            }
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 5: SINCRONIZAR TABLA CONSOLIDADA (SUBSCRIBERINFO)
        # ========================================================================
        logger.info("🔍 [VALIDATE_FIX] Paso 5/5: Sincronizando tabla consolidada...")
        try:
            from .utils.panaccess.subscriberinfo import sync_merge_all_subscribers
            sync_merge_all_subscribers()
            result['steps']['sync_consolidated'] = {
                'success': True,
                'message': 'Tabla consolidada sincronizada correctamente',
                'details': {}
            }
            logger.info("✅ [VALIDATE_FIX] Tabla consolidada sincronizada correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando tabla consolidada: {str(e)}"
            logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
            result['steps']['sync_consolidated'] = {
                'success': False,
                'message': error_msg,
                'details': {}
            }
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        
        # Verificar si todas las tareas se completaron exitosamente
        all_success = all(step['success'] for step in result['steps'].values())
        result['success'] = all_success
        
        if all_success:
            result['message'] = f'Validación y corrección completada exitosamente en {elapsed_time:.2f} segundos'
            logger.info(f"✅ [VALIDATE_FIX] {result['message']}")
        else:
            failed_steps = [name for name, step in result['steps'].items() if not step['success']]
            result['message'] = f'Validación y corrección completada con errores en: {", ".join(failed_steps)}'
            logger.warning(f"⚠️ [VALIDATE_FIX] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_FIX] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_FIX] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante validación y corrección: {str(e)}"
        logger.error(f"❌ [VALIDATE_FIX] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        raise