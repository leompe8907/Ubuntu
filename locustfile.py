from locust import HttpUser, task, between, LoadTestShape
import os
import random
import string
import json

# ============================================================
# CONFIGURACIÓN DEL ESCENARIO (AJUSTABLE)
# ============================================================

# URL base de tu API (puede venir de env var o la seteás directo)
API_HOST = os.getenv("API_HOST", "http://localhost:8000")

# Endpoint crítico que querés testear
UDID_VALIDATE_PATH = os.getenv("UDID_VALIDATE_PATH", "/udid/validate/")

# API Key de prueba (creá una en tu sistema con un plan que quieras estresar)
# Si tu API no requiere API Key, puedes dejar esto vacío o comentar el header
API_KEY = os.getenv("API_KEY", "")

# Cantidad máxima de usuarios concurrentes a simular
MAX_USERS = int(os.getenv("MAX_USERS", "1000"))

# Cuántos usuarios nuevos por segundo se agregan en el ramp-up
SPAWN_RATE = int(os.getenv("SPAWN_RATE", "50"))

# Cuánto tiempo mantener el pico (en minutos)
HOLD_PEAK_MINUTES = int(os.getenv("HOLD_PEAK_MINUTES", "5"))


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def random_udid(length=16):
    """Genera un UDID fake para pruebas."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def build_udid_payload():
    """Payload de ejemplo para el endpoint de validación de UDID.
    Ajustá las keys a lo que espere tu API real.
    """
    return {
        "udid": random_udid(),
        "device_model": "TEST_TV_MODEL",
        "tv_serial": random_udid(12),
        "app_type": "tv",
        "app_version": "1.0.0",
        "os_version": "1.0.0",
    }


def build_headers():
    """Headers estándar para tu API."""
    headers = {
        "Content-Type": "application/json",
        # Agregá acá si tu middleware espera más headers (x-tv-serial, etc.).
    }
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


# ============================================================
# USUARIO DE LOCUST (COMPORTAMIENTO)
# ============================================================

class UdidUser(HttpUser):
    """
    Simula una TV/cliente que golpea tu endpoint de validación de UDID.
    """
    wait_time = between(1, 3)  # tiempo random entre requests del mismo user

    def on_start(self):
        # Podés hacer un handshake/login previo acá si lo necesitás
        self.headers = build_headers()

    @task
    def validate_udid(self):
        """
        Task principal: llamar a /udid/validate/ con un UDID random.
        """
        payload = build_udid_payload()

        with self.client.post(
            UDID_VALIDATE_PATH,
            data=json.dumps(payload),
            headers=self.headers,
            name="UDID validate",
            catch_response=True,
        ) as response:
            # Si querés ser estricto, marcá como fallo cualquier cosa que no sea 2xx
            if response.status_code >= 500:
                response.failure(f"Error 5xx: {response.status_code} - {response.text}")
            elif response.status_code == 429:
                response.failure("Rate limited (429)")
            elif response.status_code == 503:
                response.failure("Server overloaded (503)")
            else:
                # Si querés aceptar 4xx como parte del flujo (ej: UDID inválido), podés tratarlos distinto
                response.success()


# ============================================================
# SHAPE DEL TEST: RAMP-UP -> PICO -> RAMP-DOWN
# ============================================================

class RampToThousandUsers(LoadTestShape):
    """
    Escenario:
    - Ramp-up: subís hasta MAX_USERS a razón de SPAWN_RATE usuarios/segundo
    - Hold: mantenés MAX_USERS durante HOLD_PEAK_MINUTES
    - Ramp-down: volvés a 0
    """

    def __init__(self):
        super().__init__()
        self.max_users = MAX_USERS
        self.spawn_rate = SPAWN_RATE
        self.hold_peak_seconds = HOLD_PEAK_MINUTES * 60

        # Tiempo total estimado:
        #   - Ramp-up: max_users / spawn_rate
        #   - Hold: hold_peak_seconds
        #   - Ramp-down: max_users / spawn_rate
        self.ramp_up_seconds = self.max_users / self.spawn_rate
        self.ramp_down_seconds = self.ramp_up_seconds
        self.total_time = self.ramp_up_seconds + self.hold_peak_seconds + self.ramp_down_seconds

    def tick(self):
        run_time = self.get_run_time()

        # Fase 1: ramp-up
        if run_time < self.ramp_up_seconds:
            current_users = int(run_time * self.spawn_rate)
            return (current_users, self.spawn_rate)

        # Fase 2: hold peak
        elif run_time < self.ramp_up_seconds + self.hold_peak_seconds:
            return (self.max_users, self.spawn_rate)

        # Fase 3: ramp-down
        elif run_time < self.total_time:
            elapsed_in_ramp_down = run_time - self.ramp_up_seconds - self.hold_peak_seconds
            current_users = int(self.max_users - elapsed_in_ramp_down * self.spawn_rate)
            current_users = max(current_users, 0)
            return (current_users, self.spawn_rate)

        # Fin del test
        return None
