# 🚀 Guía Completa de Despliegue - UDID Server en Ubuntu Server

## 📋 Índice

1. [Introducción y Requisitos](#1-introducción-y-requisitos)
2. [Preparación del Servidor](#2-preparación-del-servidor)
3. [Instalación de Dependencias del Sistema](#3-instalación-de-dependencias-del-sistema)
4. [Instalación y Configuración de PostgreSQL](#4-instalación-y-configuración-de-postgresql)
5. [Instalación y Configuración de Redis](#5-instalación-y-configuración-de-redis)
6. [Configuración del Proyecto Python](#6-configuración-del-proyecto-python)
7. [Configuración de Variables de Entorno](#7-configuración-de-variables-de-entorno)
8. [Migraciones y Configuración Inicial](#8-migraciones-y-configuración-inicial)
9. [Configuración de Nginx](#9-configuración-de-nginx)
10. [Configuración de SSL/HTTPS](#10-configuración-de-sslhttps)
11. [Configuración de Systemd](#11-configuración-de-systemd)
12. [Configuración de Celery (Ejecución Manual de Tareas)](#12-configuración-de-celery-ejecución-manual-de-tareas)
13. [Verificación y Pruebas](#13-verificación-y-pruebas)
14. [Mantenimiento y Monitoreo](#14-mantenimiento-y-monitoreo)
15. [Solución de Problemas](#15-solución-de-problemas)
16. [Recomendaciones de Recursos del Servidor](#16-recomendaciones-de-recursos-del-servidor)

---

## 1. Introducción y Requisitos

### 🎯 ¿Qué es este proyecto?

Este es un servidor Django/Channels que proporciona:
- **API REST** para gestión de UDID (Unique Device Identifier)
- **WebSockets** para comunicación en tiempo real
- **Sincronización** con sistema externo Panaccess
- **Autenticación JWT** para seguridad
- **Rate limiting** y protección DDoS

### 📦 Componentes del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        INTERNET                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    NGINX (Puerto 443/80)                        │
│              - SSL/TLS Termination                              │
│              - Proxy Inverso                                    │
│              - Balanceo de Carga                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              DAPHNE (Puertos 8000-8003)                         │
│         Servidor ASGI - HTTP + WebSockets                       │
│              (Múltiples instancias)                             │
└─────────────────────────────────────────────────────────────────┘
                    │                   │
                    ▼                   ▼
┌───────────────────────┐   ┌───────────────────────┐
│     POSTGRESQL        │   │        REDIS          │
│   (Puerto 5432)       │   │    (Puerto 6379)      │
│   Base de Datos       │   │   Cache + WebSockets  │
└───────────────────────┘   └───────────────────────┘
```

### ✅ Requisitos Mínimos del Servidor

| Componente  | Mínimo           | Recomendado            | Alto Rendimiento       |
|-------------|------------------|------------------------|------------------------|
| **CPU**     | 2 cores          | 4 cores                | 8+ cores               |
| **RAM**     | 4 GB             | 8 GB                   | 16+ GB                 |
| **Disco**   | 20 GB SSD        | 50 GB SSD              | 100+ GB NVMe           |
| **Sistema** | Ubuntu 22.04 LTS | Ubuntu 22.04/24.04 LTS | Ubuntu 22.04/24.04 LTS |

### 📊 Cálculo de Workers y Recursos

**Fórmula para workers Daphne:**
```
Workers = (2 × CPU cores) + 1
```

| CPU Cores | Workers | RAM Recomendada | Conexiones Simultáneas |
|-----------|---------|-----------------|------------------------|
| 2         | 5       | 4 GB            | ~500                   |
| 4         | 9       | 8 GB            | ~1,000                 |
| 8         | 17      | 16 GB           | ~2,500                 |
| 16        | 33      | 32 GB           | ~5,000+                |

---

## 2. Preparación del Servidor

### 2.1 Configurar SSH en el Servidor

> **⚠️ IMPORTANTE:** Esta sección es para cuando tienes acceso físico o por consola al servidor (VPS, servidor dedicado, máquina virtual). Si estás usando un servicio en la nube (AWS, DigitalOcean, etc.), SSH generalmente ya viene configurado.

#### ¿Necesitas configurar SSH?

Si puedes conectarte físicamente al servidor o tienes acceso por consola (KVM, VNC, etc.), sigue estos pasos para habilitar SSH.

#### Paso 1: Verificar si SSH está Instalado

```bash
# Verificar si el servicio SSH está instalado y corriendo
sudo systemctl status ssh

# O en algunas versiones de Ubuntu:
sudo systemctl status sshd
```

**Si ves "active (running)"**: SSH ya está funcionando, puedes saltar al Paso 4.

**Si ves "Unit ssh.service could not be found"**: Necesitas instalar SSH.

#### Paso 2: Instalar OpenSSH Server

```bash
# Actualizar lista de paquetes
sudo apt update

# Instalar OpenSSH Server
sudo apt install -y openssh-server

# Verificar instalación
sudo systemctl status ssh
```

#### Paso 3: Configurar SSH (Opcional pero Recomendado)

```bash
# Editar configuración de SSH
sudo nano /etc/ssh/sshd_config
```

**Configuraciones recomendadas para producción:**

```conf
# Permitir autenticación por contraseña (cambiar a 'no' si solo usas claves SSH)
PasswordAuthentication yes

# Permitir autenticación por clave pública (recomendado)
PubkeyAuthentication yes

# Deshabilitar login como root directamente (más seguro)
# Cambiar 'yes' a 'no' si quieres forzar login con usuario normal
PermitRootLogin yes

# Puerto SSH (por defecto 22, cambiar si quieres más seguridad)
Port 22

# Tiempo de inactividad antes de desconectar (segundos)
ClientAliveInterval 300
ClientAliveCountMax 2

# Máximo de intentos de login
MaxAuthTries 3

# Deshabilitar protocolos antiguos e inseguros
Protocol 2
```

**Guardar cambios:** `Ctrl + X`, luego `Y`, luego `Enter`

#### Paso 4: Habilitar e Iniciar el Servicio SSH

```bash
# Habilitar SSH para que inicie automáticamente al arrancar
sudo systemctl enable ssh

# Iniciar el servicio SSH
sudo systemctl start ssh

# Verificar que está corriendo
sudo systemctl status ssh
```

**Deberías ver:**
```
● ssh.service - OpenBSD Secure Shell server
     Loaded: loaded (/lib/systemd/system/ssh.service; enabled; vendor preset: enabled)
     Active: active (running) since ...
```

#### Paso 5: Configurar Firewall (UFW)

Si tienes un firewall activo, necesitas permitir el tráfico SSH:

```bash
# Verificar si UFW está activo
sudo ufw status

# Si está inactivo, puedes activarlo (opcional)
# sudo ufw enable

# Permitir conexiones SSH (IMPORTANTE: hacer esto ANTES de activar el firewall)
sudo ufw allow ssh
# O específicamente el puerto 22:
sudo ufw allow 22/tcp

# Si cambiaste el puerto SSH (ejemplo: 2222), permitir ese puerto:
# sudo ufw allow 2222/tcp

# Verificar reglas
sudo ufw status numbered
```

**⚠️ ADVERTENCIA CRÍTICA:**
- **NUNCA** actives el firewall sin permitir SSH primero
- Si bloqueas SSH sin tener acceso físico, perderás acceso al servidor
- Si ya activaste el firewall y perdiste acceso, necesitarás acceso físico/consola

#### Paso 6: Verificar que SSH Funciona

**Desde el mismo servidor:**

```bash
# Verificar que el servicio está escuchando en el puerto 22
sudo ss -tlnp | grep :22

# Deberías ver algo como:
# LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1234,fd=3))
```

**Obtener la IP del servidor:**

```bash
# Ver IP del servidor
ip addr show
# O más simple:
hostname -I

# Verificar conectividad
ping -c 3 8.8.8.8
```

#### Paso 7: Probar Conexión SSH (Desde Otra Máquina)

**Desde tu computadora local:**

```bash
# Intentar conectar
ssh usuario@IP_DEL_SERVIDOR

# Ejemplo:
ssh root@192.168.1.100
```

**Si funciona correctamente:**
- Te pedirá la contraseña del usuario
- Después de ingresarla, deberías ver el prompt del servidor

#### Solución de Problemas

**SSH no inicia:**

```bash
# Ver logs de errores
sudo journalctl -u ssh -n 50

# Verificar configuración
sudo sshd -t

# Reiniciar servicio
sudo systemctl restart ssh
```

**No puedes conectarte desde fuera:**

1. **Verificar firewall:**
   ```bash
   sudo ufw status
   sudo iptables -L -n  # Ver reglas de iptables
   ```

2. **Verificar que SSH está escuchando:**
   ```bash
   sudo ss -tlnp | grep :22
   ```

3. **Verificar red/router:**
   - Si es servidor local: verificar que el router permite conexiones SSH
   - Si es VPS: verificar reglas de firewall del proveedor (AWS Security Groups, etc.)

4. **Verificar que el puerto está abierto:**
   ```bash
   # Desde otra máquina en la misma red
   telnet IP_DEL_SERVIDOR 22
   # O:
   nc -zv IP_DEL_SERVIDOR 22
   ```

**Error "Connection refused":**
- SSH no está corriendo o el puerto está bloqueado
- Verificar: `sudo systemctl status ssh`

**Error "Permission denied":**
- Usuario o contraseña incorrectos
- Verificar que el usuario existe: `getent passwd usuario`

#### Seguridad Adicional (Opcional pero Recomendado)

**Cambiar puerto SSH (más seguridad):**

```bash
# Editar configuración
sudo nano /etc/ssh/sshd_config

# Cambiar:
Port 2222  # Usar un puerto diferente (ejemplo: 2222)

# Reiniciar SSH
sudo systemctl restart ssh

# Permitir nuevo puerto en firewall
sudo ufw allow 2222/tcp
```

**Deshabilitar login root (más seguro):**

```bash
# Crear usuario normal con sudo
sudo adduser nuevo_usuario
sudo usermod -aG sudo nuevo_usuario

# Editar SSH
sudo nano /etc/ssh/sshd_config
# Cambiar: PermitRootLogin no

# Reiniciar SSH
sudo systemctl restart ssh
```

---

### 2.2 Conectarse al Servidor por SSH

#### ¿Qué es SSH?

**SSH (Secure Shell)** es un protocolo que te permite conectarte de forma segura a un servidor remoto desde tu computadora local. Es la forma estándar de administrar servidores Linux/Ubuntu.

#### Requisitos Previos

Antes de conectarte, necesitas:
- **IP del servidor** o **dominio** (ejemplo: `192.168.1.100` o `servidor.midominio.com`)
- **Usuario** con permisos de administrador (normalmente `root` o un usuario con `sudo`)
- **Contraseña** o **clave SSH** para autenticación
- **Puerto SSH** (por defecto es `22`)

#### Método 1: Conexión con Contraseña (Más Simple)

**Desde Windows (PowerShell o CMD):**

```powershell
# Conectar al servidor
ssh usuario@IP_DEL_SERVIDOR

# Ejemplo con IP:
ssh root@192.168.1.100

# Ejemplo con dominio:
ssh root@servidor.midominio.com

# Si el puerto SSH no es el 22 (por defecto):
ssh -p 2222 usuario@IP_DEL_SERVIDOR
```

**Desde Mac/Linux (Terminal):**

```bash
# Conectar al servidor
ssh usuario@IP_DEL_SERVIDOR

# Ejemplo:
ssh root@192.168.1.100

# Si el puerto SSH no es el 22:
ssh -p 2222 usuario@IP_DEL_SERVIDOR
```

**Primera conexión:**
- La primera vez que te conectes, verás un mensaje sobre la autenticidad del host
- Escribe `yes` y presiona Enter
- Ingresa tu contraseña cuando se solicite (no verás caracteres mientras escribes, es normal)

#### Método 2: Conexión con Clave SSH (Más Seguro)

**Ventajas:**
- ✅ Más seguro (no necesitas contraseña cada vez)
- ✅ Recomendado para producción
- ✅ Puedes automatizar scripts

**Paso 1: Generar clave SSH (si no tienes una)**

**En Windows (PowerShell):**

```powershell
# Generar clave SSH
ssh-keygen -t ed25519 -C "tu_email@ejemplo.com"

# O si ed25519 no está disponible:
ssh-keygen -t rsa -b 4096 -C "tu_email@ejemplo.com"

# Presiona Enter para usar la ubicación por defecto
# Ingresa una contraseña (opcional pero recomendado)
```

**En Mac/Linux:**

```bash
# Generar clave SSH
ssh-keygen -t ed25519 -C "tu_email@ejemplo.com"

# O si ed25519 no está disponible:
ssh-keygen -t rsa -b 4096 -C "tu_email@ejemplo.com"
```

**Paso 2: Copiar la clave pública al servidor**

**Opción A: Usando ssh-copy-id (Mac/Linux):**

```bash
# Copiar clave al servidor
ssh-copy-id usuario@IP_DEL_SERVIDOR

# Ejemplo:
ssh-copy-id root@192.168.1.100
```

**Opción B: Manual (Windows/Mac/Linux):**

```bash
# 1. Ver tu clave pública
cat ~/.ssh/id_ed25519.pub
# O si usaste RSA:
cat ~/.ssh/id_rsa.pub

# 2. Copiar el contenido completo (desde "ssh-ed25519" hasta el final)

# 3. Conectarte al servidor con contraseña
ssh usuario@IP_DEL_SERVIDOR

# 4. En el servidor, crear directorio .ssh si no existe
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# 5. Agregar tu clave pública
nano ~/.ssh/authorized_keys
# Pegar el contenido de tu clave pública aquí
# Guardar: Ctrl + X, luego Y, luego Enter

# 6. Ajustar permisos
chmod 600 ~/.ssh/authorized_keys
```

**Paso 3: Conectarte con la clave**

```bash
# Ahora puedes conectarte sin contraseña
ssh usuario@IP_DEL_SERVIDOR
```

#### Solución de Problemas Comunes

**Error: "Connection refused" o "Connection timed out"**

```bash
# Verificar que el servidor esté encendido y accesible
ping IP_DEL_SERVIDOR

# Verificar que el puerto SSH esté abierto
telnet IP_DEL_SERVIDOR 22
# O usar:
nc -zv IP_DEL_SERVIDOR 22
```

**Error: "Permission denied (publickey)"**

```bash
# Verificar permisos de la clave
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub

# Verificar que la clave esté en el servidor
ssh usuario@IP_DEL_SERVIDOR "cat ~/.ssh/authorized_keys"
```

**Error: "Host key verification failed"**

```bash
# Eliminar la entrada antigua del archivo known_hosts
ssh-keygen -R IP_DEL_SERVIDOR
```

**No puedes conectarte desde Windows**

- Asegúrate de tener **OpenSSH** instalado (Windows 10/11 lo incluye por defecto)
- Si no funciona, instala **PuTTY** o **MobaXterm** como alternativa

#### Verificar Conexión Exitosa

Una vez conectado, deberías ver algo como:

```bash
Welcome to Ubuntu 22.04.3 LTS (GNU/Linux 5.15.0-xx-generic x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/advantage

Last login: Mon Dec 19 10:30:45 2024 from 192.168.1.50
root@servidor:~#
```

**Comandos útiles para verificar:**

```bash
# Ver información del sistema
uname -a

# Ver versión de Ubuntu
lsb_release -a

# Ver uso de recursos
free -h        # Memoria
df -h          # Disco
uptime         # Tiempo activo y carga
```

#### Desconectarse

```bash
# Para desconectarte del servidor
exit

# O simplemente presiona:
Ctrl + D
```

---

### 2.3 Actualizar el Sistema (Primer Paso Después de Conectar)

Una vez conectado por SSH, lo primero que debes hacer es actualizar el sistema:

```bash
# Ejemplo:
ssh sw4@192.168.1.100
```

> 💡 **Nota:** Reemplaza `usuario` con tu nombre de usuario y `IP_DEL_SERVIDOR` con la IP real.

### 2.2 Actualizar el Sistema

**IMPORTANTE:** Siempre actualizar el sistema antes de instalar cualquier cosa.

```bash
# Actualizar lista de paquetes
sudo apt update

# Actualizar todos los paquetes instalados
sudo apt upgrade -y

# Reiniciar si se actualizó el kernel (opcional pero recomendado)
sudo reboot

# Comando para apagar el servidor
sudo shutdown now
```

### 2.3 Configurar Zona Horaria

```bash
# Ver zona horaria actual
timedatectl

# Configurar zona horaria (ejemplo: América/Buenos Aires)
sudo timedatectl set-timezone America/Argentina/Buenos_Aires

# Verificar el cambio
date
```

### 2.4 Crear Usuario para la Aplicación

Es una buena práctica crear un usuario específico para la aplicación:

```bash
# Crear usuario 'udid' para la aplicación
sudo adduser udid

# Agregar al grupo sudo (opcional, para administración)
sudo usermod -aG sudo udid

# Cambiar al usuario udid
sudo su - udid
```

---

## 3. Instalación de Dependencias del Sistema

### 3.1 Instalar Paquetes Básicos

```bash
# Volver a root o usar sudo
exit  # Si estás como usuario udid

# Instalar paquetes esenciales
sudo apt install -y \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    curl \
    wget \
    vim \
    nano \
    htop \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release
```

### 3.2 Verificar Versión de Python

```bash
# Verificar versión de Python (debe ser 3.10 o superior)
python3 --version

# Debería mostrar algo como: Python 3.10.x o Python 3.12.x
```

### 3.3 Instalar Dependencias para PostgreSQL y Redis

```bash
# Dependencias para compilar psycopg2 (driver de PostgreSQL)
sudo apt install -y \
    libpq-dev \
    postgresql-client

# Dependencias para compilar otras librerías Python
sudo apt install -y \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev
```

---

## 4. Instalación y Configuración de PostgreSQL

### 4.1 Instalar PostgreSQL

```bash
# Instalar PostgreSQL
sudo apt install -y postgresql postgresql-contrib

# Verificar que está corriendo
sudo systemctl status postgresql

# Debería mostrar: active (running)
```

### 4.2 Configurar PostgreSQL

```bash
# Acceder a PostgreSQL como usuario postgres
sudo -u postgres psql
```

Dentro de la consola de PostgreSQL, ejecutar los siguientes comandos:

```sql
-- Crear la base de datos
CREATE DATABASE udid;

-- Crear usuario para la aplicación (CAMBIAR 'tu_password_seguro' por una contraseña real)
CREATE USER udid_user WITH PASSWORD 'parana771';

-- Configurar el usuario
ALTER ROLE udid_user SET client_encoding TO 'utf8';
ALTER ROLE udid_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE udid_user SET timezone TO 'UTC';

-- Dar permisos al usuario sobre la base de datos
GRANT ALL PRIVILEGES ON DATABASE udid TO udid_user;

-- En PostgreSQL 15+, también necesitas esto:
\c udid
GRANT ALL ON SCHEMA public TO udid_user;

-- Salir de PostgreSQL
\q
```

### 4.3 Configurar Acceso a PostgreSQL

Editar el archivo de configuración para permitir conexiones:

```bash
# Encontrar el archivo pg_hba.conf
sudo find /etc/postgresql -name pg_hba.conf

# Editar el archivo (ajustar la versión según tu instalación)
sudo nano /etc/postgresql/16/main/pg_hba.conf
```

Buscar la sección de conexiones IPv4 y asegurarse de que existe esta línea:

```
# IPv4 local connections:
host    all             all             127.0.0.1/32            scram-sha-256
```

Guardar y salir: `Ctrl + X`, luego `Y`, luego `Enter`

```bash
# Reiniciar PostgreSQL para aplicar cambios
sudo systemctl restart postgresql

# Verificar que funciona
sudo systemctl status postgresql
```

### 4.4 Probar la Conexión

```bash
# Probar conexión con el nuevo usuario
psql -h localhost -U udid_user -d udid

# Te pedirá la contraseña, ingrésala
# Si conecta correctamente, verás el prompt: udid=>

# Salir
\q
```

---

## 5. Instalación y Configuración de Redis

### 5.1 Instalar Redis

```bash
# Instalar Redis
sudo apt install -y redis-server

# Verificar versión
redis-server --version
```

### 5.2 Configurar Redis

```bash
# Editar configuración de Redis
sudo nano /etc/redis/redis.conf
```

Buscar y modificar las siguientes líneas (usa `Ctrl + W` para buscar):

```conf
# Cambiar 'supervised no' a 'supervised systemd'
supervised systemd

# Configurar memoria máxima (ajustar según tu RAM disponible)
# Para 8GB RAM, usar 2GB para Redis
maxmemory 2gb

# Política de evicción cuando se llena la memoria
maxmemory-policy allkeys-lru

# Deshabilitar persistencia para mejor rendimiento (opcional)
# Si quieres que Redis guarde datos al disco, deja estas líneas como están
save ""
# save 900 1
# save 300 10
# save 60 10000

# Bind solo a localhost por seguridad
bind 127.0.0.1 ::1
```

Guardar y salir: `Ctrl + X`, luego `Y`, luego `Enter`

### 5.3 Iniciar y Habilitar Redis

```bash
# Reiniciar Redis para aplicar cambios
sudo systemctl restart redis-server

# Habilitar inicio automático
sudo systemctl enable redis-server

# Verificar estado
sudo systemctl status redis-server
```

### 5.4 Probar Redis

```bash
# Conectar a Redis
redis-cli

# Probar con ping (debe responder PONG)
127.0.0.1:6379> ping
PONG

# Probar guardar y leer un valor
127.0.0.1:6379> set test "hola"
OK
127.0.0.1:6379> get test
"hola"
127.0.0.1:6379> del test
(integer) 1

# Salir
127.0.0.1:6379> quit
```

---

## 6. Configuración del Proyecto Python

### 6.1 Crear Directorio para la Aplicación

```bash
# Crear directorio para la aplicación
sudo mkdir -p /opt/udid

# Cambiar propietario al usuario udid
sudo chown -R udid:udid /opt/udid

# Cambiar al usuario udid
sudo su - udid

# Ir al directorio
cd /opt/udid
```

### 6.2 Copiar el Código del Proyecto

**Opción A: Usando Git (si el código está en un repositorio)**

```bash
# Clonar repositorio
git clone https://github.com/leompe8907/Ubuntu .

# O si es privado
git clone https://usuario:token@tu-repositorio.git .
```

**Opción B: Usando SCP (copiar desde tu computadora)**

Desde tu computadora local (no en el servidor):

```bash
# Windows (PowerShell) o Mac/Linux Terminal
scp -r "C:\Users\Leonard\Desktop\udid\ubuntu\*" udid@IP_DEL_SERVIDOR:/opt/udid/

# O comprimir primero y luego descomprimir
# En tu computadora:
zip -r proyecto.zip ubuntu/

# Copiar al servidor
scp proyecto.zip udid@IP_DEL_SERVIDOR:/opt/udid/

# En el servidor, descomprimir
cd /opt/udid
unzip proyecto.zip
mv ubuntu/* .
rm -rf ubuntu proyecto.zip
```

**Opción C: Usando SFTP (con FileZilla o WinSCP)**

1. Descargar e instalar FileZilla o WinSCP
2. Conectar al servidor con las credenciales SSH
3. Navegar a `/opt/udid/` en el servidor
4. Arrastrar los archivos del proyecto

### 6.3 Crear y Activar Entorno Virtual

```bash
# Asegurarse de estar en el directorio del proyecto
cd /opt/udid

# Crear entorno virtual
python3 -m venv env

# Activar entorno virtual
source env/bin/activate

# Verificar que está activado (debe mostrar (venv) al inicio del prompt)
# (venv) udid@servidor:/opt/udid$
```

### 6.4 Instalar Dependencias de Python

```bash
# Actualizar pip
pip install --upgrade pip

# Instalar dependencias del proyecto
pip install -r requirements.txt

# Si hay errores con psycopg2, intentar:
pip install psycopg2-binary

# Verificar instalación
pip list
```

### 6.5 Verificar Estructura del Proyecto

```bash
# La estructura debe verse así:
ls -la /opt/udid/

# Debería mostrar:
# - manage.py
# - config.py
# - requirements.txt
# - ubuntu/  (directorio con settings.py, urls.py, etc.)
# - udid/    (directorio con views.py, models.py, etc.)
# - env/    (entorno virtual)
```

---

## 7. Configuración de Variables de Entorno

### 7.1 Crear Archivo .env

```bash
# Crear archivo .env en el directorio del proyecto
nano /opt/udid/.env
```

Copiar y pegar el siguiente contenido, **modificando los valores según tu configuración**:

```env
# ============================================================================
# CONFIGURACIÓN DJANGO
# ============================================================================

# Clave secreta de Django (GENERAR UNA NUEVA Y ÚNICA)
# Puedes generar una con: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
SECRET_KEY=tu-clave-secreta-muy-larga-y-segura-aqui

# Modo debug (SIEMPRE False en producción)
DEBUG=False

# Hosts permitidos (separados por coma)
# Agregar la IP del servidor y el dominio si tienes uno
ALLOWED_HOSTS=127.0.0.1,localhost,tu.dominio.com,IP_DEL_SERVIDOR

# ============================================================================
# BASE DE DATOS POSTGRESQL
# ============================================================================

# Configuración de PostgreSQL
POSTGRES_DB=udid
POSTGRES_USER=udid_user
POSTGRES_PASSWORD=tu_password_seguro
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# ============================================================================
# REDIS
# ============================================================================

# URL de Redis
REDIS_URL=redis://localhost:6379/0

# Configuración de Channel Layers (WebSockets)
REDIS_CHANNEL_LAYER_URL=redis://localhost:6379/0

# Configuración de Rate Limiting
REDIS_RATE_LIMIT_URL=redis://localhost:6379/1

# ============================================================================
# PANACCESS (API Externa)
# ============================================================================

# URL de la API de Panaccess
url_panaccess=https://tu-url-panaccess.com/api

# Credenciales de Panaccess
username=tu_usuario_panaccess
password=tu_password_panaccess
api_token=tu_api_token_panaccess
salt=tu_salt_panaccess

# Clave de encriptación (32 caracteres para AES-256)
ENCRYPTION_KEY=tu_clave_encriptacion_32_chars_

# ============================================================================
# CORS Y SEGURIDAD
# ============================================================================

# Orígenes permitidos para CORS (separados por coma)
CORS_ALLOWED_ORIGINS=https://tu-frontend.com,https://otro-origen.com

# Orígenes WebSocket permitidos
WS_ALLOWED_ORIGINS=https://tu-frontend.com,wss://tu-frontend.com

# CSRF trusted origins
CSRF_TRUSTED_ORIGINS=https://tu.dominio.com,https://IP_DEL_SERVIDOR

# ============================================================================
# CONFIGURACIÓN DEL SERVIDOR
# ============================================================================

# Host y puerto del servidor
SERVER_HOST=127.0.0.1
SERVER_PORT=8000

# ============================================================================
# CONFIGURACIÓN DE UDID
# ============================================================================

# Tiempo de expiración de UDID (minutos)
UDID_EXPIRATION_MINUTES=15

# Máximo de intentos de validación
UDID_MAX_ATTEMPTS=5

# Timeout de WebSocket (segundos)
UDID_WAIT_TIMEOUT_AUTOMATIC=180
UDID_WAIT_TIMEOUT_MANUAL=180

# ============================================================================
# CONFIGURACIÓN DE JWT
# ============================================================================

# Tiempo de vida del access token (minutos)
JWT_ACCESS_TOKEN_LIFETIME_MINUTES=15

# Tiempo de vida del refresh token (días)
JWT_REFRESH_TOKEN_LIFETIME_DAYS=1

# ============================================================================
# CONFIGURACIÓN DE CACHE
# ============================================================================

# Prefijo para claves de cache
CACHE_KEY_PREFIX=udid_prod

# Timeout de cache (segundos)
CACHE_TIMEOUT=300

# ============================================================================
# CONFIGURACIÓN DE CELERY
# ============================================================================

# URL del broker de Celery (Redis)
# Por defecto usa REDIS_URL, pero puedes usar una DB diferente
CELERY_BROKER_URL=redis://localhost:6379/0

# URL del backend de resultados (Redis)
# Usa una DB diferente del broker para evitar conflictos
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# Serialización de tareas (json es más seguro)
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json

# Timezone para Celery
CELERY_TIMEZONE=UTC
CELERY_ENABLE_UTC=True

# Configuración de resultados
CELERY_RESULT_EXPIRES=3600
CELERY_RESULT_PERSISTENT=True

# Configuración de Flower (monitoreo opcional)
CELERY_FLOWER_PORT=5555
CELERY_FLOWER_BASIC_AUTH=admin:admin
```

Guardar y salir: `Ctrl + X`, luego `Y`, luego `Enter`

### 7.2 Generar SECRET_KEY

```bash
# Generar una clave secreta segura
cd /opt/udid
source venv/bin/activate
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Copiar el resultado y pegarlo en el archivo .env como SECRET_KEY
```

### 7.3 Proteger el Archivo .env

```bash
# Cambiar permisos para que solo el usuario udid pueda leerlo
chmod 600 /opt/udid/.env

# Verificar permisos
ls -la /opt/udid/.env
# Debe mostrar: -rw------- 1 udid udid
```

---

## 8. Migraciones y Configuración Inicial

### 8.1 Modificar settings.py para PostgreSQL

Editar el archivo de configuración:

```bash
nano /opt/udid/ubuntu/settings.py
```

Buscar la sección `DATABASES` y modificarla (comentar MySQL y descomentar PostgreSQL):

```python
# ============================================================================
# BASE DE DATOS
# ============================================================================

# PostgreSQL (PRODUCCIÓN)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "udid"),
        "USER": os.getenv("POSTGRES_USER", "udid_user"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# Comentar o eliminar la configuración de MySQL:
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         ...
#     }
# }
```

Guardar y salir.

### 8.2 Ejecutar Migraciones

```bash
# Asegurarse de estar en el directorio correcto con el venv activado
cd /opt/udid
source env/bin/activate

# Verificar configuración
python manage.py check

# Crear migraciones (si hay cambios en modelos)
python manage.py makemigrations

# Aplicar migraciones a la base de datos
python manage.py migrate

# Debería mostrar varios "OK" o "Applying..."
```

### 8.3 Crear Superusuario para Admin

```bash
# Crear superusuario para acceder al panel de administración
python manage.py createsuperuser

# Te pedirá:
# - Username: admin (o el que prefieras)
# - Email: tu@email.com
# - Password: (contraseña segura)
```

### 8.4 Recolectar Archivos Estáticos

```bash
# Recolectar archivos estáticos para producción
python manage.py collectstatic --noinput

# Debería crear el directorio 'staticfiles'
ls -la staticfiles/
```

### 8.5 Verificar que Todo Funciona

```bash
# Probar el servidor de desarrollo (solo para verificar)
python manage.py runserver 0.0.0.0:8000

# Abrir en el navegador: http://IP_DEL_SERVIDOR:8000/admin/
# Debería mostrar la página de login de Django Admin

# Detener con Ctrl + C
```

---

## 9. Configuración de Nginx

### 9.1 Instalar Nginx

```bash
# Salir del usuario udid si es necesario
exit

# Instalar Nginx
sudo apt install -y nginx

# Verificar instalación
nginx -v

# Verificar estado
sudo systemctl status nginx
```

### 9.2 Crear Configuración para UDID

```bash
# Crear archivo de configuración
sudo nano /etc/nginx/sites-available/udid
```

Copiar y pegar la siguiente configuración:

```nginx
# ============================================================================
# Configuración de Nginx para UDID Server
# ============================================================================

# Upstream para balanceo de carga entre múltiples instancias de Daphne
upstream udid_backend {
    # Usar ip_hash para que cada cliente siempre vaya al mismo servidor
    # Importante para WebSockets
    ip_hash;
    
    # Instancias de Daphne (ajustar según número de workers)
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
    
    # Mantener conexiones abiertas
    keepalive 32;
}

# Redirigir HTTP a HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name _;  # Acepta cualquier nombre de servidor
    
    # Redirigir todo el tráfico a HTTPS
    return 301 https://$host$request_uri;
}

# Servidor HTTPS principal
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name _;  # Acepta cualquier nombre de servidor
    
    # ========================================================================
    # Configuración SSL (se configurará en la siguiente sección)
    # ========================================================================
    ssl_certificate /etc/nginx/ssl/udid.crt;
    ssl_certificate_key /etc/nginx/ssl/udid.key;
    
    # Configuración SSL segura
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # ========================================================================
    # Headers de seguridad
    # ========================================================================
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    # ========================================================================
    # Logs
    # ========================================================================
    access_log /var/log/nginx/udid_access.log;
    error_log /var/log/nginx/udid_error.log;
    
    # ========================================================================
    # Configuración general
    # ========================================================================
    client_max_body_size 10M;
    
    # ========================================================================
    # Archivos estáticos
    # ========================================================================
    location /static/ {
        alias /opt/udid/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # ========================================================================
    # WebSocket endpoint
    # ========================================================================
    location /ws/ {
        proxy_pass http://udid_backend;
        proxy_http_version 1.1;
        
        # Headers necesarios para WebSocket
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Headers de proxy
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        
        # Timeouts para WebSocket (más largos que HTTP normal)
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        # Desactivar buffering para WebSocket
        proxy_buffering off;
    }
    
    # ========================================================================
    # API y Admin (HTTP normal)
    # ========================================================================
    location / {
        proxy_pass http://udid_backend;
        proxy_http_version 1.1;
        
        # Headers de proxy
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        
        # Mantener conexiones
        proxy_set_header Connection "";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # Buffering para mejor rendimiento
        proxy_buffering on;
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
    }
    
    # ========================================================================
    # Health check endpoint
    # ========================================================================
    location /health {
        access_log off;
        return 200 "OK\n";
        add_header Content-Type text/plain;
    }
}
```

Guardar y salir.

### 9.3 Habilitar el Sitio

```bash
# Crear enlace simbólico para habilitar el sitio
sudo ln -s /etc/nginx/sites-available/udid /etc/nginx/sites-enabled/

# Deshabilitar el sitio por defecto
sudo rm /etc/nginx/sites-enabled/default

# Verificar configuración de Nginx
sudo nginx -t

# Debería mostrar: syntax is ok / test is successful
```

---

## 10. Configuración de SSL/HTTPS

### 10.1 Opción A: Certificado Autofirmado (Para IP sin dominio)

> ⚠️ **Nota:** Los certificados autofirmados mostrarán una advertencia en el navegador. Es seguro para uso interno, pero para producción pública se recomienda un dominio con Let's Encrypt.

```bash
# Crear directorio para certificados
sudo mkdir -p /etc/nginx/ssl

# Generar certificado autofirmado (válido por 365 días)
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/udid.key \
    -out /etc/nginx/ssl/udid.crt

# Te pedirá información (puedes dejar valores por defecto presionando Enter):
# Country Name: CO
# State: Tu Estado
# Locality: Tu Ciudad
# Organization: Tu Organización
# Common Name: IP_DEL_SERVIDOR o tu.dominio.com
# Email: tu@email.com

# Cambiar permisos
sudo chmod 600 /etc/nginx/ssl/udid.key
sudo chmod 644 /etc/nginx/ssl/udid.crt
```

### 10.2 Opción B: Let's Encrypt (Cuando tengas un dominio)

> 📝 **Requisito:** Debes tener un dominio apuntando a la IP del servidor.

```bash
# Instalar Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtener certificado (reemplazar tu.dominio.com con tu dominio real)
sudo certbot --nginx -d tu.dominio.com

# Seguir las instrucciones interactivas
# - Ingresar email
# - Aceptar términos
# - Elegir si redirigir HTTP a HTTPS (recomendado: Yes)

# Verificar renovación automática
sudo certbot renew --dry-run
```

Si usas Let's Encrypt, actualizar la configuración de Nginx:

```bash
sudo nano /etc/nginx/sites-available/udid
```

Cambiar las líneas de SSL:

```nginx
ssl_certificate /etc/letsencrypt/live/tu.dominio.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/tu.dominio.com/privkey.pem;
```

### 10.3 Reiniciar Nginx

```bash
# Verificar configuración
sudo nginx -t

# Reiniciar Nginx
sudo systemctl restart nginx

# Verificar estado
sudo systemctl status nginx
```

---

## 11. Configuración de Systemd

### 11.1 Crear Servicio para Daphne

Vamos a crear un servicio systemd que ejecute múltiples instancias de Daphne:

```bash
# Crear archivo de servicio para la instancia principal
sudo nano /etc/systemd/system/udid@.service
```

Copiar el siguiente contenido:

```ini
[Unit]
Description=UDID Daphne Server (Instance %i)
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=udid
Group=udid
WorkingDirectory=/opt/udid
Environment="PATH=/opt/udid/env/bin"
EnvironmentFile=/opt/udid/.env

# Comando para ejecutar Daphne
# El puerto se calcula: 8000 + %i (instancia)
ExecStart=/opt/udid/env/bin/daphne \
    -b 127.0.0.1 \
    -p 800%i \
    --access-log - \
    --proxy-headers \
    -t 60 \
    --websocket_timeout 300 \
    ubuntu.asgi:application

# Reinicio automático
Restart=always
RestartSec=3

# Limitar recursos (ajustar según necesidad)
MemoryMax=1G
CPUQuota=100%

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=udid-%i

[Install]
WantedBy=multi-user.target
```

Guardar y salir.

### 11.2 Crear Script de Control

```bash
# Crear script para manejar todas las instancias
sudo nano /opt/udid/manage_services.sh
```

Copiar el siguiente contenido:

```bash
#!/bin/bash
# Script para manejar múltiples instancias de Daphne

# Número de instancias (ajustar según CPU cores)
INSTANCES=4

case "$1" in
    start)
        echo "Iniciando $INSTANCES instancias de UDID..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl start udid@$i
            echo "  Instancia $i iniciada (puerto 800$i)"
        done
        ;;
    stop)
        echo "Deteniendo instancias de UDID..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl stop udid@$i
            echo "  Instancia $i detenida"
        done
        ;;
    restart)
        echo "Reiniciando instancias de UDID..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl restart udid@$i
            echo "  Instancia $i reiniciada"
        done
        ;;
    status)
        echo "Estado de instancias de UDID:"
        for i in $(seq 0 $((INSTANCES-1))); do
            echo "--- Instancia $i (puerto 800$i) ---"
            sudo systemctl status udid@$i --no-pager | head -5
        done
        ;;
    enable)
        echo "Habilitando inicio automático..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl enable udid@$i
            echo "  Instancia $i habilitada"
        done
        ;;
    disable)
        echo "Deshabilitando inicio automático..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl disable udid@$i
            echo "  Instancia $i deshabilitada"
        done
        ;;
    *)
        echo "Uso: $0 {start|stop|restart|status|enable|disable}"
        exit 1
        ;;
esac
```

Guardar y hacer ejecutable:

```bash
sudo chmod +x /opt/udid/manage_services.sh
sudo chown udid:udid /opt/udid/manage_services.sh
```

### 11.3 Iniciar y Habilitar Servicios

```bash
# Recargar systemd
sudo systemctl daemon-reload

# Iniciar todas las instancias
sudo /opt/udid/manage_services.sh start

# Habilitar inicio automático
sudo /opt/udid/manage_services.sh enable

# Verificar estado
sudo /opt/udid/manage_services.sh status
```

### 11.4 Verificar que Todo Funciona

```bash
# Verificar puertos en uso
sudo ss -tlnp | grep 800

# Debería mostrar:
# LISTEN  127.0.0.1:8000  daphne
# LISTEN  127.0.0.1:8001  daphne
# LISTEN  127.0.0.1:8002  daphne
# LISTEN  127.0.0.1:8003  daphne

# Verificar logs
sudo journalctl -u udid@0 -f

# Presionar Ctrl+C para salir
```

---

## 12. Configuración de Celery (Ejecución Manual de Tareas)

### 📋 Información sobre las Tareas de Celery

El proyecto usa **Celery** para ejecutar tareas en background de forma asíncrona y escalable. **Por defecto, las tareas NO se ejecutan automáticamente** - tú decides cuándo ejecutarlas manualmente.

**Tareas disponibles para ejecución manual:**

| Tarea                                | Propósito                                  | Duración         |
|--------------------------------------|--------------------------------------------|------------------|
| `initial_sync_all_data`              | Sincronización COMPLETA inicial de todos los datos | Puede tomar horas |
| `download_new_subscribers`           | Descarga solo suscriptores nuevos          | Segundos/Minutos |
| `update_all_subscribers`             | Actualiza datos de suscriptores existentes | Segundos/Minutos |
| `update_smartcards_from_subscribers` | Actualiza asociaciones de smartcards       | Segundos/Minutos |
| `validate_and_fix_all_data`          | Sincronización completa y validación       | Puede tomar horas|

**Componentes de Celery:**
- **Celery Worker**: Ejecuta las tareas en background (SIEMPRE debe estar activo)
- **Celery Beat**: Programa y ejecuta tareas periódicas (NO se activa por defecto)
- **Flower** (opcional): Interfaz web para monitorear tareas

**¿Cómo funciona?**
- Tú ejecutas las tareas manualmente cuando lo necesites
- Las tareas se envían a Redis (broker)
- Celery Worker toma las tareas de Redis y las ejecuta
- Los resultados se almacenan en Redis (backend)

### 12.1 Verificar Instalación de Celery

Celery ya está incluido en `requirements.txt`, pero verifiquemos que se instaló correctamente:

```bash
# Activar entorno virtual
cd /opt/udid
source env/bin/activate

# Verificar que Celery está instalado
celery --version

# Debería mostrar: celery 5.4.0 (o similar)
```

### 12.2 Crear Servicio Systemd para Celery Worker

El Worker de Celery ejecuta las tareas en background. **Este servicio DEBE estar activo** para poder ejecutar tareas manualmente:

```bash
# Crear archivo de servicio para Celery Worker
sudo nano /etc/systemd/system/celery-worker.service
```

Copiar el siguiente contenido:

```ini
[Unit]
Description=Celery Worker para UDID
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=udid
Group=udid
WorkingDirectory=/opt/udid
Environment="PATH=/opt/udid/env/bin"
EnvironmentFile=/opt/udid/.env

# Comando para ejecutar Celery Worker
ExecStart=/opt/udid/env/bin/celery -A ubuntu worker \
    --loglevel=info \
    --logfile=/var/log/udid/celery-worker.log \
    --pidfile=/var/run/udid/celery-worker.pid

# Comando para detener
ExecStop=/bin/kill -s TERM $MAINPID
PIDFile=/var/run/udid/celery-worker.pid

# Reinicio automático
Restart=always
RestartSec=3

# Limitar recursos
MemoryMax=2G
CPUQuota=100%

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=celery-worker

[Install]
WantedBy=multi-user.target
```

Guardar y salir.

### 12.3 Crear Servicio Systemd para Celery Beat (Opcional - NO se activa por defecto)

> ⚠️ **NOTA:** Celery Beat se crea pero **NO se inicia automáticamente**. Solo úsalo si en el futuro quieres activar tareas periódicas. Por ahora, las tareas se ejecutan manualmente.

Celery Beat programa y ejecuta las tareas periódicas. Este servicio está disponible pero **deshabilitado por defecto**:

```bash
# Crear archivo de servicio para Celery Beat
sudo nano /etc/systemd/system/celery-beat.service
```

Copiar el siguiente contenido:

```ini
[Unit]
Description=Celery Beat Scheduler para UDID
After=network.target postgresql.service redis-server.service celery-worker.service
Requires=postgresql.service redis-server.service celery-worker.service

[Service]
Type=simple
User=udid
Group=udid
WorkingDirectory=/opt/udid
Environment="PATH=/opt/udid/env/bin"
EnvironmentFile=/opt/udid/.env

# Comando para ejecutar Celery Beat
ExecStart=/opt/udid/env/bin/celery -A ubuntu beat \
    --loglevel=info \
    --logfile=/var/log/udid/celery-beat.log \
    --pidfile=/var/run/udid/celery-beat.pid \
    --schedule=/var/run/udid/celerybeat-schedule

# Reinicio automático
Restart=always
RestartSec=3

# Limitar recursos
MemoryMax=512M
CPUQuota=50%

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=celery-beat

[Install]
WantedBy=multi-user.target
```

Guardar y salir.

### 12.4 Crear Directorios Necesarios

```bash
# Crear directorio para archivos PID y schedule
sudo mkdir -p /var/run/udid
sudo chown udid:udid /var/run/udid

# Crear directorio de logs (si no existe)
sudo mkdir -p /var/log/udid
sudo chown udid:udid /var/log/udid
```

### 12.5 Iniciar y Habilitar Servicios de Celery

**IMPORTANTE:** Solo iniciamos el **Worker** (necesario para ejecutar tareas). **NO iniciamos Beat** (tareas automáticas deshabilitadas):

```bash
# Recargar systemd
sudo systemctl daemon-reload

# Iniciar SOLO el Worker (necesario para ejecutar tareas manualmente)
sudo systemctl start celery-worker

# Habilitar inicio automático SOLO del Worker
sudo systemctl enable celery-worker

# Verificar estado del Worker
sudo systemctl status celery-worker

# Verificar que Beat NO está corriendo (debe estar inactivo)
sudo systemctl status celery-beat
# Debería mostrar: "inactive (dead)" o similar
```

### 12.6 Ejecutar Tareas Manualmente

Ahora puedes ejecutar cualquier tarea cuando lo necesites. Aquí te mostramos cómo:

**Tareas disponibles:**
- `initial_sync_all_data`: Sincronización inicial completa (ejecutar UNA VEZ cuando la BD está vacía)
- `download_new_subscribers`: Descargar solo suscriptores nuevos
- `update_all_subscribers`: Actualizar datos de suscriptores existentes
- `update_smartcards_from_subscribers`: Actualizar asociaciones de smartcards
- `validate_and_fix_all_data`: Validación y corrección completa (puede tomar horas)

#### Método 1: Ejecutar desde el Shell de Django (Recomendado)

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Abrir shell de Django
python manage.py shell
```

Dentro del shell de Python, ejecutar la tarea que necesites:

```python
# Importar las tareas disponibles
from udid.tasks import (
    initial_sync_all_data,
    download_new_subscribers,
    update_all_subscribers,
    update_smartcards_from_subscribers,
    validate_and_fix_all_data
)

# Ejecutar la tarea que quieras (ejemplo: sincronización inicial)
task = initial_sync_all_data.delay()

# Ver el ID de la tarea
print(f"Task ID: {task.id}")
print(f"Estado: {task.state}")

# IMPORTANTE: La tarea se ejecuta en background
# Puedes salir del shell y la tarea continuará ejecutándose
# Para verificar el progreso, usar Flower o los logs

# Ejemplos de otras tareas:
# task = download_new_subscribers.delay()
# task = update_all_subscribers.delay()
# task = validate_and_fix_all_data.delay()

# Salir del shell
exit()
```

#### Método 2: Ejecutar desde la Línea de Comandos

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Ejecutar la tarea directamente desde Python
python -c "
from udid.tasks import initial_sync_all_data
task = initial_sync_all_data.delay()
print(f'Task ID: {task.id}')
print('Tarea iniciada. Verifica el progreso con:')
print('  - Flower: http://IP_DEL_SERVIDOR:5555')
print('  - Logs: tail -f /var/log/udid/celery-worker.log')
"
```

#### Método 3: Ejecutar de Forma Síncrona (Solo para Pruebas)

> ⚠️ **Nota:** Este método bloquea la terminal hasta que la tarea termine. Solo usar para pruebas o si necesitas ver el resultado inmediatamente.

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Ejecutar de forma síncrona (bloquea hasta completar)
python manage.py shell -c "
from udid.tasks import initial_sync_all_data
result = initial_sync_all_data()
print('Resultado:', result)
"
```

#### Verificar el Progreso de la Tarea

**Opción 1: Usar Flower (Recomendado)**

```bash
# Acceder a Flower en el navegador
# http://IP_DEL_SERVIDOR:5555
# Usuario/contraseña: admin/admin (o el configurado en .env)

# Buscar la tarea por ID o nombre: initial_sync_all_data
# Verás el estado: PENDING → STARTED → SUCCESS/FAILURE
```

**Opción 2: Ver Logs del Worker**

```bash
# Ver logs en tiempo real
sudo tail -f /var/log/udid/celery-worker.log

# O usando journalctl
sudo journalctl -u celery-worker -f

# Buscar mensajes específicos de la tarea
grep "INITIAL_SYNC" /var/log/udid/celery-worker.log | tail -20
```

**Opción 3: Verificar desde la Línea de Comandos**

```bash
cd /opt/udid
source env/bin/activate

# Ver tareas activas
celery -A ubuntu inspect active

# Ver estadísticas
celery -A ubuntu inspect stats
```

#### Verificar que la Sincronización se Completó

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid
source env/bin/activate

# Verificar que hay datos en la base de datos
python manage.py shell
```

Dentro del shell:

```python
# Verificar suscriptores
from udid.models import ListOfSubscriber
print(f"Suscriptores: {ListOfSubscriber.objects.count()}")

# Verificar smartcards
from udid.models import ListOfSmartcards
print(f"Smartcards: {ListOfSmartcards.objects.count()}")

# Verificar credenciales
from udid.models import SubscriberLoginInfo
print(f"Credenciales: {SubscriberLoginInfo.objects.count()}")

# Verificar tabla consolidada
from udid.models import SubscriberInfo
print(f"SubscriberInfo: {SubscriberInfo.objects.count()}")

exit()
```

**Si los conteos son mayores a 0**, la sincronización inicial fue exitosa.

#### Solución de Problemas

**La tarea no inicia:**

```bash
# Verificar que Celery Worker está corriendo
sudo systemctl status celery-worker

# Si no está corriendo, iniciarlo
sudo systemctl start celery-worker

# Ver logs de errores
sudo journalctl -u celery-worker -n 50
```

**La tarea falla con errores de autenticación:**

```bash
# Verificar que las credenciales de Panaccess están correctas en .env
cat /opt/udid/.env | grep -E "(url_panaccess|username|password|api_token)"

# Verificar que puedes conectarte a Panaccess
# (revisar logs para ver el error específico)
```

**La tarea tarda mucho tiempo:**
- Esto es normal si hay muchos registros (10,000+ smartcards pueden tomar 8-9 horas)
- Verificar el progreso en Flower o en los logs
- No interrumpir la tarea, dejar que complete

**Verificar que la tarea se completó correctamente:**

```bash
# Buscar en los logs el mensaje de finalización
grep "INITIAL_SYNC.*finalizada\|completada\|success" /var/log/udid/celery-worker.log | tail -5
```

### 12.7 (Opcional) Activar Tareas Automáticas con Celery Beat

Si en el futuro quieres activar las tareas periódicas automáticas, puedes hacerlo así:

```bash
# Iniciar Celery Beat
sudo systemctl start celery-beat

# Habilitar inicio automático (opcional)
sudo systemctl enable celery-beat

# Verificar que está corriendo
sudo systemctl status celery-beat

# Ver logs
sudo journalctl -u celery-beat -f
```

**Tareas periódicas que se activarían:**
- `download_new_subscribers`: Cada 5 minutos
- `update_all_subscribers`: Cada 5 minutos
- `update_smartcards_from_subscribers`: Cada 5 minutos
- `validate_and_fix_all_data`: Diaria a las 2:00 AM

**Para desactivar las tareas automáticas nuevamente:**

```bash
# Detener Celery Beat
sudo systemctl stop celery-beat

# Deshabilitar inicio automático
sudo systemctl disable celery-beat
```

### 12.8 (Opcional) Configurar Flower para Monitoreo

Flower es una interfaz web para monitorear Celery:

```bash
# Crear archivo de servicio para Flower
sudo nano /etc/systemd/system/celery-flower.service
```

Copiar el siguiente contenido:

```ini
[Unit]
Description=Celery Flower (Monitor) para UDID
After=network.target redis-server.service celery-worker.service
Requires=redis-server.service celery-worker.service

[Service]
Type=simple
User=udid
Group=udid
WorkingDirectory=/opt/udid
Environment="PATH=/opt/udid/env/bin"
EnvironmentFile=/opt/udid/.env

# Comando para ejecutar Flower
# Cambiar usuario:contraseña en basic_auth si lo configuraste en .env
ExecStart=/opt/udid/env/bin/celery -A ubuntu flower \
    --port=5555 \
    --basic_auth=${CELERY_FLOWER_BASIC_AUTH:-admin:admin} \
    --logfile=/var/log/udid/celery-flower.log

# Reinicio automático
Restart=always
RestartSec=3

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=celery-flower

[Install]
WantedBy=multi-user.target
```

Guardar y salir.

```bash
# Iniciar y habilitar Flower (opcional)
sudo systemctl daemon-reload
sudo systemctl start celery-flower
sudo systemctl enable celery-flower

# Acceder a Flower en: http://IP_DEL_SERVIDOR:5555
# Usuario/contraseña por defecto: admin/admin (cambiar en .env)
```

### 12.9 Configurar Crontab para Tareas de Mantenimiento

Aunque Celery maneja las tareas principales, algunas tareas de mantenimiento se ejecutan con crontab:

```bash
# Editar crontab del usuario udid
sudo -u udid crontab -e

# Si te pregunta qué editor usar, selecciona nano (opción 1)
```

Agregar las siguientes líneas:

```cron
# ============================================================================
# Tareas de Mantenimiento (no relacionadas con Celery)
# ============================================================================

# Limpiar sesiones expiradas de Django (diario a las 3 AM)
0 3 * * * cd /opt/udid && /opt/udid/env/bin/python manage.py clearsessions >> /var/log/udid/clearsessions.log 2>&1

# Limpiar UDIDs expirados (cada hora)
0 * * * * cd /opt/udid && /opt/udid/env/bin/python -c "from udid.models import UDIDAuthRequest; from django.utils import timezone; UDIDAuthRequest.objects.filter(status='pending', expires_at__lt=timezone.now()).update(status='expired')" >> /var/log/udid/cleanup.log 2>&1

# Rotación de logs (semanal, domingos a las 4 AM)
0 4 * * 0 /usr/sbin/logrotate /etc/logrotate.d/udid
```

Guardar y salir.

### 12.10 Configurar Rotación de Logs

```bash
# Crear configuración de logrotate
sudo nano /etc/logrotate.d/udid
```

Copiar el siguiente contenido:

```
/var/log/udid/*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0640 udid udid
    postrotate
        systemctl reload celery-worker > /dev/null 2>&1 || true
        systemctl reload celery-beat > /dev/null 2>&1 || true
    endscript
}

/opt/udid/server.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0640 udid udid
    postrotate
        /opt/udid/manage_services.sh restart > /dev/null 2>&1 || true
    endscript
}
```

Guardar y salir.

### 12.11 Verificar que Celery está Funcionando

#### Método 1: Verificar servicios systemd

```bash
# Verificar estado del Worker (debe estar activo)
sudo systemctl status celery-worker

# Verificar que Beat NO está corriendo (debe estar inactivo)
sudo systemctl status celery-beat

# Ver logs en tiempo real
sudo journalctl -u celery-worker -f
```

#### Método 2: Revisar logs de Celery

```bash
# Ver logs del Worker
tail -f /var/log/udid/celery-worker.log

# Buscar ejecuciones de tareas específicas
grep "download_new_subscribers" /var/log/udid/celery-worker.log | tail -10
grep "initial_sync_all_data" /var/log/udid/celery-worker.log | tail -5
```

#### Método 3: Usar Flower (si está configurado)

```bash
# Acceder a Flower en el navegador
# http://IP_DEL_SERVIDOR:5555
# Usuario/contraseña: admin/admin (o el configurado en .env)

# Ver tareas ejecutándose, completadas, fallidas, etc.
```

#### Método 4: Verificar desde la línea de comandos

```bash
cd /opt/udid
source env/bin/activate

# Ver workers activos
celery -A ubuntu inspect active

# Ver tareas registradas
celery -A ubuntu inspect registered

# Ver estadísticas
celery -A ubuntu inspect stats

# Ver estado de una tarea específica
python manage.py shell
# Luego:
# from celery.result import AsyncResult
# from ubuntu.celery import app
# result = AsyncResult('TASK_ID', app=app)
# print(result.state)
```

#### Método 5: Ejecutar una tarea de prueba

```bash
cd /opt/udid
source env/bin/activate

# Ejecutar una tarea de prueba manualmente
python manage.py shell
```

Dentro del shell de Python:

```python
from udid.tasks import download_new_subscribers

# Ejecutar tarea de forma asíncrona
result = download_new_subscribers.delay()

# Ver el ID de la tarea
print(f"Task ID: {result.id}")

# Verificar estado
print(f"Estado: {result.state}")

# Esperar resultado (solo para pruebas, no usar en producción)
# result.get(timeout=60)

# Salir
exit()
```

### 12.12 Tareas Disponibles y Cuándo Usarlas

| Tarea                                | Cuándo Usarla                                  | Duración          |
|--------------------------------------|------------------------------------------------|-------------------|
| `initial_sync_all_data`              | Primera vez que despliegas el sistema o cuando la BD está vacía | Puede tomar horas |
| `download_new_subscribers`           | Cuando quieres descargar solo suscriptores nuevos | Segundos/Minutos  |
| `update_all_subscribers`             | Cuando quieres actualizar datos de suscriptores existentes | Segundos/Minutos  |
| `update_smartcards_from_subscribers` | Cuando quieres actualizar asociaciones de smartcards | Segundos/Minutos  |
| `validate_and_fix_all_data`          | Cuando quieres una sincronización completa y validación | Puede tomar horas |

### 12.13 Comandos Útiles de Celery

```bash
# Ver workers activos
celery -A ubuntu inspect active

# Ver estadísticas de workers
celery -A ubuntu inspect stats

# Ver tareas registradas
celery -A ubuntu inspect registered

# Ver estado de una tarea específica
python manage.py shell
# Luego:
# from celery.result import AsyncResult
# from ubuntu.celery import app
# result = AsyncResult('TASK_ID', app=app)
# print(result.state)

# Reiniciar worker (después de cambios en código)
sudo systemctl restart celery-worker

# Ver logs en tiempo real
sudo journalctl -u celery-worker -f

# Detener todas las tareas activas (emergencia)
sudo systemctl stop celery-worker
```

---

## 13. Verificación y Pruebas

### 13.1 Lista de Verificación Pre-Lanzamiento

Ejecutar estos comandos para verificar que todo está configurado correctamente:

```bash
# ====== 1. VERIFICAR SERVICIOS ======
echo "=== Verificando PostgreSQL ==="
sudo systemctl status postgresql | head -5

echo "=== Verificando Redis ==="
sudo systemctl status redis-server | head -5

echo "=== Verificando Nginx ==="
sudo systemctl status nginx | head -5

echo "=== Verificando Daphne ==="
sudo /opt/udid/manage_services.sh status

echo "=== Verificando Celery Worker ==="
sudo systemctl status celery-worker | head -5

echo "=== Verificando Celery Beat (debe estar inactivo) ==="
sudo systemctl status celery-beat | head -5

# ====== 2. VERIFICAR CONEXIONES ======
echo "=== Verificando conexión PostgreSQL ==="
sudo -u udid psql -h localhost -U udid_user -d udid -c "SELECT version();"

echo "=== Verificando conexión Redis ==="
redis-cli ping

# ====== 3. VERIFICAR PUERTOS ======
echo "=== Puertos en uso ==="
sudo ss -tlnp | grep -E '(80|443|8000|8001|8002|8003|5432|6379)'

# ====== 4. VERIFICAR LOGS DE ERRORES ======
echo "=== Últimos errores de Nginx ==="
sudo tail -5 /var/log/nginx/udid_error.log

echo "=== Últimos errores de Daphne ==="
sudo journalctl -u udid@0 -n 10 --no-pager
```

### 13.2 Probar la API

```bash
# Desde el servidor mismo
curl -k https://localhost/health

# Debería responder: OK

# Probar endpoint de admin
curl -k https://localhost/admin/

# Debería responder con HTML de la página de login
```

### 13.3 Probar desde Fuera del Servidor

Desde tu computadora local:

```bash
# Reemplazar IP_DEL_SERVIDOR con la IP real
curl -k https://IP_DEL_SERVIDOR/health

# Probar la API
curl -k https://IP_DEL_SERVIDOR/udid/metrics/
```

### 13.4 Probar WebSocket

Puedes usar una herramienta online como [WebSocket King](https://websocketking.com/) o desde la terminal:

```bash
# Instalar websocat (herramienta de línea de comandos para WebSocket)
sudo apt install -y websocat

# Probar conexión WebSocket
websocat -k wss://IP_DEL_SERVIDOR/ws/auth/
```

### 13.5 Abrir Firewall (si es necesario)

```bash
# Si usas UFW (firewall de Ubuntu)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp  # SSH
sudo ufw enable
sudo ufw status
```

---

## 14. Mantenimiento y Monitoreo

### 14.1 Comandos Útiles de Monitoreo

```bash
# Ver uso de recursos en tiempo real
htop

# Ver logs en tiempo real
sudo journalctl -u udid@0 -f

# Ver logs de Nginx
sudo tail -f /var/log/nginx/udid_access.log
sudo tail -f /var/log/nginx/udid_error.log

# Ver conexiones activas
sudo ss -tlnp

# Ver uso de disco
df -h

# Ver uso de memoria
free -h

# Ver procesos de Python/Daphne
ps aux | grep daphne
```

### 14.2 Script de Monitoreo Automático

```bash
# Crear script de monitoreo
sudo nano /opt/udid/health_check.sh
```

Copiar el siguiente contenido:

```bash
#!/bin/bash
# Script de monitoreo de salud del sistema

LOG_FILE="/var/log/udid/health.log"
ALERT_EMAIL="tu@email.com"  # Cambiar por tu email

check_service() {
    if systemctl is-active --quiet $1; then
        echo "✅ $1: OK"
        return 0
    else
        echo "❌ $1: FAILED"
        return 1
    fi
}

check_http() {
    if curl -sk --connect-timeout 5 "$1" > /dev/null 2>&1; then
        echo "✅ HTTP $1: OK"
        return 0
    else
        echo "❌ HTTP $1: FAILED"
        return 1
    fi
}

echo "$(date) - Health Check Started" >> $LOG_FILE

# Verificar servicios
check_service postgresql >> $LOG_FILE
check_service redis-server >> $LOG_FILE
check_service nginx >> $LOG_FILE
check_service celery-worker >> $LOG_FILE
# Nota: celery-beat está deshabilitado por defecto (tareas manuales)
# check_service celery-beat >> $LOG_FILE

for i in 0 1 2 3; do
    check_service udid@$i >> $LOG_FILE
done

# Verificar HTTP
check_http "https://localhost/health" >> $LOG_FILE

echo "$(date) - Health Check Completed" >> $LOG_FILE
echo "---" >> $LOG_FILE
```

Guardar y hacer ejecutable:

```bash
sudo chmod +x /opt/udid/health_check.sh

# Agregar al crontab para ejecutar cada 5 minutos
sudo -u udid crontab -e

# Agregar esta línea:
*/5 * * * * /opt/udid/health_check.sh
```

### 14.3 Actualizar el Proyecto

Cuando necesites actualizar el código:

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Detener servicios
exit  # Volver a root
sudo /opt/udid/manage_services.sh stop

# Cambiar al usuario udid nuevamente
sudo su - udid
cd /opt/udid

# Si usas Git
git pull origin main

# Si actualizas manualmente, copiar archivos nuevos

# Activar entorno virtual
source venv/bin/activate

# Instalar nuevas dependencias (si las hay)
pip install -r requirements.txt

# Aplicar migraciones (si las hay)
python manage.py migrate

# Recolectar archivos estáticos
python manage.py collectstatic --noinput

# Salir y reiniciar servicios
exit
sudo /opt/udid/manage_services.sh start
sudo systemctl restart celery-worker
# Nota: celery-beat está deshabilitado por defecto (tareas manuales)
# sudo systemctl restart celery-beat
sudo systemctl restart nginx

# Verificar estado
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker
```

### 14.4 Backup de Base de Datos

```bash
# Crear script de backup
sudo nano /opt/udid/backup_db.sh
```

```bash
#!/bin/bash
# Script de backup de PostgreSQL

BACKUP_DIR="/var/backups/udid"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/udid_$DATE.sql.gz"

# Crear directorio si no existe
mkdir -p $BACKUP_DIR

# Crear backup
PGPASSWORD="tu_password_seguro" pg_dump -h localhost -U udid_user udid | gzip > $BACKUP_FILE

# Eliminar backups de más de 7 días
find $BACKUP_DIR -name "udid_*.sql.gz" -mtime +7 -delete

echo "Backup creado: $BACKUP_FILE"
```

```bash
sudo chmod +x /opt/udid/backup_db.sh

# Agregar al crontab (backup diario a las 2 AM)
sudo crontab -e
# Agregar: 0 2 * * * /opt/udid/backup_db.sh >> /var/log/udid/backup.log 2>&1
```

---

## 15. Solución de Problemas

### 15.1 Problemas Comunes y Soluciones

#### Error: "Connection refused" al conectar a PostgreSQL

```bash
# Verificar que PostgreSQL está corriendo
sudo systemctl status postgresql

# Si no está corriendo
sudo systemctl start postgresql

# Verificar configuración de acceso
sudo cat /etc/postgresql/*/main/pg_hba.conf | grep -v "^#" | grep -v "^$"
```

#### Error: "Connection refused" al conectar a Redis

```bash
# Verificar que Redis está corriendo
sudo systemctl status redis-server

# Si no está corriendo
sudo systemctl start redis-server

# Verificar que está escuchando
redis-cli ping
```

#### Error 502 Bad Gateway en Nginx

```bash
# Verificar que Daphne está corriendo
sudo /opt/udid/manage_services.sh status

# Ver logs de Daphne
sudo journalctl -u udid@0 -n 50

# Verificar puertos
sudo ss -tlnp | grep 800
```

#### Error de permisos en archivos

```bash
# Corregir permisos del proyecto
sudo chown -R udid:udid /opt/udid
sudo chmod -R 755 /opt/udid
sudo chmod 600 /opt/udid/.env
```

#### Error "ModuleNotFoundError" en Python

```bash
# Asegurarse de que el entorno virtual está activado
cd /opt/udid
source venv/bin/activate

# Reinstalar dependencias
pip install -r requirements.txt
```

#### WebSocket no conecta

```bash
# Verificar configuración de Nginx para WebSocket
sudo nginx -t

# Ver logs de error de Nginx
sudo tail -f /var/log/nginx/udid_error.log

# Verificar que el upgrade header está presente
curl -i -k https://localhost/ws/auth/ \
    -H "Upgrade: websocket" \
    -H "Connection: Upgrade"
```

### 15.2 Comandos de Diagnóstico

```bash
# Ver todos los procesos de Python
ps aux | grep python

# Ver conexiones de red
sudo netstat -tlnp

# Ver uso de memoria por proceso
ps aux --sort=-%mem | head -20

# Ver logs del sistema
sudo journalctl -xe

# Ver espacio en disco
df -h

# Ver inodes (archivos)
df -i
```

### 15.3 Reinicio Completo del Sistema

Si todo falla, reiniciar todos los servicios:

```bash
# Detener todos los servicios
sudo /opt/udid/manage_services.sh stop
sudo systemctl stop celery-worker celery-flower
# Nota: celery-beat está deshabilitado por defecto (tareas manuales)
# sudo systemctl stop celery-beat
sudo systemctl stop nginx
sudo systemctl stop redis-server
sudo systemctl stop postgresql

# Esperar unos segundos
sleep 5

# Iniciar en orden
sudo systemctl start postgresql
sudo systemctl start redis-server
sudo /opt/udid/manage_services.sh start
sudo systemctl start celery-worker
# Nota: celery-beat está deshabilitado por defecto (tareas manuales)
# sudo systemctl start celery-beat
sudo systemctl start celery-flower  # Opcional
sudo systemctl start nginx

# Verificar estado
sudo systemctl status postgresql
sudo systemctl status redis-server
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker
# Nota: celery-beat está deshabilitado por defecto
# sudo systemctl status celery-beat
sudo systemctl status nginx
```

---

## 16. Recomendaciones de Recursos del Servidor

### 16.1 Configuración Recomendada según Carga

> **📝 Nota sobre "Redis Memory":**
> 
> **Redis Memory** se refiere a la cantidad máxima de RAM que Redis puede usar para almacenar datos en memoria. Esta configuración se establece con `maxmemory` en `/etc/redis/redis.conf`.
> 
> **¿Por qué es importante?**
> - Redis almacena datos en memoria para acceso rápido (cache, WebSockets, colas de Celery)
> - Sin límite, Redis podría consumir toda la RAM del servidor
> - Con `maxmemory` configurado, Redis usa la política `allkeys-lru` para eliminar datos antiguos cuando se llena
> 
> **¿Cómo se configura?**
> ```bash
> sudo nano /etc/redis/redis.conf
> # Buscar y modificar:
> maxmemory 2gb  # Ajustar según la RAM disponible del servidor
> maxmemory-policy allkeys-lru  # Eliminar claves menos usadas cuando se llena
> ```
> 
> **Recomendación:** Asignar entre 25-30% de la RAM total del servidor a Redis. Por ejemplo:
> - Servidor con 8GB RAM → Redis Memory: 2GB
> - Servidor con 16GB RAM → Redis Memory: 4GB
> - Servidor con 32GB RAM → Redis Memory: 8GB

#### 🟢 Carga Baja (hasta 500 conexiones simultáneas)

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 2-4 cores |
| **RAM** | 4-8 GB |
| **Disco** | 40 GB SSD |
| **Workers Daphne** | 4 |
| **Redis Memory** | 1 GB |
| **PostgreSQL Connections** | 50 |

#### 🟡 Carga Media (500-2000 conexiones simultáneas)

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 4-8 cores |
| **RAM** | 8-16 GB |
| **Disco** | 80 GB SSD |
| **Workers Daphne** | 8 |
| **Redis Memory** | 2 GB |
| **PostgreSQL Connections** | 100 |

#### 🔴 Carga Alta (2000+ conexiones simultáneas)

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 8-16 cores |
| **RAM** | 16-32 GB |
| **Disco** | 150+ GB NVMe |
| **Workers Daphne** | 16+ |
| **Redis Memory** | 4+ GB |
| **PostgreSQL Connections** | 200+ |

### 16.2 Ajustes de Rendimiento

#### Para PostgreSQL (alta carga):

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

```conf
# Ajustes de memoria
shared_buffers = 2GB              # 25% de la RAM total
effective_cache_size = 6GB        # 75% de la RAM total
work_mem = 256MB
maintenance_work_mem = 512MB

# Conexiones
max_connections = 200

# WAL
wal_buffers = 64MB
checkpoint_completion_target = 0.9
```

#### Para Redis (alta carga):

```bash
sudo nano /etc/redis/redis.conf
```

```conf
maxmemory 4gb
maxmemory-policy allkeys-lru
tcp-keepalive 300
timeout 0
```

#### Para Nginx (alta carga):

```bash
sudo nano /etc/nginx/nginx.conf
```

```nginx
worker_processes auto;
worker_connections 4096;
multi_accept on;
use epoll;
```

### 16.3 Monitoreo de Recursos

Instalar herramientas de monitoreo:

```bash
# htop para monitoreo en tiempo real
sudo apt install -y htop

# iotop para monitoreo de disco
sudo apt install -y iotop

# Netdata para dashboard web de monitoreo (opcional)
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

---

## 📝 Resumen de Comandos Importantes

```bash
# === GESTIÓN DE SERVICIOS ===
sudo /opt/udid/manage_services.sh start     # Iniciar aplicación Daphne
sudo /opt/udid/manage_services.sh stop      # Detener aplicación Daphne
sudo /opt/udid/manage_services.sh restart   # Reiniciar aplicación Daphne
sudo /opt/udid/manage_services.sh status    # Ver estado Daphne

sudo systemctl restart nginx                # Reiniciar Nginx
sudo systemctl restart postgresql           # Reiniciar PostgreSQL
sudo systemctl restart redis-server         # Reiniciar Redis
sudo systemctl restart celery-worker        # Reiniciar Celery Worker
# Nota: celery-beat está deshabilitado por defecto (tareas manuales)
# sudo systemctl restart celery-beat         # Reiniciar Celery Beat (si está activo)
sudo systemctl restart celery-flower        # Reiniciar Flower (opcional)

# === LOGS ===
sudo journalctl -u udid@0 -f               # Ver logs de Daphne
sudo journalctl -u celery-worker -f        # Ver logs de Celery Worker
# Nota: celery-beat está deshabilitado por defecto
# sudo journalctl -u celery-beat -f        # Ver logs de Celery Beat (si está activo)
sudo tail -f /var/log/nginx/udid_error.log # Ver errores de Nginx
sudo tail -f /var/log/udid/celery-worker.log  # Ver logs de Worker
# sudo tail -f /var/log/udid/celery-beat.log    # Ver logs de Beat (si está activo)

# === DJANGO ===
cd /opt/udid && source venv/bin/activate   # Activar entorno
python manage.py migrate                    # Aplicar migraciones
python manage.py collectstatic --noinput   # Recolectar estáticos
python manage.py createsuperuser           # Crear admin

# === CELERY ===
celery -A ubuntu inspect active            # Ver tareas activas
celery -A ubuntu inspect stats              # Ver estadísticas
celery -A ubuntu inspect registered         # Ver tareas registradas

# === VERIFICACIÓN ===
curl -k https://localhost/health           # Verificar salud
redis-cli ping                             # Verificar Redis
sudo ss -tlnp | grep 800                   # Ver puertos Daphne
sudo ss -tlnp | grep 5555                  # Ver puerto Flower (opcional)
```

---

## ✅ Lista de Verificación Final

Antes de considerar el despliegue completo, verificar:

- [ ] PostgreSQL instalado y configurado
- [ ] Redis instalado y configurado
- [ ] Proyecto copiado a `/opt/udid`
- [ ] Entorno virtual creado y dependencias instaladas
- [ ] Archivo `.env` configurado con todas las variables (incluyendo Celery)
- [ ] Migraciones aplicadas
- [ ] Archivos estáticos recolectados
- [ ] Superusuario creado
- [ ] Certificado SSL configurado
- [ ] Nginx configurado y funcionando
- [ ] Servicios systemd de Daphne creados y habilitados
- [ ] Servicios systemd de Celery Worker creado y habilitado
- [ ] Celery Beat creado pero NO habilitado (tareas manuales por defecto)
- [ ] Flower configurado (opcional pero recomendado)
- [ ] Tareas de mantenimiento en crontab configuradas
- [ ] Firewall configurado
- [ ] Pruebas de API exitosas
- [ ] Pruebas de WebSocket exitosas
- [ ] Verificación de ejecución manual de tareas de Celery

---

**¡Felicidades! 🎉** Si has llegado hasta aquí y todo funciona, tu servidor UDID está listo para producción.

Para soporte adicional, revisar los logs y la documentación de cada componente:
- [Django Documentation](https://docs.djangoproject.com/)
- [Django Channels](https://channels.readthedocs.io/)
- [Nginx Documentation](https://nginx.org/en/docs/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Redis Documentation](https://redis.io/documentation)

