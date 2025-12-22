"""
Configuración de Celery para el proyecto Django.

Celery es un sistema de colas de tareas distribuidas que permite ejecutar
tareas en background de forma asíncrona y escalable.

Este archivo inicializa la aplicación Celery y la configura para usar
Redis como broker y backend de resultados.
"""
import os
from celery import Celery

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


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """
    Tarea de prueba para verificar que Celery está funcionando correctamente.
    
    Ejemplo de uso:
        from ubuntu.celery import debug_task
        debug_task.delay()
    """
    print(f'Request: {self.request!r}')

