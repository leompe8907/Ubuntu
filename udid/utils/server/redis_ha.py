"""
Utilidades para Redis con Alta Disponibilidad (Sentinel/Cluster).
Incluye circuit breaker y manejo de failover automático.
"""
import redis
import redis.sentinel
import logging
import time
import threading
from django.conf import settings
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class RedisCircuitBreaker:
    """
    Circuit breaker para Redis que detecta fallos y entra en modo degradado.
    
    Estados:
    - CLOSED: Funcionando normalmente
    - OPEN: Fallos detectados, rechazando requests
    - HALF_OPEN: Probando si Redis se recuperó
    """
    
    CLOSED = 'closed'
    OPEN = 'open'
    HALF_OPEN = 'half_open'
    
    def __init__(self, failure_threshold=5, timeout=60, half_open_timeout=10):
        """
        Inicializa el circuit breaker.
        
        Args:
            failure_threshold: Número de fallos consecutivos antes de abrir
            timeout: Tiempo en segundos antes de intentar half-open
            half_open_timeout: Tiempo en segundos en estado half-open
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.half_open_timeout = half_open_timeout
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.last_state_change = time.time()
    
    def record_success(self):
        """Registra un éxito y resetea el circuit breaker"""
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
            self.failure_count = 0
            self.last_state_change = time.time()
            logger.info("Redis circuit breaker: CLOSED (recuperado)")
        elif self.state == self.CLOSED:
            self.failure_count = 0
    
    def record_failure(self):
        """Registra un fallo"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == self.CLOSED and self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            self.last_state_change = time.time()
            logger.warning(
                f"Redis circuit breaker: OPEN (fallos consecutivos: {self.failure_count})"
            )
        elif self.state == self.HALF_OPEN:
            self.state = self.OPEN
            self.last_state_change = time.time()
            logger.warning("Redis circuit breaker: OPEN (fallo en half-open)")
    
    def can_attempt(self) -> bool:
        """Verifica si se puede intentar una operación"""
        now = time.time()
        
        if self.state == self.CLOSED:
            return True
        elif self.state == self.OPEN:
            # Intentar half-open después del timeout
            if now - self.last_state_change >= self.timeout:
                self.state = self.HALF_OPEN
                self.last_state_change = now
                logger.info("Redis circuit breaker: HALF_OPEN (probando recuperación)")
                return True
            return False
        elif self.state == self.HALF_OPEN:
            # En half-open, permitir intentos limitados
            if now - self.last_state_change >= self.half_open_timeout:
                # Si pasó el timeout sin éxito, volver a OPEN
                self.state = self.OPEN
                self.last_state_change = now
                return False
            return True
        
        return False
    
    def get_state(self) -> str:
        """Retorna el estado actual del circuit breaker"""
        return self.state


# Instancia global del circuit breaker
# Aumentar threshold a 10 para ser menos sensible durante picos de carga
_redis_circuit_breaker = RedisCircuitBreaker(
    failure_threshold=getattr(settings, 'REDIS_CIRCUIT_BREAKER_THRESHOLD', 10),  # Aumentado de 5 a 10
    timeout=getattr(settings, 'REDIS_CIRCUIT_BREAKER_TIMEOUT', 30),  # Reducido de 60 a 30 para recuperación más rápida
    half_open_timeout=5  # Reducido de 10 a 5
)


def get_redis_client(use_sentinel: bool = False) -> Optional[redis.Redis]:
    """
    Obtiene un cliente Redis con soporte para Sentinel o conexión directa.
    
    Args:
        use_sentinel: Si True, usa Sentinel. Si False, usa conexión directa.
        
    Returns:
        redis.Redis: Cliente Redis o None si no está disponible
    """
    if not _redis_circuit_breaker.can_attempt():
        logger.warning("Redis circuit breaker: OPEN, rechazando conexión")
        return None
    
    try:
        if use_sentinel:
            return _get_redis_sentinel_client()
        else:
            return _get_redis_direct_client()
    except Exception as e:
        _redis_circuit_breaker.record_failure()
        logger.error(f"Error obteniendo cliente Redis: {e}", exc_info=True)
        return None


