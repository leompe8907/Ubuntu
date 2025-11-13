# Respuestas a Preguntas sobre el Sistema UDID

**Fecha:** 2025-01-27

---

## 1. ¿Cómo va a identificar a los dispositivos?

### Sistema de Identificación Multi-Capa

El sistema identifica dispositivos usando **Device Fingerprint** (huella digital del dispositivo) basado en múltiples factores:

#### **Para Móviles (Android/iOS):**
El sistema combina los siguientes headers HTTP para generar un hash único:

```python
# Headers utilizados:
- X-Device-ID: ID único del dispositivo (Android ID, iOS IdentifierForVendor)
- X-App-Type: Tipo de aplicación (android_mobile, ios_mobile, mobile_app)
- X-App-Version: Versión de la aplicación
- X-OS-Version: Versión del sistema operativo
- X-Device-Model: Modelo del dispositivo
- X-Build-ID: Build fingerprint (Android) - opcional pero recomendado
- User-Agent: Agente de usuario
```

**Fórmula de fingerprint para móviles:**
```
fingerprint = SHA256(
    app_type | device_id | build_id | device_model | os_version | app_version | user_agent
)[:32]
```

#### **Para Smart TVs:**
El sistema usa identificadores más específicos del hardware:

```python
# Headers utilizados:
- X-TV-Serial: Número de serie de la TV (MUY IMPORTANTE - único del hardware)
- X-TV-Model: Modelo específico de la TV
- X-Firmware-Version: Versión del firmware
- X-Device-ID: ID único del dispositivo
- X-App-Type: Tipo de aplicación (android_tv, samsung_tv, lg_tv, set_top_box)
- X-App-Version: Versión de la aplicación
- User-Agent: Agente de usuario
```

**Fórmula de fingerprint para Smart TVs:**
```
fingerprint = SHA256(
    app_type | tv_serial | tv_model | firmware_version | device_id | app_version | user_agent
)[:32]
```

#### **Fallback (si no hay headers específicos):**
Si el dispositivo no envía los headers específicos, se usa un fingerprint básico:
```
fingerprint = SHA256(
    user_agent | accept_language | accept_encoding | accept | app_type | app_version | device_id
)[:32]
```

**Ubicación del código:** `udid/util.py:136-178`

**Características:**
- ✅ **Único por dispositivo:** Mismo dispositivo siempre genera mismo fingerprint
- ✅ **Difícil de falsificar:** Usa identificadores nativos del hardware
- ✅ **Funciona sin UDID:** Se usa antes de tener UDID (primera solicitud)
- ✅ **Compatible con WebSockets:** Funciona tanto en HTTP como en WebSocket

---

## 2. ¿Ese dispositivo mobile y/o TVs cuántas veces puede realizar la misma consulta?

### Límites de Rate Limiting por Tipo de Endpoint

El sistema tiene **múltiples capas de rate limiting** con límites diferentes según el tipo de operación:

#### **A) Solicitud de UDID (`/request-udid/`):**
- **Límite:** 2 requests por dispositivo
- **Ventana:** 10 minutos
- **Identificador:** Device Fingerprint
- **Ubicación:** `udid/views.py:90-94`

```python
check_device_fingerprint_rate_limit(
    device_fingerprint,
    max_requests=2,  # Reducido de 3 a 2
    window_minutes=10  # Aumentado de 5 a 10 minutos
)
```

#### **B) Validación y Asociación de UDID (`/validate-and-associate-udid/`):**
- **Límite:** 5 requests por UDID
- **Ventana:** 60 minutos (1 hora)
- **Identificador:** UDID
- **Ubicación:** `udid/views.py:245-249`

```python
check_udid_rate_limit(
    udid,
    max_requests=5,  # Reducido de 10 a 5
    window_minutes=60
)
```

#### **C) Autenticación con UDID (`/authenticate-with-udid/`):**
- **Límite:** 5 requests por UDID
- **Ventana:** 60 minutos (1 hora)
- **Identificador:** UDID
- **Ubicación:** `udid/views.py:444-448`

```python
check_udid_rate_limit(
    udid,
    max_requests=5,  # Reducido de 10 a 5
    window_minutes=60
)
```

