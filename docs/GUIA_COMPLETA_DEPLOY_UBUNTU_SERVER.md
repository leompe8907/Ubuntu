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
12. [Configuración de Celery (Tareas Automáticas y Manuales)](#12-configuración-de-celery-tareas-automáticas-y-manuales)
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
# Para 16GB RAM, usar 4GB para Redis
# Para 32GB RAM, usar 8GB para Redis
# Para 64GB RAM / 32 cores, usar 16GB para Redis
# Para 120GB RAM / 64 cores, usar 30GB para Redis
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
source env/bin/activate
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

### 7.4 Variables de Entorno según Configuración de Hardware

Según tu configuración de hardware, ajusta estas variables en el archivo `.env`:

#### Configuración 1: 32 GB RAM / 32 cores / 1 TB SSD

```env
# ============================================================================
# CONFIGURACIÓN OPTIMIZADA PARA 32GB RAM / 32 CORES / 1TB SSD
# ============================================================================

# Redis - Conexiones y capacidad
REDIS_MAX_CONNECTIONS=300
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_TIMEOUT=5
REDIS_RETRY_ON_TIMEOUT=True

# Channel Layers (WebSockets)
CHANNEL_LAYERS_CAPACITY=5000
CHANNEL_LAYERS_EXPIRY=10
CHANNEL_LAYERS_GROUP_EXPIRY=1800

# Concurrencia y colas
GLOBAL_SEMAPHORE_SLOTS=3000
REQUEST_QUEUE_MAX_SIZE=5000
REQUEST_QUEUE_MAX_WAIT_TIME=10

# Cache
CACHE_SOCKET_CONNECT_TIMEOUT=5
CACHE_SOCKET_TIMEOUT=5
CACHE_MAX_CONNECTIONS=50
CACHE_KEY_PREFIX=udid_prod
CACHE_TIMEOUT=300

# Celery Workers
CELERY_WORKER_PREFETCH_MULTIPLIER=8
CELERY_WORKER_CONCURRENCY=16
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000
```

#### Configuración 2: 64 GB RAM / 32 cores / 1 TB SSD

```env
# ============================================================================
# CONFIGURACIÓN OPTIMIZADA PARA 64GB RAM / 32 CORES / 1TB SSD
# ============================================================================

# Redis - Conexiones y capacidad
REDIS_MAX_CONNECTIONS=400
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_TIMEOUT=5
REDIS_RETRY_ON_TIMEOUT=True

# Channel Layers (WebSockets)
CHANNEL_LAYERS_CAPACITY=10000
CHANNEL_LAYERS_EXPIRY=10
CHANNEL_LAYERS_GROUP_EXPIRY=1800

# Concurrencia y colas
GLOBAL_SEMAPHORE_SLOTS=5000
REQUEST_QUEUE_MAX_SIZE=10000
REQUEST_QUEUE_MAX_WAIT_TIME=10

# Cache
CACHE_SOCKET_CONNECT_TIMEOUT=5
CACHE_SOCKET_TIMEOUT=5
CACHE_MAX_CONNECTIONS=100
CACHE_KEY_PREFIX=udid_prod
CACHE_TIMEOUT=300

# Celery Workers
CELERY_WORKER_PREFETCH_MULTIPLIER=10
CELERY_WORKER_CONCURRENCY=24
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000
```

#### Configuración 3: 124 GB RAM / 64 cores / 1 TB SSD

```env
# ============================================================================
# CONFIGURACIÓN OPTIMIZADA PARA 124GB RAM / 64 CORES / 1TB SSD
# ============================================================================

# Redis - Conexiones y capacidad
REDIS_MAX_CONNECTIONS=600
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_SOCKET_TIMEOUT=5
REDIS_RETRY_ON_TIMEOUT=True

# Channel Layers (WebSockets)
CHANNEL_LAYERS_CAPACITY=20000
CHANNEL_LAYERS_EXPIRY=10
CHANNEL_LAYERS_GROUP_EXPIRY=1800

# Concurrencia y colas
GLOBAL_SEMAPHORE_SLOTS=10000
REQUEST_QUEUE_MAX_SIZE=20000
REQUEST_QUEUE_MAX_WAIT_TIME=10

# Cache
CACHE_SOCKET_CONNECT_TIMEOUT=5
CACHE_SOCKET_TIMEOUT=5
CACHE_MAX_CONNECTIONS=150
CACHE_KEY_PREFIX=udid_prod
CACHE_TIMEOUT=300

# Celery Workers
CELERY_WORKER_PREFETCH_MULTIPLIER=16
CELERY_WORKER_CONCURRENCY=48
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000
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
# Asegurarse de estar en el directorio correcto con el entorno virtual activado
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
    # Configuración estándar: 4 instancias
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
    
    # Configuración optimizada para 64-120GB RAM / 32-64 cores: 40-60 instancias
    # Descomentar estas líneas y comentar las 4 anteriores si usas la configuración optimizada:
    # Para 64GB RAM / 32 cores: usar 40 instancias (puertos 8000-8039)
    # Para 120GB RAM / 64 cores: usar 60 instancias (puertos 8000-8059)
    # server 127.0.0.1:8000;
    # server 127.0.0.1:8001;
    # ... (continuar hasta 8039 para 40 instancias o 8059 para 60 instancias)
    # server 127.0.0.1:8039;  # Para 40 instancias
    # server 127.0.0.1:8059;  # Para 60 instancias
    
    # Mantener conexiones abiertas (aumentado para alta carga)
    keepalive 128;
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
    # ⚠️ IMPORTANTE: Si aún no has creado los certificados SSL (Sección 10),
    # comenta temporalmente estas dos líneas para evitar errores al recargar Nginx:
    # ssl_certificate /etc/nginx/ssl/udid.crt;
    # ssl_certificate_key /etc/nginx/ssl/udid.key;
    # Después de crear los certificados, descomenta estas líneas.
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

> ⚠️ **CRÍTICO - LEE ANTES DE CONTINUAR:**
> 
> La configuración de Nginx incluye referencias a certificados SSL (`/etc/nginx/ssl/udid.crt` y `/etc/nginx/ssl/udid.key`).
> 
> **Tienes DOS opciones:**
> 
> 1. **Opción A (Recomendada):** Crear los certificados SSL primero (ir a la Sección 10) y luego volver aquí para habilitar el sitio.
> 2. **Opción B (Temporal):** Si quieres probar sin SSL primero, comenta temporalmente las líneas SSL en `/etc/nginx/sites-available/udid`:
>    ```nginx
>    # ssl_certificate /etc/nginx/ssl/udid.crt;
>    # ssl_certificate_key /etc/nginx/ssl/udid.key;
>    ```
>    Y también cambia `listen 443 ssl http2;` por `listen 80;` (HTTP sin SSL).
>    Después de crear los certificados, descomenta y vuelve a `listen 443 ssl http2;`.

```bash
# Crear enlace simbólico para habilitar el sitio
sudo ln -s /etc/nginx/sites-available/udid /etc/nginx/sites-enabled/

# Deshabilitar el sitio por defecto
sudo rm /etc/nginx/sites-enabled/default

# Verificar configuración de Nginx
sudo nginx -t

# Debería mostrar: syntax is ok / test is successful
# Si muestra error sobre certificados SSL, ve a la Sección 10 para crearlos primero

# ⚠️ IMPORTANTE: Recargar Nginx para que cargue la nueva configuración
# Sin este paso, Nginx seguirá usando la configuración anterior en memoria
# Si obtienes error "cannot load certificate", significa que los certificados no existen
# Ve a la Sección 10 para crearlos primero
sudo systemctl reload nginx
# O si prefieres reiniciar completamente:
# sudo systemctl restart nginx

# Verificar que Nginx está corriendo correctamente
sudo systemctl status nginx
# Si ves errores sobre certificados SSL, ve a la Sección 10
```

---

## 10. Configuración de SSL/HTTPS

> ⚠️ **IMPORTANTE:** Si ya habilitaste el sitio de Nginx (Sección 9.3) y obtuviste un error sobre certificados SSL, esta es la sección que necesitas. Crea los certificados aquí y luego vuelve a recargar Nginx con `sudo systemctl reload nginx`.

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

# Verificar que los archivos se crearon correctamente
ls -la /etc/nginx/ssl/

# Deberías ver:
# -rw-r--r-- 1 root root ... udid.crt
# -rw------- 1 root root ... udid.key

# ⚠️ IMPORTANTE: Si ya habilitaste el sitio de Nginx (Sección 9.3),
# ahora debes recargar Nginx para que cargue los certificados:
sudo nginx -t  # Verificar configuración
sudo systemctl reload nginx  # Recargar Nginx
sudo systemctl status nginx  # Verificar que no hay errores
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
# Para instancias 0-9: puertos 8000-8009
# Para instancias 10-19: puertos 8010-8019
# Usar script wrapper para calcular puerto correctamente
ExecStart=/bin/bash -c 'PORT=$((8000 + %i)); exec /opt/udid/env/bin/daphne -b 127.0.0.1 -p $PORT --access-log - --proxy-headers -t 60 --websocket_timeout 300 ubuntu.asgi:application'

# Reinicio automático
Restart=always
RestartSec=3

# Limitar recursos (ajustar según necesidad)
# Configuración estándar: 1GB por instancia
# Configuración optimizada para 64-120GB RAM: 512MB-1GB por instancia (más instancias)
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

# Número de instancias (ajustar según CPU cores y carga esperada)
# Configuración estándar: 4 instancias (hasta 1000 requests simultáneos)
# Configuración optimizada para 64GB RAM / 32 cores: 40 instancias
# Configuración optimizada para 120GB RAM / 64 cores: 60 instancias
INSTANCES=4

# Para configuración optimizada de 64GB RAM / 32 cores, cambiar a:
# INSTANCES=40

# Para configuración optimizada de 120GB RAM / 64 cores, cambiar a:
# INSTANCES=60

# Función para obtener el puerto según el número de instancia
get_port() {
    local instance=$1
    if [ $instance -lt 10 ]; then
        echo "800$instance"
    else
        echo "80$instance"
    fi
}

case "$1" in
    start)
        echo "Iniciando $INSTANCES instancias de UDID..."
        for i in $(seq 0 $((INSTANCES-1))); do
            sudo systemctl start udid@$i
            PORT=$(get_port $i)
            echo "  Instancia $i iniciada (puerto $PORT)"
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
            PORT=$(get_port $i)
            echo "--- Instancia $i (puerto $PORT) ---"
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

## 12. Configuración de Celery (Tareas Automáticas y Manuales)

### 📋 Información sobre las Tareas de Celery

El proyecto usa **Celery** para ejecutar tareas periódicas en background de forma asíncrona y escalable. **Las tareas periódicas se ejecutan automáticamente según su periodicidad configurada**, y también puedes ejecutarlas manualmente cuando lo necesites.

### 🔄 Flujo Completo de Configuración (Resumen)

**Orden de ejecución obligatorio:**

1. **PASO 1**: Crear y configurar el script `ejecutar_sync_tasks.py` (sección 12.6.1)
2. **PASO 2**: Ejecutar `execute_sync_tasks()` UNA SOLA VEZ con el script de cron (sección 12.6.2)
   - Esta sincronización descarga TODA la información inicial desde Panaccess
   - Se ejecuta de forma síncrona (puedes ver el progreso)
   - El script verifica si ya se ejecutó para evitar duplicados
3. **PASO 3**: Iniciar Celery Worker (sección 12.5, Paso 2)
   - Necesario para ejecutar las tareas periódicas de Celery
4. **PASO 4**: Activar Celery Beat (sección 12.5, Paso 3 y 12.7)
   - Solo después de que `execute_sync_tasks()` se haya completado exitosamente
   - Beat ejecutará las tareas periódicas automáticamente

**⚠️ IMPORTANTE**: NO activar Celery Beat hasta que `execute_sync_tasks()` se haya completado. Las tareas periódicas necesitan datos base en la BD.

**Tareas configuradas:**

| Tarea                                | Periodicidad                     | Propósito                                  | Prioridad | Método |
|--------------------------------------|----------------------------------|--------------------------------------------|-----------|--------|
| `execute_sync_tasks()` (cron.py)     | **MANUAL - UNA VEZ** (al iniciar) | **OBLIGATORIA**: Sincronización inicial completa. Se ejecuta con script de cron ANTES de activar Celery Beat | 🔴 CRÍTICA | Cron script |
| `check_and_sync_smartcards_monthly`  | Día 28 de cada mes a las 3:00 AM | Verifica y descarga nuevas smartcards desde Panaccess | 🟡 Automática | Celery Beat |
| `check_and_sync_subscribers_periodic`| Cada 5 minutos                   | Detecta nuevos suscriptores, descarga credenciales y actualiza smartcards | 🟡 Automática | Celery Beat |
| `validate_and_sync_all_data_daily`   | Cada día a las 22:00 (10:00 PM)  | Valida y corrige todos los registros existentes comparándolos con Panaccess | 🟡 Automática | Celery Beat |

**⚠️ IMPORTANTE - Orden de Ejecución:**
1. **PRIMERO**: Ejecutar `execute_sync_tasks()` usando el script de cron (`ejecutar_sync_tasks.py`) UNA VEZ cuando se despliega el sistema por primera vez
2. **SEGUNDO**: Iniciar Celery Worker (necesario para las tareas periódicas)
3. **TERCERO**: Activar Celery Beat para que las tareas periódicas se ejecuten automáticamente según su periodicidad
4. Las tareas automáticas de Celery dependen de que `execute_sync_tasks()` se haya ejecutado primero para tener datos base

**Componentes de Celery:**
- **Celery Worker**: Ejecuta las tareas en background (SIEMPRE debe estar activo)
- **Celery Beat**: Programa y ejecuta tareas periódicas automáticamente (DEBE estar activo para tareas automáticas)
- **Flower** (opcional): Interfaz web para monitorear tareas

**¿Cómo funciona?**
- **Sincronización inicial**: Se ejecuta con script de cron (`ejecutar_sync_tasks.py`) que llama a `execute_sync_tasks()` de `cron.py` - UNA SOLA VEZ
- **Tareas automáticas**: Celery Beat programa y ejecuta las tareas periódicas según su periodicidad configurada
- **Tareas manuales**: Puedes ejecutar cualquier tarea de Celery manualmente cuando lo necesites
- Las tareas de Celery se envían a Redis (broker)
- Celery Worker toma las tareas de Redis y las ejecuta
- Los resultados se almacenan en Redis (backend)
- **Mecanismo de lock**: Las tareas tienen un sistema de bloqueo para evitar ejecuciones simultáneas

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
# ⚠️ IMPORTANTE: Celery NO acepta "--concurrency auto", debe ser un número entero
# Configuración estándar (servidor pequeño): --concurrency 2
# Configuración optimizada para 32GB RAM / 32 cores: --concurrency 16
# Configuración optimizada para 64GB RAM / 32 cores: --concurrency 24
# Configuración optimizada para 124GB RAM / 64 cores: --concurrency 48
ExecStart=/opt/udid/env/bin/celery -A ubuntu worker \
    --loglevel=info \
    --logfile=/var/log/udid/celery-worker.log \
    --pidfile=/run/udid/celery-worker.pid \
    --concurrency=2

# Comando para detener
ExecStop=/bin/kill -s TERM $MAINPID
PIDFile=/run/udid/celery-worker.pid

# Reinicio automático
Restart=always
RestartSec=3

# Limitar recursos según configuración
# Configuración estándar: 2GB
# Configuración optimizada para 32GB RAM / 32 cores: 2GB
# Configuración optimizada para 64GB RAM / 32 cores: 4GB
# Configuración optimizada para 124GB RAM / 64 cores: 4GB
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

### 12.3 Crear Servicio Systemd para Celery Beat (Requerido para Tareas Automáticas)

> ✅ **IMPORTANTE:** Celery Beat es **REQUERIDO** para que las tareas periódicas se ejecuten automáticamente. Debe estar activo junto con el Worker.

Celery Beat programa y ejecuta las tareas periódicas automáticamente según la configuración en `ubuntu/settings.py`:

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
    --pidfile=/run/udid/celery-beat.pid \
    --schedule=/run/udid/celerybeat-schedule

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
# ⚠️ IMPORTANTE: Usar /run/udid (no /var/run/udid) para evitar warnings de systemd
sudo mkdir -p /run/udid
sudo chown udid:udid /run/udid

# Crear directorio de logs (si no existe)
sudo mkdir -p /var/log/udid
sudo chown udid:udid /var/log/udid
```

### 12.5 Iniciar y Habilitar Servicios de Celery

**⚠️ IMPORTANTE - Orden de Configuración:**

1. **PRIMERO**: Ejecutar `execute_sync_tasks()` con script de cron (ver sección 12.6) - UNA SOLA VEZ
2. **SEGUNDO**: Iniciar Celery Worker (necesario para las tareas periódicas)
3. **TERCERO**: Después de completar la sincronización inicial, iniciar Beat para tareas automáticas

```bash
# Recargar systemd
sudo systemctl daemon-reload

# ========================================================================
# PASO 1: Ejecutar execute_sync_tasks() con script de cron (ver sección 12.6)
# ========================================================================
# 🔴 CRÍTICO: Ejecutar la sincronización inicial ANTES de activar Celery
# Ir a la sección 12.6 para ejecutar execute_sync_tasks() con el script de cron
# Esta sincronización descarga TODA la información inicial desde Panaccess
# IMPORTANTE: Esta tarea se ejecuta UNA SOLA VEZ, no periódicamente

# ========================================================================
# PASO 2: Iniciar Celery Worker (para tareas periódicas)
# ========================================================================
# Iniciar Worker (necesario para ejecutar tareas periódicas de Celery)
sudo systemctl start celery-worker

# Habilitar inicio automático del Worker
sudo systemctl enable celery-worker

# Verificar estado del Worker
sudo systemctl status celery-worker
# Debe mostrar: "active (running)"

# ========================================================================
# PASO 3: Después de completar execute_sync_tasks(), activar Beat
# ========================================================================
# Iniciar Beat (solo después de ejecutar execute_sync_tasks())
# Beat ejecutará las tareas periódicas automáticamente
sudo systemctl start celery-beat

# Habilitar inicio automático de Beat
sudo systemctl enable celery-beat

# Verificar estado de Beat
sudo systemctl status celery-beat
# Debe mostrar: "active (running)"
```

### 12.5.1 Verificar Configuración de Tareas Periódicas

Las tareas periódicas están configuradas en `ubuntu/settings.py` en la variable `CELERY_BEAT_SCHEDULE`:

```python
CELERY_BEAT_SCHEDULE = {
    'check-and-sync-smartcards-monthly': {
        'task': 'udid.tasks.check_and_sync_smartcards_monthly',
        'schedule': crontab(day_of_month='28', hour=3, minute=0),  # Día 28 a las 3:00 AM
    },
    'check-and-sync-subscribers-periodic': {
        'task': 'udid.tasks.check_and_sync_subscribers_periodic',
        'schedule': 300.0,  # Cada 5 minutos
    },
    'validate-and-sync-all-data-daily': {
        'task': 'udid.tasks.validate_and_sync_all_data_daily',
        'schedule': crontab(hour=22, minute=0),  # Cada día a las 22:00
    },
}
```

**⚠️ IMPORTANTE:** `execute_sync_tasks()` NO está en el schedule de Celery porque:
- Se ejecuta con un script de cron (`ejecutar_sync_tasks.py`) UNA SOLA VEZ
- Es OBLIGATORIA ejecutarla ANTES de activar Celery Beat
- Las tareas automáticas de Celery dependen de que esta sincronización se haya ejecutado primero
- Si activas Beat sin ejecutar `execute_sync_tasks()`, las tareas automáticas no tendrán datos base con los que trabajar
- El script verifica si ya se ejecutó para evitar ejecuciones duplicadas

**Mecanismo de Lock:**
- Todas las tareas tienen un sistema de bloqueo para evitar ejecuciones simultáneas
- Si una tarea está en ejecución, las demás esperarán hasta que termine
- Esto previene conflictos y sobrecarga del sistema

### 12.6 Ejecutar Sincronización Inicial OBLIGATORIA: execute_sync_tasks()

> 🔴 **CRÍTICO**: Esta sincronización DEBE ejecutarse PRIMERO antes de activar las tareas automáticas de Celery. Es la sincronización inicial completa que descarga todos los datos desde Panaccess.

**¿Por qué es obligatoria?**
- Descarga TODA la información inicial desde Panaccess (suscriptores, smartcards, credenciales)
- Las tareas automáticas de Celery dependen de tener datos base en la BD
- Sin esta sincronización, las tareas automáticas no tendrán datos con los que trabajar

**⚠️ IMPORTANTE:**
- Ejecutar SOLO UNA VEZ cuando se despliega el sistema por primera vez
- Se ejecuta con un script de cron (`ejecutar_sync_tasks.py`), NO con Celery
- El script verifica si ya se ejecutó para evitar ejecuciones duplicadas
- Después de ejecutarla, puedes activar Celery Beat para las tareas periódicas

**Tareas automáticas de Celery (se ejecutan después de execute_sync_tasks()):**
- `check_and_sync_smartcards_monthly`: Verifica y descarga nuevas smartcards (normalmente se ejecuta el día 28 de cada mes)
- `check_and_sync_subscribers_periodic`: Detecta nuevos suscriptores y actualiza datos (normalmente cada 5 minutos)
- `validate_and_sync_all_data_daily`: Valida y corrige todos los registros (normalmente cada día a las 22:00)

### 12.6.1 Crear Script para Ejecutar execute_sync_tasks()

Primero, creamos el script que ejecutará `execute_sync_tasks()` desde cron:

```bash
# Crear el script
sudo nano /opt/udid/ejecutar_sync_tasks.py
```

El script ya está incluido en el proyecto. Si necesitas crearlo manualmente, copia el contenido del archivo `ejecutar_sync_tasks.py` en la raíz del proyecto.

```bash
# Hacer ejecutable
sudo chmod +x /opt/udid/ejecutar_sync_tasks.py
sudo chown udid:udid /opt/udid/ejecutar_sync_tasks.py

# Crear directorio de logs (si no existe)
sudo mkdir -p /var/log/udid
sudo chown udid:udid /var/log/udid
```

### 12.6.2 Ejecutar execute_sync_tasks() UNA SOLA VEZ

#### Método 1: Ejecutar con Script de Cron (Recomendado)

Este es el método recomendado porque:
- Ejecuta la sincronización de forma síncrona (puedes ver el progreso)
- Verifica si ya se ejecutó para evitar duplicados
- Guarda información de la ejecución para referencia futura
- No requiere Celery Worker activo

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Ejecutar el script (verifica si ya se ejecutó)
python ejecutar_sync_tasks.py

# Si necesitas forzar ejecución aunque ya se haya ejecutado:
# python ejecutar_sync_tasks.py --force

# Verificar si ya se ejecutó (sin ejecutar):
# python ejecutar_sync_tasks.py --check
```

El script mostrará el progreso en tiempo real y guardará la información en `/var/log/udid/sync_tasks_completed.json`.

#### Método 2: Ejecutar desde el Shell de Django

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Abrir shell de Django
python manage.py shell
```

Dentro del shell de Python:

```python
# Importar la función de cron.py
from udid.cron import execute_sync_tasks

# Ejecutar la sincronización (se ejecuta de forma síncrona)
result = execute_sync_tasks()

# Ver el resultado
print(f"Éxito: {result['success']}")
print(f"Mensaje: {result['message']}")
print(f"Session ID: {result.get('session_id')}")

# Ver detalles por tarea
for task_name, task_result in result['tasks'].items():
    status = "✅" if task_result['success'] else "❌"
    print(f"{status} {task_name}: {task_result['message']}")

# Salir del shell
exit()
```

#### Método 3: Ejecutar desde la Línea de Comandos

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid

# Activar entorno virtual
source env/bin/activate

# Ejecutar directamente
python -c "
from udid.cron import execute_sync_tasks
result = execute_sync_tasks()
print(f'Éxito: {result[\"success\"]}')
print(f'Mensaje: {result[\"message\"]}')
for task_name, task_result in result['tasks'].items():
    status = '✅' if task_result['success'] else '❌'
    print(f'{status} {task_name}: {task_result[\"message\"]}')
"
```

#### Verificar el Progreso de la Sincronización

**Si usas el script de cron (Método 1):**
- El script muestra el progreso en tiempo real en la terminal
- Los logs se guardan automáticamente en `/var/log/udid/sync_tasks_completed.json`
- Puedes ver los logs de Django en `/opt/udid/server.log`

**Ver logs de la sincronización:**

```bash
# Ver logs de Django (donde se registra el progreso)
sudo tail -f /opt/udid/server.log | grep -E "\[SYNC\]|\[UPDATE_SUBSCRIBERS\]"

# Ver información de ejecución guardada
cat /var/log/udid/sync_tasks_completed.json | python -m json.tool

# Verificar si ya se ejecutó
python ejecutar_sync_tasks.py --check
```

#### Verificar que la Sincronización se Completó

```bash
# Cambiar al usuario udid
sudo su - udid
cd /opt/udid
source env/bin/activate

# Verificar información de ejecución
python ejecutar_sync_tasks.py --check

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

**Ver información detallada de la ejecución:**

```bash
# Ver archivo JSON con información de ejecución
cat /var/log/udid/sync_tasks_completed.json | python -m json.tool
```

#### Solución de Problemas

**El script no ejecuta (ya se ejecutó antes):**

```bash
# Verificar si ya se ejecutó
python ejecutar_sync_tasks.py --check

# Si necesitas ejecutarla nuevamente (por ejemplo, después de limpiar la BD)
python ejecutar_sync_tasks.py --force
```

**La sincronización falla con errores de autenticación:**

```bash
# Verificar que las credenciales de Panaccess están correctas en .env
cat /opt/udid/.env | grep -E "(url_panaccess|username|password|api_token)"

# Verificar que puedes conectarte a Panaccess
# (revisar logs para ver el error específico)
tail -f /opt/udid/server.log | grep -E "\[SYNC\]|ERROR"
```

**La sincronización tarda mucho tiempo:**
- Esto es normal si hay muchos registros (10,000+ smartcards pueden tomar 8-9 horas)
- El script muestra el progreso en tiempo real
- No interrumpir la sincronización, dejar que complete
- Puedes verificar el progreso en los logs: `tail -f /opt/udid/server.log | grep "\[SYNC\]"`

**Verificar que la sincronización se completó correctamente:**

```bash
# Ver información de ejecución guardada
cat /var/log/udid/sync_tasks_completed.json | python -m json.tool

# Ver logs de Django
grep "\[SYNC\].*finalizada\|completada\|success" /opt/udid/server.log | tail -5

# Verificar estado
python ejecutar_sync_tasks.py --check
```

**Error al crear el archivo de marcador:**

```bash
# Verificar permisos del directorio
ls -la /var/log/udid/

# Si no existe o no tiene permisos, crearlo
sudo mkdir -p /var/log/udid
sudo chown udid:udid /var/log/udid
```

### 12.7 Activar Tareas Automáticas con Celery Beat

> ✅ **IMPORTANTE**: Después de ejecutar `execute_sync_tasks()` exitosamente, debes activar Celery Beat para que las tareas periódicas se ejecuten automáticamente.

**Tareas periódicas que se ejecutarán automáticamente:**
- `check_and_sync_subscribers_periodic`: Cada 5 minutos
- `check_and_sync_smartcards_monthly`: Día 28 de cada mes a las 3:00 AM
- `validate_and_sync_all_data_daily`: Cada día a las 22:00 (10:00 PM)

**Activar Celery Beat (después de ejecutar execute_sync_tasks()):**

```bash
# Verificar que execute_sync_tasks() se completó exitosamente
python ejecutar_sync_tasks.py --check

# Si la sincronización fue exitosa, activar Beat
sudo systemctl start celery-beat

# Habilitar inicio automático
sudo systemctl enable celery-beat

# Verificar que está corriendo
sudo systemctl status celery-beat
# Debe mostrar: "active (running)"

# Ver logs en tiempo real
sudo journalctl -u celery-beat -f
```

**Verificar que las tareas periódicas se están ejecutando:**

```bash
# Ver logs del Worker para ver tareas ejecutándose
sudo tail -f /var/log/udid/celery-worker.log

# Ver tareas programadas en Beat
sudo journalctl -u celery-beat | grep "Scheduler: Sending"

# Verificar estado de Beat
sudo systemctl status celery-beat
```

**Para desactivar las tareas automáticas temporalmente:**

```bash
# Detener Celery Beat (las tareas periódicas dejarán de ejecutarse)
sudo systemctl stop celery-beat

# Deshabilitar inicio automático
sudo systemctl disable celery-beat

# Para reactivarlas más tarde:
sudo systemctl start celery-beat
sudo systemctl enable celery-beat
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

> ⚠️ **NOTA IMPORTANTE**: El script `ejecutar_sync_tasks.py` NO debe programarse en crontab para ejecución periódica. Se ejecuta UNA SOLA VEZ manualmente cuando se despliega el sistema. El script tiene protección para evitar ejecuciones duplicadas.

Aunque Celery maneja las tareas principales de sincronización, algunas tareas de mantenimiento se ejecutan con crontab:

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
# Nota: El backup se hace automáticamente en el postrotate de logrotate
0 4 * * 0 /usr/sbin/logrotate /etc/logrotate.d/udid

# Backup automático de logs (diario a las 2 AM)
# El script verifica tamaño y hace backup si es necesario
0 2 * * * /opt/udid/backup_logs.sh auto >> /var/log/udid/backup_logs.log 2>&1

# Limpieza de backups antiguos (semanal, domingos a las 3 AM)
0 3 * * 0 /opt/udid/backup_logs.sh cleanup >> /var/log/udid/backup_logs.log 2>&1
```

**⚠️ NO agregar `ejecutar_sync_tasks.py` aquí:**
- El script `ejecutar_sync_tasks.py` se ejecuta UNA SOLA VEZ manualmente
- Tiene protección para evitar ejecuciones duplicadas
- Si lo programas en crontab, se ejecutará periódicamente y puede causar problemas
- Para ejecutarlo, usa: `python ejecutar_sync_tasks.py` (manual)

Guardar y salir.

### 12.10 Configurar Sistema de Backup y Rotación de Logs

El sistema incluye un script de backup automático que:
- **Hace backup cuando los logs alcanzan 100MB** (configurable)
- **Hace backup periódico** (diario a las 2 AM)
- **Guarda backups en `/var/backups/udid/logs/`**
- **Retiene backups por 30 días** (configurable)
- **Comprime los backups** para ahorrar espacio

#### 12.10.1 Instalar Script de Backup de Logs

```bash
# Copiar el script de backup al servidor
sudo cp backup_logs.sh /opt/udid/backup_logs.sh

# Hacer ejecutable
sudo chmod +x /opt/udid/backup_logs.sh
sudo chown udid:udid /opt/udid/backup_logs.sh

# Crear directorio de backups
sudo mkdir -p /var/backups/udid/logs
sudo chown udid:udid /var/backups/udid/logs

# Probar el script
sudo -u udid /opt/udid/backup_logs.sh test
```

#### 12.10.2 Configurar Backup Automático en Crontab

```bash
# Editar crontab del usuario udid
sudo crontab -u udid -e
```

Agregar las siguientes líneas:

```bash
# Backup automático de logs (diario a las 2 AM)
0 2 * * * /opt/udid/backup_logs.sh auto >> /var/log/udid/backup_logs.log 2>&1

# Limpieza de backups antiguos (semanal, domingos a las 3 AM)
0 3 * * 0 /opt/udid/backup_logs.sh cleanup >> /var/log/udid/backup_logs.log 2>&1
```

#### 12.10.3 Configurar Rotación de Logs (logrotate)

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
        # Hacer backup antes de rotar
        /opt/udid/backup_logs.sh force > /dev/null 2>&1 || true
        # Recargar servicios
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
        # Hacer backup antes de rotar
        /opt/udid/backup_logs.sh force > /dev/null 2>&1 || true
        # Reiniciar servicios Daphne
        /opt/udid/manage_services.sh restart > /dev/null 2>&1 || true
    endscript
}
```

Guardar y salir.

#### 12.10.4 Comandos Útiles del Script de Backup

```bash
# Verificar tamaño de logs y hacer backup si es necesario
sudo -u udid /opt/udid/backup_logs.sh auto

