@echo off
REM Script para ejecutar múltiples instancias de Daphne para alta carga en Windows
REM Útil para distribuir la carga entre varios procesos

set HOST=127.0.0.1
set BASE_PORT=8000
set INSTANCES=4

echo 🚀 Iniciando %INSTANCES% instancias de Daphne para alta carga
echo    Cada instancia escuchará en un puerto diferente
echo    Usa un load balancer (nginx) para distribuir la carga
echo.

for /L %%i in (1,1,%INSTANCES%) do (
    set /a PORT=%BASE_PORT% + %%i - 1
    echo    Instancia %%i: http://%HOST%:%PORT%
    start "Daphne Instance %%i" daphne -b %HOST% -p %PORT% --access-log - --proxy-headers --http-timeout 60 --websocket-timeout 60 ubuntu.asgi:application
)

echo.
echo ✅ Todas las instancias iniciadas
echo    Cierra las ventanas para detener las instancias
pause

