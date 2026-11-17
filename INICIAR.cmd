@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Primero ejecuta INSTALAR.cmd.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -X utf8 contador.py
if errorlevel 1 pause
