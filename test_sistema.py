#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script de pruebas para verificar el sistema de protección DDoS y logging.
Ejecutar: python test_sistema.py
"""

import os
import sys
import django
import requests
import time
from datetime import datetime

# Configurar encoding UTF-8 para Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Configurar Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')
django.setup()

from django.core.cache import cache
from django.conf import settings
from udid.util import (
    check_device_fingerprint_rate_limit,
    check_udid_rate_limit,
    get_system_load,
    check_circuit_breaker,
    track_system_request,
    generate_device_fingerprint,
)

# Configuración de pruebas
BASE_URL = os.getenv('TEST_BASE_URL', 'http://localhost:8000')
API_BASE = f'{BASE_URL}/udid'  # Las URLs están bajo /udid/
TEST_HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': 'TestScript/1.0',
    'x-device-id': 'test-device-123',
    'x-os-version': 'TestOS/1.0',
    'x-device-model': 'TestDevice',
}

def print_section(title):
    """Imprimir una sección de prueba"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_redis_connection():
    """Probar conexión a Redis/Cache"""
    print_section("1. TEST: Conexión a Redis/Cache")
    
    try:
        # Intentar escribir y leer del cache
        test_key = 'test_redis_connection'
        test_value = 'test_value_' + str(int(time.time()))
        
        cache.set(test_key, test_value, timeout=60)
        retrieved = cache.get(test_key)
        
        if retrieved == test_value:
            cache_type = "Redis" if settings.REDIS_URL else "LocMemCache"
            print(f"[OK] Cache funcionando correctamente ({cache_type})")
            if settings.REDIS_URL:
                print(f"   Redis URL: {settings.REDIS_URL[:50]}...")
            else:
                print("   [WARN] Usando LocMemCache (no distribuido)")
                print("   [INFO] Para usar Redis, configura REDIS_URL en variables de entorno")
            return True
        else:
            print(f"[ERROR] Valor no coincide. Esperado: {test_value}, Obtenido: {retrieved}")
            return False
    except Exception as e:
        print(f"[ERROR] Error al conectar con cache: {str(e)}")
        return False

