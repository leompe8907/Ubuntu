# Plan de Acción - Tareas: Plan A + Plan C

## Resumen Ejecutivo

**Objetivo:** Estabilizar el sistema rápidamente (Plan A) mientras se construye una arquitectura robusta y escalable (Plan C).

**Duración total estimada:** 4-6 semanas
- **Fase 1 (Plan A):** 48-72 horas - Estabilización crítica
- **Fase 2 (Plan C - Parte 1):** 1-2 semanas - Infraestructura base
- **Fase 3 (Plan C - Parte 2):** 2-3 semanas - Escalabilidad y operación

**Prioridad:** 🔴 CRÍTICA → 🟡 ALTA → 🟢 MEDIA

---

## FASE 1: ESTABILIZACIÓN RÁPIDA (Plan A) - 48-72 horas

### Sprint 1.1: Control de Concurrencia Global (4-6 horas)

#### Tarea 1.1.1: Implementar Semáforo Global en Redis
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Redis operativo  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar un semáforo global en Redis que limite la concurrencia total del sistema a 500 slots simultáneos.

**Archivos a crear/modificar:**
- `udid/util.py` - Agregar función `acquire_global_semaphore()`
- `udid/middleware.py` - Agregar middleware `GlobalConcurrencyMiddleware`
- `ubuntu/settings.py` - Agregar configuración `GLOBAL_SEMAPHORE_SLOTS`

**Implementación:**
```python
# udid/util.py
def _get_dynamic_timeout():
    """
    Calcula timeout dinámico basado en latencia p95.
    Retorna p95 × 1.5 para evitar liberar slots prematuramente.
    """
    from udid.utils.metrics import _metrics
    metrics = _metrics.get_metrics()
    p95_ms = metrics.get('p95', 2000)  # Default 2 segundos
    # Convertir a segundos y multiplicar por 1.5
    timeout = int((p95_ms / 1000) * 1.5)
    # Mínimo 10 segundos, máximo 60 segundos
    return max(10, min(60, timeout))

def _count_slots_scan(redis_client, pattern):
    """
    Cuenta slots usando SCAN en lugar de KEYS para evitar bloqueos.
    SCAN es O(1) por iteración vs KEYS que es O(n).
    """
    count = 0
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
        count += len(keys)
        if cursor == 0:
            break
    return count

def acquire_global_semaphore(timeout=None, max_slots=500):
    """
    Adquiere un slot en el semáforo global usando Redis.
    Retorna (acquired: bool, slot_id: str, retry_after: int)
    
    Args:
        timeout: TTL del slot en segundos. Si es None, se calcula dinámicamente.
        max_slots: Máximo número de slots simultáneos.
    """
    import redis
    from django.conf import settings
    import uuid
    
    redis_client = redis.from_url(settings.REDIS_URL)
    semaphore_key = "global_semaphore:slots"
    slot_id = f"{uuid.uuid4()}"
    
    # Calcular timeout dinámico si no se proporciona
    if timeout is None:
        timeout = _get_dynamic_timeout()
    
    # Contar slots ocupados usando SCAN (más eficiente que KEYS)
    pattern = f"{semaphore_key}:*"
    current_slots = _count_slots_scan(redis_client, pattern)
    
    if current_slots >= max_slots:
        # Calcular retry_after basado en TTL promedio
        # Estimar tiempo de espera basado en timeout dinámico
        retry_after = max(1, timeout // 6)  # 1/6 del timeout como mínimo
        return False, None, retry_after
    
    # Usar SET con NX y EX para operación atómica
    acquired = redis_client.set(
        f"{semaphore_key}:{slot_id}",
        "1",
        nx=True,
        ex=timeout
    )
    
    if not acquired:
        # Si falla, recalcular slots (puede haber cambiado)
        current_slots = _count_slots_scan(redis_client, pattern)
        if current_slots >= max_slots:
            retry_after = max(1, timeout // 6)
            return False, None, retry_after
        # Si no está lleno, reintentar (puede ser race condition)
        acquired = redis_client.set(
            f"{semaphore_key}:{slot_id}",
            "1",
            nx=True,
            ex=timeout
        )
        if not acquired:
            return False, None, 1
    
    return True, slot_id, 0

def release_global_semaphore(slot_id):
    """Libera un slot del semáforo global"""
    import redis
    from django.conf import settings
    
    redis_client = redis.from_url(settings.REDIS_URL)
    semaphore_key = "global_semaphore:slots"
    redis_client.delete(f"{semaphore_key}:{slot_id}")
```

**Criterios de aceptación:**
- [ ] Semáforo limita a 500 slots simultáneos
- [ ] Retorna 503 con Retry-After cuando se supera el límite
- [ ] Los slots se liberan automáticamente con TTL dinámico (p95 × 1.5)
- [ ] Usa SCAN en lugar de KEYS para contar slots (evita bloqueos)
- [ ] Dashboard muestra pico de concurrentes aplanado
- [ ] La DB no supera su pool de conexiones

**Pruebas:**
- Test de carga con 1000 usuarios simultáneos
- Verificar que solo 500 requests se procesan a la vez
- Verificar que los demás reciben 503 con Retry-After

---

#### Tarea 1.1.2: Middleware de Semáforo Global
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 1-2 horas  
**Dependencias:** Tarea 1.1.1  
**Asignado a:** Backend Developer

**Objetivo:**
Crear middleware que aplique el semáforo global antes de procesar cualquier request.

**Archivos a crear/modificar:**
- `udid/middleware.py` - Agregar `GlobalConcurrencyMiddleware`

**Implementación:**
```python
# udid/middleware.py
class GlobalConcurrencyMiddleware(MiddlewareMixin):
    """
    Middleware que aplica semáforo global de concurrencia.
    Rechaza requests con 503 cuando se supera el límite.
    """
    
    def process_request(self, request):
        # Solo aplicar a endpoints de API
        if not (request.path.startswith('/udid/') or 
                request.path.startswith('/auth/')):
            return None
        
        # Timeout se calcula dinámicamente basado en p95
        acquired, slot_id, retry_after = acquire_global_semaphore(
            timeout=None,  # Se calcula dinámicamente
            max_slots=500
        )
        
        if not acquired:
            from django.http import JsonResponse
            return JsonResponse({
                "error": "Service temporarily unavailable",
                "message": "System is handling high load. Please retry.",
                "retry_after": retry_after
            }, status=503, headers={"Retry-After": str(retry_after)})
        
        # Almacenar slot_id en request para liberarlo después
        request._semaphore_slot_id = slot_id
        return None
    
    def process_response(self, request, response):
        # Liberar slot después de procesar request
        if hasattr(request, '_semaphore_slot_id'):
            release_global_semaphore(request._semaphore_slot_id)
        return response
```

**Criterios de aceptación:**
- [ ] Middleware aplica semáforo a endpoints de API
- [ ] Retorna 503 con Retry-After cuando se supera límite
- [ ] Los slots se liberan correctamente después del request
- [ ] No afecta endpoints estáticos o de admin

