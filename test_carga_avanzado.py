#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script avanzado de simulación de carga con múltiples usuarios concurrentes.
Pruebas intercaladas que simulan diferentes escenarios de uso.

Flujo completo usando solo las views especificadas:
1. RequestUDIDManualView -> request-udid-manual/ (GET) - solicitar el udid
2. ValidateAndAssociateUDIDView -> validate-and-associate-udid/ (POST) - asociar el udid
3. ValidateStatusUDIDView -> validate/ (GET) - validar la asociacion
4. AuthWaitWS (WebSocket) -> ws://localhost:8000/ws/auth/ - autenticación con UDID
5. DisassociateUDIDView -> disassociate-udid/ (POST) - desasociar

Ejecutar: python test_carga_avanzado.py
"""

import os
import sys
import requests
import time
import random
import threading
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import django
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[WARN] psutil no está instalado. Instala con: pip install psutil")
    print("      Las métricas de CPU/Memoria no estarán disponibles")

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("[WARN] websocket-client no está instalado. Instala con: pip install websocket-client")
    print("      Las pruebas de WebSocket no estarán disponibles")


# === Instrumentación de red (HTTP & WebSocket) ==================================
# Medición realista de bytes en red para HTTP (cuerpo tal como llegó: comprimido si aplica)
# mediante envoltorio de requests.Session.send, y contadores para WebSocket.

import types, urllib.parse, gzip as _gzip, zlib as _zlib
try:
    import brotli as _brotli  # opcional
except Exception:
    _brotli = None

_HTTP_INSTRUMENTED = False

def _decode_body_for_app(raw_body: bytes, headers: dict) -> bytes:
    """Devuelve el cuerpo descomprimido si venía comprimido, para compatibilidad con .json()/.text."""
    enc = (headers.get('Content-Encoding') or '').lower()
    try:
        if enc == 'gzip':
            return _gzip.decompress(raw_body)
        elif enc == 'br' and _brotli is not None:
            return _brotli.decompress(raw_body)
        elif enc == 'deflate':
            return _zlib.decompress(raw_body)
    except Exception:
        # Si falla la descompresión, devolver crudo
        return raw_body
    return raw_body

def instrument_requests():
    global _HTTP_INSTRUMENTED
    if _HTTP_INSTRUMENTED:
        return
    _HTTP_INSTRUMENTED = True

    _orig_send = requests.Session.send

    def _counting_send(self, request, **kwargs):
        # Forzar stream para contar bytes crudos
        kwargs['stream'] = True
        # Calcular bytes del request (aprox HTTP/1.1 sobre el cable; no incluye TLS)
        parsed = urllib.parse.urlsplit(request.url)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        start_line = f"{request.method} {path} HTTP/1.1\r\n".encode('ascii', 'ignore')
        headers_len = 0
        for k, v in request.headers.items():
            headers_len += len(str(k).encode('latin-1','ignore')) + 2 + len(str(v).encode('latin-1','ignore')) + 2
        headers_len += 2  # CRLF final
        body = request.body
        if body is None:
            body_len = 0
        elif isinstance(body, (bytes, bytearray)):
            body_len = len(body)
        elif isinstance(body, str):
            body_len = len(body.encode('utf-8'))
        else:
            try:
                body_bytes = body.read()
                body_len = len(body_bytes)
            except Exception:
                body_len = 0

        req_bytes = len(start_line) + headers_len + body_len

        resp = _orig_send(self, request, **kwargs)
        # Leer cuerpo tal cual llegó (sin decode) para contar bytes reales recibidos
        rx = 0
        raw_chunks = []
        try:
            if hasattr(resp, 'raw'):
                # No decodificar a nivel urllib3
                resp.raw.decode_content = False
                for chunk in resp.raw.stream(65536, decode_content=False):
                    if not chunk:
                        continue
                    rx += len(chunk)
                    raw_chunks.append(chunk)
                raw_body = b''.join(raw_chunks)
                # Preparar contenido para el resto del script (descomprimido si corresponde)
                app_body = _decode_body_for_app(raw_body, resp.headers)
                resp._content = app_body
                resp._content_consumed = True
        except Exception:
            pass

        with stats_lock:
            stats['bytes_enviados_total'] += req_bytes
            stats['bytes_recibidos_total'] += rx
            stats['requests_count'] += 1

        return resp

    requests.Session.send = _counting_send

# Contadores específicos de WebSocket
def _ws_count_tx(nbytes: int):
    with stats_lock:
        stats['ws_bytes_tx'] = stats.get('ws_bytes_tx', 0) + nbytes

def _ws_count_rx(nbytes: int):
    with stats_lock:
        stats['ws_bytes_rx'] = stats.get('ws_bytes_rx', 0) + nbytes
# ================================================================================
# Configurar Django para acceder a la base de datos
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')
django.setup()

from udid.models import SubscriberInfo, UDIDAuthRequest
from django.utils import timezone

# Configuración
BASE_URL = os.getenv('TEST_BASE_URL', 'http://localhost:8000')
WS_BASE = os.getenv('TEST_WS_BASE', 'ws://localhost:8000')
API_BASE = f'{BASE_URL}/udid'
WS_AUTH_URL = f'{WS_BASE}/ws/auth/'

# Estadísticas globales
stats = {
    'total_usuarios': 0,
    'requests_exitosos': 0,
    'requests_rate_limited': 0,
    'requests_error': 0,
    'udids_generados': 0,
    'asociaciones_exitosas': 0,
    'validaciones_exitosas': 0,
    'desasociaciones_exitosas': 0,
    'autenticaciones_websocket_exitosas': 0,
    'tiempo_total': 0,
    'tiempos_respuesta': [],
    'tiempos_request_udid': [],
    'tiempos_associate': [],
    'tiempos_validate': [],
    'tiempos_websocket': [],
    'tiempos_disassociate': [],
    'errores': defaultdict(int),
    # Métricas de tráfico de red
    'bytes_enviados_total': 0,
    'bytes_recibidos_total': 0,
    'requests_count': 0,
    'ws_bytes_tx': 0,
    'ws_bytes_rx': 0,
'usuarios_completos': 0,  # Usuarios que completaron todo el flujo
    'usuarios_solo_udid': 0,  # Usuarios que solo pidieron UDID
    'usuarios_reasociacion': 0,  # Usuarios que desasociaron y reasociaron
}

# Métricas de rendimiento del servidor
server_metrics = {
    'cpu_inicial': 0,
    'cpu_maximo': 0,
    'cpu_promedio': 0,
    'memoria_inicial': 0,
    'memoria_maxima': 0,
    'memoria_promedio': 0,
    'muestras_cpu': [],
    'muestras_memoria': [],
    # Métricas de tráfico de red
    'bytes_enviados_inicial': 0,
    'bytes_recibidos_inicial': 0,
    'bytes_enviados_final': 0,
    'bytes_recibidos_final': 0,
    'paquetes_enviados_inicial': 0,
    'paquetes_recibidos_inicial': 0,
    'paquetes_enviados_final': 0,
    'paquetes_recibidos_final': 0,
}

stats_lock = threading.Lock()
subscribers_lock = threading.Lock()
subscribers_disponibles = []
udids_generados = []  # Lista de UDIDs generados para pruebas de desasociación
udids_lock = threading.Lock()

def conectar_websocket(udid, app_type='android_tv', app_version='1.0', timeout=10):
    """
    Conectar al WebSocket y autenticar con UDID.
    Retorna (exitoso, tiempo_respuesta, resultado)
    """
    if not WEBSOCKET_AVAILABLE:
        return False, 0, {"error": "websocket-client no disponible"}
    
    resultado = {"exitoso": False, "error": None, "data": None}
    tiempo_inicio = time.time()
    
    try:
        # Crear conexión WebSocket
        ws = websocket.create_connection(
            WS_AUTH_URL,
            timeout=timeout,
            header=[
                "User-Agent: TestLoadSimulator/1.0",
                "Origin: http://localhost:8000"
            ]
        )
        
        # Enviar mensaje de autenticación
        mensaje = {
            "type": "auth_with_udid",
            "udid": udid,
            "app_type": app_type,
            "app_version": app_version
        }
        _msg=json.dumps(mensaje)
        _ws_count_tx(len(_msg.encode('utf-8')))
        ws.send(_msg)
        
        # Esperar respuesta (con timeout)
        tiempo_limite = time.time() + timeout
        respuesta_recibida = False
        
        while time.time() < tiempo_limite:
            try:
                ws.settimeout(1.0)  # Timeout corto para polling
                respuesta = ws.recv()
                if isinstance(respuesta, bytes):
                    _ws_count_rx(len(respuesta))
                else:
                    _ws_count_rx(len(respuesta.encode('utf-8')))
                data = json.loads(respuesta)
                
                tipo = data.get("type")
                
                if tipo == "auth_with_udid:result":
                    # Respuesta inmediata exitosa
                    resultado["exitoso"] = data.get("status") == "ok"
                    resultado["data"] = data.get("result")
                    respuesta_recibida = True
                    break
                elif tipo == "pending":
                    # Esperar evento udid.validated
                    # En un escenario real, esperaríamos el evento
                    # Para el test, esperamos un tiempo corto
                    time.sleep(0.5)
                    continue
                elif tipo == "error":
                    resultado["error"] = data.get("error") or data.get("detail")
                    respuesta_recibida = True
                    break
                elif tipo == "pong":
                    # Heartbeat, continuar esperando
                    continue
                    
            except websocket.WebSocketTimeoutException:
                # Timeout en recv, continuar esperando
                continue
            except Exception as e:
                resultado["error"] = str(e)
                break
        
        if not respuesta_recibida:
            resultado["error"] = "Timeout esperando respuesta del WebSocket"
        
        ws.close()
        
    except Exception as e:
        resultado["error"] = str(e)
    
    tiempo_respuesta = time.time() - tiempo_inicio
    return resultado["exitoso"], tiempo_respuesta, resultado

def obtener_metricas_sistema():
    """Obtener métricas actuales del sistema"""
    if not PSUTIL_AVAILABLE:
        return {
            'cpu': 0, 
            'memoria_mb': 0, 
            'memoria_percent': 0,
            'bytes_enviados': 0,
            'bytes_recibidos': 0,
            'paquetes_enviados': 0,
            'paquetes_recibidos': 0,
        }
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memoria = psutil.virtual_memory()
        
        # Obtener estadísticas de red (solo interfaces activas, excluyendo loopback)
        net_io = psutil.net_io_counters(pernic=False)  # Total de todas las interfaces
        bytes_enviados = net_io.bytes_sent if net_io else 0
        bytes_recibidos = net_io.bytes_recv if net_io else 0
        paquetes_enviados = net_io.packets_sent if net_io else 0
        paquetes_recibidos = net_io.packets_recv if net_io else 0
        
        # Si hay valores negativos, significa que el contador se reinició
        # En ese caso, usar solo valores positivos
        if bytes_enviados < 0:
            bytes_enviados = 0
        if bytes_recibidos < 0:
            bytes_recibidos = 0
        
        return {
            'cpu': cpu_percent,
            'memoria_mb': memoria.used / 1024 / 1024,  # MB
            'memoria_percent': memoria.percent,
            'bytes_enviados': bytes_enviados,
            'bytes_recibidos': bytes_recibidos,
            'paquetes_enviados': paquetes_enviados,
            'paquetes_recibidos': paquetes_recibidos,
        }
    except Exception:
        return {
            'cpu': 0, 
            'memoria_mb': 0, 
            'memoria_percent': 0,
            'bytes_enviados': 0,
            'bytes_recibidos': 0,
            'paquetes_enviados': 0,
            'paquetes_recibidos': 0,
        }

def iniciar_monitoreo():
    """Iniciar monitoreo de rendimiento del servidor"""
    global server_metrics
    metricas = obtener_metricas_sistema()
    server_metrics['cpu_inicial'] = metricas['cpu']
    server_metrics['memoria_inicial'] = metricas['memoria_mb']
    server_metrics['cpu_maximo'] = metricas['cpu']
    server_metrics['memoria_maxima'] = metricas['memoria_mb']
    # Inicializar métricas de red
    server_metrics['bytes_enviados_inicial'] = metricas['bytes_enviados']
    server_metrics['bytes_recibidos_inicial'] = metricas['bytes_recibidos']
    server_metrics['paquetes_enviados_inicial'] = metricas['paquetes_enviados']
    server_metrics['paquetes_recibidos_inicial'] = metricas['paquetes_recibidos']

def monitorear_rendimiento():
    """Monitorear rendimiento continuamente"""
    while True:
        metricas = obtener_metricas_sistema()
        with stats_lock:
            server_metrics['muestras_cpu'].append(metricas['cpu'])
            server_metrics['muestras_memoria'].append(metricas['memoria_mb'])
            server_metrics['cpu_maximo'] = max(server_metrics['cpu_maximo'], metricas['cpu'])
            server_metrics['memoria_maxima'] = max(server_metrics['memoria_maxima'], metricas['memoria_mb'])
        time.sleep(0.5)  # Muestra cada 0.5 segundos

def calcular_metricas_finales():
    """Calcular métricas finales de rendimiento"""
    global server_metrics
    if server_metrics['muestras_cpu']:
        server_metrics['cpu_promedio'] = sum(server_metrics['muestras_cpu']) / len(server_metrics['muestras_cpu'])
    if server_metrics['muestras_memoria']:
        server_metrics['memoria_promedio'] = sum(server_metrics['muestras_memoria']) / len(server_metrics['muestras_memoria'])

def cargar_subscribers_disponibles():
    """Cargar lista de subscribers disponibles desde la base de datos"""
    global subscribers_disponibles
    try:
        # Obtener todas las SNs que están en uso por UDIDs activos
        udids_activos = UDIDAuthRequest.objects.filter(
            status__in=['validated', 'used'],
            expires_at__gte=timezone.now(),
            sn__isnull=False
        ).values_list('sn', flat=True)
        
        sns_en_uso = set(udids_activos)
        
        # Obtener subscribers con SNs disponibles
        subscribers = SubscriberInfo.objects.filter(
            subscriber_code__isnull=False
        ).exclude(
            subscriber_code=''
        ).exclude(
            sn__isnull=True
        ).exclude(
            sn=''
        ).exclude(
            sn__in=sns_en_uso
        ).values_list('subscriber_code', 'sn', flat=False).distinct()
        
        subscribers_disponibles = list(subscribers)
        return len(subscribers_disponibles) > 0
    except Exception as e:
        print(f"[ERROR] Error cargando subscribers: {str(e)}")
        return False

def obtener_subscriber_disponible():
    """Obtener un subscriber disponible de forma thread-safe"""
    global subscribers_disponibles
    with subscribers_lock:
        if not subscribers_disponibles:
            cargar_subscribers_disponibles()
        
        if subscribers_disponibles:
            subscriber_code, sn = subscribers_disponibles.pop(0)
            return subscriber_code, sn
        return None, None

def liberar_subscriber(subscriber_code, sn):
    """Liberar un subscriber para que pueda ser usado de nuevo"""
    global subscribers_disponibles
    with subscribers_lock:
        if (subscriber_code, sn) not in subscribers_disponibles:
            subscribers_disponibles.append((subscriber_code, sn))

def agregar_udid_generado(udid):
    """Agregar UDID a la lista de generados"""
    global udids_generados
    with udids_lock:
        udids_generados.append(udid)

def obtener_udid_para_desasociar():
    """Obtener un UDID asociado para desasociar"""
    global udids_generados
    with udids_lock:
        # Buscar UDIDs que estén asociados
        for udid in reversed(udids_generados):  # Tomar los más recientes primero
            try:
                req = UDIDAuthRequest.objects.get(udid=udid)
                if req.status in ['validated', 'used'] and req.subscriber_code and req.sn:
                    return udid, req.subscriber_code, req.sn
            except:
                continue
        return None, None, None

def print_section(title):
    """Imprimir una sección"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def generar_device_fingerprint(usuario_id):
    """Generar un device fingerprint único para cada usuario simulado"""
    device_ids = [
        f"device-{usuario_id}-android-tv",
        f"device-{usuario_id}-samsung-tv",
        f"device-{usuario_id}-ios",
        f"device-{usuario_id}-android",
    ]
    return random.choice(device_ids)

