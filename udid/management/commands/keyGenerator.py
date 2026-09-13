from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from udid.models import AppCredentials
import os
import base64
import json

def generate_rsa_key_pair(key_size=2048):
    """
    Genera un par de claves RSA
    Returns: (private_key_pem, public_key_pem)
    """
    # Generar clave privada
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )
    
    # Extraer clave pública
    public_key = private_key.public_key()
    
    # Serializar clave privada a PEM
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')
    
    # Serializar clave pública a PEM
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    return private_pem, public_pem

def rsa_encrypt_for_app(plaintext: str, app_credentials: AppCredentials) -> str:
    """
    Encripta datos usando la clave PRIVADA del backend
    La app usará la clave PÚBLICA para desencriptar

    app_credentials: instancia de AppCredentials ya resuelta por el llamador
    (filtrada por app_type + app_version, activa y no comprometida). No se
    vuelve a resolver aquí por app_type para evitar que la clave usada diverja
    de la que el llamador ya validó (AppCredentials.unique_together permite
    varias app_version activas por app_type, así que resolver solo por
    app_type es ambiguo).
    """
    try:
        # ✅ CORRECCIÓN: Usar clave PRIVADA para encriptar
        private_key = serialization.load_pem_private_key(
            app_credentials.private_key_pem.encode(),
            password=None,
            backend=default_backend()
        )
        
        # Encriptar con clave privada
        encrypted = private_key.private_key().encrypt(
            plaintext.encode(),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Retornar como base64 para mejor compatibilidad
        import base64
        return base64.b64encode(encrypted).decode('utf-8')

    except Exception as e:
        raise Exception(f"❌ Error de encriptación: {str(e)}")

def hybrid_encrypt_for_app(plaintext: str, app_credentials: AppCredentials) -> dict:
    """
    🔐 ENCRIPTACIÓN HÍBRIDA SEGURA:
    1. Genera clave AES aleatoria
    2. Encripta datos con AES (rápido)
    3. Encripta clave AES con RSA pública del dispositivo (seguro)
    4. Solo el dispositivo con clave privada puede desencriptar

    app_credentials: instancia de AppCredentials ya resuelta por el llamador
    (filtrada por app_type + app_version, activa y no comprometida). No se
    vuelve a resolver aquí por app_type para evitar que la clave usada diverja
    de la que el llamador ya validó (AppCredentials.unique_together permite
    varias app_version activas por app_type, así que resolver solo por
    app_type es ambiguo y puede lanzar MultipleObjectsReturned).
    """
    try:
        app_type = app_credentials.app_type

        # ✅ PASO 1: Cargar clave PÚBLICA del dispositivo
        public_key = serialization.load_pem_public_key(
            app_credentials.public_key_pem.encode(),
            backend=default_backend()
        )
        
        # ✅ PASO 2: Generar clave AES simétrica aleatoria (32 bytes = 256 bits)
        aes_key = os.urandom(32)
        iv = os.urandom(16)  # Initialization Vector para AES
        
        # ✅ PASO 3: Encriptar datos con AES
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Padding para AES (debe ser múltiplo de 16 bytes)
        plaintext_bytes = plaintext.encode('utf-8')
        padding_length = 16 - (len(plaintext_bytes) % 16)
        padded_plaintext = plaintext_bytes + bytes([padding_length] * padding_length)
        
        aes_encrypted_data = encryptor.update(padded_plaintext) + encryptor.finalize()
        
        # ✅ PASO 4: Encriptar clave AES con RSA pública
        rsa_encrypted_aes_key = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # ✅ PASO 5: Retornar estructura CORRECTA
        return {
            "encrypted_data": base64.b64encode(aes_encrypted_data).decode('utf-8'),  # ✅ Datos AES encriptados
            "encrypted_key": base64.b64encode(rsa_encrypted_aes_key).decode('utf-8'),  # ✅ Clave AES encriptada con RSA 
            "iv": base64.b64encode(iv).decode('utf-8'),
            "algorithm": "AES-256-CBC + RSA-OAEP",
            "app_type": app_type
        }

    except Exception as e:
        raise Exception(f"❌ Error de encriptación híbrida: {str(e)}")

def verify_app_can_decrypt(app_credentials: AppCredentials) -> bool:
    """
    Verifica que las credenciales ya resueltas por el llamador sigan siendo
    utilizables (activas, no comprometidas, no expiradas).
    """
    return bool(app_credentials) and app_credentials.is_usable()