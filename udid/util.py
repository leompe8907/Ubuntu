import hashlib
import json
import time
import random
import math
import logging
import uuid
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache

# Logger específico para rate limiting
logger = logging.getLogger('rate_limiting')

# Importar utilidades de Redis HA
try:
    from .utils.redis_ha import get_redis_client_safe, is_redis_available
    REDIS_HA_AVAILABLE = True
except ImportError:
    REDIS_HA_AVAILABLE = False
    logger.warning("redis_ha module not available, using direct Redis connections")

def get_client_ip(request):
    """Obtener la IP real del cliente desde request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0]
    return request.META.get('REMOTE_ADDR')

def compute_encrypted_hash(encrypted_data):
    """Generar hash SHA256 para payloads cifrados"""
    return hashlib.sha256(encrypted_data.encode()).hexdigest()

def json_serialize_credentials(credentials_dict):
    """Serializar credenciales a JSON para cifrado"""
    return json.dumps(credentials_dict)

def is_valid_app_type(app_type):
    return app_type in [
        'android_tv', 'samsung_tv', 'lg_tv', 'set_top_box', 'mobile_app', 'web_player'
    ]

# ============================================================================
# RATE LIMITING MULTICAPA -
# ============================================================================

def _get_header_value(source, header_name):
    """
    Obtiene el valor de un header desde request.META (HTTP) o scope (WebSocket).
    
    Args:
        source: Request object (tiene .META) o scope dict (tiene ['headers'])
        header_name: Nombre del header en formato Django (ej: 'HTTP_X_DEVICE_ID')
        
    Returns:
        str: Valor del header o string vacío
    """
    # Si es un request object (HTTP)
    if hasattr(source, 'META'):
        return source.META.get(header_name, '')
    
    # Si es un scope dict (WebSocket)
    elif isinstance(source, dict) and 'headers' in source:
        headers = dict(source.get('headers', []))
        
        # Convertir nombre de header Django a formato HTTP estándar
        # 'HTTP_X_DEVICE_ID' -> 'x-device-id'
        # 'HTTP_USER_AGENT' -> 'user-agent'
        header_key = header_name.lower()
        
        # Remover prefijo 'HTTP_' si existe
        if header_key.startswith('http_'):
            header_key = header_key[5:]  # Remover 'http_'
        
        # Convertir snake_case a kebab-case
        header_key = header_key.replace('_', '-')
        
        # Buscar en headers (los headers en scope están en formato (bytes, bytes))
        header_key_bytes = header_key.encode().lower()
        
        # Buscar en el diccionario de headers
        for key, value in headers.items():
            if isinstance(key, bytes) and key.lower() == header_key_bytes:
                if isinstance(value, bytes):
                    return value.decode(errors='ignore')
                return str(value)
        
        return ''
    
    return ''


def _build_device_fingerprint_string(headers_dict):
    """
    Construye el string de fingerprint a partir de un diccionario de headers.
    Función centralizada para evitar duplicación de código.
    
    ✅ MEJORADO: Incluye MAC address para mayor robustez
    
    Args:
        headers_dict: Diccionario con todos los headers necesarios
        
    Returns:
        str: String para hashear
    """
    app_type = headers_dict.get('app_type', '')
    mac_address = headers_dict.get('mac_address', '')  # ✅ NUEVO: MAC address
    
    # Combinar factores según el tipo de app para mayor robustez
    if app_type in ['android_tv', 'samsung_tv', 'lg_tv', 'set_top_box']:
        # Smart TV: usar serial, model, firmware, MAC (más difícil de falsificar)
        fingerprint_string = (
            f"{app_type}|{headers_dict.get('tv_serial', '')}|"
            f"{headers_dict.get('tv_model', '')}|{headers_dict.get('firmware_version', '')}|"
            f"{headers_dict.get('device_id', '')}|{mac_address}|"  # ✅ Agregado MAC
            f"{headers_dict.get('app_version', '')}|{headers_dict.get('user_agent', '')}"
        )
    elif app_type in ['android_mobile', 'ios_mobile', 'mobile_app']:
        # Móvil: usar device_id, build_id, model, os_version, MAC (identificadores nativos)
        fingerprint_string = (
            f"{app_type}|{headers_dict.get('device_id', '')}|"
            f"{headers_dict.get('build_id', '')}|{headers_dict.get('device_model', '')}|"
            f"{headers_dict.get('os_version', '')}|{mac_address}|"  # ✅ Agregado MAC
            f"{headers_dict.get('app_version', '')}|{headers_dict.get('user_agent', '')}"
        )
    else:
        # Fallback: usar headers básicos + app_type + MAC si está disponible
        fingerprint_string = (
            f"{headers_dict.get('user_agent', '')}|"
            f"{headers_dict.get('accept_language', '')}|"
            f"{headers_dict.get('accept_encoding', '')}|"
            f"{headers_dict.get('accept', '')}|{app_type}|"
            f"{headers_dict.get('app_version', '')}|{headers_dict.get('device_id', '')}|"
            f"{mac_address}"  # ✅ Agregado MAC
        )
    
    return fingerprint_string


def generate_device_fingerprint(request_or_scope):
    """
    Genera un fingerprint único del dispositivo basado en características del request/scope.
    Mejorado para móviles y Smart TVs con headers específicos.
    Funciona tanto con objetos request (HTTP) como con scope dict (WebSocket).
    
    ✅ MEJORADO: 
    - Soporte para fingerprint local (si el dispositivo lo envía directamente)
    - Incluye MAC address para mayor robustez
    
    CAPA 1: Para primera solicitud sin UDID
    
    Args:
        request_or_scope: Request object de Django (HTTP) o scope dict (WebSocket)
        
    Returns:
        str: Hash único del dispositivo (32 caracteres)
    """
    # ✅ NUEVO: Si el dispositivo envía fingerprint directamente, usarlo (más estable)
    direct_fingerprint = _get_header_value(request_or_scope, 'HTTP_X_DEVICE_FINGERPRINT')
    if direct_fingerprint and len(direct_fingerprint) == 32:
        # Validar que sea hexadecimal válido
        try:
            int(direct_fingerprint, 16)
            return direct_fingerprint  # Usar fingerprint del dispositivo
        except ValueError:
            # Si no es válido, continuar con generación normal
            pass
    
    # Extraer headers desde request o scope
    headers_dict = {
        # Factores básicos (siempre disponibles)
        'user_agent': _get_header_value(request_or_scope, 'HTTP_USER_AGENT'),
        'accept_language': _get_header_value(request_or_scope, 'HTTP_ACCEPT_LANGUAGE'),
        'accept_encoding': _get_header_value(request_or_scope, 'HTTP_ACCEPT_ENCODING'),
        'accept': _get_header_value(request_or_scope, 'HTTP_ACCEPT'),
        
        # Factores específicos de móviles/Smart TVs
        'device_id': _get_header_value(request_or_scope, 'HTTP_X_DEVICE_ID'),
        'app_version': _get_header_value(request_or_scope, 'HTTP_X_APP_VERSION'),
        'app_type': _get_header_value(request_or_scope, 'HTTP_X_APP_TYPE'),
        'os_version': _get_header_value(request_or_scope, 'HTTP_X_OS_VERSION'),
        'device_model': _get_header_value(request_or_scope, 'HTTP_X_DEVICE_MODEL'),
        'build_id': _get_header_value(request_or_scope, 'HTTP_X_BUILD_ID'),
        
        # Para Smart TVs
        'tv_serial': _get_header_value(request_or_scope, 'HTTP_X_TV_SERIAL'),
        'tv_model': _get_header_value(request_or_scope, 'HTTP_X_TV_MODEL'),
        'firmware_version': _get_header_value(request_or_scope, 'HTTP_X_FIRMWARE_VERSION'),
        
        # ✅ NUEVO: MAC address para mayor robustez
        'mac_address': _get_header_value(request_or_scope, 'HTTP_X_MAC_ADDRESS'),
    }
    
    # Construir string de fingerprint
    fingerprint_string = _build_device_fingerprint_string(headers_dict)
    
    # Generar hash SHA256 y tomar primeros 32 caracteres
    device_fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]
    
    return device_fingerprint


def check_device_fingerprint_rate_limit(device_fingerprint, max_requests=3, window_minutes=5):
    """
    Verifica el rate limit por device fingerprint.
    Versión optimizada: siempre intenta cache primero. Solo consulta BD si es absolutamente necesario.
    CAPA 1: Protege /request-udid/ (primera solicitud)
    
    Args:
        device_fingerprint: Fingerprint único del dispositivo
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    if not device_fingerprint:
        return False, 0, 0
    
    # Intentar usar cache primero (optimización)
    cache_key = f"rate_limit:device_fp:{device_fingerprint}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            # Calcular tiempo de espera
            retry_after = window_minutes * 60
            # Log de rate limit excedido
            logger.warning(
                f"Rate limit exceeded: device_fingerprint={device_fingerprint[:8]}..., "
                f"count={cached_count}, limit={max_requests}, "
                f"window={window_minutes}min, retry_after={retry_after}s"
            )
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Si no está en cache, inicializar con 0
    # Esto evita consulta a BD en primera llamada (optimización)
    cache.set(cache_key, 0, timeout=window_minutes * 60)
    return True, max_requests, 0


