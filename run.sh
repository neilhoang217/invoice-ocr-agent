#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- Check setup ---
if [ ! -d "venv" ]; then
    echo "Setup not complete. Running install.sh first..."
    echo ""
    bash install.sh || exit 1
    echo ""
fi

# --- Start Ollama if not running ---
if ! pgrep -x "ollama" > /dev/null 2>&1; then
    echo "Starting Ollama..."
    ollama serve > /dev/null 2>&1 &
    sleep 2
fi

# --- Open browser once the app is actually ready ---
(
    for i in $(seq 1 120); do
        if curl -s -o /dev/null http://127.0.0.1:7860; then
            open http://127.0.0.1:7860
            break
        fi
        sleep 1
    done
) &

echo "Starting Invoice OCR Agent at http://127.0.0.1:7860"
echo "Press Ctrl+C to stop."
echo ""
./venv/bin/python web_app.py
