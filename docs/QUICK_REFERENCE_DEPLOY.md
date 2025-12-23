# 📋 Referencia Rápida de Despliegue - UDID Server

---

## 🚀 Comandos de Instalación Rápida (Ubuntu 22.04/24.04)

### 1. Actualizar Sistema
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Instalar Dependencias
```bash
sudo apt install -y python3 python3-pip python3-venv python3-dev \
    postgresql postgresql-contrib redis-server nginx \
    build-essential libpq-dev git curl
```

### 3. Configurar PostgreSQL
```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE udid;
CREATE USER udid_user WITH PASSWORD 'tu_password';
GRANT ALL PRIVILEGES ON DATABASE udid TO udid_user;
\c udid
GRANT ALL ON SCHEMA public TO udid_user;
\q
```

### 4. Configurar Redis
```bash
sudo nano /etc/redis/redis.conf
# Cambiar: supervised systemd
# Agregar: maxmemory 2gb
# Agregar: maxmemory-policy allkeys-lru
sudo systemctl restart redis-server
```

### 5. Configurar Proyecto
```bash
sudo mkdir -p /opt/udid
sudo chown -R $USER:$USER /opt/udid
cd /opt/udid

# Copiar código del proyecto aquí

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 6. Configurar Variables de Entorno
```bash
cp docs/env.example.txt .env
nano .env  # Editar con valores reales
chmod 600 .env
```

### 7. Configurar Base de Datos para PostgreSQL
```bash
# Editar ubuntu/settings.py y cambiar DATABASES a PostgreSQL
```

### 8. Migraciones
```bash
source venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 9. Configurar Nginx
```bash
sudo nano /etc/nginx/sites-available/udid
# Copiar configuración de la guía completa
sudo ln -s /etc/nginx/sites-available/udid /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
```

### 10. Configurar SSL
```bash
# Certificado autofirmado:
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/udid.key \
    -out /etc/nginx/ssl/udid.crt
```

### 11. Configurar Systemd
```bash
sudo nano /etc/systemd/system/udid@.service
# Copiar configuración de la guía completa
sudo systemctl daemon-reload
```

### 12. Configurar Celery (Tareas Automáticas)
```bash
# Crear servicios systemd para Celery
sudo nano /etc/systemd/system/celery-worker.service
sudo nano /etc/systemd/system/celery-beat.service
# Copiar configuración de la guía completa

# Crear directorios necesarios
sudo mkdir -p /var/run/udid /var/log/udid
sudo chown udid:udid /var/run/udid /var/log/udid

# Recargar systemd
sudo systemctl daemon-reload
```

### 13. Iniciar Todo
```bash
sudo systemctl enable postgresql redis-server nginx celery-worker celery-beat
sudo systemctl start postgresql redis-server nginx celery-worker celery-beat
for i in 0 1 2 3; do sudo systemctl enable udid@$i && sudo systemctl start udid@$i; done
```

---

## 🔧 Comandos de Gestión

| Acción              | Comando                              |
|---------------------|--------------------------------------|
| **Iniciar app**     | `sudo systemctl start udid@{0..3}`   |
| **Detener app**     | `sudo systemctl stop udid@{0..3}`    |
| **Reiniciar app**   | `sudo systemctl restart udid@{0..3}` |
| **Ver estado**      | `sudo systemctl status udid@0`       |
| **Ver logs**        | `sudo journalctl -u udid@0 -f`       |
| **Reiniciar Nginx** | `sudo systemctl restart nginx`       |
| **Reiniciar Celery**| `sudo systemctl restart celery-worker celery-beat` |
| **Ver Celery**      | `sudo journalctl -u celery-worker -f` |
| **Ver puertos**     | `sudo ss -tlnp \| grep 800`          |

---

## 📁 Estructura de Directorios

```
/opt/udid/              # Directorio del proyecto
├── .env                # Variables de entorno (NO commitear)
├── venv/               # Entorno virtual Python
├── manage.py           # Django management
├── ubuntu/             # Configuración Django
│   └── settings.py
├── udid/               # Aplicación principal
├── staticfiles/        # Archivos estáticos recolectados
└── docs/               # Documentación

/etc/nginx/sites-available/udid    # Config Nginx
/etc/systemd/system/udid@.service  # Config Systemd
/var/log/udid/                     # Logs de la aplicación
/var/log/nginx/udid_*.log          # Logs de Nginx
```

