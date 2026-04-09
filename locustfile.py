from locust import HttpUser, task, between, LoadTestShape
import os
import random
import string
import json
import time
import math
import gevent

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

# Reintentos ante 429 (sin 5xx en backend)
MAX_RETRIES_429 = int(os.getenv("MAX_RETRIES_429", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("BACKOFF_BASE_SECONDS", "0.5"))
BACKOFF_MAX_SECONDS = float(os.getenv("BACKOFF_MAX_SECONDS", "10"))
BACKOFF_JITTER_RATIO = float(os.getenv("BACKOFF_JITTER_RATIO", "0.3"))


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


def _parse_retry_after_seconds(response):
    """
    Retry-After puede venir como segundos (RFC) o faltar.
    Preferimos respetarlo si está.
    """
    ra = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if not ra:
        return None
    try:
        seconds = int(str(ra).strip())
        return max(0, seconds)
    except Exception:
        return None


def _exp_backoff_seconds(attempt_number):
    """
    Backoff exponencial con jitter.
    attempt_number: 1,2,3...
    """
    base = BACKOFF_BASE_SECONDS * (2 ** (attempt_number - 1))
    base = min(base, BACKOFF_MAX_SECONDS)
    jitter = base * BACKOFF_JITTER_RATIO
    return max(0.0, base + random.uniform(-jitter, jitter))


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

        last_response = None
        for attempt in range(1, MAX_RETRIES_429 + 2):  # 1 intento + N retries
            with self.client.post(
                UDID_VALIDATE_PATH,
                data=json.dumps(payload),
                headers=self.headers,
                name="UDID validate",
                catch_response=True,
            ) as response:
                last_response = response

                # Backend ajustado para no devolver 5xx; si aparece, lo marcamos como fallo fuerte.
                if 500 <= response.status_code <= 599:
                    response.failure(f"Unexpected 5xx: {response.status_code} - {response.text}")
                    return

                # 429: respetar Retry-After si existe y reintentar con backoff.
                if response.status_code == 429:
                    retry_after = _parse_retry_after_seconds(response)
                    sleep_s = retry_after if retry_after is not None else _exp_backoff_seconds(attempt)

                    if attempt <= MAX_RETRIES_429:
                        response.success()  # No contar como fallo: es control de carga
                        gevent.sleep(sleep_s)
                        continue

                    response.failure(f"Rate limited (429) after retries. Last Retry-After={retry_after}")
                    return

                # 2xx OK
                if 200 <= response.status_code <= 299:
                    response.success()
                    return

                # 4xx esperables del flujo (UDID inválido, etc.): no “rompen” el servidor.
                # Ajustá esta lista según tu lógica de negocio.
                if response.status_code in (400, 401, 403, 404, 409):
                    response.success()
                    return

                # Cualquier otro status lo marcamos como fallo.
                response.failure(f"Unexpected status: {response.status_code} - {response.text}")
                return


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
