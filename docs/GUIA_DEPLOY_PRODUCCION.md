# Guía de Deploy a Producción - Protección DDoS

## 📋 RESUMEN: Qué Cambios Subir a Producción

### ✅ CAMBIOS PARA PRODUCCIÓN (SÍ SUBIR)

Estos cambios mejoran la seguridad y robustez del sistema y **DEBEN subirse a producción**:

#### 1. **Protecciones DDoS** ✅
- ✅ Rate limiting multi-capa (device fingerprint, UDID, temp token)
- ✅ Rate limiting adaptativo (ajusta según carga)
- ✅ Circuit breaker (protección automática)
- ✅ Exponential backoff con jitter
- ✅ Rate limiting en WebSockets
- ✅ Device fingerprinting mejorado (móviles/Smart TVs)

#### 2. **Infraestructura** ✅
- ✅ Cache Redis distribuido (ya configurado)
- ✅ Logging y monitoreo detallado
- ✅ Optimización de consultas (índices en BD)
- ✅ Migración a MariaDB/PostgreSQL

#### 3. **Código de Protección** ✅
- ✅ `udid/util.py` - Funciones de rate limiting
- ✅ `udid/middleware.py` - Monitoreo de carga
- ✅ `udid/views.py` - Integración de protecciones
- ✅ `udid/automatico.py` - Protecciones en endpoints automáticos
- ✅ `udid/auth.py` - Protecciones en autenticación
- ✅ `udid/consumers.py` - Protecciones en WebSockets

---

### ❌ CAMBIOS SOLO PARA PRUEBAS (NO SUBIR)

Estos cambios son **SOLO para pruebas de carga** y **NO deben subirse a producción**:

#### 1. **Configuración de Límites Aumentados** ❌
```python
# ubuntu/settings.py - LÍNEAS 72-73
UDID_EXPIRATION_MINUTES = int(os.getenv("UDID_EXPIRATION_MINUTES", "15"))  # ✅ OK (default 15)
UDID_MAX_ATTEMPTS = int(os.getenv("UDID_MAX_ATTEMPTS", "5"))  # ✅ OK (default 5)
```

**⚠️ IMPORTANTE**: Las variables de entorno `UDID_EXPIRATION_MINUTES=60` y `UDID_MAX_ATTEMPTS=10` son **SOLO para pruebas**. En producción deben usar los valores por defecto (15 minutos y 5 intentos).

#### 2. **Scripts de Prueba** ❌
- ❌ `test_carga_avanzado.py` - Solo para pruebas
- ❌ `test_carga_usuarios.py` - Solo para pruebas
- ❌ `test_sistema.py` - Solo para pruebas
- ❌ `desasociar_todos_udids.py` - Solo para mantenimiento/pruebas

#### 3. **Documentación de Análisis** ❌
- ❌ `ANALISIS_ERRORES_TEST_CARGA.md` - Solo para análisis
- ❌ `ANALISIS_SQLITE_VS_POSTGRESQL.md` - Solo para análisis
- ❌ `RESUMEN_PROTECCION_DDOS.md` - Opcional (documentación)

---

## 🔧 CONFIGURACIÓN PARA PRODUCCIÓN

### Variables de Entorno en Producción

**✅ CORRECTO para Producción:**
```bash
# NO establecer estas variables (usarán defaults seguros)
# UDID_EXPIRATION_MINUTES=15  # Default, no establecer
# UDID_MAX_ATTEMPTS=5          # Default, no establecer

# SÍ establecer estas (si no están ya):
REDIS_URL=redis://tu-redis-url
```

**❌ INCORRECTO para Producción:**
```bash
# NO usar estos valores en producción:
UDID_EXPIRATION_MINUTES=60  # ❌ Solo para pruebas
UDID_MAX_ATTEMPTS=10        # ❌ Solo para pruebas
```

### Valores por Defecto (Seguros para Producción)

El código ya tiene valores por defecto seguros:

```python
# udid/models.py - Línea 393
expiration_minutes = getattr(settings, 'UDID_EXPIRATION_MINUTES', 15)  # ✅ 15 min default

# udid/models.py - Línea 425
max_attempts = getattr(settings, 'UDID_MAX_ATTEMPTS', 5)  # ✅ 5 intentos default
```

**✅ Estos valores por defecto son SEGUROS para producción.**

---

## 📝 CHECKLIST DE DEPLOY

