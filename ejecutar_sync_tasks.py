#!/usr/bin/env python
"""
Script para ejecutar execute_sync_tasks() UNA SOLA VEZ.
Ejecuta la sincronización completa inicial de datos desde Panaccess.

Este script está diseñado para ejecutarse manualmente cuando se despliega
el sistema por primera vez o cuando se necesita una sincronización completa inicial.

Uso:
    # Ejecutar normalmente (verifica si ya se ejecutó)
    python ejecutar_sync_tasks.py
    
    # Forzar ejecución aunque ya se haya ejecutado antes
    python ejecutar_sync_tasks.py --force
    
    # Verificar si ya se ejecutó
    python ejecutar_sync_tasks.py --check
"""
import sys
import os
import logging
import argparse
import json
from datetime import datetime
from pathlib import Path

# Configurar Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')

import django
django.setup()

from udid.cron import execute_sync_tasks

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Archivo de marcador para verificar si ya se ejecutó
MARKER_FILE = Path('/var/log/udid/sync_tasks_completed.json')

def check_if_already_executed():
    """
    Verifica si la sincronización ya se ejecutó anteriormente.
    
    Returns:
        tuple: (bool, dict) - (ya_ejecutado, info_anterior)
    """
    if not MARKER_FILE.exists():
        return False, None
    
    try:
        with open(MARKER_FILE, 'r') as f:
            info = json.load(f)
        return True, info
    except Exception as e:
        logger.warning(f"No se pudo leer el archivo de marcador: {e}")
        return False, None

def save_execution_info(result, duration):
    """
    Guarda información sobre la ejecución para referencia futura.
    
    Args:
        result: Resultado de execute_sync_tasks()
        duration: Duración de la ejecución
    """
    try:
        # Crear directorio si no existe
        MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        info = {
            'executed_at': datetime.now().isoformat(),
            'success': result['success'],
            'message': result['message'],
            'duration_seconds': duration.total_seconds(),
            'duration_minutes': duration.total_seconds() / 60,
            'tasks': {
                task_name: {
                    'success': task_result['success'],
                    'message': task_result['message']
                }
                for task_name, task_result in result['tasks'].items()
            },
            'session_id': result.get('session_id')
        }
        
        with open(MARKER_FILE, 'w') as f:
            json.dump(info, f, indent=2)
        
        logger.info(f"Información de ejecución guardada en {MARKER_FILE}")
    except Exception as e:
        logger.warning(f"No se pudo guardar información de ejecución: {e}")