#### **D) Validación de Estado (`/validate/`):**
- **Límite:** 20 requests por UDID
- **Ventana:** 5 minutos
- **Identificador:** UDID
- **Ubicación:** `udid/views.py:730-734`

```python
check_udid_rate_limit(
    udid,
    max_requests=20,  # Reducido de 30 a 20
    window_minutes=5
)
```

#### **E) Desasociación de UDID (`/disassociate-udid/`):**
- **Límite:** 5 requests por UDID
- **Ventana:** 60 minutos (1 hora)
- **Identificador:** UDID
- **Ubicación:** `udid/views.py:926-930`

#### **F) Token Bucket (Límite adicional por cliente):**
Además de los límites por UDID/Device Fingerprint, hay un **token bucket** que limita por cliente:

- **Request UDID Manual:** 3 tokens, 1 token/segundo
- **Validar y Asociar:** 5 tokens, 1 token/segundo
- **Autenticar:** 10 tokens, 1 token/segundo
- **Validar Estado:** 20 tokens, 1 token/segundo
- **Desasociar:** 5 tokens, 1 token/segundo

**Ubicación:** `udid/views.py` - múltiples endpoints

---

## 3. ¿Para los dispositivos móviles y/o TVs tienen penalizaciones por exceso de intento de llamadas?

### Sistema de Penalizaciones

**SÍ, el sistema tiene penalizaciones progresivas:**

#### **A) Rate Limiting con Bloqueo Temporal:**
Cuando un dispositivo excede el límite, recibe:
- **HTTP 429 (Too Many Requests)**
- **Header `Retry-After`:** Tiempo en segundos que debe esperar
- **Mensaje de error:** "Rate limit exceeded"

**Ejemplo de respuesta:**
```json
{
    "error": "Rate limit exceeded",
    "message": "Too many requests. Please retry later.",
    "retry_after": 600,  // segundos (10 minutos)
    "remaining": 0
}
```

#### **B) Sistema de Violaciones (Tracking de Abuso):**
El sistema rastrea violaciones de rate limiting:

```python
# Ubicación: udid/util.py:977-985
violation_key = f"rate_limit_violations:{identifier_type}:{identifier}"
violations = cache.get(violation_key, 0) + 1
cache.set(violation_key, violations, timeout=window_minutes * 60 * 2)
```

**Consecuencias:**
- Las violaciones se acumulan
- Pueden afectar límites adaptativos futuros
- Se registran en logs para auditoría

#### **C) Rate Limiting Adaptativo (Penalizaciones Progresivas):**
El sistema ajusta límites según la carga y comportamiento:

**Ubicación:** `udid/util.py:902-997`

**Niveles de degradación:**
1. **Normal:** Límites base
2. **Medium:** Límites base × 1.5
3. **High:** Límites base × 2.0
4. **Critical:** Límites base × 3.0

**En modo crítico:**
- Límites se reducen a la mitad o un tercio
- Ventanas de tiempo se duplican o triplican
- Circuit breaker puede activarse

#### **D) Exponential Backoff con Jitter:**
Para reconexiones, el sistema aplica delays exponenciales:

**Ubicación:** `udid/util.py:1098-1140`

**Fórmula:**
```python
base_delay = 2 ** attempt_number  # 2, 4, 8, 16, 32 segundos...
jitter = random.uniform(0, base_delay * 0.3)  # 30% de variación
retry_delay = base_delay + jitter
```

**Máximo:** 24 horas de backoff

#### **E) Bloqueo de WebSocket:**
Si un dispositivo intenta abrir demasiadas conexiones WebSocket:
- **Código de cierre:** 4001
- **Mensaje:** "Too many connections. Retry after Xs"
- **Límite:** 5 conexiones por dispositivo/UDID

**Ubicación:** `udid/consumers.py:76-93`

---

## 4. ¿El WS tiene más de una forma de abrirse?

### Formas de Abrir WebSocket

**NO, actualmente solo hay UNA forma de abrir el WebSocket:**

#### **Única Ruta:**
```
ws://<host>/ws/auth/
```

**Ubicación:** `udid/routing.py:7`

