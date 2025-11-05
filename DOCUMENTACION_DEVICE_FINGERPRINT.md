# Documentación: Device Fingerprint Mejorado

## Tarea 1.2: Mejorar Device Fingerprint para Móviles y Smart TVs

### Descripción

Se ha mejorado el sistema de device fingerprint para ser más robusto y específico para aplicaciones móviles y Smart TVs. El nuevo sistema usa identificadores nativos del dispositivo que son más difíciles de falsificar.

### Headers Requeridos

Las aplicaciones cliente deben enviar los siguientes headers HTTP para generar un fingerprint robusto:

#### Headers Básicos (siempre disponibles)
- `User-Agent`: Agente de usuario del dispositivo
- `Accept-Language`: Idiomas aceptados
- `Accept-Encoding`: Codificaciones aceptadas
- `Accept`: Tipos de contenido aceptados

#### Headers para Móviles (Android/iOS)
- `X-Device-ID`: ID único del dispositivo (Android ID, iOS IdentifierForVendor)
- `X-App-Type`: Tipo de aplicación (`android_mobile`, `ios_mobile`, `mobile_app`)
- `X-App-Version`: Versión de la aplicación
- `X-OS-Version`: Versión del sistema operativo
- `X-Device-Model`: Modelo del dispositivo
- `X-Build-ID`: Build fingerprint (Android) - opcional pero recomendado

#### Headers para Smart TVs
- `X-Device-ID`: ID único del dispositivo
- `X-App-Type`: Tipo de aplicación (`android_tv`, `samsung_tv`, `lg_tv`, `set_top_box`)
- `X-App-Version`: Versión de la aplicación
- `X-TV-Serial`: Número de serie de la TV (muy importante)
- `X-TV-Model`: Modelo específico de la TV
- `X-Firmware-Version`: Versión del firmware

### Implementación en Apps Cliente

#### Android Mobile
```kotlin
val headers = mapOf(
    "X-Device-ID" to Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID),
    "X-App-Type" to "android_mobile",
    "X-App-Version" to BuildConfig.VERSION_NAME,
    "X-OS-Version" to Build.VERSION.RELEASE,
    "X-Device-Model" to "${Build.MANUFACTURER} ${Build.MODEL}",
    "X-Build-ID" to Build.FINGERPRINT
)
```

#### iOS Mobile
```swift
let headers: [String: String] = [
    "X-Device-ID": UIDevice.current.identifierForVendor?.uuidString ?? "",
    "X-App-Type": "ios_mobile",
    "X-App-Version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "",
    "X-OS-Version": UIDevice.current.systemVersion,
    "X-Device-Model": UIDevice.current.model
]
```

#### Android TV
```kotlin
val headers = mapOf(
    "X-Device-ID" to Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID),
    "X-App-Type" to "android_tv",
    "X-App-Version" to BuildConfig.VERSION_NAME,
    "X-TV-Serial" to Build.SERIAL, // o usar otro método para obtener serial
    "X-TV-Model" to "${Build.MANUFACTURER} ${Build.MODEL}",
    "X-Firmware-Version" to Build.VERSION.RELEASE
)
```

#### Samsung TV / LG TV / Tizen
```javascript
// Tizen Web App
const headers = {
    'X-Device-ID': tizen.systeminfo.getCapability('http://tizen.org/feature/screen.size.normal'),
    'X-App-Type': 'samsung_tv', // o 'lg_tv'
    'X-App-Version': '1.0.0',
    'X-TV-Serial': tizen.systeminfo.getPropertyValue('DUID'),
    'X-TV-Model': tizen.systeminfo.getPropertyValue('modelName'),
    'X-Firmware-Version': tizen.systeminfo.getPropertyValue('platformVersion')
};
```

### Cómo Funciona

1. **Clasificación por Tipo de App:**
   - El sistema identifica el tipo de app desde `X-App-Type`
   - Usa diferentes combinaciones de headers según el tipo

2. **Smart TVs:**
   - Prioriza: Serial Number, Model, Firmware
   - Estos son más difíciles de falsificar que headers HTTP básicos

3. **Móviles:**
   - Prioriza: Device ID, Build ID, Model, OS Version
   - Usa identificadores nativos del sistema operativo

4. **Fallback:**
   - Si no hay headers específicos, usa headers HTTP básicos
   - Menos robusto pero funciona para compatibilidad

### Beneficios

1. **Más Difícil de Falsificar:**
   - Serial numbers y Device IDs son únicos del hardware
   - Build fingerprints son específicos del dispositivo

2. **Mejor Identificación:**
   - Mismo dispositivo siempre genera mismo fingerprint
   - Diferentes dispositivos generan fingerprints diferentes

3. **Específico por Plataforma:**
   - Optimizado para cada tipo de dispositivo
   - Usa los identificadores más confiables disponibles

### Rate Limiting

El device fingerprint se usa para rate limiting en:
- `/udid/request-udid/` - Primera solicitud sin UDID
- Protege contra creación masiva de UDIDs desde el mismo dispositivo

### Pruebas

Para probar el device fingerprint:

```python
from django.test import RequestFactory
from udid.util import generate_device_fingerprint

factory = RequestFactory()

# Test con headers móvil
request = factory.get('/udid/request-udid/', HTTP_X_DEVICE_ID='test123', 
                      HTTP_X_APP_TYPE='android_mobile', HTTP_X_APP_VERSION='1.0')
fp1 = generate_device_fingerprint(request)

# Mismo dispositivo debería generar mismo fingerprint
request2 = factory.get('/udid/request-udid/', HTTP_X_DEVICE_ID='test123', 
                       HTTP_X_APP_TYPE='android_mobile', HTTP_X_APP_VERSION='1.0')
fp2 = generate_device_fingerprint(request2)

assert fp1 == fp2, "Mismo dispositivo debe generar mismo fingerprint"

# Diferente dispositivo debe generar diferente fingerprint
request3 = factory.get('/udid/request-udid/', HTTP_X_DEVICE_ID='test456', 
                       HTTP_X_APP_TYPE='android_mobile', HTTP_X_APP_VERSION='1.0')
fp3 = generate_device_fingerprint(request3)

assert fp1 != fp3, "Diferentes dispositivos deben generar fingerprints diferentes"
```

### Migración

Las apps existentes que no envíen estos headers seguirán funcionando usando el fallback, pero se recomienda actualizar las apps para enviar los headers necesarios para mejor seguridad.

### Notas Importantes

- Los headers son opcionales (hay fallback)
- El sistema es compatible con versiones anteriores
- Se recomienda enviar todos los headers disponibles para máxima robustez
- El serial number de Smart TVs es especialmente importante para identificación única