def check_udid_rate_limit(udid, max_requests=20, window_minutes=60):
    """
    Verifica el rate limit por UDID.
    Versión optimizada: siempre intenta cache primero. Solo consulta BD si es absolutamente necesario.
    CAPA 3: Protege /get-subscriber-info/, /authenticate-with-udid/, /validate/
    
    Args:
        udid: UDID único del dispositivo
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    if not udid:
        return False, 0, 0
    
    # Intentar usar cache primero (optimización)
    cache_key = f"rate_limit:udid:{udid}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            retry_after = window_minutes * 60
            # Log de rate limit excedido
            logger.warning(
                f"Rate limit exceeded: udid={udid[:8] if len(udid) > 8 else udid}..., "
                f"count={cached_count}, limit={max_requests}, "
                f"window={window_minutes}min, retry_after={retry_after}s"
            )
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Si no está en cache, inicializar con 0
    # Esto evita consulta a BD en primera llamada (optimización)
    cache.set(cache_key, 0, timeout=window_minutes * 60)
    return True, max_requests, 0


def check_temp_token_rate_limit(temp_token, max_requests=10, window_minutes=5):
    """
    Verifica el rate limit por temp_token.
    CAPA 2: Protege /validate-udid/
    
    Args:
        temp_token: Token temporal único
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    # Importar aquí para evitar imports circulares
    from .models import UDIDAuthRequest
    
    if not temp_token:
        return False, 0, 0
    
    # Intentar usar cache primero
    cache_key = f"rate_limit:temp_token:{temp_token}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            retry_after = window_minutes * 60
            # Log de rate limit excedido
            logger.warning(
                f"Rate limit exceeded: temp_token={temp_token[:8] if len(temp_token) > 8 else temp_token}..., "
                f"count={cached_count}, limit={max_requests}, "
                f"window={window_minutes}min, retry_after={retry_after}s"
            )
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Si no está en cache, inicializar con 0
    # Esto evita consulta a BD en primera llamada (optimización)
    cache.set(cache_key, 0, timeout=window_minutes * 60)
    return True, max_requests, 0


def check_combined_rate_limit(udid, temp_token, max_requests=10, window_minutes=5):
    """
    Verifica el rate limit combinando UDID + Temp Token.
    CAPA 4: Protege /validate-udid/ con doble verificación
    
    Args:
        udid: UDID único del dispositivo
        temp_token: Token temporal único
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int, reason: str)
    """
    if not udid or not temp_token:
        return False, 0, window_minutes * 60, "Missing UDID or temp_token"
    
    # Verificar límite por UDID
    udid_allowed, udid_remaining, udid_retry = check_udid_rate_limit(
        udid, max_requests=max_requests, window_minutes=window_minutes
    )
    
    # Verificar límite por temp_token
    token_allowed, token_remaining, token_retry = check_temp_token_rate_limit(
        temp_token, max_requests=max_requests, window_minutes=window_minutes
    )
    
    # Si alguno excede el límite, bloquear
    if not udid_allowed:
        return False, udid_remaining, udid_retry, "UDID rate limit exceeded"
    
    if not token_allowed:
        return False, token_remaining, token_retry, "Temp token rate limit exceeded"
    
    # Tomar el menor de los remaining
    min_remaining = min(udid_remaining, token_remaining)
    
    return True, min_remaining, 0, "OK"


def increment_rate_limit_counter(identifier_type, identifier):
    """
    Incrementa el contador de rate limiting en cache.
    Útil para actualizar contadores después de operaciones exitosas.
    
    Args:
        identifier_type: 'device_fp', 'udid', o 'temp_token'
        identifier: El valor del identificador
    """
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    try:
        cache.incr(cache_key)
    except ValueError:
        # Si no existe, inicializar
        cache.set(cache_key, 1, timeout=3600)  # 1 hora por defecto


