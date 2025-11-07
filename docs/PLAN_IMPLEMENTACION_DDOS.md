# Plan de Implementación: Protección DDoS para Aplicaciones Móviles y Smart TVs

## Contexto del Proyecto

- **Cliente:** Aplicaciones Android, iOS y Smart TVs
- **Infraestructura:** NAT (muchos usuarios comparten IP pública)
- **Limitación:** No podemos usar IP para rate limiting
- **Caso Crítico:** Inestabilidad eléctrica - hasta 3000 dispositivos reconectándose simultáneamente
- **Estrategia:** Rate limiting adaptativo que distingue entre reconexión legítima y ataques DDoS

---

## Estrategia de Protección DDoS

### Principios de Diseño

1. **Rate Limiting Adaptativo Multi-Capa:**
   - Capa 1: Device Fingerprint mejorado (identificación única del dispositivo)
   - Capa 2: UDID (identificación de sesión/autenticación)
   - Capa 3: Subscriber Code (identificación de usuario)
   - Capa 4: Comportamiento anómalo (detección de patrones)
   - **Capa 5: Rate Limiting Adaptativo** - Se ajusta según carga del sistema

2. **Manejo de Reconexión Masiva Legítima:**
   - **Reconexión con UDID válido existente:** Prioridad alta, límites más permisivos
   - **Nueva solicitud de UDID:** Límites más restrictivos
   - **Circuit Breaker:** Protección automática cuando el sistema está sobrecargado
   - **Exponential Backoff con Jitter:** Evita thundering herd al distribuir reconexiones

3. **Sin Dependencia de IP:**
   - Enfoque en identificadores únicos del dispositivo
   - Fingerprint robusto para móviles/Smart TVs
   - Validación de consistencia de identificadores

4. **Protección Específica por Plataforma:**
   - Android/iOS: Device ID, Advertising ID, Build ID
   - Smart TVs: Modelo, Serial Number, Firmware Version
   - WebSockets: Rate limiting por UDID y Device ID

### Estrategia para Reconexión Masiva

**Escenario:** 3000 dispositivos reconectándose después de corte de luz

**Problema:** Si todos intentan reconectarse al mismo tiempo, pueden saturar el sistema.

**Solución:**
1. **Reconocimiento de Reconexión Legítima:**
   - UDID válido y previamente usado → Prioridad ALTA
   - UDID nuevo o no validado → Prioridad NORMAL
   
2. **Rate Limiting Adaptativo:**
   - **Carga normal:** Límites estándar
   - **Carga alta (>500 requests/min):** Aumentar límites para reconexiones legítimas
   - **Carga crítica (>2000 requests/min):** Activar circuit breaker, usar cola

3. **Exponential Backoff con Jitter:**
   - Primera reconexión: inmediata
   - Si falla: retry con delay aleatorio (1-3 segundos)
   - Segundo fallo: retry con delay aleatorio (2-6 segundos)
   - Tercer fallo: retry con delay aleatorio (5-15 segundos)
   - Esto distribuye las reconexiones en el tiempo

4. **Circuit Breaker:**
   - Si el sistema detecta sobrecarga, activa circuit breaker
   - Requests de reconexión legítima: cola con prioridad
   - Requests nuevos: rechazados temporalmente con mensaje de retry

---

## Plan de Tareas Detallado

### FASE 1: INFRAESTRUCTURA CRÍTICA (Prioridad ALTA)

#### Tarea 1.1: Migrar Cache a Redis Distribuido
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Redis ya configurado para Channels

**Objetivo:**
Migrar el cache local a Redis para que el rate limiting funcione correctamente en múltiples instancias del servidor.

**Archivos a modificar:**
- `ubuntu/settings.py`

**Implementación:**
```python
# settings.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,  # Ya existe en settings
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'IGNORE_EXCEPTIONS': True,  # Continuar si Redis falla
        },
        'KEY_PREFIX': 'udid_cache',
        'TIMEOUT': 300,
    }
}
```

**Pruebas:**
- Verificar que el cache funciona entre múltiples instancias
- Probar rate limiting desde diferentes workers
- Validar fallback cuando Redis no está disponible

**Criterios de aceptación:**
- [ ] Cache distribuido funciona correctamente
- [ ] Rate limiting funciona entre instancias
- [ ] Fallback implementado si Redis falla

---

#### Tarea 1.2: Mejorar Device Fingerprint para Móviles y Smart TVs
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Tarea 1.1

**Objetivo:**
Crear un fingerprint más robusto y difícil de falsificar específico para aplicaciones móviles y Smart TVs.

**Archivos a modificar:**
- `udid/util.py` (función `generate_device_fingerprint`)
- `udid/models.py` (si es necesario agregar campos)