# Forzar backup inmediato de todos los logs
sudo -u udid /opt/udid/backup_logs.sh force

# Ver estadísticas de backups
sudo -u udid /opt/udid/backup_logs.sh stats

# Limpiar backups antiguos manualmente
sudo -u udid /opt/udid/backup_logs.sh cleanup

# Modo de prueba (no hace backup real)
sudo -u udid /opt/udid/backup_logs.sh test
```

#### 12.10.5 Configuración del Script de Backup

Puedes personalizar el script editando las variables al inicio de `/opt/udid/backup_logs.sh`:

```bash
# Tamaño máximo antes de forzar backup (en MB)
MAX_SIZE_MB=100

# Retención de backups (días)
RETENTION_DAYS=30

# Directorio de backup
BACKUP_BASE_DIR="/var/backups/udid/logs"
```

#### 12.10.6 Estructura de Backups

Los backups se organizan así:

```
/var/backups/udid/logs/
├── 20260122_020000/          # Backup del 22 de enero a las 2 AM
│   ├── udid/
│   │   ├── celery-worker.log.gz
│   │   ├── celery-beat.log.gz
│   │   └── celery-flower.log.gz
│   ├── django/
│   │   └── server.log.gz
│   ├── nginx/
│   │   ├── udid_access.log.gz
│   │   └── udid_error.log.gz
│   └── backup_info.txt       # Información del backup
├── 20260123_020000/
└── ...
```

#### 12.10.7 Verificar que el Backup Funciona

```bash
# Verificar que el script está instalado
ls -la /opt/udid/backup_logs.sh

