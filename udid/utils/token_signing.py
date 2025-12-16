"""
Utilidades para generación y verificación de API keys firmadas.
Implementa tokens firmados con HMAC-SHA256 para autenticación segura.
"""
import hmac
import hashlib
import time
import json
import base64
import secrets
import logging
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def generate_api_key(tenant_id, plan_id, key_length=64):
    """
    Genera una API key única y firmada.
    
    Args:
        tenant_id: ID del tenant
        plan_id: ID del plan
        key_length: Longitud de la parte aleatoria de la key (default: 64)
        
    Returns:
        str: API key en formato base64.signature
    """
    # Generar parte aleatoria única
    random_part = secrets.token_urlsafe(key_length)
    
    # Crear payload con metadatos
    payload = {
        'tenant_id': tenant_id,
        'plan_id': plan_id,
        'timestamp': int(time.time()),
        'random': random_part
    }
    
    # Codificar payload en base64
    payload_json = json.dumps(payload, sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    
    # Generar firma HMAC-SHA256
    secret_key = getattr(settings, 'SECRET_KEY', '')
    if not secret_key:
        raise ValueError("SECRET_KEY no está configurado en settings")
    
    signature = hmac.new(
        secret_key.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # API key en formato: payload.signature
    api_key = f"{payload_b64}.{signature}"
    
    return api_key


def verify_api_key(api_key):
    """
    Verifica y decodifica una API key.
    
    Args:
        api_key: API key a verificar (formato: payload.signature)
        
    Returns:
        dict: Payload decodificado si es válido, None si es inválido
    """
    if not api_key:
        return None
    
    try:
        # Separar payload y firma
        parts = api_key.split('.')
        if len(parts) != 2:
            logger.warning("API key con formato inválido (no tiene 2 partes)")
            return None
        
        payload_b64, signature = parts
        
        # Verificar firma
        secret_key = getattr(settings, 'SECRET_KEY', '')
        if not secret_key:
            logger.error("SECRET_KEY no está configurado en settings")
            return None
        
        expected_signature = hmac.new(
            secret_key.encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Comparación segura de firmas (timing-safe)
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("API key con firma inválida")
            return None
        
        # Decodificar payload
        payload_json = base64.urlsafe_b64decode(payload_b64.encode()).decode()
        payload = json.loads(payload_json)
        
        return payload
        
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Error decodificando API key: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado verificando API key: {e}", exc_info=True)
        return None


def generate_simple_api_key():
    """
    Genera una API key simple (solo aleatoria, sin firma).
    Útil para almacenar en BD y luego verificar contra el hash.
    
    Returns:
        str: API key aleatoria de 64 caracteres
    """
    return secrets.token_urlsafe(64)


def hash_api_key(api_key):
    """
    Genera un hash de una API key para almacenamiento seguro.
    Usa SHA-256 para hashear la key antes de almacenarla.
    
    Args:
        api_key: API key a hashear
        
    Returns:
        str: Hash SHA-256 de la key
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key_hash(api_key, stored_hash):
    """
    Verifica una API key contra un hash almacenado.
    
    Args:
        api_key: API key a verificar
        stored_hash: Hash almacenado en BD
        
    Returns:
        bool: True si la key coincide con el hash
    """
    computed_hash = hash_api_key(api_key)
    return hmac.compare_digest(computed_hash, stored_hash)