**Implementación:**
```python
def generate_device_fingerprint(request):
    """
    Genera fingerprint mejorado para móviles y Smart TVs.
    Incluye factores que son más difíciles de falsificar.
    """
    # Factores básicos
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    accept_language = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
    accept_encoding = request.META.get('HTTP_ACCEPT_ENCODING', '')
    
    # Factores específicos de móviles/Smart TVs (desde headers personalizados)
    device_id = request.META.get('HTTP_X_DEVICE_ID', '')  # Device ID nativo
    app_version = request.META.get('HTTP_X_APP_VERSION', '')
    app_type = request.META.get('HTTP_X_APP_TYPE', '')
    os_version = request.META.get('HTTP_X_OS_VERSION', '')
    device_model = request.META.get('HTTP_X_DEVICE_MODEL', '')
    build_id = request.META.get('HTTP_X_BUILD_ID', '')  # Build fingerprint
    
    # Para Smart TVs: Serial Number, Model Name
    tv_serial = request.META.get('HTTP_X_TV_SERIAL', '')
    tv_model = request.META.get('HTTP_X_TV_MODEL', '')
    firmware_version = request.META.get('HTTP_X_FIRMWARE_VERSION', '')
    
    # Combinar factores según el tipo de app
    if app_type in ['android_tv', 'samsung_tv', 'lg_tv', 'set_top_box']:
        # Smart TV: usar serial, model, firmware
        fingerprint_string = f"{app_type}|{tv_serial}|{tv_model}|{firmware_version}|{device_id}|{app_version}"
    elif app_type in ['android_mobile', 'ios_mobile']:
        # Móvil: usar device_id, build_id, model, os_version
        fingerprint_string = f"{app_type}|{device_id}|{build_id}|{device_model}|{os_version}|{app_version}"
    else:
        # Fallback: usar headers básicos
        fingerprint_string = f"{user_agent}|{accept_language}|{accept_encoding}|{app_type}|{app_version}"
    
    # Generar hash SHA256
    device_fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]
    
    return device_fingerprint
```

**Actualizar headers permitidos en CORS:**
```python
# settings.py
CORS_ALLOW_HEADERS = [
    # ... headers existentes
    'x-device-id',      # Ya existe
    'x-app-version',    # Ya existe
    'x-app-type',       # Ya existe
    'x-os-version',     # Nuevo
    'x-device-model',   # Nuevo
    'x-build-id',       # Nuevo
    'x-tv-serial',      # Nuevo (Smart TVs)
    'x-tv-model',       # Nuevo (Smart TVs)
    'x-firmware-version', # Nuevo (Smart TVs)
]
```

**Pruebas:**
- Probar con diferentes tipos de dispositivos
- Verificar que el fingerprint es consistente para el mismo dispositivo
- Validar que diferentes dispositivos generan fingerprints diferentes

**Criterios de aceptación:**
- [ ] Fingerprint mejorado implementado
- [ ] Headers CORS actualizados
- [ ] Funciona para Android, iOS y Smart TVs
- [ ] Documentación para clientes móviles

---

#### Tarea 1.3: Proteger WebSockets con Rate Limiting por UDID
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.1

**Objetivo:**
Implementar rate limiting en conexiones WebSocket basado en UDID y Device Fingerprint.

**Archivos a modificar:**
- `udid/consumers.py`
- `udid/util.py` (nueva función para WebSocket rate limiting)

**Implementación:**

1. Agregar función de rate limiting para WebSockets:
```python
# udid/util.py
def check_websocket_rate_limit(udid, device_fingerprint, max_connections=5, window_minutes=5):
    """
    Verifica rate limit para conexiones WebSocket.
    Limita conexiones simultáneas por UDID y device fingerprint.
    """
    from .models import UDIDAuthRequest
    
    # Limitar por UDID (si existe)
    if udid:
        cache_key_udid = f"ws_rate_limit:udid:{udid}"
        current_connections = cache.get(cache_key_udid, 0)
        
        if current_connections >= max_connections:
            return False, 0, window_minutes * 60
        
        # Incrementar contador
        cache.set(cache_key_udid, current_connections + 1, timeout=window_minutes * 60)
    
    # Limitar por device fingerprint
    if device_fingerprint:
        cache_key_fp = f"ws_rate_limit:fp:{device_fingerprint}"
        current_connections_fp = cache.get(cache_key_fp, 0)
        
        if current_connections_fp >= max_connections:
            return False, 0, window_minutes * 60
        
        cache.set(cache_key_fp, current_connections_fp + 1, timeout=window_minutes * 60)
    
    return True, max_connections - current_connections, 0
```

