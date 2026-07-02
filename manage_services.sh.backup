#!/bin/bash
# Script para manejar múltiples instancias de Daphne

# Número de instancias (ajustar según CPU cores y carga esperada)
# Configuración estándar: 4 instancias (hasta 1000 requests simultáneos)
# Configuración optimizada para 64GB RAM / 32 cores: 40 instancias
# Configuración optimizada para 120GB RAM / 64 cores: 60 instancias
INSTANCES=33

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
