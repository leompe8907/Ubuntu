# Respuestas Detalladas sobre Device Fingerprint y Sistema

**Fecha:** 2025-01-27

---

## 1. ¿Quién genera el Fingerprint y dónde se almacena?

### Generación del Fingerprint

**El SERVIDOR genera el fingerprint**, NO el dispositivo.

#### **Proceso de Generación:**

1. **El dispositivo envía headers HTTP** con información del dispositivo:
   - `X-Device-ID`, `X-TV-Serial`, `X-Device-Model`, etc.
   - Estos headers son enviados por la aplicación cliente en cada request

2. **El servidor extrae los headers** del request:
   ```python
   # udid/util.py:150-170
   headers_dict = {
       'user_agent': request.META.get('HTTP_USER_AGENT'),
       'device_id': request.META.get('HTTP_X_DEVICE_ID'),
       'tv_serial': request.META.get('HTTP_X_TV_SERIAL'),
       # ... más headers
   }
   ```

3. **El servidor genera el hash SHA256:**
   ```python
   # udid/util.py:173-176
   fingerprint_string = _build_device_fingerprint_string(headers_dict)
   device_fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]
   ```

**Ubicación del código:** `udid/util.py:136-178`

### Almacenamiento del Fingerprint

El fingerprint se almacena en **DOS lugares**:

#### **A) Base de Datos (Persistente):**
- **Tabla:** `UDIDAuthRequest`
- **Campo:** `device_fingerprint` (CharField, max_length=255)
- **Cuándo se guarda:** Cuando se crea un nuevo UDID
- **Ubicación:** `udid/models.py:359`

```python
# udid/views.py:119-125
auth_request = UDIDAuthRequest.objects.create(
    udid=udid,
    status='pending',
    client_ip=client_ip,
    user_agent=request.META.get('HTTP_USER_AGENT', ''),
    device_fingerprint=device_fingerprint  # ✅ Se guarda aquí
)
```

#### **B) Redis/Cache (Temporal):**
- **Clave:** `rate_limit:device_fp:{device_fingerprint}`
- **Propósito:** Rate limiting rápido
- **TTL:** 10 minutos (ventana de rate limiting)
- **Ubicación:** `udid/util.py:199-219`

```python
# Se usa para rate limiting sin consultar BD
cache_key = f"rate_limit:device_fp:{device_fingerprint}"
cached_count = cache.get(cache_key)
```

**Resumen:**
- ✅ **Generado por:** Servidor (Django)
- ✅ **Almacenado en:** Base de datos (UDIDAuthRequest) + Redis (cache)
- ✅ **Persistencia:** BD = permanente, Redis = temporal (10 min)

---

## 2. ¿El Fingerprint es por dispositivo?

### Sí, el Fingerprint es ÚNICO por dispositivo

#### **Características:**

1. **Mismo dispositivo = Mismo fingerprint:**
   - Si un dispositivo envía los mismos headers, siempre genera el mismo fingerprint
   - El hash SHA256 es determinístico (misma entrada = misma salida)

2. **Diferentes dispositivos = Diferentes fingerprints:**
   - Cada dispositivo tiene características únicas (serial, device ID, etc.)
   - Genera un fingerprint diferente

3. **Ejemplo:**
   ```python
   # Dispositivo A (Android TV, Serial: ABC123)
   fingerprint_A = SHA256("android_tv|ABC123|ModelX|Firmware1.0|...")[:32]
   # Resultado: "a1b2c3d4e5f6..."
   
   # Dispositivo B (Android TV, Serial: XYZ789)
   fingerprint_B = SHA256("android_tv|XYZ789|ModelX|Firmware1.0|...")[:32]
   # Resultado: "f6e5d4c3b2a1..." (diferente)
   
   # Mismo Dispositivo A (mismos headers)
   fingerprint_A2 = SHA256("android_tv|ABC123|ModelX|Firmware1.0|...")[:32]
   # Resultado: "a1b2c3d4e5f6..." (igual que fingerprint_A)
   ```

#### **Limitaciones:**

⚠️ **El fingerprint puede cambiar si:**
- El dispositivo actualiza su firmware (cambia `X-Firmware-Version`)
- La aplicación se actualiza (cambia `X-App-Version`)
- El dispositivo se restablece de fábrica (cambia `X-Device-ID` en algunos casos)

