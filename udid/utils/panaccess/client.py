"""
Cliente para interactuar con la API de Panaccess.

Este módulo proporciona una clase cliente para realizar llamadas a la API
de Panaccess, manejando automáticamente la autenticación y el sessionId.
"""
import logging
import time
import requests
from urllib.parse import urlencode
from typing import Dict, Any, Optional

from config import PanaccessConfig
from .auth import login, logged_in
from .exceptions import (
    PanaccessException,
    PanaccessConnectionError,
    PanaccessTimeoutError,
    PanaccessAPIError,
    PanaccessSessionError
)

logger = logging.getLogger(__name__)


class PanaccessClient:
    """
    Cliente para interactuar con la API de Panaccess.
    
    Maneja la autenticación y el sessionId automáticamente.
    Proporciona métodos para realizar llamadas a las funciones de la API.
    """
    
    # Tiempo de vida de sesión (4 horas, con margen de seguridad de 3.5 horas)
    SESSION_TTL = 3.5 * 3600  # 3.5 horas en segundos
    
    def __init__(self, base_url: str = None):
        """
        Inicializa el cliente de Panaccess.
        
        Args:
            base_url: URL base de Panaccess (por defecto usa la de la configuración)
        """
        PanaccessConfig.validate()
        self.base_url = base_url or PanaccessConfig.PANACCESS
        self.session_id: Optional[str] = None
        self._session_created_at: Optional[float] = None  # Timestamp de creación de sesión
    
    def authenticate(self) -> str:
        """
        Realiza la autenticación con Panaccess y guarda el sessionId.
        
        Returns:
            sessionId obtenido de Panaccess
        
        Raises:
            PanaccessException: Si hay algún error en la autenticación
        """
        self.session_id = login()
        self._session_created_at = time.time()  # Guardar timestamp de creación
        return self.session_id
    
    def _ensure_valid_session(self):
        """
        Asegura que haya una sesión válida usando cache basado en tiempo.
        
        No usa logged_in() porque puede fallar por problemas de permisos
        aunque la sesión sea válida. En su lugar, usa el tiempo transcurrido
        desde la creación de la sesión (las sesiones duran 4 horas).
        
        Solo refresca si:
        - No hay sessionId
        - Han pasado más de 3.5 horas desde la creación
        """
        # Si no hay sessionId, autenticar
        if not self.session_id:
            self.authenticate()
            return
        
        # Verificar si la sesión es "vieja" según el tiempo transcurrido
        if self._session_created_at is None:
            # Si no tenemos timestamp, asumir que es vieja y refrescar
            logger.debug("🔄 No hay timestamp de sesión en cliente, refrescando...")
            self.authenticate()
            return
        
        # Calcular tiempo transcurrido desde la creación de la sesión
        elapsed = time.time() - self._session_created_at
        
        if elapsed > self.SESSION_TTL:
            # Sesión expirada (más de 3.5 horas), refrescar
            logger.debug(
                f"🔄 Sesión expirada en cliente ({elapsed/3600:.2f} horas), refrescando..."
            )
            self.authenticate()
        else:
            # Sesión aún válida según tiempo
            logger.debug(
                f"✅ Sesión válida en cliente (creada hace {elapsed/60:.1f} minutos)"
            )
    
    def call(self, func_name: str, parameters: Dict[str, Any] = None, timeout: int = 60) -> Dict[str, Any]:
        """
        Llama a una función remota del API Panaccess.
        
        Si no hay sessionId o si está caducado, intenta autenticarse/refrescar
        automáticamente antes de realizar la llamada (excepto para la función 'login').
        
        Args:
            func_name: Nombre de la función a llamar (ej: 'getListOfSmartcards')
            parameters: Diccionario con los parámetros de la función
            timeout: Timeout en segundos para la conexión (default: 60)
        
        Returns:
            Diccionario con la respuesta de la API
        
        Raises:
            PanaccessException: Si hay algún error en la llamada
        """
        if parameters is None:
            parameters = {}
        
        # Asegurar sesión válida antes de hacer la llamada (excepto para login)
        if func_name != 'login' and func_name != 'cvLoggedIn':
            self._ensure_valid_session()
        
        # Preparar parámetros para logging (ocultar sessionId por seguridad)
        log_parameters = parameters.copy()
        if 'sessionId' in log_parameters:
            session_id_value = log_parameters['sessionId']
            if session_id_value:
                log_parameters['sessionId'] = f"{session_id_value[:20]}..." if len(str(session_id_value)) > 20 else "[REDACTED]"
        
        # Agregar sessionId a los parámetros si existe y no es login
        if self.session_id and func_name != 'login' and func_name != 'cvLoggedIn':
            parameters['sessionId'] = self.session_id
        
        # Construir URL
        url = f"{self.base_url}?f={func_name}&requestMode=function"
        
        # Preparar headers y datos
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        param_string = urlencode(parameters)
        
        # Log de la petición
        logger.info(f"📞 [call] Llamando función '{func_name}' - URL: {url}")
        logger.info(f"📞 [call] Parámetros: {log_parameters}")
        logger.debug(f"📞 [call] Headers: {headers}")
        logger.debug(f"📞 [call] Timeout: {timeout}s")
        
        try:
            response = requests.post(
                url,
                data=param_string,
                headers=headers,
                timeout=timeout
            )
            
            # Log del status code
            logger.info(f"📡 [call] Respuesta recibida para '{func_name}' - Status Code: {response.status_code}")
            
            response.raise_for_status()
            
            # Parsear respuesta JSON
            try:
                json_response = response.json()
                logger.info(f"📦 [call] Respuesta JSON completa para '{func_name}': {json_response}")
            except ValueError as e:
                logger.error(f"❌ [call] Error al parsear JSON para '{func_name}': {str(e)}")
                logger.error(f"❌ [call] Respuesta raw: {response.text}")
                raise PanaccessAPIError(
                    f"Respuesta inválida del servidor Panaccess: {response.text}",
                    status_code=response.status_code
                )
            
            # Verificar si hay error en la respuesta
            success = json_response.get("success")
            logger.info(f"✅ [call] Campo 'success' para '{func_name}': {success}")
            
            if not success:
                error_message = json_response.get("errorMessage", "Error desconocido")
                answer = json_response.get("answer")
                logger.error(f"❌ [call] Llamada a '{func_name}' falló - Error: {error_message}")
                logger.error(f"❌ [call] Campo 'answer' para '{func_name}': {answer}")
                
                # Si el error es de sesión, limpiar sessionId y timestamp
                if "session" in error_message.lower() or "logged" in error_message.lower():
                    logger.warning(f"⚠️ [call] Error de sesión detectado para '{func_name}', limpiando sessionId")
                    self.session_id = None
                    self._session_created_at = None
                    # Retornar el diccionario para compatibilidad, pero también lanzar excepción opcional
                    # El código existente puede manejar el diccionario con success=False
                    # Pero también podemos lanzar excepción si se prefiere manejo por excepciones
                    # Por ahora retornamos el diccionario para mantener compatibilidad
                
                # Retornar el diccionario completo para compatibilidad con código existente
                # El código puede verificar response.get('success') y manejar el error
                return json_response
            
            # Log del resultado exitoso
            answer = json_response.get("answer")
            logger.info(f"✅ [call] Llamada a '{func_name}' exitosa")
            logger.info(f"📋 [call] Campo 'answer' para '{func_name}': {answer} (tipo: {type(answer).__name__})")
            
            return json_response
            
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ [call] Timeout al llamar a '{func_name}' ({timeout} segundos)")
            raise PanaccessTimeoutError(
                f"Timeout al llamar a {func_name}. "
                f"El servidor no respondió en {timeout} segundos."
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"🔌 [call] Error de conexión al llamar a '{func_name}': {str(e)}")
            raise PanaccessConnectionError(
                f"Error de conexión con Panaccess: {str(e)}"
            )
        except requests.exceptions.HTTPError as e:
            status_code = response.status_code if 'response' in locals() else None
            logger.error(f"❌ [call] Error HTTP al llamar a '{func_name}': {str(e)} (Status: {status_code})")
            if 'response' in locals():
                logger.error(f"❌ [call] Respuesta completa: {response.text}")
            raise PanaccessAPIError(
                f"Error HTTP al llamar a {func_name}: {str(e)}",
                status_code=status_code
            )
        except (PanaccessException, PanaccessAPIError, PanaccessTimeoutError, PanaccessConnectionError, PanaccessSessionError):
            # Re-lanzar nuestras excepciones personalizadas
            raise
        except Exception as e:
            logger.error(f"💥 [call] Error inesperado al llamar a '{func_name}': {str(e)}", exc_info=True)
            raise PanaccessAPIError(
                f"Error inesperado al llamar a {func_name}: {str(e)}"
            )
    
    def logout(self) -> bool:
        """
        Cierra la sesión actual en Panaccess.
        
        Returns:
            True si el logout fue exitoso, False en caso contrario
        
        Raises:
            PanaccessException: Si hay algún error al cerrar sesión
        """
        if not self.session_id:
            return True  # Ya no hay sesión activa
        
        try:
            result = self.call("cvLogout", {})
            self.session_id = None
            return result.get("success", False)
        except PanaccessException:
            # Limpiar sessionId incluso si hay error
            self.session_id = None
            raise
    
    def login(self):
        """
        Realiza el login al sistema Panaccess y guarda el sessionId si es exitoso.
        
        Mantiene compatibilidad con código existente que espera (bool, str).
        
        Returns:
            tuple: (True, None) si es exitoso, (False, error_message) si falla
        """
        try:
            self.authenticate()
            return True, None
        except PanaccessException as e:
            return False, str(e)
        except Exception as e:
            return False, f"Error inesperado: {str(e)}"
    
    def is_authenticated(self) -> bool:
        """
        Verifica si hay una sesión activa.
        
        Returns:
            True si hay sessionId, False en caso contrario
        """
        return self.session_id is not None
    
    def check_session(self) -> bool:
        """
        Verifica si la sesión actual sigue siendo válida.
        
        Returns:
            True si la sesión es válida, False si está caducada
        
        Raises:
            PanaccessException: Si hay algún error al verificar la sesión
        """
        if not self.session_id:
            return False
        
        try:
            return logged_in(self.session_id)
        except PanaccessException:
            # Si hay error al verificar, asumimos que la sesión no es válida
            self.session_id = None
            return False


# Alias para mantener compatibilidad con código existente
CVClient = PanaccessClient
