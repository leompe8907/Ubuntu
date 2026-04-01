# Auditoría técnica: seguridad, rendimiento y robustez (UDID)

Documento generado a partir del análisis de código y de las **correcciones aplicadas** en el repositorio. Resume hallazgos, severidad y qué se hizo para mitigarlos.

---

## Resumen ejecutivo

| Área | Riesgo principal | Acción |
|------|------------------|--------|
| Modelo `SubscriberInfo` | `__str__` referenciaba `self.data` inexistente → posibles **500** | Corregido a representación estable |
| `GetSubscriberInfoView` | `select_for_update()` fuera de transacción → **500** / lock inválido | Flujo crítico movido a método con `@transaction.atomic` |
| `ValidateUDIDView` | Clase **duplicada** en `automatico.py` (la segunda pisa a la primera) | Eliminada la duplicada; lógica de limpieza fusionada en la vista principal |
| Middleware backpressure | `get_metrics()` completo en cada request (psutil bloqueante + Redis SCAN) | Nuevo `get_metrics_for_degradation()` con CPU no bloqueante y **caché TTL** de métricas costosas |
| PanAccess login | `timeout=None` → workers colgados | Timeout finito (90s) en `getSubscriberLoginInfo` |
| UDID manual único | Bucle `while True` + `.exists()` | Reintentos con `IntegrityError` acotados |
| API keys | Búsqueda solo por texto plano en BD | Campo `key_hash` + búsqueda por hash; compatibilidad con filas legacy |
| Middleware API key | Fail-open ante error de BD | **Fail-closed** con **503** y `Retry-After` |
| Logs / debug | `print()` en producción | Sustituido por `logger.debug` |
| Respuestas internas | Detalle de excepción al cliente en `services.py` | Mensaje genérico; detalle solo en logs |

### Limitaciones documentadas (no resueltas solo en código)

- **Credenciales de `SubscriberInfo`**: siguen siendo recuperables vía cifrado simétrico (`encrypt_value`/`decrypt_value`) porque el flujo de negocio exige entregar secretos al cliente cifrados. Mitigar en profundidad implica KMS/HSM, rotación y posible cambio de modelo de negocio (hash irreversible donde aplique).
- **`.env` / `settings`**: `ALLOWED_HOSTS=*` y flags de cookies/HTTPS deben ajustarse **por entorno** en despliegue; no se sobrescriben aquí para no romper entornos locales.

---

## 1. Robustez y mantenibilidad

### `SubscriberInfo.__str__` roto

- **Problema:** `return self.data` → `AttributeError` en cualquier uso implícito de string del modelo.
- **Solución:** `__str__` devuelve `subscriber_code` y `sn` de forma segura.

### Duplicado `ValidateUDIDView`

- **Problema:** Dos clases con el mismo nombre; Python conserva la última definición → confusión y código muerto.
- **Solución:** Una sola vista; se incorporó la limpieza de UDIDs expirados por `subscriber_code` antes de validar (comportamiento de la segunda definición).

### `services.authenticate_with_udid_service`

- **Problema:** Respuesta `internal_error` incluía `details: str(exc)` al cliente.
- **Solución:** Respuesta genérica; el error real se asume registrado por el caller / logging.

---

## 2. Rendimiento

### Middleware de degradación

- **Problema:** `get_metrics()` ejecutaba `_get_system_metrics()` con `cpu_percent(interval=0.1)` (bloqueo ~100 ms), `redis.ping()` y `SCAN` en cada request de API.
- **Solución:** `get_metrics_for_degradation()`:
  - Usa `psutil.cpu_percent(interval=None)` (no bloqueante tras primer muestreo).
  - **No** ejecuta ping Redis ni métricas WS en el camino del middleware.
  - Cachea **concurrencia por semáforo** (SCAN) con TTL ~3 s.

### PanAccess `getSubscriberLoginInfo`

- **Problema:** `timeout=None` podía bloquear workers indefinidamente.
- **Solución:** `timeout=90` (ajustable vía constante en el módulo si hace falta).

### Generación UDID manual (`RequestUDIDManualView.generate_unique_udid`)

- **Problema:** Bucle potencialmente costoso bajo colisiones.
- **Solución:** Hasta 8 intentos con `create`; captura `IntegrityError` y reintenta.

---

## 3. Errores y 500

### `select_for_update` sin transacción

- **Problema:** En `GetSubscriberInfoView`, lock sin `atomic` → error de transacción en Django.
- **Solución:** Método `_execute_get_subscriber_info` decorado con `@transaction.atomic`, con un solo `select_for_update().get()`.

### Actualización masiva de expirados en GET

- **Problema:** `update()` global de filas expiradas en cada petición → carga innecesaria.
- **Solución:** Eliminada esa actualización masiva en este endpoint (la expiración puntual del `req` sigue igual).

---

## 4. Seguridad

### API keys

- **Problema:** Lookup solo por columna `key` en texto plano.
- **Solución:** Campo `key_hash` (SHA-256 del secreto vía `hash_api_key`), rellenado en `save()`, búsqueda preferente por `key_hash`, fallback por `key` para datos antiguos. Migración con **backfill** de hashes.

### `APIKeyAuthMiddleware`

- **Problema:** Ante fallo de BD/Redis, se ignoraba la API key (fail-open).
- **Solución:** Respuesta **503** con cuerpo JSON y `Retry-After: 30` cuando hay excepción tras enviar `X-API-Key`.

### `LoginView` / `except:`

- **Problema:** `except:` demasiado amplio.
- **Solución:** `except UserProfile.DoesNotExist` y `except AttributeError`.

---

## Archivos tocados (referencia)

- `udid/models.py` — `SubscriberInfo.__str__`, `APIKey.key_hash`, `save`, `find_by_key`, índices.
- `udid/migrations/0002_apikey_key_hash.py` — campo + datos.
- `udid/utils/server/metrics.py` — `get_metrics_for_degradation`.
- `udid/middleware.py` — degradación + API key fail-closed.
- `udid/views.py` — `generate_unique_udid`.
- `udid/auth.py` — excepciones en login.
- `udid/services.py` — error interno sin filtrar detalles.
- `udid/utils/panaccess/login.py` — timeout finito.
- `udid/automatico.py` — `ValidateUDIDView`, `GetSubscriberInfoView`, prints, `except` específico.

---

## Próximos pasos recomendados (fuera de este PR)

1. Endurecer `ALLOWED_HOSTS`, `CSRF_*` y cookies seguras en producción.
2. Valorar eliminar almacenamiento reversible de contraseñas de suscriptor o aislarlo en un vault.
3. Rotación de API keys: dejar de persistir `key` en claro cuando todas las filas tengan `key_hash` y un proceso de migración de clientes.