```python
websocket_urlpatterns = [
    re_path(r"^ws/auth/$", AuthWaitWS.as_asgi()),
]
```

#### **Protocolo de Conexión:**

1. **Conexión inicial:**
   - Cliente se conecta a `ws://host/ws/auth/`
   - El servidor verifica rate limits
   - Si pasa, acepta la conexión

2. **Mensaje de autenticación:**
   ```json
   {
       "type": "auth_with_udid",
       "udid": "abc12345",
       "app_type": "android_tv",
       "app_version": "1.0"
   }
   ```

3. **Respuestas posibles:**
   - **Si está listo:** Envía credenciales y cierra
   - **Si no está listo:** Responde "pending" y espera evento
   - **Si hay error:** Envía error y cierra

**Ubicación:** `udid/consumers.py:110-249`

#### **Nota:**
Aunque solo hay una ruta, el WebSocket puede usarse de dos formas:
1. **Push (evento):** Espera a que el servidor notifique cuando el UDID esté validado
2. **Polling (respaldo):** Si está habilitado, consulta periódicamente el estado

**Configuración de polling:**
```python
# settings.py
UDID_ENABLE_POLLING = os.getenv("UDID_ENABLE_POLLING", "0") == "1"
UDID_POLL_INTERVAL = int(os.getenv("UDID_POLL_INTERVAL", "2"))  # segundos
```

**Ubicación:** `udid/consumers.py:247-344`

---

## 5. ¿El WS cuánto tiempo dura abierto?

### Timeouts y Duración del WebSocket

El WebSocket tiene **múltiples timeouts** configurados:

#### **A) Timeout Principal (Espera de Validación):**
- **Duración:** 60 segundos (configurable)
- **Configuración:** `UDID_WAIT_TIMEOUT`
- **Ubicación:** `ubuntu/settings.py:146`

```python
UDID_WAIT_TIMEOUT = int(os.getenv("UDID_WAIT_TIMEOUT", "60"))  # Reducido de 600 a 60 segundos
```

**Comportamiento:**
- Si el UDID no se valida en 60 segundos, el WebSocket se cierra
- Envía mensaje: `{"type": "timeout", "detail": "No se recibió validación/asociación a tiempo."}`

**Ubicación:** `udid/consumers.py:304-308`

#### **B) Timeout de Inactividad:**
- **Duración:** 60 segundos (configurable)
- **Configuración:** `UDID_WS_INACTIVITY_TIMEOUT`
- **Ubicación:** `udid/consumers.py:45`

```python
INACTIVITY_TIMEOUT = getattr(settings, "UDID_WS_INACTIVITY_TIMEOUT", 60)  # segundos
```

**Comportamiento:**
- Si no hay actividad (mensajes) por 60 segundos, se cierra
- Verifica cada 10 segundos
- Envía mensaje de error antes de cerrar

**Ubicación:** `udid/consumers.py:367-383`

#### **C) Ping/Pong (Mantener Conexión Viva):**
- **Intervalo:** 30 segundos (configurable)
- **Configuración:** `UDID_WS_PING_INTERVAL`
- **Ubicación:** `udid/consumers.py:44`

```python
PING_INTERVAL = getattr(settings, "UDID_WS_PING_INTERVAL", 30)  # segundos
```

**Comportamiento:**
- El servidor envía `{"type": "ping"}` cada 30 segundos
- El cliente debe responder con `{"type": "pong"}`
- Si no hay respuesta, la conexión puede cerrarse

**Ubicación:** `udid/consumers.py:353-365`

#### **D) Cierre Automático:**
El WebSocket se cierra automáticamente cuando:
1. ✅ Se reciben las credenciales (éxito)
2. ✅ Se recibe un error fatal
3. ✅ Se alcanza el timeout de validación (60s)
4. ✅ Se alcanza el timeout de inactividad (60s)
5. ✅ El cliente se desconecta

**Resumen de Duración:**
- **Mínimo:** Inmediato (si ya está validado)
- **Máximo:** 60 segundos (timeout de validación o inactividad)
- **Típico:** 5-30 segundos (esperando validación)

---

## 6. ¿Cuántos WS se pueden abrir en general?

### Límites de Conexiones WebSocket

El sistema tiene **dos niveles de límites** para WebSockets:

