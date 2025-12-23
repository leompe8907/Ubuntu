"""
Módulo de utilidades del servidor.
Contiene funcionalidades de logging, métricas, degradación, Redis, etc.
"""
from .token_signing import (
    generate_api_key,
    verify_api_key,
    generate_simple_api_key,
    hash_api_key,
    verify_api_key_hash,
)
from .log_buffer import log_audit_async, flush_logs, shutdown_log_buffer
from .logging_handlers import SafeConsoleHandler, UnicodeSafeFilter
from .metrics import MetricsCollector, get_metrics, reset_metrics, record_request_latency, record_error
from .degradation import DegradationManager, get_degradation_manager, should_degrade

# Importación lazy de redis_ha para evitar errores si redis no está disponible
# o si hay problemas de compatibilidad con Python 3.12
try:
    from .redis_ha import (
        get_redis_client_safe,
        is_redis_available,
        get_circuit_breaker_state,
        RedisCircuitBreaker,
    )
    _redis_ha_available = True
except (ImportError, ModuleNotFoundError) as e:
    # Si redis_ha no se puede importar, crear funciones stub
    _redis_ha_available = False
    _redis_import_error = str(e)
    
    def get_redis_client_safe():
        return None
    
    def is_redis_available():
        return False
    
    def get_circuit_breaker_state():
        return 'unknown'
    
    class RedisCircuitBreaker:
        CLOSED = 'closed'
        OPEN = 'open'
        HALF_OPEN = 'half_open'

try:
    from .request_queue import RequestQueue, get_request_queue
except (ImportError, ModuleNotFoundError):
    RequestQueue = None
    def get_request_queue():
        return None

__all__ = [
    # Token signing
    'generate_api_key',
    'verify_api_key',
    'generate_simple_api_key',
    'hash_api_key',
    'verify_api_key_hash',
    # Log buffer
    'log_audit_async',
    'flush_logs',
    'shutdown_log_buffer',
    # Logging handlers
    'SafeConsoleHandler',
    'UnicodeSafeFilter',
    # Metrics
    'MetricsCollector',
    'get_metrics',
    'reset_metrics',
    'record_request_latency',
    'record_error',
    # Degradation
    'DegradationManager',
    'get_degradation_manager',
    'should_degrade',
    # Redis HA
    'get_redis_client_safe',
    'is_redis_available',
    'get_circuit_breaker_state',
    'RedisCircuitBreaker',
    # Request queue
    'RequestQueue',
    'get_request_queue',
]

