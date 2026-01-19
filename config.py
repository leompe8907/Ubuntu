# config.py
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Cargar variables desde el archivo .env
load_dotenv()

def _getenv_or_default(name, default=None):
    """
    Obtiene una variable de entorno del .env.
    Si no existe o está vacía, retorna el valor por defecto.
    """
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value

def _csv(name, default=""):
    """Convierte una variable de entorno separada por comas en una lista."""
    raw = _getenv_or_default(name, default)
    if raw is None:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

def _bool(name, default="False"):
    """Convierte una variable de entorno en booleano."""
    value = _getenv_or_default(name, default)
    if value is None:
        return default.lower() in ("true", "1", "yes")
    return str(value).lower() in ("true", "1", "yes")

def _int(name, default="0"):
    """Convierte una variable de entorno en entero."""
    value = _getenv_or_default(name, default)
    if value is None:
        return int(default)
    try:
        return int(value)
    except (ValueError, TypeError):
        return int(default)

def _float(name, default="0.0"):
    """Convierte una variable de entorno en float."""
    value = _getenv_or_default(name, default)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (ValueError, TypeError):
        return float(default)

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
    DEBUG = _bool("DEBUG", "False")

    # ✅ usar ALLOWED_HOSTS (plural) y filtrar vacíos
    ALLOWED_HOSTS = _csv("ALLOWED_HOSTS")

    # Opcionales: no obligues si no usás CORS/CSRF
    CORS_ORIGIN_WHITELIST = _csv("CORS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGINS = _csv("WS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGIN_REGEXES = _csv("WS_ALLOWED_ORIGIN_REGEXES")
    REST_FRAMEWORK_PAGE_SIZE = _int("REST_FRAMEWORK_PAGE_SIZE", "100")
    JWT_ACCESS_TOKEN_LIFETIME_MINUTES = _int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", "15")
    JWT_REFRESH_TOKEN_LIFETIME_DAYS = _int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", "1")
    JWT_ROTATE_REFRESH_TOKENS = _bool("JWT_ROTATE_REFRESH_TOKENS", "True")
    JWT_BLACKLIST_AFTER_ROTATION = _bool("JWT_BLACKLIST_AFTER_ROTATION", "True")
    
    # CSRF
    CSRF_TRUSTED_ORIGINS = _csv("CSRF_TRUSTED_ORIGINS")

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

class RedisConfig:
    """Configuración de Redis para Channel Layers, Cache y Rate Limiting."""
    REDIS_URL = _getenv_or_default("REDIS_URL", "redis://localhost:6379/0")
    REDIS_SENTINEL = _getenv_or_default("REDIS_SENTINEL", None)
    REDIS_SENTINEL_MASTER = _getenv_or_default("REDIS_SENTINEL_MASTER", "mymaster")
    REDIS_SOCKET_CONNECT_TIMEOUT = _int("REDIS_SOCKET_CONNECT_TIMEOUT", "5")
    REDIS_SOCKET_TIMEOUT = _int("REDIS_SOCKET_TIMEOUT", "5")
    REDIS_RETRY_ON_TIMEOUT = _bool("REDIS_RETRY_ON_TIMEOUT", "True")
    REDIS_MAX_CONNECTIONS = _int("REDIS_MAX_CONNECTIONS", "100")
    REDIS_CIRCUIT_BREAKER_THRESHOLD = _int("REDIS_CIRCUIT_BREAKER_THRESHOLD", "10")
    REDIS_CIRCUIT_BREAKER_TIMEOUT = _int("REDIS_CIRCUIT_BREAKER_TIMEOUT", "30")
    # Se inicializa después de REDIS_URL para poder usar su valor
    REDIS_CHANNEL_LAYER_URL = None
    REDIS_RATE_LIMIT_URL = None
    
    @classmethod
    def _init_urls(cls):
        """Inicializa las URLs que dependen de REDIS_URL."""
        if cls.REDIS_CHANNEL_LAYER_URL is None:
            cls.REDIS_CHANNEL_LAYER_URL = _getenv_or_default("REDIS_CHANNEL_LAYER_URL", cls.REDIS_URL)
        if cls.REDIS_RATE_LIMIT_URL is None:
            cls.REDIS_RATE_LIMIT_URL = _getenv_or_default("REDIS_RATE_LIMIT_URL", cls.REDIS_URL)
    
    @classmethod
    def get_sentinel_list(cls):
        """Parsea REDIS_SENTINEL y retorna lista de tuplas (host, puerto) o None."""
        if cls.REDIS_SENTINEL:
            return [
                (h, int(p)) for h, p in (hp.split(":") for hp in cls.REDIS_SENTINEL.split(","))
            ]
        return None
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Redis esté completa.
        No es estricto porque Redis tiene valores por defecto.
        """
        warnings = []
        
        if not cls.REDIS_URL:
            warnings.append("REDIS_URL no configurado, usando valor por defecto")
        
        if warnings:
            import warnings as py_warnings
            for warning in warnings:
                py_warnings.warn(f"⚠️ Redis: {warning}")
        
        return True

class ChannelLayersConfig:
    """Configuración de Channel Layers para WebSockets."""
    CAPACITY = _int("CHANNEL_LAYERS_CAPACITY", "2000")
    EXPIRY = _int("CHANNEL_LAYERS_EXPIRY", "10")
    GROUP_EXPIRY = _int("CHANNEL_LAYERS_GROUP_EXPIRY", "900")
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Channel Layers esté completa.
        No es estricto porque tiene valores por defecto.
        """
        return True

class UdidConfig:
    """Configuración de UDID, carga y concurrencia."""
    WAIT_TIMEOUT_AUTOMATIC = _int("UDID_WAIT_TIMEOUT_AUTOMATIC", "180")
    WAIT_TIMEOUT_MANUAL = _int("UDID_WAIT_TIMEOUT_MANUAL", "180")
    # WAIT_TIMEOUT puede ser None si no está configurado (se usa WAIT_TIMEOUT_AUTOMATIC como fallback)
    WAIT_TIMEOUT = None
    
    @classmethod
    def _init_wait_timeout(cls):
        """Inicializa WAIT_TIMEOUT desde el .env o usa None."""
        wait_timeout = _getenv_or_default("UDID_WAIT_TIMEOUT")
        if wait_timeout:
            try:
                cls.WAIT_TIMEOUT = int(wait_timeout)
            except (ValueError, TypeError):
                cls.WAIT_TIMEOUT = None
        else:
            cls.WAIT_TIMEOUT = None
    
    ENABLE_POLLING = _bool("UDID_ENABLE_POLLING", "False")
    POLL_INTERVAL = _int("UDID_POLL_INTERVAL", "2")
    EXPIRATION_MINUTES = _int("UDID_EXPIRATION_MINUTES", "5")
    MAX_ATTEMPTS = _int("UDID_MAX_ATTEMPTS", "5")
    WS_MAX_PER_TOKEN = _int("UDID_WS_MAX_PER_TOKEN", "1")
    GLOBAL_SEMAPHORE_SLOTS = _int("GLOBAL_SEMAPHORE_SLOTS", "1000")
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de UDID esté completa.
        No es estricto porque tiene valores por defecto.
        """
        return True

class BackpressureConfig:
    """Configuración de backpressure y degradación elegante."""
    REQUEST_QUEUE_MAX_SIZE = _int("REQUEST_QUEUE_MAX_SIZE", "1000")
    REQUEST_QUEUE_MAX_WAIT_TIME = _int("REQUEST_QUEUE_MAX_WAIT_TIME", "10")
    DEGRADATION_BASELINE_LOAD = _int("DEGRADATION_BASELINE_LOAD", "100")
    DEGRADATION_MEDIUM_THRESHOLD = _float("DEGRADATION_MEDIUM_THRESHOLD", "1.5")
    DEGRADATION_HIGH_THRESHOLD = _float("DEGRADATION_HIGH_THRESHOLD", "2.0")
    DEGRADATION_CRITICAL_THRESHOLD = _float("DEGRADATION_CRITICAL_THRESHOLD", "3.0")
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Backpressure esté completa.
        No es estricto porque tiene valores por defecto.
        """
        return True

class DatabaseConfig:
    """Configuración de base de datos."""
    MYSQL_HOST = _getenv_or_default("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = _int("MYSQL_PORT", "3307")
    # PostgreSQL (comentado en settings, pero disponible)
    POSTGRES_DB = _getenv_or_default("POSTGRES_DB", "udid")
    POSTGRES_USER = _getenv_or_default("POSTGRES_USER", "udid_user")
    POSTGRES_PASSWORD = _getenv_or_default("POSTGRES_PASSWORD", "")
    POSTGRES_HOST = _getenv_or_default("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = _int("POSTGRES_PORT", "5432")
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Database esté completa.
        No es estricto porque tiene valores por defecto.
        """
        return True

class CacheConfig:
    """Configuración de cache."""
    TIMEOUT = _int("CACHE_TIMEOUT", "300")
    KEY_PREFIX = _getenv_or_default("CACHE_KEY_PREFIX", "udid_cache")
    SOCKET_CONNECT_TIMEOUT = _int("CACHE_SOCKET_CONNECT_TIMEOUT", "5")
    SOCKET_TIMEOUT = _int("CACHE_SOCKET_TIMEOUT", "5")
    MAX_CONNECTIONS = _int("CACHE_MAX_CONNECTIONS", "50")
    
    @classmethod
    def validate(cls):
        """
        Valida que la configuración de Cache esté completa.
        No es estricto porque tiene valores por defecto.
        """
        return True

class CeleryConfig:
    """
    Configuración de Celery para tareas asíncronas.
    
    Celery es un sistema de colas de tareas distribuidas que permite ejecutar
    tareas en background de forma asíncrona y escalable.
    """
    # URL del broker (Redis o RabbitMQ)
    # Por defecto usa Redis que ya está configurado en el proyecto
    # Si CELERY_BROKER_URL está vacía o no existe, usa REDIS_URL
    BROKER_URL = None
    RESULT_BACKEND = None
    
    # Serialización de tareas (json es más seguro que pickle)
    TASK_SERIALIZER = _getenv_or_default("CELERY_TASK_SERIALIZER", "json")
    RESULT_SERIALIZER = _getenv_or_default("CELERY_RESULT_SERIALIZER", "json")
    ACCEPT_CONTENT = _csv("CELERY_ACCEPT_CONTENT") or ["json"]
    
    # Timezone
    TIMEZONE = _getenv_or_default("CELERY_TIMEZONE", "UTC")
    ENABLE_UTC = _bool("CELERY_ENABLE_UTC", "True")
    
    # Configuración de resultados
    RESULT_EXPIRES = _int("CELERY_RESULT_EXPIRES", "3600")  # 1 hora por defecto
    RESULT_PERSISTENT = _bool("CELERY_RESULT_PERSISTENT", "True")
    
    # Configuración de tareas
    TASK_TRACK_STARTED = _bool("CELERY_TASK_TRACK_STARTED", "True")
    TASK_TIME_LIMIT = _int("CELERY_TASK_TIME_LIMIT", "0")  # 0 = sin límite
    TASK_SOFT_TIME_LIMIT = _int("CELERY_TASK_SOFT_TIME_LIMIT", "0")  # 0 = sin límite suave
    TASK_ACKS_LATE = _bool("CELERY_TASK_ACKS_LATE", "True")
    TASK_REJECT_ON_WORKER_LOST = _bool("CELERY_TASK_REJECT_ON_WORKER_LOST", "True")
    
    # Configuración de workers
    WORKER_PREFETCH_MULTIPLIER = _int("CELERY_WORKER_PREFETCH_MULTIPLIER", "4")
    WORKER_MAX_TASKS_PER_CHILD = _int("CELERY_WORKER_MAX_TASKS_PER_CHILD", "1000")
    WORKER_DISABLE_RATE_LIMITS = _bool("CELERY_WORKER_DISABLE_RATE_LIMITS", "False")
    WORKER_CONCURRENCY = _int("CELERY_WORKER_CONCURRENCY", "0")  # 0 = auto
    
    # Configuración de reintentos
    TASK_DEFAULT_RETRY_DELAY = _int("CELERY_TASK_DEFAULT_RETRY_DELAY", "60")  # 60 segundos
    TASK_MAX_RETRIES = _int("CELERY_TASK_MAX_RETRIES", "3")
    
    # Configuración de colas (routing)
    TASK_DEFAULT_QUEUE = _getenv_or_default("CELERY_TASK_DEFAULT_QUEUE", "default")
    TASK_DEFAULT_EXCHANGE = _getenv_or_default("CELERY_TASK_DEFAULT_EXCHANGE", "default")
    TASK_DEFAULT_ROUTING_KEY = _getenv_or_default("CELERY_TASK_DEFAULT_ROUTING_KEY", "default")
    
    # Configuración de beat (tareas periódicas)
    BEAT_SCHEDULE_FILENAME = _getenv_or_default("CELERY_BEAT_SCHEDULE_FILENAME", "celerybeat-schedule")
    BEAT_SCHEDULE_DIR = _getenv_or_default("CELERY_BEAT_SCHEDULE_DIR", "/var/run/udid")
    
    # Configuración de monitoreo (Flower)
    FLOWER_PORT = _int("CELERY_FLOWER_PORT", "5555")
    FLOWER_BASIC_AUTH = _getenv_or_default("CELERY_FLOWER_BASIC_AUTH", "")  # formato: "usuario:contraseña"
    
    @classmethod
    def _init_broker_and_backend(cls):
        """Inicializa BROKER_URL y RESULT_BACKEND usando REDIS_URL como fallback."""
        # Obtener REDIS_URL de RedisConfig (ya inicializado)
        redis_url = RedisConfig.REDIS_URL
        
        # BROKER_URL: Si está vacía o no existe, usa REDIS_URL con db 0
        broker_url = _getenv_or_default("CELERY_BROKER_URL")
        if not broker_url:
            cls.BROKER_URL = redis_url if redis_url else "redis://localhost:6379/0"
        else:
            cls.BROKER_URL = broker_url
        
        # RESULT_BACKEND: Si está vacía o no existe, usa REDIS_URL con db 1
        result_backend = _getenv_or_default("CELERY_RESULT_BACKEND")
        if not result_backend:
            # Cambiar el número de base de datos a 1 para resultados
            if redis_url:
                # Reemplazar el último número de base de datos por 1
                if "/" in redis_url:
                    parts = redis_url.rsplit("/", 1)
                    cls.RESULT_BACKEND = parts[0] + "/1"
                else:
                    cls.RESULT_BACKEND = redis_url + "/1"
            else:
                cls.RESULT_BACKEND = "redis://localhost:6379/1"
        else:
            cls.RESULT_BACKEND = result_backend
    
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

# Inicializar URLs de Redis que dependen de REDIS_URL
RedisConfig._init_urls()
# Inicializar BROKER_URL y RESULT_BACKEND de Celery que dependen de REDIS_URL
CeleryConfig._init_broker_and_backend()
# Inicializar WAIT_TIMEOUT de UdidConfig
UdidConfig._init_wait_timeout()