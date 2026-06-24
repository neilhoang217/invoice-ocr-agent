# Windows Setup Guide

## What You Need

- Windows 10 or 11 (64-bit)
- Internet connection (first run only)

---

## Step 1 — Install Python

1. Go to https://www.python.org/downloads/
2. Download the latest **Python 3.11** installer (Windows installer 64-bit)
3. Run the installer
4. **Important:** Check **"Add Python to PATH"** before clicking Install
5. Verify — open **Command Prompt** and run:
   ```
   python --version
   ```
   You should see `Python 3.11.x`

---

## Step 2 — Install Ollama

1. Go to https://ollama.com/download
2. Download the **Windows** installer and run it
3. After install, open **Command Prompt** and verify:
   ```
   ollama --version
   ```

---

## Step 3 — Copy the App Folder

Copy the entire `invoice-ocr-agent` folder to the Windows machine.

---

## Step 4 — Run First-Time Setup

Open **Command Prompt**, navigate to the app folder, and run:

```
install.bat
```

This will automatically:
- Check your internet connection
- Check Python 3.9+ is installed
- Create a Python virtual environment
- Install all Python dependencies (including pywin32 for printing)
- Verify Ollama is installed
- Download the AI model (~5 GB — takes a few minutes)

---

## Step 5 — Add Your Excel File

Copy **Purchase Orders.xlsx** into the `approved_excel_files\` folder inside the app.

---

## Step 6 — Start the App

Double-click **run.bat** (or run it from Command Prompt):

```
run.bat
```

The app will open automatically in your browser at:
```
http://127.0.0.1:7860
```

To stop the app, press **Ctrl+C** in the Command Prompt window.

---

## Printing Setup

- The printer queue name in the app must match exactly what Windows shows under:
  **Settings → Bluetooth & devices → Printers & scanners**
- Example: `DYMO LabelWriter 450`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Reinstall Python and check "Add to PATH" |
| `ollama` not found | Restart Command Prompt after installing Ollama |
| App says Ollama not running | Open the Ollama app from the Start menu |
| EasyOCR slow on first run | Normal — loading ML models into memory |
| Print fails | Check the printer name matches exactly in Windows settings |
| `install.bat` blocked by antivirus | Right-click → Properties → Unblock, then retry |
