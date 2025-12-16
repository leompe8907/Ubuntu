from django_cron import CronJobBase, Schedule
from .utils.auth  import CVClient
from .utils.smartcard import sync_smartcards
from .utils.subscriber import sync_subscribers
from .utils.login import sync_subscriber_logins
from .utils.subscriberinfo import sync_merge_all_subscribers
import logging

logger = logging.getLogger(__name__)

def execute_sync_tasks():
    """
    Ejecuta todas las tareas de sincronización.
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
        logger.info("[SYNC] Iniciando sincronización manual")
        
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
        
        # 1. Sincronizar smartcards
        try:
            logger.info("[SYNC] Iniciando sincronización de smartcards")
            sync_smartcards(session_id)
            result['tasks']['smartcards'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[SYNC] Fin de sincronización de smartcards")
        except Exception as e:
            error_msg = f"Error en sincronización de smartcards: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['smartcards'] = {'success': False, 'message': error_msg}
        
        # 2. Sincronizar suscriptores
        try:
            logger.info("[SYNC] Iniciando sincronización de suscriptores")
            sync_subscribers(session_id)
            result['tasks']['subscribers'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[SYNC] Fin de sincronización de suscriptores")
        except Exception as e:
            error_msg = f"Error en sincronización de suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscribers'] = {'success': False, 'message': error_msg}
        
        # 3. Sincronizar logins de suscriptores
        try:
            logger.info("[SYNC] Iniciando sincronización de logins de suscriptores")
            sync_subscriber_logins(session_id)
            result['tasks']['subscriber_logins'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[SYNC] Fin de sincronización de logins de suscriptores")
        except Exception as e:
            error_msg = f"Error en sincronización de logins: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscriber_logins'] = {'success': False, 'message': error_msg}
        
        # 4. Merge de suscriptores
        try:
            logger.info("[SYNC] Inicio de sincronización y merge de suscriptores")
            sync_merge_all_subscribers()
            result['tasks']['merge_subscribers'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[SYNC] Fin de sincronización y merge de suscriptores")
        except Exception as e:
            error_msg = f"Error en merge de suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['merge_subscribers'] = {'success': False, 'message': error_msg}
        
        # Verificar si todas las tareas fueron exitosas
        all_success = all(task['success'] for task in result['tasks'].values())
        result['success'] = all_success
        result['message'] = 'Sincronización completada' if all_success else 'Sincronización completada con algunos errores'
        
        logger.info(f"[SYNC] Sincronización finalizada. Éxito: {all_success}")
        
    except Exception as e:
        error_msg = f"Error inesperado durante la sincronización: {str(e)}"
        logger.error(f"[SYNC] {error_msg}", exc_info=True)
        result['message'] = error_msg
    
    return result

class MergeSyncCronJob(CronJobBase):
    """
    CronJob para sincronizar smartcards cada 10min.
    """
    RUN_EVERY_MINS = 10
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.sync_smartcards_cron'

    def do(self):
        # Usar la función reutilizable
        execute_sync_tasks()