✅ **El fingerprint es estable si:**
- Los headers enviados no cambian
- El dispositivo no se actualiza
- La aplicación no se actualiza

**Conclusión:** El fingerprint identifica al dispositivo de forma única, pero puede cambiar si las características del dispositivo cambian.

---

## 3. ¿Cómo recibe el proyecto el Fingerprint o cómo le llega al dispositivo?

### El Dispositivo NO envía el Fingerprint

**Aclaración importante:** El dispositivo **NO envía el fingerprint directamente**. En su lugar:

#### **Proceso Real:**

1. **El dispositivo envía headers HTTP** con información del dispositivo:
   ```http
   GET /udid/request-udid/ HTTP/1.1
   Host: api.example.com
   X-Device-ID: android_abc123def456
   X-App-Type: android_tv
   X-App-Version: 1.0.0
   X-TV-Serial: SN123456789
   X-TV-Model: Samsung QLED 2023
   X-Firmware-Version: 1.2.3
   User-Agent: MyApp/1.0.0
   ```

2. **El servidor recibe estos headers** en el request:
   ```python
   # udid/util.py:150-170
   device_id = request.META.get('HTTP_X_DEVICE_ID')  # "android_abc123def456"
   tv_serial = request.META.get('HTTP_X_TV_SERIAL')  # "SN123456789"
   # ... más headers
   ```

3. **El servidor genera el fingerprint** a partir de estos headers:
   ```python
   # udid/util.py:173-176
   fingerprint_string = f"{app_type}|{tv_serial}|{tv_model}|..."
   device_fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]
   ```

4. **El servidor usa el fingerprint** para:
   - Rate limiting
   - Identificación del dispositivo
   - Almacenamiento en BD

#### **Flujo Completo:**

```
Dispositivo → Envía Headers HTTP → Servidor → Genera Fingerprint → Usa para Rate Limiting
```

**Ejemplo de código en el dispositivo (Android):**
```kotlin
// El dispositivo NO genera el fingerprint, solo envía headers
val headers = mapOf(
    "X-Device-ID" to Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID),
    "X-App-Type" to "android_tv",
    "X-TV-Serial" to Build.SERIAL,
    "X-TV-Model" to Build.MODEL,
    // ... más headers
)

// Hacer request HTTP con estos headers
httpClient.get("/udid/request-udid/", headers = headers)
```

**Resumen:**
- ❌ **El dispositivo NO envía el fingerprint**
- ✅ **El dispositivo envía headers con información del dispositivo**
- ✅ **El servidor genera el fingerprint a partir de esos headers**

---

## 4. ¿Tienes alguna sugerencia de alternativa o con el Fingerprint está bien?

### Análisis del Sistema Actual

#### **Fortalezas del Sistema Actual:**

✅ **Ventajas:**
1. **No depende de IP:** Funciona con NAT (múltiples dispositivos comparten IP)
2. **Difícil de falsificar:** Usa identificadores de hardware (serial, device ID)
3. **Funciona sin UDID:** Identifica dispositivos antes de tener UDID
4. **Multi-plataforma:** Funciona para móviles y Smart TVs
5. **No requiere almacenamiento en dispositivo:** El servidor lo genera

#### **Debilidades del Sistema Actual:**

⚠️ **Limitaciones:**
1. **Puede cambiar:** Si el dispositivo se actualiza, el fingerprint cambia
2. **Depende de headers:** Si el dispositivo no envía headers, usa fallback menos robusto
3. **No es 100% único:** Teóricamente dos dispositivos idénticos podrían generar el mismo fingerprint (muy improbable)

### Sugerencias de Mejora

#### **Opción 1: Combinar Fingerprint + UDID (Recomendado)**

**Mejora:** Usar fingerprint para identificación inicial, luego usar UDID (más estable)

**Ventajas:**
- Fingerprint para primera solicitud (sin UDID)
- UDID para solicitudes posteriores (más estable)
- Doble capa de seguridad

**Implementación:**
```python
# Ya implementado parcialmente
# 1. Primera solicitud: usa device_fingerprint
# 2. Solicitudes posteriores: usa UDID
```

#### **Opción 2: Almacenar Fingerprint en Dispositivo (Opcional)**

**Mejora:** Generar fingerprint en el dispositivo y almacenarlo localmente

**Ventajas:**
- Fingerprint más estable (no cambia con actualizaciones menores)
- El dispositivo puede enviarlo directamente
- Menos procesamiento en servidor