def get_headers(usuario_id):
    """Generar headers para un usuario simulado"""
    device_id = generar_device_fingerprint(usuario_id)
    return {
        'Content-Type': 'application/json',
        'User-Agent': f'SimulatedUser/{usuario_id}/1.0',
        'x-device-id': device_id,
        'x-os-version': f'TestOS/{random.randint(1, 5)}.{random.randint(0, 9)}',
        'x-device-model': f'TestDevice-{random.randint(1000, 9999)}',
        'x-build-id': f'BUILD-{random.randint(100000, 999999)}',
    }

def simular_usuario_completo(usuario_id, delay_inicial=0):
    """
    Simula un usuario que completa TODO el flujo:
    1. Request UDID
    2. Asociar
    3. Validar
    4. Desasociar
    """
    user_stats = {
        'usuario_id': usuario_id,
        'tipo': 'completo',
        'udid_generado': False,
        'asociacion_exitosa': False,
        'validacion_exitosa': False,
        'websocket_exitoso': False,
        'desasociacion_exitosa': False,
        'rate_limited': False,
        'errores': [],
        'tiempo_total': 0,
    }
    
    start_time = time.time()
    if delay_inicial > 0:
        time.sleep(delay_inicial)
    
    headers = get_headers(usuario_id)
    udid = None
    subscriber_code = None
    sn = None
    
    try:
        # PASO 1: Request UDID Manual
        step_start = time.time()
        response = requests.get(
            f'{API_BASE}/request-udid-manual/',
            headers=headers,
            timeout=10
        )
        step_time = time.time() - step_start
        
        # Medir tráfico de red
        request_size = len(str(headers).encode('utf-8')) + len(f'{API_BASE}/request-udid-manual/'.encode('utf-8'))
        response_size = len(response.content) if hasattr(response, 'content') else 0
        
        with stats_lock:
            stats['tiempos_request_udid'].append(step_time)
            stats['bytes_enviados_total'] += request_size
            stats['bytes_recibidos_total'] += response_size
            stats['requests_count'] += 1
        
        if response.status_code == 201:
            udid_data = response.json()
            udid = udid_data.get('udid')
            user_stats['udid_generado'] = True
            agregar_udid_generado(udid)
            
            with stats_lock:
                stats['requests_exitosos'] += 1
                stats['udids_generados'] += 1
        else:
            with stats_lock:
                if response.status_code == 429:
                    stats['requests_rate_limited'] += 1
                else:
                    stats['requests_error'] += 1
            return user_stats
        
        # PASO 2: Asociar UDID
        subscriber_code, sn = obtener_subscriber_disponible()
        if subscriber_code and sn:
            time.sleep(random.uniform(0.1, 0.3))
            step_start = time.time()
            
            associate_response = requests.post(
                f'{API_BASE}/validate-and-associate-udid/',
                json={
                    'udid': udid,
                    'subscriber_code': subscriber_code,
                    'sn': sn,
                    'operator_id': f'operator-{random.randint(1, 10)}',
                    'method': 'manual',
                },
                headers=headers,
                timeout=10
            )
            step_time = time.time() - step_start
            
            # Medir tráfico de red (siempre, independientemente del resultado)
            request_data = {
                'udid': udid,
                'subscriber_code': subscriber_code,
                'sn': sn,
                'operator_id': f'operator-{random.randint(1, 10)}',
                'method': 'manual',
            }
            request_size = len(json.dumps(request_data).encode('utf-8')) + len(str(headers).encode('utf-8'))
            response_size = len(associate_response.content) if hasattr(associate_response, 'content') else 0
            
            with stats_lock:
                stats['tiempos_associate'].append(step_time)
                stats['bytes_enviados_total'] += request_size
                stats['bytes_recibidos_total'] += response_size
                stats['requests_count'] += 1
            
            if associate_response.status_code == 200:
                user_stats['asociacion_exitosa'] = True
                with stats_lock:
                    stats['asociaciones_exitosas'] += 1
            else:
                liberar_subscriber(subscriber_code, sn)
                subscriber_code = None
                sn = None
        
        # PASO 3: Validar
        if subscriber_code and sn:
            time.sleep(random.uniform(0.1, 0.3))
            step_start = time.time()
            
            validate_response = requests.get(
                f'{API_BASE}/validate/',
                params={'udid': udid},
                headers=headers,
                timeout=10
            )
            step_time = time.time() - step_start
            
            # Medir tráfico de red (siempre, independientemente del resultado)
            request_size = len(str(headers).encode('utf-8')) + len(f'{API_BASE}/validate/?udid={udid}'.encode('utf-8'))
            response_size = len(validate_response.content) if hasattr(validate_response, 'content') else 0
            
            with stats_lock:
                stats['tiempos_validate'].append(step_time)
                stats['bytes_enviados_total'] += request_size
                stats['bytes_recibidos_total'] += response_size
                stats['requests_count'] += 1
            
            if validate_response.status_code == 200:
                user_stats['validacion_exitosa'] = True
                with stats_lock:
                    stats['validaciones_exitosas'] += 1
        
        # PASO 4: WebSocket - Autenticación con UDID
        if subscriber_code and sn:
            time.sleep(random.uniform(0.1, 0.3))
            step_start = time.time()
            
            ws_exitoso, ws_tiempo, ws_resultado = conectar_websocket(
                udid=udid,
                app_type='android_tv',
                app_version='1.0',
                timeout=10
            )
            step_time = time.time() - step_start
            
            with stats_lock:
                stats['tiempos_websocket'].append(step_time)
            
            if ws_exitoso:
                user_stats['websocket_exitoso'] = True
                with stats_lock:
                    stats['autenticaciones_websocket_exitosas'] += 1
        
        # PASO 5: Desasociar
        if subscriber_code and sn:
            time.sleep(random.uniform(0.1, 0.3))
            step_start = time.time()
            
            disassociate_response = requests.post(
                f'{API_BASE}/disassociate-udid/',
                json={
                    'udid': udid,
                    'operator_id': f'operator-{random.randint(1, 10)}',
                    'reason': 'Test disassociation'
                },
                headers=headers,
                timeout=10
            )
            step_time = time.time() - step_start
            
            # Medir tráfico de red (siempre, independientemente del resultado)
            request_data = {
                'udid': udid,
                'operator_id': f'operator-{random.randint(1, 10)}',
                'reason': 'Test disassociation'
            }
            request_size = len(json.dumps(request_data).encode('utf-8')) + len(str(headers).encode('utf-8'))
            response_size = len(disassociate_response.content) if hasattr(disassociate_response, 'content') else 0
            
            with stats_lock:
                stats['tiempos_disassociate'].append(step_time)
                stats['bytes_enviados_total'] += request_size
                stats['bytes_recibidos_total'] += response_size
                stats['requests_count'] += 1
            
            if disassociate_response.status_code == 200:
                user_stats['desasociacion_exitosa'] = True
                liberar_subscriber(subscriber_code, sn)
                with stats_lock:
                    stats['desasociaciones_exitosas'] += 1
                    stats['usuarios_completos'] += 1
        
    except Exception as e:
        user_stats['errores'].append(str(e))
        if subscriber_code and sn:
            liberar_subscriber(subscriber_code, sn)
    
    finally:
        user_stats['tiempo_total'] = time.time() - start_time
        with stats_lock:
            stats['tiempos_respuesta'].append(user_stats['tiempo_total'])
    
    return user_stats

