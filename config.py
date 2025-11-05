# config.py
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Cargar variables desde el archivo .env
load_dotenv()

def _csv(name):
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]

class PanaccessConfig:
    PANACCESS = os.getenv("url_panaccess")
    USERNAME = os.getenv("username")
    PASSWORD = os.getenv("password")
    API_TOKEN = os.getenv("api_token")
    SALT = os.getenv("salt")
    KEY = os.getenv("ENCRYPTION_KEY")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.PANACCESS:
            missing.append("url_panaccess")
        if not cls.USERNAME:
            missing.append("username")
        if not cls.PASSWORD:
            missing.append("password")
        if not cls.API_TOKEN:
            missing.append("api_token")
        if not cls.SALT:
            missing.append("salt")
        if not cls.KEY:
            missing.append("ENCRYPTION_KEY")

        if missing:
            raise EnvironmentError(f"❌ Faltan variables de entorno: {', '.join(missing)}")

class DjangoConfig:
    SECRET_KEY = os.getenv("SECRET_KEY")
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

    # ✅ usar ALLOWED_HOSTS (plural) y filtrar vacíos
    ALLOWED_HOSTS = _csv("ALLOWED_HOSTS")

    # Opcionales: no obligues si no usás CORS/CSRF
    CORS_ORIGIN_WHITELIST = _csv("CORS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGINS = _csv("WS_ALLOWED_ORIGINS")
    WS_ALLOWED_ORIGIN_REGEXES = _csv("WS_ALLOWED_ORIGIN_REGEXES")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not cls.ALLOWED_HOSTS:
            missing.append("ALLOWED_HOSTS")
        # lo siguiente solo si realmente los exigís:
        # if not cls.WS_ALLOWED_ORIGINS: missing.append("WS_ALLOWED_ORIGINS")
        if missing:
            raise EnvironmentError(f"❌ Faltan variables de entorno: {', '.join(missing)}")