# Probar el script
sudo -u udid /opt/udid/backup_logs.sh test

# Verificar que se creó el directorio de backup
ls -la /var/backups/udid/logs/

# Ver logs del script de backup
tail -f /var/log/udid/backup_logs.log

# Ver estadísticas
sudo -u udid /opt/udid/backup_logs.sh stats
```

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
grep "check_and_sync_subscribers_periodic" /var/log/udid/celery-worker.log | tail -10
grep "check_and_sync_smartcards_monthly" /var/log/udid/celery-worker.log | tail -5
grep "validate_and_sync_all_data_daily" /var/log/udid/celery-worker.log | tail -5
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
# Importar tareas de Celery (NO execute_sync_tasks, esa se ejecuta con el script de cron)
from udid.tasks import (
    check_and_sync_smartcards_monthly,
    check_and_sync_subscribers_periodic,
    validate_and_sync_all_data_daily
)

# Ejecutar tarea de forma asíncrona (ejemplo: verificación periódica de suscriptores)
result = check_and_sync_subscribers_periodic.delay()

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

| Tarea                                | Periodicidad Automática | Cuándo Usarla Manualmente | Duración          | Método de Ejecución |
|--------------------------------------|-------------------------|----------------------------|-------------------|---------------------|
| `execute_sync_tasks()` (cron.py)     | **UNA VEZ** (manual) | Primera vez que despliegas el sistema o cuando la BD está vacía | Puede tomar horas | Script de cron (`ejecutar_sync_tasks.py`) |
| `check_and_sync_smartcards_monthly`  | Día 28 de cada mes a las 3:00 AM | Cuando quieres verificar smartcards fuera del horario programado | Minutos/Horas | Celery (tarea periódica) |
| `check_and_sync_subscribers_periodic`| Cada 5 minutos | Cuando quieres forzar una verificación inmediata | Segundos/Minutos  | Celery (tarea periódica) |
| `validate_and_sync_all_data_daily`   | Cada día a las 22:00 | Cuando quieres validar y corregir datos fuera del horario programado | Puede tomar horas | Celery (tarea periódica) |

**Flujo de ejecución:**
1. **Primero**: Ejecutar `execute_sync_tasks()` con el script de cron (`ejecutar_sync_tasks.py`) - UNA SOLA VEZ
2. **Después**: Activar Celery Beat para que las tareas periódicas se ejecuten automáticamente
3. **Opcional**: Ejecutar tareas de Celery manualmente cuando lo necesites

**Nota sobre ejecución simultánea:**
- Las tareas de Celery tienen un mecanismo de lock para evitar ejecuciones simultáneas
- Si una tarea está en ejecución, las demás esperarán hasta que termine
- Esto previene conflictos y sobrecarga del sistema
- El script `ejecutar_sync_tasks.py` verifica si ya se ejecutó para evitar duplicados

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

# Reiniciar ambos servicios (después de cambios en código)
sudo systemctl restart celery-worker celery-beat

# Ver logs en tiempo real del Worker
sudo journalctl -u celery-worker -f

# Ver logs en tiempo real de Beat
sudo journalctl -u celery-beat -f

# Detener todas las tareas activas (emergencia)
sudo systemctl stop celery-worker celery-beat
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
check_service celery-beat >> $LOG_FILE

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

> ⚠️ **IMPORTANTE**: Después de actualizar el código con `git pull`, debes reiniciar los servicios que ejecutan código Python (Daphne, Celery Worker, Celery Beat) para que carguen el nuevo código. Los servicios del sistema (Nginx, Redis, PostgreSQL) NO necesitan reinicio.

**Proceso completo de actualización:**

```bash
# 1. Actualizar código
cd /opt/udid
git pull origin main
# O si actualizas manualmente, copiar archivos nuevos

