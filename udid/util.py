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

def generate_device_fingerprint(request):
    """
    Genera un fingerprint único del dispositivo basado en características del request.
    CAPA 1: Para primera solicitud sin UDID
    
    Args:
        request: Request object de Django
        
    Returns:
        str: Hash único del dispositivo (32 caracteres)
    """
    # Combinar múltiples características del dispositivo
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    accept_language = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
    accept_encoding = request.META.get('HTTP_ACCEPT_ENCODING', '')
    accept = request.META.get('HTTP_ACCEPT', '')
    
    # Crear string único combinando características
    fingerprint_string = f"{user_agent}|{accept_language}|{accept_encoding}|{accept}"
    
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