def simular_usuario_solo_udid(usuario_id, delay_inicial=0):
    """Simula un usuario que solo solicita UDID (no asocia)"""
    user_stats = {
        'usuario_id': usuario_id,
        'tipo': 'solo_udid',
        'udid_generado': False,
        'rate_limited': False,
        'errores': [],
        'tiempo_total': 0,
    }
    
    start_time = time.time()
    if delay_inicial > 0:
        time.sleep(delay_inicial)
    
    headers = get_headers(usuario_id)
    
    try:
        step_start = time.time()
        response = requests.get(
            f'{API_BASE}/request-udid-manual/',
            headers=headers,
            timeout=10
        )
        step_time = time.time() - step_start
        
        with stats_lock:
            stats['tiempos_request_udid'].append(step_time)
        
        # Medir tráfico de red
        request_size = len(str(headers).encode('utf-8')) + len(f'{API_BASE}/request-udid-manual/'.encode('utf-8'))
        response_size = len(response.content) if hasattr(response, 'content') else 0
        
        if response.status_code == 201:
            udid_data = response.json()
            udid = udid_data.get('udid')
            user_stats['udid_generado'] = True
            agregar_udid_generado(udid)
            
            with stats_lock:
                stats['requests_exitosos'] += 1
                stats['udids_generados'] += 1
                stats['usuarios_solo_udid'] += 1
                stats['bytes_enviados_total'] += request_size
                stats['bytes_recibidos_total'] += response_size
                stats['requests_count'] += 1
        else:
            with stats_lock:
                if response.status_code == 429:
                    stats['requests_rate_limited'] += 1
                else:
                    stats['requests_error'] += 1
    
    except Exception as e:
        user_stats['errores'].append(str(e))
        with stats_lock:
            stats['requests_error'] += 1
    
    finally:
        user_stats['tiempo_total'] = time.time() - start_time
        with stats_lock:
            stats['tiempos_respuesta'].append(user_stats['tiempo_total'])
    
    return user_stats