**Desventajas:**
- Requiere cambios en aplicaciones cliente
- Puede ser modificado por usuarios avanzados

**Implementación:**
```kotlin
// En el dispositivo
val fingerprint = generateFingerprint() // Generar una vez
SharedPreferences.save("device_fingerprint", fingerprint)

// Enviar en cada request
headers["X-Device-Fingerprint"] = fingerprint
```

#### **Opción 3: Usar Certificado de Dispositivo (Avanzado)**

**Mejora:** Generar un certificado único por dispositivo en el primer uso

**Ventajas:**
- Muy difícil de falsificar
- Estable (no cambia)
- Puede usarse para autenticación

**Desventajas:**
- Complejidad alta
- Requiere infraestructura PKI
- Más lento

#### **Opción 4: Combinar Múltiples Factores (Actual + Mejoras)**

**Mejora:** Agregar más factores al fingerprint

**Factores adicionales sugeridos:**
- MAC Address (si está disponible)
- Screen Resolution
- Timezone
- Idioma del sistema
- Lista de aplicaciones instaladas (hash)

**Implementación:**
```python
# Agregar más headers
fingerprint_string = (
    f"{app_type}|{tv_serial}|{device_id}|"
    f"{mac_address}|{screen_resolution}|{timezone}|"
    f"{system_language}|{installed_apps_hash}"
)
```

### Recomendación Final

**✅ El sistema actual está BIEN para la mayoría de casos de uso.**

**Mejoras sugeridas (prioridad):**

1. **🟡 MEDIA: Combinar con UDID** (ya parcialmente implementado)
   - Usar fingerprint solo para primera solicitud
   - Usar UDID para solicitudes posteriores

2. **🟢 BAJA: Agregar más factores al fingerprint**
   - MAC address, screen resolution, etc.
   - Mejora robustez sin cambios grandes

3. **🟢 BAJA: Almacenar fingerprint en dispositivo**
   - Solo si hay problemas de estabilidad
   - Requiere cambios en aplicaciones

**Conclusión:** El sistema actual es adecuado. Las mejoras son opcionales y dependen de los requisitos específicos.

---

## 5. ¿Qué hace el fingerprint? ¿El dispositivo lo manda como seguridad o no es necesario?

### Propósito del Fingerprint

#### **El Fingerprint NO es enviado por el dispositivo como medida de seguridad**

**Aclaración:** El dispositivo **NO envía el fingerprint**. El servidor lo genera.

#### **Funciones del Fingerprint:**

**1. Identificación del Dispositivo:**
- Identifica de forma única cada dispositivo
- Permite rastrear actividad por dispositivo
- Útil para auditoría y logs

**2. Rate Limiting:**
- Limita requests por dispositivo (no por IP)
- Protege contra abuso desde el mismo dispositivo
- Funciona con NAT (múltiples dispositivos comparten IP)

**3. Detección de Comportamiento Anómalo:**
- Identifica dispositivos que hacen demasiadas solicitudes
- Permite bloquear dispositivos específicos
- Útil para prevenir ataques DDoS

**4. Seguridad (Indirecta):**
- Dificulta el abuso del sistema
- Hace más difícil falsificar identidad del dispositivo
- Complementa otras medidas de seguridad

#### **¿Es Necesario?**

**✅ SÍ, es necesario para:**
- Rate limiting sin depender de IP
- Identificación de dispositivos en entornos NAT
- Protección contra abuso

**❌ NO es necesario para:**
- Autenticación (eso lo hace el UDID)
- Autorización (eso lo hace el subscriber code)
- Encriptación (eso lo hacen las credenciales)

#### **Alternativas si NO se usa Fingerprint:**

**Opción 1: Rate Limiting por IP**
- ❌ No funciona bien con NAT
- ❌ Puede bloquear usuarios legítimos
- ❌ Fácil de evadir con proxies

**Opción 2: Rate Limiting solo por UDID**
- ❌ No protege la primera solicitud (antes de tener UDID)
- ❌ Permite crear muchos UDIDs desde el mismo dispositivo

**Opción 3: Sin Rate Limiting**
- ❌ Sistema vulnerable a abuso
- ❌ Puede ser saturado fácilmente

### Conclusión

**El fingerprint es NECESARIO** para el correcto funcionamiento del sistema de rate limiting y protección contra abuso. No es una medida de seguridad directa (como autenticación), pero es una medida de seguridad indirecta importante.