2. Modificar consumer para usar rate limiting:
```python
# udid/consumers.py
async def connect(self):
    # Obtener device fingerprint del scope
    device_fingerprint = self._get_device_fingerprint_from_scope()
    
    # Rate limiting antes de aceptar conexión
    udid = None  # Se obtendrá después del primer mensaje
    is_allowed, remaining, retry_after = await sync_to_async(
        check_websocket_rate_limit
    )(udid, device_fingerprint, max_connections=5, window_minutes=5)
    
    if not is_allowed:
        await self.close(code=4001, reason="Too many connections")
        return
    
    self.udid = None
    self.device_fingerprint = device_fingerprint
    self.app_type = None
    self.app_version = None
    # ... resto del código
    await self.accept()
```

3. Reducir timeout y mejorar limpieza:
```python
# settings.py
UDID_WAIT_TIMEOUT = int(os.getenv("UDID_WAIT_TIMEOUT", "60"))  # Reducido de 600 a 60 segundos
```

**Pruebas:**
- Probar múltiples conexiones desde el mismo dispositivo
- Verificar que se rechazan conexiones excesivas
- Validar limpieza de contadores al cerrar conexiones

**Criterios de aceptación:**
- [ ] Rate limiting implementado en WebSockets
- [ ] Timeout reducido a 60 segundos
- [ ] Limpieza de contadores al desconectar
- [ ] Pruebas de carga completadas

---

#### Tarea 1.4: Implementar Rate Limiting Adaptativo y Circuit Breaker
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 6-8 horas  
**Dependencias:** Tarea 1.1

**Objetivo:**
Implementar sistema de rate limiting adaptativo que se ajuste según la carga del sistema y circuit breaker para proteger durante reconexiones masivas.

**Archivos a modificar:**
- `udid/util.py` (nuevas funciones)
- `udid/middleware.py` (nuevo archivo para middleware)

**Implementación:**

1. Sistema de monitoreo de carga:
```python
# udid/util.py
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import time

def get_system_load():
    """
    Calcula la carga actual del sistema basado en requests recientes.
    Retorna: 'normal', 'high', 'critical'
    """
    cache_key = 'system_load:requests_per_minute'
    
    # Contar requests en último minuto
    current_time = time.time()
    minute_ago = current_time - 60
    
    # Usar Redis sorted set para contar requests por segundo
    # O usar contador simple en Redis
    requests_count = cache.get('system_load:counter', 0)
    
    # Resetear contador cada minuto
    if not cache.get('system_load:last_reset'):
        cache.set('system_load:last_reset', current_time, timeout=60)
        cache.set('system_load:counter', 0, timeout=60)
    
    if requests_count < 500:
        return 'normal'
    elif requests_count < 2000:
        return 'high'
    else:
        return 'critical'

def check_circuit_breaker():
    """
    Verifica si el circuit breaker está activo.
    """
    breaker_state = cache.get('circuit_breaker:state', 'closed')
    breaker_until = cache.get('circuit_breaker:until', 0)
    
    if breaker_state == 'open':
        if time.time() < breaker_until:
            return True, breaker_until - time.time()
        else:
            # Intentar cerrar (half-open)
            cache.set('circuit_breaker:state', 'half-open', timeout=60)
            cache.delete('circuit_breaker:until')
            return False, 0
    
    return False, 0

def activate_circuit_breaker(duration_seconds=60):
    """
    Activa el circuit breaker por un tiempo determinado.
    """
    cache.set('circuit_breaker:state', 'open', timeout=duration_seconds)
    cache.set('circuit_breaker:until', time.time() + duration_seconds, timeout=duration_seconds)
```

2. Rate limiting adaptativo:
```python
def check_adaptive_rate_limit(identifier_type, identifier, is_reconnection=False):
    """
    Rate limiting adaptativo que ajusta límites según carga del sistema
    y si es una reconexión legítima.
    
    Args:
        identifier_type: 'udid', 'device_fp', etc.
        identifier: El valor del identificador
        is_reconnection: True si es reconexión de UDID válido existente
    """
    # Verificar circuit breaker
    breaker_active, retry_after = check_circuit_breaker()
    if breaker_active and not is_reconnection:
        # En circuit breaker, solo permitir reconexiones
        return False, 0, retry_after, "Circuit breaker active"
    
    # Obtener carga del sistema
    system_load = get_system_load()
    
    # Determinar límites según carga y tipo de request
    if is_reconnection:
        # Reconexiones legítimas: límites más permisivos
        if system_load == 'normal':
            max_requests = 20
            window_minutes = 5
        elif system_load == 'high':
            max_requests = 50  # Más permisivo durante alta carga
            window_minutes = 5
        else:  # critical
            max_requests = 100  # Muy permisivo, pero con circuit breaker
            window_minutes = 10
    else:
        # Nuevas solicitudes: límites más restrictivos
        if system_load == 'normal':
            max_requests = 3
            window_minutes = 5
        elif system_load == 'high':
            max_requests = 2  # Más restrictivo durante alta carga
            window_minutes = 10
        else:  # critical
            max_requests = 1  # Muy restrictivo
            window_minutes = 15
    
    # Verificar límite estándar
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    current_count = cache.get(cache_key, 0)
    
    if current_count >= max_requests:
        # Si es reconexión y carga crítica, activar circuit breaker
        if is_reconnection and system_load == 'critical':
            activate_circuit_breaker(duration_seconds=30)
        
        return False, 0, window_minutes * 60, "Rate limit exceeded"
    
    return True, max_requests - current_count, 0, "OK"
```

