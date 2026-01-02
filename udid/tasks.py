"""
Tareas de Celery para sincronización de datos desde Panaccess.

Este módulo contiene todas las tareas asíncronas que se ejecutan en background
usando Celery. Las tareas se pueden ejecutar de forma periódica (con celery-beat)
o bajo demanda.

PERIODICIDAD CONFIGURADA:
1. sync_all_data_automatic -> Se ejecuta UNA VEZ cuando se levante el proyecto en la VM
2. check_and_sync_smartcards_monthly -> Día 28 de cada mes a las 3:00 AM
3. check_and_sync_subscribers_periodic -> Cada 5 minutos
4. validate_and_sync_all_data_daily -> Cada día a las 22:00 (10:00 PM)

IMPORTANTE: Las tareas tienen un mecanismo de lock para evitar ejecuciones simultáneas.
Si una tarea está en ejecución, las demás esperarán hasta que termine.
"""
import logging
import time
from celery import shared_task
from django.core.cache import cache

from .utils.panaccess.subscriber import (
    sync_subscribers, 
    CallListSubscribers,
    compare_and_update_all_subscribers
)
from .utils.panaccess.smartcard import (
    sync_smartcards, 
    CallListSmartcards, 
    update_smartcards_from_subscribers,
    compare_and_update_all_smartcards
)
from .utils.panaccess.login import (
    sync_subscriber_logins, 
    fetch_new_logins_from_panaccess,
    compare_and_update_all_existing
)
from .utils.panaccess.subscriberinfo import (
    sync_merge_all_subscribers,
    compare_and_update_subscriber_data,
    get_all_subscriber_codes
)
from ...models import ListOfSmartcards, ListOfSubscriber
from .utils.panaccess.exceptions import (
    PanaccessException,
    PanaccessAuthenticationError,
    PanaccessConnectionError,
    PanaccessTimeoutError,
)

logger = logging.getLogger(__name__)

# Lock key para evitar ejecuciones simultáneas de tareas
TASK_LOCK_KEY = 'panaccess_sync_task_lock'
TASK_LOCK_TIMEOUT = 3600 * 6  # 6 horas máximo (por si una tarea se cuelga)


def acquire_task_lock(task_name, timeout=TASK_LOCK_TIMEOUT):
    """
    Adquiere un lock para evitar que múltiples tareas se ejecuten simultáneamente.
    
    Args:
        task_name: Nombre de la tarea que intenta adquirir el lock
        timeout: Tiempo máximo que el lock estará activo (en segundos)
    
    Returns:
        bool: True si se adquirió el lock, False si otra tarea está en ejecución
    """
    lock_key = f"{TASK_LOCK_KEY}:{task_name}"
    
    # Intentar adquirir el lock (si no existe, lo crea con timeout)
    acquired = cache.add(lock_key, task_name, timeout)
    
    if acquired:
        logger.info(f"🔒 [LOCK] Lock adquirido para tarea: {task_name}")
        return True
    else:
        # Verificar qué tarea tiene el lock
        current_task = cache.get(lock_key)
        logger.warning(
            f"⚠️ [LOCK] No se pudo adquirir lock para {task_name}. "
            f"Tarea en ejecución: {current_task}"
        )
        return False


def release_task_lock(task_name):
    """
    Libera el lock de una tarea.
    
    Args:
        task_name: Nombre de la tarea que libera el lock
    """
    lock_key = f"{TASK_LOCK_KEY}:{task_name}"
    cache.delete(lock_key)
    logger.info(f"🔓 [LOCK] Lock liberado para tarea: {task_name}")


