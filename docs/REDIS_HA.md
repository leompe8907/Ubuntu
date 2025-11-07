# Redis Alta Disponibilidad (HA) - Documentación

## Resumen

Este documento describe la configuración y estrategia de alta disponibilidad para Redis, incluyendo soporte para Redis Sentinel, circuit breaker, y manejo de failover.

## Configuración

### Variables de Entorno

```bash
# Redis básico (requerido)
REDIS_URL=redis://localhost:6379

# Redis Sentinel (opcional, para HA)
REDIS_SENTINEL=sentinel1.example.com:26379,sentinel2.example.com:26379,sentinel3.example.com:26379
REDIS_SENTINEL_MASTER=mymaster

# Separación de Redis (opcional)
REDIS_CHANNEL_LAYER_URL=redis://redis-ws.example.com:6379  # Para WebSockets
REDIS_RATE_LIMIT_URL=redis://redis-rl.example.com:6379     # Para rate limiting

# Configuración de timeouts
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_TIMEOUT=5
REDIS_RETRY_ON_TIMEOUT=True
REDIS_MAX_CONNECTIONS=50

# Circuit breaker
REDIS_CIRCUIT_BREAKER_THRESHOLD=5   # Fallos consecutivos antes de abrir
REDIS_CIRCUIT_BREAKER_TIMEOUT=60    # Segundos antes de intentar half-open
```

## Estrategia de Failover

### 1. Failover Automático con Sentinel

**Cómo funciona:**
- Sentinel detecta fallo del master en < 30 segundos
- Promoción automática de replica a master
- Cliente Redis se reconecta automáticamente al nuevo master
- Timeout de conexión: 5 segundos con retry exponencial

**Configuración mínima recomendada:**
- 3 instancias de Sentinel (quorum mínimo)
- 1 master + 2 replicas (para alta disponibilidad)
- Configurar `sentinel down-after-milliseconds` a 30000ms (30 segundos)

### 2. Circuit Breaker

**Estados:**
- **CLOSED**: Funcionando normalmente, todas las operaciones se permiten
- **OPEN**: Fallos detectados, rechazando requests (fail-open)
- **HALF_OPEN**: Probando si Redis se recuperó

**Comportamiento:**
- Después de `REDIS_CIRCUIT_BREAKER_THRESHOLD` fallos consecutivos, el circuit breaker se abre
- En estado OPEN, las operaciones Redis retornan `None` (fail-open)
- Después de `REDIS_CIRCUIT_BREAKER_TIMEOUT` segundos, entra en estado HALF_OPEN
- Si una operación tiene éxito en HALF_OPEN, vuelve a CLOSED
- Si falla en HALF_OPEN, vuelve a OPEN

**Fail-Open Strategy:**
- Si Redis no está disponible, el sistema continúa funcionando sin protección de rate limiting
- Esto evita que un fallo de Redis bloquee todo el sistema
- Los rate limits se pierden temporalmente, pero el servicio sigue disponible

## Consistencia de Claves Temporales

### Tipos de Claves y su Manejo

#### 1. Claves de Rate Limiting
- **TTL**: Corto (60-300 segundos)
- **Pérdida en failover**: Aceptable
- **Estrategia**: Se regeneran automáticamente
- **Ejemplos**: `plan_rate_limit:tenant_id:minute`, `token_bucket:identifier`

#### 2. Claves de Semáforo
- **TTL**: Dinámico (p95 × 1.5, mínimo 10s, máximo 60s)
- **Pérdida en failover**: Aceptable (se regeneran)
- **Estrategia**: Se regeneran automáticamente con nuevos requests
- **Ejemplos**: `global_semaphore:slots:uuid`

#### 3. Claves de Sesión/WebSocket
- **TTL**: Largo (1h+)
- **Pérdida en failover**: Crítica
- **Estrategia**: Replicación síncrona recomendada
- **Ejemplos**: `ws_connections:token:identifier`, `ws_connections:global`

### Evitar Race Conditions

