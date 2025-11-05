from django_cron import CronJobBase, Schedule
from .utils.auth  import CVClient
from .utils.smartcard import sync_smartcards
from .utils.subscriber import sync_subscribers
from .utils.login import sync_subscriber_logins
from .utils.subscriberinfo import sync_merge_all_subscribers
import logging

logger = logging.getLogger(__name__)

class MergeSyncCronJob(CronJobBase):
    """
    CronJob para sincronizar smartcards cada 10min.
    """
    RUN_EVERY_MINS = 1
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.sync_smartcards_cron'

    def do(self):
        logger.info("[CRON] Iniciando de Tareas")

        cleint = CVClient()
        cleint.login()
        session_id = cleint.session_id

        logger.info(f"[CRON] Session ID: {session_id}")

        logger.info("[CRON] Iniciando sincronización de smartcards")

        sync_smartcards(session_id)

        logger.info(f"[CRON] Fin de sincronización de smartcards")

        logger.info("[CRON] Iniciando sincronización de suscriptores")

        sync_subscribers(session_id)

        logger.info(f"[CRON] Fin de sincronización de suscriptores")

        logger.info("[CRON] Iniciando sincronización de logins de suscriptores")

        sync_subscriber_logins(session_id)

        logger.info(f"[CRON] Fin de sincronización de logins de suscriptores")
  
        logger.info("[CRON] Inicio de sincronización y merge de suscriptores")

        sync_merge_all_subscribers()

        logger.info("[CRON] Fin de sincronización y merge de suscriptores")

