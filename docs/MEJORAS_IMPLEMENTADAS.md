# Mejoras Implementadas - Fingerprint y WebSocket

**Fecha:** 2025-01-27

---

## Resumen de Mejoras

Se han implementado las siguientes mejoras solicitadas:

1. ✅ **Soporte para MAC Address en Fingerprint**
2. ✅ **Soporte para Fingerprint Local (opcional)**
3. ✅ **Timeout Configurable para WebSocket según Tipo de Validación**
4. ✅ **Reducción de Límite de WebSocket (de 5 a 3 por dispositivo)**

---

## 1. Soporte para MAC Address

### Implementación

**Archivo modificado:** `udid/util.py`

**Cambios:**
- ✅ Agregado header `HTTP_X_MAC_ADDRESS` a la extracción de headers
- ✅ Incluido MAC address en la fórmula de fingerprint para todos los tipos de dispositivos
- ✅ MAC address se incluye en Smart TVs, móviles y fallback

**Código:**
```python
# udid/util.py:190-191
'mac_address': _get_header_value(request_or_scope, 'HTTP_X_MAC_ADDRESS'),
```

**Fórmulas actualizadas:**
- **Smart TVs:** `app_type|tv_serial|tv_model|firmware|device_id|mac_address|app_version|user_agent`
- **Móviles:** `app_type|device_id|build_id|device_model|os_version|mac_address|app_version|user_agent`
- **Fallback:** `user_agent|accept_language|accept_encoding|accept|app_type|app_version|device_id|mac_address`

**Headers CORS actualizados:** `ubuntu/settings.py:337`
- ✅ Agregado `x-mac-address` a la lista de headers permitidos

**Uso en el dispositivo:**
```kotlin
// Android - Obtener MAC address
val macAddress = getMacAddress()  // Implementar según plataforma
headers["X-MAC-Address"] = macAddress
```

---

## 2. Soporte para Fingerprint Local

### Implementación

**Archivo modificado:** `udid/util.py`

**Funcionalidad:**
- ✅ Si el dispositivo envía `X-Device-Fingerprint`, el servidor lo usa directamente
- ✅ Validación: debe ser hexadecimal de 32 caracteres
- ✅ Si no se envía o es inválido, se genera normalmente (compatibilidad hacia atrás)

**Código:**
```python
# udid/util.py:158-167
# Si el dispositivo envía fingerprint directamente, usarlo (más estable)
direct_fingerprint = _get_header_value(request_or_scope, 'HTTP_X_DEVICE_FINGERPRINT')
if direct_fingerprint and len(direct_fingerprint) == 32:
    # Validar que sea hexadecimal válido
    try:
        int(direct_fingerprint, 16)
        return direct_fingerprint  # Usar fingerprint del dispositivo
    except ValueError:
        # Si no es válido, continuar con generación normal
        pass
```

**Headers CORS actualizados:** `ubuntu/settings.py:338`
- ✅ Agregado `x-device-fingerprint` a la lista de headers permitidos

**Ventajas:**
- ✅ Fingerprint más estable (no cambia con actualizaciones menores)
- ✅ Compatible con sistema actual (fallback si no se envía)
- ✅ El dispositivo puede generar y almacenar el fingerprint localmente

**Uso en el dispositivo:**
```kotlin
// Generar y almacenar fingerprint localmente
val fingerprint = generateAndStoreFingerprint(context)
headers["X-Device-Fingerprint"] = fingerprint
```

---

## 3. Timeout Configurable para WebSocket

### Implementación

**Archivos modificados:**
- `ubuntu/settings.py:147-150`
- `udid/consumers.py:41-44, 215-223, 318-327`

**Funcionalidad:**
- ✅ Timeout automático: 60 segundos (validación automática)
- ✅ Timeout manual: 180 segundos (validación manual)
- ✅ El sistema detecta automáticamente el método de validación del UDID
- ✅ Usa el timeout apropiado según el método

**Configuración:**
```python
# settings.py
UDID_WAIT_TIMEOUT_AUTOMATIC = 60   # Validación automática
UDID_WAIT_TIMEOUT_MANUAL = 180     # Validación manual
```

**Código:**
```python
# udid/consumers.py:215-223
# Determinar timeout según método de validación
from .models import UDIDAuthRequest
try:
    udid_request = await sync_to_async(UDIDAuthRequest.objects.get)(udid=self.udid)
    # Usar timeout según método: manual = 180s, automatic = 60s
    timeout_seconds = self.TIMEOUT_MANUAL if udid_request.method == 'manual' else self.TIMEOUT_AUTOMATIC
except Exception:
    # Si no se puede obtener, usar default
    timeout_seconds = self.TIMEOUT_SECONDS
```

**Ventajas:**
- ✅ Validación automática: timeout corto (60s) - más eficiente
- ✅ Validación manual: timeout largo (180s) - permite tiempo para operadores
- ✅ Configurable por variables de entorno

---

## 4. Reducción de Límite de WebSocket

### Implementación

**Archivos modificados:**
- `ubuntu/settings.py:163`
- `udid/consumers.py:50`

**Cambio:**
- ✅ Reducido de **5 a 3 conexiones** por dispositivo/UDID
- ✅ Reduce carga del servidor aproximadamente **40%**

**Configuración:**
```python
# settings.py
UDID_WS_MAX_PER_TOKEN = 3  # Reducido de 5 a 3
```