**Pruebas:**
- Verificar que requests normales pasan
- Verificar que con 600 requests simultáneos, 100 reciben 503
- Verificar que los slots se liberan correctamente

---

### Sprint 1.2: Rate Limiting por Token/UDID con Lua (4-6 horas)

#### Tarea 1.2.1: Script Lua para Token Bucket Atómico
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Redis operativo  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar token bucket atómico en Redis usando scripts Lua para evitar race conditions.

**Archivos a crear/modificar:**
- `udid/util.py` - Agregar función `check_token_bucket_lua()`
- `udid/scripts/` - Crear directorio y `token_bucket.lua`

**Implementación:**
```lua
-- udid/scripts/token_bucket.lua
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local tokens_requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local window_seconds = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- Calcular tokens a reponer
local elapsed = now - last_refill
local tokens_to_add = math.floor(elapsed * refill_rate / window_seconds)
tokens = math.min(capacity, tokens + tokens_to_add)

-- Verificar si hay suficientes tokens
if tokens >= tokens_requested then
    tokens = tokens - tokens_requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, window_seconds)
    return {1, tokens}  -- allowed, remaining
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, window_seconds)
    local retry_after = math.ceil((tokens_requested - tokens) / refill_rate * window_seconds)
    return {0, tokens, retry_after}  -- denied, remaining, retry_after
end
```

```python
# udid/util.py
# Singleton para el script Lua (se registra una sola vez)
_token_bucket_script = None

def _get_token_bucket_script():
    """
    Obtiene el script Lua registrado (singleton).
    Se registra una sola vez para reducir overhead.
    """
    global _token_bucket_script
    
    if _token_bucket_script is None:
        import redis
        from django.conf import settings
        import os
        
        redis_client = redis.from_url(settings.REDIS_URL)
        
        # Cargar script Lua una sola vez
        script_path = os.path.join(
            os.path.dirname(__file__),
            'scripts',
            'token_bucket.lua'
        )
        with open(script_path, 'r') as f:
            lua_script = f.read()
        
        # Registrar script (persistente en redis_client)
        _token_bucket_script = redis_client.register_script(lua_script)
    
    return _token_bucket_script

def check_token_bucket_lua(identifier, capacity=10, refill_rate=1, 
                          window_seconds=60, tokens_requested=1):
    """
    Verifica rate limit usando token bucket atómico en Lua.
    Retorna (is_allowed: bool, remaining: int, retry_after: int)
    
    El script se registra una sola vez (singleton) para mejorar rendimiento.
    """
    import redis
    from django.conf import settings
    import time
    
    redis_client = redis.from_url(settings.REDIS_URL)
    key = f"token_bucket:{identifier}"
    
    # Obtener script registrado (singleton)
    script = _get_token_bucket_script()
    
    # Ejecutar script
    result = script(
        keys=[key],
        args=[capacity, refill_rate, tokens_requested, int(time.time()), window_seconds],
        client=redis_client  # Especificar cliente explícitamente
    )
    
    if result[0] == 1:
        return True, result[1], 0
    else:
        return False, result[1], result[2]
```
<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
read_file

**Criterios de aceptación:**
- [ ] Script Lua funciona correctamente
- [ ] Script se registra una sola vez (singleton) para reducir overhead
- [ ] Operaciones son atómicas (sin race conditions)
- [ ] Retorna 429 cuando se excede el límite
- [ ] Calcula Retry-After correctamente
- [ ] Los tokens se reponen correctamente
- [ ] Mejora rendimiento bajo carga (menos overhead de registro)

**Pruebas:**
- Test con múltiples requests simultáneos
- Verificar que no hay race conditions
- Verificar que los tokens se reponen correctamente

---

#### Tarea 1.2.2: Integrar Token Bucket en Vistas
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 1.2.1  
**Asignado a:** Backend Developer

**Objetivo:**
Integrar el token bucket en las vistas críticas usando X-Client-Token o UDID.

**Archivos a modificar:**
- `udid/views.py` - Modificar vistas para usar token bucket
- `udid/auth.py` - Modificar vistas de autenticación

**Implementación:**
```python
# udid/views.py
def get_client_token(request):
    """Obtiene token del cliente desde header o UDID"""
    token = request.META.get('HTTP_X_CLIENT_TOKEN')
    if not token:
        # Fallback a UDID si está disponible
        token = request.data.get('udid') or request.query_params.get('udid')
    return token

class AuthenticateWithUDIDView(APIView):
    def post(self, request):
        # Rate limiting con token bucket
        client_token = get_client_token(request)
        if client_token:
            is_allowed, remaining, retry_after = check_token_bucket_lua(
                identifier=client_token,
                capacity=10,  # 10 requests
                refill_rate=1,  # 1 token por segundo
                window_seconds=60,
                tokens_requested=1
            )
            
            if not is_allowed:
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "Too many requests. Please retry later.",
                    "retry_after": retry_after,
                    "remaining": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })
        
        # ... resto del código
```

**Criterios de aceptación:**
- [ ] Token bucket se aplica a vistas críticas
- [ ] Usa X-Client-Token o UDID como identificador
- [ ] Retorna 429 con Retry-After cuando se excede
- [ ] No afecta requests legítimos normales

**Pruebas:**
- Test con múltiples requests del mismo token
- Verificar que se respetan los límites
- Verificar que los tokens se reponen

---

### Sprint 1.3: Fast-Fail Antes de BD (3-4 horas)

#### Tarea 1.3.1: Reordenar Flujo en Vistas
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.1.2, Tarea 1.2.2  
**Asignado a:** Backend Developer

**Objetivo:**
Reordenar el flujo en vistas para aplicar semáforo y rate limit ANTES de tocar la BD.

**Archivos a modificar:**
- `udid/views.py` - Reordenar flujo en todas las vistas
- `udid/services.py` - Reordenar flujo en servicios

**Implementación:**
```python
# udid/views.py
class ValidateAndAssociateUDIDView(APIView):
    def post(self, request):
        # 1. Validación de datos (rápido, sin BD)
        serializer = UDIDAssociationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, 
                          status=status.HTTP_400_BAD_REQUEST)
        
        # 2. Rate limiting (Redis, sin BD)
        udid = serializer.validated_data['udid_request'].udid
        is_allowed, remaining, retry_after = check_token_bucket_lua(
            identifier=udid,
            capacity=5,
            refill_rate=1,
            window_seconds=60
        )
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "retry_after": retry_after
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # 3. Semáforo global (Redis, sin BD)
        acquired, slot_id, retry_after = acquire_global_semaphore()
        if not acquired:
            return Response({
                "error": "Service temporarily unavailable",
                "retry_after": retry_after
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            # 4. AHORA SÍ: Operaciones de BD (después de todas las validaciones)
            with transaction.atomic():
                udid_request = UDIDAuthRequest.objects.select_for_update().get(
                    pk=serializer.validated_data['udid_request'].pk
                )
                # ... resto del código
        finally:
            # Liberar semáforo
            release_global_semaphore(slot_id)
```

