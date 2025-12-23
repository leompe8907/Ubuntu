"""
Cliente singleton thread-safe para Panaccess.

Este módulo proporciona una instancia única y compartida del cliente Panaccess
que se inicializa al arrancar Django y se mantiene durante toda la vida del servidor.
"""
import threading
import time
import logging
from typing import Optional

from .client import PanaccessClient
from .auth import login, logged_in
from .exceptions import (
    PanaccessException,
    PanaccessAuthenticationError,
    PanaccessConnectionError,
    PanaccessTimeoutError
)

logger = logging.getLogger(__name__)


class PanaccessSingleton:
    """
    Singleton thread-safe para el cliente Panaccess.
    
    Garantiza que solo haya una instancia del cliente compartida entre
    todos los threads/workers, con manejo seguro de concurrencia.
    """
    
    _instance = None
    _lock = threading.Lock()  # Lock para inicialización
    _session_lock = threading.RLock()  # Reentrant lock para sesión
    
    # Configuración de reintentos
    MAX_RETRY_ATTEMPTS = 5
    INITIAL_RETRY_DELAY = 1  # segundos
    MAX_RETRY_DELAY = 60  # segundos
    ALERT_AFTER_ATTEMPTS = 3  # Enviar alerta después de X intentos
    
    # Configuración de validación periódica
    VALIDATION_INTERVAL = 6000  # Validar cada hora (6000 segundos = 1 hora)
    
    # Configuración de tiempo de vida de sesión
    SESSION_TTL = 3.5 * 3600  # 3.5 horas en segundos (casi 4 horas, margen de seguridad)
    
    def __new__(cls):
        """
        Implementa el patrón Singleton con thread-safety.
        """
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super(PanaccessSingleton, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """
        Inicializa el singleton (solo se ejecuta una vez).
        """
        if self._initialized:
            return
        
        self.client = PanaccessClient()
        self._initialized = True
        self._retry_count = 0
        self._last_alert_sent = False
        self._validation_thread = None
        self._stop_validation = threading.Event()
        self._session_created_at = None  # Timestamp de cuando se creó la sesión actual
        
        logger.info("✅ PanaccessSingleton inicializado")
    
    def _authenticate_with_retry(self) -> str:
        """
        Intenta autenticarse con reintentos y backoff exponencial.
        
        Returns:
            sessionId obtenido
        
        Raises:
            PanaccessException: Si falla después de todos los reintentos
        """
        attempt = 0
        delay = self.INITIAL_RETRY_DELAY
        
        while attempt < self.MAX_RETRY_ATTEMPTS:
            try:
                logger.info(f"🔄 Intento de login #{attempt + 1}/{self.MAX_RETRY_ATTEMPTS}")
                session_id = login()
                
                # Login exitoso, resetear contador y actualizar timestamp
                self._retry_count = 0
                self._last_alert_sent = False
                self._session_created_at = time.time()  # Guardar timestamp de creación
                logger.info("✅ Login exitoso")
                return session_id
                
            except (PanaccessAuthenticationError, PanaccessConnectionError, PanaccessTimeoutError) as e:
                attempt += 1
                self._retry_count = attempt
                
                # Enviar alerta después de X intentos
                if attempt >= self.ALERT_AFTER_ATTEMPTS and not self._last_alert_sent:
                    self._send_alert(attempt, str(e))
                    self._last_alert_sent = True
                
                # Si es el último intento, lanzar excepción
                if attempt >= self.MAX_RETRY_ATTEMPTS:
                    logger.error(f"❌ Login falló después de {self.MAX_RETRY_ATTEMPTS} intentos")
                    raise PanaccessException(
                        f"Error de autenticación después de {self.MAX_RETRY_ATTEMPTS} intentos: {str(e)}"
                    )
                
                # Calcular delay con backoff exponencial
                delay = min(delay * 2, self.MAX_RETRY_DELAY)
                logger.warning(
                    f"⚠️ Login falló (intento {attempt}/{self.MAX_RETRY_ATTEMPTS}). "
                    f"Reintentando en {delay} segundos... Error: {str(e)}"
                )
                
                time.sleep(delay)
            
            except PanaccessException as e:
                # Re-lanzar excepciones de Panaccess
                raise
            except Exception as e:
                # Error inesperado
                attempt += 1
                if attempt >= self.MAX_RETRY_ATTEMPTS:
                    logger.error(f"❌ Error inesperado después de {attempt} intentos: {str(e)}")
                    raise PanaccessException(f"Error inesperado en login: {str(e)}")
                
                delay = min(delay * 2, self.MAX_RETRY_DELAY)
                logger.warning(
                    f"⚠️ Error inesperado (intento {attempt}/{self.MAX_RETRY_ATTEMPTS}). "
                    f"Reintentando en {delay} segundos..."
                )
                time.sleep(delay)
        
        # No debería llegar aquí, pero por seguridad
        raise PanaccessException("Error crítico: no se pudo autenticar después de múltiples intentos")
    
    def _send_alert(self, attempt: int, error_message: str):
        """
        Envía una alerta cuando se superan los intentos de alerta.
        
        Por ahora solo loguea, pero puedes extender esto para enviar emails,
        notificaciones, etc.
        
        Args:
            attempt: Número de intento actual
            error_message: Mensaje de error
        """
        alert_message = (
            f"🚨 ALERTA: Panaccess login ha fallado {attempt} veces. "
            f"Último error: {error_message}. "
            f"El sistema seguirá intentando hasta {self.MAX_RETRY_ATTEMPTS} intentos."
        )
        logger.error(alert_message)
        
        # TODO: Aquí puedes agregar:
        # - Envío de email
        # - Notificación a Slack/Discord
        # - Métricas a sistema de monitoreo
        # - etc.
    
    def ensure_session(self):
        """
        Asegura que haya una sesión válida (thread-safe).
        
        Usa un cache basado en tiempo en lugar de verificar con cvLoggedIn,
        ya que las sesiones de Panaccess duran 4 horas y la verificación
        puede fallar por problemas de permisos.
        
        Solo refresca si:
        - No hay sessionId
        - Han pasado más de 3.5 horas desde la creación (margen de seguridad)
        
        Solo un thread puede ejecutar el refresh a la vez.
        """
        with self._session_lock:
            # Verificar si hay sessionId
            if not self.client.session_id:
                logger.info("🔑 No hay sesión, autenticando...")
                self.client.session_id = self._authenticate_with_retry()
                # _authenticate_with_retry ya actualiza _session_created_at
                return
            
            # Verificar si la sesión es "vieja" según el tiempo transcurrido
            if self._session_created_at is None:
                # Si no tenemos timestamp, asumir que es vieja y refrescar
                logger.info("🔄 No hay timestamp de sesión, refrescando...")
                self.client.session_id = self._authenticate_with_retry()
                # _authenticate_with_retry ya actualiza _session_created_at
                return
            
            # Calcular tiempo transcurrido desde la creación de la sesión
            elapsed = time.time() - self._session_created_at
            
            if elapsed > self.SESSION_TTL:
                # Sesión expirada (más de 3.5 horas), refrescar
                logger.info(
                    f"🔄 Sesión expirada ({elapsed/3600:.2f} horas > {self.SESSION_TTL/3600:.2f} horas), "
                    f"refrescando..."
                )
                self.client.session_id = self._authenticate_with_retry()
                # _authenticate_with_retry ya actualiza _session_created_at
            else:
                # Sesión aún válida según tiempo
                logger.debug(
                    f"✅ Sesión válida (creada hace {elapsed/60:.1f} minutos, "
                    f"expira en {(self.SESSION_TTL - elapsed)/60:.1f} minutos)"
                )
    
    def call(self, func_name: str, parameters: dict = None, timeout: Optional[int] = 60) -> dict:
        """
        Llama a una función de la API Panaccess (thread-safe).
        
        Asegura que haya una sesión válida antes de cada llamada usando
        el cache basado en tiempo (no verifica con cvLoggedIn que puede
        fallar por permisos).
        
        Args:
            func_name: Nombre de la función a llamar
            parameters: Parámetros de la función
            timeout: Timeout en segundos (None = sin timeout, default: 60)
        
        Returns:
            Respuesta de la API
        
        Raises:
            PanaccessException: Si hay algún error
        """
        # Asegurar sesión válida antes de cada llamada (excepto login)
        # Usa cache basado en tiempo en lugar de verificar con cvLoggedIn
        if func_name != 'login' and func_name != 'cvLoggedIn':
            self.ensure_session()
        
        # Usar el cliente para hacer la llamada
        # El cliente ya tiene el sessionId y lo agregará automáticamente
        return self.client.call(func_name, parameters, timeout)
    
    def get_client(self) -> PanaccessClient:
        """
        Obtiene la instancia del cliente (para uso avanzado).
        
        Returns:
            Instancia del PanaccessClient
        """
        return self.client
    
    def reset_session(self):
        """
        Fuerza el reset de la sesión (útil para testing o recuperación).
        """
        with self._session_lock:
            self.client.session_id = None
            self._session_created_at = None  # Limpiar también el timestamp
            logger.info("🔄 Sesión reseteada manualmente")
    
    def _periodic_validation(self):
        """
        Thread en background que valida periódicamente si la sesión está activa.
        
        Usa el cache basado en tiempo para verificar si la sesión necesita refrescarse.
        Si la sesión está caducada (más de 3.5 horas), la refresca automáticamente.
        Este thread se ejecuta cada VALIDATION_INTERVAL segundos.
        """
        logger.info(f"🔄 Thread de validación periódica iniciado (intervalo: {self.VALIDATION_INTERVAL}s)")
        
        while not self._stop_validation.is_set():
            try:
                # Esperar el intervalo (o hasta que se detenga)
                if self._stop_validation.wait(timeout=self.VALIDATION_INTERVAL):
                    # Si el evento está activado, salir del loop
                    break
                
                # Validar y refrescar si es necesario (thread-safe)
                # ensure_session() usa el cache basado en tiempo
                logger.debug("🔍 Validando sesión periódicamente (basado en tiempo)...")
                self.ensure_session()
                logger.debug("✅ Validación periódica completada")
                
            except Exception as e:
                logger.error(f"❌ Error en validación periódica: {str(e)}")
                # Continuar el loop aunque haya error
                # El siguiente ciclo intentará nuevamente
        
        logger.info("🛑 Thread de validación periódica detenido")
    
    def start_periodic_validation(self):
        """
        Inicia el thread de validación periódica en background.
        
        Este thread valida la sesión cada VALIDATION_INTERVAL segundos
        y la refresca automáticamente si está caducada.
        """
        if self._validation_thread is not None and self._validation_thread.is_alive():
            logger.warning("⚠️ Thread de validación ya está corriendo")
            return
        
        # Detener cualquier thread anterior
        self.stop_periodic_validation()
        
        # Crear y empezar nuevo thread
        self._stop_validation.clear()
        self._validation_thread = threading.Thread(
            target=self._periodic_validation,
            name="PanaccessValidationThread",
            daemon=True  # Thread daemon se detiene cuando el proceso principal termina
        )
        self._validation_thread.start()
        logger.info("✅ Thread de validación periódica iniciado")
    
    def stop_periodic_validation(self):
        """
        Detiene el thread de validación periódica.
        """
        if self._validation_thread is not None and self._validation_thread.is_alive():
            logger.info("🛑 Deteniendo thread de validación periódica...")
            self._stop_validation.set()
            self._validation_thread.join(timeout=5)  # Esperar máximo 5 segundos
            if self._validation_thread.is_alive():
                logger.warning("⚠️ Thread de validación no se detuvo en 5 segundos")
            else:
                logger.info("✅ Thread de validación detenido correctamente")
            self._validation_thread = None


# Instancia global del singleton
_panaccess_singleton: Optional[PanaccessSingleton] = None


def get_panaccess() -> PanaccessSingleton:
    """
    Obtiene la instancia singleton de Panaccess.
    
    Returns:
        Instancia de PanaccessSingleton
    """
    global _panaccess_singleton
    if _panaccess_singleton is None:
        _panaccess_singleton = PanaccessSingleton()
    return _panaccess_singleton


def initialize_panaccess():
    """
    Inicializa el singleton, realiza el primer login y inicia la validación periódica.
    
    Esta función debe llamarse al arrancar Django (en AppConfig.ready()).
    
    Flujo:
    1. Obtiene el singleton
    2. Hace login inicial
    3. Inicia thread de validación periódica en background
    """
    singleton = get_panaccess()
    try:
        # 1. Login inicial
        singleton.ensure_session()
        logger.info("✅ Panaccess inicializado y autenticado correctamente")
        
        # 2. Iniciar validación periódica en background
        singleton.start_periodic_validation()
        logger.info("✅ Validación periódica iniciada")
        
    except PanaccessException as e:
        logger.error(f"❌ Error al inicializar Panaccess: {str(e)}")
        # No lanzamos excepción para que Django pueda arrancar
        # El sistema intentará autenticarse en el primer request
        logger.warning("⚠️ El sistema intentará autenticarse en el primer request")
        
        # Intentar iniciar validación periódica de todas formas
        # (puede que el login falle pero la validación periódica lo intente después)
        try:
            singleton.start_periodic_validation()
        except Exception as ve:
            logger.error(f"❌ Error al iniciar validación periódica: {str(ve)}")

