"""
Utilidades para manejo de conexiones de base de datos con reconexión automática.
Compatible con MySQL y PostgreSQL.
"""
import logging
import time
from django.db import connection
from django.db.utils import OperationalError, DatabaseError

logger = logging.getLogger(__name__)

# Errores comunes de conexión perdida
MYSQL_CONNECTION_ERRORS = [
    '2006',  # MySQL server has gone away
    '2013',  # Lost connection to MySQL server
    'Server has gone away',
    'Lost connection',
    'Connection lost',
    'Broken pipe',
]

POSTGRESQL_CONNECTION_ERRORS = [
    'server closed the connection',
    'connection to server was lost',
    'terminating connection due to administrator command',
    'connection unexpectedly closed',
    'could not receive data from server',
    'connection refused',
    'FATAL: terminating connection',
]

ALL_CONNECTION_ERRORS = MYSQL_CONNECTION_ERRORS + POSTGRESQL_CONNECTION_ERRORS


def is_connection_error(error):
    """
    Detecta si un error es de conexión perdida (MySQL o PostgreSQL).
    
    Args:
        error: Excepción o string del error
    
    Returns:
        bool: True si es un error de conexión perdida
    """
    error_str = str(error).lower()
    
    for error_pattern in ALL_CONNECTION_ERRORS:
        if error_pattern.lower() in error_str:
            return True
    
    return False


def reconnect_database():
    """
    Cierra la conexión actual y fuerza una reconexión.
    Compatible con MySQL y PostgreSQL.
    """
    try:
        connection.close()
        logger.debug("🔌 Conexión a BD cerrada, se reconectará automáticamente")
    except Exception as e:
        logger.warning(f"Error al cerrar conexión: {str(e)}")


def execute_with_reconnect(func, max_retries=3, retry_delay=2, *args, **kwargs):
    """
    Ejecuta una función con reconexión automática en caso de error de conexión.
    
    Args:
        func: Función a ejecutar
        max_retries: Número máximo de reintentos
        retry_delay: Delay base entre reintentos (segundos)
        *args, **kwargs: Argumentos para la función
    
    Returns:
        Resultado de la función
    
    Raises:
        DatabaseError: Si no se puede reconectar después de max_retries
        Exception: Otros errores que no sean de conexión
    """
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            return func(*args, **kwargs)
            
        except (OperationalError, DatabaseError) as e:
            if is_connection_error(e):
                retry_count += 1
                logger.warning(
                    f"🔌 Conexión a BD perdida (intento {retry_count}/{max_retries}). "
                    f"Reconectando..."
                )
                
                # Cerrar conexión actual
                reconnect_database()
                
                if retry_count < max_retries:
                    # Backoff exponencial
                    delay = retry_delay * retry_count
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"❌ No se pudo reconectar a la BD después de {max_retries} intentos"
                    )
                    raise DatabaseError(
                        f"No se pudo reconectar a la BD después de {max_retries} intentos: {str(e)}"
                    )
            else:
                # Otro error de BD, no reintentar
                logger.error(f"❌ Error de base de datos: {str(e)}")
                raise
                
        except Exception as e:
            # Otros errores, no reintentar
            raise
    
    # Si llegamos aquí, se agotaron los reintentos
    raise DatabaseError(f"No se pudo ejecutar la operación después de {max_retries} intentos")


def atomic_with_reconnect(max_retries=3, retry_delay=2):
    """
    Context manager para transaction.atomic() con reconexión automática.
    
    Uso:
        with atomic_with_reconnect():
            # Operaciones de BD
            Model.objects.bulk_create(...)
    
    Args:
        max_retries: Número máximo de reintentos
        retry_delay: Delay base entre reintentos (segundos)
    """
    from django.db import transaction
    
    class AtomicWithReconnect:
        def __init__(self, max_retries, retry_delay):
            self.max_retries = max_retries
            self.retry_delay = retry_delay
            self.retry_count = 0
        
        def __enter__(self):
            while self.retry_count < self.max_retries:
                try:
                    self.atomic_context = transaction.atomic()
                    return self.atomic_context.__enter__()
                except (OperationalError, DatabaseError) as e:
                    if is_connection_error(e):
                        self.retry_count += 1
                        logger.warning(
                            f"🔌 Error de conexión al iniciar transacción "
                            f"(intento {self.retry_count}/{self.max_retries}). Reconectando..."
                        )
                        reconnect_database()
                        if self.retry_count < self.max_retries:
                            time.sleep(self.retry_delay * self.retry_count)
                            continue
                        else:
                            raise DatabaseError(
                                f"No se pudo iniciar transacción después de {self.max_retries} intentos"
                            )
                    raise
            raise DatabaseError("No se pudo iniciar transacción")
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type and is_connection_error(exc_val):
                reconnect_database()
            return self.atomic_context.__exit__(exc_type, exc_val, exc_tb)
    
    return AtomicWithReconnect(max_retries, retry_delay)