**Criterios de aceptación:**
- [ ] Semáforo y rate limit se aplican ANTES de BD
- [ ] Los `select_for_update()` están al final del flujo
- [ ] La latencia p95 disminuye
- [ ] La BD muestra menos locks

**Pruebas:**
- Medir latencia antes y después
- Verificar que los locks de BD disminuyen
- Test de carga para validar mejoras

---

### Sprint 1.4: Logs Asíncronos (4-6 horas)

#### Tarea 1.4.1: Buffer en Memoria para Logs
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Ninguna  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar buffer en memoria para logs que se escriben en batch cada N segundos.

**Archivos a crear/modificar:**
- `udid/utils/log_buffer.py` - Crear módulo de buffer
- `udid/views.py` - Reemplazar `AuthAuditLog.objects.create()` por buffer
- `udid/auth.py` - Reemplazar logs síncronos

**Implementación:**
```python
# udid/utils/log_buffer.py
import threading
import time
from collections import deque
from django.db import transaction

class LogBuffer:
    """
    Buffer en memoria para logs que se escriben en batch.
    Thread-safe usando locks.
    """
    
    def __init__(self, batch_size=100, flush_interval=5):
        self.buffer = deque()
        self.lock = threading.Lock()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.last_flush = time.time()
        self._start_flush_thread()
    
    def _start_flush_thread(self):
        """Inicia thread que hace flush periódico"""
        def flush_periodic():
            while True:
                time.sleep(self.flush_interval)
                self.flush()
        
        thread = threading.Thread(target=flush_periodic, daemon=True)
        thread.start()
    
    def add(self, log_data):
        """Agrega un log al buffer"""
        with self.lock:
            self.buffer.append(log_data)
            
            # Flush si se alcanza el tamaño del batch
            if len(self.buffer) >= self.batch_size:
                self._flush_internal()
    
    def flush(self):
        """Fuerza flush del buffer"""
        with self.lock:
            self._flush_internal()
    
    def _flush_internal(self):
        """Flush interno (debe llamarse con lock adquirido)"""
        if not self.buffer:
            return
        
        logs_to_write = list(self.buffer)
        self.buffer.clear()
        self.last_flush = time.time()
        
        # Escribir en BD en batch (fire-and-forget)
        try:
            with transaction.atomic():
                from udid.models import AuthAuditLog
                AuthAuditLog.objects.bulk_create([
                    AuthAuditLog(**log_data) for log_data in logs_to_write
                ])
        except Exception as e:
            # Log error pero no bloquear
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error writing logs to DB: {e}")

# Instancia global
_log_buffer = LogBuffer(batch_size=100, flush_interval=5)

def log_audit_async(action_type, **kwargs):
    """Función helper para logging asíncrono"""
    _log_buffer.add({
        'action_type': action_type,
        **kwargs
    })
```

**Criterios de aceptación:**
- [ ] Los logs se escriben en batch
- [ ] No bloquea el request
- [ ] Los logs se persisten correctamente
- [ ] El tiempo de respuesta no varía con volumen de logs

**Pruebas:**
- Test con 1000 requests y verificar que los logs se escriben
- Medir tiempo de respuesta antes y después
- Verificar que no se pierden logs

---

#### Tarea 1.4.2: Reemplazar Logs Síncronos
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 1-2 horas  
**Dependencias:** Tarea 1.4.1  
**Asignado a:** Backend Developer

**Objetivo:**
Reemplazar todas las llamadas síncronas a `AuthAuditLog.objects.create()` por el buffer asíncrono.

**Archivos a modificar:**
- `udid/views.py` - Reemplazar 8+ llamadas
- `udid/auth.py` - Reemplazar llamadas
- `udid/services.py` - Reemplazar llamadas
- `udid/automatico.py` - Reemplazar llamadas

**Implementación:**
```python
# Antes:
AuthAuditLog.objects.create(
    action_type='udid_generated',
    udid=udid,
    client_ip=client_ip,
    ...
)

# Después:
from udid.utils.log_buffer import log_audit_async
log_audit_async(
    action_type='udid_generated',
    udid=udid,
    client_ip=client_ip,
    ...
)
```

**Criterios de aceptación:**
- [ ] Todas las llamadas síncronas reemplazadas
- [ ] No hay `AuthAuditLog.objects.create()` directos
- [ ] Los logs se siguen escribiendo correctamente

**Pruebas:**
- Buscar todas las ocurrencias de `AuthAuditLog.objects.create()`
- Verificar que se reemplazaron todas
- Test de carga para validar mejoras

---

### Sprint 1.5: WebSocket - Límites y Timeouts (3-4 horas)

#### Tarea 1.5.1: Contador por Token y Semáforo Global WS
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 1.1.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar límites por token (máx 3-5 conexiones) y semáforo global para WebSockets (máx 1000).

**Archivos a modificar:**
- `udid/consumers.py` - Agregar límites
- `udid/util.py` - Agregar funciones de límites WS

**Implementación:**
```python
# udid/util.py
def check_websocket_limits(udid, device_fingerprint, max_per_token=5, 
                          max_global=1000):
    """
    Verifica límites de WebSocket por token y global.
    Retorna (is_allowed: bool, reason: str, retry_after: int)
    """
    import redis
    from django.conf import settings
    
    redis_client = redis.from_url(settings.REDIS_URL)
    
    # Límite por token/UDID
    token_key = f"ws_connections:token:{udid or device_fingerprint}"
    token_count = redis_client.incr(token_key)
    if token_count == 1:
        redis_client.expire(token_key, 300)  # 5 minutos
    
    if token_count > max_per_token:
        redis_client.decr(token_key)
        return False, "Too many connections for this token", 60
    
    # Semáforo global
    global_key = "ws_connections:global"
    global_count = redis_client.incr(global_key)
    if global_count == 1:
        redis_client.expire(global_key, 300)
    
    if global_count > max_global:
        redis_client.decr(global_key)
        redis_client.decr(token_key)
        return False, "Too many global WebSocket connections", 60
    
    return True, None, 0
```

**Criterios de aceptación:**
- [ ] Máximo 5 conexiones por token
- [ ] Máximo 1000 conexiones globales
- [ ] Rechaza conexiones con mensaje claro
- [ ] El número de FDs se mantiene estable

**Pruebas:**
- Test con múltiples conexiones del mismo token
- Test con 1500 conexiones simultáneas
- Verificar que los límites se respetan

---

#### Tarea 1.5.2: Pings/Keepalive y Cierre Agresivo
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 1-2 horas  
**Dependencias:** Tarea 1.5.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar pings/keepalive y cierre agresivo de conexiones inactivas.

**Archivos a modificar:**
- `udid/consumers.py` - Agregar pings y timeouts