def main():
    """Ejecuta la sincronización completa de tareas UNA SOLA VEZ."""
    parser = argparse.ArgumentParser(
        description='Ejecutar execute_sync_tasks() una sola vez (sincronización inicial completa)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Forzar ejecución aunque ya se haya ejecutado antes'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Solo verificar si ya se ejecutó, sin ejecutar'
    )
    
    args = parser.parse_args()
    
    # Verificar si ya se ejecutó
    already_executed, previous_info = check_if_already_executed()
    
    if args.check:
        # Solo verificar estado
        print("=" * 80)
        print("VERIFICACIÓN DE EJECUCIÓN PREVIA")
        print("=" * 80)
        if already_executed:
            print("✅ La sincronización YA se ejecutó anteriormente")
            print(f"\nInformación de la ejecución anterior:")
            print(f"  Fecha: {previous_info.get('executed_at', 'N/A')}")
            print(f"  Éxito: {'✅ SÍ' if previous_info.get('success') else '❌ NO'}")
            print(f"  Duración: {previous_info.get('duration_minutes', 0):.2f} minutos")
            print(f"  Mensaje: {previous_info.get('message', 'N/A')}")
            print(f"\nPara forzar una nueva ejecución, usa: --force")
        else:
            print("❌ La sincronización NO se ha ejecutado aún")
            print("Puedes ejecutarla con: python ejecutar_sync_tasks.py")
        print("=" * 80)
        sys.exit(0)
    
    if already_executed and not args.force:
        # Ya se ejecutó y no se fuerza
        print("=" * 80)
        print("⚠️  SINCRONIZACIÓN YA EJECUTADA")
        print("=" * 80)
        print(f"Esta sincronización ya se ejecutó anteriormente:")
        print(f"  Fecha: {previous_info.get('executed_at', 'N/A')}")
        print(f"  Éxito: {'✅ SÍ' if previous_info.get('success') else '❌ NO'}")
        print(f"  Duración: {previous_info.get('duration_minutes', 0):.2f} minutos")
        print(f"  Mensaje: {previous_info.get('message', 'N/A')}")
        print("\n" + "-" * 80)
        print("Si necesitas ejecutarla nuevamente, usa:")
        print("  python ejecutar_sync_tasks.py --force")
        print("=" * 80)
        logger.warning("Sincronización ya ejecutada. Usar --force para ejecutar nuevamente.")
        sys.exit(0)
    
    # Ejecutar la sincronización
    start_time = datetime.now()
    
    print("=" * 80)
    print(f"EJECUTANDO SINCRONIZACIÓN COMPLETA: execute_sync_tasks()")
    if already_executed:
        print("⚠️  FORZANDO EJECUCIÓN (ya se ejecutó anteriormente)")
    print(f"Inicio: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    logger.info("=" * 80)
    logger.info("Iniciando ejecución de execute_sync_tasks() (ejecución única)")
    if already_executed:
        logger.warning("Ejecución forzada - ya existía una ejecución previa")
    logger.info("=" * 80)
    
    try:
        # Ejecutar la sincronización
        result = execute_sync_tasks()
        
        # Calcular tiempo de ejecución
        end_time = datetime.now()
        duration = end_time - start_time
        minutes = duration.total_seconds() / 60
        
        # Guardar información de ejecución
        save_execution_info(result, duration)
        
        # Mostrar resultados
        print("\n" + "=" * 80)
        print("RESULTADO DE LA SINCRONIZACIÓN")
        print("=" * 80)
        print(f"Éxito general: {'✅ SÍ' if result['success'] else '❌ NO'}")
        print(f"Mensaje: {result['message']}")
        print(f"Session ID: {result.get('session_id', 'N/A')}")
        print(f"Duración: {minutes:.2f} minutos ({duration})")
        print(f"Fin: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("\nDetalles por tarea:")
        print("-" * 80)
        
        for task_name, task_result in result['tasks'].items():
            status = "✅" if task_result['success'] else "❌"
            print(f"  {status} {task_name}: {task_result['message']}")
        
        print("=" * 80)
        print(f"\n✅ Información guardada en: {MARKER_FILE}")
        print("Esta sincronización se marcó como completada.")
        print("Para ejecutarla nuevamente, usa: --force")
        print("=" * 80)
        
        # Logging
        logger.info(f"Sincronización completada. Éxito: {result['success']}")
        logger.info(f"Duración: {minutes:.2f} minutos")
        logger.info(f"Mensaje: {result['message']}")
        
        for task_name, task_result in result['tasks'].items():
            if task_result['success']:
                logger.info(f"✅ {task_name}: {task_result['message']}")
            else:
                logger.error(f"❌ {task_name}: {task_result['message']}")
        
        # Código de salida según el resultado
        if result['success']:
            sys.exit(0)  # Éxito
        else:
            sys.exit(1)  # Fallo parcial o total
            
    except Exception as e:
        end_time = datetime.now()
        duration = end_time - start_time
        error_msg = f"Error inesperado ejecutando execute_sync_tasks(): {str(e)}"
        
        print("\n" + "=" * 80)
        print("❌ ERROR EN LA EJECUCIÓN")
        print("=" * 80)
        print(f"Error: {error_msg}")
        print(f"Duración: {duration}")
        print("=" * 80)
        
        logger.error(error_msg, exc_info=True)
        sys.exit(2)  # Error crítico

if __name__ == '__main__':
    main()