def simular_usuario_reasociacion(usuario_id, delay_inicial=0):
    """Simula un usuario que desasocia un UDID existente y lo reasocia"""
    user_stats = {
        'usuario_id': usuario_id,
        'tipo': 'reasociacion',
        'desasociacion_exitosa': False,
        'reasociacion_exitosa': False,
        'rate_limited': False,
        'errores': [],
        'tiempo_total': 0,
    }
    
    start_time = time.time()
    if delay_inicial > 0:
        time.sleep(delay_inicial)
    
    headers = get_headers(usuario_id)
    
    try:
        # Obtener UDID existente para desasociar
        udid, subscriber_code, sn = obtener_udid_para_desasociar()
        
        if not udid:
            # No hay UDIDs para desasociar, crear uno nuevo
            return simular_usuario_completo(usuario_id, 0)
        
        # PASO 1: Desasociar
        step_start = time.time()
        
        disassociate_response = requests.post(
            f'{API_BASE}/disassociate-udid/',
            json={
                'udid': udid,
                'operator_id': f'operator-{random.randint(1, 10)}',
                'reason': 'Test reasociation'
            },
            headers=headers,
            timeout=10
        )
        step_time = time.time() - step_start
        
        # Medir tráfico de red
        request_data = {
            'udid': udid,
            'operator_id': f'operator-{random.randint(1, 10)}',
            'reason': 'Test reasociation'
        }
        request_size = len(json.dumps(request_data).encode('utf-8')) + len(str(headers).encode('utf-8'))
        response_size = len(disassociate_response.content) if hasattr(disassociate_response, 'content') else 0
        
        with stats_lock:
            stats['tiempos_disassociate'].append(step_time)
            stats['bytes_enviados_total'] += request_size
            stats['bytes_recibidos_total'] += response_size
            stats['requests_count'] += 1
        
        if disassociate_response.status_code == 200:
            user_stats['desasociacion_exitosa'] = True
            liberar_subscriber(subscriber_code, sn)
            with stats_lock:
                stats['desasociaciones_exitosas'] += 1
            
            # PASO 2: Reasociar
            time.sleep(random.uniform(0.2, 0.5))
            step_start = time.time()
            
            associate_response = requests.post(
                f'{API_BASE}/validate-and-associate-udid/',
                json={
                    'udid': udid,
                    'subscriber_code': subscriber_code,
                    'sn': sn,
                    'operator_id': f'operator-{random.randint(1, 10)}',
                    'method': 'manual',
                },
                headers=headers,
                timeout=10
            )
            step_time = time.time() - step_start
            
            with stats_lock:
                stats['tiempos_associate'].append(step_time)
            
            # Medir tráfico de red
            request_size = len(json.dumps({
                'udid': udid,
                'subscriber_code': subscriber_code,
                'sn': sn,
                'operator_id': f'operator-{random.randint(1, 10)}',
                'method': 'manual',
            }).encode('utf-8')) + len(str(headers).encode('utf-8'))
            response_size = len(associate_response.content) if hasattr(associate_response, 'content') else 0
            
            if associate_response.status_code == 200:
                user_stats['reasociacion_exitosa'] = True
                with stats_lock:
                    stats['asociaciones_exitosas'] += 1
                    stats['usuarios_reasociacion'] += 1
                    stats['bytes_enviados_total'] += request_size
                    stats['bytes_recibidos_total'] += response_size
                    stats['requests_count'] += 1
            else:
                liberar_subscriber(subscriber_code, sn)
        else:
            liberar_subscriber(subscriber_code, sn)
    
    except Exception as e:
        user_stats['errores'].append(str(e))
        if subscriber_code and sn:
            liberar_subscriber(subscriber_code, sn)
    
    finally:
        user_stats['tiempo_total'] = time.time() - start_time
        with stats_lock:
            stats['tiempos_respuesta'].append(user_stats['tiempo_total'])
    
    return user_stats