**Implementación:**
```python
# udid/consumers.py
class AuthWaitWS(AsyncWebsocketConsumer):
    PING_INTERVAL = 30  # segundos
    INACTIVITY_TIMEOUT = 60  # segundos
    
    async def connect(self):
        # ... código existente ...
        
        # Iniciar ping periódico
        self.ping_task = asyncio.create_task(self._ping_loop())
        
        # Iniciar timeout de inactividad
        self.last_activity = time.time()
        self.inactivity_task = asyncio.create_task(self._inactivity_check())
    
    async def _ping_loop(self):
        """Envía pings periódicos"""
        while not self.done:
            await asyncio.sleep(self.PING_INTERVAL)
            try:
                await self.send(text_data=json.dumps({"type": "ping"}))
            except:
                break
    
    async def _inactivity_check(self):
        """Cierra conexión si está inactiva"""
        while not self.done:
            await asyncio.sleep(10)
            if time.time() - self.last_activity > self.INACTIVITY_TIMEOUT:
                await self.close(code=4000, reason="Inactivity timeout")
                break
    
    async def receive(self, text_data=None, bytes_data=None):
        self.last_activity = time.time()
        # ... resto del código ...
```

**Criterios de aceptación:**
- [ ] Pings se envían cada 30 segundos
- [ ] Conexiones inactivas se cierran después de 60 segundos
- [ ] El número de FDs se mantiene estable

**Pruebas:**
- Test con conexiones inactivas
- Verificar que se cierran correctamente
- Verificar que los pings se envían

---

### Sprint 1.6: Observabilidad Mínima (4-6 horas)

#### Tarea 1.6.1: Dashboard de Métricas Básico
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.1.1, Tarea 1.2.1  
**Asignado a:** Backend Developer

**Objetivo:**
Crear dashboard básico que muestre: concurrencia, 429/503, uso de pool DB, latencias p50/p95/p99, CPU, RAM, Redis latency y backlog WS.

**Archivos a crear:**
- `udid/views.py` - Agregar vista `MetricsDashboardView`
- `udid/templates/metrics.html` - Template del dashboard
- `udid/utils/metrics.py` - Funciones de recolección de métricas

**Implementación:**
```python
# udid/utils/metrics.py
import time
import psutil
from collections import deque
from django.core.cache import cache
import redis

class MetricsCollector:
    def __init__(self):
        self.latencies = deque(maxlen=1000)
        self.error_counts = {'429': 0, '503': 0, '500': 0}
        self.redis_latencies = deque(maxlen=100)
    
    def record_latency(self, latency_ms):
        self.latencies.append(latency_ms)
    
    def record_error(self, status_code):
        if str(status_code) in self.error_counts:
            self.error_counts[str(status_code)] += 1
    
    def record_redis_latency(self, latency_ms):
        """Registra latencia de operaciones Redis"""
        self.redis_latencies.append(latency_ms)
    
    def get_metrics(self):
        """Obtiene todas las métricas del sistema"""
        if not self.latencies:
            base_metrics = {
                'p50': 0, 'p95': 0, 'p99': 0,
                'errors': self.error_counts,
                'concurrency': 0
            }
        else:
            sorted_latencies = sorted(self.latencies)
            n = len(sorted_latencies)
            base_metrics = {
                'p50': sorted_latencies[int(n * 0.5)],
                'p95': sorted_latencies[int(n * 0.95)],
                'p99': sorted_latencies[int(n * 0.99)],
                'errors': self.error_counts,
                'concurrency': self._get_current_concurrency()
            }
        
        # Agregar métricas del sistema
        system_metrics = self._get_system_metrics()
        redis_metrics = self._get_redis_metrics()
        ws_metrics = self._get_websocket_metrics()
        
        return {
            **base_metrics,
            **system_metrics,
            **redis_metrics,
            **ws_metrics
        }
    
    def _get_current_concurrency(self):
        """Obtiene concurrencia actual usando SCAN"""
        import redis
        from django.conf import settings
        redis_client = redis.from_url(settings.REDIS_URL)
        semaphore_key = "global_semaphore:slots"
        pattern = f"{semaphore_key}:*"
        
        # Usar SCAN en lugar de KEYS
        count = 0
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            count += len(keys)
            if cursor == 0:
                break
        return count
    
    def _get_system_metrics(self):
        """Obtiene métricas de CPU y RAM"""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            return {
                'cpu_percent': cpu_percent,
                'ram_percent': memory.percent,
                'ram_used_mb': memory.used / (1024 * 1024),
                'ram_total_mb': memory.total / (1024 * 1024)
            }
        except:
            return {
                'cpu_percent': 0,
                'ram_percent': 0,
                'ram_used_mb': 0,
                'ram_total_mb': 0
            }
    
    def _get_redis_metrics(self):
        """Obtiene métricas de Redis (latency, conexiones)"""
        try:
            import redis
            from django.conf import settings
            
            # Medir latencia de Redis
            start = time.time()
            redis_client = redis.from_url(settings.REDIS_URL)
            redis_client.ping()
            latency_ms = (time.time() - start) * 1000
            self.record_redis_latency(latency_ms)
            
            # Calcular latencia promedio
            if self.redis_latencies:
                avg_redis_latency = sum(self.redis_latencies) / len(self.redis_latencies)
            else:
                avg_redis_latency = latency_ms
            
            # Obtener info de Redis
            info = redis_client.info()
            
            return {
                'redis_latency_ms': round(avg_redis_latency, 2),
                'redis_connected_clients': info.get('connected_clients', 0),
                'redis_used_memory_mb': info.get('used_memory', 0) / (1024 * 1024),
                'redis_keyspace_hits': info.get('keyspace_hits', 0),
                'redis_keyspace_misses': info.get('keyspace_misses', 0)
            }
        except Exception as e:
            return {
                'redis_latency_ms': 0,
                'redis_connected_clients': 0,
                'redis_used_memory_mb': 0,
                'redis_keyspace_hits': 0,
                'redis_keyspace_misses': 0,
                'redis_error': str(e)
            }
    
    def _get_websocket_metrics(self):
        """Obtiene métricas de WebSocket (conexiones activas, backlog)"""
        try:
            import redis
            from django.conf import settings
            redis_client = redis.from_url(settings.REDIS_URL)
            
            # Contar conexiones WS activas
            ws_pattern = "ws_connections:token:*"
            ws_count = 0
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor, match=ws_pattern, count=100)
                ws_count += len(keys)
                if cursor == 0:
                    break
            
            # Contar backlog de mensajes WS (si existe)
            backlog_pattern = "ws_backlog:*"
            backlog_count = 0
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor, match=backlog_pattern, count=100)
                backlog_count += len(keys)
                if cursor == 0:
                    break
            
            return {
                'ws_active_connections': ws_count,
                'ws_backlog_messages': backlog_count
            }
        except:
            return {
                'ws_active_connections': 0,
                'ws_backlog_messages': 0
            }

# Instancia global
_metrics = MetricsCollector()
```

**Criterios de aceptación:**
- [ ] Dashboard muestra métricas en tiempo real
- [ ] Muestra concurrencia, errores, latencias (p50/p95/p99)
- [ ] Muestra CPU, RAM, Redis latency y backlog WS
- [ ] Se actualiza cada 5 segundos
- [ ] Accesible solo para administradores
- [ ] Panorama completo de salud del sistema

**Pruebas:**
- Verificar que las métricas se muestran correctamente
- Test de carga y verificar métricas
- Verificar que se actualiza en tiempo real