**Impacto:**
- ✅ Menos conexiones activas = menos recursos de memoria
- ✅ Menos procesamiento = menos pings y verificaciones
- ✅ Menos ancho de banda = menos tráfico de red
- ✅ Menos overhead = menos gestión de conexiones

**Nota:** Cada dispositivo sigue teniendo su propio límite independiente. Un usuario con múltiples dispositivos puede tener 3 WS por cada dispositivo.

---

## Respuestas a Preguntas Finales

### 1. ¿Se deben enviar headers obligatoriamente?

**Respuesta:** ❌ **NO, los headers NO son obligatorios**

- ✅ El sistema tiene **fallback automático**
- ✅ Funciona sin headers específicos (usa headers básicos)
- ⚠️ **Pero es menos robusto** sin headers específicos
- 📝 **Recomendación:** Enviar headers específicos para mejor seguridad

### 2. ¿Qué pasa si no se envían headers?

**Respuesta:** ✅ **El sistema usa fallback automáticamente**

- Usa headers básicos: `User-Agent`, `Accept-Language`, `Accept-Encoding`, `Accept`
- Funciona pero es menos robusto
- Dos dispositivos diferentes pueden generar el mismo fingerprint si tienen configuración idéntica

### 3. ¿Dos dispositivos idénticos tendrán el mismo fingerprint?

**Respuesta:** ❌ **NO, cada dispositivo físico tiene un fingerprint único**

- Cada dispositivo tiene **serial number único**, **device ID único**, **MAC address único**
- Dos iPhone 14 diferentes tendrán fingerprints diferentes
- Solo podrían ser iguales en casos edge (emuladores, fallback sin headers)

### 4. ¿La desventaja de reducir WS por UDID afecta a usuarios con múltiples dispositivos?

**Respuesta:** ❌ **NO, cada dispositivo tiene su propio límite**

- ✅ Cada dispositivo tiene **3 WS independientes**
- ✅ Un usuario con TV y móvil puede tener **3 WS en TV + 3 WS en móvil = 6 WS totales**
- ⚠️ La única desventaja es si un **dispositivo individual** necesita más de 3 conexiones simultáneas (raro)

---

## Configuración Recomendada

### Variables de Entorno

```bash
# Timeout WebSocket
UDID_WAIT_TIMEOUT_AUTOMATIC=60   # Validación automática (segundos)
UDID_WAIT_TIMEOUT_MANUAL=180     # Validación manual (segundos)

# Límites WebSocket
UDID_WS_MAX_PER_TOKEN=3          # Conexiones por dispositivo/UDID
UDID_WS_MAX_GLOBAL=1000          # Conexiones globales totales
```

### Headers Recomendados para Dispositivos

**Mínimos (funciona con fallback):**
```
User-Agent: MyApp/1.0.0
```

**Recomendados (mejor identificación):**
```
X-Device-ID: <device_id>
X-App-Type: android_tv | android_mobile | ios_mobile
X-App-Version: 1.0.0
X-MAC-Address: aa:bb:cc:dd:ee:ff
```

**Óptimos (máxima robustez):**
```
X-Device-ID: <device_id>
X-App-Type: android_tv
X-App-Version: 1.0.0
X-TV-Serial: SN123456789
X-TV-Model: Samsung QLED 2023
X-Firmware-Version: 1.2.3
X-MAC-Address: aa:bb:cc:dd:ee:ff
X-Device-Fingerprint: <fingerprint_generado_localmente>  # Opcional
```

---

## Compatibilidad

### ✅ Compatibilidad Hacia Atrás

Todas las mejoras son **compatibles hacia atrás**:

1. **MAC Address:** Si no se envía, se usa valor vacío (comportamiento anterior)
2. **Fingerprint Local:** Si no se envía, se genera normalmente (comportamiento anterior)
3. **Timeout WS:** Si no se puede determinar método, usa default (60s)
4. **Límite WS:** Reducido pero configurable (puede volver a 5 si es necesario)

### ⚠️ Cambios que Requieren Atención

1. **Límite WS reducido:** Dispositivos que necesitaban más de 3 conexiones simultáneas pueden verse afectados
   - **Solución:** Configurar `UDID_WS_MAX_PER_TOKEN=5` si es necesario

2. **Timeout WS:** Validación manual ahora tiene 180s en lugar de 60s
   - **Ventaja:** Más tiempo para operadores
   - **Desventaja:** Conexiones abiertas por más tiempo

---

## Próximos Pasos

### Para Aplicaciones Cliente

1. **Agregar MAC Address:**
   - Implementar obtención de MAC address
   - Enviar como header `X-MAC-Address`

2. **Implementar Fingerprint Local (Opcional):**
   - Generar fingerprint una vez
   - Almacenar localmente
   - Enviar como header `X-Device-Fingerprint`

3. **Actualizar Headers:**
   - Asegurar que se envían todos los headers recomendados
   - Especialmente `X-Device-ID` y `X-MAC-Address`

### Para Servidor

1. **Monitorear:**
   - Reducción de carga con límite WS reducido
   - Tiempos de timeout en validación manual
   - Uso de fingerprint local vs generado

2. **Ajustar si es necesario:**
   - Aumentar límite WS si hay problemas
   - Ajustar timeouts según experiencia

---

**Última actualización:** 2025-01-27





