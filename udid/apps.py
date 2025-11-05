from django.apps import AppConfig


class UdidConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'udid'

def ready(self):
    import udid.signals 