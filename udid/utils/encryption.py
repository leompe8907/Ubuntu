from cryptography.fernet import Fernet

from config import PanaccessConfig

PanaccessConfig.validate()

ENCRYPTION_KEY = PanaccessConfig.KEY
fernet = Fernet(ENCRYPTION_KEY)

def encrypt_value(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()

def decrypt_value(encrypted: str) -> str:
    return fernet.decrypt(encrypted.encode()).decode()