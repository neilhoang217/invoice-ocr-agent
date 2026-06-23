# Windows Setup Guide

Follow these steps in order on the Windows machine.

---

## Step 1 — Install Python

1. Go to https://www.python.org/downloads/
2. Download the latest **Python 3.11** installer (Windows installer 64-bit)
3. Run the installer
4. **Important:** Check the box **"Add Python to PATH"** before clicking Install
5. Verify it worked — open **Command Prompt** and run:
   ```
   python --version
   ```
   You should see something like `Python 3.11.x`

---

## Step 2 — Install Git

1. Go to https://git-scm.com/download/win
2. Download and run the installer (keep all default options)
3. Verify it worked — open a new **Command Prompt** and run:
   ```
   git --version
   ```

---

## Step 3 — Install Ollama

1. Go to https://ollama.com/download
2. Download the **Windows** installer and run it
3. After install, open **Command Prompt** and pull the AI model:
   ```
   ollama pull llama3.1:8b
   ```
   This downloads ~5 GB — let it finish before continuing

---

## Step 4 — Clone the App

Open **Command Prompt** and run:

```
git clone -b windows https://github.com/neilhoang217/invoice-ocr-agent.git
cd invoice-ocr-agent
```

---

## Step 5 — Create a Virtual Environment and Install Dependencies

Still in the `invoice-ocr-agent` folder, run these one at a time:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install pywin32
```

The `pip install -r requirements.txt` step will take several minutes — EasyOCR downloads large model files.

---

## Step 6 — Copy the Excel File

Copy your **Purchase Orders.xlsx** into the `approved_excel_files\` folder inside the project.

---

## Step 7 — Run the App

Make sure Ollama is running (it should start automatically after install). Then:

```
cd invoice-ocr-agent
venv\Scripts\activate
python web_app.py
```

Open a browser and go to:
```
http://127.0.0.1:7860
```

---

## Step 8 — Printing Setup

- The printer queue name in the app must match exactly what Windows shows under **Settings → Bluetooth & devices → Printers & scanners**
- Example: `DYMO LabelWriter 450`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Reinstall Python and check "Add to PATH" |
| `ollama` not found | Restart Command Prompt after installing Ollama |
| App says Ollama not running | Open the Ollama app from the Start menu |
| EasyOCR slow on first run | Normal — it's loading ML models into memory |
| Print fails | Check the printer name matches exactly in Windows settings |
