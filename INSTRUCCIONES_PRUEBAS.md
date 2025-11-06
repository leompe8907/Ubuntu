# Instrucciones para Pruebas del Sistema

## Prerrequisitos

1. **Servidor Django corriendo**: El servidor debe estar activo en el puerto configurado (por defecto `http://localhost:8000`)

2. **Dependencias instaladas**: Asegúrate de tener `requests` instalado:
   ```bash
   pip install requests
   ```

3. **Redis (Opcional pero recomendado)**: 
   - Si tienes Redis configurado, asegúrate de que esté corriendo
   - Si no, el sistema usará LocMemCache (solo para desarrollo, no distribuido)

## Ejecutar las Pruebas

### Método 1: Script de Pruebas Automático

Ejecuta el script de pruebas completo:

```bash
python test_sistema.py
```

Este script verificará:
1. ✅ Conexión a Redis/Cache
2. ✅ Rate Limiting (device fingerprint)
3. ✅ Rastreo de carga del sistema
4. ✅ Endpoints HTTP (request UDID, validate status, rate limits)
5. ✅ Logging (verificación de archivo de logs)

### Método 2: Pruebas Manuales con cURL

#### 1. Probar Request UDID (endpoint principal)
```bash
curl -X GET "http://localhost:8000/request-udid/" \
  -H "Content-Type: application/json" \
  -H "x-device-id: test-device-123" \
  -H "x-os-version: TestOS/1.0" \
  -H "x-device-model: TestDevice" \
  -v
```

#### 2. Probar Rate Limiting (enviar 3 requests rápidas)
```bash
# Request 1 (debe funcionar)
curl -X GET "http://localhost:8000/request-udid/" \
  -H "x-device-id: test-device-123" \
  -v

# Request 2 (debe funcionar)
curl -X GET "http://localhost:8000/request-udid/" \
  -H "x-device-id: test-device-123" \
  -v

# Request 3 (debe ser bloqueado con 429)
curl -X GET "http://localhost:8000/request-udid/" \
  -H "x-device-id: test-device-123" \
  -v
```

#### 3. Probar Login con Rate Limiting
```bash
# Login exitoso (si tienes usuario de prueba)
curl -X POST "http://localhost:8000/auth/login/" \
  -H "Content-Type: application/json" \
  -H "x-device-id: test-device-123" \
  -d '{"username": "test_user", "password": "test_pass"}' \
  -v

# Intentos fallidos (para probar rate limiting)
for i in {1..6}; do
  echo "Intento $i:"
  curl -X POST "http://localhost:8000/auth/login/" \
    -H "Content-Type: application/json" \
    -H "x-device-id: test-device-123" \
    -d '{"username": "test_user", "password": "wrong_pass"}' \
    -v
  sleep 1
done
```

### Método 3: Verificar Logs en Tiempo Real

#### Windows (PowerShell):
```powershell
Get-Content server.log -Wait -Tail 20
```

#### Linux/Mac:
```bash
tail -f server.log
```

## Verificar Funcionalidades Específicas

### 1. Verificar que Redis está funcionando

Ejecuta en la consola de Django:
```python
python manage.py shell
```

Luego:
```python
from django.core.cache import cache
from django.conf import settings

# Verificar tipo de cache
print("Redis URL:", settings.REDIS_URL)
print("Cache backend:", settings.CACHES['default']['BACKEND'])

# Probar cache
cache.set('test_key', 'test_value', 60)
print("Valor guardado:", cache.get('test_key'))
```

### 2. Verificar Rate Limiting

En la consola de Django:
```python
from udid.util import check_device_fingerprint_rate_limit

# Probar rate limit
test_fp = "test_fingerprint_123"
for i in range(5):
    allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
        test_fp, max_requests=2, window_minutes=5
    )
    print(f"Request {i+1}: Allowed={allowed}, Remaining={remaining}, RetryAfter={retry_after}")
```

### 3. Verificar Logging

Busca en `server.log` mensajes como:
- `RequestUDIDView: Request recibido`
- `Rate limit exceeded`
- `LoginView: Login exitoso`
- `AuthenticateWithUDIDView: Autenticación exitosa`

### 4. Verificar System Load Tracking

En la consola de Django:
```python
from udid.util import get_system_load, track_system_request

# Simular algunas requests
for i in range(10):
    track_system_request()

# Verificar carga
load = get_system_load()
print(f"System load: {load}")  # Debe mostrar 'normal', 'high', o 'critical'
```

## Qué Esperar en las Pruebas

### ✅ Pruebas Exitosas:

1. **Rate Limiting**: 
   - Primeras 2 requests permitidas
   - Tercera request bloqueada con código 429
   - Header `Retry-After` presente

2. **Logging**:
   - Archivo `server.log` se crea o actualiza
   - Logs contienen información estructurada (IP, UDID, device fingerprint, etc.)

3. **Cache**:
   - Si Redis está configurado: muestra "Redis funcionando"
   - Si no: muestra "LocMemCache funcionando" (solo desarrollo)

4. **Endpoints**:
   - `/request-udid/` retorna 201 con UDID
   - `/validate-status/?udid=XXX` retorna 200 o 404 según corresponda
   - Rate limits aplican correctamente

### ⚠️ Problemas Comunes:

1. **"No se pudo conectar al servidor"**:
   - Verifica que el servidor Django esté corriendo
   - Verifica la URL en `BASE_URL` del script

2. **"Rate limit no funciona"**:
   - Verifica que Redis esté configurado (o acepta LocMemCache para desarrollo)
   - Verifica que el cache esté funcionando

3. **"No hay logs"**:
   - Verifica que el archivo `server.log` tenga permisos de escritura
   - Verifica la configuración de logging en `settings.py`

4. **"Redis no está funcionando"**:
   - Si Redis no está disponible, el sistema usará LocMemCache automáticamente
   - Para producción, asegúrate de configurar `REDIS_URL` en variables de entorno

## Pruebas de Carga (Opcional)

Para probar el sistema bajo carga (simular reconexión masiva):

```bash
# Instalar Apache Bench (ab)
# Windows: Descargar desde Apache
# Linux: sudo apt-get install apache2-utils
# Mac: brew install httpd

# Probar con 100 requests, 10 concurrentes
ab -n 100 -c 10 -H "x-device-id: test-device" \
   http://localhost:8000/request-udid/
```

## Verificar Logs de Rate Limiting Específicos

Los logs de rate limiting se guardan con el logger `rate_limiting`. Para ver solo esos logs:

```bash
# Windows PowerShell
Select-String -Path server.log -Pattern "rate_limiting" | Select-Object -Last 20

# Linux/Mac
grep "rate_limiting" server.log | tail -20
```

## Siguiente Paso

Una vez que todas las pruebas pasen, puedes:
1. Revisar los logs en `server.log` para verificar que la información se está registrando correctamente
2. Probar con aplicaciones móviles/Smart TVs reales
3. Monitorear el comportamiento bajo carga real