# 2. Activar entorno virtual
source env/bin/activate

# 3. Instalar nuevas dependencias (si las hay)
pip install -r requirements.txt

# 4. Aplicar migraciones (si las hay)
python manage.py migrate

# 5. Recolectar archivos estáticos (si hay cambios en estáticos)
python manage.py collectstatic --noinput

# 6. ⚠️ CRÍTICO: Reiniciar servicios que ejecutan código Python
# Estos servicios tienen el código viejo en memoria y necesitan reiniciarse
sudo /opt/udid/manage_services.sh restart  # Reinicia todas las instancias de Daphne
sudo systemctl restart celery-worker        # Reinicia Celery Worker
sudo systemctl restart celery-beat           # Reinicia Celery Beat (si está activo)

# 7. Recargar Nginx (solo si cambiaste configuración de Nginx)
# sudo systemctl reload nginx

# 8. Verificar que todo está funcionando
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker
sudo systemctl status celery-beat
```

**Script rápido para actualización (opcional):**

Puedes crear un script `/opt/udid/update.sh`:

```bash
#!/bin/bash
# Script para actualizar el proyecto y reiniciar servicios

cd /opt/udid
source env/bin/activate

echo "📥 Actualizando código..."
git pull origin main

echo "📦 Instalando dependencias..."
pip install -r requirements.txt