---

#### Tarea 1.6.2: Prueba de Carga Controlada
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 1-2 horas  
**Dependencias:** Tarea 1.6.1  
**Asignado a:** QA/Backend Developer

**Objetivo:**
Ejecutar prueba de carga controlada (rampa 0→1200) y validar mejoras.

**Archivos a modificar:**
- `test_carga_avanzado.py` - Actualizar script de prueba

**Criterios de aceptación:**
- [ ] Prueba de carga ejecutada (rampa 0→1200)
- [ ] Sistema sostiene 800-1000 concurrentes
- [ ] Error rate < 2%
- [ ] Latencia p95 < 5 segundos

**Pruebas:**
- Ejecutar prueba de carga
- Analizar resultados
- Comparar con baseline anterior

---

## FASE 2: INFRAESTRUCTURA BASE (Plan C - Parte 1) - 1-2 semanas

### Sprint 2.1: API Keys/Tokens Firmados (3-5 días)

#### Tarea 2.1.1: Modelo de API Keys
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Ninguna  
**Asignado a:** Backend Developer

**Objetivo:**
Crear modelo para API keys/tokens firmados con cuotas por tenant y plan.

**Archivos a crear:**
- `udid/models.py` - Agregar modelos `APIKey`, `Tenant`, `Plan`

**Implementación:**
```python
# udid/models.py
class Tenant(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Plan(models.Model):
    name = models.CharField(max_length=50)
    max_requests_per_minute = models.IntegerField()
    max_requests_per_hour = models.IntegerField()
    max_requests_per_day = models.IntegerField()
    max_concurrent_connections = models.IntegerField()

class APIKey(models.Model):
    key = models.CharField(max_length=64, unique=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    def is_valid(self):
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True
```

**Criterios de aceptación:**
- [ ] Modelos creados con migraciones
- [ ] Relaciones correctas
- [ ] Validaciones implementadas

**Pruebas:**
- Crear API keys de prueba
- Verificar validaciones
- Test de relaciones

---

#### Tarea 2.1.2: Generación y Firma de Tokens
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 2.1.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar generación y verificación de tokens firmados.

**Archivos a crear:**
- `udid/utils/token_signing.py` - Funciones de firma

**Implementación:**
```python
# udid/utils/token_signing.py
import hmac
import hashlib
import time
import json
import base64

def generate_api_key(tenant_id, plan_id):
    """Genera una API key única y firmada"""
    payload = {
        'tenant_id': tenant_id,
        'plan_id': plan_id,
        'timestamp': int(time.time())
    }
    
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    signature = hmac.new(
        settings.SECRET_KEY.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return f"{payload_b64}.{signature}"

def verify_api_key(api_key):
    """Verifica y decodifica una API key"""
    try:
        payload_b64, signature = api_key.split('.')
        expected_signature = hmac.new(
            settings.SECRET_KEY.encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            return None
        
        payload = json.loads(base64.b64decode(payload_b64).decode())
        return payload
    except:
        return None
```

**Criterios de aceptación:**
- [ ] Tokens se generan correctamente
- [ ] Verificación funciona
- [ ] Tokens expirados se rechazan

**Pruebas:**
- Generar tokens y verificar
- Intentar falsificar tokens
- Test de expiración

---

#### Tarea 2.1.3: Middleware de Autenticación por API Key
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 2.1.2  
**Asignado a:** Backend Developer

**Objetivo:**
Crear middleware que autentique requests por API key y aplique cuotas.

**Archivos a crear:**
- `udid/middleware.py` - Agregar `APIKeyAuthMiddleware`

**Implementación:**
```python
# udid/middleware.py
class APIKeyAuthMiddleware(MiddlewareMixin):
    """
    Middleware que autentica requests por API key y aplica cuotas.
    """
    
    def process_request(self, request):
        api_key = request.META.get('HTTP_X_API_KEY')
        if not api_key:
            return None  # No requiere API key
        
        # Verificar API key
        payload = verify_api_key(api_key)
        if not payload:
            return JsonResponse({
                "error": "Invalid API key"
            }, status=401)
        
        # Obtener API key de BD
        try:
            api_key_obj = APIKey.objects.get(key=api_key)
            if not api_key_obj.is_valid():
                return JsonResponse({
                    "error": "API key expired or inactive"
                }, status=401)
            
            # Aplicar cuotas del plan
            plan = api_key_obj.plan
            tenant_id = api_key_obj.tenant_id
            
            # Verificar rate limit por plan
            is_allowed, remaining, retry_after = check_plan_rate_limit(
                tenant_id, plan
            )
            
            if not is_allowed:
                return JsonResponse({
                    "error": "Rate limit exceeded",
                    "retry_after": retry_after,
                    "remaining": remaining
                }, status=429, headers={"Retry-After": str(retry_after)})
            
            # Almacenar en request para uso posterior
            request.api_key = api_key_obj
            request.tenant = api_key_obj.tenant
            request.plan = plan
            
        except APIKey.DoesNotExist:
            return JsonResponse({
                "error": "API key not found"
            }, status=401)
        
        return None
```

**Criterios de aceptación:**
- [ ] Middleware autentica por API key
- [ ] Aplica cuotas del plan
- [ ] Retorna 401/429 apropiados

**Pruebas:**
- Test con API key válida
- Test con API key inválida
- Test con cuotas excedidas

---

### Sprint 2.2: Redis Alta Disponibilidad (2-3 días)

#### Tarea 2.2.1: Configurar Redis Cluster/Sentinel
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Infraestructura  
**Asignado a:** DevOps/Backend Developer

**Objetivo:**
Configurar Redis Cluster o Sentinel para alta disponibilidad.

**Archivos a modificar:**
- `ubuntu/settings.py` - Configurar Redis Cluster
- Documentación de infraestructura

**Implementación:**
```python
# ubuntu/settings.py
REDIS_CLUSTER_NODES = [
    {'host': 'redis1.example.com', 'port': 6379},
    {'host': 'redis2.example.com', 'port': 6379},
    {'host': 'redis3.example.com', 'port': 6379},
]

# O usar Sentinel
REDIS_SENTINEL = [
    ('sentinel1.example.com', 26379),
    ('sentinel2.example.com', 26379),
    ('sentinel3.example.com', 26379),
]
REDIS_SENTINEL_MASTER = 'mymaster'
```

**Estrategia de Failover y Consistencia:**

1. **Failover Automático:**
   - Sentinel detecta fallo del master en < 30 segundos
   - Promoción automática de replica a master
   - Cliente Redis se reconecta automáticamente al nuevo master
   - Timeout de conexión: 5 segundos con retry exponencial

2. **Consistencia de Claves Temporales:**
   - Claves de rate limiting: TTL corto (60-300s) → pérdida aceptable
   - Claves de semáforo: TTL dinámico (p95 × 1.5) → se regeneran
   - Claves de sesión: TTL largo (1h+) → replicación síncrona recomendada
   - **Evitar race conditions:** Usar operaciones atómicas (SET NX, Lua scripts)

