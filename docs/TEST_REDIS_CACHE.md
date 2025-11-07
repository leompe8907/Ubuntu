# Pruebas: Migración de Cache a Redis

## Tarea 1.1: Migrar Cache a Redis Distribuido

### Instalación

1. Instalar dependencia:
```bash
pip install django-redis==5.4.0
```

O instalar desde requirements.txt:
```bash
pip install -r requirements.txt
```

### Configuración

1. Asegúrate de que la variable de entorno `REDIS_URL` esté configurada:
```bash
export REDIS_URL="redis://localhost:6379/0"
# O para Redis con SSL:
export REDIS_URL="rediss://username:password@host:port/0"
```

2. Verificar que Django detecta la configuración:
```python
python manage.py shell
>>> from django.core.cache import cache
>>> cache.set('test_key', 'test_value', 60)
>>> cache.get('test_key')
'test_value'
```

### Pruebas de Funcionamiento

#### Prueba 1: Verificar que Redis está funcionando

```python
# En Django shell
from django.core.cache import cache

# Test básico
cache.set('test_key', 'test_value', 60)
value = cache.get('test_key')
assert value == 'test_value', "Cache no funciona correctamente"

# Test con prefijo
cache.set('udid_cache:test', 'value', 60)
assert cache.get('udid_cache:test') == 'value', "Prefijo no funciona"
```

#### Prueba 2: Verificar rate limiting entre instancias

Si tienes múltiples workers/instancias del servidor:

1. En instancia 1:
```python
from django.core.cache import cache
cache.set('rate_limit:udid:test123', 5, 300)
```

2. En instancia 2:
```python
from django.core.cache import cache
value = cache.get('rate_limit:udid:test123')
assert value == 5, "Cache no se comparte entre instancias"
```

#### Prueba 3: Verificar funciones de rate limiting

```python
from udid.util import check_udid_rate_limit, increment_rate_limit_counter

# Primera llamada
is_allowed, remaining, retry_after = check_udid_rate_limit('test_udid_123', max_requests=5, window_minutes=5)
assert is_allowed == True, "Primera llamada debe ser permitida"

# Incrementar contador
increment_rate_limit_counter('udid', 'test_udid_123')

# Segunda llamada
is_allowed, remaining, retry_after = check_udid_rate_limit('test_udid_123', max_requests=5, window_minutes=5)
assert remaining == 3, f"Debería quedar 3 requests, obtuvo {remaining}"
```

#### Prueba 4: Verificar fallback cuando Redis falla

Si Redis no está disponible, el sistema debería continuar funcionando (con IGNORE_EXCEPTIONS=True):

1. Detener Redis temporalmente
2. Verificar que el sistema no crashea
3. Las funciones deberían fallar silenciosamente y usar BD como fallback

### Verificación en Producción

1. **Monitorear Redis:**
```bash
redis-cli
> KEYS udid_cache:*
> INFO stats
```

2. **Verificar rendimiento:**
- El cache debería responder en < 10ms
- Monitorear uso de memoria en Redis
- Verificar que no hay errores de conexión

3. **Logs:**
- Verificar que no hay errores de conexión a Redis
- Monitorear tiempo de respuesta de cache

### Criterios de Aceptación

- [ ] Cache funciona correctamente con Redis
- [ ] Rate limiting funciona entre múltiples instancias
- [ ] Fallback funciona si Redis no está disponible
- [ ] No hay errores en logs
- [ ] Rendimiento es aceptable (< 10ms por operación de cache)

### Troubleshooting

**Problema: Cache no funciona**
- Verificar que REDIS_URL está configurado
- Verificar que Redis está corriendo
- Verificar conexión de red

**Problema: Rate limiting no funciona entre instancias**
- Verificar que todas las instancias usan el mismo REDIS_URL
- Verificar que no hay cache local en uso

**Problema: Errores de conexión**
- Verificar timeout en settings (5 segundos)
- Verificar que IGNORE_EXCEPTIONS=True está configurado
- Verificar logs de Redis