**Operaciones Atómicas:**
- Usar `SET NX EX` para operaciones atómicas de creación con TTL
- Usar scripts Lua para operaciones complejas atómicas
- Usar `INCR`/`DECR` para contadores (operaciones atómicas)

**Ejemplo:**
```python
# Operación atómica de semáforo
redis_client.set(
    f"global_semaphore:slots:{slot_id}",
    "1",
    nx=True,  # Solo si no existe
    ex=timeout  # TTL
)
```

## Manejo de Pérdida Parcial

### Escenario 1: Fallo de un Nodo del Cluster
- **Efecto**: Redistribución automática de slots
- **Pérdida de datos**: Mínima (solo claves en el nodo fallido)
- **Recuperación**: Automática cuando el nodo se recupera

### Escenario 2: Fallo del Master (con Sentinel)
- **Efecto**: Failover a replica
- **Pérdida de datos**: Writes en tránsito pueden perderse
- **Recuperación**: Automática (< 30 segundos)

### Escenario 3: Redis Completamente No Disponible
- **Efecto**: Circuit breaker se abre
- **Comportamiento**: Sistema funciona en modo degradado (sin rate limiting)
- **Recuperación**: Automática cuando Redis vuelve (circuit breaker half-open)

## Aislamiento de Channel Layer

### Configuración Recomendada

**Opción 1: Redis Compartido (Desarrollo)**
```bash
REDIS_URL=redis://localhost:6379
# Channel layer y rate limiting usan el mismo Redis
```

**Opción 2: Redis Separado (Producción)**
```bash
REDIS_URL=redis://redis-rl.example.com:6379          # Rate limiting
REDIS_CHANNEL_LAYER_URL=redis://redis-ws.example.com:6379  # WebSockets
```

**Ventajas del aislamiento:**
- Rate limiting no afecta WebSockets y viceversa
- Mejor rendimiento bajo carga
- Escalabilidad independiente

## Monitoreo

### Métricas Importantes

1. **Estado del Circuit Breaker**
   - Estado actual (CLOSED/OPEN/HALF_OPEN)
   - Número de fallos consecutivos
   - Tiempo desde último cambio de estado

2. **Latencia de Redis**
   - Latencia promedio de operaciones
   - Latencia p95/p99
   - Timeouts

3. **Disponibilidad**
   - Uptime de Redis
   - Fallos de conexión
   - Failovers

### Verificar Estado

```python
from udid.utils.redis_ha import get_circuit_breaker_state, is_redis_available

# Estado del circuit breaker
state = get_circuit_breaker_state()  # 'closed', 'open', 'half_open'

# Disponibilidad de Redis
available = is_redis_available()  # True/False
```

## Pruebas

### Test de Failover

1. **Simular fallo del master:**
   ```bash
   # En el servidor Redis master
   redis-cli DEBUG SEGFAULT
   ```

2. **Verificar promoción:**
   - Sentinel debe detectar el fallo en < 30 segundos
   - Una replica debe ser promovida a master
   - El sistema debe continuar funcionando

### Test de Circuit Breaker

1. **Simular Redis no disponible:**
   ```bash
   # Detener Redis
   systemctl stop redis
   ```

2. **Verificar circuit breaker:**
   - Después de 5 fallos, el circuit breaker debe abrirse
   - El sistema debe continuar funcionando (fail-open)
   - Después de 60 segundos, debe entrar en half-open

3. **Recuperación:**
   ```bash
   # Reiniciar Redis
   systemctl start redis
   ```
   - El circuit breaker debe cerrarse después de un éxito

## Mejores Prácticas

1. **Siempre usar operaciones atómicas** (SET NX, Lua scripts)
2. **Configurar TTL apropiados** para cada tipo de clave
3. **Monitorear el circuit breaker** en producción
4. **Usar Redis separado** para rate limiting y WebSockets en producción
5. **Configurar alertas** para fallos de Redis y circuit breaker abierto
6. **Documentar estrategia de recuperación** para cada tipo de clave

## Referencias

- [Redis Sentinel Documentation](https://redis.io/docs/management/sentinel/)
- [Redis High Availability](https://redis.io/docs/management/sentinel/)
- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)