3. Detección de reconexión legítima:
```python
def is_legitimate_reconnection(udid):
    """
    Determina si un request es una reconexión legítima.
    UDID válido y previamente usado = reconexión legítima.
    """
    from .models import UDIDAuthRequest
    
    try:
        req = UDIDAuthRequest.objects.get(udid=udid)
        # Es reconexión si:
        # - UDID existe
        # - Está validado o usado previamente
        # - No ha expirado (o expiró recientemente, < 1 hora)
        if req.status in ['validated', 'used']:
            # Verificar si expiró recientemente (reconexión después de corte)
            if req.is_expired():
                # Si expiró hace menos de 1 hora, considerar reconexión legítima
                time_since_expiry = timezone.now() - req.expires_at
                if time_since_expiry.total_seconds() < 3600:  # 1 hora
                    return True
            else:
                return True
    except UDIDAuthRequest.DoesNotExist:
        pass
    
    return False
```

**Pruebas:**
- Simular reconexión masiva (3000 dispositivos)
- Verificar que rate limiting se adapta
- Probar activación de circuit breaker
- Validar que reconexiones legítimas tienen prioridad

**Criterios de aceptación:**
- [ ] Rate limiting adaptativo implementado
- [ ] Circuit breaker funcional
- [ ] Reconexiones legítimas tienen prioridad
- [ ] Pruebas de carga completadas

---

#### Tarea 1.5: Implementar Exponential Backoff con Jitter para Reconexiones
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 4-5 horas  
**Dependencias:** Tarea 1.4

**Objetivo:**
Implementar exponential backoff con jitter aleatorio para distribuir reconexiones en el tiempo y evitar thundering herd.

**Archivos a modificar:**
- `udid/util.py`
- `udid/views.py` (AuthenticateWithUDIDView)
- `udid/consumers.py` (WebSocket)

**Implementación:**

1. Función de exponential backoff con jitter:
```python
# udid/util.py
import random
import math

def calculate_retry_delay(attempt_number, base_delay=1, max_delay=60, jitter=True):
    """
    Calcula delay para retry con exponential backoff y jitter.
    
    Args:
        attempt_number: Número de intento (1, 2, 3, ...)
        base_delay: Delay base en segundos
        max_delay: Delay máximo en segundos
        jitter: Si True, agrega aleatoriedad para evitar sincronización
    
    Returns:
        delay en segundos
    """
    # Exponential backoff: base_delay * 2^(attempt_number - 1)
    exponential_delay = base_delay * (2 ** (attempt_number - 1))
    
    # Capar al máximo
    delay = min(exponential_delay, max_delay)
    
    # Agregar jitter aleatorio (±30% del delay)
    if jitter:
        jitter_amount = delay * 0.3
        delay = delay + random.uniform(-jitter_amount, jitter_amount)
        delay = max(0.5, delay)  # Mínimo 0.5 segundos
    
    return math.ceil(delay)

def get_retry_info(udid, action_type='reconnection'):
    """
    Obtiene información de retry para un UDID.
    Si es primera vez, retorna delay 0 (inmediato).
    Si ya hubo intentos, retorna delay calculado.
    """
    cache_key = f"retry_info:{action_type}:{udid}"
    retry_data = cache.get(cache_key, {'attempts': 0, 'last_attempt': 0})
    
    attempts = retry_data['attempts']
    last_attempt = retry_data['last_attempt']
    current_time = time.time()
    
    # Si pasó más de 5 minutos desde último intento, resetear
    if current_time - last_attempt > 300:
        attempts = 0
    
    if attempts == 0:
        # Primera vez: intento inmediato
        delay = 0
    else:
        # Calcular delay con exponential backoff
        delay = calculate_retry_delay(attempts, base_delay=1, max_delay=30)
    
    # Incrementar contador
    retry_data['attempts'] = attempts + 1
    retry_data['last_attempt'] = current_time
    cache.set(cache_key, retry_data, timeout=600)  # 10 minutos
    
    return delay, attempts + 1

def reset_retry_info(udid, action_type='reconnection'):
    """
    Resetea información de retry (cuando reconexión es exitosa).
    """
    cache_key = f"retry_info:{action_type}:{udid}"
    cache.delete(cache_key)
```