**El dispositivo NO necesita enviarlo** - el servidor lo genera automáticamente a partir de los headers que el dispositivo envía normalmente.

---

## 6. Aclaración: ¿Qué pasa cuando se exceden 20 consultas en /validate?

### Comportamiento al Exceder el Límite

**Endpoint:** `/udid/validate/` (ValidateStatusUDIDView)

**Límite actual:** 20 requests por UDID cada 5 minutos

#### **Proceso cuando se hace la consulta #21:**

**1. Verificación de Rate Limit:**
```python
# udid/views.py:730-734
is_allowed, remaining, retry_after = check_udid_rate_limit(
    udid,
    max_requests=20,  # Límite
    window_minutes=5  # Ventana de 5 minutos
)
```

**2. Si se excede el límite (request #21):**

**Respuesta HTTP 429 (Too Many Requests):**
```json
{
    "error": "Rate limit exceeded",
    "message": "Too many status checks for this UDID. Please try again later.",
    "retry_after": 300,  // 5 minutos en segundos
    "remaining_requests": 0
}
```

**Headers HTTP:**
```
HTTP/1.1 429 Too Many Requests
Retry-After: 300
Content-Type: application/json
```

**3. Comportamiento:**
- ✅ **El request es RECHAZADO** (no se procesa)
- ✅ **No se consulta la base de datos** (fast-fail)
- ✅ **Se retorna error inmediatamente**
- ✅ **Se incluye tiempo de espera** (`retry_after`)

**4. Logging:**
```python
# Se registra en logs
logger.warning(
    f"Rate limit exceeded: udid={udid}..., "
    f"count=21, limit=20, window=5min, retry_after=300s"
)
```

**5. Contador de Violaciones:**
- Se incrementa un contador de violaciones en Redis
- Se usa para detección de comportamiento anómalo
- Puede afectar límites adaptativos futuros

#### **Ejemplo de Flujo:**

```
Request #1-20: ✅ Procesados normalmente (HTTP 200)
Request #21:   ❌ Rechazado (HTTP 429, Retry-After: 300s)
Request #22:   ❌ Rechazado (HTTP 429, Retry-After: 299s)
...
Request #N:    ❌ Rechazado hasta que pasen 5 minutos
```

**Después de 5 minutos:**
- El contador se resetea
- Se pueden hacer 20 requests nuevos
- El ciclo se repite

#### **Código Relevante:**

```python
# udid/views.py:730-744
is_allowed, remaining, retry_after = check_udid_rate_limit(
    udid,
    max_requests=20,
    window_minutes=5
)

if not is_allowed:
    return Response({
        "error": "Rate limit exceeded",
        "message": "Too many status checks for this UDID. Please try again later.",
        "retry_after": retry_after,  # 300 segundos (5 minutos)
        "remaining_requests": remaining  # 0
    }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
        "Retry-After": str(retry_after)
    })
```

**Resumen:**
- ❌ **Request #21 es RECHAZADO** con HTTP 429
- ⏱️ **Debe esperar 5 minutos** antes de poder hacer más requests
- 📊 **Se registra en logs** para auditoría
- 🔒 **No se procesa** (fast-fail, no toca BD)

---

## 7. ¿El tiempo de duración de apertura del WS es prudente o se debería ampliar?

### Análisis del Timeout Actual

**Timeout actual:** 60 segundos

**Ubicación:** `udid/consumers.py:41`, `ubuntu/settings.py:146`

```python
TIMEOUT_SECONDS = getattr(settings, "UDID_WAIT_TIMEOUT", 60)  # 60 segundos
```

#### **Análisis:**

**✅ 60 segundos es PRUDENTE para la mayoría de casos:**

**Ventajas del timeout corto (60s):**
1. **Protección contra recursos colgados:** Evita conexiones que consumen recursos indefinidamente
2. **Detección rápida de problemas:** Si no se valida en 60s, probablemente hay un problema
3. **Liberación rápida de recursos:** Permite que otros dispositivos se conecten
4. **Mejor experiencia de usuario:** El usuario sabe rápidamente si hay un problema

**Desventajas del timeout corto (60s):**
1. **Puede ser corto para validación manual:** Si un operador tarda en validar, puede expirar
2. **Reconexiones frecuentes:** Si el timeout es muy corto, puede haber muchas reconexiones

#### **Recomendaciones:**

**🟢 Para Validación Automática:**
- **60 segundos es ADECUADO**
- La validación automática debería ser casi instantánea
- Si tarda más de 60s, probablemente hay un problema

**🟡 Para Validación Manual:**
- **Considerar aumentar a 120-180 segundos**
- Los operadores pueden tardar más en validar
- 60s puede ser corto si hay múltiples validaciones pendientes

**🔴 Para Casos Especiales:**
- **Considerar timeout configurable por tipo de validación**
- Validación automática: 60s
- Validación manual: 180s

#### **Sugerencia de Mejora:**

```python
# settings.py
# Timeout diferente según el método de validación
UDID_WAIT_TIMEOUT_AUTOMATIC = int(os.getenv("UDID_WAIT_TIMEOUT_AUTOMATIC", "60"))  # 60s
UDID_WAIT_TIMEOUT_MANUAL = int(os.getenv("UDID_WAIT_TIMEOUT_MANUAL", "180"))  # 180s
```

**Implementación:**
```python
# udid/consumers.py
# Determinar timeout según el método de validación
if req.method == 'automatic':
    timeout = settings.UDID_WAIT_TIMEOUT_AUTOMATIC
else:
    timeout = settings.UDID_WAIT_TIMEOUT_MANUAL
```

### Conclusión

**✅ 60 segundos es PRUDENTE para validación automática**

**🟡 Considerar aumentar a 120-180 segundos para validación manual**

**Recomendación:** Mantener 60s como default, pero hacer configurable según el método de validación.

---

## 8. ¿Se puede reducir el número de WS que estén abiertos por UDID? ¿Ayudaría a reducir la carga del servidor?

### Análisis del Límite Actual

**Límite actual:** 5 conexiones WebSocket por dispositivo/UDID

**Ubicación:** `udid/consumers.py:46`

```python
MAX_CONNECTIONS_PER_TOKEN = getattr(settings, "UDID_WS_MAX_PER_TOKEN", 5)
```

#### **¿Se puede reducir?**

**✅ SÍ, se puede reducir fácilmente**

**Configuración actual:**
```python
# settings.py (o variable de entorno)
UDID_WS_MAX_PER_TOKEN = 5  # Configurable
```

**Opciones:**
- **Reducir a 3:** Más restrictivo, menos carga
- **Reducir a 2:** Muy restrictivo, significativamente menos carga
- **Reducir a 1:** Máximo restrictivo, mínima carga

#### **¿Ayudaría a reducir la carga del servidor?**

**✅ SÍ, reduciría la carga significativamente:**

**Impacto en carga:**
1. **Menos conexiones activas:** Menos recursos de memoria
2. **Menos procesamiento:** Menos pings, menos verificaciones
3. **Menos ancho de banda:** Menos tráfico de red
4. **Menos overhead:** Menos gestión de conexiones

**Estimación de reducción:**
- **De 5 a 3:** ~40% menos conexiones por dispositivo
- **De 5 a 2:** ~60% menos conexiones por dispositivo
- **De 5 a 1:** ~80% menos conexiones por dispositivo

#### **Consideraciones:**

**⚠️ Desventajas de reducir demasiado:**

1. **Múltiples aplicaciones en el mismo dispositivo:**
   - Si un usuario tiene la app en TV y móvil, necesita 2 conexiones
   - Con límite de 1, solo una app puede conectarse

2. **Reconexiones:**
   - Si hay problemas de red, puede haber reconexiones
   - Con límite muy bajo, puede bloquear reconexiones legítimas

3. **Experiencia de usuario:**
   - Si se rechazan conexiones legítimas, el usuario puede tener problemas

#### **Recomendación:**

**🟡 Reducir a 3 conexiones por UDID:**

**Ventajas:**
- ✅ Reduce carga significativamente (~40%)
- ✅ Permite múltiples aplicaciones (TV + móvil)
- ✅ Permite reconexiones
- ✅ Sigue siendo razonable para uso normal

**Implementación:**
```python
# settings.py
UDID_WS_MAX_PER_TOKEN = int(os.getenv("UDID_WS_MAX_PER_TOKEN", "3"))  # Reducido de 5 a 3
```

**Para casos extremos (alta carga):**
```python
# Reducir a 2 si hay problemas de carga
UDID_WS_MAX_PER_TOKEN = int(os.getenv("UDID_WS_MAX_PER_TOKEN", "2"))
```

### Conclusión

**✅ SÍ, se puede reducir fácilmente**

**✅ SÍ, ayudaría a reducir la carga del servidor**

**Recomendación:** Reducir a **3 conexiones por UDID** como balance entre carga y funcionalidad.

---

## 9. Aclaración: ¿El servidor solo puede abrir 5 WS simultáneos sin importar el número de dispositivos?

### Aclaración Importante

**❌ NO, esa interpretación es INCORRECTA**

#### **Límites Reales:**

**A) Límite por Dispositivo/UDID:**
- **5 conexiones por dispositivo/UDID** (configurable)
- Cada dispositivo puede tener hasta 5 conexiones
- Si hay 100 dispositivos, pueden haber hasta 500 conexiones (100 × 5)