---

## 🔍 Verificación Rápida

```bash
# Verificar servicios
sudo systemctl status postgresql redis-server nginx udid@0 celery-worker celery-beat

# Verificar puertos
sudo ss -tlnp | grep -E '(80|443|8000|5432|6379|5555)'

# Probar API
curl -k https://localhost/health

# Probar Redis
redis-cli ping

# Probar PostgreSQL
psql -h localhost -U udid_user -d udid -c "SELECT 1;"
```

---

## 🆘 Solución Rápida de Problemas

| Problema                  | Solución                                                      |
|---------------------------|---------------------------------------------------------------|
| **502 Bad Gateway**       | `sudo systemctl restart udid@{0..3}`                          |
| **PostgreSQL no conecta** | `sudo systemctl restart postgresql`                           |
| **Redis no conecta**      | `sudo systemctl restart redis-server`                         |
| **Celery no ejecuta**     | `sudo systemctl restart celery-worker celery-beat`            |
| **Permisos**              | `sudo chown -R udid:udid /opt/udid`                           |
| **ModuleNotFound**        | `source venv/bin/activate && pip install -r requirements.txt` |

---

## 📊 Recursos Recomendados

| Carga            | CPU       | RAM      | Workers |
|------------------|-----      |-----     |---------|
| Baja (<500 conn) | 2-4 cores | 4-8 GB   | 4       |
| Media (500-2000) | 4-8 cores | 8-16 GB  | 8       |
| Alta (2000+)     | 8-16 cores| 16-32 GB | 16+     |

---

## 📝 Variables de Entorno Críticas

```env
SECRET_KEY=            # Clave secreta Django (generar nueva)
DEBUG=False            # SIEMPRE False en producción
ALLOWED_HOSTS=         # IP/dominios permitidos
POSTGRES_PASSWORD=     # Password de PostgreSQL
REDIS_URL=             # redis://localhost:6379/0
url_panaccess=         # URL API Panaccess
username=              # Usuario Panaccess
password=              # Password Panaccess
api_token=             # Token API Panaccess
salt=                  # Salt Panaccess
ENCRYPTION_KEY=        # 32 caracteres para AES-256
CELERY_BROKER_URL=     # redis://localhost:6379/0
CELERY_RESULT_BACKEND= # redis://localhost:6379/1
CELERY_TIMEZONE=       # UTC
CELERY_FLOWER_PORT=    # 5555 (opcional)
CELERY_FLOWER_BASIC_AUTH= # admin:admin (opcional)
```

---

## ⏰ Tareas Automáticas con Celery

### Configurar Celery

```bash
# Los servicios de Celery ya están configurados en el paso 12
# Verificar que están activos:
sudo systemctl status celery-worker celery-beat
```

### Tareas Automáticas del Sistema

| Tarea | Frecuencia | Propósito |
|-------|------------|-----------|
| `download_new_subscribers` | Cada 5 min | Descarga solo suscriptores nuevos |
| `update_all_subscribers` | Cada 5 min | Actualiza datos de suscriptores existentes |
| `update_smartcards_from_subscribers` | Cada 5 min | Actualiza asociaciones de smartcards |
| `validate_and_fix_all_data` | Diaria 2:00 AM | Sincronización completa y validación |

### Verificar Ejecución

```bash
# Ver logs de Celery Worker
sudo journalctl -u celery-worker -f
tail -f /var/log/udid/celery-worker.log

# Ver logs de Celery Beat
sudo journalctl -u celery-beat -f
tail -f /var/log/udid/celery-beat.log

# Ver tareas activas
cd /opt/udid && source venv/bin/activate
celery -A ubuntu inspect active

# Ver estadísticas
celery -A ubuntu inspect stats

# Acceder a Flower (si está configurado)
# http://IP_DEL_SERVIDOR:5555
```

---

## 🔄 Actualizar Proyecto

```bash
# 1. Detener servicios
sudo systemctl stop udid@{0..3}

# 2. Actualizar código
cd /opt/udid
git pull  # o copiar archivos nuevos

# 3. Actualizar dependencias y migraciones
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput

# 4. Reiniciar servicios
sudo systemctl start udid@{0..3}
sudo systemctl restart celery-worker celery-beat
sudo systemctl restart nginx
```