def simular_usuario(usuario_id, delay_inicial=0):
    """
    Simula un usuario con comportamiento aleatorio intercalado.
    Diferentes tipos de usuarios para probar diferentes escenarios.
    """
    # Distribución de tipos de usuarios:
    # 40% - Usuario completo (todo el flujo)
    # 30% - Solo UDID (no asocia)
    # 30% - Reasociación (desasocia y reasocia)
    rand = random.random()
    
    if rand < 0.4:
        return simular_usuario_completo(usuario_id, delay_inicial)
    elif rand < 0.7:
        return simular_usuario_solo_udid(usuario_id, delay_inicial)
    else:
        return simular_usuario_reasociacion(usuario_id, delay_inicial)

def verificar_redis():
    """Verificar si Redis está disponible y funcionando"""
    try:
        from django.core.cache import cache
        from django.conf import settings
        
        # Verificar primero si REDIS_URL está configurado
        redis_url = getattr(settings, 'REDIS_URL', None)
        redis_url_env = os.getenv('REDIS_URL', None)
        
        if not redis_url and not redis_url_env:
            print(f"[WARN] Redis NO está configurado")
            print(f"     REDIS_URL no está definido en las variables de entorno")
            print(f"     El sistema está usando LocMemCache (cache local, no distribuido)")
            print(f"     ADVERTENCIA: En pruebas de carga, el rate limiting puede no funcionar correctamente")
            print(f"     entre múltiples instancias del servidor.")
            print(f"     Para usar Redis:")
            print(f"     1. Asegúrate de que Redis esté corriendo (ej: docker run -d -p 6379:6379 redis)")
            print(f"     2. Configura la variable de entorno: $env:REDIS_URL='redis://localhost:6379/0'")
            print(f"     3. O en PowerShell: $env:REDIS_URL='redis://localhost:6379/0'")
            return False
        
        # Intentar escribir y leer del cache
        test_key = 'test_redis_connection_' + str(int(time.time()))
        test_value = 'test_value_' + str(int(time.time()))
        
        cache.set(test_key, test_value, timeout=60)
        retrieved = cache.get(test_key)
        
        if retrieved == test_value:
            # Verificar si está usando Redis o LocMemCache
            if redis_url:
                print(f"[OK] Redis está activo y funcionando correctamente")
                print(f"     Redis URL: {redis_url[:60]}...")
                return True
            else:
                # Cache funciona pero no está usando Redis (probablemente LocMemCache)
                print(f"[WARN] Cache funciona pero NO está usando Redis")
                print(f"     REDIS_URL está definido pero Django está usando LocMemCache")
                print(f"     Verifica la configuración en settings.py")
                return False
        else:
            print(f"[ERROR] Redis no funciona correctamente (valor no coincide)")
            print(f"     Esperado: {test_value}, Obtenido: {retrieved}")
            return False
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Error al verificar Redis: {error_msg}")
        if "Connection refused" in error_msg or "cannot connect" in error_msg.lower():
            print(f"     No se puede conectar a Redis. Verifica que Redis esté corriendo:")
            print(f"     - Docker: docker ps | findstr redis")
            print(f"     - O ejecuta: docker run -d -p 6379:6379 redis")
        print(f"     El sistema puede estar usando LocMemCache como fallback")
        return False

