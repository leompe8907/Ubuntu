#!/usr/bin/env python
"""Verificar cola de Redis directamente"""
import redis

try:
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    
    print("Verificando cola de Redis...")
    print("=" * 60)
    
    # Ver todas las claves relacionadas con Celery
    keys = r.keys('celery*')
    print(f"\nClaves de Celery encontradas: {len(keys)}")
    for key in keys[:10]:  # Mostrar primeras 10
        print(f"  - {key}")
    
    # Ver cola de tareas
    queue_length = r.llen('celery')
    print(f"\nTareas en cola 'celery': {queue_length}")
    
    if queue_length > 0:
        print("\nPrimeras tareas en cola:")
        for i in range(min(3, queue_length)):
            task = r.lindex('celery', i)
            print(f"  {i+1}. {task[:100]}...")
    
    # Verificar conexión
    print(f"\n✅ Redis conectado: {r.ping()}")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