2. Integrar en AuthenticateWithUDIDView:
```python
# udid/views.py
class AuthenticateWithUDIDView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        udid = request.data.get('udid')
        # ... validaciones ...
        
        # Verificar si es reconexión legítima
        is_reconnection = is_legitimate_reconnection(udid)
        
        # Obtener información de retry
        retry_delay, attempt_number = get_retry_info(udid, 'reconnection')
        
        # Si hay delay, retornar con información de retry
        if retry_delay > 0:
            return Response({
                "error": "Service temporarily unavailable",
                "message": "Please retry after a short delay",
                "retry_after": retry_delay,
                "attempt": attempt_number,
                "is_reconnection": is_reconnection
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={
                "Retry-After": str(retry_delay),
                "X-Retry-After": str(retry_delay)
            })
        
        # Verificar rate limiting adaptativo
        is_allowed, remaining, retry_after, reason = check_adaptive_rate_limit(
            'udid', udid, is_reconnection=is_reconnection
        )
        
        if not is_allowed:
            # Si es reconexión y fue rechazada, calcular delay
            if is_reconnection:
                retry_delay, _ = get_retry_info(udid, 'reconnection')
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "System is handling high reconnection volume. Please retry.",
                    "retry_after": retry_delay,
                    "is_reconnection": True
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_delay)
                })
            else:
                return Response({
                    "error": "Rate limit exceeded",
                    "retry_after": retry_after
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        try:
            # ... procesar autenticación ...
            
            # Si es exitoso, resetear retry info
            reset_retry_info(udid, 'reconnection')
            
            return Response({...})
        except Exception as e:
            # En caso de error, incrementar retry info
            get_retry_info(udid, 'reconnection')  # Esto incrementa el contador
            return Response({...})
```

**Pruebas:**
- Simular 3000 reconexiones simultáneas
- Verificar que se distribuyen en el tiempo
- Probar que jitter funciona correctamente
- Validar que reconexiones exitosas resetean contador

**Criterios de aceptación:**
- [ ] Exponential backoff con jitter implementado
- [ ] Reconexiones se distribuyen en el tiempo
- [ ] Thundering herd evitado
- [ ] Pruebas de carga completadas

---

### FASE 2: RATE LIMITING EN ENDPOINTS (Prioridad ALTA)

#### Tarea 2.1: Rate Limiting en Endpoints de Autenticación
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.1, Tarea 1.2

**Objetivo:**
Implementar rate limiting robusto en endpoints de login y registro.

**Archivos a modificar:**
- `udid/auth.py`
- `udid/util.py` (nueva función para rate limiting por subscriber)

**Endpoints a proteger:**
1. `/udid/auth/login/` - Rate limiting por username + device fingerprint
2. `/udid/auth/register/` - Rate limiting por device fingerprint

**Implementación:**

```python
# udid/util.py
def check_login_rate_limit(username, device_fingerprint, max_attempts=5, window_minutes=15):
    """
    Rate limiting para login: combina username + device fingerprint
    Protege contra fuerza bruta sin afectar usuarios legítimos.
    """
    cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
    attempts = cache.get(cache_key, 0)
    
    if attempts >= max_attempts:
        retry_after = window_minutes * 60
        return False, 0, retry_after
    
    return True, max_attempts - attempts, 0

def check_register_rate_limit(device_fingerprint, max_requests=3, window_minutes=60):
    """
    Rate limiting para registro: por device fingerprint
    Previene creación masiva de cuentas.
    """
    cache_key = f"register_rate_limit:{device_fingerprint}"
    requests = cache.get(cache_key, 0)
    
    if requests >= max_requests:
        retry_after = window_minutes * 60
        return False, 0, retry_after
    
    return True, max_requests - requests, 0
```

```python
# udid/auth.py
class LoginView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        
        if not all([username, password]):
            return Response({"error": "username y password son requeridos"}, status=400)
        
        # Rate limiting por username + device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        is_allowed, remaining, retry_after = check_login_rate_limit(
            username, device_fingerprint, max_attempts=5, window_minutes=15
        )
        
        if not is_allowed:
            return Response({
                "error": "Too many login attempts",
                "message": "Please try again later",
                "retry_after": retry_after
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        user = authenticate(username=username, password=password)
        
        if user is None:
            # Incrementar contador en cache
            cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
            cache.incr(cache_key)
            cache.expire(cache_key, 15 * 60)
            
            return Response({"error": "Credenciales inválidas"}, status=status.HTTP_401_UNAUTHORIZED)
        
        # Login exitoso: resetear contador
        cache_key = f"login_rate_limit:{username}:{device_fingerprint}"
        cache.delete(cache_key)
        
        # ... resto del código de login
```

