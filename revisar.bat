@echo off
REM Pasada manual de las tres tiendas que bloquean a GitHub.
REM
REM El Corte Ingles, MediaMarkt y Carrefour filtran las IPs de centro de datos
REM por las que salen los runners de GitHub, pero desde una conexion domestica
REM responden bien. Este .bat las consulta desde aqui y avisa al mismo bot.
REM
REM Lleva su propia memoria (estado-local.json) para no pisar la que mantiene
REM GitHub con Amazon, GAME y Pokemon Center.
REM
REM Las credenciales salen del fichero .env de esta carpeta.

cd /d "%~dp0"
echo.
echo  Revisando MediaMarkt y Carrefour...
echo.

python radar.py --solo mediamarkt,carrefour --estado estado-local.json

echo.
if errorlevel 1 (
  echo  Algo ha fallado. Mira el error de arriba.
) else (
  echo  Listo. Si habia novedades, ya las tienes en Telegram.
)
echo.
pause
