#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script de simulación de carga con múltiples usuarios concurrentes.
Simula el flujo completo del proyecto usando las views de views.py (no automatico.py).

Flujo simulado:
1. Request UDID Manual (GET /udid/request-udid-manual/)
2. Validate Status (GET /udid/validate/?udid=XXX)
3. Authenticate with UDID (POST /udid/authenticate-with-udid/) - opcional si hay subscriber

Ejecutar: python test_carga_usuarios.py
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

# Configurar Django para acceder a la base de datos
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')
django.setup()

from udid.models import SubscriberInfo, UDIDAuthRequest
from django.utils import timezone

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
    'validaciones_exitosas': 0,
    'autenticaciones_exitosas': 0,
    'tiempo_total': 0,
    'tiempos_respuesta': [],
    'errores': defaultdict(int),
}

stats_lock = threading.Lock()
subscribers_lock = threading.Lock()
subscribers_disponibles = []

def cargar_subscribers_disponibles():
    """Cargar lista de subscribers disponibles desde la base de datos"""
    global subscribers_disponibles
    try:
        # Obtener subscribers que tienen SNs y no están asociados a UDIDs activos
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
        print(f"[INFO] Cargados {len(subscribers_disponibles)} subscribers disponibles")
        return len(subscribers_disponibles) > 0
    except Exception as e:
        print(f"[ERROR] Error cargando subscribers: {str(e)}")
        return False

def obtener_subscriber_disponible():
    """Obtener un subscriber disponible de forma thread-safe"""
    global subscribers_disponibles
    with subscribers_lock:
        if not subscribers_disponibles:
            # Recargar si se agotaron
            cargar_subscribers_disponibles()
        
        if subscribers_disponibles:
            subscriber_code, sn = subscribers_disponibles.pop(0)
            return subscriber_code, sn
        return None, None

