#!/bin/bash
# Script de backup de logs de UDID
# Hace backup de logs cuando alcanzan cierto tamaño o periódicamente
# Autor: Sistema UDID
# Fecha: 2026-01-22

set -euo pipefail

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

# Directorio donde se guardan los logs originales
LOG_DIR="/var/log/udid"
DJANGO_LOG="/opt/udid/server.log"
NGINX_ACCESS_LOG="/var/log/nginx/udid_access.log"
NGINX_ERROR_LOG="/var/log/nginx/udid_error.log"

# Directorio de backup
BACKUP_BASE_DIR="/var/backups/udid/logs"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_BASE_DIR/$DATE"

# Configuración de tamaño máximo (en MB) antes de forzar backup
MAX_SIZE_MB=100

# Retención de backups (días)
RETENTION_DAYS=30

# Archivo de log del script de backup
SCRIPT_LOG="/var/log/udid/backup_logs.log"

# ============================================================================
# FUNCIONES
# ============================================================================

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$SCRIPT_LOG"
}

check_size() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return 1
    fi
    
    local size_bytes=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null || echo "0")
    local size_mb=$((size_bytes / 1024 / 1024))
    
    if [ "$size_mb" -ge "$MAX_SIZE_MB" ]; then
        return 0  # Archivo es mayor o igual al tamaño máximo
    else
        return 1  # Archivo es menor al tamaño máximo
    fi
}

backup_file() {
    local source_file="$1"
    local dest_dir="$2"
    
    if [ ! -f "$source_file" ]; then
        log_message "⚠️  Archivo no existe: $source_file (saltando)"
        return 0
    fi
    
    local filename=$(basename "$source_file")
    local dest_file="$dest_dir/$filename"
    
    # Crear directorio de destino si no existe
    mkdir -p "$dest_dir"
    
    # Copiar archivo
    if cp "$source_file" "$dest_file"; then
        # Comprimir el backup
        if gzip "$dest_file"; then
            log_message "✅ Backup creado: $dest_file.gz ($(du -h "$dest_file.gz" | cut -f1))"
            return 0
        else
            log_message "⚠️  Backup creado pero falló compresión: $dest_file"
            return 0
        fi
    else
        log_message "❌ Error al crear backup de: $source_file"
        return 1
    fi
}

backup_logs_by_size() {
    local files_to_check=(
        "$LOG_DIR/celery-worker.log"
        "$LOG_DIR/celery-beat.log"
        "$LOG_DIR/celery-flower.log"
        "$DJANGO_LOG"
        "$NGINX_ACCESS_LOG"
        "$NGINX_ERROR_LOG"
    )
    
    local needs_backup=false
    
    log_message "🔍 Verificando tamaño de archivos de log..."
    
    for file in "${files_to_check[@]}"; do
        if check_size "$file"; then
            log_message "📊 Archivo alcanzó tamaño máximo: $file ($(du -h "$file" | cut -f1))"
            needs_backup=true
        fi
    done
    
    if [ "$needs_backup" = true ]; then
        log_message "📦 Iniciando backup por tamaño..."
        create_backup
    else
        log_message "✅ Todos los archivos están por debajo del tamaño máximo ($MAX_SIZE_MB MB)"
    fi
}

create_backup() {
    log_message "🚀 Iniciando backup de logs..."
    
    # Crear directorio de backup con timestamp
    mkdir -p "$BACKUP_DIR"
    
    local backup_count=0
    local error_count=0
    
    # Backup de logs de UDID
    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            if backup_file "$log_file" "$BACKUP_DIR/udid"; then
                ((backup_count++))
            else
                ((error_count++))
            fi
        fi
    done
    
    # Backup de log de Django
    if [ -f "$DJANGO_LOG" ]; then
        if backup_file "$DJANGO_LOG" "$BACKUP_DIR/django"; then
            ((backup_count++))
        else
            ((error_count++))
        fi
    fi
    
    # Backup de logs de Nginx
    if [ -f "$NGINX_ACCESS_LOG" ]; then
        if backup_file "$NGINX_ACCESS_LOG" "$BACKUP_DIR/nginx"; then
            ((backup_count++))
        else
            ((error_count++))
        fi
    fi
    
    if [ -f "$NGINX_ERROR_LOG" ]; then
        if backup_file "$NGINX_ERROR_LOG" "$BACKUP_DIR/nginx"; then
            ((backup_count++))
        else
            ((error_count++))
        fi
    fi
    
    # Crear archivo de información del backup
    cat > "$BACKUP_DIR/backup_info.txt" <<EOF
Backup de Logs UDID
===================
Fecha: $(date '+%Y-%m-%d %H:%M:%S')
Directorio: $BACKUP_DIR
Archivos respaldados: $backup_count
Errores: $error_count
Tamaño total: $(du -sh "$BACKUP_DIR" | cut -f1)
EOF
    
    log_message "✅ Backup completado: $backup_count archivos respaldados, $error_count errores"
    log_message "📁 Ubicación: $BACKUP_DIR"
    
    # Limpiar backups antiguos
    cleanup_old_backups
}

