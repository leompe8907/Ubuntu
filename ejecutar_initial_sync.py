#!/usr/bin/env python
"""
Ejecutar tarea initial_sync_all_data y monitorear su progreso
"""
import sys
import os
import time

# Configurar Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

import django
django.setup()

from udid.tasks import initial_sync_all_data
from celery.result import AsyncResult
from ubuntu.celery import app

print("=" * 60)
print("EJECUTANDO TAREA: initial_sync_all_data")
print("=" * 60)

# Verificar que hay un worker activo
print("\n1. Verificando worker activo...")
try:
    inspect = app.control.inspect()
    active = inspect.active()
    if active:
        print(f"   ✅ Worker activo: {list(active.keys())[0]}")
    else:
        print("   ⚠️  No hay workers activos")
        print("   Iniciando worker... (debe estar corriendo en otra terminal)")
except Exception as e:
    print(f"   ⚠️  No se pudo verificar workers: {e}")

# Ejecutar la tarea
print("\n2. Ejecutando tarea initial_sync_all_data...")
try:
    result = initial_sync_all_data.delay()
    task_id = result.id
    
    print(f"   ✅ Tarea enviada exitosamente")
    print(f"   Task ID: {task_id}")
    print(f"   Estado inicial: {result.state}")
    
    # Monitorear progreso
    print("\n3. Monitoreando ejecución...")
    print("   (Esto puede tomar varios minutos u horas dependiendo de la cantidad de datos)")
    print("   Presiona Ctrl+C para detener el monitoreo (la tarea continuará ejecutándose)\n")
    
    start_time = time.time()
    last_state = result.state
    check_count = 0
    
    while not result.ready():
        try:
            result.reload()
        except:
            pass
        
        elapsed = int(time.time() - start_time)
        minutes = elapsed // 60
        seconds = elapsed % 60
        
        if result.state != last_state:
            print(f"   [{minutes:02d}:{seconds:02d}] Estado cambió: {last_state} → {result.state}")
            last_state = result.state
        
        check_count += 1
        if check_count % 10 == 0:  # Cada 10 verificaciones mostrar estado
            print(f"   [{minutes:02d}:{seconds:02d}] Estado: {result.state} - Esperando...")
        
        time.sleep(2)  # Verificar cada 2 segundos
        
    # Tarea completada
    elapsed = int(time.time() - start_time)
    minutes = elapsed // 60
    seconds = elapsed % 60
    
    print(f"\n   ✅ Tarea completada en {minutes} minutos y {seconds} segundos")
    print(f"   Estado final: {result.state}")
    
    if result.successful():
        print(f"   ✅ Tarea exitosa")
        if result.result:
            print(f"   Resultado: {result.result}")
    else:
        print(f"   ❌ Tarea falló")
        print(f"   Error: {result.info}")
        if hasattr(result, 'traceback') and result.traceback:
            print(f"\n   Traceback:")
            print(result.traceback)
    
except KeyboardInterrupt:
    print(f"\n\n   ⚠️  Monitoreo detenido por el usuario")
    print(f"   La tarea continúa ejecutándose en background")
    print(f"   Task ID: {task_id}")
    print(f"   Para verificar el estado más tarde:")
    print(f"   python -c \"from celery.result import AsyncResult; from ubuntu.celery import app; result = AsyncResult('{task_id}', app=app); print(f'Estado: {{result.state}}')\"")
    
except Exception as e:
    print(f"\n   ❌ Error ejecutando tarea: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)





