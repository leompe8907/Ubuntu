#!/bin/bash
# Script de actualización del proyecto UDID.
#
# Uso: /opt/udid/update.sh
#
# Hace: git pull -> pip install -> migrate -> collectstatic -> reinicia
# Daphne (todas las instancias vía manage_services.sh) + Celery worker/beat.
# Nginx/PostgreSQL/Redis NO se tocan: no ejecutan código Python de la app,
# así que no necesitan reiniciarse en una actualización normal (ver sección
# 14.3 de docs/GUIA_COMPLETA_DEPLOY_UBUNTU_SERVER.md). Nginx solo se recarga
# si se pasa --reload-nginx (por si cambió su configuración).

set -euo pipefail

PROJECT_DIR="/opt/udid"
VENV_DIR="$PROJECT_DIR/env"
LOG_DIR="/var/log/udid"
LOG_FILE="$LOG_DIR/update-$(date +%Y%m%d-%H%M%S).log"

RELOAD_NGINX=false
if [[ "${1:-}" == "--reload-nginx" ]]; then
    RELOAD_NGINX=true
fi

mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

STEP=""
fail_handler() {
    echo ""
    echo "❌ Falló en el paso: ${STEP:-desconocido} (línea $1)"
    echo "   El resto de la actualización se detuvo. Nada más se ejecutó después de este punto."
    echo "   Log completo en: $LOG_FILE"
    exit 1
}
trap 'fail_handler $LINENO' ERR

echo "======================================================================"
echo "  ACTUALIZACIÓN UDID - $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================================"

cd "$PROJECT_DIR"

STEP="verificar working tree limpio"
echo ""
echo "🔍 [1/7] Verificando que no haya cambios locales sin commitear..."
if [[ -n "$(git status --porcelain)" ]]; then
    echo "❌ Hay cambios locales sin commitear en $PROJECT_DIR:"
    git status --short
    echo ""
    echo "   git pull podría abortar o pisar estos cambios. Resuélvelo a mano"
    echo "   (git stash / git commit / git diff para revisar) antes de reintentar."
    exit 1
fi
echo "✅ Working tree limpio"

STEP="git pull"
echo ""
echo "📥 [2/7] Actualizando código (git pull origin main)..."
git pull origin main

STEP="activar entorno virtual"
echo ""
echo "🐍 [3/7] Activando entorno virtual..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

STEP="pip install"
echo ""
echo "📦 [4/7] Instalando/actualizando dependencias..."
pip install -r requirements.txt

STEP="migraciones"
echo ""
echo "🗄️  [5/7] Aplicando migraciones..."
python manage.py migrate

STEP="collectstatic"
echo ""
echo "📁 [6/7] Recolectando archivos estáticos..."
python manage.py collectstatic --noinput

STEP="reiniciar servicios"
echo ""
echo "🔄 [7/7] Reiniciando servicios con código Python..."
sudo "$PROJECT_DIR/manage_services.sh" restart
sudo systemctl restart celery-worker celery-beat

if systemctl list-unit-files celery-flower.service >/dev/null 2>&1; then
    echo "🌸 Reiniciando Celery Flower..."
    sudo systemctl restart celery-flower || echo "⚠️  No se pudo reiniciar celery-flower (revisar manualmente)"
fi

if [[ "$RELOAD_NGINX" == "true" ]]; then
    echo "🌐 Recargando Nginx (--reload-nginx solicitado)..."
    sudo systemctl reload nginx
else
    echo "ℹ️  Nginx no se toca (no ejecuta código de la app). Usa --reload-nginx si cambiaste su configuración."
fi

echo ""
echo "======================================================================"
echo "✅ ACTUALIZACIÓN COMPLETADA - $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================================"
echo ""
echo "📊 Estado final:"
sudo "$PROJECT_DIR/manage_services.sh" status
sudo systemctl status celery-worker --no-pager | head -5
sudo systemctl status celery-beat --no-pager | head -5
echo ""
echo "Log completo: $LOG_FILE"
