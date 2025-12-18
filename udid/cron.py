from django_cron import CronJobBase, Schedule
from .utils.auth  import CVClient
from .utils.smartcard import sync_smartcards, update_smartcards_from_subscribers
from .utils.subscriber import sync_subscribers
from .utils.login import sync_subscriber_logins
from .utils.subscriberinfo import sync_merge_all_subscribers
import logging

logger = logging.getLogger(__name__)

def execute_sync_tasks():
    """
    Ejecuta todas las tareas de sincronización y validación.
    Valida que la información en la base de datos esté correcta comparándola con Panaccess
    y la ajusta automáticamente si hay diferencias.
    
    Esta función puede ser llamada tanto por el cron como por el endpoint manual.
    
    Returns:
        dict: Resultado de la sincronización con información de cada tarea
    """
    result = {
        'success': False,
        'message': '',
        'tasks': {
            'smartcards': {'success': False, 'message': ''},
            'subscribers': {'success': False, 'message': ''},
            'subscriber_logins': {'success': False, 'message': ''},
            'merge_subscribers': {'success': False, 'message': ''}
        },
        'session_id': None
    }
    
    try:
        logger.info("[SYNC] Iniciando validación y sincronización completa de información")
        
        # Login a Panaccess
        client = CVClient()
        success, error_message = client.login()
        
        if not success:
            error_msg = f"Error al hacer login: {error_message}"
            logger.error(f"[SYNC] {error_msg}")
            result['message'] = error_msg
            return result
        
        session_id = client.session_id
        result['session_id'] = session_id
        logger.info(f"[SYNC] Session ID: {session_id}")
        
        # 1. Validar y sincronizar smartcards desde Panaccess
        # Compara con Panaccess y actualiza productos, paquetes, estado, etc.
        try:
            logger.info("[SYNC] Validando y sincronizando smartcards desde Panaccess")
            sync_smartcards(session_id)
            result['tasks']['smartcards'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Smartcards validadas y sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error validando smartcards: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['smartcards'] = {'success': False, 'message': error_msg}
        
        # 2. Validar y sincronizar suscriptores desde Panaccess
        # Compara con Panaccess y actualiza información de suscriptores
        try:
            logger.info("[SYNC] Validando y sincronizando suscriptores desde Panaccess")
            sync_subscribers(session_id)
            result['tasks']['subscribers'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Suscriptores validados y sincronizados correctamente")
        except Exception as e:
            error_msg = f"Error validando suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscribers'] = {'success': False, 'message': error_msg}
        
        # 3. Validar y sincronizar credenciales de suscriptores desde Panaccess
        # Compara con Panaccess y actualiza credenciales
        try:
            logger.info("[SYNC] Validando y sincronizando credenciales de suscriptores desde Panaccess")
            sync_subscriber_logins(session_id)
            result['tasks']['subscriber_logins'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Credenciales validadas y sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error validando credenciales: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscriber_logins'] = {'success': False, 'message': error_msg}
        
        # 4. Validar y actualizar merge de suscriptores en SubscriberInfo
        # Compara y actualiza la tabla consolidada
        try:
            logger.info("[SYNC] Validando y actualizando merge de suscriptores en SubscriberInfo")
            sync_merge_all_subscribers()
            result['tasks']['merge_subscribers'] = {'success': True, 'message': 'Validación y actualización completada'}
            logger.info("[SYNC] SubscriberInfo validado y actualizado correctamente")
        except Exception as e:
            error_msg = f"Error validando merge de suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['merge_subscribers'] = {'success': False, 'message': error_msg}
        
        # Verificar si todas las tareas fueron exitosas
        all_success = all(task['success'] for task in result['tasks'].values())
        result['success'] = all_success
        result['message'] = 'Sincronización completada' if all_success else 'Sincronización completada con algunos errores'
        
        logger.info(f"[SYNC] Validación y sincronización completa finalizada. Éxito: {all_success}")
        
    except Exception as e:
        error_msg = f"Error inesperado durante la sincronización: {str(e)}"
        logger.error(f"[SYNC] {error_msg}", exc_info=True)
        result['message'] = error_msg
    
    return result

def execute_update_subscribers():
    """
    Ejecuta la sincronización completa de suscriptores: descarga nuevos, actualiza los existentes,
    actualiza las credenciales de login, actualiza smartcards desde información de suscriptores
    y hace merge en SubscriberInfo (tabla consolidada).
    
    NOTA: No sincroniza smartcards completas desde Panaccess aquí porque con 10,000 smartcards
    puede tomar 8-9 horas. La actualización desde suscriptores es más rápida y suficiente para
    asociar UDIDs. Para sincronizar productos/paquetes completos, usar la tarea manual o
    SyncSmartcardsCronJob (configurada para ejecutarse menos frecuentemente).
    
    Esta función puede ser llamada tanto por el cron como por el endpoint manual.
    
    Returns:
        dict: Resultado de la sincronización con información del proceso
    """
    result = {
        'success': False,
        'message': '',
        'session_id': None,
        'tasks': {
            'subscribers': {'success': False, 'message': ''},
            'credentials': {'success': False, 'message': ''},
            'smartcards_from_subscribers': {'success': False, 'message': ''},
            'merge_subscribers': {'success': False, 'message': ''}
        }
    }
    
    try:
        logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización de suscriptores y credenciales")
        
        # Login a Panaccess
        client = CVClient()
        success, error_message = client.login()
        
        if not success:
            error_msg = f"Error al hacer login: {error_message}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}")
            result['message'] = error_msg
            return result
        
        session_id = client.session_id
        result['session_id'] = session_id
        logger.info(f"[UPDATE_SUBSCRIBERS] Session ID: {session_id}")
        
        # 1. Sincronizar suscriptores: descarga nuevos y actualiza existentes
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización completa de suscriptores")
            sync_subscribers(session_id, limit=100)
            result['tasks']['subscribers'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Sincronización de suscriptores completada exitosamente")
        except Exception as e:
            error_msg = f"Error en sincronización de suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['subscribers'] = {'success': False, 'message': error_msg}
        
        # 2. Sincronizar credenciales de login de suscriptores
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización de credenciales de suscriptores")
            sync_subscriber_logins(session_id)
            result['tasks']['credentials'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Sincronización de credenciales completada exitosamente")
        except Exception as e:
            error_msg = f"Error en sincronización de credenciales: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['credentials'] = {'success': False, 'message': error_msg}
        
        # 3. Actualizar smartcards desde información de suscriptores (asociación subscriberCode)
        # NOTA: No sincronizamos smartcards completas desde Panaccess aquí porque toma 8-9 horas
        # con 10,000 smartcards. Esta actualización es más rápida y suficiente para asociar UDIDs.
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando actualización de smartcards desde suscriptores")
            update_result = update_smartcards_from_subscribers()
            result['tasks']['smartcards_from_subscribers'] = {
                'success': True,
                'message': f"Actualización completada: {update_result.get('total_smartcards_created', 0)} creadas, {update_result.get('total_smartcards_updated', 0)} actualizadas",
                'details': update_result
            }
            logger.info("[UPDATE_SUBSCRIBERS] Actualización de smartcards desde suscriptores completada exitosamente")
        except Exception as e:
            error_msg = f"Error en actualización de smartcards desde suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['smartcards_from_subscribers'] = {'success': False, 'message': error_msg}
        
        # 4. Merge y actualización de SubscriberInfo (tabla consolidada)
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando merge y actualización de SubscriberInfo")
            sync_merge_all_subscribers()
            result['tasks']['merge_subscribers'] = {'success': True, 'message': 'Merge y actualización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Merge y actualización de SubscriberInfo completada exitosamente")
        except Exception as e:
            error_msg = f"Error en merge de suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['merge_subscribers'] = {'success': False, 'message': error_msg}
        
        # Verificar si todas las tareas fueron exitosas
        all_success = all(task['success'] for task in result['tasks'].values())
        result['success'] = all_success
        result['message'] = 'Sincronización completada' if all_success else 'Sincronización completada con algunos errores'
        
        logger.info(f"[UPDATE_SUBSCRIBERS] Sincronización finalizada. Éxito: {all_success}")
        
    except Exception as e:
        error_msg = f"Error inesperado durante la sincronización: {str(e)}"
        logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
    
    return result


class MergeSyncCronJob(CronJobBase):
    """
    CronJob para validar y corregir toda la información de la base de datos.
    Se ejecuta una vez al día (cada 24 horas) para:
    - Validar y actualizar smartcards desde Panaccess (productos, paquetes, estado, etc.)
    - Validar y actualizar suscriptores desde Panaccess
    - Validar y actualizar credenciales de suscriptores
    - Validar y actualizar asociación de smartcards con suscriptores
    - Validar y actualizar SubscriberInfo (tabla consolidada)
    
    Esta tarea asegura que toda la información esté correcta y sincronizada con Panaccess.
    Puede tomar varias horas con grandes volúmenes de datos, por lo que se ejecuta diariamente.
    """
    RUN_EVERY_MINS = 1440  # 24 horas (1 vez al día)
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.sync_smartcards_cron'

    def do(self):
        logger.info("[MERGE_SYNC] Iniciando validación y corrección completa de información")
        # Usar la función reutilizable que valida y corrige toda la información
        result = execute_sync_tasks()
        
        # Además, actualizar smartcards desde suscriptores para asegurar asociación correcta
        try:
            logger.info("[MERGE_SYNC] Validando asociación de smartcards con suscriptores")
            update_result = update_smartcards_from_subscribers()
            logger.info(f"[MERGE_SYNC] Asociación validada: {update_result.get('total_smartcards_created', 0)} creadas, {update_result.get('total_smartcards_updated', 0)} actualizadas")
        except Exception as e:
            logger.error(f"[MERGE_SYNC] Error validando asociación smartcards-suscriptores: {str(e)}", exc_info=True)
        
        logger.info("[MERGE_SYNC] Validación y corrección completa finalizada")
        return result


class UpdateSubscribersCronJob(CronJobBase):
    """
    CronJob para sincronizar suscriptores cada 5 minutos.
    Descarga nuevos suscriptores, actualiza la información de los existentes,
    actualiza las credenciales de login, actualiza las smartcards desde la información
    de suscriptores y hace merge en SubscriberInfo (tabla consolidada que se usa al validar UDIDs).
    
    NOTA: No sincroniza smartcards completas desde Panaccess (toma 8-9 horas con 10,000 smartcards).
    Para sincronizar productos/paquetes completos, usar SyncSmartcardsCronJob o ejecución manual.
    """
    RUN_EVERY_MINS = 5
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.update_subscribers_cron'

    def do(self):
        # Usar la función reutilizable
        execute_update_subscribers()


class SyncSmartcardsCronJob(CronJobBase):
    """
    CronJob para sincronizar smartcards completas desde Panaccess (productos, paquetes, etc.).
    Se ejecuta cada 24 horas (1440 minutos) para actualizar información completa de smartcards.
    
    NOTA: Esta tarea puede tomar 8-9 horas con 10,000 smartcards, por lo que se ejecuta
    solo una vez al día. Para actualizaciones rápidas de asociación con suscriptores,
    usar UpdateSubscribersCronJob.
    
    IMPORTANTE: Esta tarea está desactivada por defecto en settings.py. Activar solo si
    necesitas actualizar productos/paquetes de smartcards periódicamente.
    """
    RUN_EVERY_MINS = 1440  # 24 horas (1 vez al día)
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.sync_smartcards_full_cron'

    def do(self):
        logger.info("[SYNC_SMARTCARDS_FULL] Iniciando sincronización completa de smartcards desde Panaccess")
        client = CVClient()
        success, error_message = client.login()
        
        if not success:
            logger.error(f"[SYNC_SMARTCARDS_FULL] Error al hacer login: {error_message}")
            return
        
        session_id = client.session_id
        logger.info(f"[SYNC_SMARTCARDS_FULL] Session ID: {session_id}")
        
        try:
            sync_smartcards(session_id, limit=100)
            logger.info("[SYNC_SMARTCARDS_FULL] Sincronización completa de smartcards finalizada")
        except Exception as e:
            logger.error(f"[SYNC_SMARTCARDS_FULL] Error: {str(e)}", exc_info=True)

