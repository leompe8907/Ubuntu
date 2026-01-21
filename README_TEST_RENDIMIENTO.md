# Script de Prueba de Rendimiento Completo

Este script simula múltiples usuarios ejecutando el flujo completo de autenticación UDID para evaluar el rendimiento del servidor bajo carga.

## Características

- ✅ Simula hasta 1000 usuarios concurrentes
- ✅ Ejecuta el flujo completo de autenticación:
  1. Solicitar un UDID
  2. Validar el UDID con subscriber_code
  3. Abrir conexión WebSocket
  4. Enviar mensaje de autenticación
  5. Recibir credenciales codificadas
  6. Validar el proceso completo
- ✅ Genera estadísticas detalladas:
  - Solicitudes completadas vs fallidas
  - Errores por paso del flujo
  - Errores por tipo
  - Códigos de estado HTTP
  - Tiempos de respuesta (promedio, mediana, P95, P99)
  - Análisis de rendimiento del servidor
  - Identificación de cuellos de botella

## Requisitos

### Dependencias Python

El script requiere las siguientes dependencias:

```bash
# Opción 1: Usar aiohttp (recomendado)
pip install aiohttp websockets

# Opción 2: Usar httpx (alternativa)
pip install httpx websockets
```

### Requisitos del Sistema

- Python 3.7 o superior
- Acceso al servidor UDID (HTTP y WebSocket)
- Un subscriber_code válido en la base de datos para las pruebas

## Uso

### Ejemplo Básico

```bash
python test_rendimiento_completo.py \
    --host http://localhost:8000 \
    --users 100 \
    --subscriber-code TEST123
```

### Ejemplo Completo (1000 usuarios)

```bash
python test_rendimiento_completo.py \
    --host http://localhost:8000 \
    --users 1000 \
    --subscriber-code TEST123 \
    --max-concurrent 50 \
    --timeout 60
```

### Ejemplo con Configuración Personalizada

```bash
python test_rendimiento_completo.py \
    --host https://api.ejemplo.com \
    --users 500 \
    --subscriber-code ABC123 \
    --operator-id test_operator \
    --app-type android_tv \
    --app-version 1.0 \
    --timeout 45 \
    --max-concurrent 100
```

## Parámetros

| Parámetro | Descripción | Default | Requerido |
|-----------|-------------|---------|-----------|
| `--host` | URL base del servidor | - | ✅ Sí |
| `--users` | Número de usuarios a simular | 1000 | ❌ No |
| `--subscriber-code` | Código de suscriptor para pruebas | - | ✅ Sí |
| `--operator-id` | ID del operador | test_operator | ❌ No |
| `--app-type` | Tipo de aplicación | android_tv | ❌ No |
| `--app-version` | Versión de la aplicación | 1.0 | ❌ No |
| `--timeout` | Timeout en segundos por operación | 30 | ❌ No |
| `--max-concurrent` | Máximo de usuarios concurrentes | 100 | ❌ No |

## Interpretación de Resultados

### Resumen General

- **Usuarios exitosos**: Porcentaje de usuarios que completaron el flujo completo
- **Usuarios fallidos**: Porcentaje de usuarios que fallaron en algún paso
- **Usuarios por segundo**: Velocidad de procesamiento del servidor

### Errores por Paso

Indica en qué paso del flujo fallaron más usuarios:
- `request_udid`: Error al solicitar el UDID
- `validate_udid`: Error al validar el UDID
- `websocket_auth`: Error en la conexión WebSocket o recepción de credenciales
- `execution`: Error en la ejecución del script

### Tiempos de Respuesta

- **Promedio**: Tiempo promedio de respuesta
- **Mediana**: Tiempo mediano (50% de las respuestas)
- **P95**: 95% de las respuestas fueron más rápidas que este valor
- **P99**: 99% de las respuestas fueron más rápidas que este valor

### Análisis de Rendimiento

El script evalúa automáticamente el rendimiento:

- ✅ **EXCELENTE** (≥95% éxito): El servidor manejó la carga muy bien
- ⚠️ **BUENO** (≥80% éxito): El servidor manejó la carga bien, pero hay margen de mejora
- ⚠️ **REGULAR** (≥50% éxito): El servidor tuvo dificultades con la carga
- ❌ **MALO** (<50% éxito): El servidor no pudo manejar la carga adecuadamente

## Ejemplos de Salida

