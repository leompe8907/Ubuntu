import requests
import hashlib
from urllib.parse import urlencode
import logging

from django.http import JsonResponse

from config import PanaccessConfig

PanaccessConfig.validate()

logger = logging.getLogger(__name__)

def login():
    #logger.info("🔑 Iniciando sesión en Panaccess")

    salt = PanaccessConfig.SALT
    username = PanaccessConfig.USERNAME
    password = PanaccessConfig.PASSWORD
    apitoken = PanaccessConfig.API_TOKEN

    # Hasheo de contraseña
    hashed = hashlib.md5((password + salt).encode()).hexdigest()

    payload = {
        "username": username,
        "password": hashed,
        "apiToken": apitoken
    }

    try:
        response = requests.post(
            "https://cv01.panaccess.com/?f=login&requestMode=function",
            data=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            json_response = response.json()
            if not json_response.get("success"):
                raise Exception(json_response.get("errorMessage", "Login fallido sin mensaje explícito."))

            return json_response.get("answer")  # Solo session_id

        raise Exception(f"Respuesta inesperada del servidor Panaccess: {response.status_code}")
        

    except requests.RequestException as e:
        return JsonResponse({'error': str(e)}, status=500)

class CVClient:
    def __init__(self, base_url=PanaccessConfig.PANACCESS):
        self.base_url = base_url
        self.session_id = None

    def md5_hash(self, password):
        """
        Genera un hash MD5 del password con sal. Uso específico requerido por Panaccess.
        No recomendado para otros contextos de seguridad.
        """
        salt = PanaccessConfig.SALT
        return hashlib.md5((password + salt).encode()).hexdigest()

    def call(self, func_name, parameters, timeout=60):
        """
        Llama a una función remota del API Panaccess con los parámetros indicados.
        Agrega automáticamente la sessionId si ya se ha hecho login.
        
        Args:
            func_name: Nombre de la función a llamar
            parameters: Diccionario con los parámetros
            timeout: Timeout en segundos para la conexión (None = sin timeout, default: 60)
        """
        url = f"{self.base_url}?f={func_name}&requestMode=function"

        if self.session_id and func_name != 'login':
            parameters['sessionId'] = self.session_id

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        param_string = urlencode(parameters)

        try:
            response = requests.post(url, data=param_string, headers=headers, timeout=timeout)  # None = sin timeout
            response.raise_for_status()  # lanza error si el status code es 4xx/5xx
            return response.json()
        except requests.exceptions.Timeout as e:
            return {"success": False, "error": f"Timeout error: {str(e)}"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except requests.exceptions.HTTPError as e:
            status_code = response.status_code if 'response' in locals() else None
            return {"success": False, "error": f"HTTP error: {str(e)}", "status_code": status_code}
        except ValueError as ve:
            response_text = response.text if 'response' in locals() else "No response available"
            return {"success": False, "error": f"Invalid JSON: {str(ve)}", "response": response_text}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    def login(self):
        """
        Realiza el login al sistema Panaccess y guarda el sessionId si es exitoso.
        """
        username = PanaccessConfig.USERNAME
        password = PanaccessConfig.PASSWORD
        api_token = PanaccessConfig.API_TOKEN

        if not username or not password or not api_token:
            return False, "Missing Panaccess credentials in configuration."

        hashed_pw = self.md5_hash(password)

        result = self.call("login", {
            "username": username,
            "password": hashed_pw,
            "apiToken": api_token
        })

        if result.get("success"):
            self.session_id = result.get("answer")
            print(f"✅ Login successful. Session ID: {self.session_id}")
            return True, None
        else:
            return False, result.get("errorMessage")