# Variables de Entorno para Celery

Este documento describe todas las variables de entorno necesarias para configurar Celery en el proyecto.

## Variables Requeridas

Ninguna variable es estrictamente requerida, ya que Celery tiene valores por defecto. Sin embargo, se recomienda configurar al menos las básicas.

## Variables Opcionales (con valores por defecto)

### Broker y Backend

```bash
# URL del broker (Redis o RabbitMQ)
# Por defecto usa REDIS_URL si está configurado, sino redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0

# URL del backend de resultados
# Por defecto usa REDIS_URL si está configurado, sino redis://localhost:6379/1
# Nota: Usa una base de datos diferente (1) para evitar conflictos con el broker
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

### Serialización

```bash
# Formato de serialización de tareas (json es más seguro que pickle)
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json

# Formatos aceptados (separados por comas)
CELERY_ACCEPT_CONTENT=json
```

### Timezone

```bash
# Zona horaria para Celery
CELERY_TIMEZONE=UTC

# Habilitar UTC
CELERY_ENABLE_UTC=True
```

### Configuración de Resultados

```bash
# Tiempo de expiración de resultados en segundos (1 hora por defecto)
CELERY_RESULT_EXPIRES=3600

# Persistir resultados en el backend
CELERY_RESULT_PERSISTENT=True
```

### Configuración de Tareas

```bash
# Rastrear cuando una tarea inicia
CELERY_TASK_TRACK_STARTED=True

# Límite de tiempo duro para tareas (0 = sin límite)
CELERY_TASK_TIME_LIMIT=0

# Límite de tiempo suave para tareas (0 = sin límite)
CELERY_TASK_SOFT_TIME_LIMIT=0

# Acusar recibo de tareas después de completarlas (mejor para tareas largas)
CELERY_TASK_ACKS_LATE=True

# Rechazar tareas si el worker se pierde
CELERY_TASK_REJECT_ON_WORKER_LOST=True
```

### Configuración de Workers

```bash
# Multiplicador de prefetch (cuántas tareas pre-cargar)
CELERY_WORKER_PREFETCH_MULTIPLIER=4

# Máximo de tareas por proceso hijo antes de reiniciar (previene memory leaks)
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000

# Deshabilitar límites de tasa
CELERY_WORKER_DISABLE_RATE_LIMITS=False
```

### Configuración de Reintentos

```bash
# Delay por defecto entre reintentos (segundos)
CELERY_TASK_DEFAULT_RETRY_DELAY=60

# Máximo número de reintentos por defecto
CELERY_TASK_MAX_RETRIES=3
```

### Configuración de Colas

```bash
# Cola por defecto
CELERY_TASK_DEFAULT_QUEUE=default

# Exchange por defecto
CELERY_TASK_DEFAULT_EXCHANGE=default

# Routing key por defecto
CELERY_TASK_DEFAULT_ROUTING_KEY=default
```

### Configuración de Beat (Tareas Periódicas)

```bash
# Nombre del archivo donde Beat guarda el schedule
CELERY_BEAT_SCHEDULE_FILENAME=celerybeat-schedule
```

### Configuración de Flower (Monitoreo)

```bash
# Puerto donde Flower escucha (interfaz web de monitoreo)
CELERY_FLOWER_PORT=5555

# Autenticación básica para Flower (formato: "usuario:contraseña")
# Ejemplo: CELERY_FLOWER_BASIC_AUTH=admin:password123
CELERY_FLOWER_BASIC_AUTH=
```

## Ejemplo de .env Completo

```bash
# ============================================================================
# CELERY CONFIGURATION
# ============================================================================

# Broker y Backend (usa Redis que ya está configurado)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Serialización
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json
CELERY_ACCEPT_CONTENT=json

# Timezone
CELERY_TIMEZONE=UTC
CELERY_ENABLE_UTC=True

# Resultados
CELERY_RESULT_EXPIRES=3600
CELERY_RESULT_PERSISTENT=True

# Tareas
CELERY_TASK_TRACK_STARTED=True
CELERY_TASK_ACKS_LATE=True
CELERY_TASK_REJECT_ON_WORKER_LOST=True

# Workers
CELERY_WORKER_PREFETCH_MULTIPLIER=4
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000

# Reintentos
CELERY_TASK_DEFAULT_RETRY_DELAY=60
CELERY_TASK_MAX_RETRIES=3

# Colas
CELERY_TASK_DEFAULT_QUEUE=default

# Beat
CELERY_BEAT_SCHEDULE_FILENAME=celerybeat-schedule

# Flower (monitoreo)
CELERY_FLOWER_PORT=5555
CELERY_FLOWER_BASIC_AUTH=admin:password123
```

## Notas Importantes

1. **Redis ya está configurado**: El proyecto ya usa Redis para cache y Channel Layers, así que puedes usar la misma instancia de Redis para Celery.

2. **Bases de datos diferentes**: Se recomienda usar bases de datos diferentes de Redis:
   - Base 0: Broker (CELERY_BROKER_URL)
   - Base 1: Resultados (CELERY_RESULT_BACKEND)
   - Base 2: Cache (ya configurado)
   - Base 3: Channel Layers (ya configurado)

3. **Valores por defecto**: Si no configuras estas variables, Celery usará valores por defecto razonables, pero se recomienda configurarlas explícitamente para producción.

4. **Flower**: Es opcional pero muy útil para monitorear tareas. Si lo usas, configura la autenticación básica para seguridad.