#### **A) Límite por Dispositivo/UDID:**
- **Máximo:** 5 conexiones simultáneas por dispositivo/UDID
- **Configuración:** `UDID_WS_MAX_PER_TOKEN`
- **Ubicación:** `udid/consumers.py:46`

```python
MAX_CONNECTIONS_PER_TOKEN = getattr(settings, "UDID_WS_MAX_PER_TOKEN", 5)
```

**Comportamiento:**
- Si un dispositivo intenta abrir más de 5 conexiones, las adicionales son rechazadas
- Código de cierre: 4001
- Mensaje: "Too many connections. Retry after Xs"

**Ubicación:** `udid/consumers.py:68-93`

#### **B) Límite Global del Sistema:**
- **Máximo:** 1000 conexiones simultáneas en todo el sistema
- **Configuración:** `UDID_WS_MAX_GLOBAL`
- **Ubicación:** `udid/consumers.py:47`

```python
MAX_GLOBAL_CONNECTIONS = getattr(settings, "UDID_WS_MAX_GLOBAL", 1000)
```

**Comportamiento:**
- Si el sistema tiene 1000 conexiones activas, nuevas conexiones son rechazadas
- Protege contra saturación del servidor
- Código de cierre: 4001

**Ubicación:** `udid/consumers.py:68-93`

#### **C) Rate Limiting Adicional (Sistema Anterior):**
Además de los límites nuevos, hay un sistema de rate limiting por ventana de tiempo:

- **Máximo:** 5 conexiones
- **Ventana:** 5 minutos
- **Identificador:** Device Fingerprint o UDID

**Ubicación:** `udid/util.py:361-401`

#### **Resumen:**
```
Límite por dispositivo: 5 conexiones simultáneas
Límite global: 1000 conexiones simultáneas
Límite por ventana: 5 conexiones cada 5 minutos
```

**Nota:** Los límites se aplican en cascada - si cualquiera se excede, la conexión es rechazada.

---

## 7. ¿El proyecto tiene un sistema de cola en el momento de cuando se saturan las consultas?

### Sistema de Cola y Backpressure

**SÍ, el proyecto tiene un sistema completo de cola y backpressure:**

#### **A) Cola de Requests:**
**Ubicación:** `udid/utils/request_queue.py`

**Características:**
- **Tamaño máximo:** 1000 requests (configurable)
- **Tiempo máximo de espera:** 10 segundos (configurable)
- **Prioridades:** Soporta priorización de requests
- **Thread-safe:** Usa locks para concurrencia

**Configuración:**
```python
# settings.py
REQUEST_QUEUE_MAX_SIZE = int(os.getenv("REQUEST_QUEUE_MAX_SIZE", "1000"))
REQUEST_QUEUE_MAX_WAIT_TIME = int(os.getenv("REQUEST_QUEUE_MAX_WAIT_TIME", "10"))  # segundos
```

**Comportamiento:**
1. Si la cola está llena, nuevos requests son rechazados (HTTP 503)
2. Si un request espera más de 10 segundos, se descarta
3. Los requests se procesan por prioridad (mayor número = mayor prioridad)

**Ubicación:** `udid/utils/request_queue.py:14-174`

#### **B) Middleware de Backpressure:**
**Ubicación:** `udid/middleware.py:206-346`

**Funcionalidad:**
- Detecta cuando el sistema está bajo carga
- Encola requests cuando hay degradación (high/critical)
- Rechaza requests de baja prioridad en modo crítico
- Agrega headers de degradación a las respuestas

**Niveles de degradación:**
1. **None:** Sin degradación, requests se procesan normalmente
2. **Medium:** 1.5x carga base - comienza a encolar
3. **High:** 2.0x carga base - encola más agresivamente
4. **Critical:** 3.0x carga base - rechaza requests de baja prioridad

**Configuración:**
```python
# settings.py
DEGRADATION_BASELINE_LOAD = int(os.getenv("DEGRADATION_BASELINE_LOAD", "100"))
DEGRADATION_MEDIUM_THRESHOLD = float(os.getenv("DEGRADATION_MEDIUM_THRESHOLD", "1.5"))
DEGRADATION_HIGH_THRESHOLD = float(os.getenv("DEGRADATION_HIGH_THRESHOLD", "2.0"))
DEGRADATION_CRITICAL_THRESHOLD = float(os.getenv("DEGRADATION_CRITICAL_THRESHOLD", "3.0"))
```

