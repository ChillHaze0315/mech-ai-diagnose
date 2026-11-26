@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- launch the GUI without a console window ----
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0app.py"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "%~dp0app.py"
    exit /b 0
)

echo Python not found. Install Python 3.10+ and add it to PATH.
pause