def ejecutar_simulacion(num_usuarios, usuarios_simultaneos=10):
    """Ejecuta la simulación con múltiples usuarios"""
    print_section(f"SIMULACIÓN DE CARGA - {num_usuarios} USUARIOS")
    
    # Verificar Redis antes de continuar
    print("Verificando conexión a Redis...")
    redis_activo = verificar_redis()
    if not redis_activo:
        # Verificar si estamos en modo no interactivo (variable de entorno)
        skip_redis_check = os.getenv('TEST_SKIP_REDIS_CHECK', '0') == '1'
        if not skip_redis_check:
            try:
                respuesta = input("\n¿Deseas continuar con el test sin Redis? (s/n): ")
                if respuesta.lower() != 's':
                    print("Test cancelado por el usuario.")
                    return None
            except (EOFError, KeyboardInterrupt):
                # Modo no interactivo o cancelado
                print("\n[INFO] Modo no interactivo detectado. Continuando sin Redis...")
        else:
            print("\n[INFO] TEST_SKIP_REDIS_CHECK=1 detectado. Continuando sin Redis...")
        print()
    
    # Cargar subscribers disponibles
    print("Cargando subscribers disponibles desde la base de datos...")
    if not cargar_subscribers_disponibles():
        print("[ERROR] No se pudieron cargar subscribers. La simulación puede fallar.")
    else:
        print(f"[OK] {len(subscribers_disponibles)} subscribers disponibles cargados")
    
    # Verificar disponibilidad de WebSocket
    if not WEBSOCKET_AVAILABLE:
        print(f"[WARN] websocket-client no está disponible. Las pruebas de WebSocket se omitirán\n")
    else:
        print(f"[OK] WebSocket disponible para pruebas\n")
    
    instrument_requests()
    # Iniciar monitoreo
    iniciar_monitoreo()
    monitor_thread = threading.Thread(target=monitorear_rendimiento, daemon=True)
    monitor_thread.start()
    
    print(f"Configuración:")
    print(f"  - Total de usuarios: {num_usuarios}")
    print(f"  - Usuarios simultáneos: {usuarios_simultaneos}")
    print(f"  - Base URL: {BASE_URL}")
    print(f"  - Subscribers disponibles: {len(subscribers_disponibles)}")
    print(f"  - CPU inicial: {server_metrics['cpu_inicial']:.1f}%")
    print(f"  - Memoria inicial: {server_metrics['memoria_inicial']:.1f} MB")
    print(f"  - Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Resetear estadísticas (usar el diccionario global, no crear uno nuevo)
    global stats
    stats['total_usuarios'] = num_usuarios
    stats['requests_exitosos'] = 0
    stats['requests_rate_limited'] = 0
    stats['requests_error'] = 0
    stats['udids_generados'] = 0
    stats['asociaciones_exitosas'] = 0
    stats['validaciones_exitosas'] = 0
    stats['desasociaciones_exitosas'] = 0
    stats['autenticaciones_websocket_exitosas'] = 0
    stats['tiempo_total'] = 0
    stats['tiempos_respuesta'] = []
    stats['tiempos_request_udid'] = []
    stats['tiempos_associate'] = []
    stats['tiempos_validate'] = []
    stats['tiempos_websocket'] = []
    stats['tiempos_disassociate'] = []
    stats['errores'] = defaultdict(int)
    stats['usuarios_completos'] = 0
    stats['usuarios_solo_udid'] = 0
    stats['usuarios_reasociacion'] = 0
    # Métricas de tráfico de red
    stats['bytes_enviados_total'] = 0
    stats['bytes_recibidos_total'] = 0
    stats['requests_count'] = 0
    
    inicio = time.time()
    
    # Distribuir usuarios en el tiempo
    delays = [random.uniform(0, 1) for _ in range(num_usuarios)]
    
    # Ejecutar con ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=usuarios_simultaneos) as executor:
        futures = {
            executor.submit(simular_usuario, i+1, delays[i]): i+1 
            for i in range(num_usuarios)
        }
        
        completados = 0
        for future in as_completed(futures):
            usuario_id = futures[future]
            try:
                user_stats = future.result()
                completados += 1
                
                if completados % max(1, num_usuarios // 10) == 0 or completados == num_usuarios:
                    print(f"  Progreso: {completados}/{num_usuarios} usuarios completados "
                          f"({completados*100//num_usuarios}%)")
                    
            except Exception as e:
                print(f"  Error procesando usuario {usuario_id}: {str(e)}")
    
    tiempo_total = time.time() - inicio
    stats['tiempo_total'] = tiempo_total
    
    # Calcular métricas finales
    calcular_metricas_finales()
    
    return stats

def mostrar_resultados(stats):
    """Mostrar resultados de la simulación"""
    print_section("RESULTADOS DE LA SIMULACIÓN")
    
    total = stats['total_usuarios']
    exitosos = stats['requests_exitosos']
    rate_limited = stats['requests_rate_limited']
    errores = stats['requests_error']
    
    print(f"Resumen General:")
    print(f"  - Total de usuarios simulados: {total}")
    print(f"  - Requests exitosos: {exitosos} ({exitosos*100//total if total > 0 else 0}%)")
    print(f"  - Requests bloqueados (rate limit): {rate_limited} ({rate_limited*100//total if total > 0 else 0}%)")
    print(f"  - Requests con error: {errores} ({errores*100//total if total > 0 else 0}%)")
    print()
    
    print(f"Tipos de Usuarios Simulados:")
    print(f"  - Usuarios con flujo completo: {stats['usuarios_completos']}")
    print(f"  - Usuarios solo UDID: {stats['usuarios_solo_udid']}")
    print(f"  - Usuarios con reasociación: {stats['usuarios_reasociacion']}")
    print()
    
    print(f"Operaciones Completadas:")
    print(f"  - UDIDs generados: {stats['udids_generados']}")
    print(f"  - Asociaciones exitosas: {stats.get('asociaciones_exitosas', 0)}")
    print(f"  - Validaciones exitosas: {stats['validaciones_exitosas']}")
    print(f"  - Autenticaciones WebSocket exitosas: {stats.get('autenticaciones_websocket_exitosas', 0)}")
    print(f"  - Desasociaciones exitosas: {stats['desasociaciones_exitosas']}")
    print()
    
    print(f"Rendimiento del Servidor:")
    print(f"  - CPU inicial: {server_metrics['cpu_inicial']:.1f}%")
    print(f"  - CPU máximo: {server_metrics['cpu_maximo']:.1f}%")
    print(f"  - CPU promedio: {server_metrics['cpu_promedio']:.1f}%")
    print(f"  - Memoria inicial: {server_metrics['memoria_inicial']:.1f} MB")
    print(f"  - Memoria máxima: {server_metrics['memoria_maxima']:.1f} MB ({server_metrics['memoria_maxima'] - server_metrics['memoria_inicial']:.1f} MB incremento)")
    print(f"  - Memoria promedio: {server_metrics['memoria_promedio']:.1f} MB")
    print()
    
    # Mostrar métricas de tráfico de red (medido desde las respuestas HTTP)
    bytes_enviados_total = stats.get('bytes_enviados_total', 0)
    bytes_recibidos_total = stats.get('bytes_recibidos_total', 0)
    bytes_total = bytes_enviados_total + bytes_recibidos_total
    requests_count = stats.get('requests_count', 0)
    
    print(f"Trafico de Red (medido desde requests HTTP):")
    if bytes_total > 0 or requests_count > 0:
        print(f"  - Bytes enviados (requests): {bytes_enviados_total:,} bytes ({bytes_enviados_total / 1024 / 1024:.2f} MB)")
        print(f"  - Bytes recibidos (responses): {bytes_recibidos_total:,} bytes ({bytes_recibidos_total / 1024 / 1024:.2f} MB)")
        print(f"  - Total de bytes: {bytes_total:,} bytes ({bytes_total / 1024 / 1024:.2f} MB)")
        print(f"  - Requests HTTP procesados: {requests_count:,}")
        if requests_count > 0:
            bytes_promedio_request = bytes_total / requests_count
            print(f"  - Bytes promedio por request: {bytes_promedio_request / 1024:.2f} KB")
        if stats['tiempo_total'] > 0:
            bytes_por_segundo = bytes_total / stats['tiempo_total']
            mbps = (bytes_por_segundo * 8) / 1024 / 1024  # Megabits por segundo
            print(f"  - Velocidad promedio: {bytes_por_segundo / 1024:.2f} KB/s ({mbps:.2f} Mbps)")
            if total > 0:
                bytes_por_usuario = bytes_total / total
                print(f"  - Bytes por usuario: {bytes_por_usuario / 1024:.2f} KB")
    else:
        print(f"  - [INFO] No se pudo medir tráfico de red")
    print()
    
    # Métricas de WebSocket (payload de aplicación)
    print(f"Tráfico WebSocket (payload app):")
    print(f"  - Bytes enviados (WS): {stats.get('ws_bytes_tx', 0)}")
    print(f"  - Bytes recibidos (WS): {stats.get('ws_bytes_rx', 0)}")
    print()

    # Métricas de red del host (psutil) - tráfico real total del host
    metricas_final = obtener_metricas_sistema()
    try:
        delta_tx = metricas_final['bytes_enviados'] - server_metrics.get('bytes_enviados_inicial', 0)
        delta_rx = metricas_final['bytes_recibidos'] - server_metrics.get('bytes_recibidos_inicial', 0)
        pkt_tx = metricas_final['paquetes_enviados'] - server_metrics.get('paquetes_enviados_inicial', 0)
        pkt_rx = metricas_final['paquetes_recibidos'] - server_metrics.get('paquetes_recibidos_inicial', 0)
        print("Tráfico del host (psutil):")
        print(f"  - Bytes enviados (host): {delta_tx:,} bytes ({delta_tx/1024/1024:.2f} MB)")
        print(f"  - Bytes recibidos (host): {delta_rx:,} bytes ({delta_rx/1024/1024:.2f} MB)")
        print(f"  - Paquetes enviados (host): {pkt_tx:,}")
        print(f"  - Paquetes recibidos (host): {pkt_rx:,}")
    except Exception:
        print("Tráfico del host (psutil): no disponible")
    print()

    print(f"Tiempos de Respuesta:")
    print(f"  - Tiempo total: {stats['tiempo_total']:.2f} segundos")
    if total > 0:
        print(f"  - Usuarios por segundo: {total/stats['tiempo_total']:.2f}")
    
    if stats['tiempos_respuesta']:
        tiempos = stats['tiempos_respuesta']
        print(f"  - Tiempo de respuesta promedio (total): {sum(tiempos)/len(tiempos):.3f}s")
        print(f"  - Tiempo de respuesta mínimo: {min(tiempos):.3f}s")
        print(f"  - Tiempo de respuesta máximo: {max(tiempos):.3f}s")
    print()
    
    if stats['tiempos_request_udid']:
        tiempos = stats['tiempos_request_udid']
        print(f"  - Request UDID - Promedio: {sum(tiempos)/len(tiempos):.3f}s, Min: {min(tiempos):.3f}s, Max: {max(tiempos):.3f}s")
    
    if stats['tiempos_associate']:
        tiempos = stats['tiempos_associate']
        print(f"  - Associate - Promedio: {sum(tiempos)/len(tiempos):.3f}s, Min: {min(tiempos):.3f}s, Max: {max(tiempos):.3f}s")
    
    if stats['tiempos_validate']:
        tiempos = stats['tiempos_validate']
        print(f"  - Validate - Promedio: {sum(tiempos)/len(tiempos):.3f}s, Min: {min(tiempos):.3f}s, Max: {max(tiempos):.3f}s")
    
    if stats['tiempos_websocket']:
        tiempos = stats['tiempos_websocket']
        print(f"  - WebSocket Auth - Promedio: {sum(tiempos)/len(tiempos):.3f}s, Min: {min(tiempos):.3f}s, Max: {max(tiempos):.3f}s")
    
    if stats['tiempos_disassociate']:
        tiempos = stats['tiempos_disassociate']
        print(f"  - Disassociate - Promedio: {sum(tiempos)/len(tiempos):.3f}s, Min: {min(tiempos):.3f}s, Max: {max(tiempos):.3f}s")
    print()
    
    if stats['errores']:
        print(f"Errores Encontrados:")
        for error, count in sorted(stats['errores'].items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  - {error}: {count}")
        print()
    
    print(f"Fecha/Hora de finalización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

def main():
    """Función principal"""
    print("\n" + "="*70)
    print("  SIMULADOR DE CARGA AVANZADO - PRUEBAS INTERCALADAS")
    print("="*70)
    print("\nEste script simula múltiples usuarios usando solo las views especificadas:")
    print("  1. RequestUDIDManualView -> request-udid-manual/ (GET) - solicitar el udid")
    print("  2. ValidateAndAssociateUDIDView -> validate-and-associate-udid/ (POST) - asociar el udid")
    print("  3. ValidateStatusUDIDView -> validate/ (GET) - validar la asociacion")
    print("  4. AuthWaitWS (WebSocket) -> ws://localhost:8000/ws/auth/ - autenticación con UDID")
    print("  5. DisassociateUDIDView -> disassociate-udid/ (POST) - desasociar")
    print()
    print("Tipos de pruebas intercaladas:")
    print("  - 40% Usuarios completos (todo el flujo)")
    print("  - 30% Solo UDID (no asocian)")
    print("  - 30% Reasociación (desasocian y reasocian)")
    print()
    
    # Configuración
    NUM_USUARIOS = int(os.getenv('TEST_NUM_USUARIOS', '100'))
    USUARIOS_SIMULTANEOS = int(os.getenv('TEST_USUARIOS_SIMULTANEOS', '50'))  # Reducido de 20 a 50 para mejor balance
    
    # Configurar límites aumentados para pruebas de carga
    os.environ['UDID_EXPIRATION_MINUTES'] = os.getenv('UDID_EXPIRATION_MINUTES', '60')  # 60 minutos para pruebas
    os.environ['UDID_MAX_ATTEMPTS'] = os.getenv('UDID_MAX_ATTEMPTS', '10')  # 10 intentos para pruebas
    
    print(f"Configuración actual:")
    print(f"  - Usuarios totales: {NUM_USUARIOS}")
    print(f"  - Usuarios simultáneos: {USUARIOS_SIMULTANEOS}")
    print(f"  - Tiempo de expiración UDID: {os.getenv('UDID_EXPIRATION_MINUTES', '60')} minutos")
    print(f"  - Máximo de intentos UDID: {os.getenv('UDID_MAX_ATTEMPTS', '10')}")
    print()
    
    # Ejecutar simulación
    print("Iniciando simulación...\n")
    stats = ejecutar_simulacion(NUM_USUARIOS, USUARIOS_SIMULTANEOS)
    
    # Mostrar resultados
    if stats:
        mostrar_resultados(stats)
        print("\n" + "="*70)
        print(f"  SIMULACIÓN COMPLETADA")
        print(f"  TOTAL DE USUARIOS SIMULADOS: {stats['total_usuarios']}")
        print("="*70 + "\n")
    else:
        if stats is None:
            print("[INFO] La simulación fue cancelada por el usuario.")
        else:
            print("[ERROR] La simulación falló.")
        print("\n" + "="*70)
        print(f"  SIMULACIÓN NO COMPLETADA")
        print("="*70 + "\n")

if __name__ == '__main__':
    main()

