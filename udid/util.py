import hashlib
import json
from django.utils import timezone

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