def _get_redis_sentinel_client() -> redis.Redis:
    """
    Obtiene un cliente Redis usando Sentinel para alta disponibilidad.
    """
    sentinel_hosts = getattr(settings, 'REDIS_SENTINEL', None)
    sentinel_master = getattr(settings, 'REDIS_SENTINEL_MASTER', 'mymaster')
    
    if not sentinel_hosts:
        # Fallback a conexión directa si Sentinel no está configurado
        return _get_redis_direct_client()
    
    # Crear conexión a Sentinel
    sentinel = redis.sentinel.Sentinel(
        sentinel_hosts,
        socket_connect_timeout=getattr(settings, 'REDIS_SOCKET_CONNECT_TIMEOUT', 5),
        socket_timeout=getattr(settings, 'REDIS_SOCKET_TIMEOUT', 5),
        retry_on_timeout=getattr(settings, 'REDIS_RETRY_ON_TIMEOUT', True),
        max_connections=getattr(settings, 'REDIS_MAX_CONNECTIONS', 50),
    )
    
    # Obtener master desde Sentinel
    master = sentinel.master_for(
        sentinel_master,
        socket_connect_timeout=getattr(settings, 'REDIS_SOCKET_CONNECT_TIMEOUT', 5),
        socket_timeout=getattr(settings, 'REDIS_SOCKET_TIMEOUT', 5),
        retry_on_timeout=getattr(settings, 'REDIS_RETRY_ON_TIMEOUT', True),
    )
    
    # Test de conexión
    master.ping()
    _redis_circuit_breaker.record_success()
    
    return master


# Connection pool global para reutilizar conexiones
_redis_connection_pool = None
_pool_lock = threading.Lock()


def _get_redis_direct_client() -> redis.Redis:
    """
    Obtiene un cliente Redis usando conexión directa con connection pooling.
    """
    global _redis_connection_pool
    
    redis_url = getattr(settings, 'REDIS_URL', None)
    
    if not redis_url:
        # Intentar usar valor por defecto para desarrollo local
        redis_url = "redis://localhost:6379/0"
        logger.warning(f"REDIS_URL no está configurado, usando valor por defecto: {redis_url}")
    
    # Crear connection pool si no existe (singleton)
    if _redis_connection_pool is None:
        with _pool_lock:
            if _redis_connection_pool is None:
                _redis_connection_pool = redis.ConnectionPool.from_url(
                    redis_url,
                    socket_connect_timeout=getattr(settings, 'REDIS_SOCKET_CONNECT_TIMEOUT', 5),
                    socket_timeout=getattr(settings, 'REDIS_SOCKET_TIMEOUT', 5),
                    retry_on_timeout=getattr(settings, 'REDIS_RETRY_ON_TIMEOUT', True),
                    max_connections=getattr(settings, 'REDIS_MAX_CONNECTIONS', 50),
                    decode_responses=False  # Mantener compatibilidad con código existente
                )
                logger.info(f"Redis connection pool created (max_connections={getattr(settings, 'REDIS_MAX_CONNECTIONS', 50)})")
    
    # Crear cliente usando el pool
    client = redis.Redis(connection_pool=_redis_connection_pool)
    
    # Test de conexión con timeout corto
    try:
        client.ping()
        _redis_circuit_breaker.record_success()
    except Exception as e:
        _redis_circuit_breaker.record_failure()
        raise
    
    return client


def get_redis_client_safe() -> Optional[redis.Redis]:
    """
    Obtiene un cliente Redis de forma segura (con circuit breaker).
    Retorna None si Redis no está disponible.
    
    Returns:
        redis.Redis: Cliente Redis o None si no está disponible
    """
    # Intentar con Sentinel primero si está configurado
    use_sentinel = hasattr(settings, 'REDIS_SENTINEL') and settings.REDIS_SENTINEL
    
    return get_redis_client(use_sentinel=use_sentinel)


def is_redis_available() -> bool:
    """
    Verifica si Redis está disponible.
    
    Returns:
        bool: True si Redis está disponible, False si no
    """
    client = get_redis_client_safe()
    if client:
        try:
            client.ping()
            return True
        except Exception:
            return False
    return False


def get_circuit_breaker_state() -> str:
    """
    Obtiene el estado actual del circuit breaker.
    
    Returns:
        str: Estado del circuit breaker ('closed', 'open', 'half_open')
    """
    return _redis_circuit_breaker.get_state()