echo "🗄️ Aplicando migraciones..."
python manage.py migrate

echo "📁 Recolectando estáticos..."
python manage.py collectstatic --noinput

echo "🔄 Reiniciando servicios..."
sudo /opt/udid/manage_services.sh restart
sudo systemctl restart celery-worker celery-beat

echo "✅ Actualización completada"
echo "📊 Verificando estado..."
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker --no-pager | head -10
sudo systemctl status celery-beat --no-pager | head -10
```

Hacer ejecutable:

```bash
sudo chmod +x /opt/udid/update.sh
sudo chown udid:udid /opt/udid/update.sh
```

Uso:

```bash
/opt/udid/update.sh
```

### 14.4 Backup de Base de Datos

> 📝 **Nota:** Para backup de logs, ver la sección 12.10 "Configurar Sistema de Backup y Rotación de Logs".

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

> 📝 **Nota:** El backup de logs se ejecuta automáticamente a las 2 AM (ver sección 12.10). Si quieres cambiar la hora del backup de base de datos para que no coincida, puedes usar otra hora (ej: 1 AM).

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

#### Error: "cannot load certificate" en Nginx

**Síntoma:**
```
nginx[XXXX]: [emerg] XXXX#XXXX: cannot load certificate "/etc/nginx/ssl/udid.crt": 
BIO_new_file() failed (SSL: error:80000002:system library::No such file or directory
```

**Causa:** Nginx está configurado para usar certificados SSL que no existen aún.

**Solución:**

```bash
# 1. Verificar si los certificados existen
ls -la /etc/nginx/ssl/

# 2. Si no existen, crearlos (ver Sección 10.1)
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/udid.key \
    -out /etc/nginx/ssl/udid.crt

# 3. Cambiar permisos
sudo chmod 600 /etc/nginx/ssl/udid.key
sudo chmod 644 /etc/nginx/ssl/udid.crt

# 4. Verificar configuración de Nginx
sudo nginx -t

# 5. Recargar Nginx
sudo systemctl reload nginx

# 6. Verificar que no hay errores
sudo systemctl status nginx
```

**Alternativa temporal (sin SSL):**

Si quieres probar sin SSL primero, comenta temporalmente las líneas SSL en `/etc/nginx/sites-available/udid`:

```nginx
# ssl_certificate /etc/nginx/ssl/udid.crt;
# ssl_certificate_key /etc/nginx/ssl/udid.key;
```

Y cambia `listen 443 ssl http2;` por `listen 80;`. Después de crear los certificados, descomenta y vuelve a `listen 443 ssl http2;`.

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
source env/bin/activate

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
sudo systemctl stop celery-worker celery-beat celery-flower
sudo systemctl stop nginx
sudo systemctl stop redis-server
sudo systemctl stop postgresql

# Esperar unos segundos
sleep 5

# Iniciar en orden
sudo systemctl start postgresql
sudo systemctl start redis-server
sudo /opt/udid/manage_services.sh start
sudo systemctl start celery-worker celery-beat
sudo systemctl start celery-flower  # Opcional
sudo systemctl start nginx

# Verificar estado
sudo systemctl status postgresql
sudo systemctl status redis-server
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker
sudo systemctl status celery-beat
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

#### 🚀 Carga Muy Alta Optimizada (64-120GB RAM / 32-64 cores / 3000+ requests simultáneos)

Esta configuración está optimizada específicamente para un servidor de **alta gama** con **64-120GB RAM**, **32-64 cores de CPU** que necesita soportar **~3000+ requests casi simultáneos** y **1TB de almacenamiento**.

| Recurso | Especificación Optimizada |
|---------|---------------------------|
| **CPU** | 32-64 cores |
| **RAM** | 64-120 GB |
| **Disco** | 1 TB SSD/NVMe |
| **Workers Daphne** | 40-60 instancias (según cores disponibles) |
| **Redis Memory** | 16-30 GB (25% de RAM) |
| **PostgreSQL Connections** | 1000-1500 |
| **Nginx Worker Connections** | 16384 |
| **Conexiones Simultáneas** | ~5000-10000+ |

**Distribución de Memoria (ejemplo con 64GB RAM):**
- PostgreSQL: ~20GB (shared_buffers + work_mem + otros)
- Redis: 16GB (25% de RAM)
- Daphne Workers: ~20GB (40 instancias × ~500MB cada una)
- Sistema Operativo: ~4GB
- Nginx: ~2GB
- Celery: ~4GB
- Buffer/Cache: ~2GB

**Distribución de Memoria (ejemplo con 120GB RAM):**
- PostgreSQL: ~40GB (shared_buffers + work_mem + otros)
- Redis: 30GB (25% de RAM)
- Daphne Workers: ~30GB (60 instancias × ~500MB cada una)
- Sistema Operativo: ~4GB
- Nginx: ~2GB
- Celery: ~4GB
- Buffer/Cache: ~10GB

#### 🎯 Configuraciones Específicas por Hardware (32GB, 64GB, 124GB RAM / 32-64 cores / 1TB)

##### Configuración 1: 32 GB RAM / 32 cores / 1 TB SSD

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 32 cores |
| **RAM** | 32 GB |
| **Disco** | 1 TB SSD/NVMe |
| **Workers Daphne** | 32 instancias (puertos 8000-8031) |
| **Redis Memory** | 8 GB (25% de RAM) |
| **PostgreSQL Connections** | 800 |
| **Nginx Worker Connections** | 8192 |
| **Conexiones Simultáneas** | ~3000-4000 |

**Distribución de Memoria (32GB RAM):**
- PostgreSQL: ~10GB (shared_buffers + work_mem + otros)
- Redis: 8GB (25% de RAM)
- Daphne Workers: ~16GB (32 instancias × ~500MB cada una)
- Sistema Operativo: ~4GB
- Nginx: ~1GB
- Celery: ~2GB
- Buffer/Cache: ~1GB

##### Configuración 2: 64 GB RAM / 32 cores / 1 TB SSD

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 32 cores |
| **RAM** | 64 GB |
| **Disco** | 1 TB SSD/NVMe |
| **Workers Daphne** | 40 instancias (puertos 8000-8039) |
| **Redis Memory** | 16 GB (25% de RAM) |
| **PostgreSQL Connections** | 1000 |
| **Nginx Worker Connections** | 16384 |
| **Conexiones Simultáneas** | ~5000-7000 |

**Distribución de Memoria (64GB RAM):**
- PostgreSQL: ~20GB (shared_buffers + work_mem + otros)
- Redis: 16GB (25% de RAM)
- Daphne Workers: ~20GB (40 instancias × ~500MB cada una)
- Sistema Operativo: ~4GB
- Nginx: ~2GB
- Celery: ~4GB
- Buffer/Cache: ~2GB

##### Configuración 3: 124 GB RAM / 64 cores / 1 TB SSD

| Recurso | Especificación |
|---------|----------------|
| **CPU** | 64 cores |
| **RAM** | 124 GB |
| **Disco** | 1 TB SSD/NVMe |
| **Workers Daphne** | 60 instancias (puertos 8000-8059) |
| **Redis Memory** | 31 GB (25% de RAM) |
| **PostgreSQL Connections** | 1500 |
| **Nginx Worker Connections** | 16384 |
| **Conexiones Simultáneas** | ~10000-15000+ |

**Distribución de Memoria (124GB RAM):**
- PostgreSQL: ~40GB (shared_buffers + work_mem + otros)
- Redis: 31GB (25% de RAM)
- Daphne Workers: ~30GB (60 instancias × ~500MB cada una)
- Sistema Operativo: ~4GB
- Nginx: ~2GB
- Celery: ~4GB
- Buffer/Cache: ~13GB

### 16.1.1 Tabla de Configuraciones por Hardware

| Configuración | RAM | CPU | Disco | Redis Max Connections | Channel Layers Capacity | Semaphore Slots | Queue Max Size | Celery Concurrency | Celery Prefetch |
|---------------|-----|-----|-------|----------------------|------------------------|-----------------|----------------|-------------------|-----------------|
| **Estándar** | 8-16 GB | 4-8 cores | 80 GB | 100 | 2000 | 1000 | 1000 | auto | 4 |
| **32GB/32cores** | 32 GB | 32 cores | 1 TB | 300 | 5000 | 3000 | 5000 | 16 | 8 |
| **64GB/32cores** | 64 GB | 32 cores | 1 TB | 400 | 10000 | 5000 | 10000 | 24 | 10 |
| **124GB/64cores** | 124 GB | 64 cores | 1 TB | 600 | 20000 | 10000 | 20000 | 48 | 16 |

**Notas:**
- **Redis Max Connections**: Pool de conexiones simultáneas a Redis
- **Channel Layers Capacity**: Mensajes máximos por canal WebSocket antes de bloquear
- **Semaphore Slots**: Requests simultáneos permitidos globalmente
- **Queue Max Size**: Tamaño máximo de la cola de requests pendientes
- **Celery Concurrency**: Número de procesos/threads por worker de Celery
- **Celery Prefetch**: Multiplicador de tareas pre-cargadas por worker

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

#### Para PostgreSQL (32GB RAM / 32 cores / 1TB SSD / 3000+ requests simultáneos):

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

```conf
# Ajustes de memoria optimizados para 32GB RAM
shared_buffers = 8GB              # 25% de 32GB RAM
effective_cache_size = 24GB       # 75% de 32GB RAM
work_mem = 128MB                  # Ajustado para más conexiones simultáneas
maintenance_work_mem = 2GB        # Aumentado para operaciones de mantenimiento
temp_buffers = 32MB
hash_mem_multiplier = 2.0

# Conexiones - aumentado para 3000+ requests simultáneos
max_connections = 800             # Soporta ~3000-4000 requests con pooling
superuser_reserved_connections = 15

# WAL (Write-Ahead Logging) optimizado
wal_buffers = 128MB               # Aumentado para mejor rendimiento
checkpoint_completion_target = 0.9
wal_writer_delay = 200ms
commit_delay = 100
commit_siblings = 10
max_wal_size = 2GB                # Aumentado para reducir checkpoints
min_wal_size = 512MB

# Query Planner
random_page_cost = 1.1            # Para SSD/NVMe
effective_io_concurrency = 200    # Para SSD con múltiples operaciones
parallel_tuple_cost = 0.1
parallel_setup_cost = 1000.0

# Parallel Query (aprovechar múltiples cores - 32 cores)
max_parallel_workers_per_gather = 8  # Para 32 cores
max_parallel_workers = 32        # Número de workers paralelos
max_worker_processes = 64        # Total de procesos worker

# Autovacuum optimizado para alta carga
autovacuum_max_workers = 8       # Aumentado para más cores
autovacuum_naptime = 10s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02

# Logging (opcional, desactivar en producción para mejor rendimiento)
logging_collector = on
log_min_duration_statement = 1000  # Solo log queries > 1 segundo
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
```

**Aplicar cambios:**
```bash
sudo systemctl restart postgresql
```

#### Para PostgreSQL (64GB RAM / 32 cores / 1TB SSD / 5000+ requests simultáneos):

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

**Para servidor con 64GB RAM:**
```conf
# Ajustes de memoria optimizados para 64GB RAM
shared_buffers = 16GB             # 25% de 64GB RAM
effective_cache_size = 48GB       # 75% de 64GB RAM
work_mem = 128MB                   # Ajustado para más conexiones simultáneas
maintenance_work_mem = 4GB         # Aumentado para operaciones de mantenimiento
temp_buffers = 32MB
hash_mem_multiplier = 2.0

# Conexiones - aumentado para 5000+ requests simultáneos
max_connections = 1000             # Soporta ~5000-7000 requests con pooling
superuser_reserved_connections = 20

# WAL (Write-Ahead Logging) optimizado
wal_buffers = 256MB               # Aumentado para mejor rendimiento
checkpoint_completion_target = 0.9
wal_writer_delay = 200ms
commit_delay = 100
commit_siblings = 10
max_wal_size = 4GB                # Aumentado para reducir checkpoints
min_wal_size = 1GB

# Query Planner
random_page_cost = 1.1            # Para SSD/NVMe
effective_io_concurrency = 200    # Para SSD con múltiples operaciones
parallel_tuple_cost = 0.1
parallel_setup_cost = 1000.0

# Parallel Query (aprovechar múltiples cores)
max_parallel_workers_per_gather = 8  # Para 32+ cores
max_parallel_workers = 32        # Número de workers paralelos
max_worker_processes = 64        # Total de procesos worker

# Autovacuum optimizado para alta carga
autovacuum_max_workers = 8       # Aumentado para más cores
autovacuum_naptime = 10s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02

# Logging (opcional, desactivar en producción para mejor rendimiento)
logging_collector = on
log_min_duration_statement = 1000  # Solo log queries > 1 segundo
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
```

**Para servidor con 120GB RAM:**
```conf
# Ajustes de memoria optimizados para 120GB RAM
shared_buffers = 30GB             # 25% de 120GB RAM
effective_cache_size = 90GB       # 75% de 120GB RAM
work_mem = 128MB                   # Ajustado para más conexiones simultáneos
maintenance_work_mem = 8GB         # Aumentado para operaciones de mantenimiento
temp_buffers = 64MB
hash_mem_multiplier = 2.0

# Conexiones - aumentado para 10000+ requests simultáneos
max_connections = 1500            # Soporta ~10000+ requests con pooling
superuser_reserved_connections = 30

# WAL (Write-Ahead Logging) optimizado
wal_buffers = 512MB               # Aumentado para mejor rendimiento
checkpoint_completion_target = 0.9
wal_writer_delay = 200ms
commit_delay = 100
commit_siblings = 10
max_wal_size = 8GB                # Aumentado para reducir checkpoints
min_wal_size = 2GB

# Query Planner
random_page_cost = 1.1            # Para SSD/NVMe
effective_io_concurrency = 200    # Para SSD con múltiples operaciones
parallel_tuple_cost = 0.1
parallel_setup_cost = 1000.0

# Parallel Query (aprovechar múltiples cores - 64 cores)
max_parallel_workers_per_gather = 16  # Para 64 cores
max_parallel_workers = 64        # Número de workers paralelos
max_worker_processes = 128       # Total de procesos worker

# Autovacuum optimizado para alta carga
autovacuum_max_workers = 16      # Aumentado para más cores
autovacuum_naptime = 10s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02

# Logging (opcional, desactivar en producción para mejor rendimiento)
logging_collector = on
log_min_duration_statement = 1000  # Solo log queries > 1 segundo
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
```

**Aplicar cambios:**
```bash
sudo systemctl restart postgresql
```

#### Para PostgreSQL (124GB RAM / 64 cores / 1TB SSD / 10000+ requests simultáneos):

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

```conf
# Ajustes de memoria optimizados para 124GB RAM
shared_buffers = 31GB            # 25% de 124GB RAM
effective_cache_size = 93GB      # 75% de 124GB RAM
work_mem = 128MB                 # Ajustado para más conexiones simultáneas
maintenance_work_mem = 8GB       # Aumentado para operaciones de mantenimiento
temp_buffers = 64MB
hash_mem_multiplier = 2.0

# Conexiones - aumentado para 10000+ requests simultáneos
max_connections = 1500           # Soporta ~10000-15000+ requests con pooling
superuser_reserved_connections = 30

# WAL (Write-Ahead Logging) optimizado
wal_buffers = 512MB              # Aumentado para mejor rendimiento
checkpoint_completion_target = 0.9
wal_writer_delay = 200ms
commit_delay = 100
commit_siblings = 10
max_wal_size = 8GB               # Aumentado para reducir checkpoints
min_wal_size = 2GB

# Query Planner
random_page_cost = 1.1           # Para SSD/NVMe
effective_io_concurrency = 200   # Para SSD con múltiples operaciones
parallel_tuple_cost = 0.1
parallel_setup_cost = 1000.0

# Parallel Query (aprovechar múltiples cores - 64 cores)
max_parallel_workers_per_gather = 16  # Para 64 cores
max_parallel_workers = 64       # Número de workers paralelos
max_worker_processes = 128       # Total de procesos worker

# Autovacuum optimizado para alta carga
autovacuum_max_workers = 16      # Aumentado para más cores
autovacuum_naptime = 10s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02

# Logging (opcional, desactivar en producción para mejor rendimiento)
logging_collector = on
log_min_duration_statement = 1000  # Solo log queries > 1 segundo
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
```

**Aplicar cambios:**
```bash
sudo systemctl restart postgresql
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

#### Para Redis (32GB RAM / 32 cores / 3000+ requests simultáneos):

```bash
sudo nano /etc/redis/redis.conf
```

```conf
# Memoria optimizada para 32GB RAM
maxmemory 8gb                     # 25% de 32GB RAM
maxmemory-policy allkeys-lru      # Eliminar claves menos usadas cuando se llena

# Conexiones y timeouts
tcp-keepalive 300
timeout 0                         # Sin timeout (conexiones persistentes)
tcp-backlog 1024                  # Aumentado para más conexiones simultáneas
maxclients 8000                   # Máximo de clientes simultáneos

# Persistencia (ajustar según necesidades)
# Para mejor rendimiento, desactivar persistencia si no es crítica:
save ""                           # Desactivar snapshots automáticos
# O usar AOF con fsync cada segundo:
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Optimizaciones de rendimiento
hz 10                             # Frecuencia de tareas en background
dynamic-hz yes                    # Ajustar dinámicamente según carga
lazyfree-lazy-eviction yes        # Liberación asíncrona de memoria
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes

# Threading (Redis 6+ con múltiples cores)
io-threads 8                      # Para 32 cores, usar 8 threads de I/O
io-threads-do-reads yes

# Logging
loglevel notice                   # Reducir logging en producción
```

**Aplicar cambios:**
```bash
sudo systemctl restart redis-server
```

#### Para Redis (64GB RAM / 32 cores / 5000+ requests simultáneos):

```bash
sudo nano /etc/redis/redis.conf
```

**Para servidor con 64GB RAM:**
```conf
# Memoria optimizada para 64GB RAM
maxmemory 16gb                    # 25% de 64GB RAM
maxmemory-policy allkeys-lru      # Eliminar claves menos usadas cuando se llena

# Conexiones y timeouts
tcp-keepalive 300
timeout 0                         # Sin timeout (conexiones persistentes)
tcp-backlog 1024                  # Aumentado para más conexiones simultáneas
maxclients 10000                  # Máximo de clientes simultáneos

# Persistencia (ajustar según necesidades)
# Para mejor rendimiento, desactivar persistencia si no es crítica:
save ""                           # Desactivar snapshots automáticos
# O usar AOF con fsync cada segundo:
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Optimizaciones de rendimiento
hz 10                             # Frecuencia de tareas en background
dynamic-hz yes                    # Ajustar dinámicamente según carga
lazyfree-lazy-eviction yes        # Liberación asíncrona de memoria
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes

# Threading (Redis 6+ con múltiples cores)
io-threads 8                      # Para 32+ cores, usar 8 threads de I/O
io-threads-do-reads yes

# Logging
loglevel notice                   # Reducir logging en producción
```

**Para servidor con 120GB RAM:**
```conf
# Memoria optimizada para 120GB RAM
maxmemory 30gb                    # 25% de 120GB RAM
maxmemory-policy allkeys-lru      # Eliminar claves menos usadas cuando se llena

# Conexiones y timeouts
tcp-keepalive 300
timeout 0                         # Sin timeout (conexiones persistentes)
tcp-backlog 2048                  # Aumentado para más conexiones simultáneas
maxclients 20000                  # Máximo de clientes simultáneos

# Persistencia (ajustar según necesidades)
# Para mejor rendimiento, desactivar persistencia si no es crítica:
save ""                           # Desactivar snapshots automáticos
# O usar AOF con fsync cada segundo:
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Optimizaciones de rendimiento
hz 10                             # Frecuencia de tareas en background
dynamic-hz yes                    # Ajustar dinámicamente según carga
lazyfree-lazy-eviction yes        # Liberación asíncrona de memoria
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes

# Threading (Redis 6+ con múltiples cores)
io-threads 16                     # Para 64 cores, usar 16 threads de I/O
io-threads-do-reads yes

# Logging
loglevel notice                   # Reducir logging en producción
```

**Aplicar cambios:**
```bash
sudo systemctl restart redis-server
```

#### Para Redis (124GB RAM / 64 cores / 10000+ requests simultáneos):

```bash
sudo nano /etc/redis/redis.conf
```

```conf
# Memoria optimizada para 124GB RAM
maxmemory 31gb                    # 25% de 124GB RAM
maxmemory-policy allkeys-lru     # Eliminar claves menos usadas cuando se llena

# Conexiones y timeouts
tcp-keepalive 300
timeout 0                         # Sin timeout (conexiones persistentes)
tcp-backlog 2048                  # Aumentado para más conexiones simultáneas
maxclients 20000                  # Máximo de clientes simultáneos

# Persistencia (ajustar según necesidades)
# Para mejor rendimiento, desactivar persistencia si no es crítica:
save ""                           # Desactivar snapshots automáticos
# O usar AOF con fsync cada segundo:
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Optimizaciones de rendimiento
hz 10                             # Frecuencia de tareas en background
dynamic-hz yes                    # Ajustar dinámicamente según carga
lazyfree-lazy-eviction yes        # Liberación asíncrona de memoria
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes

# Threading (Redis 6+ con múltiples cores)
io-threads 16                     # Para 64 cores, usar 16 threads de I/O
io-threads-do-reads yes

# Logging
loglevel notice                   # Reducir logging en producción
```

**Aplicar cambios:**
```bash
sudo systemctl restart redis-server
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

#### Para Nginx (32GB RAM / 32 cores / 3000+ requests simultáneos):

```bash
sudo nano /etc/nginx/nginx.conf
```

```nginx
# Worker processes - usar todos los cores disponibles (32 cores)
worker_processes auto;
worker_rlimit_nofile 65536;       # Aumentado para más archivos abiertos (32 cores)

events {
    # Conexiones por worker - aumentado para 3000+ requests simultáneos
    worker_connections 8192;      # 8192 × número de workers = capacidad total
    use epoll;                     # Mejor para Linux
    multi_accept on;              # Aceptar múltiples conexiones a la vez
    accept_mutex off;             # Desactivar mutex para mejor rendimiento con muchos cores
}

http {
    # Buffers optimizados para alta carga
    client_body_buffer_size 256k;
    client_max_body_size 20m;
    client_header_buffer_size 2k;
    large_client_header_buffers 8 32k;
    
    # Timeouts optimizados
    keepalive_timeout 75;          # Mantener conexiones abiertas más tiempo
    keepalive_requests 2000;       # Más requests por conexión
    send_timeout 60s;
    client_body_timeout 60s;
    client_header_timeout 60s;
    sendfile on;                   # Usar sendfile para mejor rendimiento
    tcp_nopush on;                 # Optimizar envío de paquetes
    tcp_nodelay on;                # Desactivar Nagle algorithm
    
    # Compresión
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_min_length 1000;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript application/x-javascript text/x-js;
    gzip_disable "msie6";
    
    # Cache de archivos abiertos (aumentado para alta carga)
    open_file_cache max=250000 inactive=30s;
    open_file_cache_valid 60s;
    open_file_cache_min_uses 2;
    open_file_cache_errors on;
    
    # Logging optimizado (reducir en producción)
    access_log off;                 # Desactivar para mejor rendimiento
    # O usar logging asíncrono con buffer grande:
    # access_log /var/log/nginx/access.log buffer=64k flush=10s;
    error_log /var/log/nginx/error.log warn;
    
    # Rate limiting (protección DDoS) - aumentado para alta carga
    limit_req_zone $binary_remote_addr zone=api_limit:20m rate=150r/s;
    limit_req_zone $binary_remote_addr zone=ws_limit:20m rate=80r/s;
    
    # Connection limiting
    limit_conn_zone $binary_remote_addr zone=conn_limit_per_ip:20m;
    limit_conn conn_limit_per_ip 50;
    
    # Incluir configuración del sitio
    include /etc/nginx/sites-enabled/*;
}
```

**Aplicar cambios:**
```bash
sudo nginx -t                    # Verificar configuración
sudo systemctl restart nginx
```

#### Para Nginx (64GB RAM / 32 cores / 5000+ requests simultáneos):

```bash
sudo nano /etc/nginx/nginx.conf
```

```nginx
# Worker processes - usar todos los cores disponibles (32 cores)
worker_processes auto;
worker_rlimit_nofile 131072;      # Aumentado para más archivos abiertos (32-64 cores)

events {
    # Conexiones por worker - aumentado para 5000+ requests simultáneos
    worker_connections 16384;      # 16384 × número de workers = capacidad total
    use epoll;                     # Mejor para Linux
    multi_accept on;               # Aceptar múltiples conexiones a la vez
    accept_mutex off;              # Desactivar mutex para mejor rendimiento con muchos cores
}

http {
    # Buffers optimizados para alta carga
    client_body_buffer_size 256k;
    client_max_body_size 20m;
    client_header_buffer_size 2k;
    large_client_header_buffers 8 32k;
    
    # Timeouts optimizados
    keepalive_timeout 75;          # Mantener conexiones abiertas más tiempo
    keepalive_requests 2000;       # Más requests por conexión
    send_timeout 60s;
    client_body_timeout 60s;
    client_header_timeout 60s;
    sendfile on;                    # Usar sendfile para mejor rendimiento
    tcp_nopush on;                  # Optimizar envío de paquetes
    tcp_nodelay on;                 # Desactivar Nagle algorithm
    
    # Compresión
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_min_length 1000;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript application/x-javascript text/x-js;
    gzip_disable "msie6";
    
    # Cache de archivos abiertos (aumentado para alta carga)
    open_file_cache max=500000 inactive=30s;
    open_file_cache_valid 60s;
    open_file_cache_min_uses 2;
    open_file_cache_errors on;
    
    # Logging optimizado (reducir en producción)
    access_log off;                 # Desactivar para mejor rendimiento
    # O usar logging asíncrono con buffer grande:
    # access_log /var/log/nginx/access.log buffer=64k flush=10s;
    error_log /var/log/nginx/error.log warn;
    
    # Rate limiting (protección DDoS) - aumentado para alta carga
    limit_req_zone $binary_remote_addr zone=api_limit:20m rate=200r/s;
    limit_req_zone $binary_remote_addr zone=ws_limit:20m rate=100r/s;
    
    # Connection limiting
    limit_conn_zone $binary_remote_addr zone=conn_limit_per_ip:20m;
    limit_conn conn_limit_per_ip 50;
    
    # Incluir configuración del sitio
    include /etc/nginx/sites-enabled/*;
}
```

**Aplicar cambios:**
```bash
sudo nginx -t                    # Verificar configuración
sudo systemctl restart nginx
```

### 16.3 Aplicar Optimizaciones para 64-120GB RAM / 32-64 cores / 5000+ Requests

Si tienes un servidor de **alta gama** con **64-120GB RAM**, **32-64 cores de CPU** y necesitas soportar **~5000-10000+ requests simultáneos**, sigue estos pasos:

#### Paso 1: Actualizar Configuración de PostgreSQL

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

Aplicar los valores de la sección "Para PostgreSQL (64-120GB RAM / 32-64 cores / 5000+ requests simultáneos)" arriba:
- **Para 64GB RAM**: usar configuración con `shared_buffers = 16GB`, `max_connections = 1000`
- **Para 120GB RAM**: usar configuración con `shared_buffers = 30GB`, `max_connections = 1500`

#### Paso 2: Actualizar Configuración de Redis

```bash
sudo nano /etc/redis/redis.conf
```

Aplicar los valores de la sección "Para Redis (64-120GB RAM / 5000+ requests simultáneos)" arriba:
- **Para 64GB RAM**: cambiar `maxmemory` a `16gb`, `io-threads 8`
- **Para 120GB RAM**: cambiar `maxmemory` a `30gb`, `io-threads 16`

#### Paso 3: Actualizar Configuración de Nginx

```bash
sudo nano /etc/nginx/nginx.conf
```

Aplicar los valores de la sección "Para Nginx (64-120GB RAM / 32-64 cores / 5000+ requests simultáneos)" arriba.

#### Paso 4: Actualizar Nginx Site Configuration

```bash
sudo nano /etc/nginx/sites-available/udid
```

Descomentar las instancias de Daphne en el bloque `upstream udid_backend` (ver sección 9.2):
- **Para 64GB RAM / 32 cores**: configurar 40 instancias (puertos 8000-8039)
- **Para 120GB RAM / 64 cores**: configurar 60 instancias (puertos 8000-8059)

#### Paso 5: Actualizar Script de Control

```bash
sudo nano /opt/udid/manage_services.sh
```

Cambiar `INSTANCES=4` a:
- **Para 64GB RAM / 32 cores**: `INSTANCES=40`
- **Para 120GB RAM / 64 cores**: `INSTANCES=60`

#### Paso 6: Actualizar Variables de Entorno

```bash
sudo nano /opt/udid/.env
```

Agregar o actualizar estas variables:

```env
# Optimizaciones para 64-120GB RAM / 32-64 cores / 5000+ requests simultáneos
# Para 64GB RAM / 32 cores:
REDIS_MAX_CONNECTIONS=400
GLOBAL_SEMAPHORE_SLOTS=5000
REQUEST_QUEUE_MAX_SIZE=10000
CHANNEL_LAYERS_CAPACITY=10000

# Para 120GB RAM / 64 cores (valores aún más altos):
# REDIS_MAX_CONNECTIONS=600
# GLOBAL_SEMAPHORE_SLOTS=10000
# REQUEST_QUEUE_MAX_SIZE=20000
# CHANNEL_LAYERS_CAPACITY=20000
```

#### Paso 7: Reiniciar Servicios

```bash
# Reiniciar PostgreSQL
sudo systemctl restart postgresql

# Reiniciar Redis
sudo systemctl restart redis-server

# Reiniciar Nginx
sudo nginx -t                    # Verificar configuración primero
sudo systemctl restart nginx

# Reiniciar instancias de Daphne
sudo /opt/udid/manage_services.sh restart
```

#### Paso 8: Verificar

```bash
# Verificar que todas las instancias están corriendo
sudo /opt/udid/manage_services.sh status

# Verificar puertos
sudo ss -tlnp | grep 800

# Verificar uso de memoria
free -h

# Verificar conexiones PostgreSQL
sudo -u postgres psql -c "SHOW max_connections;"

# Verificar memoria Redis
redis-cli INFO memory | grep maxmemory
```

### 16.4 Monitoreo de Recursos

Instalar herramientas de monitoreo:

```bash
# htop para monitoreo en tiempo real
sudo apt install -y htop

# iotop para monitoreo de disco
sudo apt install -y iotop

# Netdata para dashboard web de monitoreo (opcional)
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

**Comandos útiles de monitoreo para alta carga:**

```bash
# Ver uso de recursos en tiempo real
htop

# Ver conexiones activas por puerto
sudo ss -tlnp | grep -E '(800|5432|6379|443)'

# Ver conexiones PostgreSQL activas
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"

# Ver memoria Redis
redis-cli INFO memory

# Ver estadísticas de Nginx
curl http://localhost/nginx_status  # Si está habilitado

# Ver logs en tiempo real
sudo journalctl -u udid@0 -f
sudo tail -f /var/log/nginx/udid_access.log
```

---

## 🔄 Actualizar Código del Proyecto (Después de git pull)

> ⚠️ **IMPORTANTE**: Después de hacer `git pull` o actualizar archivos, los servicios que ejecutan código Python (Daphne, Celery Worker, Celery Beat) tienen el código viejo en memoria y **DEBEN reiniciarse** para cargar el nuevo código.

### Proceso Rápido de Actualización

```bash
# 1. Actualizar código
cd /opt/udid
git pull origin main

# 2. Activar entorno y actualizar dependencias (si es necesario)
source env/bin/activate
pip install -r requirements.txt  # Solo si hay nuevas dependencias
python manage.py migrate         # Solo si hay nuevas migraciones
python manage.py collectstatic --noinput  # Solo si hay cambios en estáticos

# 3. ⚠️ CRÍTICO: Reiniciar servicios que ejecutan código Python
sudo /opt/udid/manage_services.sh restart  # Reinicia todas las instancias de Daphne
sudo systemctl restart celery-worker        # Reinicia Celery Worker
sudo systemctl restart celery-beat          # Reinicia Celery Beat

# 4. Verificar que todo está funcionando
sudo /opt/udid/manage_services.sh status
sudo systemctl status celery-worker
sudo systemctl status celery-beat
```

### Servicios que NO Necesitan Reinicio

Estos servicios **NO** necesitan reinicio después de `git pull`:
- ✅ **Nginx**: Solo sirve archivos estáticos y hace proxy, no ejecuta código Python
- ✅ **Redis**: Base de datos en memoria, no ejecuta código Python
- ✅ **PostgreSQL**: Base de datos, no ejecuta código Python

### Servicios que SÍ Necesitan Reinicio

Estos servicios **SÍ** necesitan reinicio después de `git pull`:
- 🔄 **Daphne** (instancias): Ejecutan código Django/ASGI
- 🔄 **Celery Worker**: Ejecuta código de tareas
- 🔄 **Celery Beat**: Ejecuta código de programación de tareas

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
sudo systemctl restart celery-beat          # Reiniciar Celery Beat
sudo systemctl restart celery-flower        # Reiniciar Flower (opcional)

# === LOGS ===
sudo journalctl -u udid@0 -f               # Ver logs de Daphne
sudo journalctl -u celery-worker -f        # Ver logs de Celery Worker
sudo journalctl -u celery-beat -f           # Ver logs de Celery Beat
sudo tail -f /var/log/nginx/udid_error.log # Ver errores de Nginx
sudo tail -f /var/log/udid/celery-worker.log  # Ver logs de Worker
sudo tail -f /var/log/udid/celery-beat.log    # Ver logs de Beat

# === BACKUP DE LOGS ===
sudo -u udid /opt/udid/backup_logs.sh auto   # Backup automático (verifica tamaño)
sudo -u udid /opt/udid/backup_logs.sh force  # Forzar backup inmediato
sudo -u udid /opt/udid/backup_logs.sh stats  # Ver estadísticas de backups
sudo -u udid /opt/udid/backup_logs.sh cleanup # Limpiar backups antiguos
sudo -u udid /opt/udid/backup_logs.sh test   # Modo de prueba

# === DJANGO ===
cd /opt/udid && source env/bin/activate   # Activar entorno
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

## 🔄 Verificación Después de Reiniciar el Servidor

Después de reiniciar el servidor, los servicios configurados con `systemctl enable` deberían iniciarse automáticamente. Sigue estos pasos para verificar que todo está funcionando:

### Verificar Servicios Habilitados para Inicio Automático

```bash
# Ver qué servicios están habilitados
systemctl list-unit-files | grep -E "udid|celery|nginx|redis|postgresql" | grep enabled

# Deberías ver:
# celery-beat.service          enabled
# celery-worker.service        enabled
# nginx.service                enabled
# postgresql.service           enabled
# redis-server.service         enabled
# udid@0.service               enabled
# udid@1.service               enabled
# udid@2.service               enabled
# udid@3.service               enabled
```

### Verificar Estado de Todos los Servicios

```bash
# Servicios del sistema
sudo systemctl status nginx
sudo systemctl status redis-server
sudo systemctl status postgresql

# Instancias de Daphne
sudo /opt/udid/manage_services.sh status

# Celery
sudo systemctl status celery-worker
sudo systemctl status celery-beat
```

Todos deberían mostrar `Active: active (running)`.

### Si Algún Servicio No Está Corriendo

```bash
# Habilitar e iniciar servicios que falten
sudo systemctl enable nginx && sudo systemctl start nginx
sudo systemctl enable redis-server && sudo systemctl start redis-server
sudo systemctl enable postgresql && sudo systemctl start postgresql

# Instancias de Daphne
sudo /opt/udid/manage_services.sh enable
sudo /opt/udid/manage_services.sh start

# Celery
sudo systemctl enable celery-worker && sudo systemctl start celery-worker
sudo systemctl enable celery-beat && sudo systemctl start celery-beat
```

### Verificar Conectividad y Funcionamiento

```bash
# Verificar que Daphne está escuchando
sudo ss -tlnp | grep 800

# Verificar que Nginx está escuchando
sudo ss -tlnp | grep -E "80|443"

# Probar endpoint de salud
curl -k https://localhost/health

# Verificar Redis
redis-cli ping
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
- [ ] Script `ejecutar_sync_tasks.py` creado y configurado
- [ ] `execute_sync_tasks()` ejecutado UNA VEZ con el script de cron (sincronización inicial)
- [ ] Servicios systemd de Celery Worker creado y habilitado
- [ ] Celery Beat creado y habilitado (después de ejecutar execute_sync_tasks())
- [ ] Flower configurado (opcional pero recomendado)
- [ ] Tareas de mantenimiento en crontab configuradas (NO incluir ejecutar_sync_tasks.py)
- [ ] Firewall configurado
- [ ] Pruebas de API exitosas
- [ ] Pruebas de WebSocket exitosas
- [ ] Verificación de ejecución de tareas periódicas de Celery

---

**¡Felicidades! 🎉** Si has llegado hasta aquí y todo funciona, tu servidor UDID está listo para producción.

Para soporte adicional, revisar los logs y la documentación de cada componente:
- [Django Documentation](https://docs.djangoproject.com/)
- [Django Channels](https://channels.readthedocs.io/)
- [Nginx Documentation](https://nginx.org/en/docs/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Redis Documentation](https://redis.io/documentation)

