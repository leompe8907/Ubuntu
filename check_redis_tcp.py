#!/usr/bin/env python
"""
Script simple para verificar el estado de Redis usando conexión TCP directa.
No requiere bibliotecas externas, solo socket estándar de Python.
Uso: python check_redis_tcp.py
"""
import socket
import os
import sys
from urllib.parse import urlparse

def print_header(text):
    """Imprime un encabezado formateado"""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def print_success(text):
    """Imprime un mensaje de éxito"""
    print(f"  ✅ {text}")

def print_error(text):
    """Imprime un mensaje de error"""
    print(f"  ❌ {text}")

def print_info(text):
    """Imprime información"""
    print(f"  ℹ️  {text}")

def get_redis_url():
    """Obtiene la URL de Redis desde variables de entorno o usa el default"""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis_url

def parse_redis_url(url):
    """Parsea la URL de Redis y retorna host, port, db"""
    parsed = urlparse(url)
    host = parsed.hostname or 'localhost'
    port = parsed.port or 6379
    db = int(parsed.path.lstrip('/')) if parsed.path else 0
    return host, port, db

def test_redis_ping(host, port, timeout=5):
    """Prueba la conexión a Redis usando PING"""
    try:
        # Crear socket TCP
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        # Conectar
        sock.connect((host, port))
        
        # Enviar comando PING
        sock.sendall(b"PING\r\n")
        
        # Recibir respuesta
        response = sock.recv(1024).decode('utf-8', errors='ignore')
        
        # Cerrar conexión
        sock.close()
        
        # Verificar respuesta
        if response.strip().upper() == '+PONG':
            return True, None
        else:
            return False, f"Respuesta inesperada: {response}"
            
    except socket.timeout:
        return False, "Timeout: No se pudo conectar en el tiempo esperado"
    except socket.gaierror as e:
        return False, f"Error de DNS: No se pudo resolver el host '{host}'"
    except ConnectionRefusedError:
        return False, f"Conexión rechazada: Redis no está escuchando en {host}:{port}"
    except Exception as e:
        return False, f"Error: {str(e)}"

def test_tcp_connection(host, port, timeout=5):
    """Prueba si el puerto está abierto"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def main():
    print_header("VERIFICACIÓN DE ESTADO DE REDIS")
    
    # 1. Obtener configuración
    print("\n1. CONFIGURACIÓN:")
    redis_url = get_redis_url()
    print_info(f"REDIS_URL: {redis_url}")
    
    host, port, db = parse_redis_url(redis_url)
    print_info(f"Host: {host}")
    print_info(f"Puerto: {port}")
    print_info(f"Base de datos: {db}")
    
    # 2. Test de puerto TCP
    print("\n2. TEST DE PUERTO TCP:")
    port_open = test_tcp_connection(host, port, timeout=5)
    
    if not port_open:
        print_error(f"Puerto {port} no está abierto en {host}")
        print("\n" + "="*70)
        print("  RESUMEN: Redis NO está activo - Puerto no accesible")
        print("="*70 + "\n")
        print_info("Posibles causas:")
        print_info("  - Redis no está corriendo")
        print_info("  - Redis está escuchando en otro puerto")
        print_info("  - Firewall bloqueando la conexión")
        print_info("  - Host incorrecto")
        sys.exit(1)
    else:
        print_success(f"Puerto {port} está abierto en {host}")
    
    # 3. Test de comando PING
    print("\n3. TEST DE COMANDO PING:")
    ping_ok, error = test_redis_ping(host, port, timeout=5)
    
    if not ping_ok:
        print_error(error or "PING falló")
        print("\n" + "="*70)
        print("  RESUMEN: Puerto abierto pero Redis no responde correctamente")
        print("="*70 + "\n")
        print_info("Posibles causas:")
        print_info("  - El puerto está abierto pero no es Redis")
        print_info("  - Redis está en modo protegido (requiere autenticación)")
        print_info("  - Redis está sobrecargado y no responde")
        sys.exit(1)
    else:
        print_success("Redis responde correctamente al comando PING")
    
    # Resumen final
    print("\n" + "="*70)
    print("  RESUMEN: Redis está ACTIVO y respondiendo correctamente")
    print("="*70 + "\n")
    print_info(f"Redis está escuchando en {host}:{port}")
    print_info("La conexión TCP funciona correctamente")
    print_info("Redis responde a comandos básicos")
    print("\n")
    sys.exit(0)

if __name__ == '__main__':
    main()

