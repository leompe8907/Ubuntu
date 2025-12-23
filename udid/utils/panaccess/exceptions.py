"""
Excepciones personalizadas para el manejo de errores de Panaccess.
"""
import logging

logger = logging.getLogger(__name__)


class PanaccessException(Exception):
    """Excepción base para todos los errores relacionados con Panaccess."""
    pass


class PanaccessAuthenticationError(PanaccessException):
    """Error de autenticación con Panaccess (credenciales inválidas, API key deshabilitada, etc.)."""
    pass


class PanaccessSessionError(PanaccessException):
    """Error relacionado con la sesión de Panaccess (sesión expirada, inválida, etc.)."""
    pass


class PanaccessRateLimitError(PanaccessException):
    """Error cuando se excede el límite de rate limiting (más de 20 logins en 5 minutos)."""
    pass


class PanaccessConnectionError(PanaccessException):
    """Error de conexión con el servidor de Panaccess."""
    pass


class PanaccessTimeoutError(PanaccessException):
    """Error de timeout al comunicarse con Panaccess."""
    pass


class PanaccessAPIError(PanaccessException):
    """Error genérico de la API de Panaccess."""
    
    def __init__(self, message, status_code=None, error_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code

