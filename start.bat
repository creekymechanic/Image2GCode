@echo off
title Image2GCode
cd /d "%~dp0"

echo [1/3] Starting Ollama...
start "" ollama serve

echo [2/3] Waiting for Ollama to be ready...
:wait_loop
timeout /t 1 /nobreak >nul
curl -s http://localhost:11434 >nul 2>&1
if errorlevel 1 goto wait_loop

echo [3/3] Starting Image2GCode...
call venv\Scripts\activate.bat
start "" https://localhost:5000
python app.py

pause