def check_websocket_rate_limit(udid, device_fingerprint, max_connections=5, window_minutes=5):
    """
    Verifica rate limit para conexiones WebSocket.
    Limita conexiones simultáneas por UDID y device fingerprint.
    
    Args:
        udid: UDID único del dispositivo (puede ser None si aún no se conoce)
        device_fingerprint: Fingerprint único del dispositivo
        max_connections: Máximo de conexiones simultáneas permitidas
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_connections: int, retry_after_seconds: int)
    """
    if not device_fingerprint:
        # Si no hay fingerprint, no podemos hacer rate limiting
        # Permitir pero con límite más restrictivo
        return True, 1, 0
    
    # Limitar por device fingerprint (siempre disponible)
    cache_key_fp = f"ws_rate_limit:fp:{device_fingerprint}"
    current_connections_fp = cache.get(cache_key_fp, 0)
    
    if current_connections_fp >= max_connections:
        retry_after = window_minutes * 60
        return False, 0, retry_after
    
    # Limitar por UDID si está disponible (más específico)
    if udid:
        cache_key_udid = f"ws_rate_limit:udid:{udid}"
        current_connections_udid = cache.get(cache_key_udid, 0)
        
        if current_connections_udid >= max_connections:
            retry_after = window_minutes * 60
            return False, 0, retry_after
    
    # Si pasa ambas verificaciones, está permitido
    remaining = max_connections - max(current_connections_fp, current_connections_udid if udid else 0)
    return True, remaining, 0


def increment_websocket_connection(udid, device_fingerprint, window_minutes=5):
    """
    Incrementa el contador de conexiones WebSocket activas.
    
    Args:
        udid: UDID único del dispositivo (puede ser None)
        device_fingerprint: Fingerprint único del dispositivo
        window_minutes: Ventana de tiempo en minutos para el timeout
    """
    timeout = window_minutes * 60
    
    # Incrementar contador por device fingerprint
    cache_key_fp = f"ws_rate_limit:fp:{device_fingerprint}"
    try:
        cache.incr(cache_key_fp)
    except ValueError:
        cache.set(cache_key_fp, 1, timeout=timeout)
    else:
        # Si existe, actualizar timeout
        cache.expire(cache_key_fp, timeout)
    
    # Incrementar contador por UDID si está disponible
    if udid:
        cache_key_udid = f"ws_rate_limit:udid:{udid}"
        try:
            cache.incr(cache_key_udid)
        except ValueError:
            cache.set(cache_key_udid, 1, timeout=timeout)
        else:
            cache.expire(cache_key_udid, timeout)


def decrement_websocket_connection(udid, device_fingerprint):
    """
    Decrementa el contador de conexiones WebSocket activas.
    Se llama cuando una conexión se cierra.
    
    Args:
        udid: UDID único del dispositivo (puede ser None)
        device_fingerprint: Fingerprint único del dispositivo
    """
    # Decrementar contador por device fingerprint
    cache_key_fp = f"ws_rate_limit:fp:{device_fingerprint}"
    try:
        current = cache.get(cache_key_fp, 0)
        if current > 0:
            cache.set(cache_key_fp, current - 1)
    except Exception:
        pass  # Ignorar errores en limpieza
    
    # Decrementar contador por UDID si está disponible
    if udid:
        cache_key_udid = f"ws_rate_limit:udid:{udid}"
        try:
            current = cache.get(cache_key_udid, 0)
            if current > 0:
                cache.set(cache_key_udid, current - 1)
        except Exception:
            pass  # Ignorar errores en limpieza


def check_websocket_limits(udid, device_fingerprint, max_per_token=5, max_global=1000):
    """
    Verifica límites de WebSocket por token y global usando Redis.
    Implementa semáforo global y límite por token/UDID.
    
    Args:
        udid: UDID único del dispositivo (puede ser None)
        device_fingerprint: Fingerprint único del dispositivo
        max_per_token: Máximo de conexiones por token/UDID (default: 5)
        max_global: Máximo de conexiones globales (default: 1000)
        
    Returns:
        tuple: (is_allowed: bool, reason: str, retry_after: int)
    """
    import redis
    from django.conf import settings
    
    try:
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                # Si Redis no está disponible (circuit breaker abierto), permitir conexión (fail-open)
                logger.warning("Redis not available (circuit breaker open), allowing connection")
                return True, None, 0
        else:
            # Fallback a conexión directa
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                logger.warning("Redis not configured for WebSocket limits, allowing connection")
                return True, None, 0
            import redis
            redis_client = redis.from_url(redis_url)
        
        # Identificador del token (UDID o device_fingerprint)
        token_identifier = udid or device_fingerprint
        if not token_identifier:
            # Si no hay identificador, permitir pero con límite más restrictivo
            return True, None, 0
        
        # Límite por token/UDID
        token_key = f"ws_connections:token:{token_identifier}"
        token_count = redis_client.incr(token_key)
        if token_count == 1:
            redis_client.expire(token_key, 300)  # 5 minutos
        
        if token_count > max_per_token:
            # Revertir incremento
            redis_client.decr(token_key)
            return False, "Too many connections for this token", 60
        
        # Semáforo global
        global_key = "ws_connections:global"
        global_count = redis_client.incr(global_key)
        if global_count == 1:
            redis_client.expire(global_key, 300)  # 5 minutos
        
        if global_count > max_global:
            # Revertir ambos incrementos
            redis_client.decr(global_key)
            redis_client.decr(token_key)
            return False, "Too many global WebSocket connections", 60
        
        return True, None, 0
        
    except Exception as e:
        # Fail-open: si hay error con Redis, permitir conexión
        logger.error(f"Error checking WebSocket limits: {e}", exc_info=True)
        return True, None, 0