```
================================================================================
Iniciando prueba de rendimiento con 1000 usuarios
URL base: http://localhost:8000
Subscriber code: TEST123
================================================================================

Ejecutando usuarios...

================================================================================
RESULTADOS DE LA PRUEBA DE RENDIMIENTO
================================================================================

📊 RESUMEN GENERAL
  Total de usuarios simulados: 1000
  Usuarios exitosos: 950 (95.00%)
  Usuarios fallidos: 50 (5.00%)
  Tiempo total: 45.23 segundos
  Usuarios por segundo: 22.11

❌ ERRORES POR PASO DEL FLUJO
  validate_udid: 30 errores (3.00%)
  websocket_auth: 15 errores (1.50%)
  request_udid: 5 errores (0.50%)

🔍 ERRORES POR TIPO
  Rate limit (429): 25 ocurrencias
  Timeout esperando credenciales: 15 ocurrencias
  HTTP 404: 10 ocurrencias

📡 CÓDIGOS DE ESTADO HTTP
  200: 1950 requests (97.50%)
  201: 1000 requests (50.00%)
  429: 25 requests (1.25%)
  404: 10 requests (0.50%)

⏱️  TIEMPOS DE RESPUESTA (segundos)
  request_udid:
    Promedio: 0.125s
    Mediana: 0.120s
    P95: 0.250s
    P99: 0.350s
    Min: 0.080s
    Max: 0.450s

🚀 ANÁLISIS DE RENDIMIENTO DEL SERVIDOR
  Tasa de éxito: 95.00%
  ✅ EXCELENTE: El servidor manejó la carga muy bien

  🔍 Cuellos de botella identificados:
    - websocket_auth: 2.345s promedio
    - validate_udid: 0.450s promedio
    - request_udid: 0.125s promedio

================================================================================
```

## Recomendaciones

### Para Pruebas de Desarrollo

```bash
# Prueba pequeña para desarrollo
python test_rendimiento_completo.py \
    --host http://localhost:8000 \
    --users 10 \
    --subscriber-code TEST123 \
    --max-concurrent 5
```

### Para Pruebas de Producción

```bash
# Prueba completa de producción
python test_rendimiento_completo.py \
    --host https://api.produccion.com \
    --users 1000 \
    --subscriber-code PROD_TEST \
    --max-concurrent 50 \
    --timeout 60
```

### Para Identificar Cuellos de Botella

```bash
# Prueba con menos usuarios pero más detallada
python test_rendimiento_completo.py \
    --host http://localhost:8000 \
    --users 100 \
    --subscriber-code TEST123 \
    --max-concurrent 10 \
    --timeout 120
```

## Solución de Problemas

### Error: "WebSockets no disponible"

**Solución**: Instalar la dependencia:
```bash
pip install websockets
```

### Error: "aiohttp o httpx no disponible"

**Solución**: Instalar una de las dependencias:
```bash
pip install aiohttp
# o
pip install httpx
```

### Muchos errores 429 (Rate Limit)

El servidor está aplicando rate limiting. Considera:
- Reducir el número de usuarios concurrentes (`--max-concurrent`)
- Aumentar el tiempo entre solicitudes
- Verificar la configuración de rate limiting del servidor

### Timeouts frecuentes

- Aumentar el timeout (`--timeout`)
- Verificar la carga del servidor
- Verificar la conectividad de red

## Notas Importantes

1. **Subscriber Code**: Asegúrate de que el subscriber_code usado en las pruebas tenga datos válidos en la base de datos.

2. **Rate Limiting**: El servidor tiene rate limiting activo. Si simulas muchos usuarios, algunos pueden ser rechazados por rate limiting, lo cual es esperado.

3. **WebSocket**: El script requiere que el servidor tenga WebSockets habilitados y accesibles en la ruta `/ws/auth/`.

4. **Concurrencia**: Ajusta `--max-concurrent` según la capacidad de tu máquina y el servidor. Valores muy altos pueden saturar tu conexión de red.

5. **Producción**: No ejecutes pruebas de carga intensivas en servidores de producción sin autorización, ya que pueden afectar el rendimiento para usuarios reales.

## Integración con CI/CD

Puedes integrar este script en tu pipeline de CI/CD:

```yaml
# Ejemplo para GitHub Actions
- name: Prueba de rendimiento
  run: |
    pip install aiohttp websockets
    python test_rendimiento_completo.py \
      --host http://localhost:8000 \
      --users 100 \
      --subscriber-code TEST123
```

## Soporte

Si encuentras problemas o tienes preguntas, revisa:
- Los logs del servidor para errores específicos
- La configuración de rate limiting
- La conectividad de red
- Los recursos del servidor (CPU, RAM, conexiones)
