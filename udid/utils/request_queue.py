"""
Cola de entrada para requests con backpressure.
Implementa una cola con límite de tamaño y timeout para manejar ráfagas de tráfico.
"""
import threading
import time
from collections import deque
from typing import Optional, Tuple, Dict
import logging

logger = logging.getLogger(__name__)


class RequestQueue:
    """
    Cola de entrada para requests con límite de tiempo y tamaño.
    Implementa backpressure para evitar saturación del sistema.
    
    Características:
    - Límite de tamaño máximo
    - Timeout por request
    - Prioridades (mayor número = mayor prioridad)
    - Thread-safe
    """
    
    def __init__(self, max_size=1000, max_wait_time=10):
        """
        Inicializa la cola de requests.
        
        Args:
            max_size: Tamaño máximo de la cola
            max_wait_time: Tiempo máximo de espera en segundos antes de timeout
        """
        self.queue = deque()
        self.max_size = max_size
        self.max_wait_time = max_wait_time
        self.lock = threading.Lock()
        self._processing_count = 0  # Contador de requests siendo procesados
    
    def enqueue(self, request_id: str, priority: int = 0) -> Tuple[bool, int, int]:
        """
        Agrega un request a la cola.
        
        Args:
            request_id: Identificador único del request
            priority: Prioridad del request (mayor = más prioritario, default: 0)
            
        Returns:
            tuple: (success: bool, queue_position: int, estimated_wait: int)
                - success: True si se agregó, False si la cola está llena
                - queue_position: Posición en la cola (0 = siguiente en procesar)
                - estimated_wait: Tiempo estimado de espera en segundos
        """
        with self.lock:
            # Verificar si la cola está llena
            if len(self.queue) >= self.max_size:
                logger.warning(f"Request queue full (max_size={self.max_size}), rejecting request {request_id}")
                return False, -1, 0
            
            # Agregar request a la cola
            item = {
                'request_id': request_id,
                'priority': priority,
                'enqueued_at': time.time()
            }
            
            # Insertar manteniendo orden por prioridad (mayor primero)
            inserted = False
            for i, existing_item in enumerate(self.queue):
                if priority > existing_item['priority']:
                    self.queue.insert(i, item)
                    inserted = True
                    break
            
            if not inserted:
                self.queue.append(item)
            
            queue_position = len(self.queue) - 1
            # Estimar tiempo de espera basado en posición (asumiendo 1 request/segundo)
            estimated_wait = min(queue_position, self.max_wait_time)
            
            logger.debug(
                f"Request {request_id} enqueued (priority={priority}, "
                f"position={queue_position}, estimated_wait={estimated_wait}s)"
            )
            
            return True, queue_position, estimated_wait
    
    def dequeue(self) -> Optional[Dict]:
        """
        Saca un request de la cola (el de mayor prioridad).
        
        Returns:
            dict: Item del request o None si la cola está vacía o todos tienen timeout
        """
        with self.lock:
            if not self.queue:
                return None
            
            # La cola ya está ordenada por prioridad, tomar el primero
            item = self.queue.popleft()
            
            # Verificar timeout
            wait_time = time.time() - item['enqueued_at']
            if wait_time > self.max_wait_time:
                logger.warning(
                    f"Request {item['request_id']} timed out in queue "
                    f"(wait_time={wait_time:.2f}s > max_wait_time={self.max_wait_time}s)"
                )
                # Intentar con el siguiente
                return self.dequeue()  # Recursivo para encontrar uno sin timeout
            
            self._processing_count += 1
            logger.debug(f"Request {item['request_id']} dequeued (wait_time={wait_time:.2f}s)")
            return item
    
    def release(self, request_id: str):
        """
        Libera un slot de procesamiento (cuando el request termina).
        
        Args:
            request_id: Identificador del request que terminó
        """
        with self.lock:
            if self._processing_count > 0:
                self._processing_count -= 1
                logger.debug(f"Request {request_id} released, processing_count={self._processing_count}")
    
    def get_stats(self) -> Dict:
        """
        Obtiene estadísticas de la cola.
        
        Returns:
            dict: Estadísticas (size, processing_count, oldest_wait_time, etc.)
        """
        with self.lock:
            now = time.time()
            oldest_wait_time = 0
            if self.queue:
                oldest_item = self.queue[0]
                oldest_wait_time = now - oldest_item['enqueued_at']
            
            return {
                'queue_size': len(self.queue),
                'processing_count': self._processing_count,
                'max_size': self.max_size,
                'oldest_wait_time': round(oldest_wait_time, 2),
                'max_wait_time': self.max_wait_time,
                'utilization_percent': round((len(self.queue) / self.max_size) * 100, 2) if self.max_size > 0 else 0
            }
    
    def clear_expired(self) -> int:
        """
        Limpia requests expirados de la cola.
        
        Returns:
            int: Número de requests eliminados
        """
        with self.lock:
            now = time.time()
            initial_size = len(self.queue)
            
            # Filtrar requests que no han expirado
            self.queue = deque([
                item for item in self.queue
                if (now - item['enqueued_at']) <= self.max_wait_time
            ])
            
            removed = initial_size - len(self.queue)
            if removed > 0:
                logger.info(f"Cleared {removed} expired requests from queue")
            
            return removed


# Instancia global de la cola
_global_request_queue = None
_queue_lock = threading.Lock()


def get_request_queue() -> RequestQueue:
    """
    Obtiene la instancia global de la cola de requests.
    
    Returns:
        RequestQueue: Instancia global de la cola
    """
    global _global_request_queue
    
    if _global_request_queue is None:
        with _queue_lock:
            if _global_request_queue is None:
                from django.conf import settings
                max_size = getattr(settings, 'REQUEST_QUEUE_MAX_SIZE', 1000)
                max_wait_time = getattr(settings, 'REQUEST_QUEUE_MAX_WAIT_TIME', 10)
                _global_request_queue = RequestQueue(
                    max_size=max_size,
                    max_wait_time=max_wait_time
                )
                logger.info(f"Initialized global request queue (max_size={max_size}, max_wait_time={max_wait_time}s)")
    
    return _global_request_queue