def check_plan_rate_limit(tenant_id, plan, window='minute'):
    """
    Verifica rate limit basado en el plan del tenant.
    
    Args:
        tenant_id: ID del tenant
        plan: Instancia del modelo Plan
        window: Ventana de tiempo ('minute', 'hour', 'day')
        
    Returns:
        tuple: (is_allowed: bool, remaining: int, retry_after: int)
    """
    import redis
    from django.conf import settings
    
    try:
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                # Si Redis no está disponible (circuit breaker abierto), permitir (fail-open)
                logger.warning("Redis not available (circuit breaker open) for plan rate limiting, allowing request")
                return True, 999999, 0
        else:
            # Fallback a conexión directa
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                logger.warning("Redis not configured for plan rate limiting, allowing request")
                return True, 999999, 0
            redis_client = redis.from_url(redis_url)
        
        # Determinar límite y ventana según el plan
        if window == 'minute':
            max_requests = plan.max_requests_per_minute
            window_seconds = 60
        elif window == 'hour':
            max_requests = plan.max_requests_per_hour
            window_seconds = 3600
        elif window == 'day':
            max_requests = plan.max_requests_per_day
            window_seconds = 86400
        else:
            # Default a minute
            max_requests = plan.max_requests_per_minute
            window_seconds = 60
        
        # Clave Redis para el rate limit del tenant
        key = f"plan_rate_limit:{tenant_id}:{window}"
        
        # Incrementar contador
        current = redis_client.incr(key)
        
        # Establecer TTL si es la primera vez
        if current == 1:
            redis_client.expire(key, window_seconds)
        
        # Verificar si excede el límite
        if current > max_requests:
            # Calcular retry_after basado en TTL restante
            ttl = redis_client.ttl(key)
            retry_after = max(1, ttl) if ttl > 0 else window_seconds
            return False, 0, retry_after
        
        remaining = max(0, max_requests - current)
        return True, remaining, 0
        
    except Exception as e:
        # Fail-open: si hay error, permitir request
        logger.error(f"Error checking plan rate limit: {e}", exc_info=True)
        return True, 999999, 0


def decrement_websocket_limits(udid, device_fingerprint):
    """
    Decrementa los contadores de límites de WebSocket (token y global).
    Se llama cuando una conexión se cierra.
    
    Args:
        udid: UDID único del dispositivo (puede ser None)
        device_fingerprint: Fingerprint único del dispositivo
    """
    import redis
    from django.conf import settings
    
    try:
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                # Si Redis no está disponible, no hacer nada (fail-open)
                return
        else:
            # Fallback a conexión directa
            redis_url = getattr(settings, 'REDIS_URL', None)
            if not redis_url:
                return
            redis_client = redis.from_url(redis_url)
        
        # Identificador del token
        token_identifier = udid or device_fingerprint
        if token_identifier:
            token_key = f"ws_connections:token:{token_identifier}"
            try:
                current = redis_client.get(token_key)
                if current and int(current) > 0:
                    redis_client.decr(token_key)
            except Exception:
                pass  # Ignorar errores en limpieza
        
        # Decrementar semáforo global
        global_key = "ws_connections:global"
        try:
            current = redis_client.get(global_key)
            if current and int(current) > 0:
                redis_client.decr(global_key)
        except Exception:
            pass  # Ignorar errores en limpieza
            
    except Exception as e:
        logger.error(f"Error decrementing WebSocket limits: {e}", exc_info=True)


def check_login_rate_limit(username, device_fingerprint, max_attempts=5, window_minutes=15):
    """
    Rate limiting para login: combina username + device fingerprint.
    Protege contra fuerza bruta sin afectar usuarios legítimos en diferentes dispositivos.
    
    Args:
        username: Nombre de usuario
        device_fingerprint: Fingerprint único del dispositivo
        max_attempts: Máximo de intentos permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_attempts: int, retry_after_seconds: int)
    """
    if not username or not device_fingerprint:
        return True, max_attempts, 0
    
    cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
    attempts = cache.get(cache_key, 0)
    
    if attempts >= max_attempts:
        retry_after = window_minutes * 60
        return False, 0, retry_after
    
    return True, max_attempts - attempts, 0


def increment_login_attempt(username, device_fingerprint, window_minutes=15):
    """
    Incrementa el contador de intentos de login fallidos.
    
    Args:
        username: Nombre de usuario
        device_fingerprint: Fingerprint único del dispositivo
        window_minutes: Ventana de tiempo en minutos para el timeout
    """
    if not username or not device_fingerprint:
        return
    
    cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
    timeout = window_minutes * 60
    
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=timeout)
    else:
        cache.expire(cache_key, timeout)


def reset_login_attempts(username, device_fingerprint):
    """
    Resetea el contador de intentos de login (cuando login es exitoso).
    
    Args:
        username: Nombre de usuario
        device_fingerprint: Fingerprint único del dispositivo
    """
    if not username or not device_fingerprint:
        return
    
    cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
    cache.delete(cache_key)


