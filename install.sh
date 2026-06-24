#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo "  Invoice OCR Agent - First-Time Setup"
echo "================================================"
echo ""

# --- Internet check ---
echo "Checking internet connection..."
if ! curl -s --connect-timeout 5 https://pypi.org > /dev/null 2>&1; then
    echo "ERROR: No internet connection detected."
    echo "Please connect to the internet and run this script again."
    exit 1
fi
echo "Internet connection: OK"
echo ""

# --- Python check ---
echo "Checking Python..."
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3 not found."
    echo "Download and install from: https://www.python.org/downloads/"
    exit 1
fi

PYTHON_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
PYTHON_VERSION="${PYTHON_MAJOR}.${PYTHON_MINOR}"

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]; }; then
    echo "ERROR: Python 3.9 or newer is required (found $PYTHON_VERSION)."
    echo "Download from: https://www.python.org/downloads/"
    exit 1
fi
echo "Python $PYTHON_VERSION: OK"
echo ""

# --- Virtual environment ---
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    "$PYTHON" -m venv venv
    echo "Virtual environment created."
else
    echo "Virtual environment: already exists"
fi
echo ""

# --- Python dependencies ---
echo "Installing Python dependencies (this may take several minutes on first run)..."
./venv/bin/pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt --quiet
echo "Python dependencies: installed"
echo ""

# --- Required directories ---
mkdir -p uploads generated_labels approved_excel_files

# --- Ollama check / install ---
echo "Checking Ollama..."
if command -v ollama &> /dev/null; then
    echo "Ollama: already installed ($(ollama --version 2>/dev/null | head -1))"
else
    echo "Ollama not found. Attempting to install..."
    echo ""

    if command -v brew &> /dev/null; then
        echo "Installing via Homebrew..."
        brew install ollama
        echo "Ollama: installed"
    else
        echo "------------------------------------------------------"
        echo "  Homebrew not found. Please install Ollama manually:"
        echo ""
        echo "  Option A (recommended): Install Homebrew first"
        echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "    then re-run: bash install.sh"
        echo ""
        echo "  Option B: Download the Mac app"
        echo "    https://ollama.com/download"
        echo "    After installing, re-run: bash install.sh"
        echo "------------------------------------------------------"
        exit 1
    fi
fi
echo ""

# --- Pull AI model ---
echo "Checking AI model (llama3.1:8b)..."
if ollama list 2>/dev/null | grep -q "llama3.1:8b"; then
    echo "Model llama3.1:8b: already downloaded"
else
    echo "Downloading llama3.1:8b (~5 GB — this will take a while on first run)..."

    # Start ollama server temporarily if not running
    STARTED_OLLAMA=false
    if ! pgrep -x "ollama" > /dev/null 2>&1; then
        ollama serve > /dev/null 2>&1 &
        OLLAMA_PID=$!
        STARTED_OLLAMA=true
        sleep 3
    fi

    ollama pull llama3.1:8b

    if $STARTED_OLLAMA; then
        kill $OLLAMA_PID 2>/dev/null || true
    fi

    echo "Model: downloaded"
fi
echo ""

echo "================================================"
echo "  Setup complete!"
echo "  Run ./run.sh to start the app."
echo "================================================"