3. **Manejo de Pérdida Parcial:**
   - Si falla un nodo del cluster: redistribución automática de slots
   - Si falla el master: failover a replica (pérdida de writes en tránsito)
   - Circuit breaker: fallback a modo degradado si Redis no disponible

4. **Configuración Recomendada:**
   ```python
   # Configuración con Sentinel
   REDIS_SENTINEL = [
       ('sentinel1.example.com', 26379),
       ('sentinel2.example.com', 26379),
       ('sentinel3.example.com', 26379),
   ]
   REDIS_SENTINEL_MASTER = 'mymaster'
   REDIS_SOCKET_CONNECT_TIMEOUT = 5
   REDIS_SOCKET_TIMEOUT = 5
   REDIS_RETRY_ON_TIMEOUT = True
   REDIS_MAX_CONNECTIONS = 50
   
   # Circuit breaker para Redis
   REDIS_CIRCUIT_BREAKER_THRESHOLD = 5  # Fallos consecutivos
   REDIS_CIRCUIT_BREAKER_TIMEOUT = 60  # Segundos
   ```

**Criterios de aceptación:**
- [ ] Redis Cluster/Sentinel configurado
- [ ] Failover automático funciona (< 30 segundos)
- [ ] Estrategia de consistencia documentada
- [ ] Circuit breaker implementado
- [ ] No hay race conditions entre nodos
- [ ] Pérdida de datos mínima en failover

**Pruebas:**
- Test de failover (kill master, verificar promoción)
- Test de pérdida de nodo (kill nodo del cluster)
- Test de race conditions (múltiples nodos, misma clave)
- Test de circuit breaker (Redis no disponible)
- Verificar que los límites se mantienen después de failover

---

#### Tarea 2.2.2: Aislamiento de Channel Layer
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 2.2.1  
**Asignado a:** Backend Developer

**Objetivo:**
Separar Redis para rate-limit/semaforización del channel layer (dos clústeres).

**Archivos a modificar:**
- `ubuntu/settings.py` - Configurar dos instancias de Redis

**Implementación:**
```python
# ubuntu/settings.py
# Redis para cache y rate limiting
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_RATE_LIMIT_URL,  # Cluster 1
        ...
    }
}

# Redis para channel layer (WebSockets)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": REDIS_CHANNEL_LAYER_URL,  # Cluster 2
        },
    }
}
```

**Criterios de aceptación:**
- [ ] Dos instancias de Redis separadas
- [ ] Rate limiting usa Cluster 1
- [ ] Channel layer usa Cluster 2

**Pruebas:**
- Verificar que funcionan independientemente
- Test de carga en ambos

---

### Sprint 2.3: Backpressure Multicapa (3-4 días)

#### Tarea 2.3.1: Cola de Entrada en Gateway
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 2.1.3  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar cola de entrada con límite de tiempo y respuestas 503 con Retry-After.

**Archivos a crear:**
- `udid/utils/request_queue.py` - Cola de requests

**Implementación:**
```python
# udid/utils/request_queue.py
import asyncio
from collections import deque
import time

class RequestQueue:
    """
    Cola de entrada para requests con límite de tiempo.
    """
    
    def __init__(self, max_size=1000, max_wait_time=10):
        self.queue = deque()
        self.max_size = max_size
        self.max_wait_time = max_wait_time
        self.lock = asyncio.Lock()
    
    async def enqueue(self, request_id, priority=0):
        """Agrega request a la cola"""
        async with self.lock:
            if len(self.queue) >= self.max_size:
                return False, 0  # Cola llena
            
            self.queue.append({
                'request_id': request_id,
                'priority': priority,
                'enqueued_at': time.time()
            })
            return True, len(self.queue)
    
    async def dequeue(self):
        """Saca request de la cola (prioridad alta primero)"""
        async with self.lock:
            if not self.queue:
                return None
            
            # Ordenar por prioridad
            self.queue = deque(sorted(self.queue, key=lambda x: x['priority'], reverse=True))
            
            item = self.queue.popleft()
            
            # Verificar timeout
            wait_time = time.time() - item['enqueued_at']
            if wait_time > self.max_wait_time:
                return None  # Timeout
            
            return item
```

**Criterios de aceptación:**
- [ ] Cola funciona correctamente
- [ ] Respeta límite de tamaño
- [ ] Timeout funciona

**Pruebas:**
- Test con cola llena
- Test de timeout
- Test de prioridades

---

#### Tarea 2.3.2: Degradación Elegante
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 2.3.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar degradación elegante ante ráfagas 2-3×.

**Archivos a crear:**
- `udid/utils/degradation.py` - Lógica de degradación

**Implementación:**
```python
# udid/utils/degradation.py
def should_degrade(current_load, baseline_load=100):
    """
    Determina si el sistema debe degradar funcionalidades.
    """
    ratio = current_load / baseline_load
    
    if ratio >= 3.0:
        return 'critical'  # Degradación máxima
    elif ratio >= 2.0:
        return 'high'  # Degradación moderada
    elif ratio >= 1.5:
        return 'medium'  # Degradación mínima
    else:
        return 'none'

def get_degraded_response(level):
    """
    Retorna respuesta degradada según el nivel.
    """
    if level == 'critical':
        return {
            'error': 'Service temporarily unavailable',
            'message': 'System is under extreme load',
            'retry_after': 60
        }, 503
    elif level == 'high':
        return {
            'warning': 'Service degraded',
            'message': 'Some features may be unavailable',
            'retry_after': 30
        }, 200
    else:
        return None, 200
```

**Criterios de aceptación:**
- [ ] Degradación funciona ante ráfagas
- [ ] Respuestas apropiadas
- [ ] Sistema se recupera

**Pruebas:**
- Test con ráfagas 2-3×
- Verificar degradación
- Test de recuperación

---

## FASE 3: ESCALABILIDAD Y OPERACIÓN (Plan C - Parte 2) - 2-3 semanas

### Sprint 3.1: Feature Flags para Degradación (3-4 días)

#### Tarea 3.1.1: Sistema de Feature Flags
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 2.3.2  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar sistema de feature flags gobernado por métricas.

**Archivos a crear:**
- `udid/models.py` - Modelo `FeatureFlag`
- `udid/utils/feature_flags.py` - Lógica de flags

**Implementación:**
```python
# udid/models.py
class FeatureFlag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_enabled = models.BooleanField(default=True)
    enable_condition = models.JSONField(default=dict)  # {'latency_p95': 5000, 'error_rate': 0.05}
    created_at = models.DateTimeField(auto_now_add=True)

# udid/utils/feature_flags.py
def should_enable_feature(feature_name, current_metrics):
    """
    Determina si una feature debe estar habilitada según métricas.
    """
    try:
        flag = FeatureFlag.objects.get(name=feature_name)
        if not flag.is_enabled:
            return False
        
        conditions = flag.enable_condition
        for metric, threshold in conditions.items():
            if current_metrics.get(metric, 0) > threshold:
                return False
        
        return True
    except FeatureFlag.DoesNotExist:
        return True  # Por defecto habilitado
```