**Pruebas:**
- Probar múltiples intentos de login fallidos
- Verificar que se bloquea después del límite
- Validar que usuarios legítimos no se ven afectados

**Criterios de aceptación:**
- [ ] Rate limiting en login implementado
- [ ] Rate limiting en registro implementado
- [ ] Contadores se resetean en login exitoso
- [ ] Pruebas de fuerza bruta completadas

---

#### Tarea 2.2: Rate Limiting en Endpoints de UDID
**Prioridad:** 🟡 ALTA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 1.1, Tarea 1.2

**Objetivo:**
Ajustar y reforzar rate limiting en endpoints de gestión de UDID.

**Archivos a modificar:**
- `udid/automatico.py`
- `udid/views.py`
- `udid/util.py` (ajustar límites)

**Endpoints a revisar:**
1. `/udid/request-udid/` - Ya tiene rate limiting, ajustar límites
2. `/udid/validate-udid/` - Ya tiene rate limiting, revisar
3. `/udid/revoke-udid/` - Agregar rate limiting
4. `/udid/udid-requests/` - Agregar rate limiting

**Ajustes de límites:**
```python
# Límites más restrictivos para endpoints críticos
# udid/automatico.py
check_device_fingerprint_rate_limit(
    device_fingerprint,
    max_requests=2,  # Reducido de 3 a 2
    window_minutes=10  # Aumentado de 5 a 10 minutos
)

# udid/views.py
check_udid_rate_limit(
    udid,
    max_requests=5,  # Reducido de 10 a 5 para operaciones críticas
    window_minutes=60
)
```

**Implementar rate limiting en endpoints sin protección:**
```python
# udid/automatico.py
class RevokeUDIDView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        udid = request.data.get('udid')
        
        # Rate limiting por UDID
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid, max_requests=3, window_minutes=60
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "retry_after": retry_after
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # ... resto del código
```

**Pruebas:**
- Verificar límites en todos los endpoints
- Probar comportamiento con límites excedidos
- Validar que usuarios legítimos no se ven afectados

**Criterios de aceptación:**
- [ ] Límites ajustados en todos los endpoints
- [ ] Rate limiting agregado en endpoints faltantes
- [ ] Pruebas de carga completadas

---

### FASE 3: OPTIMIZACIÓN Y MONITOREO (Prioridad MEDIA)

#### Tarea 3.1: Optimizar Consultas de Rate Limiting
**Prioridad:** 🟡 MEDIA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 1.1

**Objetivo:**
Asegurar que las consultas a BD sean eficientes y no se ejecuten cuando el cache está disponible.

**Archivos a modificar:**
- `udid/util.py`
- `udid/models.py` (agregar índices)

**Implementación:**

1. Agregar índices en modelos:
```python
# udid/models.py
class UDIDAuthRequest(models.Model):
    # ... campos existentes
    
    class Meta:
        indexes = [
            # ... índices existentes
            models.Index(fields=['device_fingerprint', 'created_at']),
            models.Index(fields=['udid', 'created_at']),
            models.Index(fields=['temp_token', 'created_at']),
        ]
```

2. Mejorar funciones de rate limiting para evitar consultas BD:
```python
# udid/util.py
def check_udid_rate_limit(udid, max_requests=20, window_minutes=60):
    """
    Versión optimizada: siempre intenta cache primero.
    Solo consulta BD si es absolutamente necesario.
    """
    if not udid:
        return False, 0, 0
    
    cache_key = f"rate_limit:udid:{udid}"
    cached_count = cache.get(cache_key)
    
    if cached_count is not None:
        remaining = max(0, max_requests - cached_count)
        if cached_count >= max_requests:
            retry_after = window_minutes * 60
            return False, remaining, retry_after
        return True, remaining, 0
    
    # Si no está en cache, inicializar con 0
    # Esto evita consulta a BD en primera llamada
    cache.set(cache_key, 0, timeout=window_minutes * 60)
    return True, max_requests, 0

def increment_rate_limit_counter(identifier_type, identifier):
    """
    Versión mejorada: siempre incrementa en cache.
    """
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    try:
        cache.incr(cache_key)
    except ValueError:
        # Si no existe, inicializar
        cache.set(cache_key, 1, timeout=3600)
```

**Pruebas:**
- Verificar que no hay consultas BD innecesarias
- Probar rendimiento con alto volumen
- Validar que los índices mejoran el rendimiento

**Criterios de aceptación:**
- [ ] Índices agregados en BD
- [ ] Consultas optimizadas
- [ ] Pruebas de rendimiento completadas

---

