@echo off
cd /d "%~dp0"

:: --- Check setup ---
if not exist "venv\" (
    echo Setup not complete. Running install.bat first...
    echo.
    call install.bat
    if errorlevel 1 exit /b 1
    echo.
)

:: --- Start Ollama if not running ---
tasklist /fi "imagename eq ollama.exe" 2>nul | findstr /i "ollama.exe" >nul 2>&1
if errorlevel 1 (
    echo Starting Ollama...
    start /b "" ollama serve >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: --- Open browser after short delay ---
start /b "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:7860"

echo Starting Invoice OCR Agent at http://127.0.0.1:7860
echo Press Ctrl+C to stop.
echo.
call venv\Scripts\python web_app.py
