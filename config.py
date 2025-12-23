# config.py
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Cargar variables desde el archivo .env
load_dotenv()

def _csv(name):
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]

class PanaccessConfig:
    PANACCESS = os.getenv("url_panaccess")
    USERNAME = os.getenv("username")
    PASSWORD = os.getenv("password")
    API_TOKEN = os.getenv("api_token")
    SALT = os.getenv("salt")
    KEY = os.getenv("ENCRYPTION_KEY")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.PANACCESS:
            missing.append("url_panaccess")
        if not cls.USERNAME:
            missing.append("username")
        if not cls.PASSWORD:
            missing.append("password")
        if not cls.API_TOKEN:
            missing.append("api_token")
        if not cls.SALT:
            missing.append("salt")
        if not cls.KEY:
            missing.append("ENCRYPTION_KEY")

        if missing:
            raise EnvironmentError(f"❌ Faltan variables de entorno: {', '.join(missing)}")

class DjangoConfig:
    SECRET_KEY = os.getenv("SECRET_KEY")
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

    # ✅ usar ALLOWED_HOSTS (plural) y filtrar vacíos
    ALLOWED_HOSTS = _csv("ALLOWED_HOSTS")

    # Opcionales: no obligues si no usás CORS/CSRF
    CORS_ORIGIN_WHITELIST = _csv("CORS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGINS = _csv("WS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGIN_REGEXES = _csv("WS_ALLOWED_ORIGIN_REGEXES")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not cls.ALLOWED_HOSTS:
            missing.append("ALLOWED_HOSTS")
        # lo siguiente solo si realmente los exigís:
        # if not cls.WS_ALLOWED_ORIGINS: missing.append("WS_ALLOWED_ORIGINS")
        if missing:
            raise EnvironmentError(f"❌ Faltan variables de entorno: {', '.join(missing)}")


class CeleryConfig:
    """
    Configuración de Celery para tareas asíncronas.
    
    Celery es un sistema de colas de tareas distribuidas que permite ejecutar
    tareas en background de forma asíncrona y escalable.
    """
    # URL del broker (Redis o RabbitMQ)
    # Por defecto usa Redis que ya está configurado en el proyecto
    BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    
    # URL del backend de resultados (donde se almacenan los resultados de las tareas)
    # Usa una base de datos diferente de Redis para evitar conflictos
    RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/1"))
    
    # Serialización de tareas (json es más seguro que pickle)
    TASK_SERIALIZER = os.getenv("CELERY_TASK_SERIALIZER", "json")
    RESULT_SERIALIZER = os.getenv("CELERY_RESULT_SERIALIZER", "json")
    ACCEPT_CONTENT = _csv("CELERY_ACCEPT_CONTENT") or ["json"]
    
    # Timezone
    TIMEZONE = os.getenv("CELERY_TIMEZONE", "UTC")
    ENABLE_UTC = os.getenv("CELERY_ENABLE_UTC", "True").lower() in ("true", "1", "yes")
    
    # Configuración de resultados
    RESULT_EXPIRES = int(os.getenv("CELERY_RESULT_EXPIRES", "3600"))  # 1 hora por defecto
    RESULT_PERSISTENT = os.getenv("CELERY_RESULT_PERSISTENT", "True").lower() in ("true", "1", "yes")
    
    # Configuración de tareas
    TASK_TRACK_STARTED = os.getenv("CELERY_TASK_TRACK_STARTED", "True").lower() in ("true", "1", "yes")
    TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "0"))  # 0 = sin límite
    TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "0"))  # 0 = sin límite suave
    TASK_ACKS_LATE = os.getenv("CELERY_TASK_ACKS_LATE", "True").lower() in ("true", "1", "yes")
    TASK_REJECT_ON_WORKER_LOST = os.getenv("CELERY_TASK_REJECT_ON_WORKER_LOST", "True").lower() in ("true", "1", "yes")
    
    # Configuración de workers
    WORKER_PREFETCH_MULTIPLIER = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "4"))
    WORKER_MAX_TASKS_PER_CHILD = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "1000"))
    WORKER_DISABLE_RATE_LIMITS = os.getenv("CELERY_WORKER_DISABLE_RATE_LIMITS", "False").lower() in ("true", "1", "yes")
    
    # Configuración de reintentos
    TASK_DEFAULT_RETRY_DELAY = int(os.getenv("CELERY_TASK_DEFAULT_RETRY_DELAY", "60"))  # 60 segundos
    TASK_MAX_RETRIES = int(os.getenv("CELERY_TASK_MAX_RETRIES", "3"))
    
    # Configuración de colas (routing)
    TASK_DEFAULT_QUEUE = os.getenv("CELERY_TASK_DEFAULT_QUEUE", "default")
    TASK_DEFAULT_EXCHANGE = os.getenv("CELERY_TASK_DEFAULT_EXCHANGE", "default")
    TASK_DEFAULT_ROUTING_KEY = os.getenv("CELERY_TASK_DEFAULT_ROUTING_KEY", "default")
    
    # Configuración de beat (tareas periódicas)
    BEAT_SCHEDULE_FILENAME = os.getenv("CELERY_BEAT_SCHEDULE_FILENAME", "celerybeat-schedule")
    
    # Configuración de monitoreo (Flower)
    FLOWER_PORT = int(os.getenv("CELERY_FLOWER_PORT", "5555"))
    FLOWER_BASIC_AUTH = os.getenv("CELERY_FLOWER_BASIC_AUTH", "")  # formato: "usuario:contraseña"
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Celery esté completa.
        No es estricto porque Celery puede funcionar con valores por defecto.
        """
        warnings = []
        
        if not cls.BROKER_URL:
            warnings.append("CELERY_BROKER_URL o REDIS_URL no configurado, usando valor por defecto")
        
        if not cls.RESULT_BACKEND:
            warnings.append("CELERY_RESULT_BACKEND o REDIS_URL no configurado, usando valor por defecto")
        
        if warnings:
            import warnings as py_warnings
            for warning in warnings:
                py_warnings.warn(f"⚠️ Celery: {warning}")
        
        return True