#!/bin/bash
# Script para ejecutar múltiples instancias de Daphne para alta carga
# Útil para distribuir la carga entre varios procesos

HOST="127.0.0.1"
BASE_PORT=8000
INSTANCES=4  # Número de instancias de Daphne

echo "🚀 Iniciando $INSTANCES instancias de Daphne para alta carga"
echo "   Cada instancia escuchará en un puerto diferente"
echo "   Usa un load balancer (nginx) para distribuir la carga"
echo ""

for i in $(seq 1 $INSTANCES); do
    PORT=$((BASE_PORT + i - 1))
    echo "   Instancia $i: http://$HOST:$PORT"
    daphne -b $HOST -p $PORT --access-log - --proxy-headers \
           --http-timeout 60 --websocket-timeout 60 \
           ubuntu.asgi:application &
done

echo ""
echo "✅ Todas las instancias iniciadas"
echo "   Presiona Ctrl+C para detener todas"
wait

