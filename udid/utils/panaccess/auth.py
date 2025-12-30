"""
Funciones de autenticación con Panaccess.

Este módulo proporciona funciones para autenticarse con la API de Panaccess
y obtener un sessionId para realizar llamadas posteriores.
"""
import hashlib
import logging
import requests
from urllib.parse import urlencode

from config import PanaccessConfig
from .exceptions import (
    PanaccessAuthenticationError,
    PanaccessConnectionError,
    PanaccessTimeoutError,
    PanaccessAPIError
)

logger = logging.getLogger(__name__)


def hash_password(password: str, salt: str = None) -> str:
    """
    Genera un hash MD5 del password con sal.
    
    Uso específico requerido por Panaccess. No recomendado para otros 
    contextos de seguridad.
    
    Args:
        password: Contraseña en texto plano
        salt: Salt para el hash (por defecto usa el de la configuración)
    
    Returns:
        Hash MD5 hexadecimal del password + salt
    """
    if salt is None:
        salt = PanaccessConfig.SALT
    
    return hashlib.md5((password + salt).encode()).hexdigest()


def login() -> str:
    """
    Realiza login en Panaccess y retorna el sessionId.
    
    Autentica usando las credenciales configuradas en PanaccessConfig
    y retorna el sessionId encriptado para usar en llamadas posteriores.
    
    Nota: No hacer más de 20 logins en 5 minutos o se activará el rate limiter.
    
    Returns:
        sessionId encriptado para usar en llamadas posteriores
    
    Raises:
        PanaccessAuthenticationError: Si las credenciales son inválidas o 
            el API key está deshabilitado
        PanaccessConnectionError: Si hay problemas de conexión
        PanaccessTimeoutError: Si la petición excede el timeout
        PanaccessAPIError: Si hay un error genérico de la API
    """
    # Validar configuración
    PanaccessConfig.validate()
    
    username = PanaccessConfig.USERNAME
    password = PanaccessConfig.PASSWORD
    api_token = PanaccessConfig.API_TOKEN
    base_url = PanaccessConfig.PANACCESS
    
    if not username or not password or not api_token:
        raise PanaccessAuthenticationError(
            "Faltan credenciales de Panaccess en la configuración. "
            "Verifica las variables de entorno: username, password, api_token"
        )
    
    # Hashear contraseña
    hashed_password = hash_password(password)
    
    # Preparar payload
    payload = {
        "username": username,
        "password": hashed_password,
        "apiToken": api_token
    }
    
    # URL del endpoint
    url = f"{base_url}?f=login&requestMode=function"
    
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    param_string = urlencode(payload)
    
    # Log de la petición
    logger.info(f"🔐 [login] Iniciando login - URL: {url}")
    logger.debug(f"🔐 [login] Payload (sin password): {{'username': '{username}', 'password': '[HASHED]', 'apiToken': '[REDACTED]'}}")
    logger.debug(f"🔐 [login] Headers: {headers}")
    
    try:
        response = requests.post(
            url,
            data=param_string,
            headers=headers,
            timeout=30
        )
        
        # Log del status code
        logger.info(f"📡 [login] Respuesta recibida - Status Code: {response.status_code}")
        
        # Verificar status code
        if response.status_code != 200:
            logger.error(f"❌ [login] Status code inesperado: {response.status_code}")
            # Truncar respuesta para evitar logs enormes
            response_text = response.text[:1000] if len(response.text) > 1000 else response.text
            logger.error(f"❌ [login] Respuesta (primeros 1000 chars): {response_text}")
            raise PanaccessAPIError(
                f"Respuesta inesperada del servidor Panaccess: {response.status_code}",
                status_code=response.status_code
            )
        
        # Parsear respuesta JSON
        try:
            json_response = response.json()
            # NO loguear la respuesta completa - solo un resumen
            if logger.isEnabledFor(logging.DEBUG):
                json_str = str(json_response)
                if len(json_str) > 500:
                    json_summary = json_str[:500] + "... [truncado]"
                else:
                    json_summary = json_str
                logger.debug(f"📦 [login] Respuesta JSON (resumen): {json_summary}")
            else:
                logger.info(f"📦 [login] Respuesta JSON recibida exitosamente")
        except ValueError as e:
            logger.error(f"❌ [login] Error al parsear JSON: {str(e)}")
            # Truncar respuesta raw para evitar logs enormes
            response_text = response.text[:1000] if len(response.text) > 1000 else response.text
            logger.error(f"❌ [login] Respuesta raw (primeros 1000 chars): {response_text}")
            raise PanaccessAPIError(
                f"Respuesta inválida del servidor Panaccess: {response.text}",
                status_code=response.status_code
            )
        
        # Verificar si el login fue exitoso
        success = json_response.get("success")
        logger.info(f"✅ [login] Campo 'success' en respuesta: {success}")
        
        if not success:
            error_message = json_response.get("errorMessage", "Login fallido sin mensaje explícito")
            answer = json_response.get("answer")
            logger.error(f"❌ [login] Login fallido - Error: {error_message}")
            # Solo loguear un resumen del answer si es muy grande
            if answer:
                answer_str = str(answer)
                if len(answer_str) > 200:
                    logger.error(f"❌ [login] Campo 'answer': {answer_str[:200]}... [truncado]")
                else:
                    logger.error(f"❌ [login] Campo 'answer': {answer}")
            
            # Si retorna 'false' como string, es error de autenticación
            if answer == "false" or error_message:
                raise PanaccessAuthenticationError(
                    f"Error de autenticación: {error_message}"
                )
            
            raise PanaccessAPIError(
                f"Error en la respuesta de Panaccess: {error_message}",
                status_code=response.status_code
            )
        
        # Extraer sessionId
        session_id = json_response.get("answer")
        logger.info(f"🔑 [login] Campo 'answer' (sessionId): {session_id[:20] + '...' if session_id and len(session_id) > 20 else session_id}")
        
        if not session_id:
            logger.error("❌ [login] No se recibió sessionId en la respuesta")
            raise PanaccessAPIError(
                "Login exitoso pero no se recibió sessionId en la respuesta"
            )
        
        logger.info(f"✅ [login] Login exitoso - SessionId obtenido (longitud: {len(session_id) if session_id else 0} caracteres)")
        return session_id
        
    except requests.exceptions.Timeout:
        logger.error("⏱️ [login] Timeout al intentar login (30 segundos)")
        raise PanaccessTimeoutError(
            "Timeout al intentar conectarse con Panaccess. "
            "El servidor no respondió en 30 segundos."
        )
    except requests.exceptions.ConnectionError as e:
        logger.error(f"🔌 [login] Error de conexión: {str(e)}")
        raise PanaccessConnectionError(
            f"Error de conexión con Panaccess: {str(e)}"
        )
    except (PanaccessAuthenticationError, PanaccessAPIError, PanaccessTimeoutError, PanaccessConnectionError):
        # Re-lanzar nuestras excepciones personalizadas
        raise
    except Exception as e:
        logger.error(f"💥 [login] Error inesperado: {str(e)}", exc_info=True)
        raise PanaccessAPIError(
            f"Error inesperado al intentar login con Panaccess: {str(e)}"
        )