@shared_task(
    bind=True,
    name='udid.tasks.sync_all_data_automatic',
    max_retries=3,
    default_retry_delay=300,  # 5 minutos entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=3600,  # Máximo 1 hora de delay
    retry_jitter=True,
)
def sync_all_data_automatic(self):
    """
    Tarea principal que sincroniza todos los datos desde Panaccess usando lógica automática.
    
    Esta tarea está diseñada para ejecutarse UNA SOLA VEZ cuando se configura Celery en el servidor.
    Después se pueden gestionar otras tareas según necesidad.
    
    LÓGICA AUTOMÁTICA:
    - Si BD vacía → descarga completa desde cero
    - Si BD tiene registros → descarga nuevos desde último registro + actualiza existentes
    - Si hay error/interrupción → los reintentos están implementados
    - Si reintentos fallan → al llamar de nuevo, detecta registros y continúa desde último
    
    QUÉ HACE:
    - Sincroniza suscriptores desde Panaccess (automático según estado de BD)
    - Sincroniza smartcards desde Panaccess (automático según estado de BD)
    - Sincroniza credenciales de login desde Panaccess (automático según estado de BD)
    - Consolida información en SubscriberInfo (tabla consolidada)
    
    CÓMO LO HACE:
    - Usa el singleton de Panaccess para autenticación automática
    - Ejecuta las sincronizaciones en orden usando funciones sync_*() que tienen lógica automática:
      1. sync_subscribers() - Detecta si BD vacía o tiene registros
      2. sync_smartcards() - Detecta si BD vacía o tiene registros
      3. sync_subscriber_logins() - Detecta si BD vacía o tiene registros
      4. sync_merge_all_subscribers() - Consolida información
    
    IMPORTANTE:
    - Esta tarea puede tomar varias horas si hay muchos registros (ej: 10,000+)
    - Se recomienda ejecutarla cuando se configura Celery por primera vez
    - Si se interrumpe, al ejecutarla de nuevo continuará desde donde se quedó
    - Los reintentos automáticos están configurados para errores de conexión/timeout
    
    Returns:
        dict: Resultado de la sincronización con información detallada de cada paso
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    
    PERIODICIDAD: Se ejecuta UNA VEZ cuando se levante el proyecto en la VM.
    No es una tarea periódica, se ejecuta manualmente al iniciar el servidor.
    """
    task_name = 'sync_all_data_automatic'
    
    # Verificar si hay otra tarea en ejecución
    if not acquire_task_lock(task_name):
        logger.warning(
            f"⚠️ [SYNC_ALL] Otra tarea está en ejecución. "
            f"Esta tarea se cancelará para evitar conflictos."
        )
        return {
            'success': False,
            'message': 'Otra tarea de sincronización está en ejecución. Esta tarea se canceló.',
            'skipped': True
        }
    
    try:
        logger.info("🚀 [SYNC_ALL] Iniciando sincronización automática completa de datos desde Panaccess")
        
        result = {
            'success': False,
            'message': '',
            'steps': {
                'subscribers': {'success': False, 'message': '', 'result': None},
                'smartcards': {'success': False, 'message': '', 'result': None},
                'subscriber_logins': {'success': False, 'message': '', 'result': None},
                'merge_subscribers': {'success': False, 'message': ''},
            },
            'total_time_seconds': 0,
        }
        
        start_time = time.time()
        # ========================================================================
        # PASO 1: SINCRONIZACIÓN DE SUSCRIPTORES (LÓGICA AUTOMÁTICA)
        # ========================================================================
        logger.info("📥 [SYNC_ALL] Paso 1/4: Sincronizando suscriptores...")
        try:
            subscribers_result = sync_subscribers(session_id=None, limit=100)
            result['steps']['subscribers'] = {
                'success': True,
                'message': 'Suscriptores sincronizados correctamente',
                'result': subscribers_result
            }
            logger.info("✅ [SYNC_ALL] Suscriptores sincronizados correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando suscriptores: {str(e)}"
            logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
            result['steps']['subscribers'] = {
                'success': False,
                'message': error_msg,
                'result': None
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 2: SINCRONIZACIÓN DE SMARTCARDS (LÓGICA AUTOMÁTICA)
        # ========================================================================
        logger.info("📥 [SYNC_ALL] Paso 2/4: Sincronizando smartcards...")
        try:
            smartcards_result = sync_smartcards(session_id=None, limit=100)
            result['steps']['smartcards'] = {
                'success': True,
                'message': 'Smartcards sincronizadas correctamente',
                'result': smartcards_result
            }
            logger.info("✅ [SYNC_ALL] Smartcards sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando smartcards: {str(e)}"
            logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
            result['steps']['smartcards'] = {
                'success': False,
                'message': error_msg,
                'result': None
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 3: SINCRONIZACIÓN DE CREDENCIALES DE LOGIN (LÓGICA AUTOMÁTICA)
        # ========================================================================
        logger.info("📥 [SYNC_ALL] Paso 3/4: Sincronizando credenciales de login...")
        try:
            logins_result = sync_subscriber_logins(session_id=None)
            result['steps']['subscriber_logins'] = {
                'success': True,
                'message': 'Credenciales de login sincronizadas correctamente',
                'result': logins_result
            }
            logger.info("✅ [SYNC_ALL] Credenciales de login sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error sincronizando credenciales: {str(e)}"
            logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
            result['steps']['subscriber_logins'] = {
                'success': False,
                'message': error_msg,
                'result': None
            }
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 4: CONSOLIDACIÓN EN SUBSCRIBERINFO (TABLA CONSOLIDADA)
        # ========================================================================
        logger.info("📥 [SYNC_ALL] Paso 4/4: Consolidando información en SubscriberInfo...")
        try:
            sync_merge_all_subscribers()
            result['steps']['merge_subscribers'] = {
                'success': True,
                'message': 'Información consolidada correctamente'
            }
            logger.info("✅ [SYNC_ALL] Información consolidada en SubscriberInfo")
        except Exception as e:
            error_msg = f"Error consolidando información: {str(e)}"
            logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
            result['steps']['merge_subscribers'] = {
                'success': False,
                'message': error_msg
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
            result['message'] = f'Sincronización automática completada exitosamente en {elapsed_time:.2f} segundos'
            logger.info(f"✅ [SYNC_ALL] {result['message']}")
        else:
            failed_steps = [name for name, step in result['steps'].items() if not step['success']]
            result['message'] = f'Sincronización completada con errores en: {", ".join(failed_steps)}'
            logger.warning(f"⚠️ [SYNC_ALL] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [SYNC_ALL] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [SYNC_ALL] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante sincronización: {str(e)}"
        logger.error(f"❌ [SYNC_ALL] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
    finally:
        # Liberar el lock siempre, incluso si hay error
        release_task_lock(task_name)


@shared_task(
    bind=True,
    name='udid.tasks.check_and_sync_smartcards_monthly',
    max_retries=3,
    default_retry_delay=300,  # 5 minutos entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=3600,  # Máximo 1 hora de delay
    retry_jitter=True,
)
def check_and_sync_smartcards_monthly(self):
    """
    Tarea mensual que verifica la cantidad de smartcards en Panaccess vs base de datos.
    
    Si existen más smartcards en Panaccess que en la base de datos, descarga las nuevas
    desde la última smartcard registrada en BD.
    
    PERIODICIDAD: Día 28 de cada mes a las 3:00 AM (configurar en Celery Beat).
    Ejemplo: crontab(day_of_month='28', hour=3, minute=0)
    
    QUÉ HACE:
    1. Obtiene el total de smartcards en Panaccess (haciendo una llamada a la API)
    2. Obtiene el total de smartcards en la base de datos local
    3. Compara ambos totales
    4. Si Panaccess tiene más smartcards:
       - Ejecuta sync_smartcards() que automáticamente descarga desde la última registrada
       - La función sync_smartcards() detecta el último SN en BD y descarga solo los nuevos
    
    LÓGICA AUTOMÁTICA:
    - sync_smartcards() detecta automáticamente si hay registros en BD
    - Si hay registros, descarga solo los nuevos desde el último SN
    - Si no hay registros, descarga todo desde cero
    
    IMPORTANTE:
    - Esta tarea puede tomar tiempo si hay muchas smartcards nuevas (ej: miles)
    - Se recomienda ejecutarla en horarios de bajo tráfico (madrugada: 2:00 AM - 4:00 AM)
    - Los reintentos automáticos están configurados para errores de conexión/timeout
    - Si se interrumpe, al ejecutarla de nuevo continuará desde donde se quedó
    
    Returns:
        dict: Resultado de la verificación y sincronización con información detallada:
            - panaccess_total: Total de smartcards en Panaccess
            - database_total: Total de smartcards en BD local
            - difference: Diferencia entre Panaccess y BD
            - sync_executed: Si se ejecutó la sincronización
            - sync_result: Resultado de la sincronización (si se ejecutó)
            - success: Si la tarea se completó exitosamente
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    task_name = 'check_and_sync_smartcards_monthly'
    
    # Verificar si hay otra tarea en ejecución
    if not acquire_task_lock(task_name):
        logger.warning(
            f"⚠️ [CHECK_SMARTCARDS] Otra tarea está en ejecución. "
            f"Esta tarea se cancelará para evitar conflictos."
        )
        return {
            'success': False,
            'message': 'Otra tarea de sincronización está en ejecución. Esta tarea se canceló.',
            'skipped': True
        }
    
    try:
        logger.info("🔍 [CHECK_SMARTCARDS] Iniciando verificación mensual de smartcards")
        
        result = {
            'success': False,
            'message': '',
            'panaccess_total': 0,
            'database_total': 0,
            'difference': 0,
            'sync_executed': False,
            'sync_result': None,
            'total_time_seconds': 0,
        }
        
        start_time = time.time()
        # ========================================================================
        # PASO 1: OBTENER TOTAL DE SMARTCARDS EN PANACCESS
        # ========================================================================
        logger.info("📊 [CHECK_SMARTCARDS] Obteniendo total de smartcards en Panaccess...")
        try:
            # Hacer una llamada con offset=0, limit=1 solo para obtener el count total
            panaccess_response = CallListSmartcards(session_id=None, offset=0, limit=1, timeout=30)
            panaccess_total = panaccess_response.get('count', 0)
            result['panaccess_total'] = panaccess_total
            logger.info(f"✅ [CHECK_SMARTCARDS] Total en Panaccess: {panaccess_total} smartcards")
        except Exception as e:
            error_msg = f"Error obteniendo total de Panaccess: {str(e)}"
            logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}", exc_info=True)
            result['message'] = error_msg
            result['success'] = False
            elapsed_time = time.time() - start_time
            result['total_time_seconds'] = int(elapsed_time)
            raise
        
        # ========================================================================
        # PASO 2: OBTENER TOTAL DE SMARTCARDS EN BASE DE DATOS LOCAL
        # ========================================================================
        logger.info("📊 [CHECK_SMARTCARDS] Obteniendo total de smartcards en base de datos local...")
        try:
            database_total = ListOfSmartcards.objects.count()
            result['database_total'] = database_total
            logger.info(f"✅ [CHECK_SMARTCARDS] Total en BD local: {database_total} smartcards")
        except Exception as e:
            error_msg = f"Error obteniendo total de BD local: {str(e)}"
            logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}", exc_info=True)
            result['message'] = error_msg
            result['success'] = False
            elapsed_time = time.time() - start_time
            result['total_time_seconds'] = int(elapsed_time)
            raise
        
        # ========================================================================
        # PASO 3: COMPARAR Y DECIDIR SI SINCRONIZAR
        # ========================================================================
        difference = panaccess_total - database_total
        result['difference'] = difference
        
        logger.info(
            f"📊 [CHECK_SMARTCARDS] Comparación: "
            f"Panaccess={panaccess_total}, BD={database_total}, Diferencia={difference}"
        )
        
        if difference > 0:
            logger.info(
                f"🔄 [CHECK_SMARTCARDS] Se detectaron {difference} smartcards nuevas en Panaccess. "
                f"Iniciando sincronización desde la última smartcard registrada..."
            )
            
            # ========================================================================
            # PASO 4: SINCRONIZAR SMARTCARDS (DESCARGA AUTOMÁTICA DESDE ÚLTIMA)
            # ========================================================================
            try:
                # sync_smartcards() automáticamente detecta si hay registros en BD
                # y descarga solo los nuevos desde el último SN registrado
                sync_result = sync_smartcards(session_id=None, limit=100)
                result['sync_executed'] = True
                result['sync_result'] = sync_result
                
                logger.info(
                    f"✅ [CHECK_SMARTCARDS] Sincronización completada. "
                    f"Se descargaron smartcards nuevas desde la última registrada."
                )
            except Exception as e:
                error_msg = f"Error durante sincronización de smartcards: {str(e)}"
                logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}", exc_info=True)
                result['sync_executed'] = True
                result['sync_result'] = {'error': error_msg}
                # No marcar como fallo total, la verificación fue exitosa
        else:
            logger.info(
                f"✅ [CHECK_SMARTCARDS] No hay smartcards nuevas. "
                f"BD local está actualizada ({database_total} smartcards)."
            )
            result['sync_executed'] = False
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        result['success'] = True
        
        if difference > 0 and result['sync_executed']:
            result['message'] = (
                f'Verificación completada. Se encontraron {difference} smartcards nuevas. '
                f'Sincronización ejecutada correctamente en {elapsed_time:.2f} segundos'
            )
        elif difference > 0:
            result['message'] = (
                f'Verificación completada. Se encontraron {difference} smartcards nuevas, '
                f'pero hubo un error durante la sincronización'
            )
        else:
            result['message'] = (
                f'Verificación completada. No hay smartcards nuevas. '
                f'BD local está actualizada ({database_total} smartcards)'
            )
        
        logger.info(f"✅ [CHECK_SMARTCARDS] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante verificación de smartcards: {str(e)}"
        logger.error(f"❌ [CHECK_SMARTCARDS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise


@shared_task(
    bind=True,
    name='udid.tasks.check_and_sync_subscribers_periodic',
    max_retries=2,
    default_retry_delay=60,  # 1 minuto entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,  # Máximo 5 minutos de delay
    retry_jitter=True,
)
def check_and_sync_subscribers_periodic(self):
    """
    Tarea periódica que verifica y sincroniza suscriptores cada 5 minutos.
    
    Si existen más suscriptores en Panaccess que en la base de datos, descarga los nuevos
    desde el último suscriptor registrado. Al terminar, obtiene las credenciales de login
    de esos suscriptores nuevos y las almacena en la base de datos.
    
    PERIODICIDAD: Cada 5 minutos (configurar en Celery Beat).
    Ejemplo: schedule=300.0 (300 segundos = 5 minutos)
    
    QUÉ HACE:
    1. Obtiene el total de suscriptores en la base de datos local
    2. Verifica si hay nuevos suscriptores en Panaccess (usando sync_subscribers)
    3. Si hay nuevos suscriptores:
       - Descarga los nuevos desde el último código registrado (automático)
       - Almacena los nuevos suscriptores en ListOfSubscriber
    4. Obtiene las credenciales de login de los nuevos suscriptores
       - Almacena las credenciales en SubscriberLoginInfo
    
    LÓGICA AUTOMÁTICA:
    - sync_subscribers() detecta automáticamente si hay registros en BD
    - Si hay registros, descarga solo los nuevos desde el último código
    - fetch_new_logins_from_panaccess() obtiene credenciales solo de nuevos suscriptores
    
    IMPORTANTE:
    - Esta tarea se ejecuta frecuentemente (cada 5 minutos)
    - Es rápida ya que solo procesa nuevos registros
    - Los reintentos automáticos están configurados para errores de conexión/timeout
    - Si se interrumpe, al ejecutarse de nuevo continuará desde donde se quedó
    
    Returns:
        dict: Resultado de la verificación y sincronización con información detallada:
            - database_total_before: Total de suscriptores en BD local antes de sincronizar
            - database_total_after: Total de suscriptores en BD local después de sincronizar
            - sync_executed: Si se ejecutó la sincronización
            - sync_result: Resultado de sync_subscribers() (si se ejecutó)
            - credentials_downloaded: Cantidad de credenciales descargadas y almacenadas
            - success: Si la tarea se completó exitosamente
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    task_name = 'check_and_sync_subscribers_periodic'
    
    # Verificar si hay otra tarea en ejecución
    if not acquire_task_lock(task_name):
        logger.warning(
            f"⚠️ [CHECK_SUBSCRIBERS] Otra tarea está en ejecución. "
            f"Esta tarea se cancelará para evitar conflictos."
        )
        return {
            'success': False,
            'message': 'Otra tarea de sincronización está en ejecución. Esta tarea se canceló.',
            'skipped': True
        }
    
    try:
        logger.info("🔄 [CHECK_SUBSCRIBERS] Iniciando verificación periódica de suscriptores")
        
        result = {
            'success': False,
            'message': '',
            'database_total_before': 0,
            'database_total_after': 0,
            'sync_executed': False,
            'sync_result': None,
            'credentials_downloaded': 0,
            'smartcards_updated': None,
            'merge_executed': False,
            'total_time_seconds': 0,
        }
        
        start_time = time.time()
        # ========================================================================
        # PASO 1: VALIDAR Y DESCARGAR NUEVOS SUSCRIPTORES
        # ========================================================================
        logger.info("📊 [CHECK_SUBSCRIBERS] Validando si existen nuevos suscriptores...")
        try:
            from .utils.panaccess.subscriber import LastSubscriber
            
            # Obtener último suscriptor antes de sincronizar
            last_subscriber_before = LastSubscriber()
            last_code_before = last_subscriber_before.code if last_subscriber_before else None
            database_total_before = ListOfSubscriber.objects.count()
            result['database_total_before'] = database_total_before
            
            logger.info(
                f"✅ [CHECK_SUBSCRIBERS] Estado actual: {database_total_before} suscriptores, "
                f"último código: {last_code_before}"
            )
            
            # sync_subscribers() automáticamente detecta si hay registros en BD
            # y descarga solo los nuevos desde el último código registrado
            logger.info("🔄 [CHECK_SUBSCRIBERS] Sincronizando suscriptores desde Panaccess...")
            sync_result = sync_subscribers(session_id=None, limit=100)
            result['sync_executed'] = True
            result['sync_result'] = sync_result
            
            # Obtener total después de sincronizar
            database_total_after = ListOfSubscriber.objects.count()
            result['database_total_after'] = database_total_after
            new_subscribers_count = database_total_after - database_total_before
            
            if new_subscribers_count > 0:
                logger.info(
                    f"✅ [CHECK_SUBSCRIBERS] Se encontraron y descargaron {new_subscribers_count} nuevos suscriptores"
                )
            else:
                logger.info(
                    f"ℹ️ [CHECK_SUBSCRIBERS] No hay nuevos suscriptores. BD está actualizada."
                )
                # Si no hay nuevos, terminar aquí
                result['success'] = True
                result['message'] = 'No hay nuevos suscriptores. BD está actualizada.'
                result['total_time_seconds'] = int(time.time() - start_time)
                return result
                
            # Guardar last_code_before para usar en pasos siguientes
            result['last_code_before'] = last_code_before
                
        except Exception as e:
            error_msg = f"Error durante sincronización de suscriptores: {str(e)}"
            logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['sync_executed'] = True
            result['sync_result'] = {'error': error_msg}
            database_total_after = ListOfSubscriber.objects.count()
            result['database_total_after'] = database_total_after
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 3: OBTENER CREDENCIALES DE LOGIN DE NUEVOS SUSCRIPTORES
        # ========================================================================
        logger.info("🔑 [CHECK_SUBSCRIBERS] Obteniendo credenciales de login de nuevos suscriptores...")
        try:
            # fetch_new_logins_from_panaccess() obtiene credenciales solo de nuevos suscriptores
            # que no están aún en SubscriberLoginInfo y las almacena en la BD
            credentials_count = fetch_new_logins_from_panaccess(session_id=None)
            result['credentials_downloaded'] = credentials_count if isinstance(credentials_count, int) else 0
            
            if result['credentials_downloaded'] > 0:
                logger.info(
                    f"✅ [CHECK_SUBSCRIBERS] {result['credentials_downloaded']} credenciales "
                    f"de nuevos suscriptores descargadas y almacenadas en BD"
                )
            else:
                logger.info(
                    f"ℹ️ [CHECK_SUBSCRIBERS] No hay credenciales nuevas para descargar"
                )
        except Exception as e:
            error_msg = f"Error obteniendo credenciales de nuevos suscriptores: {str(e)}"
            logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['credentials_downloaded'] = 0
            # No marcar como fallo total si solo falla la descarga de credenciales
        
        # ========================================================================
        # PASO 4: ACTUALIZAR SMARTCARDS EXISTENTES CON INFORMACIÓN DE NUEVOS SUSCRIPTORES
        # ========================================================================
        logger.info("📱 [CHECK_SUBSCRIBERS] Revisando smartcards de nuevos suscriptores y asociándolas...")
        try:
            from .utils.panaccess.smartcard import extract_sns_from_smartcards_field
            
            # Obtener último código antes de sincronizar (guardado en resultado)
            last_code_before = result.get('last_code_before')
            
            # Obtener solo los nuevos suscriptores (código mayor al último que había antes)
            if last_code_before:
                new_subscribers = ListOfSubscriber.objects.filter(code__gt=last_code_before).order_by('code')
            else:
                # Si no había suscriptores antes, todos son nuevos
                new_subscribers = ListOfSubscriber.objects.all().order_by('code')
            
            new_subscribers_count = new_subscribers.count()
            smartcards_updated_count = 0
            smartcards_found_count = 0
            
            if new_subscribers_count > 0:
                # Obtener todas las smartcards existentes en memoria para actualización rápida
                existing_smartcards = {
                    obj.sn: obj for obj in ListOfSmartcards.objects.all() if obj.sn
                }
                
                logger.info(
                    f"📱 [CHECK_SUBSCRIBERS] Procesando {new_subscribers_count} nuevos suscriptores "
                    f"para asociar sus smartcards"
                )
                
                for subscriber in new_subscribers:
                    if not subscriber.code:
                        continue
                    
                    try:
                        # Extraer SNs del campo smartcards (JSON) del suscriptor
                        smartcards_data = subscriber.smartcards
                        sns = extract_sns_from_smartcards_field(smartcards_data)
                        
                        if not sns:
                            logger.debug(
                                f"[CHECK_SUBSCRIBERS] Suscriptor {subscriber.code} no tiene smartcards asociadas"
                            )
                            continue
                        
                        smartcards_found_count += len(sns)
                        
                        # Buscar y actualizar cada smartcard existente con información del suscriptor
                        for sn in sns:
                            if sn in existing_smartcards:
                                smartcard = existing_smartcards[sn]
                                changed_fields = []
                                
                                # Actualizar campos del suscriptor si han cambiado
                                if str(smartcard.subscriberCode) != str(subscriber.code):
                                    smartcard.subscriberCode = subscriber.code
                                    changed_fields.append('subscriberCode')
                                
                                if str(smartcard.lastName) != str(subscriber.lastName):
                                    smartcard.lastName = subscriber.lastName
                                    changed_fields.append('lastName')
                                
                                if str(smartcard.firstName) != str(subscriber.firstName):
                                    smartcard.firstName = subscriber.firstName
                                    changed_fields.append('firstName')
                                
                                if str(smartcard.hcId) != str(subscriber.hcId):
                                    smartcard.hcId = subscriber.hcId
                                    changed_fields.append('hcId')
                                
                                # Guardar solo si hay cambios
                                if changed_fields:
                                    smartcard.save(update_fields=changed_fields)
                                    smartcards_updated_count += 1
                                    logger.debug(
                                        f"[CHECK_SUBSCRIBERS] Smartcard {sn} asociada al suscriptor {subscriber.code}. "
                                        f"Campos actualizados: {changed_fields}"
                                    )
                            else:
                                logger.warning(
                                    f"⚠️ [CHECK_SUBSCRIBERS] Smartcard {sn} del suscriptor {subscriber.code} "
                                    f"no existe en ListOfSmartcards. Debería existir."
                                )
                    
                    except Exception as e:
                        logger.error(
                            f"❌ [CHECK_SUBSCRIBERS] Error procesando smartcards del suscriptor "
                            f"{subscriber.code}: {str(e)}", exc_info=True
                        )
                
                result['smartcards_updated'] = {
                    'new_subscribers_processed': new_subscribers_count,
                    'sns_found': smartcards_found_count,
                    'smartcards_updated': smartcards_updated_count,
                }
                
                logger.info(
                    f"✅ [CHECK_SUBSCRIBERS] Smartcards asociadas: "
                    f"{smartcards_updated_count} smartcards existentes asociadas a {new_subscribers_count} nuevos suscriptores"
                )
            else:
                result['smartcards_updated'] = {
                    'new_subscribers_processed': 0,
                    'sns_found': 0,
                    'smartcards_updated': 0,
                }
                logger.info(
                    f"ℹ️ [CHECK_SUBSCRIBERS] No hay nuevos suscriptores, no se actualizan smartcards"
                )
        except Exception as e:
            error_msg = f"Error actualizando smartcards desde nuevos suscriptores: {str(e)}"
            logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['smartcards_updated'] = {'error': error_msg}
            # No marcar como fallo total si solo falla la actualización de smartcards
        
        # ========================================================================
        # PASO 5: HACER MERGE DE NUEVOS SUSCRIPTORES EN SUBSCRIBERINFO
        # ========================================================================
        logger.info("🔄 [CHECK_SUBSCRIBERS] Haciendo merge de nuevos suscriptores en SubscriberInfo...")
        try:
            # sync_merge_all_subscribers() detecta automáticamente los nuevos suscriptores
            # (mayores al último código en SubscriberInfo) y hace merge de sus datos
            sync_merge_all_subscribers()
            result['merge_executed'] = True
            logger.info(
                f"✅ [CHECK_SUBSCRIBERS] Merge completado. "
                f"Nuevos suscriptores consolidados en SubscriberInfo"
            )
        except Exception as e:
            error_msg = f"Error haciendo merge en SubscriberInfo: {str(e)}"
            logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['merge_executed'] = False
            result['merge_error'] = error_msg
            # No marcar como fallo total si solo falla el merge
        
        # ========================================================================
        # VERIFICACIÓN FINAL
        # ========================================================================
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        result['success'] = True
        
        new_subscribers = result['database_total_after'] - result['database_total_before']
        
        if new_subscribers > 0:
            message_parts = [
                f'Se descargaron {new_subscribers} nuevos suscriptores',
                f'{result["credentials_downloaded"]} credenciales almacenadas'
            ]
            
            if result.get('smartcards_updated') and not result['smartcards_updated'].get('error'):
                sc_info = result['smartcards_updated']
                message_parts.append(
                    f'{sc_info.get("smartcards_created", 0)} smartcards creadas'
                )
            
            if result.get('merge_executed'):
                message_parts.append('merge en SubscriberInfo completado')
            
            result['message'] = (
                f'Verificación completada. {", ".join(message_parts)} '
                f'en {elapsed_time:.2f} segundos'
            )
        else:
            result['message'] = (
                f'Verificación completada. No hay nuevos suscriptores. '
                f'BD local está actualizada ({result["database_total_after"]} suscriptores)'
            )
        
        logger.info(f"✅ [CHECK_SUBSCRIBERS] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante verificación de suscriptores: {str(e)}"
        logger.error(f"❌ [CHECK_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
    finally:
        # Liberar el lock siempre, incluso si hay error
        release_task_lock(task_name)


@shared_task(
    bind=True,
    name='udid.tasks.validate_and_sync_all_data_daily',
    max_retries=2,
    default_retry_delay=600,  # 10 minutos entre reintentos
    autoretry_for=(PanaccessConnectionError, PanaccessTimeoutError),
    retry_backoff=True,
    retry_backoff_max=7200,  # Máximo 2 horas de delay
    retry_jitter=True,
)
def validate_and_sync_all_data_daily(self):
    """
    Tarea diaria de validación y corrección de todos los datos existentes.
    
    Esta tarea está diseñada para ejecutarse UNA VEZ AL DÍA, preferiblemente de noche o madrugada
    (ej: 2:00 AM - 4:00 AM) cuando hay bajo tráfico.
    
    IMPORTANTE: Esta tarea asume que la base de datos ya tiene datos. No descarga nuevos registros,
    solo compara y actualiza los existentes con la información de Panaccess.
    
    QUÉ HACE:
    1. Compara y actualiza suscriptores existentes con datos de Panaccess
    2. Compara y actualiza smartcards existentes con datos de Panaccess
    3. Compara y actualiza credenciales existentes con datos de Panaccess
    4. Valida y ajusta SubscriberInfo comparando con datos de las otras tablas
    
    PROCESAMIENTO POR LOTES:
    - Todas las funciones procesan por lotes (limit) para no sobrecargar memoria
    - Se agregan pausas entre lotes para dar tiempo al sistema
    - Las funciones ya implementan procesamiento eficiente
    
    IMPORTANTE:
    - Esta tarea puede tomar varias horas si hay muchos registros (ej: 400,000+)
    - Se recomienda ejecutarla en horarios de bajo tráfico (madrugada: 2:00 AM - 4:00 AM)
    - Los reintentos automáticos están configurados para errores de conexión/timeout
    - Solo actualiza registros existentes, NO descarga nuevos
    
    Returns:
        dict: Resultado de la validación y corrección con información detallada de cada paso:
            - steps: Diccionario con el resultado de cada paso
            - success: Si la tarea se completó exitosamente
            - total_time_seconds: Tiempo total de ejecución
        
    Raises:
        PanaccessException: Si hay errores críticos de autenticación o conexión
    """
    logger.info("🔍 [VALIDATE_DAILY] Iniciando validación y corrección diaria de datos existentes")
    
    result = {
        'success': False,
        'message': '',
        'steps': {
            'subscribers': {'success': False, 'message': '', 'updated': 0},
            'smartcards': {'success': False, 'message': '', 'updated': 0},
            'credentials': {'success': False, 'message': '', 'updated': 0},
            'subscriber_info': {'success': False, 'message': '', 'updated': 0},
        },
        'total_time_seconds': 0,
    }
    
    start_time = time.time()
    batch_delay = 2  # Pausa de 2 segundos entre lotes para no sobrecargar
    
    try:
        # ========================================================================
        # PASO 1: COMPARAR Y ACTUALIZAR SUSCRIPTORES EXISTENTES
        # ========================================================================
        logger.info("📥 [VALIDATE_DAILY] Paso 1/4: Comparando y actualizando suscriptores existentes...")
        try:
            # compare_and_update_all_subscribers() procesa por lotes (limit)
            # Compara cada suscriptor existente con Panaccess y actualiza solo si hay diferencias
            compare_and_update_all_subscribers(session_id=None, limit=100, timeout=30)
            result['steps']['subscribers'] = {
                'success': True,
                'message': 'Suscriptores comparados y actualizados correctamente',
                'updated': 'N/A'  # La función no retorna el conteo directamente
            }
            logger.info("✅ [VALIDATE_DAILY] Suscriptores comparados y actualizados correctamente")
            time.sleep(batch_delay)  # Pausa entre pasos
        except Exception as e:
            error_msg = f"Error comparando suscriptores: {str(e)}"
            logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
            result['steps']['subscribers'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 2: COMPARAR Y ACTUALIZAR SMARTCARDS EXISTENTES
        # ========================================================================
        logger.info("📥 [VALIDATE_DAILY] Paso 2/4: Comparando y actualizando smartcards existentes...")
        try:
            # compare_and_update_all_smartcards() procesa por lotes (limit)
            # Compara cada smartcard existente con Panaccess y actualiza solo si hay diferencias
            compare_and_update_all_smartcards(session_id=None, limit=100, timeout=30)
            result['steps']['smartcards'] = {
                'success': True,
                'message': 'Smartcards comparadas y actualizadas correctamente',
                'updated': 'N/A'  # La función no retorna el conteo directamente
            }
            logger.info("✅ [VALIDATE_DAILY] Smartcards comparadas y actualizadas correctamente")
            time.sleep(batch_delay)  # Pausa entre pasos
        except Exception as e:
            error_msg = f"Error comparando smartcards: {str(e)}"
            logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
            result['steps']['smartcards'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
            # Continuar con los siguientes pasos aunque este falle
        
        # ========================================================================
        # PASO 3: COMPARAR Y ACTUALIZAR CREDENCIALES EXISTENTES
        # ========================================================================
        logger.info("📥 [VALIDATE_DAILY] Paso 3/4: Comparando y actualizando credenciales existentes...")
        try:
            # compare_and_update_all_existing() procesa uno por uno
            # Compara cada credencial existente con Panaccess y actualiza solo si hay diferencias
            credentials_updated = compare_and_update_all_existing(session_id=None)
            result['steps']['credentials'] = {
                'success': True,
                'message': 'Credenciales comparadas y actualizadas correctamente',
                'updated': credentials_updated if isinstance(credentials_updated, int) else 'N/A'
            }
            logger.info(
                f"✅ [VALIDATE_DAILY] Credenciales comparadas y actualizadas: "
                f"{credentials_updated} registros actualizados"
            )
            time.sleep(batch_delay)  # Pausa entre pasos
        except Exception as e:
            error_msg = f"Error comparando credenciales: {str(e)}"
            logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
            result['steps']['credentials'] = {
                'success': False,
                'message': error_msg,
                'updated': 0
            }
            # Continuar con el siguiente paso aunque este falle
        
        # ========================================================================
        # PASO 4: VALIDAR Y AJUSTAR SUBSCRIBERINFO
        # ========================================================================
        logger.info("🔄 [VALIDATE_DAILY] Paso 4/4: Validando y ajustando SubscriberInfo...")
        try:
            # Obtener todos los códigos de suscriptores y procesar por lotes
            all_codes = sorted(get_all_subscriber_codes())
            total_codes = len(all_codes)
            batch_size = 100  # Procesar 100 suscriptores por lote
            total_updated = 0
            
            logger.info(f"📊 [VALIDATE_DAILY] Procesando {total_codes} suscriptores en lotes de {batch_size}...")
            
            for i in range(0, total_codes, batch_size):
                batch_codes = all_codes[i:i + batch_size]
                batch_updated = 0
                
                for code in batch_codes:
                    try:
                        updated = compare_and_update_subscriber_data(code)
                        if updated:
                            batch_updated += updated
                            total_updated += updated
                    except Exception as e:
                        logger.warning(f"⚠️ [VALIDATE_DAILY] Error procesando suscriptor {code}: {str(e)}")
                        continue
                
                logger.info(
                    f"📊 [VALIDATE_DAILY] Lote {i//batch_size + 1}/{(total_codes-1)//batch_size + 1}: "
                    f"{batch_updated} registros actualizados en SubscriberInfo"
                )
                
                # Pausa entre lotes para no sobrecargar memoria
                if i + batch_size < total_codes:
                    time.sleep(batch_delay)
            
            result['steps']['subscriber_info'] = {
                'success': True,
                'message': 'SubscriberInfo validado y ajustado correctamente',
                'updated': total_updated
            }
            logger.info(
                f"✅ [VALIDATE_DAILY] SubscriberInfo validado: {total_updated} registros actualizados"
            )
        except Exception as e:
            error_msg = f"Error validando SubscriberInfo: {str(e)}"
            logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
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
            result['message'] = (
                f'Validación y corrección diaria completada exitosamente en {elapsed_time:.2f} segundos. '
                f'Todos los registros existentes fueron comparados y actualizados con Panaccess.'
            )
            logger.info(f"✅ [VALIDATE_DAILY] {result['message']}")
        else:
            failed_steps = [name for name, step in result['steps'].items() if not step['success']]
            result['message'] = (
                f'Validación completada con errores en: {", ".join(failed_steps)}. '
                f'Tiempo: {elapsed_time:.2f} segundos'
            )
            logger.warning(f"⚠️ [VALIDATE_DAILY] {result['message']}")
        
        return result
        
    except PanaccessAuthenticationError as e:
        # Error de autenticación - no reintentar automáticamente
        error_msg = f"Error de autenticación con Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_DAILY] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise PanaccessAuthenticationError(error_msg) from e
        
    except (PanaccessConnectionError, PanaccessTimeoutError) as e:
        # Errores de conexión/timeout - reintentar automáticamente
        error_msg = f"Error de conexión/timeout con Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_DAILY] {error_msg}")
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        # Celery reintentará automáticamente gracias a autoretry_for
        raise
        
    except PanaccessException as e:
        # Otros errores de Panaccess
        error_msg = f"Error de Panaccess: {str(e)}"
        logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
        
    except Exception as e:
        # Error inesperado
        error_msg = f"Error inesperado durante validación diaria: {str(e)}"
        logger.error(f"❌ [VALIDATE_DAILY] {error_msg}", exc_info=True)
        result['message'] = error_msg
        result['success'] = False
        elapsed_time = time.time() - start_time
        result['total_time_seconds'] = int(elapsed_time)
        raise