#### **C) Semáforo Global de Concurrencia:**
**Ubicación:** `udid/middleware.py:55-106`

**Funcionalidad:**
- Limita la concurrencia total del sistema
- **Límite:** 500 slots simultáneos (configurable)
- Si se alcanza el límite, nuevos requests reciben HTTP 503

**Configuración:**
```python
# settings.py
GLOBAL_SEMAPHORE_SLOTS = int(os.getenv("GLOBAL_SEMAPHORE_SLOTS", "500"))
```

**Comportamiento:**
- Cada request adquiere un slot al inicio
- Libera el slot al finalizar (incluso si hay error)
- Timeout dinámico basado en latencia p95

#### **D) Circuit Breaker:**
**Ubicación:** `udid/utils/redis_ha.py:16-101`

**Funcionalidad:**
- Detecta cuando Redis está caído o saturado
- Entra en modo "OPEN" después de 10 fallos consecutivos
- Rechaza requests que dependen de Redis
- Se recupera automáticamente después de 30 segundos

**Estados:**
- **CLOSED:** Funcionando normalmente
- **OPEN:** Fallos detectados, rechazando requests
- **HALF_OPEN:** Probando si Redis se recuperó

#### **Resumen del Sistema de Cola:**
```
1. Semáforo Global (500 slots) → Limita concurrencia total
2. Circuit Breaker → Protege si Redis falla
3. Backpressure Middleware → Encola si hay degradación
4. Request Queue (1000 slots) → Cola con prioridades
5. Rate Limiting → Rechaza antes de encolar
```

**Flujo:**
```
Request → Semáforo → Circuit Breaker → Rate Limiting → Backpressure → Cola → Procesamiento
```

---

## 8. ¿Cuáles son las proyecciones que tiene el proyecto para evitar ataques?

### Protecciones Contra Ataques Implementadas

El proyecto tiene **múltiples capas de protección** contra diferentes tipos de ataques:

#### **A) Protección DDoS Multi-Capa:**

**1. Rate Limiting Multi-Capa:**
- **Capa 1:** Device Fingerprint (2 requests/10min)
- **Capa 2:** Token Bucket (límites por segundo)
- **Capa 3:** UDID Rate Limiting (5-20 requests según endpoint)
- **Capa 4:** Plan Rate Limiting (por API key/tenant)
- **Capa 5:** Rate Limiting Adaptativo (ajusta según carga)

**Ubicación:** `udid/util.py`, `udid/views.py`

**2. Identificación Robusta de Dispositivos:**
- Device Fingerprint basado en hardware (serial, device ID, build ID)
- Difícil de falsificar
- Funciona sin IP (importante para NAT)

**Ubicación:** `udid/util.py:136-178`

**3. Circuit Breaker:**
- Protege contra saturación de Redis
- Fail-fast cuando hay problemas
- Recuperación automática

**Ubicación:** `udid/utils/redis_ha.py:16-101`

#### **B) Protección Contra Reconexión Masiva (Thundering Herd):**

**1. Exponential Backoff con Jitter:**
- Distribuye reconexiones en el tiempo
- Evita que todos reconecten simultáneamente
- Delay: 2^n segundos + jitter aleatorio

**Ubicación:** `udid/util.py:1098-1140`

**2. Rate Limiting Adaptativo:**
- Aumenta límites durante reconexiones legítimas
- Reduce límites durante ataques
- Detecta patrones anómalos

**Ubicación:** `udid/util.py:902-997`

**3. Reconocimiento de Reconexión Legítima:**
- Distingue entre reconexión legítima y ataque
- Límites más permisivos para UDIDs válidos existentes
- Límites más restrictivos para nuevas solicitudes

**Ubicación:** `udid/util.py:750-900`

#### **C) Protección de Recursos:**

**1. Semáforo Global:**
- Limita concurrencia total (500 slots)
- Previene saturación del servidor
- Protege base de datos

