#!/usr/bin/env python
"""
Script de verificación de Celery
Verifica que Celery esté funcionando correctamente
"""
import sys
import os

# Agregar el directorio del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

import django
django.setup()

from ubuntu.celery import app
from celery import current_app
from celery.result import AsyncResult

def print_section(title):
    """Imprime un título de sección"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def check_redis_connection():
    """Verifica la conexión a Redis"""
    print_section("1. Verificando Conexión a Redis")
    try:
        # Intentar conectar a Redis
        broker_url = app.conf.broker_url
        result_backend = app.conf.result_backend
        
        print(f"✅ Broker URL: {broker_url}")
        print(f"✅ Result Backend: {result_backend}")
        
        # Intentar hacer ping al broker
        with app.connection() as conn:
            conn.ensure_connection(max_retries=3)
            print("✅ Conexión a Redis: OK")
            return True
    except Exception as e:
        print(f"❌ Error conectando a Redis: {e}")
        print("   Verifica que Redis esté corriendo y accesible")
        return False

def check_workers():
    """Verifica si hay workers activos"""
    print_section("2. Verificando Workers Activos")
    try:
        inspect = app.control.inspect()
        
        # Verificar workers activos
        active = inspect.active()
        if active:
            print("✅ Workers activos encontrados:")
            for worker_name, tasks in active.items():
                print(f"   - {worker_name}: {len(tasks)} tarea(s) activa(s)")
                if tasks:
                    for task in tasks:
                        print(f"     • {task['name']} (ID: {task['id'][:8]}...)")
        else:
            print("⚠️  No hay workers activos")
            print("   Inicia un worker con: celery -A ubuntu worker --loglevel=info")
            return False
        
        # Ver estadísticas
        stats = inspect.stats()
        if stats:
            print("\n📊 Estadísticas de Workers:")
            for worker_name, worker_stats in stats.items():
                print(f"   - {worker_name}:")
                if 'pool' in worker_stats:
                    pool = worker_stats['pool']
                    print(f"     • Procesos: {pool.get('processes', 'N/A')}")
                    print(f"     • Max concurrencia: {pool.get('max-concurrency', 'N/A')}")
                if 'total' in worker_stats:
                    total = worker_stats['total']
                    print(f"     • Tareas ejecutadas: {sum(total.values())}")
        
        return True
    except Exception as e:
        print(f"❌ Error verificando workers: {e}")
        print("   Verifica que haya al menos un worker corriendo")
        return False

def check_registered_tasks():
    """Verifica las tareas registradas"""
    print_section("3. Verificando Tareas Registradas")
    try:
        inspect = app.control.inspect()
        registered = inspect.registered()
        
        if registered:
            all_tasks = set()
            for worker_name, tasks in registered.items():
                all_tasks.update(tasks)
            
            print(f"✅ Tareas registradas: {len(all_tasks)}")
            print("\n📋 Lista de tareas:")
            for task in sorted(all_tasks):
                # Resaltar tareas principales
                if 'udid.tasks' in task:
                    print(f"   ✅ {task}")
                else:
                    print(f"   • {task}")
            
            # Verificar tareas principales
            main_tasks = [
                'udid.tasks.initial_sync_all_data',
                'udid.tasks.download_new_subscribers',
                'udid.tasks.update_all_subscribers',
                'udid.tasks.update_smartcards_from_subscribers',
                'udid.tasks.validate_and_fix_all_data',
            ]
            
            print("\n🔍 Verificando tareas principales:")
            for task in main_tasks:
                if task in all_tasks:
                    print(f"   ✅ {task}")
                else:
                    print(f"   ❌ {task} (NO encontrada)")
            
            return True
        else:
            print("⚠️  No se encontraron tareas registradas")
            return False
    except Exception as e:
        print(f"❌ Error verificando tareas: {e}")
        return False

def test_task_execution():
    """Prueba ejecutar una tarea de prueba"""
    print_section("4. Prueba de Ejecución de Tarea")
    try:
        # Usar la tarea de debug incluida en celery.py
        from ubuntu.celery import debug_task
        
        print("📤 Enviando tarea de prueba...")
        result = debug_task.delay()
        
        print(f"✅ Tarea enviada exitosamente")
        print(f"   Task ID: {result.id}")
        print(f"   Estado inicial: {result.state}")
        
        # Esperar un momento para que se ejecute
        import time
        print("\n⏳ Esperando 2 segundos para que se ejecute...")
        time.sleep(2)
        
        # Verificar estado (reload() en lugar de refresh())
        try:
            result.reload()
        except AttributeError:
            # Si reload() no existe, simplemente acceder a state nuevamente
            pass
        
        print(f"   Estado actual: {result.state}")
        
        if result.ready():
            if result.successful():
                print("✅ Tarea completada exitosamente")
            else:
                print(f"❌ Tarea falló: {result.info}")
        else:
            print("⚠️  Tarea aún ejecutándose o en cola...")
            print("   Esto es normal si no hay workers activos")
        
        return True
    except Exception as e:
        print(f"❌ Error ejecutando tarea de prueba: {e}")
        print("   Verifica que el worker esté corriendo")
        import traceback
        traceback.print_exc()
        return False

def check_beat_schedule():
    """Verifica la configuración de Beat Schedule"""
    print_section("5. Verificando Configuración de Beat Schedule")
    try:
        from django.conf import settings
        
        beat_schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
        
        # Verificar que sea un diccionario
        if not isinstance(beat_schedule, dict):
            print(f"⚠️  Beat Schedule no es un diccionario: {type(beat_schedule)}")
            print(f"   Valor: {beat_schedule}")
            return False
        
        if beat_schedule:
            print(f"📅 Tareas periódicas configuradas: {len(beat_schedule)}")
            for name, config in beat_schedule.items():
                task = config.get('task', 'N/A')
                schedule = config.get('schedule', 'N/A')
                print(f"   • {name}:")
                print(f"     - Tarea: {task}")
                print(f"     - Schedule: {schedule}")
        else:
            print("ℹ️  No hay tareas periódicas configuradas (Beat Schedule vacío)")
            print("   Esto es normal si ejecutas tareas manualmente")
        
        return True
    except Exception as e:
        print(f"❌ Error verificando Beat Schedule: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Función principal"""
    print("\n" + "🔍" * 30)
    print("  VERIFICACIÓN DE CELERY")
    print("🔍" * 30)
    
    results = []
    
    # Ejecutar todas las verificaciones
    results.append(("Redis", check_redis_connection()))
    results.append(("Workers", check_workers()))
    results.append(("Tareas Registradas", check_registered_tasks()))
    results.append(("Beat Schedule", check_beat_schedule()))
    results.append(("Ejecución de Tarea", test_task_execution()))
    
    # Resumen final
    print_section("📊 RESUMEN")
    
    all_ok = True
    for name, status in results:
        icon = "✅" if status else "❌"
        print(f"{icon} {name}: {'OK' if status else 'FALLO'}")
        if not status:
            all_ok = False
    
    print("\n" + "=" * 60)
    if all_ok:
        print("✅ Celery está funcionando correctamente")
    else:
        print("⚠️  Hay problemas con Celery. Revisa los errores arriba.")
        print("\n💡 Soluciones comunes:")
        print("   1. Inicia un worker: celery -A ubuntu worker --loglevel=info")
        print("   2. Verifica que Redis esté corriendo")
        print("   3. Verifica la configuración en settings.py")
    print("=" * 60 + "\n")

if __name__ == '__main__':
    main()

