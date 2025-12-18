"""
Comando personalizado para ejecutar tareas cron específicas por código.
Uso: python manage.py runcron <código_de_la_tarea>
"""
from django.core.management.base import BaseCommand
from django_cron import CronJobBase
import logging

logger = logging.getLogger(__name__)

# Mapeo de códigos a clases de tareas
CRON_JOBS_BY_CODE = {
    'udid.update_subscribers_cron': 'udid.cron.UpdateSubscribersCronJob',
    'udid.sync_smartcards_cron': 'udid.cron.MergeSyncCronJob',
    'udid.sync_smartcards_full_cron': 'udid.cron.SyncSmartcardsCronJob',
}


def get_class(class_path):
    """
    Importa y retorna una clase desde su ruta completa.
    Ejemplo: 'udid.cron.UpdateSubscribersCronJob' -> UpdateSubscribersCronJob
    """
    module_path, class_name = class_path.rsplit('.', 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


class Command(BaseCommand):
    help = 'Ejecuta una tarea cron específica por su código'

    def add_arguments(self, parser):
        parser.add_argument(
            'cron_code',
            type=str,
            help='Código de la tarea cron a ejecutar (ej: udid.update_subscribers_cron)'
        )

    def handle(self, *args, **options):
        cron_code = options['cron_code']
        
        # Buscar la clase de la tarea por código
        if cron_code not in CRON_JOBS_BY_CODE:
            self.stdout.write(
                self.style.ERROR(
                    f'Error: Código de tarea "{cron_code}" no encontrado.\n'
                    f'Tareas disponibles: {", ".join(CRON_JOBS_BY_CODE.keys())}'
                )
            )
            return
        
        class_path = CRON_JOBS_BY_CODE[cron_code]
        
        try:
            # Importar la clase
            cron_class = get_class(class_path)
            
            # Crear instancia y ejecutar
            self.stdout.write(self.style.SUCCESS(f'Ejecutando tarea: {cron_code}'))
            cron_job = cron_class()
            cron_job.do()
            
            self.stdout.write(self.style.SUCCESS(f'Tarea {cron_code} ejecutada exitosamente'))
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error ejecutando tarea {cron_code}: {str(e)}')
            )
            logger.error(f'Error ejecutando tarea {cron_code}: {str(e)}', exc_info=True)
            raise

