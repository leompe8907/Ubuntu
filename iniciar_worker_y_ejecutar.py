#!/usr/bin/env python
"""
Iniciar worker y ejecutar tarea initial_sync_all_data
"""
import subprocess
import sys
import os
import time
import threading

# Configurar Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

import django
django.setup()

from udid.tasks import initial_sync_all_data
from celery.result import AsyncResult
from ubuntu.celery import app

def iniciar_worker():
    """Iniciar worker en un proceso separado"""
    print("Iniciando worker de Celery...")
    worker_process = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "ubuntu", "worker", "--loglevel=info", "--pool=solo"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    return worker_process

print("=" * 60)
print("INICIANDO WORKER Y EJECUTANDO TAREA")
print("=" * 60)

# Iniciar worker
print("\n1. Iniciando worker...")
worker = iniciar_worker()

# Esperar a que el worker se inicie
print("   Esperando 5 segundos para que el worker se inicie...")
time.sleep(5)

# Verificar worker
print("\n2. Verificando worker...")
try:
    inspect = app.control.inspect()
    active = inspect.active()
    if active:
        print(f"   ✅ Worker activo: {list(active.keys())[0]}")
    else:
        print("   ⚠️  Worker aún no responde, esperando más tiempo...")
        time.sleep(5)
        active = inspect.active()
        if active:
            print(f"   ✅ Worker activo: {list(active.keys())[0]}")
        else:
            print("   ❌ Worker no responde")
            worker.terminate()
            sys.exit(1)
except Exception as e:
    print(f"   ⚠️  Error verificando worker: {e}")

# Ejecutar tarea
print("\n3. Ejecutando tarea initial_sync_all_data...")
try:
    result = initial_sync_all_data.delay()
    task_id = result.id
    
    print(f"   ✅ Tarea enviada")
    print(f"   Task ID: {task_id}")
    print(f"   Estado inicial: {result.state}")
    
    # Monitorear
    print("\n4. Monitoreando ejecución...")
    print("   (La tarea puede tardar mucho tiempo)")
    print("   Presiona Ctrl+C para detener el monitoreo\n")
    
    start_time = time.time()
    
    while not result.ready():
        try:
            result.reload()
        except:
            pass
        
        elapsed = int(time.time() - start_time)
        minutes = elapsed // 60
        seconds = elapsed % 60
        
        if elapsed % 30 == 0:  # Mostrar cada 30 segundos
            print(f"   [{minutes:02d}:{seconds:02d}] Estado: {result.state}")
        
        time.sleep(2)
    
    # Completada
    elapsed = int(time.time() - start_time)
    minutes = elapsed // 60
    seconds = elapsed % 60
    
    print(f"\n   ✅ Tarea completada en {minutes}m {seconds}s")
    print(f"   Estado: {result.state}")
    
    if result.successful():
        print(f"   ✅ Exitoso")
    else:
        print(f"   ❌ Falló: {result.info}")
        
except KeyboardInterrupt:
    print(f"\n\n   Monitoreo detenido")
    print(f"   Task ID: {task_id}")
    print(f"   Worker sigue corriendo")
except Exception as e:
    print(f"   ❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    print("\n   Para detener el worker, presiona Ctrl+C o cierra esta ventana")





