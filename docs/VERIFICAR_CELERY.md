# 🔍 Guía Rápida: Verificar que Celery está Funcionando

## Método 1: Usar el Script de Verificación (Recomendado)

```bash
# En Windows, en la raíz del proyecto
python check_celery.py
```

Este script verifica automáticamente:
- ✅ Conexión a Redis
- ✅ Workers activos
- ✅ Tareas registradas
- ✅ Configuración de Beat Schedule
- ✅ Ejecución de una tarea de prueba

## Método 2: Comandos Manuales

### 1. Verificar que Redis está corriendo

```bash
# Verificar conexión a Redis
redis-cli ping
# Debe responder: PONG
```

### 2. Verificar Workers Activos

```bash
# Ver workers activos
celery -A ubuntu inspect active

# Ver estadísticas de workers
celery -A ubuntu inspect stats

# Ver tareas registradas
celery -A ubuntu inspect registered
```

**Salida esperada:**
```
-> celery@hostname: OK
    * {'id': '...', 'name': 'udid.tasks.initial_sync_all_data', ...}
```

### 3. Verificar desde Python/Django Shell

```bash
python manage.py shell
```

```python
from ubuntu.celery import app
from celery import current_app

# Verificar conexión
print(f"Broker: {app.conf.broker_url}")
print(f"Backend: {app.conf.result_backend}")

# Verificar workers
inspect = app.control.inspect()
active = inspect.active()
print(f"Workers activos: {active}")

# Ver tareas registradas
registered = inspect.registered()
print(f"Tareas registradas: {registered}")

# Probar una tarea
from ubuntu.celery import debug_task
result = debug_task.delay()
print(f"Task ID: {result.id}")
print(f"Estado: {result.state}")
```

### 4. Ejecutar una Tarea de Prueba

```bash
python manage.py shell
```

```python
# Importar tarea de prueba
from ubuntu.celery import debug_task

# Ejecutar tarea
result = debug_task.delay()

# Ver información
print(f"Task ID: {result.id}")
print(f"Estado: {result.state}")

# Esperar resultado (opcional)
import time
time.sleep(2)
result.refresh()
print(f"Estado después de 2 seg: {result.state}")
print(f"Completada: {result.ready()}")
```

### 5. Verificar Estado de una Tarea Específica

```python
from celery.result import AsyncResult
from ubuntu.celery import app

# Reemplazar con el Task ID real
task_id = "TU_TASK_ID_AQUI"
result = AsyncResult(task_id, app=app)

print(f"Estado: {result.state}")
print(f"Listo: {result.ready()}")
print(f"Exitoso: {result.successful() if result.ready() else 'Aún ejecutándose'}")
if result.ready():
    if result.successful():
        print(f"Resultado: {result.result}")
    else:
        print(f"Error: {result.info}")
```

## Método 3: Verificar Logs

### Si usas systemd (en Linux/VM):

```bash
# Ver logs del worker
sudo journalctl -u celery-worker -f

# Ver últimas 50 líneas
sudo journalctl -u celery-worker -n 50
```

### Si ejecutas el worker manualmente:

El worker mostrará logs en la terminal donde lo ejecutaste.

## Método 4: Verificar con Flower (si está instalado)

```bash
# Iniciar Flower
celery -A ubuntu flower

# Acceder en el navegador
# http://localhost:5555
```

En Flower puedes ver:
- ✅ Workers activos
- ✅ Tareas ejecutándose
- ✅ Tareas completadas
- ✅ Tareas fallidas
- ✅ Estadísticas en tiempo real

## Problemas Comunes y Soluciones

### ❌ "No hay workers activos"

**Solución:**
```bash
# Iniciar un worker
celery -A ubuntu worker --loglevel=info
```

### ❌ "Error conectando a Redis"

**Solución:**
1. Verificar que Redis esté corriendo:
   ```bash
   redis-cli ping
   ```
2. Verificar la URL en `settings.py` o `.env`:
   ```python
   CELERY_BROKER_URL = "redis://localhost:6379/0"
   ```

### ❌ "Tarea queda en estado PENDING"

**Causas posibles:**
1. No hay worker activo
2. El worker no puede conectarse a Redis
3. La tarea no está registrada

**Solución:**
```bash
# Verificar workers
celery -A ubuntu inspect active

# Si no hay workers, iniciar uno
celery -A ubuntu worker --loglevel=info
```

### ❌ "Tarea falla con error"

**Solución:**
```python
# Ver el error completo
from celery.result import AsyncResult
from ubuntu.celery import app

result = AsyncResult('TASK_ID', app=app)
if result.failed():
    print(f"Error: {result.info}")
    print(f"Traceback: {result.traceback}")
```

## Checklist de Verificación

- [ ] Redis está corriendo (`redis-cli ping` responde `PONG`)
- [ ] Hay al menos un worker activo (`celery -A ubuntu inspect active`)
- [ ] Las tareas están registradas (`celery -A ubuntu inspect registered`)
- [ ] Puedo ejecutar una tarea de prueba (`debug_task.delay()`)
- [ ] La tarea se ejecuta y completa exitosamente
- [ ] Los logs del worker muestran actividad

## Comandos Rápidos de Referencia

```bash
# Ver workers activos
celery -A ubuntu inspect active

# Ver estadísticas
celery -A ubuntu inspect stats

# Ver tareas registradas
celery -A ubuntu inspect registered

# Ver tareas programadas (Beat)
celery -A ubuntu inspect scheduled

# Ver tareas reservadas (en cola)
celery -A ubuntu inspect reserved

# Iniciar worker
celery -A ubuntu worker --loglevel=info

# Iniciar worker con más procesos
celery -A ubuntu worker --loglevel=info --concurrency=4

# Iniciar Beat (scheduler)
celery -A ubuntu beat --loglevel=info

# Iniciar Flower (monitor)
celery -A ubuntu flower
```

## Ejemplo de Salida Correcta

Cuando Celery está funcionando correctamente, deberías ver:

```
✅ Conexión a Redis: OK
✅ Workers activos: 1
✅ Tareas registradas: 15+
✅ Tarea de prueba ejecutada exitosamente
```

Si todo está bien, puedes ejecutar tus tareas manualmente:

```python
from udid.tasks import initial_sync_all_data
result = initial_sync_all_data.delay()
print(f"Task ID: {result.id}")
```