### Antes de Subir a Producción:

- [ ] **Verificar que NO hay variables de entorno de prueba:**
  ```bash
  # Asegurarse de que NO están establecidas:
  # UDID_EXPIRATION_MINUTES=60
  # UDID_MAX_ATTEMPTS=10
  ```

- [ ] **Verificar configuración de Redis:**
  ```bash
  # Asegurarse de que está configurado:
  REDIS_URL=redis://tu-redis-url
  ```

- [ ] **Verificar base de datos:**
  - [ ] MariaDB/PostgreSQL configurado (NO SQLite3)
  - [ ] Migraciones aplicadas
  - [ ] Índices creados

- [ ] **Verificar archivos a subir:**
  - [x] `udid/util.py` - ✅ Subir
  - [x] `udid/middleware.py` - ✅ Subir
  - [x] `udid/views.py` - ✅ Subir
  - [x] `udid/automatico.py` - ✅ Subir
  - [x] `udid/auth.py` - ✅ Subir
  - [x] `udid/consumers.py` - ✅ Subir
  - [x] `udid/models.py` - ✅ Subir (con valores por defecto)
  - [x] `ubuntu/settings.py` - ✅ Subir (con valores por defecto)
  - [ ] `test_*.py` - ❌ NO subir
  - [ ] `desasociar_todos_udids.py` - ❌ NO subir

- [ ] **Verificar que los valores por defecto son seguros:**
  - [x] `UDID_EXPIRATION_MINUTES` default = 15 minutos ✅
  - [x] `UDID_MAX_ATTEMPTS` default = 5 intentos ✅

---

## 🚀 PASOS PARA DEPLOY

### 1. Preparar Código

```bash
# Asegurarse de que NO hay variables de prueba en .env o configuración
# Verificar que los defaults en el código son seguros (15 min, 5 intentos)
```

### 2. Subir Código

```bash
# Subir todos los archivos modificados EXCEPTO:
# - test_*.py
# - desasociar_todos_udids.py
# - ANALISIS_*.md (opcional, solo documentación)
```

### 3. Configurar Variables de Entorno en Producción

```bash
# En el servidor de producción, asegurarse de que:
# - REDIS_URL está configurado
# - NO hay UDID_EXPIRATION_MINUTES=60
# - NO hay UDID_MAX_ATTEMPTS=10
```

### 4. Aplicar Migraciones

```bash
python manage.py migrate
```

### 5. Verificar Funcionamiento

```bash
# Verificar logs para asegurar que:
# - Redis está conectado
# - Rate limiting funciona
# - No hay errores
```

---

## ⚠️ ADVERTENCIAS IMPORTANTES

### 1. **NO Subir Límites Aumentados a Producción**

Los valores `UDID_EXPIRATION_MINUTES=60` y `UDID_MAX_ATTEMPTS=10` son **SOLO para pruebas de carga**. En producción deben usar los valores por defecto (15 minutos y 5 intentos) para mantener la seguridad.

### 2. **Verificar Redis en Producción**

Asegurarse de que Redis está configurado y funcionando en producción. Sin Redis, el sistema usará LocMemCache (local) que no funciona en entornos multi-instancia.

### 3. **Base de Datos**

**NO usar SQLite3 en producción**. Usar MariaDB o PostgreSQL para evitar bloqueos de BD.

---

## ✅ CONCLUSIÓN

### Cambios que SÍ van a Producción:
- ✅ **Todas las protecciones DDoS** (rate limiting, circuit breaker, etc.)
- ✅ **Código de seguridad** (util.py, middleware.py, views.py, etc.)
- ✅ **Configuración con valores por defecto seguros** (15 min, 5 intentos)

### Cambios que NO van a Producción:
- ❌ **Variables de entorno de prueba** (60 min, 10 intentos)
- ❌ **Scripts de prueba** (test_*.py)
- ❌ **Scripts de mantenimiento** (desasociar_todos_udids.py)

### Valores Seguros para Producción:
- ✅ `UDID_EXPIRATION_MINUTES = 15` (default)
- ✅ `UDID_MAX_ATTEMPTS = 5` (default)
- ✅ Redis configurado
- ✅ MariaDB/PostgreSQL (no SQLite3)

---

**El código está listo para producción con valores seguros por defecto. Solo asegúrate de NO establecer las variables de entorno de prueba en el servidor de producción.**