**Criterios de aceptación:**
- [ ] Feature flags funcionan
- [ ] Gobernados por métricas
- [ ] Se pueden cambiar dinámicamente

**Pruebas:**
- Test de flags
- Test con métricas
- Test de cambios dinámicos

---

#### Tarea 3.1.2: Respuestas Simplificadas
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 3.1.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar respuestas simplificadas cuando features están deshabilitadas.

**Archivos a modificar:**
- `udid/views.py` - Agregar lógica de respuestas simplificadas

**Implementación:**
```python
# udid/views.py
def get_simplified_response(request, full_response):
    """
    Retorna respuesta simplificada si features están deshabilitadas.
    """
    from udid.utils.feature_flags import should_enable_feature
    from udid.utils.metrics import _metrics
    
    metrics = _metrics.get_metrics()
    
    # Verificar si feature de respuestas completas está habilitada
    if not should_enable_feature('full_responses', metrics):
        # Retornar respuesta simplificada
        return {
            'status': 'success',
            'data': full_response.get('essential_data'),
            'degraded': True
        }
    
    return full_response
```

**Criterios de aceptación:**
- [ ] Respuestas simplificadas funcionan
- [ ] Bajo presión, p95 baja
- [ ] Throughput aumenta

**Pruebas:**
- Test con features deshabilitadas
- Medir mejoras en latencia
- Test de throughput

---

### Sprint 3.2: WebSocket Concentrator (3-4 días)

#### Tarea 3.2.1: Gateway Lógico para WS
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 1.5.1  
**Asignado a:** Backend Developer

**Objetivo:**
Implementar gateway lógico con conteo por token, límites de suscripciones, batch de mensajes.

**Archivos a crear:**
- `udid/ws_gateway.py` - Gateway para WebSockets

**Implementación:**
```python
# udid/ws_gateway.py
class WebSocketGateway:
    """
    Gateway lógico para WebSockets con límites y batching.
    """
    
    def __init__(self):
        self.connections = {}  # token -> [connections]
        self.message_queue = {}  # token -> [messages]
    
    def register_connection(self, token, connection):
        """Registra una conexión"""
        if token not in self.connections:
            self.connections[token] = []
        
        # Verificar límite
        if len(self.connections[token]) >= 5:
            return False
        
        self.connections[token].append(connection)
        return True
    
    async def send_batch(self, token, messages):
        """Envía mensajes en batch"""
        if token not in self.connections:
            return
        
        # Agrupar mensajes
        batch = {
            'type': 'batch',
            'messages': messages
        }
        
        # Enviar a todas las conexiones
        for connection in self.connections[token]:
            try:
                await connection.send(json.dumps(batch))
            except:
                pass
```

**Criterios de aceptación:**
- [ ] Gateway funciona
- [ ] Límites se respetan
- [ ] Batching funciona

**Pruebas:**
- Test con múltiples conexiones
- Test de batching
- Test de límites

---

### Sprint 3.3: SLOs, Alertas y Simulacros (4-5 días)

#### Tarea 3.3.1: Definir SLOs y Error Budget
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 1.6.1  
**Asignado a:** DevOps/Backend Developer

**Objetivo:**
Definir SLOs (p95, error budget) y sistema de alertas.

**Archivos a crear:**
- `udid/slos.py` - Definición de SLOs
- `udid/utils/alerting.py` - Sistema de alertas

**Implementación:**
```python
# udid/slos.py
SLO_DEFINITIONS = {
    'latency_p95': {
        'target': 2000,  # 2 segundos
        'error_budget': 0.01,  # 1% de error budget
    },
    'error_rate': {
        'target': 0.02,  # 2% de errores
        'error_budget': 0.01,
    },
    'availability': {
        'target': 0.99,  # 99% de disponibilidad
        'error_budget': 0.01,
    }
}

# udid/utils/alerting.py
def check_slo_breach(metrics):
    """
    Verifica si se está incumpliendo algún SLO.
    """
    breaches = []
    
    for slo_name, slo_def in SLO_DEFINITIONS.items():
        current_value = metrics.get(slo_name)
        target = slo_def['target']
        
        if current_value > target:
            breaches.append({
                'slo': slo_name,
                'current': current_value,
                'target': target,
                'breach': current_value - target
            })
    
    return breaches
```

**Criterios de aceptación:**
- [ ] SLOs definidos
- [ ] Sistema de alertas funciona
- [ ] Alertas se envían correctamente

**Pruebas:**
- Test de breach de SLO
- Verificar alertas
- Test de error budget

---

#### Tarea 3.3.2: GameDays y Simulacros
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 3.3.1  
**Asignado a:** DevOps/QA

**Objetivo:**
Implementar simulacros mensuales de picos y fallas.

**Archivos a crear:**
- `udid/management/commands/simulate_load.py` - Comando de simulación
- `udid/management/commands/simulate_failure.py` - Comando de falla

**Implementación:**
```python
# udid/management/commands/simulate_load.py
class Command(BaseCommand):
    help = 'Simula pico de carga para GameDay'
    
    def add_arguments(self, parser):
        parser.add_argument('--users', type=int, default=1000)
        parser.add_argument('--duration', type=int, default=300)
    
    def handle(self, *args, **options):
        # Simular pico de carga
        # Ejecutar test de carga
        # Monitorear SLOs
        # Reportar resultados
        pass
```

**Criterios de aceptación:**
- [ ] Simulacros funcionan
- [ ] Se ejecutan mensualmente
- [ ] 2 simulacros consecutivos sin incumplir SLO

**Pruebas:**
- Ejecutar simulacro
- Verificar que SLOs se mantienen
- Documentar resultados

---

## Pruebas de Resiliencia

### Sprint 3.4: Pruebas de Resiliencia (2-3 días)

#### Tarea 3.4.1: Suite de Pruebas de Resiliencia
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 2.2.1, Tarea 3.3.1  
**Asignado a:** QA/DevOps/Backend Developer

**Objetivo:**
Implementar suite completa de pruebas de resiliencia para validar circuit breaker, degradación y recuperación ante fallos.

**Archivos a crear:**
- `udid/tests/test_resilience.py` - Suite de pruebas de resiliencia
- `udid/management/commands/test_resilience.py` - Comando para ejecutar pruebas

**Pruebas a Implementar:**

1. **Test: Kill Redis Node**
   ```python
   def test_redis_node_failure():
       """
       Simula fallo de nodo Redis y verifica:
       - Circuit breaker se activa
       - Sistema degrada funcionalidades
       - Recuperación automática después de failover
       """
       # 1. Matar nodo Redis master
       # 2. Verificar que circuit breaker se activa
       # 3. Verificar que sistema sigue funcionando (modo degradado)
       # 4. Verificar que failover ocurre (< 30 segundos)
       # 5. Verificar que sistema se recupera
   ```