cleanup_old_backups() {
    log_message "🧹 Limpiando backups antiguos (más de $RETENTION_DAYS días)..."
    
    local deleted_count=0
    local freed_space=0
    
    if [ -d "$BACKUP_BASE_DIR" ]; then
        while IFS= read -r -d '' backup_path; do
            local backup_date=$(basename "$backup_path")
            local backup_timestamp=$(echo "$backup_date" | cut -d'_' -f1-2 | tr '_' ' ')
            
            # Calcular días desde el backup
            local days_old=0
            if command -v date >/dev/null 2>&1; then
                local backup_epoch=$(date -d "$backup_timestamp" +%s 2>/dev/null || echo "0")
                local current_epoch=$(date +%s)
                if [ "$backup_epoch" -gt 0 ]; then
                    days_old=$(( (current_epoch - backup_epoch) / 86400 ))
                fi
            fi
            
            # Si no se pudo calcular o tiene más de RETENTION_DAYS días, eliminar
            if [ "$days_old" -ge "$RETENTION_DAYS" ] || [ "$days_old" -eq 0 ]; then
                local size=$(du -sk "$backup_path" 2>/dev/null | cut -f1 || echo "0")
                if rm -rf "$backup_path"; then
                    ((deleted_count++))
                    freed_space=$((freed_space + size))
                    log_message "🗑️  Eliminado backup antiguo: $backup_date"
                fi
            fi
        done < <(find "$BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null || true)
    fi
    
    if [ "$deleted_count" -gt 0 ]; then
        log_message "✅ Limpieza completada: $deleted_count backups eliminados (~$(numfmt --to=iec-i --suffix=B $((freed_space * 1024)) 2>/dev/null || echo "${freed_space}KB") liberados)"
    else
        log_message "✅ No hay backups antiguos para eliminar"
    fi
}

show_stats() {
    echo "=========================================="
    echo "📊 Estadísticas de Backup de Logs"
    echo "=========================================="
    echo ""
    echo "📁 Directorio de backup: $BACKUP_BASE_DIR"
    echo "📏 Tamaño máximo antes de backup: ${MAX_SIZE_MB}MB"
    echo "🗓️  Retención: $RETENTION_DAYS días"
    echo ""
    
    if [ -d "$BACKUP_BASE_DIR" ]; then
        local total_backups=$(find "$BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
        local total_size=$(du -sh "$BACKUP_BASE_DIR" 2>/dev/null | cut -f1 || echo "0")
        
        echo "📦 Total de backups: $total_backups"
        echo "💾 Tamaño total: $total_size"
        echo ""
        echo "Últimos 5 backups:"
        find "$BACKUP_BASE_DIR" -mindepth 1 -maxdepth 1 -type d -printf "%T@ %p\n" | \
            sort -rn | head -5 | while read timestamp path; do
            echo "  - $(basename "$path") ($(du -sh "$path" | cut -f1))"
        done
    else
        echo "⚠️  No hay backups aún"
    fi
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================

main() {
    # Crear directorio de log del script si no existe
    mkdir -p "$(dirname "$SCRIPT_LOG")"
    touch "$SCRIPT_LOG"
    
    case "${1:-auto}" in
        "auto"|"size")
            # Modo automático: verificar tamaño y hacer backup si es necesario
            backup_logs_by_size
            ;;
        "force"|"now")
            # Forzar backup inmediato
            log_message "🔄 Forzando backup de todos los logs..."
            create_backup
            ;;
        "cleanup")
            # Solo limpiar backups antiguos
            cleanup_old_backups
            ;;
        "stats"|"status")
            # Mostrar estadísticas
            show_stats
            ;;
        "test")
            # Modo de prueba (no hace backup real)
            log_message "🧪 Modo de prueba - Verificando configuración..."
            echo "Archivos que se respaldarían:"
            for file in "$LOG_DIR"/*.log "$DJANGO_LOG" "$NGINX_ACCESS_LOG" "$NGINX_ERROR_LOG"; do
                if [ -f "$file" ]; then
                    local size=$(du -h "$file" | cut -f1)
                    if check_size "$file"; then
                        echo "  ✅ $file ($size) - REQUIERE BACKUP"
                    else
                        echo "  ⏳ $file ($size) - OK"
                    fi
                fi
            done
            ;;
        *)
            echo "Uso: $0 {auto|force|cleanup|stats|test}"
            echo ""
            echo "Comandos:"
            echo "  auto     - Verificar tamaño y hacer backup si es necesario (por defecto)"
            echo "  force    - Forzar backup inmediato de todos los logs"
            echo "  cleanup  - Limpiar backups antiguos"
            echo "  stats    - Mostrar estadísticas de backups"
            echo "  test     - Modo de prueba (no hace backup real)"
            exit 1
            ;;
    esac
}

main "$@"
