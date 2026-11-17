@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\instalar.ps1"
if errorlevel 1 (
 echo La instalacion no termino. Revisa el mensaje anterior.
 pause
 exit /b 1
)
echo Listo. Abre OBS y despues INICIAR.cmd.
pause
