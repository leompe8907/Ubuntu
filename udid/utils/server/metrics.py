"""
Recolector de métricas del sistema para el dashboard de observabilidad.
Incluye métricas de latencia, errores, concurrencia, CPU, RAM, Redis y WebSockets.
"""
import time
import psutil
from collections import deque
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Recolector de métricas en memoria para el dashboard.
    Mantiene métricas de latencia, errores, concurrencia, sistema, Redis y WebSockets.
    """
    
    def __init__(self):
        self.latencies = deque(maxlen=1000)
        self.error_counts = {'429': 0, '503': 0, '500': 0}
        self.redis_latencies = deque(maxlen=100)
        self._last_reset = time.time()
    
    def record_latency(self, latency_ms):
        """Registra la latencia de una request en milisegundos"""
        self.latencies.append(latency_ms)
    
    def record_error(self, status_code):
        """Registra un error por código de estado"""
        status_str = str(status_code)
        if status_str in self.error_counts:
            self.error_counts[status_str] += 1
    
    def record_redis_latency(self, latency_ms):
        """Registra latencia de operaciones Redis"""
        self.redis_latencies.append(latency_ms)
    
    def reset(self):
        """Resetea todas las métricas"""
        self.latencies.clear()
        self.error_counts = {'429': 0, '503': 0, '500': 0}
        self.redis_latencies.clear()
        self._last_reset = time.time()
    
    def get_metrics(self):
        """Obtiene todas las métricas del sistema"""
        base_metrics = self._get_base_metrics()
        system_metrics = self._get_system_metrics()
        redis_metrics = self._get_redis_metrics()
        ws_metrics = self._get_websocket_metrics()
        concurrency_metrics = self._get_concurrency_metrics()
        
        return {
            **base_metrics,
            **system_metrics,
            **redis_metrics,
            **ws_metrics,
            **concurrency_metrics,
            'last_reset': self._last_reset,
            'uptime_seconds': time.time() - self._last_reset
        }
    
    def _get_base_metrics(self):
        """Obtiene métricas base de latencia y errores"""
        if not self.latencies:
            return {
                'p50': 0,
                'p95': 0,
                'p99': 0,
                'p50_ms': 0,
                'p95_ms': 0,
                'p99_ms': 0,
                'errors': self.error_counts.copy(),
                'total_requests': 0,
                'avg_latency_ms': 0
            }
        
        sorted_latencies = sorted(self.latencies)
        n = len(sorted_latencies)
        
        p50_idx = int(n * 0.5)
        p95_idx = int(n * 0.95)
        p99_idx = int(n * 0.99)
        
        p50 = sorted_latencies[p50_idx] if p50_idx < n else 0
        p95 = sorted_latencies[p95_idx] if p95_idx < n else 0
        p99 = sorted_latencies[p99_idx] if p99_idx < n else 0
        
        avg_latency = sum(sorted_latencies) / n if n > 0 else 0
        
        return {
            'p50': p50,
            'p95': p95,
            'p99': p99,
            'p50_ms': round(p50, 2),
            'p95_ms': round(p95, 2),
            'p99_ms': round(p99, 2),
            'errors': self.error_counts.copy(),
            'total_requests': n,
            'avg_latency_ms': round(avg_latency, 2)
        }
    
    def _get_system_metrics(self):
        """Obtiene métricas del sistema (CPU, RAM)"""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            
            return {
                'cpu_percent': round(cpu_percent, 2),
                'ram_percent': round(memory.percent, 2),
                'ram_used_mb': round(memory.used / (1024 * 1024), 2),
                'ram_total_mb': round(memory.total / (1024 * 1024), 2),
                'ram_available_mb': round(memory.available / (1024 * 1024), 2)
            }
        except Exception as e:
            logger.error(f"Error getting system metrics: {e}", exc_info=True)
            return {
                'cpu_percent': 0,
                'ram_percent': 0,
                'ram_used_mb': 0,
                'ram_total_mb': 0,
                'ram_available_mb': 0
            }
    
    def _get_redis_metrics(self):
        """Obtiene métricas de Redis (latencia, conexiones)"""
        try:
            import redis
            from django.conf import settings
            
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                return {
                    'redis_latency_ms': 0,
                    'redis_avg_latency_ms': 0,
                    'redis_available': False
                }
            
            # Medir latencia de Redis
            start = time.time()
            redis_client = redis.from_url(redis_url)
            redis_client.ping()
            latency_ms = (time.time() - start) * 1000
            
            # Registrar latencia
            self.record_redis_latency(latency_ms)
            
            # Calcular promedio de latencias
            avg_redis_latency = 0
            if self.redis_latencies:
                avg_redis_latency = sum(self.redis_latencies) / len(self.redis_latencies)
            
            return {
                'redis_latency_ms': round(latency_ms, 2),
                'redis_avg_latency_ms': round(avg_redis_latency, 2),
                'redis_available': True
            }
        except Exception as e:
            logger.error(f"Error getting Redis metrics: {e}", exc_info=True)
            return {
                'redis_latency_ms': 0,
                'redis_avg_latency_ms': 0,
                'redis_available': False
            }
    
    def _get_websocket_metrics(self):
        """Obtiene métricas de WebSockets (conexiones activas, backlog)"""
        try:
            import redis
            from django.conf import settings
            
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                return {
                    'ws_connections_global': 0,
                    'ws_connections_per_token': {}
                }
            
            redis_client = redis.from_url(redis_url)
            
            # Contar conexiones globales
            global_key = "ws_connections:global"
            global_count = 0
            try:
                count_str = redis_client.get(global_key)
                if count_str:
                    global_count = int(count_str)
            except Exception:
                pass
            
            # Contar conexiones por token (usando SCAN)
            token_connections = {}
            pattern = "ws_connections:token:*"
            cursor = 0
            total_token_connections = 0
            
            try:
                while True:
                    cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
                    for key in keys:
                        try:
                            count_str = redis_client.get(key)
                            if count_str:
                                count = int(count_str)
                                token_connections[key.decode()] = count
                                total_token_connections += count
                        except Exception:
                            pass
                    if cursor == 0:
                        break
            except Exception as e:
                logger.error(f"Error scanning WebSocket connections: {e}", exc_info=True)
            
            return {
                'ws_connections_global': global_count,
                'ws_connections_total_tokens': len(token_connections),
                'ws_connections_total': total_token_connections
            }
        except Exception as e:
            logger.error(f"Error getting WebSocket metrics: {e}", exc_info=True)
            return {
                'ws_connections_global': 0,
                'ws_connections_total_tokens': 0,
                'ws_connections_total': 0
            }
    
    def _get_concurrency_metrics(self):
        """Obtiene métricas de concurrencia (semáforo global)"""
        try:
            import redis
            from django.conf import settings
            
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                return {
                    'concurrency_current': 0,
                    'concurrency_max': 0,
                    'concurrency_percent': 0
                }
            
            redis_client = redis.from_url(redis_url)
            
            # Contar slots ocupados usando SCAN
            pattern = "global_semaphore:slots:*"
            cursor = 0
            count = 0
            
            try:
                while True:
                    cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
                    count += len(keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.error(f"Error scanning semaphore slots: {e}", exc_info=True)
            
            # Obtener máximo de slots desde settings
            from django.conf import settings
            max_slots = getattr(settings, 'GLOBAL_SEMAPHORE_SLOTS', 500)
            
            concurrency_percent = (count / max_slots * 100) if max_slots > 0 else 0
            
            return {
                'concurrency_current': count,
                'concurrency_max': max_slots,
                'concurrency_percent': round(concurrency_percent, 2)
            }
        except Exception as e:
            logger.error(f"Error getting concurrency metrics: {e}", exc_info=True)
            return {
                'concurrency_current': 0,
                'concurrency_max': 0,
                'concurrency_percent': 0
            }


# Instancia global del recolector
_metrics_collector = MetricsCollector()


def get_metrics_collector():
    """Obtiene la instancia global del recolector de métricas"""
    return _metrics_collector


def record_request_latency(latency_ms):
    """Registra la latencia de una request"""
    _metrics_collector.record_latency(latency_ms)


def record_error(status_code):
    """Registra un error por código de estado"""
    _metrics_collector.record_error(status_code)


def record_redis_latency(latency_ms):
    """Registra latencia de operaciones Redis"""
    _metrics_collector.record_redis_latency(latency_ms)


def get_metrics():
    """Obtiene todas las métricas del sistema"""
    return _metrics_collector.get_metrics()


def reset_metrics():
    """Resetea todas las métricas"""
    _metrics_collector.reset()

