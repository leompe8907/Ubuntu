#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Simulador de carga simplificado.

Cada usuario ejecuta un flujo con los siguientes pasos:
1. Solicitar un UDID (`request-udid-manual/`).
2. Abrir un WebSocket y esperar la solicitud de credenciales.
3. Validar y asociar el UDID (`validate-and-associate-udid/`).
4. Enviar credenciales mediante el WebSocket (si se solicitó).
5. Validar el estado del UDID (`validate/`).
6. Desasociar el UDID (`disassociate-udid/`).

Parámetros configurables (línea de comandos o variables de entorno):
    --usuarios / TEST_NUM_USUARIOS              : cantidad de usuarios simulados.
    --solicitudes / TEST_SOLICITUDES_POR_USUARIO: número de iteraciones por usuario.
    --intervalo / TEST_INTERVALO_SEG            : segundos entre solicitudes de un mismo usuario (default 5).
    --simultaneos                                : número de usuarios a ejecutar simultáneamente (default: todos en paralelo).
"""

import argparse
import json
import os
import random
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

try:
    import websocket

    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    websocket = None
    print("[WARN] Falta dependencia websocket-client. Instala con `pip install websocket-client`.")

# Configurar Django para reutilizar modelos (subscribers disponibles, etc.)
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ubuntu.settings")

import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402
from udid.models import SubscriberInfo, UDIDAuthRequest  # noqa: E402

# Constantes de endpoints
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
WS_BASE_URL = os.getenv("TEST_WS_BASE_URL", "ws://localhost:8000")
API_BASE = f"{BASE_URL}/udid"
WS_AUTH_URL = f"{WS_BASE_URL}/ws/auth/"

# Recursos compartidos
subscribers_lock = threading.Lock()
subscribers_cache = []

stats_lock = threading.Lock()
global_stats = defaultdict(int)
global_stats["errores"] = defaultdict(int)


# -----------------------------------------------------------------------------
# Utilidades de subscribers
# -----------------------------------------------------------------------------
def cargar_subscribers():
    """Carga subscribers disponibles que tengan sn/subscriber_code válido y no estén en uso."""
    global subscribers_cache
    try:
        udids_en_uso = UDIDAuthRequest.objects.filter(
            status__in=["validated", "used"], expires_at__gte=timezone.now(), sn__isnull=False
        ).values_list("sn", flat=True)

        en_uso = set(udids_en_uso)
        disponibles = (
            SubscriberInfo.objects.filter(subscriber_code__isnull=False)
            .exclude(subscriber_code="")
            .exclude(sn__isnull=True)
            .exclude(sn="")
            .exclude(sn__in=en_uso)
            .values_list("subscriber_code", "sn")
            .distinct()
        )

        subscribers_cache = list(disponibles)
        return True
    except Exception as exc:  # pragma: no cover - solo logging
        print(f"[ERROR] No fue posible cargar subscribers: {exc}")
        subscribers_cache = []
        return False


def obtener_subscriber():
    """Obtiene un subscriber disponible de forma thread-safe."""
    with subscribers_lock:
        if not subscribers_cache:
            cargar_subscribers()

        if subscribers_cache:
            return subscribers_cache.pop(0)
        return None, None


def liberar_subscriber(subscriber_code, sn):
    """Devuelve un subscriber al pool."""
    if not subscriber_code or not sn:
        return
    with subscribers_lock:
        if (subscriber_code, sn) not in subscribers_cache:
            subscribers_cache.append((subscriber_code, sn))


# -----------------------------------------------------------------------------
# Utilidades HTTP/WS
# -----------------------------------------------------------------------------
def generar_headers(usuario_id):
    """Genera headers pseudo-aleatorios para simular distintos dispositivos."""
    device_id = f"device-{usuario_id}-{random.randint(1000, 9999)}"
    return {
        "Content-Type": "application/json",
        "User-Agent": f"LoadTester/{usuario_id}/1.0",
        "x-device-id": device_id,
        "x-os-version": f"TestOS/{random.randint(1, 5)}.{random.randint(0, 9)}",
        "x-device-model": f"TestDevice-{random.randint(1000, 9999)}",
        "x-build-id": f"BUILD-{random.randint(100000, 999999)}",
    }


def solicitar_udid(headers):
    url = f"{API_BASE}/request-udid-manual/"
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code == 201:
        data = response.json()
        return True, data.get("udid")
    return False, response.text


def asociar_udid(udid, subscriber_code, sn, headers, max_retries=3):
    """
    Asocia un UDID con reintentos automáticos para manejar errores transitorios
    como 'database is locked' en SQLite.
    """
    payload = {
        "udid": udid,
        "subscriber_code": subscriber_code,
        "sn": sn,
        "operator_id": os.getenv("TEST_OPERATOR_ID", "operator-load-test"),
        "method": "manual",
    }
    url = f"{API_BASE}/validate-and-associate-udid/"
    
    for intento in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            if response.status_code == 200:
                return True, response.text
            
            # Si es un error 500 o 503 (Service Unavailable), puede ser un problema temporal
            if response.status_code in (500, 503) and intento < max_retries - 1:
                # Intentar obtener retry_after del response si está disponible
                try:
                    data = response.json()
                    retry_after = data.get("retry_after", None)
                    if retry_after:
                        wait_time = min(retry_after, 10)  # Máximo 10 segundos
                    else:
                        wait_time = (2 ** intento) + random.uniform(0, 1)
                except:
                    wait_time = (2 ** intento) + random.uniform(0, 1)
                
                time.sleep(wait_time)
                continue
            
            return False, response.text
        except requests.exceptions.RequestException as e:
            if intento < max_retries - 1:
                wait_time = (2 ** intento) + random.uniform(0, 1)
                time.sleep(wait_time)
                continue
            return False, str(e)
    
    return False, "Max retries exceeded"


def validar_udid(udid, headers, max_retries=3):
    """
    Valida el estado de un UDID con reintentos automáticos para manejar errores transitorios.
    """
    url = f"{API_BASE}/validate/"
    
    for intento in range(max_retries):
        try:
            response = requests.get(url, params={"udid": udid}, headers=headers, timeout=15)
            if response.status_code == 200:
                return True, response.text
            
            # Si es un error 500 o 503 (Service Unavailable), puede ser un problema temporal
            if response.status_code in (500, 503) and intento < max_retries - 1:
                try:
                    data = response.json()
                    retry_after = data.get("retry_after", None)
                    if retry_after:
                        wait_time = min(retry_after, 10)  # Máximo 10 segundos
                    else:
                        wait_time = (2 ** intento) + random.uniform(0, 1)
                except:
                    wait_time = (2 ** intento) + random.uniform(0, 1)
                
                time.sleep(wait_time)
                continue
            
            return False, response.text
        except requests.exceptions.RequestException as e:
            if intento < max_retries - 1:
                wait_time = (2 ** intento) + random.uniform(0, 1)
                time.sleep(wait_time)
                continue
            return False, str(e)
    
    return False, "Max retries exceeded"


def desasociar_udid(udid, headers, max_retries=3):
    """
    Desasocia un UDID con reintentos automáticos para manejar errores transitorios.
    """
    payload = {
        "udid": udid,
        "operator_id": os.getenv("TEST_OPERATOR_ID", "operator-load-test"),
        "reason": "Load test cleanup",
    }
    url = f"{API_BASE}/disassociate-udid/"
    
    for intento in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            if response.status_code == 200:
                return True, response.text
            
            # Si es un error 500 o 503 (Service Unavailable), puede ser un problema temporal
            if response.status_code in (500, 503) and intento < max_retries - 1:
                # Intentar obtener retry_after del response si está disponible
                try:
                    data = response.json()
                    retry_after = data.get("retry_after", None)
                    if retry_after:
                        wait_time = min(retry_after, 10)  # Máximo 10 segundos
                    else:
                        wait_time = (2 ** intento) + random.uniform(0, 1)
                except:
                    wait_time = (2 ** intento) + random.uniform(0, 1)
                
                time.sleep(wait_time)
                continue
            
            return False, response.text
        except requests.exceptions.RequestException as e:
            if intento < max_retries - 1:
                wait_time = (2 ** intento) + random.uniform(0, 1)
                time.sleep(wait_time)
                continue
            return False, str(e)
    
    return False, "Max retries exceeded"


def abrir_websocket():
    """Abre una conexión WebSocket si está disponible."""
    if not WEBSOCKET_AVAILABLE:
        return None
    try:
        ws = websocket.create_connection(
            WS_AUTH_URL,
            timeout=10,
            header=[
                "User-Agent: LoadTester/1.0",
                "Origin: http://localhost:8000",
            ],
        )
        return ws
    except Exception as exc:
        print(f"[WARN] No se pudo abrir el WebSocket: {exc}")
        return None


def esperar_solicitud_credenciales(ws, timeout=10):
    """
    Espera a que el servidor solicite credenciales.
    Interpreta cualquier mensaje con type request_credentials/pending como trigger.
    """
    if not ws:
        return False, None

    deadline = time.time() + timeout
    ws.settimeout(1)

    while time.time() < deadline:
        try:
            raw = ws.recv()
            data = json.loads(raw)
            tipo = data.get("type")
            if tipo in {"request_credentials", "pending", "auth_with_udid:pending"}:
                return True, data
            if tipo == "auth_with_udid:result":
                # Ya recibimos resultado, no hará falta enviar nada
                return True, data
            if tipo == "error":
                return False, data
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as exc:
            return False, {"error": str(exc)}
    return False, {"error": "timeout_credenciales"}


def enviar_credenciales(ws, udid, app_type="android_tv", app_version="1.0"):
    """Envía mensaje de autenticación por el WebSocket."""
    if not ws:
        return False, "websocket_no_disponible"
    payload = {
        "type": "auth_with_udid",
        "udid": udid,
        "app_type": app_type,
        "app_version": app_version,
    }
    try:
        ws.send(json.dumps(payload))
        return True, None
    except Exception as exc:
        return False, str(exc)


# -----------------------------------------------------------------------------
# Simulador por usuario
# -----------------------------------------------------------------------------
class SimuladorUsuario:
    def __init__(self, usuario_id, solicitudes, intervalo):
        self.usuario_id = usuario_id
        self.solicitudes = solicitudes
        self.intervalo = intervalo
        self.headers = generar_headers(usuario_id)

    def ejecutar(self):
        resultados = []
        for intento in range(1, self.solicitudes + 1):
            resultado = self._ejecutar_flujo(intento)
            resultados.append(resultado)
            if intento < self.solicitudes:
                time.sleep(self.intervalo)
        return resultados

    def _ejecutar_flujo(self, numero_intento):
        resultado = {
            "usuario_id": self.usuario_id,
            "intento": numero_intento,
            "udid": None,
            "websocket_conectado": False,
            "solicitud_credenciales_recibida": False,
            "credenciales_enviadas": False,
            "asociado": False,
            "validado": False,
            "desasociado": False,
            "errores": [],
        }

        ws = None
        subscriber_code = None
        sn = None

        try:
            # Paso 1: solicitar UDID
            ok, data = solicitar_udid(self.headers)
            if not ok:
                resultado["errores"].append(f"request_udid: {data}")
                self._registrar_error("request_udid", data)
                return resultado
            udid = data
            resultado["udid"] = udid

            # Paso 2: abrir WebSocket y esperar solicitud
            ws = abrir_websocket()
            if ws:
                resultado["websocket_conectado"] = True
                solicitud_ok, ws_data = esperar_solicitud_credenciales(ws)
                resultado["solicitud_credenciales_recibida"] = solicitud_ok
                if not solicitud_ok and ws_data:
                    resultado["errores"].append(f"ws_espera: {ws_data}")
                    self._registrar_error("websocket", ws_data)

            # Paso 3: asociar UDID
            subscriber_code, sn = obtener_subscriber()
            if not subscriber_code:
                resultado["errores"].append("sin_subscribers_disponibles")
                self._registrar_error("subscribers", "pool_vacio")
                return resultado

            ok, resp = asociar_udid(udid, subscriber_code, sn, self.headers)
            if not ok:
                resultado["errores"].append(f"asociar: {resp}")
                liberar_subscriber(subscriber_code, sn)
                self._registrar_error("associate", resp)
                return resultado
            resultado["asociado"] = True

            # Paso 4: enviar credenciales si el WS lo pidió
            if ws and resultado["solicitud_credenciales_recibida"]:
                cred_ok, cred_error = enviar_credenciales(ws, udid)
                resultado["credenciales_enviadas"] = cred_ok
                if not cred_ok:
                    resultado["errores"].append(f"credenciales: {cred_error}")
                    self._registrar_error("credenciales", cred_error or "envio_fallido")

            # Paso 5: validar estado del UDID
            ok, resp = validar_udid(udid, self.headers)
            resultado["validado"] = ok
            if not ok:
                resultado["errores"].append(f"validar: {resp}")
                self._registrar_error("validate", resp)

            # Paso 6: desasociar
            ok, resp = desasociar_udid(udid, self.headers)
            resultado["desasociado"] = ok
            if not ok:
                resultado["errores"].append(f"desasociar: {resp}")
                self._registrar_error("disassociate", resp)
            else:
                liberar_subscriber(subscriber_code, sn)

            return resultado
        except Exception as exc:
            resultado["errores"].append(str(exc))
            self._registrar_error("exception", str(exc))
            if subscriber_code and sn:
                liberar_subscriber(subscriber_code, sn)
            return resultado
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    @staticmethod
    def _registrar_error(tipo, detalle):
        with stats_lock:
            global_stats["errores"][tipo] += 1


# -----------------------------------------------------------------------------
# Orquestador
# -----------------------------------------------------------------------------
def ejecutar_simulacion(num_usuarios, solicitudes_por_usuario, intervalo, usuarios_simultaneos=None):
    print("=" * 70)
    print("  NUEVA SIMULACIÓN DE CARGA - FLUJO COMPLETO")
    print("=" * 70)
    
    # Si no se especifica, ejecutar todos simultáneamente
    if usuarios_simultaneos is None:
        usuarios_simultaneos = num_usuarios
    
    modo = "PARALELO" if usuarios_simultaneos > 1 else "SECUENCIAL"
    print(
        f"\nModo: {modo} | Usuarios: {num_usuarios} | Simultáneos: {usuarios_simultaneos} | "
        f"Solicitudes por usuario: {solicitudes_por_usuario} | "
        f"Intervalo entre solicitudes: {intervalo}s\n"
    )

    if not cargar_subscribers():
        print("[WARN] No se pudieron cargar subscribers inicialmente.")

    inicio = datetime.now()
    resultados = []

    # Ejecutar usuarios en paralelo usando ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=usuarios_simultaneos) as executor:
        # Enviar todas las tareas al pool
        futures = {
            executor.submit(
                lambda uid=usuario_id: SimuladorUsuario(uid, solicitudes_por_usuario, intervalo).ejecutar()
            ): usuario_id
            for usuario_id in range(1, num_usuarios + 1)
        }
        
        # Recopilar resultados conforme se completan
        completados = 0
        for future in as_completed(futures):
            usuario_id = futures[future]
            try:
                usuario_resultados = future.result()
                resultados.extend(usuario_resultados)
                completados += 1
                # Mostrar progreso cada 10% o al final
                if completados % max(1, num_usuarios // 10) == 0 or completados == num_usuarios:
                    print(f"  Progreso: {completados}/{num_usuarios} usuarios completados "
                          f"({completados*100//num_usuarios}%)")
            except Exception as e:
                print(f"  [ERROR] Usuario {usuario_id} falló: {e}")
                resultados.append({
                    "usuario_id": usuario_id,
                    "intento": 1,
                    "errores": [f"excepcion: {str(e)}"],
                    "desasociado": False,
                })

    fin = datetime.now()
    tiempo_total = (fin - inicio).total_seconds()
    print(f"\nSimulación completada en {tiempo_total:.2f} segundos.")
    if num_usuarios > 0:
        print(f"Promedio: {tiempo_total/num_usuarios:.2f} segundos por usuario.")
    mostrar_resumen(resultados)


def mostrar_resumen(resultados):
    total_flujos = len(resultados)
    completados = sum(1 for r in resultados if r["desasociado"])

    print("\n--- RESUMEN ---")
    print(f"Total de flujos ejecutados   : {total_flujos}")
    print(f"Flujos completados correctamente: {completados}")
    print(f"WebSocket disponible          : {'Sí' if WEBSOCKET_AVAILABLE else 'No'}")

    fallas = defaultdict(int)
    errores_detallados = []
    for r in resultados:
        if not r["desasociado"]:
            for err in r["errores"]:
                fallo = err.split(":")[0]
                fallas[fallo] += 1
                # Guardar detalles del error
                detalle = err.split(":", 1)[1] if ":" in err else err
                errores_detallados.append({
                    "usuario": r["usuario_id"],
                    "intento": r["intento"],
                    "tipo": fallo,
                    "detalle": detalle[:200] if len(detalle) > 200 else detalle,  # Limitar longitud
                    "udid": r.get("udid", "N/A")
                })

    if fallas:
        print("\nPrincipales errores:")
        for fallo, cuenta in sorted(fallas.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  - {fallo}: {cuenta}")
        
        # Mostrar detalles de los primeros 5 errores
        if errores_detallados:
            print("\nDetalles de errores (primeros 5):")
            for i, err in enumerate(errores_detallados[:5], 1):
                print(f"  {i}. Usuario {err['usuario']} (intento {err['intento']}) - {err['tipo']}")
                print(f"     UDID: {err['udid']}")
                print(f"     Error: {err['detalle']}")
    else:
        print("\nNo se registraron errores.")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Simulador de flujo completo de UDID.")
    parser.add_argument(
        "--usuarios",
        type=int,
        default=int(os.getenv("TEST_NUM_USUARIOS", "1")),
        help="Número de usuarios a simular.",
    )
    parser.add_argument(
        "--solicitudes",
        type=int,
        default=int(os.getenv("TEST_SOLICITUDES_POR_USUARIO", "1")),
        help="Solicitudes por usuario.",
    )
    parser.add_argument(
        "--intervalo",
        type=float,
        default=float(os.getenv("TEST_INTERVALO_SEG", "5")),
        help="Intervalo (segundos) entre solicitudes de un mismo usuario.",
    )
    parser.add_argument(
        "--simultaneos",
        type=int,
        default=None,
        help="Número de usuarios a ejecutar simultáneamente (default: todos en paralelo).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    ejecutar_simulacion(args.usuarios, args.solicitudes, args.intervalo, args.simultaneos)


if __name__ == "__main__":
    main()