def print_section(title):
    """Imprimir una sección"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def generar_device_fingerprint(usuario_id):
    """Generar un device fingerprint único para cada usuario simulado"""
    # Simular diferentes dispositivos
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

def simular_usuario(usuario_id, delay_inicial=0):
    """
    Simula un usuario completo siguiendo el flujo del proyecto.
    Retorna estadísticas del usuario.
    """
    user_stats = {
        'usuario_id': usuario_id,
        'udid_generado': False,
        'asociacion_exitosa': False,
        'validacion_exitosa': False,
        'autenticacion_exitosa': False,
        'rate_limited': False,
        'errores': [],
        'tiempo_total': 0,
    }
    
    start_time = time.time()
    
    # Delay inicial para distribuir la carga
    if delay_inicial > 0:
        time.sleep(delay_inicial)
    
    headers = get_headers(usuario_id)
    
    try:
        # PASO 1: Request UDID Manual
        try:
            response = requests.get(
                f'{API_BASE}/request-udid-manual/',
                headers=headers,
                timeout=10
            )
            response_time = time.time() - start_time
            
            with stats_lock:
                stats['tiempos_respuesta'].append(response_time)
            
            if response.status_code == 201:
                udid_data = response.json()
                udid = udid_data.get('udid')
                user_stats['udid_generado'] = True
                
                with stats_lock:
                    stats['requests_exitosos'] += 1
                    stats['udids_generados'] += 1
                
                # PASO 2: Obtener subscriber disponible y asociar UDID
                subscriber_code, sn = obtener_subscriber_disponible()
                
                if subscriber_code and sn:
                    time.sleep(random.uniform(0.1, 0.5))
                    
                    try:
                        # Asociar UDID con subscriber
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
                        
                        if associate_response.status_code == 200:
                            user_stats['asociacion_exitosa'] = True
                            with stats_lock:
                                stats['asociaciones_exitosas'] = stats.get('asociaciones_exitosas', 0) + 1
                        else:
                            user_stats['errores'].append(f'Associate error: {associate_response.status_code}')
                    
                    except Exception as e:
                        user_stats['errores'].append(f'Associate exception: {str(e)}')
                
                # PASO 3: Validate Status (después de asociar)
                time.sleep(random.uniform(0.1, 0.3))
                
                try:
                    validate_response = requests.get(
                        f'{API_BASE}/validate/',
                        params={'udid': udid},
                        headers=headers,
                        timeout=10
                    )
                    
                    if validate_response.status_code == 200:
                        user_stats['validacion_exitosa'] = True
                        with stats_lock:
                            stats['validaciones_exitosas'] += 1
                    
                except Exception as e:
                    user_stats['errores'].append(f'Validate error: {str(e)}')
                
                # PASO 4: Authenticate with UDID (ahora que está asociado debería funcionar)
                time.sleep(random.uniform(0.1, 0.3))
                
                try:
                    auth_response = requests.post(
                        f'{API_BASE}/authenticate-with-udid/',
                        json={
                            'udid': udid,
                            'app_type': random.choice(['android_tv', 'samsung_tv', 'mobile_app']),
                            'app_version': f'{random.randint(1, 3)}.{random.randint(0, 9)}',
                        },
                        headers=headers,
                        timeout=10
                    )
                    
                    if auth_response.status_code == 200:
                        user_stats['autenticacion_exitosa'] = True
                        with stats_lock:
                            stats['autenticaciones_exitosas'] += 1
                    
                except Exception as e:
                    # No es un error crítico si no hay subscriber asociado
                    pass
                    
            elif response.status_code == 429:
                user_stats['rate_limited'] = True
                with stats_lock:
                    stats['requests_rate_limited'] += 1
                    stats['errores']['rate_limit_exceeded'] += 1
            else:
                error_msg = f"Status {response.status_code}"
                user_stats['errores'].append(error_msg)
                with stats_lock:
                    stats['requests_error'] += 1
                    stats['errores'][error_msg] += 1
                    
        except requests.exceptions.Timeout:
            error_msg = "timeout"
            user_stats['errores'].append(error_msg)
            with stats_lock:
                stats['requests_error'] += 1
                stats['errores'][error_msg] += 1
        except requests.exceptions.ConnectionError:
            error_msg = "connection_error"
            user_stats['errores'].append(error_msg)
            with stats_lock:
                stats['requests_error'] += 1
                stats['errores'][error_msg] += 1
        except Exception as e:
            error_msg = f"exception: {str(e)}"
            user_stats['errores'].append(error_msg)
            with stats_lock:
                stats['requests_error'] += 1
                stats['errores'][error_msg] += 1
    
    finally:
        user_stats['tiempo_total'] = time.time() - start_time
    
    return user_stats

def ejecutar_simulacion(num_usuarios, usuarios_simultaneos=10):
    """
    Ejecuta la simulación con múltiples usuarios.
    
    Args:
        num_usuarios: Número total de usuarios a simular
        usuarios_simultaneos: Número máximo de usuarios concurrentes
    """
    print_section(f"SIMULACIÓN DE CARGA - {num_usuarios} USUARIOS")
    
    # Cargar subscribers disponibles
    print("Cargando subscribers disponibles desde la base de datos...")
    if not cargar_subscribers_disponibles():
        print("[ERROR] No se pudieron cargar subscribers. La simulación puede fallar.")
    else:
        print(f"[OK] {len(subscribers_disponibles)} subscribers disponibles cargados\n")
    
    print(f"Configuración:")
    print(f"  - Total de usuarios: {num_usuarios}")
    print(f"  - Usuarios simultáneos: {usuarios_simultaneos}")
    print(f"  - Base URL: {BASE_URL}")
    print(f"  - Subscribers disponibles: {len(subscribers_disponibles)}")
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
        'autenticaciones_exitosas': 0,
        'tiempo_total': 0,
        'tiempos_respuesta': [],
        'errores': defaultdict(int),
    }
    
    inicio = time.time()
    
    # Distribuir usuarios en el tiempo para simular carga realista
    # Los primeros usuarios empiezan inmediatamente, luego se distribuyen
    delays = [random.uniform(0, 2) for _ in range(num_usuarios)]
    
    # Ejecutar con ThreadPoolExecutor para controlar concurrencia
    with ThreadPoolExecutor(max_workers=usuarios_simultaneos) as executor:
        # Enviar todas las tareas
        futures = {
            executor.submit(simular_usuario, i+1, delays[i]): i+1 
            for i in range(num_usuarios)
        }
        
        # Procesar resultados conforme se completan
        completados = 0
        for future in as_completed(futures):
            usuario_id = futures[future]
            try:
                user_stats = future.result()
                completados += 1
                
                # Mostrar progreso cada 10 usuarios o al final
                if completados % max(1, num_usuarios // 10) == 0 or completados == num_usuarios:
                    print(f"  Progreso: {completados}/{num_usuarios} usuarios completados "
                          f"({completados*100//num_usuarios}%)")
                    
            except Exception as e:
                print(f"  Error procesando usuario {usuario_id}: {str(e)}")
    
    tiempo_total = time.time() - inicio
    stats['tiempo_total'] = tiempo_total
    
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
    
    print(f"Operaciones Completadas:")
    print(f"  - UDIDs generados: {stats['udids_generados']}")
    print(f"  - Asociaciones exitosas: {stats.get('asociaciones_exitosas', 0)}")
    print(f"  - Validaciones exitosas: {stats['validaciones_exitosas']}")
    print(f"  - Autenticaciones exitosas: {stats['autenticaciones_exitosas']}")
    print()
    
    print(f"Rendimiento:")
    print(f"  - Tiempo total: {stats['tiempo_total']:.2f} segundos")
    if total > 0:
        print(f"  - Usuarios por segundo: {total/stats['tiempo_total']:.2f}")
    
    if stats['tiempos_respuesta']:
        tiempos = stats['tiempos_respuesta']
        print(f"  - Tiempo de respuesta promedio: {sum(tiempos)/len(tiempos):.3f}s")
        print(f"  - Tiempo de respuesta mínimo: {min(tiempos):.3f}s")
        print(f"  - Tiempo de respuesta máximo: {max(tiempos):.3f}s")
    print()
    
    if stats['errores']:
        print(f"Errores Encontrados:")
        for error, count in sorted(stats['errores'].items(), key=lambda x: x[1], reverse=True):
            print(f"  - {error}: {count}")
        print()
    
    print(f"Fecha/Hora de finalización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

def main():
    """Función principal"""
    print("\n" + "="*70)
    print("  SIMULADOR DE CARGA - MÚLTIPLES USUARIOS CONCURRENTES")
    print("="*70)
    print("\nEste script simula múltiples usuarios siguiendo el flujo completo:")
    print("  1. Request UDID Manual")
    print("  2. Validate and Associate UDID (con subscriber_code y SN disponibles)")
    print("  3. Validate Status")
    print("  4. Authenticate with UDID")
    print()
    
    # Configuración de la simulación
    # Puedes ajustar estos valores desde variables de entorno
    NUM_USUARIOS = int(os.getenv('TEST_NUM_USUARIOS', '100'))  # Total de usuarios a simular
    USUARIOS_SIMULTANEOS = int(os.getenv('TEST_USUARIOS_SIMULTANEOS', '20'))  # Máximo de usuarios concurrentes
    
    print(f"Configuración actual:")
    print(f"  - Usuarios totales: {NUM_USUARIOS}")
    print(f"  - Usuarios simultáneos: {USUARIOS_SIMULTANEOS}")
    print()
    
    # Ejecutar simulación directamente
    print("Iniciando simulación...\n")
    stats = ejecutar_simulacion(NUM_USUARIOS, USUARIOS_SIMULTANEOS)
    
    # Mostrar resultados
    mostrar_resultados(stats)
    
    print("\n" + "="*70)
    print(f"  SIMULACIÓN COMPLETADA - {stats['total_usuarios']} USUARIOS SIMULADOS")
    print("="*70 + "\n")

if __name__ == '__main__':
    main()

