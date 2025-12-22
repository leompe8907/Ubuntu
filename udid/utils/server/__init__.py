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
from .redis_ha import (
    get_redis_client_safe,
    is_redis_available,
    get_circuit_breaker_state,
    RedisCircuitBreaker,
    RedisHAClient,
)
from .request_queue import RequestQueue, get_request_queue

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
    'RedisHAClient',
    # Request queue
    'RequestQueue',
    'get_request_queue',
]

