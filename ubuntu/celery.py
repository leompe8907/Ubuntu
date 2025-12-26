"""
Configuración de Celery para el proyecto Django.

Celery es un sistema de colas de tareas distribuidas que permite ejecutar
tareas en background de forma asíncrona y escalable.

Este archivo inicializa la aplicación Celery y la configura para usar
Redis como broker y backend de resultados.
"""
import os
import logging
from celery import Celery
from celery.signals import setup_logging

# Establecer el módulo de configuración de Django por defecto
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

# Crear la instancia de Celery
app = Celery('udid')

# Cargar configuración desde Django settings
# El namespace 'CELERY' significa que todas las configuraciones de Celery
# deben tener el prefijo 'CELERY_' en settings.py
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-descubrir tareas en todas las apps instaladas
# Busca archivos tasks.py en cada app de INSTALLED_APPS
app.autodiscover_tasks()


@setup_logging.connect
def config_loggers(*args, **kwargs):
    """
    Configurar logging para Celery usando la configuración de Django.
    
    Esta función se ejecuta cuando Celery inicializa el logging y asegura
    que Celery use la misma configuración de logging que Django, permitiendo
    que los logs de las tareas se escriban en server.log.
    """
    from django.conf import settings
    if hasattr(settings, 'LOGGING'):
        import logging.config
        logging.config.dictConfig(settings.LOGGING)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """
    Tarea de prueba para verificar que Celery está funcionando correctamente.
    
    Ejemplo de uso:
        from ubuntu.celery import debug_task
        debug_task.delay()
    """
    print(f'Request: {self.request!r}')

