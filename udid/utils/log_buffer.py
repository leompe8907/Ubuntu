"""
Buffer en memoria para logs de auditoría que se escriben en batch.
Reduce la latencia de requests al evitar escrituras síncronas a la BD.
"""
import threading
import time
import logging
from collections import deque
from django.db import transaction, connection
from django.db.utils import OperationalError, DatabaseError
from udid.utils.db_utils import is_connection_error, reconnect_database

logger = logging.getLogger(__name__)


class LogBuffer:
    """
    Buffer en memoria para logs que se escriben en batch.
    Thread-safe usando locks.
    
    Características:
    - Escribe logs en batch cada N segundos o cuando se alcanza batch_size
    - Thread-safe para uso en entornos multi-threaded
    - Fire-and-forget: no bloquea el request
    - Manejo de errores: no falla si hay problemas con BD
    """
    
    def __init__(self, batch_size=100, flush_interval=5):
        """
        Inicializa el buffer de logs.
        
        Args:
            batch_size: Número de logs antes de hacer flush automático
            flush_interval: Intervalo en segundos para flush periódico
        """
        self.buffer = deque()
        self.lock = threading.Lock()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.last_flush = time.time()
        self._shutdown = False
        self._start_flush_thread()
    
    def _start_flush_thread(self):
        """Inicia thread daemon que hace flush periódico"""
        def flush_periodic():
            while not self._shutdown:
                try:
                    time.sleep(self.flush_interval)
                    if not self._shutdown:
                        self.flush()
                except (SystemExit, KeyboardInterrupt):
                    # Permitir shutdown limpio
                    break
                except Exception as e:
                    logger.error(f"Error in flush thread: {e}", exc_info=True)
        
        thread = threading.Thread(target=flush_periodic, daemon=True, name="LogBufferFlush")
        thread.start()
        logger.info(f"LogBuffer flush thread started (interval={self.flush_interval}s, batch_size={self.batch_size})")
    
    def add(self, log_data):
        """
        Agrega un log al buffer.
        
        Args:
            log_data: Diccionario con los datos del log
        """
        with self.lock:
            self.buffer.append(log_data)
            
            # Flush si se alcanza el tamaño del batch
            if len(self.buffer) >= self.batch_size:
                self._flush_internal()
    
    def flush(self):
        """
        Fuerza flush del buffer.
        Útil para asegurar que los logs se escriban antes de shutdown.
        """
        with self.lock:
            self._flush_internal()
    
    def _flush_internal(self):
        """
        Flush interno (debe llamarse con lock adquirido).
        Escribe los logs en batch a la BD.
        """
        if not self.buffer:
            return
        
        logs_to_write = list(self.buffer)
        buffer_size = len(logs_to_write)
        self.buffer.clear()
        self.last_flush = time.time()
        
        # Escribir en BD en batch (fire-and-forget)
        # Usar thread separado para no bloquear
        def write_to_db():
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    with transaction.atomic():
                        from udid.models import AuthAuditLog
                        # Usar bulk_create para mejor rendimiento
                        AuthAuditLog.objects.bulk_create([
                            AuthAuditLog(**log_data) for log_data in logs_to_write
                        ], ignore_conflicts=True)  # Ignorar conflictos si hay duplicados
                    logger.debug(f"LogBuffer: Wrote {buffer_size} logs to DB")
                    return  # Éxito
                except (OperationalError, DatabaseError) as e:
                    if is_connection_error(e):
                        retry_count += 1
                        logger.warning(f"LogBuffer: Conexión perdida (intento {retry_count}/{max_retries}). Reconectando...")
                        reconnect_database()
                        if retry_count < max_retries:
                            time.sleep(2 * retry_count)
                            continue
                        else:
                            logger.error(f"LogBuffer: No se pudo reconectar después de {max_retries} intentos")
                            return
                    else:
                        # Otro error de BD, loggear y salir
                        logger.error(f"LogBuffer: Error de BD escribiendo {buffer_size} logs: {e}", exc_info=True)
                        return
                except Exception as e:
                    # Log error pero no bloquear
                    logger.error(f"LogBuffer: Error writing {buffer_size} logs to DB: {e}", exc_info=True)
                    return
        
        # Ejecutar en thread separado para no bloquear
        write_thread = threading.Thread(target=write_to_db, daemon=True)
        write_thread.start()
    
    def shutdown(self):
        """Cierra el buffer y hace flush final"""
        self._shutdown = True
        self.flush()
        logger.info("LogBuffer shutdown completed")


# Instancia global del buffer
_log_buffer = LogBuffer(batch_size=100, flush_interval=5)


def log_audit_async(action_type, **kwargs):
    """
    Función helper para logging asíncrono.
    
    Args:
        action_type: Tipo de acción (ej: 'udid_generated', 'udid_used')
        **kwargs: Campos adicionales del log (udid, client_ip, subscriber_code, etc.)
    
    Ejemplo:
        log_audit_async(
            action_type='udid_generated',
            udid='abc123',
            client_ip='192.168.1.1',
            user_agent='Mozilla/5.0',
            details={'method': 'manual_request'}
        )
    """
    try:
        log_data = {
            'action_type': action_type,
            **kwargs
        }
        _log_buffer.add(log_data)
    except Exception as e:
        # Si el buffer falla, loggear pero no bloquear
        logger.error(f"Error adding log to buffer: {e}", exc_info=True)


def flush_logs():
    """
    Fuerza flush de todos los logs pendientes.
    Útil para testing o shutdown graceful.
    """
    _log_buffer.flush()


def shutdown_log_buffer():
    """
    Cierra el buffer de logs y hace flush final.
    Útil para shutdown graceful de la aplicación.
    """
    _log_buffer.shutdown()