#### Tarea 3.2: Implementar Exponential Backoff
**Prioridad:** 🟡 MEDIA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.1, Tarea 2.1

**Objetivo:**
Implementar escalado progresivo de rate limiting para detectar y bloquear ataques más agresivos.

**Archivos a modificar:**
- `udid/util.py`

**Implementación:**
```python
def check_rate_limit_with_backoff(identifier_type, identifier, base_max_requests=10, 
                                  window_minutes=5, max_backoff_hours=24):
    """
    Rate limiting con exponential backoff.
    Si se excede el límite múltiples veces, aumenta el tiempo de bloqueo.
    """
    cache_key = f"rate_limit:{identifier_type}:{identifier}"
    backoff_key = f"rate_limit_backoff:{identifier_type}:{identifier}"
    
    # Verificar si está en backoff
    backoff_until = cache.get(backoff_key)
    if backoff_until:
        if timezone.now().timestamp() < backoff_until:
            remaining_seconds = int(backoff_until - timezone.now().timestamp())
            return False, 0, remaining_seconds
    
    # Verificar rate limit normal
    current_count = cache.get(cache_key, 0)
    if current_count >= base_max_requests:
        # Incrementar backoff
        violation_count_key = f"rate_limit_violations:{identifier_type}:{identifier}"
        violations = cache.get(violation_count_key, 0)
        violations += 1
        
        # Exponential backoff: 5min, 15min, 1h, 4h, 24h
        backoff_minutes = min(5 * (3 ** min(violations - 1, 3)), max_backoff_hours * 60)
        backoff_until = timezone.now().timestamp() + (backoff_minutes * 60)
        
        cache.set(backoff_key, backoff_until, timeout=max_backoff_hours * 3600)
        cache.set(violation_count_key, violations, timeout=max_backoff_hours * 3600)
        
        return False, 0, backoff_minutes * 60
    
    return True, base_max_requests - current_count, 0
```

**Pruebas:**
- Probar escalado progresivo
- Verificar que usuarios legítimos no se ven afectados
- Validar reset de backoff después del período

**Criterios de aceptación:**
- [ ] Exponential backoff implementado
- [ ] Pruebas de escalado completadas
- [ ] Documentación actualizada

---

#### Tarea 3.3: Agregar Logging y Monitoreo de Rate Limiting
**Prioridad:** 🟢 BAJA  
**Tiempo estimado:** 2-3 horas  
**Dependencias:** Tarea 1.1

**Objetivo:**
Implementar logging detallado para monitorear intentos de rate limiting y detectar ataques.

**Archivos a modificar:**
- `udid/util.py`
- `udid/models.py` (agregar modelo de RateLimitLog si es necesario)

**Implementación:**
```python
# udid/util.py
import logging

logger = logging.getLogger('rate_limiting')

def check_udid_rate_limit(udid, max_requests=20, window_minutes=60):
    # ... código existente ...
    
    if not is_allowed:
        # Log de rate limit excedido
        logger.warning(
            f"Rate limit exceeded: udid={udid}, "
            f"count={total_count}, limit={max_requests}, "
            f"window={window_minutes}min"
        )
        
        # Opcional: crear log en BD para análisis
        RateLimitLog.objects.create(
            identifier_type='udid',
            identifier=udid,
            action='rate_limit_exceeded',
            count=total_count,
            limit=max_requests
        )
    
    return is_allowed, remaining, retry_after
```

**Pruebas:**
- Verificar que los logs se generan correctamente
- Probar consultas de análisis de logs
- Validar que no afecta el rendimiento

**Criterios de aceptación:**
- [ ] Logging implementado
- [ ] Logs estructurados para análisis
- [ ] Dashboard de monitoreo (opcional)

---

### FASE 4: VALIDACIÓN Y PRUEBAS (Prioridad ALTA)

#### Tarea 4.1: Pruebas de Carga y Estrés
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 4-6 horas  
**Dependencias:** Todas las tareas anteriores

**Objetivo:**
Validar que el sistema resiste ataques DDoS simulados.

**Herramientas:**
- Locust o JMeter para pruebas de carga
- Scripts personalizados para simular ataques

**Escenarios de prueba:**
1. **Ataque de Volumen:**
   - 1000 requests/segundo desde múltiples dispositivos
   - Verificar que rate limiting funciona correctamente

2. **Ataque de Fuerza Bruta:**
   - Múltiples intentos de login fallidos
   - Verificar bloqueo progresivo

3. **Ataque de WebSocket:**
   - 100 conexiones simultáneas desde el mismo dispositivo
   - Verificar límite de conexiones

4. **Ataque Distribuido:**
   - Requests desde múltiples device fingerprints
   - Verificar que el sistema no se satura

**Criterios de aceptación:**
- [ ] Pruebas de carga completadas
- [ ] Sistema resiste ataques simulados
- [ ] Documentación de resultados

