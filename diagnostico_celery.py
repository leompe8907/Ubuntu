#!/usr/bin/env python
"""
Script de diagnóstico rápido de Celery
Ejecuta verificaciones y muestra el estado actual
"""
import sys
import os

# Configurar Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

import django
django.setup()

from ubuntu.celery import app
from celery.result import AsyncResult
import time

print("=" * 60)
print("DIAGNÓSTICO DE CELERY")
print("=" * 60)

# 1. Verificar conexión a Redis
print("\n1. Verificando conexión a Redis...")
try:
    with app.connection() as conn:
        conn.ensure_connection(max_retries=3)
        print("   ✅ Redis: Conectado")
except Exception as e:
    print(f"   ❌ Redis: Error - {e}")
    sys.exit(1)

# 2. Verificar workers activos
print("\n2. Verificando workers activos...")
try:
    inspect = app.control.inspect()
    active = inspect.active()
    if active:
        print(f"   ✅ Workers activos: {len(active)}")
        for worker_name in active.keys():
            print(f"      - {worker_name}")
    else:
        print("   ⚠️  No hay workers activos")
        print("      Inicia un worker con: celery -A ubuntu worker --loglevel=info --pool=solo")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 3. Ver tareas registradas
print("\n3. Verificando tareas registradas...")
try:
    inspect = app.control.inspect()
    registered = inspect.registered()
    if registered:
        all_tasks = set()
        for tasks in registered.values():
            all_tasks.update(tasks)
        print(f"   ✅ Tareas registradas: {len(all_tasks)}")
        print(f"      Tareas principales:")
        for task in sorted(all_tasks):
            if 'udid.tasks' in task or 'debug_task' in task:
                print(f"         - {task}")
    else:
        print("   ⚠️  No se encontraron tareas registradas")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 4. Ejecutar tarea de prueba
print("\n4. Ejecutando tarea de prueba...")
try:
    from ubuntu.celery import debug_task
    
    print("   📤 Enviando tarea...")
    result = debug_task.delay()
    task_id = result.id
    print(f"   ✅ Tarea enviada - Task ID: {task_id}")
    print(f"   Estado inicial: {result.state}")
    
    # Esperar y verificar
    print("\n   ⏳ Esperando 5 segundos...")
    for i in range(5):
        time.sleep(1)
        try:
            result.reload()
        except:
            pass
        print(f"      {i+1}s - Estado: {result.state}")
    
    print(f"\n   Estado final: {result.state}")
    if result.ready():
        if result.successful():
            print("   ✅ Tarea completada exitosamente")
        else:
            print(f"   ❌ Tarea falló: {result.info}")
    else:
        print("   ⚠️  Tarea aún en cola o ejecutándose")
        print("      Verifica los logs del worker")
        
except Exception as e:
    print(f"   ❌ Error ejecutando tarea: {e}")
    import traceback
    traceback.print_exc()

# 5. Ver estadísticas del worker
print("\n5. Estadísticas del worker...")
try:
    inspect = app.control.inspect()
    stats = inspect.stats()
    if stats:
        for worker_name, worker_stats in stats.items():
            print(f"   Worker: {worker_name}")
            if 'total' in worker_stats:
                total = worker_stats['total']
                print(f"      Tareas ejecutadas: {sum(total.values())}")
                for task_name, count in total.items():
                    print(f"         - {task_name}: {count}")
except Exception as e:
    print(f"   ⚠️  No se pudieron obtener estadísticas: {e}")

print("\n" + "=" * 60)
print("DIAGNÓSTICO COMPLETADO")
print("=" * 60)
print("\n💡 Si la tarea quedó en PENDING:")
print("   1. Verifica que el worker esté corriendo")
print("   2. Revisa los logs del worker en la terminal donde lo iniciaste")
print("   3. Reinicia el worker si es necesario")