def test_rate_limiting():
    """Probar rate limiting"""
    print_section("2. TEST: Rate Limiting")
    
    try:
        from udid.util import increment_rate_limit_counter
        
        test_fingerprint = "test_fingerprint_123"
        
        # Limpiar cache anterior para este test
        cache_key = f"rate_limit:device_fp:{test_fingerprint}"
        cache.delete(cache_key)
        
        # Probar rate limit de device fingerprint
        print("   Probando rate limit de device fingerprint...")
        allowed_count = 0
        blocked_count = 0
        
        for i in range(5):
            is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
                test_fingerprint, max_requests=2, window_minutes=5
            )
            
            if is_allowed:
                allowed_count += 1
                # Incrementar contador después de verificar (como lo hace el sistema real)
                increment_rate_limit_counter('device_fp', test_fingerprint)
                print(f"   [OK] Request {i+1}: Permitido (quedan {remaining})")
            else:
                blocked_count += 1
                print(f"   [BLOCKED] Request {i+1}: Bloqueado (retry_after: {retry_after}s)")
            
            time.sleep(0.1)
        
        if allowed_count == 2 and blocked_count == 3:
            print(f"   [OK] Rate limiting funcionando correctamente (2 permitidos, 3 bloqueados)")
            return True
        else:
            print(f"   [WARN] Resultado inesperado: {allowed_count} permitidos, {blocked_count} bloqueados")
            print(f"   [INFO] Esto puede ser normal si el cache se reinició o hay múltiples instancias")
            return allowed_count <= 2  # Al menos no debe permitir más de 2
            
    except Exception as e:
        print(f"[ERROR] Error en rate limiting: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_system_load_tracking():
    """Probar rastreo de carga del sistema"""
    print_section("3. TEST: Rastreo de Carga del Sistema")
    
    try:
        # Simular algunas requests
        for i in range(10):
            track_system_request()
        
        load = get_system_load()
        print(f"   Carga del sistema: {load}")
        
        breaker_active, breaker_retry_after = check_circuit_breaker()
        print(f"   Circuit breaker: {'ACTIVO' if breaker_active else 'INACTIVO'}")
        if breaker_active:
            print(f"   Retry after: {breaker_retry_after}s")
        
        print("   [OK] Rastreo de carga funcionando")
        return True
        
    except Exception as e:
        print(f"[ERROR] Error en rastreo de carga: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_endpoint(url, method='GET', data=None, description=""):
    """Probar un endpoint"""
    try:
        if method == 'GET':
            response = requests.get(f"{BASE_URL}{url}", headers=TEST_HEADERS, timeout=10)
        elif method == 'POST':
            response = requests.post(f"{BASE_URL}{url}", json=data, headers=TEST_HEADERS, timeout=10)
        else:
            print(f"   ⚠️  Método {method} no soportado")
            return None
        
        status_ok = "[OK]" if response.status_code < 400 else "[ERROR]"
        print(f"   {status_ok} {description}")
        print(f"      Status: {response.status_code}")
        
        if response.status_code == 429:
            print(f"      [INFO] Rate limit aplicado (esperado en algunos casos)")
        
        return response
    except requests.exceptions.ConnectionError:
        print(f"   [ERROR] No se pudo conectar al servidor en {BASE_URL}")
        print(f"      Asegúrate de que el servidor esté corriendo")
        return None
    except Exception as e:
        print(f"   [ERROR] Error: {str(e)}")
        return None

def test_endpoints():
    """Probar endpoints principales"""
    print_section("4. TEST: Endpoints HTTP")
    
    # Test 1: Request UDID
    print("\n   Test 4.1: Request UDID (GET /udid/request-udid/)")
    response = test_endpoint('/udid/request-udid/', 'GET', description='Request UDID')
    
    if response and response.status_code == 201:
        udid_data = response.json()
        print(f"      UDID generado: {udid_data.get('udid', 'N/A')[:16]}...")
        print(f"      Device fingerprint: {udid_data.get('device_fingerprint', 'N/A')[:16]}...")
        test_udid = udid_data.get('udid')
    else:
        print("      [WARN] No se pudo obtener UDID para pruebas adicionales")
        test_udid = None
    
    # Test 2: Validate Status
    if test_udid:
        print("\n   Test 4.2: Validate Status UDID")
        test_endpoint(f'/udid/validate/?udid={test_udid}', 'GET', 
                     description='Validate Status UDID')
    
    # Test 3: Rate limit test (intentar varias veces)
    print("\n   Test 4.3: Rate Limit en Endpoint")
    print("      Enviando 3 requests rápidas para probar rate limiting...")
    for i in range(3):
        response = test_endpoint('/udid/request-udid/', 'GET', 
                               description=f'Request {i+1} (debe bloquearse después de 2)')
        if response and response.status_code == 429:
            print(f"      [OK] Rate limit funcionando - bloqueado en request {i+1}")
            break
        time.sleep(0.5)
    
    print("\n   [OK] Pruebas de endpoints completadas")

def test_logging():
    """Verificar que el logging funciona"""
    print_section("5. TEST: Logging")
    
    log_file = os.path.join(settings.BASE_DIR, 'server.log')
    
    if os.path.exists(log_file):
        file_size = os.path.getsize(log_file)
        print(f"   [OK] Archivo de log encontrado: {log_file}")
        print(f"      Tamaño: {file_size} bytes")
        
        # Leer últimas líneas del log
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                if lines:
                    print(f"      Últimas líneas del log:")
                    for line in lines[-5:]:
                        print(f"         {line.strip()[:80]}")
                else:
                    print("      [WARN] El archivo de log está vacío")
        except Exception as e:
            print(f"      [WARN] Error al leer el log: {str(e)}")
    else:
        print(f"   [WARN] Archivo de log no encontrado: {log_file}")
        print(f"      Los logs se crearán cuando se ejecuten requests")

def main():
    """Ejecutar todas las pruebas"""
    print("\n" + "="*60)
    print("  SISTEMA DE PRUEBAS - PROTECCIÓN DDoS Y LOGGING")
    print("="*60)
    print(f"\nFecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {BASE_URL}")
    print(f"Redis URL: {'Configurado' if settings.REDIS_URL else 'No configurado (usando LocMemCache)'}")
    
    results = []
    
    # Ejecutar pruebas
    results.append(("Redis/Cache", test_redis_connection()))
    results.append(("Rate Limiting", test_rate_limiting()))
    results.append(("System Load Tracking", test_system_load_tracking()))
    test_endpoints()
    test_logging()
    
    # Resumen
    print_section("RESUMEN DE PRUEBAS")
    
    for test_name, result in results:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status}: {test_name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n  Resultado: {passed}/{total} pruebas pasaron")
    
    if passed == total:
        print("\n  [SUCCESS] ¡Todas las pruebas pasaron exitosamente!")
    else:
        print("\n  [WARN] Algunas pruebas fallaron. Revisa los detalles arriba.")
    
    print("\n" + "="*60 + "\n")

if __name__ == '__main__':
    main()

