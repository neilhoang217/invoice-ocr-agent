# Invoice OCR Agent

Local web app for extracting invoice and shipping fields from PDFs and images, validating order numbers against a Purchase Orders workbook, and printing DYMO pickup labels for every matched ticket.

---

## Quick Start

```
./venv/bin/python web_app.py
```

Open `http://127.0.0.1:7860` in your browser.

---

## Web App Features

### Upload Invoices
Drag and drop one or more PDF, PNG, JPG, or TIFF files. The app:
1. Runs EasyOCR to extract raw text
2. Sends the text to a local Ollama model (`llama3.1:8b`) to extract structured fields
3. Validates the extracted order/PO value against `Purchase Orders.xlsx`
4. Generates a DYMO label for **every matching ticket row** in the workbook
5. Optionally sends all labels to the DYMO printer automatically

Files are locked for new uploads while a job is processing. Once complete, the button re-enables.

### Manual Order Lookup
Below the upload section is a **Manual Order Lookup** input. Type any order number, press Enter or click **Look Up & Print**, and the app:
- Searches `Purchase Orders.xlsx` directly (no OCR needed)
- Lists every matching ticket row (Ticket, Dept, Requester, Qty, Item)
- Generates a label per row
- Shows a **Print** button per row to send each label individually to the DYMO printer

### Corrections Panel
When the Excel lookup fails or AI verification is not confident, a row is added to `corrections.csv`. Open the **Corrections** panel in the UI to enter a corrected lookup value. Saved corrections feed back into future AI prompts automatically.

---

## Vendor Template Detection

The agent recognises vendor-specific document templates from the letterhead and adjusts its lookup priority accordingly:

| Detected Vendor | Lookup Priority |
|---|---|
| Computerland Packing Slip | Sales Order → PO Number |
| Computerland Invoice | Sales Order → PO Number |
| FedEx | Tracking Number → PO Number |
| All others | PO Number → Sales Order |

To add a new vendor, add one line to `VENDOR_SIGNATURES` in `invoice_ocr.py`.

---

## Multi-Ticket Labels

If an order number matches multiple rows in `Purchase Orders.xlsx`, the app generates and prints **one label per row**. For example, order 11773 with 5 ticket rows produces 5 labels — one per requester.

---

## DYMO Label Layout

Labels are 54 mm × 70 mm at 300 DPI (landscape). Each label shows:

```
ITD Pickup Item
──────────────
Order: 11773
Ticket: RITM0024764
Dept: ESD
Requester: Julia Leal
Qty: 1

Item:
Adobe Pro
```

---

## Label File Cleanup

- Labels are saved as PNG files in `generated_labels/` when generated
- **After a successful print, the PNG file is deleted automatically**
- The print event is still recorded in `print_jobs.csv` for duplicate tracking
- If you click Look Up & Print twice without printing, the existing file is reused (no overwrite)
- If a label was already sent to the printer, a second print attempt is blocked as a duplicate

---

## DYMO Printer Setup (macOS)

1. Add the DYMO LabelWriter in **System Settings → Printers & Scanners**
2. Note the queue name (e.g. `DYMO_LabelWriter_450`)
3. Enter it in the **DYMO Printer Queue Name** field in the web app
4. Check **Send matched labels to DYMO printer**

The app sends the PNG label with `lp -d <queue-name> -o PageSize=w154h198 <label-file>`.

If the printer option is off, labels are generated and saved but not sent. Use this for testing.

---

## Excel Workbook

- File: `approved_excel_files/Purchase Orders.xlsx`
- Sheet: `Current Year`
- Required column: `Order Number`

The AI model never reads the workbook directly. Python performs all lookups.

---

## Lookup Candidate Order

When multiple values could be the lookup key, the app tries them in this order (vendor-adjusted):

1. PO Number (from a visible PO/P.O./Purchase Order label)
2. Sales Order / Order Number
3. FedEx Tracking Number
4. Invoice Number (fallback)

---

## Learning Loop

- **`learned_po_patterns.jsonl`** — when AI verification returns `VERIFIED`, the matched PO→Order pair is saved and included in future Ollama prompts
- **`corrections.csv`** — when lookup fails or verification is not confident, a review row is saved; completed corrections are included in future prompts

---

## Key Files

| File | Role |
|---|---|
| `web_app.py` | HTTP server, API routes, concurrent file processing, SSE streaming |
| `invoice_ocr.py` | OCR, field extraction, vendor detection, Ollama prompts, lookup orchestration |
| `excel_lookup.py` | Searches `Purchase Orders.xlsx`, returns all matching rows |
| `dymo_printing.py` | Builds PNG labels, duplicate tracking, CUPS print, file cleanup |
| `approved_excel_files/Purchase Orders.xlsx` | Source of truth for order lookups |
| `print_jobs.csv` | Append-only log of every label event |
| `corrections.csv` | Rows needing human review; corrections feed back into AI prompts |
| `learned_po_patterns.jsonl` | Verified PO→Order matches for improving future extractions |
| `web_static/` | Frontend HTML, CSS, JS |

---

## Requirements

```
pip install -r requirements.txt
```

Requires Ollama running locally with `llama3.1:8b` pulled:
```
ollama pull llama3.1:8b
```

---

## Branches

| Branch | Platform |
|---|---|
| `main` | macOS |
| `windows` | Windows (platform-aware fonts and printing via `pywin32`) |

See `WINDOWS_SETUP.md` on the `windows` branch for setup instructions.
