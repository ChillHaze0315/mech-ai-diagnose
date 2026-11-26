@echo off
chcp 65001 >nul
rem ---- start the local model service (Ollama) in its own window ----
where ollama >nul 2>nul
if %errorlevel%==0 (
    echo Starting Ollama at 127.0.0.1:11434 ...
    start "Ollama" ollama serve
    timeout /t 4 >nul
    echo Done. You can now double-click 启动.bat
    pause
    exit /b 0
)

echo Ollama not found. Download it from https://ollama.com and install first.
pause