**B) Límite Global del Sistema:**
- **1000 conexiones simultáneas en todo el sistema** (configurable)
- Límite total para todos los dispositivos combinados
- Si hay 1000 dispositivos, solo 1000 pueden tener conexión (no 5000)

#### **Ejemplo Práctico:**

```
Dispositivo A (UDID: abc123): 5 conexiones ✅
Dispositivo B (UDID: def456): 5 conexiones ✅
Dispositivo C (UDID: ghi789): 5 conexiones ✅
...
Dispositivo 200 (UDID: xyz999): 5 conexiones ✅

Total: 200 dispositivos × 5 conexiones = 1000 conexiones ✅
```

**Si el Dispositivo A intenta abrir la 6ta conexión:**
```
Dispositivo A, conexión #6: ❌ RECHAZADA (límite de 5 por dispositivo)
```

**Si hay 201 dispositivos intentando conectarse:**
```
Dispositivo 201, conexión #1: ❌ RECHAZADA (límite global de 1000)
```

#### **Código Relevante:**

```python
# udid/consumers.py:46-47
MAX_CONNECTIONS_PER_TOKEN = 5  # Por dispositivo/UDID
MAX_GLOBAL_CONNECTIONS = 1000  # Total del sistema

# Verificación
is_allowed, reason, retry_after = check_websocket_limits(
    udid=self.udid,
    device_fingerprint=self.device_fingerprint,
    max_per_token=5,      # Límite por dispositivo
    max_global=1000       # Límite global
)
```

