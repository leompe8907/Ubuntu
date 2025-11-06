#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script avanzado de simulación de carga con múltiples usuarios concurrentes.
Pruebas intercaladas que simulan diferentes escenarios de uso.

Flujo completo:
1. request-udid-manual/ -> solicitar el udid
2. validate-and-associate-udid/ -> asociar el udid a un subscriber code - sn
3. validate/ -> validar la asociacion
4. disassociate-udid/ -> desasociar

Ejecutar: python test_carga_avanzado.py
"""

import os
import sys
import requests
import time
import random
import threading
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

# Configurar Django para acceder a la base de datos
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')
django.setup()

from udid.models import SubscriberInfo, UDIDAuthRequest
from django.utils import timezone
from django.contrib.auth.models import User

# Configuración
BASE_URL = os.getenv('TEST_BASE_URL', 'http://localhost:8000')
API_BASE = f'{BASE_URL}/udid'

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
    'autenticaciones_exitosas': 0,
    'tiempo_total': 0,
    'tiempos_respuesta': [],
    'tiempos_request_udid': [],
    'tiempos_associate': [],
    'tiempos_validate': [],
    'tiempos_disassociate': [],
    'errores': defaultdict(int),
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
}

stats_lock = threading.Lock()
subscribers_lock = threading.Lock()
subscribers_disponibles = []
udids_generados = []  # Lista de UDIDs generados para pruebas de desasociación
udids_lock = threading.Lock()
jwt_token = None  # Token JWT para autenticación en disassociate
jwt_lock = threading.Lock()

def obtener_token_jwt():
    """Obtener token JWT para autenticación"""
    global jwt_token
    
    with jwt_lock:
        if jwt_token:
            return jwt_token
        
        try:
            # Intentar obtener un usuario existente o crear uno de prueba
            test_user, created = User.objects.get_or_create(
                username='test_user_load',
                defaults={
                    'email': 'test@load.com',
                    'password': 'pbkdf2_sha256$600000$test$test',  # Password hash de prueba
                    'is_active': True,
                }
            )
            
            # Si se creó, necesitamos un password real
            if created:
                test_user.set_password('test_password_123')
                test_user.save()
            
            # Hacer login para obtener token
            login_response = requests.post(
                f'{BASE_URL}/udid/auth/login/',
                json={
                    'username': 'test_user_load',
                    'password': 'test_password_123'
                },
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if login_response.status_code == 200:
                data = login_response.json()
                jwt_token = data.get('access')
                return jwt_token
            else:
                print(f"[WARN] No se pudo obtener token JWT: {login_response.status_code}")
                return None
        except Exception as e:
            print(f"[WARN] Error obteniendo token JWT: {str(e)}")
            return None

def obtener_metricas_sistema():
    """Obtener métricas actuales del sistema"""
    if not PSUTIL_AVAILABLE:
        return {'cpu': 0, 'memoria_mb': 0, 'memoria_percent': 0}
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memoria = psutil.virtual_memory()
        return {
            'cpu': cpu_percent,
            'memoria_mb': memoria.used / 1024 / 1024,  # MB
            'memoria_percent': memoria.percent,
        }
    except Exception:
        return {'cpu': 0, 'memoria_mb': 0, 'memoria_percent': 0}

def iniciar_monitoreo():
    """Iniciar monitoreo de rendimiento del servidor"""
    global server_metrics
    metricas = obtener_metricas_sistema()
    server_metrics['cpu_inicial'] = metricas['cpu']
    server_metrics['memoria_inicial'] = metricas['memoria_mb']
    server_metrics['cpu_maximo'] = metricas['cpu']
    server_metrics['memoria_maxima'] = metricas['memoria_mb']

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
        
        with stats_lock:
            stats['tiempos_request_udid'].append(step_time)
        
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
            
            with stats_lock:
                stats['tiempos_associate'].append(step_time)
            
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
            
            with stats_lock:
                stats['tiempos_validate'].append(step_time)
            
            if validate_response.status_code == 200:
                user_stats['validacion_exitosa'] = True
                with stats_lock:
                    stats['validaciones_exitosas'] += 1
        
        # PASO 4: Desasociar
        if subscriber_code and sn:
            time.sleep(random.uniform(0.1, 0.3))
            step_start = time.time()
            
            # Obtener token JWT para autenticación
            token = obtener_token_jwt()
            headers_auth = headers.copy()
            if token:
                headers_auth['Authorization'] = f'Bearer {token}'
            
            disassociate_response = requests.post(
                f'{API_BASE}/disassociate-udid/',
                json={
                    'udid': udid,
                },
                headers=headers_auth,
                timeout=10
            )
            step_time = time.time() - step_start
            
            with stats_lock:
                stats['tiempos_disassociate'].append(step_time)
            
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
        
        if response.status_code == 201:
            udid_data = response.json()
            udid = udid_data.get('udid')
            user_stats['udid_generado'] = True
            agregar_udid_generado(udid)
            
            with stats_lock:
                stats['requests_exitosos'] += 1
                stats['udids_generados'] += 1
                stats['usuarios_solo_udid'] += 1
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
        
        # Obtener token JWT para autenticación
        token = obtener_token_jwt()
        headers_auth = headers.copy()
        if token:
            headers_auth['Authorization'] = f'Bearer {token}'
        
        disassociate_response = requests.post(
            f'{API_BASE}/disassociate-udid/',
            json={'udid': udid},
            headers=headers_auth,
            timeout=10
        )
        step_time = time.time() - step_start
        
        with stats_lock:
            stats['tiempos_disassociate'].append(step_time)
        
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
            
            if associate_response.status_code == 200:
                user_stats['reasociacion_exitosa'] = True
                with stats_lock:
                    stats['asociaciones_exitosas'] += 1
                    stats['usuarios_reasociacion'] += 1
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

def ejecutar_simulacion(num_usuarios, usuarios_simultaneos=10):
    """Ejecuta la simulación con múltiples usuarios"""
    print_section(f"SIMULACIÓN DE CARGA - {num_usuarios} USUARIOS")
    
    # Cargar subscribers disponibles
    print("Cargando subscribers disponibles desde la base de datos...")
    if not cargar_subscribers_disponibles():
        print("[ERROR] No se pudieron cargar subscribers. La simulación puede fallar.")
    else:
        print(f"[OK] {len(subscribers_disponibles)} subscribers disponibles cargados")
    
    # Obtener token JWT para disassociate
    print("Obteniendo token JWT para autenticación...")
    token = obtener_token_jwt()
    if token:
        print(f"[OK] Token JWT obtenido\n")
    else:
        print(f"[WARN] No se pudo obtener token JWT. Las desasociaciones pueden fallar\n")
    
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
    
    # Resetear estadísticas
    global stats
    stats = {
        'total_usuarios': num_usuarios,
        'requests_exitosos': 0,
        'requests_rate_limited': 0,
        'requests_error': 0,
        'udids_generados': 0,
        'asociaciones_exitosas': 0,
        'validaciones_exitosas': 0,
        'desasociaciones_exitosas': 0,
        'autenticaciones_exitosas': 0,
        'tiempo_total': 0,
        'tiempos_respuesta': [],
        'tiempos_request_udid': [],
        'tiempos_associate': [],
        'tiempos_validate': [],
        'tiempos_disassociate': [],
        'errores': defaultdict(int),
        'usuarios_completos': 0,
        'usuarios_solo_udid': 0,
        'usuarios_reasociacion': 0,
    }
    
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
    print("\nEste script simula múltiples usuarios con diferentes comportamientos:")
    print("  1. request-udid-manual/ -> solicitar el udid")
    print("  2. validate-and-associate-udid/ -> asociar el udid a un subscriber code - sn")
    print("  3. validate/ -> validar la asociacion")
    print("  4. disassociate-udid/ -> desasociar")
    print()
    print("Tipos de pruebas intercaladas:")
    print("  - 40% Usuarios completos (todo el flujo)")
    print("  - 30% Solo UDID (no asocian)")
    print("  - 30% Reasociación (desasocian y reasocian)")
    print()
    
    # Configuración
    NUM_USUARIOS = int(os.getenv('TEST_NUM_USUARIOS', '100'))
    USUARIOS_SIMULTANEOS = int(os.getenv('TEST_USUARIOS_SIMULTANEOS', '20'))
    
    print(f"Configuración actual:")
    print(f"  - Usuarios totales: {NUM_USUARIOS}")
    print(f"  - Usuarios simultáneos: {USUARIOS_SIMULTANEOS}")
    print()
    
    # Ejecutar simulación
    print("Iniciando simulación...\n")
    stats = ejecutar_simulacion(NUM_USUARIOS, USUARIOS_SIMULTANEOS)
    
    # Mostrar resultados
    mostrar_resultados(stats)
    
    print("\n" + "="*70)
    print(f"  SIMULACIÓN COMPLETADA")
    print(f"  TOTAL DE USUARIOS SIMULADOS: {stats['total_usuarios']}")
    print("="*70 + "\n")

if __name__ == '__main__':
    main()

