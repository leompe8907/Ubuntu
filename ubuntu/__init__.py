# Esto asegura que la app de Celery se cargue cuando Django arranca
from .celery import app as celery_app

__all__ = ('celery_app',)