def logged_in(session_id: str) -> bool:
    """
    Verifica si un sessionId de Panaccess sigue siendo válido.
    
    Esta función puede usarse para confirmar si la sesión sigue activa.
    Si retorna False, será necesario hacer login nuevamente.
    
    Args:
        session_id: El sessionId retornado por la función login()
    
    Returns:
        True si la sesión es válida, False si está caducada o es inválida
    
    Raises:
        PanaccessConnectionError: Si hay problemas de conexión
        PanaccessTimeoutError: Si la petición excede el timeout
        PanaccessAPIError: Si hay un error genérico de la API
    """
    # Validar configuración
    PanaccessConfig.validate()
    
    if not session_id:
        logger.debug("🔍 [logged_in] No hay session_id proporcionado, retornando False")
        return False
    
    base_url = PanaccessConfig.PANACCESS
    
    # Preparar payload
    payload = {
        "sessionId": session_id
    }
    
    # URL del endpoint
    url = f"{base_url}?f=cvLoggedIn&requestMode=function"
    
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    param_string = urlencode(payload)
    
    # Log de la petición
    logger.info(f"🔍 [logged_in] Verificando sesión - URL: {url}")
    logger.debug(f"🔍 [logged_in] Payload: {payload}")
    logger.debug(f"🔍 [logged_in] Headers: {headers}")
    
    try:
        response = requests.post(
            url,
            data=param_string,
            headers=headers,
            timeout=30
        )
        
        # Log del status code
        logger.info(f"📡 [logged_in] Respuesta recibida - Status Code: {response.status_code}")
        
        # Verificar status code
        if response.status_code != 200:
            logger.error(f"❌ [logged_in] Status code inesperado: {response.status_code}")
            # Truncar respuesta para evitar logs enormes
            response_text = response.text[:1000] if len(response.text) > 1000 else response.text
            logger.error(f"❌ [logged_in] Respuesta (primeros 1000 chars): {response_text}")
            raise PanaccessAPIError(
                f"Respuesta inesperada del servidor Panaccess: {response.status_code}",
                status_code=response.status_code
            )
        
        # Parsear respuesta JSON
        try:
            json_response = response.json()
            # NO loguear la respuesta completa - solo un resumen
            if logger.isEnabledFor(logging.DEBUG):
                json_str = str(json_response)
                if len(json_str) > 500:
                    json_summary = json_str[:500] + "... [truncado]"
                else:
                    json_summary = json_str
                logger.debug(f"📦 [logged_in] Respuesta JSON (resumen): {json_summary}")
            else:
                logger.info(f"📦 [logged_in] Respuesta JSON recibida exitosamente")
        except ValueError as e:
            logger.error(f"❌ [logged_in] Error al parsear JSON: {str(e)}")
            # Truncar respuesta raw para evitar logs enormes
            response_text = response.text[:1000] if len(response.text) > 1000 else response.text
            logger.error(f"❌ [logged_in] Respuesta raw (primeros 1000 chars): {response_text}")
            raise PanaccessAPIError(
                f"Respuesta inválida del servidor Panaccess: {response.text}",
                status_code=response.status_code
            )
        
        # Verificar si la llamada fue exitosa
        success = json_response.get("success")
        logger.info(f"✅ [logged_in] Campo 'success' en respuesta: {success}")
        
        if not success:
            # Si la llamada falla, asumimos que la sesión no es válida
            error_message = json_response.get("errorMessage", "Sin mensaje de error")
            logger.warning(f"⚠️ [logged_in] Llamada no exitosa - Error: {error_message}")
            logger.info(f"🔍 [logged_in] Resultado: Sesión NO válida (False)")
            return False
        
        # La respuesta debe ser un booleano
        answer = json_response.get("answer")
        # NO loguear el answer completo - solo tipo y resumen
        if isinstance(answer, dict):
            logger.info(f"📋 [logged_in] Campo 'answer' en respuesta: recibido (tipo: {type(answer).__name__}, keys: {list(answer.keys())[:5]})")
        else:
            answer_str = str(answer)
            if len(answer_str) > 100:
                logger.info(f"📋 [logged_in] Campo 'answer' en respuesta: {answer_str[:100]}... [truncado] (tipo: {type(answer).__name__})")
            else:
                logger.info(f"📋 [logged_in] Campo 'answer' en respuesta: {answer} (tipo: {type(answer).__name__})")
        
        # Panaccess puede retornar el booleano como string o como booleano
        if isinstance(answer, bool):
            result = answer
            logger.info(f"✅ [logged_in] Resultado final: Sesión {'VÁLIDA' if result else 'NO VÁLIDA'} ({result})")
            return result
        elif isinstance(answer, str):
            result = answer.lower() in ('true', '1', 'yes')
            logger.info(f"✅ [logged_in] Resultado final (convertido desde string): Sesión {'VÁLIDA' if result else 'NO VÁLIDA'} ({result})")
            return result
        else:
            # Si no es booleano ni string, asumimos False
            logger.warning(f"⚠️ [logged_in] Tipo de 'answer' inesperado: {type(answer).__name__}, asumiendo False")
            logger.info(f"🔍 [logged_in] Resultado: Sesión NO válida (False)")
            return False
        
    except requests.exceptions.Timeout:
        logger.error("⏱️ [logged_in] Timeout al verificar sesión (30 segundos)")
        raise PanaccessTimeoutError(
            "Timeout al intentar verificar sesión con Panaccess. "
            "El servidor no respondió en 30 segundos."
        )
    except requests.exceptions.ConnectionError as e:
        logger.error(f"🔌 [logged_in] Error de conexión: {str(e)}")
        raise PanaccessConnectionError(
            f"Error de conexión con Panaccess: {str(e)}"
        )
    except (PanaccessTimeoutError, PanaccessConnectionError, PanaccessAPIError):
        # Re-lanzar excepciones de conexión/timeout/API
        raise
    except Exception as e:
        logger.error(f"💥 [logged_in] Error inesperado: {str(e)}", exc_info=True)
        raise PanaccessAPIError(
            f"Error inesperado al verificar sesión con Panaccess: {str(e)}"
        )