#### **Resumen:**

| Límite | Valor | Alcance |
|--------|-------|---------|
| **Por dispositivo/UDID** | 5 conexiones | Cada dispositivo individual |
| **Global del sistema** | 1000 conexiones | Todos los dispositivos combinados |

**Ejemplo:**
- ✅ **100 dispositivos** pueden tener **5 conexiones cada uno** = **500 conexiones totales**
- ✅ **200 dispositivos** pueden tener **5 conexiones cada uno** = **1000 conexiones totales** (límite global)
- ❌ **201 dispositivos** → El dispositivo #201 es rechazado (límite global alcanzado)

### Conclusión

**❌ NO son solo 5 WS totales**

**✅ Son 5 WS por dispositivo, con un máximo global de 1000 WS**

**En la práctica:**
- Hasta **200 dispositivos** pueden tener 5 conexiones cada uno
- O **1000 dispositivos** pueden tener 1 conexión cada uno
- O cualquier combinación que no exceda 1000 conexiones totales

---

## Resumen Ejecutivo

| Pregunta | Respuesta |
|----------|-----------|
| **1. Quién genera fingerprint** | Servidor (Django) genera el hash SHA256 |
| **2. Dónde se almacena** | BD (UDIDAuthRequest) + Redis (cache temporal) |
| **3. Es por dispositivo** | Sí, único por dispositivo (mismo dispositivo = mismo fingerprint) |
| **4. Cómo llega al servidor** | El dispositivo NO lo envía, el servidor lo genera de los headers HTTP |
| **5. Alternativas** | Sistema actual está bien, mejoras opcionales sugeridas |
| **6. Propósito** | Rate limiting, identificación, seguridad indirecta |
| **7. Exceder 20 consultas** | HTTP 429, rechazado, debe esperar 5 minutos |
| **8. Timeout WS (60s)** | Prudente para automático, considerar aumentar para manual |
| **9. Reducir WS por UDID** | Sí, recomendado reducir a 3 (reduce carga ~40%) |
| **10. Límite de WS** | 5 por dispositivo, 1000 globales (NO 5 totales) |

---

**Última actualización:** 2025-01-27





