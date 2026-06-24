@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo ================================================
echo   Invoice OCR Agent - First-Time Setup
echo ================================================
echo.

:: --- Internet check ---
echo Checking internet connection...
curl -s --connect-timeout 5 https://pypi.org >nul 2>&1
if errorlevel 1 (
    echo ERROR: No internet connection detected.
    echo Please connect to the internet and run this script again.
    pause
    exit /b 1
)
echo Internet connection: OK
echo.

:: --- Python check ---
echo Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Download and install from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYTHON_VERSION=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set MAJOR=%%a
    set MINOR=%%b
)

if %MAJOR% LSS 3 (
    echo ERROR: Python 3.9 or newer is required ^(found %PYTHON_VERSION%^).
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)
if %MAJOR% EQU 3 if %MINOR% LSS 9 (
    echo ERROR: Python 3.9 or newer is required ^(found %PYTHON_VERSION%^).
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo Python %PYTHON_VERSION%: OK
echo.

:: --- Virtual environment ---
if not exist "venv\" (
    echo Creating Python virtual environment...
    python -m venv venv
    echo Virtual environment created.
) else (
    echo Virtual environment: already exists
)
echo.

:: --- Python dependencies ---
echo Installing Python dependencies ^(this may take several minutes on first run^)...
call venv\Scripts\pip install --upgrade pip --quiet
call venv\Scripts\pip install -r requirements.txt --quiet
call venv\Scripts\pip install pywin32 --quiet
echo Python dependencies: installed
echo.

:: --- Required directories ---
if not exist "uploads\" mkdir uploads
if not exist "generated_labels\" mkdir generated_labels
if not exist "approved_excel_files\" mkdir approved_excel_files

:: --- Ollama check / install ---
echo Checking Ollama...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo Ollama not found.
    echo.
    echo Please install Ollama manually:
    echo   1. Go to https://ollama.com/download
    echo   2. Download and run the Windows installer
    echo   3. After installing, run this script again
    echo.
    pause
    exit /b 1
) else (
    for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo Ollama %%v: OK
)
echo.

:: --- Pull AI model ---
echo Checking AI model ^(llama3.1:8b^)...
ollama list 2>nul | findstr /i "llama3.1:8b" >nul 2>&1
if errorlevel 1 (
    echo Downloading llama3.1:8b ^(~5 GB - this will take a while on first run^)...
    ollama pull llama3.1:8b
    echo Model: downloaded
) else (
    echo Model llama3.1:8b: already downloaded
)
echo.

echo ================================================
echo   Setup complete!
echo   Run run.bat to start the app.
echo ================================================
echo.
pause