---

#### Tarea 4.2: Pruebas de Integración con Aplicaciones Móviles
**Prioridad:** 🔴 CRÍTICA  
**Tiempo estimado:** 3-4 horas  
**Dependencias:** Tarea 1.2, Tarea 2.1

**Objetivo:**
Validar que las aplicaciones móviles y Smart TVs funcionan correctamente con las nuevas protecciones.

**Dispositivos a probar:**
- Android Mobile (varias versiones)
- iOS Mobile (varias versiones)
- Android TV
- Samsung TV
- LG TV

**Escenarios:**
1. Login normal
2. Múltiples intentos de login fallidos
3. Solicitud de UDID
4. Conexión WebSocket
5. Operaciones normales del usuario

**Criterios de aceptación:**
- [ ] Pruebas en todos los tipos de dispositivos
- [ ] Usuarios legítimos no se ven afectados
- [ ] Documentación de problemas encontrados

---

## Resumen de Prioridades

### 🔴 CRÍTICAS (Implementar primero)
1. **Tarea 1.1:** Migrar cache a Redis
2. **Tarea 1.2:** Mejorar device fingerprint
3. **Tarea 1.3:** Proteger WebSockets
4. **Tarea 1.4:** Rate limiting adaptativo y circuit breaker ⭐ **NUEVO - CRÍTICO para reconexión masiva**
5. **Tarea 1.5:** Exponential backoff con jitter ⭐ **NUEVO - CRÍTICO para reconexión masiva**
6. **Tarea 2.1:** Rate limiting en autenticación
7. **Tarea 4.1:** Pruebas de carga (especialmente reconexión masiva)
8. **Tarea 4.2:** Pruebas con aplicaciones móviles

### 🟡 IMPORTANTES (Implementar después)
7. **Tarea 2.2:** Rate limiting en endpoints UDID
8. **Tarea 3.1:** Optimizar consultas
9. **Tarea 3.2:** Exponential backoff

### 🟢 MEJORAS (Implementar cuando sea posible)
10. **Tarea 3.3:** Logging y monitoreo

---

## Estimación Total de Tiempo

- **Fase 1 (Infraestructura):** 15-20 horas (incluye tareas 1.4 y 1.5 para reconexión masiva)
- **Fase 2 (Rate Limiting):** 5-7 horas
- **Fase 3 (Optimización):** 7-10 horas
- **Fase 4 (Pruebas):** 8-12 horas (más tiempo para pruebas de reconexión masiva)

**Total estimado:** 35-49 horas de desarrollo

**Nota:** Las tareas 1.4 y 1.5 son críticas para manejar el caso de reconexión masiva (3000 dispositivos) y deben implementarse antes de poner en producción.

---

## Notas Importantes

1. **Caso Crítico: Reconexión Masiva (3000 dispositivos):**
   - ⚠️ **CRÍTICO:** Implementar Tareas 1.4 y 1.5 antes de producción
   - El sistema debe distinguir entre reconexión legítima y ataque
   - Exponential backoff con jitter es esencial para evitar thundering herd
   - Circuit breaker protege el sistema durante picos de carga

2. **Sin Rate Limiting por IP:**
   - Todas las protecciones se basan en UDID y Device Fingerprint
   - El device fingerprint mejorado es crítico para la efectividad
   - Rate limiting adaptativo ajusta según carga del sistema

3. **Compatibilidad con Móviles:**
   - Las aplicaciones móviles deben enviar los headers necesarios
   - Documentar qué headers son requeridos para cada plataforma
   - Las apps deben implementar retry con exponential backoff en el cliente

4. **Redis es Crítico:**
   - Sin Redis, el rate limiting no funcionará en múltiples instancias
   - Implementar fallback robusto si Redis falla
   - Redis también se usa para monitoreo de carga del sistema

5. **Testing Continuo:**
   - **Especialmente importante:** Probar escenario de reconexión masiva (3000 dispositivos)
   - Probar con usuarios reales antes de desplegar
   - Monitorear logs después del despliegue
   - Ajustar límites según el comportamiento real
   - Monitorear activación de circuit breaker durante eventos reales

6. **Recomendaciones para Apps Cliente:**
   - Implementar exponential backoff con jitter en el cliente
   - Detectar respuesta 503/429 y respetar header `Retry-After`
   - Mostrar mensaje al usuario: "Reconectando, por favor espere..."
   - No bombardear el servidor con retries inmediatos

---

## Próximos Pasos

1. Revisar y aprobar este plan
2. Priorizar tareas según necesidades del negocio
3. Asignar recursos y tiempos
4. Comenzar con Fase 1 (Tareas críticas)
5. Revisar progreso semanalmente