2. **Test: Flood de WebSocket**
   ```python
   def test_websocket_flood():
       """
       Simula flood de conexiones WebSocket y verifica:
       - Límites se respetan (máx 5 por token, 1000 global)
       - Conexiones excedentes se rechazan
       - Sistema no colapsa
       - Conexiones inactivas se cierran
       """
       # 1. Abrir 2000 conexiones WebSocket simultáneas
       # 2. Verificar que solo 1000 se aceptan
       # 3. Verificar que las demás reciben error apropiado
       # 4. Verificar que conexiones inactivas se cierran
   ```

3. **Test: Picos > 3× Capacidad**
   ```python
   def test_load_spike_3x():
       """
       Simula pico de carga 3× la capacidad normal y verifica:
       - Semáforo global limita concurrencia
       - Rate limiting funciona
       - Degradación elegante se activa
       - Circuit breaker protege recursos
       - Sistema se recupera después del pico
       """
       # 1. Generar 3000 requests simultáneos (3× capacidad)
       # 2. Verificar que semáforo limita a 500
       # 3. Verificar que rate limiting rechaza excesos
       # 4. Verificar que degradación se activa
       # 5. Verificar que sistema se recupera
   ```

4. **Test: Fallo de Base de Datos**
   ```python
   def test_database_failure():
       """
       Simula fallo de base de datos y verifica:
       - Circuit breaker se activa
       - Respuestas de error apropiadas
       - Sistema no colapsa
       - Recuperación después de restauración
       """
       # 1. Simular fallo de BD (cerrar conexiones)
       # 2. Verificar que circuit breaker se activa
       # 3. Verificar que requests reciben 503
       # 4. Restaurar BD
       # 5. Verificar que sistema se recupera
   ```

5. **Test: Race Conditions en Redis**
   ```python
   def test_redis_race_conditions():
       """
       Verifica que no hay race conditions en operaciones Redis:
       - Semáforo global (múltiples nodos)
       - Token bucket (múltiples requests simultáneos)
       - Rate limiting (múltiples instancias)
       """
       # 1. Múltiples requests simultáneos al mismo recurso
       # 2. Verificar que operaciones son atómicas
       # 3. Verificar que no hay pérdida de datos
       # 4. Verificar que límites se respetan
   ```

6. **Test: Recuperación Completa del Sistema**
   ```python
   def test_full_system_recovery():
       """
       Simula fallo completo y verifica recuperación:
       - Redis down → Circuit breaker
       - BD down → Circuit breaker
       - Recuperación gradual
       - SLOs se mantienen después de recuperación
       """
       # 1. Simular fallo completo (Redis + BD)
       # 2. Verificar que circuit breakers se activan
       # 3. Restaurar servicios gradualmente
       # 4. Verificar que sistema se recupera
       # 5. Verificar que SLOs se mantienen
   ```

**Implementación:**
```python
# udid/tests/test_resilience.py
import pytest
import time
import subprocess
from django.test import TestCase
from django.conf import settings
import redis

class ResilienceTests(TestCase):
    """Suite de pruebas de resiliencia"""
    
    def setUp(self):
        self.redis_client = redis.from_url(settings.REDIS_URL)
        self.original_redis_url = settings.REDIS_URL
    
    def test_redis_node_failure(self):
        """Test de fallo de nodo Redis"""
        # Simular fallo (desconectar Redis)
        # ... implementación ...
        pass
    
    def test_websocket_flood(self):
        """Test de flood de WebSocket"""
        # Abrir 2000 conexiones
        # ... implementación ...
        pass
    
    def test_load_spike_3x(self):
        """Test de pico 3× capacidad"""
        # Generar 3000 requests
        # ... implementación ...
        pass
    
    # ... más tests ...
```

**Criterios de aceptación:**
- [ ] Suite de pruebas de resiliencia implementada
- [ ] Test de kill Redis node pasa
- [ ] Test de flood WebSocket pasa
- [ ] Test de picos > 3× pasa
- [ ] Test de fallo de BD pasa
- [ ] Test de race conditions pasa
- [ ] Test de recuperación completa pasa
- [ ] Todos los tests documentados
- [ ] Tests se ejecutan en CI/CD

**Pruebas:**
- Ejecutar suite completa de pruebas
- Verificar que todos los tests pasan
- Documentar resultados
- Integrar en pipeline CI/CD

---

#### Tarea 3.4.2: GameDays y Simulacros Mensuales
**Prioridad:** 🟢 MEDIA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 3.4.1  
**Asignado a:** DevOps/QA

**Objetivo:**
Establecer proceso de GameDays mensuales para validar resiliencia en producción.

**Archivos a crear:**
- `udid/management/commands/gameday.py` - Comando para GameDays
- `docs/gameday_procedures.md` - Procedimientos de GameDay

**Procedimientos:**

1. **Preparación:**
   - Notificar al equipo
   - Backup de datos críticos
   - Monitoreo activo
   - Rollback plan listo

2. **Ejecución:**
   - Simular fallo de Redis node
   - Simular flood de WebSocket
   - Simular pico de carga 3×
   - Monitorear métricas y SLOs

3. **Post-Ejecución:**
   - Análisis de resultados
   - Documentar lecciones aprendidas
   - Mejoras identificadas
   - Actualizar runbooks

**Criterios de aceptación:**
- [ ] GameDays mensuales establecidos
- [ ] Procedimientos documentados
- [ ] 2 simulacros consecutivos sin incumplir SLO
- [ ] Lecciones aprendidas documentadas

**Pruebas:**
- Ejecutar GameDay de prueba
- Verificar que procedimientos funcionan
- Documentar resultados

---

## Resumen de Tareas

### Fase 1 (Plan A) - 48-72 horas
- ✅ 6 sprints
- ✅ 12 tareas
- ✅ Prioridad: 🔴 CRÍTICA

### Fase 2 (Plan C - Parte 1) - 1-2 semanas
- ✅ 3 sprints
- ✅ 6 tareas
- ✅ Prioridad: 🟡 ALTA

### Fase 3 (Plan C - Parte 2) - 2-3 semanas
- ✅ 4 sprints (incluye pruebas de resiliencia)
- ✅ 8 tareas (incluye 2 tareas de resiliencia)
- ✅ Prioridad: 🟢 MEDIA

**Total:** 13 sprints, 26 tareas, 4-6 semanas

---

## Criterios de Éxito Global

### Fase 1 (Plan A)
- [ ] Sistema sostiene 800-1000 concurrentes
- [ ] Error rate < 2%
- [ ] Latencia p95 < 5 segundos
- [ ] CPU < 80% en picos
- [ ] Locks BD reducidos 50-60%

### Fase 2 (Plan C - Parte 1)
- [ ] API keys funcionando
- [ ] Redis HA operativo
- [ ] Backpressure implementado
- [ ] Degradación elegante funcionando

### Fase 3 (Plan C - Parte 2)
- [ ] Feature flags operativos
- [ ] WebSocket concentrator funcionando
- [ ] SLOs definidos y monitoreados
- [ ] Suite de pruebas de resiliencia implementada
- [ ] GameDays mensuales establecidos
- [ ] 2 simulacros consecutivos sin incumplir SLO

---

**Fecha de creación:** 2025-01-XX  
**Estado:** ✅ Plan completo y listo para ejecución

