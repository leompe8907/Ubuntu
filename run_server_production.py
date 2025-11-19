#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para ejecutar el servidor Django con Daphne optimizado para producción/carga.
Daphne es el servidor ASGI que maneja tanto HTTP como WebSockets.
"""
import os
import sys
import multiprocessing

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

# Calcular número de workers basado en CPU
# Fórmula recomendada: (2 x CPU cores) + 1
cpu_count = multiprocessing.cpu_count()
workers = (2 * cpu_count) + 1

# Configuración de Daphne
host = os.getenv('SERVER_HOST', '127.0.0.1')
port = int(os.getenv('SERVER_PORT', '8000'))
bind = f"{host}:{port}"

# Opciones de Daphne para mejor rendimiento
daphne_options = [
    'daphne',
    '-b', host,
    '-p', str(port),
    '--access-log', '-',  # Logs a stdout
    '--proxy-headers',  # Para manejar correctamente headers de proxy
    '--http-timeout', '60',  # Timeout HTTP de 60 segundos
    '--websocket-timeout', '60',  # Timeout WebSocket de 60 segundos
    'ubuntu.asgi:application',
]

print(f"🚀 Iniciando servidor Daphne optimizado para carga")
print(f"   Host: {host}")
print(f"   Port: {port}")
print(f"   CPU cores: {cpu_count}")
print(f"   Workers recomendados: {workers} (Daphne maneja esto internamente)")
print(f"   Para más workers, ejecuta múltiples instancias con diferentes puertos")
print()

# Ejecutar Daphne
os.execvp('daphne', daphne_options)