def check_register_rate_limit(device_fingerprint, max_requests=3, window_minutes=60):
    """
    Rate limiting para registro: por device fingerprint.
    Previene creación masiva de cuentas desde el mismo dispositivo.
    
    Args:
        device_fingerprint: Fingerprint único del dispositivo
        max_requests: Máximo de registros permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    if not device_fingerprint:
        return True, max_requests, 0
    
    cache_key = f"register_rate_limit:{device_fingerprint}"
    requests = cache.get(cache_key, 0)
    
    if requests >= max_requests:
        retry_after = window_minutes * 60
        return False, 0, retry_after
    
    return True, max_requests - requests, 0


def increment_register_attempt(device_fingerprint, window_minutes=60):
    """
    Incrementa el contador de intentos de registro.
    
    Args:
        device_fingerprint: Fingerprint único del dispositivo
        window_minutes: Ventana de tiempo en minutos para el timeout
    """
    if not device_fingerprint:
        return
    
    cache_key = f"register_rate_limit:{device_fingerprint}"
    timeout = window_minutes * 60
    
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=timeout)
    else:
        cache.expire(cache_key, timeout)


# ============================================================================
# RATE LIMITING ADAPTATIVO Y CIRCUIT BREAKER
# ============================================================================

def track_system_request():
    """
    Rastrea un request para monitoreo de carga del sistema.
    Debe llamarse en cada request para calcular la carga.
    """
    current_time = time.time()
    current_minute = int(current_time // 60)
    cache_key = f'system_load:minute:{current_minute}'
    
    try:
        cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=120)  # Mantener por 2 minutos


def get_system_load():
    """
    Calcula la carga actual del sistema basado en requests recientes.
    Retorna: 'normal', 'high', 'critical'
    """
    current_time = time.time()
    current_minute = int(current_time // 60)
    
    # Contar requests en último minuto (actual y anterior)
    requests_last_minute = cache.get(f'system_load:minute:{current_minute}', 0)
    requests_prev_minute = cache.get(f'system_load:minute:{current_minute - 1}', 0)
    
    # Promedio de requests en los últimos 2 minutos
    total_requests = requests_last_minute + requests_prev_minute
    
    # Thresholds (ajustables según capacidad del servidor)
    if total_requests < 500:
        load_level = 'normal'
    elif total_requests < 2000:
        load_level = 'high'
        # Log cuando la carga es alta
        logger.info(f"System load HIGH: {total_requests} requests in last 2 minutes")
    else:
        load_level = 'critical'
        # Log crítico cuando la carga es crítica
        logger.warning(
            f"System load CRITICAL: {total_requests} requests in last 2 minutes, "
            f"requests_last_minute={requests_last_minute}, requests_prev_minute={requests_prev_minute}"
        )
    
    return load_level


def check_circuit_breaker():
    """
    Verifica si el circuit breaker está activo.
    
    Returns:
        tuple: (is_active: bool, retry_after_seconds: float)
    """
    breaker_state = cache.get('circuit_breaker:state', 'closed')
    breaker_until = cache.get('circuit_breaker:until', 0)
    
    if breaker_state == 'open':
        if time.time() < breaker_until:
            retry_after = int(breaker_until - time.time())
            # Log cuando circuit breaker está bloqueando requests
            logger.debug(f"Circuit breaker OPEN: blocking requests, retry_after={retry_after}s")
            return True, retry_after
        else:
            # Intentar cerrar (half-open)
            cache.set('circuit_breaker:state', 'half-open', timeout=60)
            cache.delete('circuit_breaker:until')
            logger.info("Circuit breaker transitioned to HALF-OPEN state (testing recovery)")
            return False, 0
    
    return False, 0


def activate_circuit_breaker(duration_seconds=60):
    """
    Activa el circuit breaker por un tiempo determinado.
    
    Args:
        duration_seconds: Duración del circuit breaker en segundos
    """
    until_time = time.time() + duration_seconds
    cache.set('circuit_breaker:state', 'open', timeout=duration_seconds)
    cache.set('circuit_breaker:until', until_time, timeout=duration_seconds)
    
    # Log crítico de activación de circuit breaker
    logger.critical(
        f"Circuit breaker ACTIVATED: duration={duration_seconds}s, "
        f"until={timezone.now() + timedelta(seconds=duration_seconds)}"
    )


def is_legitimate_reconnection(udid):
    """
    Determina si un request es una reconexión legítima.
    UDID válido y previamente usado = reconexión legítima.
    
    Args:
        udid: UDID a verificar
        
    Returns:
        bool: True si es reconexión legítima
    """
    from .models import UDIDAuthRequest
    
    if not udid:
        return False
    
    try:
        req = UDIDAuthRequest.objects.get(udid=udid)
        # Es reconexión si:
        # - UDID existe
        # - Está validado o usado previamente
        # - No ha expirado (o expiró recientemente, < 1 hora)
        if req.status in ['validated', 'used']:
            # Si está validado o usado, es reconexión legítima
            return True
        elif req.status == 'pending' and req.is_expired():
            # Si expiró hace menos de 1 hora, considerar reconexión legítima
            # (puede ser reconexión después de corte de luz)
            time_since_expiry = timezone.now() - req.expires_at
            if time_since_expiry.total_seconds() < 3600:  # 1 hora
                return True
    except UDIDAuthRequest.DoesNotExist:
        pass
    
    return False


def check_adaptive_rate_limit(identifier_type, identifier, is_reconnection=False, 
                             base_max_requests=None, base_window_minutes=None):
    """
    Rate limiting adaptativo que ajusta límites según carga del sistema
    y si es una reconexión legítima.
    
    Args:
        identifier_type: 'udid', 'device_fp', etc.
        identifier: El valor del identificador
        is_reconnection: True si es reconexión de UDID válido existente
        base_max_requests: Límite base de requests (si None, usa defaults)
        base_window_minutes: Ventana base en minutos (si None, usa defaults)
        
    Returns:
        tuple: (is_allowed: bool, remaining: int, retry_after: int, reason: str)
    """
    # Rastrear request para monitoreo
    track_system_request()
    
    # Verificar circuit breaker
    breaker_active, breaker_retry_after = check_circuit_breaker()
    if breaker_active and not is_reconnection:
        # En circuit breaker, solo permitir reconexiones
        return False, 0, breaker_retry_after, "Circuit breaker active"
    
    # Obtener carga del sistema
    system_load = get_system_load()
    
    # Determinar límites base si no se proporcionaron
    if base_max_requests is None:
        if identifier_type == 'udid':
            base_max_requests = 5
            base_window_minutes = 60
        elif identifier_type == 'device_fp':
            base_max_requests = 2
            base_window_minutes = 10
        else:
            base_max_requests = 3
            base_window_minutes = 5
    
    if base_window_minutes is None:
        base_window_minutes = 5
    
    # Determinar límites según carga y tipo de request
    if is_reconnection:
        # Reconexiones legítimas: límites más permisivos
        if system_load == 'normal':
            max_requests = base_max_requests * 2  # Doble para reconexiones
            window_minutes = base_window_minutes
        elif system_load == 'high':
            max_requests = base_max_requests * 5  # Muy permisivo durante alta carga
            window_minutes = base_window_minutes
        else:  # critical
            max_requests = base_max_requests * 10  # Muy permisivo, pero con circuit breaker
            window_minutes = base_window_minutes * 2
    else:
        # Nuevas solicitudes: límites más restrictivos
        if system_load == 'normal':
            max_requests = base_max_requests
            window_minutes = base_window_minutes
        elif system_load == 'high':
            max_requests = max(1, base_max_requests // 2)  # Más restrictivo
            window_minutes = base_window_minutes * 2
        else:  # critical
            max_requests = max(1, base_max_requests // 3)  # Muy restrictivo
            window_minutes = base_window_minutes * 3
    
    # Verificar límite estándar
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    current_count = cache.get(cache_key, 0)
    
    if current_count >= max_requests:
        # Si es reconexión y carga crítica, considerar activar circuit breaker
        if is_reconnection and system_load == 'critical' and not breaker_active:
            # Solo activar si hay muchas violaciones
            violation_key = f"rate_limit_violations:{identifier_type}:{identifier}"
            violations = cache.get(violation_key, 0)
            violations += 1
            cache.set(violation_key, violations, timeout=3600)
            
            # Si hay muchas violaciones de reconexión, puede ser ataque
            if violations > 10:
                activate_circuit_breaker(duration_seconds=30)
        
        retry_after = window_minutes * 60
        # Log de rate limit excedido en rate limiting adaptativo
        logger.warning(
            f"Adaptive rate limit exceeded: type={identifier_type}, "
            f"identifier={str(identifier)[:8] if len(str(identifier)) > 8 else identifier}..., "
            f"count={current_count}, limit={max_requests}, "
            f"is_reconnection={is_reconnection}, system_load={system_load}, "
            f"window={window_minutes}min, retry_after={retry_after}s"
        )
        return False, 0, retry_after, "Rate limit exceeded"
    
    return True, max_requests - current_count, 0, "OK"


# ============================================================================
# EXPONENTIAL BACKOFF CON JITTER -
# ============================================================================

def calculate_retry_delay(attempt_number, base_delay=1, max_delay=60, jitter=True):
    """
    Calcula delay para retry con exponential backoff y jitter.
    
    Args:
        attempt_number: Número de intento (1, 2, 3, ...)
        base_delay: Delay base en segundos
        max_delay: Delay máximo en segundos
        jitter: Si True, agrega aleatoriedad para evitar sincronización
    
    Returns:
        delay en segundos (entero)
    """
    # Exponential backoff: base_delay * 2^(attempt_number - 1)
    exponential_delay = base_delay * (2 ** (attempt_number - 1))
    
    # Capar al máximo
    delay = min(exponential_delay, max_delay)
    
    # Agregar jitter aleatorio (±30% del delay)
    if jitter:
        jitter_amount = delay * 0.3
        delay = delay + random.uniform(-jitter_amount, jitter_amount)
        delay = max(0.5, delay)  # Mínimo 0.5 segundos
    
    return int(math.ceil(delay))


def get_retry_info(udid, action_type='reconnection'):
    """
    Obtiene información de retry para un UDID.
    Si es primera vez, retorna delay 0 (inmediato).
    Si ya hubo intentos, retorna delay calculado con exponential backoff.
    
    Args:
        udid: UDID del dispositivo
        action_type: Tipo de acción ('reconnection', 'authentication', etc.)
        
    Returns:
        tuple: (delay_seconds: int, attempt_number: int)
    """
    if not udid:
        return 0, 1
    
    cache_key = f"retry_info:{action_type}:{udid}"
    retry_data = cache.get(cache_key)
    
    if retry_data is None:
        retry_data = {'attempts': 0, 'last_attempt': 0}
    
    attempts = retry_data.get('attempts', 0)
    last_attempt = retry_data.get('last_attempt', 0)
    current_time = time.time()
    
    # Si pasó más de 5 minutos desde último intento, resetear
    if current_time - last_attempt > 300:
        attempts = 0
    
    if attempts == 0:
        # Primera vez: intento inmediato
        delay = 0
    else:
        # Calcular delay con exponential backoff
        # Para reconexiones: base_delay=1, max_delay=30
        # Para otras acciones: base_delay=2, max_delay=60
        if action_type == 'reconnection':
            delay = calculate_retry_delay(attempts, base_delay=1, max_delay=30, jitter=True)
        else:
            delay = calculate_retry_delay(attempts, base_delay=2, max_delay=60, jitter=True)
    
    # Incrementar contador antes de guardar
    attempts += 1
    retry_data['attempts'] = attempts
    retry_data['last_attempt'] = current_time
    cache.set(cache_key, retry_data, timeout=600)  # 10 minutos
    
    return delay, attempts


def reset_retry_info(udid, action_type='reconnection'):
    """
    Resetea información de retry (cuando reconexión/acción es exitosa).
    
    Args:
        udid: UDID del dispositivo
        action_type: Tipo de acción ('reconnection', 'authentication', etc.)
    """
    if not udid:
        return
    
    cache_key = f"retry_info:{action_type}:{udid}"
    cache.delete(cache_key)


def should_apply_retry_delay(udid, action_type='reconnection', system_load=None):
    """
    Determina si se debe aplicar un delay de retry basado en:
    - Intentos previos del dispositivo
    - Carga del sistema
    
    Args:
        udid: UDID del dispositivo
        action_type: Tipo de acción
        system_load: Carga del sistema ('normal', 'high', 'critical'). Si None, se calcula.
        
    Returns:
        tuple: (should_delay: bool, delay_seconds: int, attempt_number: int)
    """
    if not udid:
        return False, 0, 0
    
    # Obtener carga del sistema si no se proporciona
    if system_load is None:
        system_load = get_system_load()
    
    # Obtener información de retry
    retry_delay, attempt_number = get_retry_info(udid, action_type)
    
    # Si hay delay calculado, aplicarlo
    if retry_delay > 0:
        # Ajustar delay según carga del sistema
        if system_load == 'critical':
            # En carga crítica, aumentar delay para distribuir mejor
            retry_delay = int(retry_delay * 1.5)
        elif system_load == 'high':
            # En carga alta, aumentar ligeramente
            retry_delay = int(retry_delay * 1.2)
        
        return True, retry_delay, attempt_number
    
    # Si no hay delay pero la carga es crítica y hay múltiples intentos
    if system_load == 'critical' and attempt_number > 1:
        # Aplicar delay mínimo incluso en primera reconexión durante carga crítica
        retry_delay = calculate_retry_delay(1, base_delay=1, max_delay=5, jitter=True)
        return True, retry_delay, attempt_number
    
    return False, 0, attempt_number


# ============================================================================
# EXPONENTIAL BACKOFF PROGRESIVO PARA RATE LIMITING -
# ============================================================================

def check_rate_limit_with_backoff(identifier_type, identifier, base_max_requests=10, 
                                  window_minutes=5, max_backoff_hours=24):
    """
    Rate limiting con exponential backoff progresivo.
    Si se excede el límite múltiples veces, aumenta el tiempo de bloqueo progresivamente.
    Útil para detectar y bloquear ataques más agresivos.
    
    Args:
        identifier_type: 'udid', 'device_fp', 'temp_token', etc.
        identifier: El valor del identificador
        base_max_requests: Límite base de requests
        window_minutes: Ventana de tiempo en minutos para el límite base
        max_backoff_hours: Máximo de horas de backoff (24 por defecto)
        
    Returns:
        tuple: (is_allowed: bool, remaining: int, retry_after: int)
    """
    if not identifier:
        return False, 0, 0
    
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    backoff_key = f"rate_limit_backoff:{identifier_type}:{identifier}"
    violation_count_key = f"rate_limit_violations:{identifier_type}:{identifier}"
    
    # Verificar si está en backoff (bloqueo progresivo activo)
    backoff_until = cache.get(backoff_key)
    if backoff_until:
        current_time = time.time()
        if current_time < backoff_until:
            remaining_seconds = int(backoff_until - current_time)
            return False, 0, remaining_seconds
        else:
            # El período de backoff expiró, resetear
            cache.delete(backoff_key)
            # Opcional: resetear contador de violaciones después de un período largo
            # (mantener por 24 horas para tracking)
    
    # Verificar rate limit normal
    current_count = cache.get(cache_key, 0)
    
    if current_count >= base_max_requests:
        # Límite excedido: incrementar contador de violaciones
        violations = cache.get(violation_count_key, 0)
        violations += 1
        
        # Exponential backoff progresivo: 5min, 15min, 1h, 4h, 24h
        # Fórmula: 5 * (3 ^ min(violations - 1, 3)) minutos
        # violations=1: 5 minutos
        # violations=2: 15 minutos (5 * 3^1)
        # violations=3: 45 minutos (5 * 3^2)
        # violations=4+: 135 minutos (5 * 3^3) pero capado a max_backoff_hours
        backoff_multiplier = min(violations - 1, 3)
        backoff_minutes = min(5 * (3 ** backoff_multiplier), max_backoff_hours * 60)
        backoff_until = time.time() + (backoff_minutes * 60)
        
        # Guardar backoff y contador de violaciones
        cache.set(backoff_key, backoff_until, timeout=max_backoff_hours * 3600)
        cache.set(violation_count_key, violations, timeout=max_backoff_hours * 3600)
        
        # Log de backoff progresivo aplicado
        logger.warning(
            f"Progressive backoff applied: type={identifier_type}, "
            f"identifier={str(identifier)[:8] if len(str(identifier)) > 8 else identifier}..., "
            f"violations={violations}, backoff_minutes={backoff_minutes}, "
            f"backoff_until={timezone.now() + timedelta(minutes=backoff_minutes)}"
        )
        
        return False, 0, backoff_minutes * 60
    
    # Si no se excedió el límite, el contador de violaciones se mantiene para tracking
    # pero no se aplica backoff adicional
    
    return True, base_max_requests - current_count, 0


def reset_rate_limit_backoff(identifier_type, identifier):
    """
    Resetea el backoff progresivo para un identificador.
    Útil cuando se confirma que el dispositivo es legítimo.
    
    Args:
        identifier_type: 'udid', 'device_fp', 'temp_token', etc.
        identifier: El valor del identificador
    """
    if not identifier:
        return
    
    backoff_key = f"rate_limit_backoff:{identifier_type}:{identifier}"
    violation_count_key = f"rate_limit_violations:{identifier_type}:{identifier}"
    
    cache.delete(backoff_key)
    cache.delete(violation_count_key)


# ============================================================================
# SEMÁFORO GLOBAL DE CONCURRENCIA
# ============================================================================

def _get_dynamic_timeout():
    """
    Calcula timeout dinámico basado en latencia p95.
    Retorna p95 × 1.5 para evitar liberar slots prematuramente.
    
    Si las métricas no están disponibles, usa un valor por defecto.
    """
    try:
        # Intentar obtener métricas del sistema
        # TODO: Reemplazar con métricas reales cuando se implemente el dashboard
        from django.conf import settings
        
        # Por ahora, usar un valor por defecto basado en configuración
        # Cuando se implemente el dashboard de métricas, usar:
        # from udid.utils.metrics import _metrics
        # metrics = _metrics.get_metrics()
        # p95_ms = metrics.get('p95', 2000)  # Default 2 segundos
        
        # Valor por defecto: 30 segundos (se ajustará cuando tengamos métricas)
        default_p95_ms = 2000  # 2 segundos
        p95_ms = default_p95_ms
        
        # Convertir a segundos y multiplicar por 1.5
        timeout = int((p95_ms / 1000) * 1.5)
        # Mínimo 10 segundos, máximo 60 segundos
        return max(10, min(60, timeout))
    except Exception as e:
        logger.warning(f"Error calculating dynamic timeout, using default: {e}")
        return 30  # Valor por defecto seguro


def _count_slots_scan(redis_client, pattern):
    """
    Cuenta slots usando SCAN en lugar de KEYS para evitar bloqueos.
    SCAN es O(1) por iteración vs KEYS que es O(n).
    
    Args:
        redis_client: Cliente Redis
        pattern: Patrón de búsqueda (ej: "global_semaphore:slots:*")
        
    Returns:
        int: Número de slots encontrados
    """
    count = 0
    cursor = 0
    
    try:
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            count += len(keys)
            if cursor == 0:
                break
    except Exception as e:
        logger.error(f"Error counting slots with SCAN: {e}")
        # Fallback: intentar con KEYS solo si SCAN falla (no recomendado en producción)
        try:
            keys = redis_client.keys(pattern)
            count = len(keys)
        except Exception as e2:
            logger.error(f"Error with KEYS fallback: {e2}")
            count = 0
    
    return count


def acquire_global_semaphore(timeout=None, max_slots=None):
    """
    Adquiere un slot en el semáforo global usando Redis.
    Retorna (acquired: bool, slot_id: str, retry_after: int)
    
    Args:
        timeout: TTL del slot en segundos. Si es None, se calcula dinámicamente.
        max_slots: Máximo número de slots simultáneos. Si es None, usa configuración.
        
    Returns:
        tuple: (acquired: bool, slot_id: str or None, retry_after: int)
    """
    try:
        import redis
        from django.conf import settings
        
        # Obtener configuración
        if max_slots is None:
            max_slots = getattr(settings, 'GLOBAL_SEMAPHORE_SLOTS', 500)
        
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                # Si Redis no está disponible (circuit breaker abierto), permitir (fail-open)
                logger.warning("Redis not available (circuit breaker open), semaphore disabled")
                return True, None, 0
        else:
            # Fallback a conexión directa
            if not hasattr(settings, 'REDIS_URL') or not settings.REDIS_URL:
                logger.warning("REDIS_URL not configured, semaphore disabled")
                return True, None, 0  # Permitir si Redis no está configurado
            redis_client = redis.from_url(settings.REDIS_URL)
        semaphore_key = "global_semaphore:slots"
        slot_id = str(uuid.uuid4())
        
        # Calcular timeout dinámico si no se proporciona
        if timeout is None:
            timeout = _get_dynamic_timeout()
        
        # Contar slots ocupados usando SCAN (más eficiente que KEYS)
        pattern = f"{semaphore_key}:*"
        current_slots = _count_slots_scan(redis_client, pattern)
        
        if current_slots >= max_slots:
            # Calcular retry_after basado en TTL promedio
            # Estimar tiempo de espera basado en timeout dinámico
            retry_after = max(1, timeout // 6)  # 1/6 del timeout como mínimo
            logger.warning(
                f"Global semaphore full: {current_slots}/{max_slots} slots, "
                f"retry_after={retry_after}s"
            )
            return False, None, retry_after
        
        # Usar SET con NX y EX para operación atómica
        acquired = redis_client.set(
            f"{semaphore_key}:{slot_id}",
            "1",
            nx=True,
            ex=timeout
        )
        
        if not acquired:
            # Si falla, recalcular slots (puede haber cambiado)
            current_slots = _count_slots_scan(redis_client, pattern)
            if current_slots >= max_slots:
                retry_after = max(1, timeout // 6)
                return False, None, retry_after
            # Si no está lleno, reintentar (puede ser race condition)
            acquired = redis_client.set(
                f"{semaphore_key}:{slot_id}",
                "1",
                nx=True,
                ex=timeout
            )
            if not acquired:
                logger.warning(f"Failed to acquire semaphore slot after retry: {slot_id}")
                return False, None, 1
        
        logger.debug(f"Acquired semaphore slot: {slot_id}, current_slots={current_slots + 1}/{max_slots}")
        return True, slot_id, 0
        
    except Exception as e:
        logger.error(f"Error acquiring global semaphore: {e}", exc_info=True)
        # En caso de error, permitir el request (fail-open)
        # Esto evita que un fallo de Redis bloquee todo el sistema
        return True, None, 0


def release_global_semaphore(slot_id):
    """
    Libera un slot del semáforo global.
    
    Args:
        slot_id: ID del slot a liberar
    """
    if not slot_id:
        return
    
    try:
        import redis
        from django.conf import settings
        
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                # Si Redis no está disponible, no hacer nada (fail-open)
                return
        else:
            # Fallback a conexión directa
            if not hasattr(settings, 'REDIS_URL') or not settings.REDIS_URL:
                return  # Redis no configurado, no hacer nada
            redis_client = redis.from_url(settings.REDIS_URL)
        semaphore_key = "global_semaphore:slots"
        
        deleted = redis_client.delete(f"{semaphore_key}:{slot_id}")
        if deleted:
            logger.debug(f"Released semaphore slot: {slot_id}")
        else:
            logger.debug(f"Semaphore slot already released or expired: {slot_id}")
            
    except Exception as e:
        logger.error(f"Error releasing global semaphore slot {slot_id}: {e}", exc_info=True)
        # No lanzar excepción, solo loggear el error


# ============================================================================
# TOKEN BUCKET RATE LIMITING CON LUA
# ============================================================================

# Singleton para el script Lua (se registra una sola vez)
_token_bucket_script = None

def _get_token_bucket_script():
    """
    Obtiene el script Lua registrado (singleton).
    Se registra una sola vez para reducir overhead.
    
    Returns:
        Script registrado de Redis
    """
    global _token_bucket_script
    
    if _token_bucket_script is None:
        import redis
        from django.conf import settings
        import os
        
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                logger.error("Redis not available (circuit breaker open), cannot load token bucket script")
                return None
        else:
            # Fallback a conexión directa
            if not hasattr(settings, 'REDIS_URL') or not settings.REDIS_URL:
                logger.error("REDIS_URL not configured, cannot load token bucket script")
                return None
            redis_client = redis.from_url(settings.REDIS_URL)
        
        # Cargar script Lua una sola vez
        script_path = os.path.join(
            os.path.dirname(__file__),
            'scripts',
            'token_bucket.lua'
        )
        
        try:
            with open(script_path, 'r') as f:
                lua_script = f.read()
            
            # Registrar script (persistente en redis_client)
            _token_bucket_script = redis_client.register_script(lua_script)
            logger.info("Token bucket Lua script loaded successfully")
        except FileNotFoundError:
            logger.error(f"Token bucket script not found at {script_path}")
            return None
        except Exception as e:
            logger.error(f"Error loading token bucket script: {e}", exc_info=True)
            return None
    
    return _token_bucket_script


def check_token_bucket_lua(identifier, capacity=10, refill_rate=1, 
                          window_seconds=60, tokens_requested=1):
    """
    Verifica rate limit usando token bucket atómico en Lua.
    Retorna (is_allowed: bool, remaining: int, retry_after: int)
    
    El script se registra una sola vez (singleton) para mejorar rendimiento.
    Las operaciones son atómicas, evitando race conditions.
    
    Args:
        identifier: Identificador único (UDID, token, etc.)
        capacity: Capacidad máxima del bucket (tokens)
        refill_rate: Tasa de reposición de tokens por segundo
        window_seconds: Ventana de tiempo en segundos
        tokens_requested: Número de tokens solicitados (default: 1)
        
    Returns:
        tuple: (is_allowed: bool, remaining: int, retry_after: int)
    """
    try:
        import redis
        from django.conf import settings
        
        if not identifier:
            return False, 0, 0
        
        # Usar cliente Redis con HA si está disponible
        if REDIS_HA_AVAILABLE:
            redis_client = get_redis_client_safe()
            if not redis_client:
                logger.warning("Redis not available (circuit breaker open), allowing request (fail-open)")
                return True, capacity, 0
        else:
            # Fallback a conexión directa
            if not hasattr(settings, 'REDIS_URL') or not settings.REDIS_URL:
                logger.warning("REDIS_URL not configured, allowing request (fail-open)")
                return True, capacity, 0
            redis_client = redis.from_url(settings.REDIS_URL)
        key = f"token_bucket:{identifier}"
        
        # Obtener script registrado (singleton)
        script = _get_token_bucket_script()
        if script is None:
            logger.warning("Token bucket script not available, allowing request (fail-open)")
            return True, capacity, 0
        
        # Ejecutar script atómicamente
        result = script(
            keys=[key],
            args=[capacity, refill_rate, tokens_requested, int(time.time()), window_seconds],
            client=redis_client
        )
        
        # Resultado: [allowed, remaining] o [denied, remaining, retry_after]
        if result[0] == 1:
            # Permitido
            return True, int(result[1]), 0
        else:
            # Denegado
            remaining = int(result[1]) if len(result) > 1 else 0
            retry_after = int(result[2]) if len(result) > 2 else window_seconds
            return False, remaining, retry_after
            
    except Exception as e:
        logger.error(f"Error checking token bucket for {identifier}: {e}", exc_info=True)
        # Fail-open: permitir request en caso de error
        return True, capacity, 0


def get_client_token(request):
    """
    Obtiene token del cliente desde header X-Client-Token o UDID.
    
    Args:
        request: Request object de Django
        
    Returns:
        str: Token del cliente o None
    """
    # Intentar obtener desde header X-Client-Token
    token = request.META.get('HTTP_X_CLIENT_TOKEN')
    
    if not token:
        # Fallback a UDID si está disponible
        if hasattr(request, 'data') and request.data:
            token = request.data.get('udid')
        if not token and hasattr(request, 'query_params') and request.query_params:
            token = request.query_params.get('udid')
    
    return token