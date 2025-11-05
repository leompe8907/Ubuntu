import hashlib
import json
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache

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
# RATE LIMITING MULTICAPA - Fase 1
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
    
    Args:
        headers_dict: Diccionario con todos los headers necesarios
        
    Returns:
        str: String para hashear
    """
    app_type = headers_dict.get('app_type', '')
    
    # Combinar factores según el tipo de app para mayor robustez
    if app_type in ['android_tv', 'samsung_tv', 'lg_tv', 'set_top_box']:
        # Smart TV: usar serial, model, firmware (más difícil de falsificar)
        fingerprint_string = (
            f"{app_type}|{headers_dict.get('tv_serial', '')}|"
            f"{headers_dict.get('tv_model', '')}|{headers_dict.get('firmware_version', '')}|"
            f"{headers_dict.get('device_id', '')}|{headers_dict.get('app_version', '')}|"
            f"{headers_dict.get('user_agent', '')}"
        )
    elif app_type in ['android_mobile', 'ios_mobile', 'mobile_app']:
        # Móvil: usar device_id, build_id, model, os_version (identificadores nativos)
        fingerprint_string = (
            f"{app_type}|{headers_dict.get('device_id', '')}|"
            f"{headers_dict.get('build_id', '')}|{headers_dict.get('device_model', '')}|"
            f"{headers_dict.get('os_version', '')}|{headers_dict.get('app_version', '')}|"
            f"{headers_dict.get('user_agent', '')}"
        )
    else:
        # Fallback: usar headers básicos + app_type si está disponible
        fingerprint_string = (
            f"{headers_dict.get('user_agent', '')}|"
            f"{headers_dict.get('accept_language', '')}|"
            f"{headers_dict.get('accept_encoding', '')}|"
            f"{headers_dict.get('accept', '')}|{app_type}|"
            f"{headers_dict.get('app_version', '')}|{headers_dict.get('device_id', '')}"
        )
    
    return fingerprint_string


def generate_device_fingerprint(request_or_scope):
    """
    Genera un fingerprint único del dispositivo basado en características del request/scope.
    Mejorado para móviles y Smart TVs con headers específicos.
    Funciona tanto con objetos request (HTTP) como con scope dict (WebSocket).
    
    CAPA 1: Para primera solicitud sin UDID
    
    Args:
        request_or_scope: Request object de Django (HTTP) o scope dict (WebSocket)
        
    Returns:
        str: Hash único del dispositivo (32 caracteres)
    """
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
    }
    
    # Construir string de fingerprint
    fingerprint_string = _build_device_fingerprint_string(headers_dict)
    
    # Generar hash SHA256 y tomar primeros 32 caracteres
    device_fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]
    
    return device_fingerprint


def check_device_fingerprint_rate_limit(device_fingerprint, max_requests=3, window_minutes=5):
    """
    Verifica el rate limit por device fingerprint.
    CAPA 1: Protege /request-udid/ (primera solicitud)
    
    Args:
        device_fingerprint: Fingerprint único del dispositivo
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    # Importar aquí para evitar imports circulares
    from .models import UDIDAuthRequest
    
    # Intentar usar cache primero (más rápido)
    cache_key = f"rate_limit:device_fp:{device_fingerprint}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            # Calcular tiempo de espera
            retry_after = window_minutes * 60
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Si no está en cache, consultar base de datos
    time_threshold = timezone.now() - timedelta(minutes=window_minutes)
    recent_count = UDIDAuthRequest.objects.filter(
        device_fingerprint=device_fingerprint,
        created_at__gte=time_threshold
    ).count()
    
    # Actualizar cache
    cache.set(cache_key, recent_count, timeout=window_minutes * 60)
    
    remaining = max(0, max_requests - recent_count)
    if recent_count >= max_requests:
        retry_after = window_minutes * 60
        return False, remaining, retry_after
    
    return True, remaining, 0


def check_udid_rate_limit(udid, max_requests=20, window_minutes=60):
    """
    Verifica el rate limit por UDID.
    CAPA 3: Protege /get-subscriber-info/, /authenticate-with-udid/, /validate/
    
    Args:
        udid: UDID único del dispositivo
        max_requests: Máximo de requests permitidos
        window_minutes: Ventana de tiempo en minutos
        
    Returns:
        tuple: (is_allowed: bool, remaining_requests: int, retry_after_seconds: int)
    """
    # Importar aquí para evitar imports circulares
    from .models import UDIDAuthRequest, AuthAuditLog
    
    if not udid:
        return False, 0, 0
    
    # Intentar usar cache primero
    cache_key = f"rate_limit:udid:{udid}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            retry_after = window_minutes * 60
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Consultar base de datos
    time_threshold = timezone.now() - timedelta(minutes=window_minutes)
    
    # Contar en UDIDAuthRequest
    udid_count = UDIDAuthRequest.objects.filter(
        udid=udid,
        created_at__gte=time_threshold
    ).count()
    
    # Contar en AuthAuditLog (para operaciones que no crean UDIDAuthRequest)
    audit_count = AuthAuditLog.objects.filter(
        udid=udid,
        timestamp__gte=time_threshold
    ).count()
    
    total_count = udid_count + audit_count
    
    # Actualizar cache
    cache.set(cache_key, total_count, timeout=window_minutes * 60)
    
    remaining = max(0, max_requests - total_count)
    if total_count >= max_requests:
        retry_after = window_minutes * 60
        return False, remaining, retry_after
    
    return True, remaining, 0


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
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Consultar base de datos
    time_threshold = timezone.now() - timedelta(minutes=window_minutes)
    recent_count = UDIDAuthRequest.objects.filter(
        temp_token=temp_token,
        created_at__gte=time_threshold
    ).exclude(
        validated_at__isnull=True
    ).count()
    
    # Actualizar cache
    cache.set(cache_key, recent_count, timeout=window_minutes * 60)
    
    remaining = max(0, max_requests - recent_count)
    if recent_count >= max_requests:
        retry_after = window_minutes * 60
        return False, remaining, retry_after
    
    return True, remaining, 0


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