**Ubicación:** `udid/middleware.py:55-106`

**2. Cola de Requests:**
- Encola requests cuando hay carga
- Priorización de requests
- Timeout automático

**Ubicación:** `udid/utils/request_queue.py`

**3. Degradación Elegante:**
- Reduce funcionalidad en lugar de caer
- Rechaza requests de baja prioridad primero
- Mantiene servicio crítico funcionando

**Ubicación:** `udid/middleware.py:206-346`

#### **D) Protección de WebSockets:**

**1. Límites de Conexión:**
- 5 conexiones por dispositivo/UDID
- 1000 conexiones globales
- Rate limiting por ventana de tiempo

**Ubicación:** `udid/consumers.py:46-47`, `udid/util.py:361-401`

**2. Timeouts:**
- Timeout de validación: 60 segundos
- Timeout de inactividad: 60 segundos
- Ping/pong cada 30 segundos

**Ubicación:** `udid/consumers.py:41-45`

#### **E) Protección de Autenticación:**

**1. Rate Limiting de Login:**
- 5 intentos por usuario/device
- Ventana: 15 minutos
- Bloqueo progresivo

**Ubicación:** `udid/util.py:656-720`

**2. Rate Limiting de Registro:**
- 3 registros por dispositivo
- Ventana: 60 minutos

**Ubicación:** `udid/util.py:721-760`

**3. API Key Authentication:**
- Validación de API keys
- Rate limiting por plan
- Revocación de keys

**Ubicación:** `udid/middleware.py:109-203`

#### **F) Monitoreo y Detección:**

**1. Logging de Auditoría:**
- Registra todas las acciones importantes
- Tracking de violaciones de rate limiting
- Logs asíncronos (no bloquean)

**Ubicación:** `udid/utils/log_buffer.py`

**2. Métricas del Sistema:**
- Latencia p95
- Tasa de errores
- Concurrencia actual
- CPU y RAM

**Ubicación:** `udid/utils/metrics.py`

**3. Dashboard de Métricas:**
- Endpoint `/udid/metrics/` para monitoreo
- Métricas en tiempo real
- Útil para detectar ataques

**Ubicación:** `udid/views.py:1094-1117`

#### **G) Protecciones Futuras (Documentadas):**

**1. Detección de Comportamiento Anómalo:**
- Patrones de uso sospechosos
- Alertas automáticas
- Bloqueo automático

**Ubicación:** `docs/PLAN_IMPLEMENTACION_DDOS.md`

**2. Rate Limiting por Subscriber:**
- Límites por código de suscriptor
- Prevención de abuso de cuentas

**3. Geolocalización:**
- Detección de ubicaciones sospechosas
- Bloqueo por región (opcional)

#### **Resumen de Protecciones:**

```
✅ Rate Limiting Multi-Capa (5 capas)
✅ Device Fingerprint Robusto
✅ Circuit Breaker
✅ Exponential Backoff
✅ Semáforo Global
✅ Cola de Requests
✅ Degradación Elegante
✅ Límites de WebSocket
✅ Protección de Autenticación
✅ Monitoreo y Métricas
✅ Logging de Auditoría
```

**Documentación Completa:** `docs/PLAN_IMPLEMENTACION_DDOS.md`

---

## Resumen Ejecutivo

| Pregunta | Respuesta |
|----------|-----------|
| **1. Identificación** | Device Fingerprint basado en hardware (serial, device ID, build ID) |
| **2. Límites de consulta** | 2-20 requests según endpoint, ventanas de 5-60 minutos |
| **3. Penalizaciones** | Sí: bloqueo temporal, tracking de violaciones, límites adaptativos, exponential backoff |
| **4. Formas de WS** | 1 ruta (`/ws/auth/`), 2 modos (push/polling) |
| **5. Duración WS** | Máximo 60 segundos (timeout de validación o inactividad) |
| **6. Límites WS** | 5 por dispositivo, 1000 globales |
| **7. Sistema de cola** | Sí: cola de 1000 slots, backpressure, semáforo global |
| **8. Protecciones** | 11+ capas: rate limiting, circuit breaker, backpressure, monitoreo |

---

**Última actualización:** 2025-01-27





