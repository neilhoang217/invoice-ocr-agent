Invoice OCR (EasyOCR)

Local prototype for extracting invoice and shipping fields from images/PDFs, validating PO/order values against a local Excel workbook, and saving results to CSV.

Main script:

Use `invoice_ocr.py` to read one or more image/PDF files, run OCR, extract common invoice fields, check the PO against `Purchase Orders.xlsx`, print the results, and optionally append them to `extracted.csv`.

Local web app:

Run this command from the project folder:

```
./venv/bin/python web_app.py
```

Then open this address in your browser:

```
http://127.0.0.1:7860
```

The web app lets you drag and drop one or more PDF/PNG/JPG/TIFF files, choose the Ollama model name, turn Excel validation on or off, and optionally save results to `extracted.csv`. Uploaded files are written to the local `uploads/` folder so Python can process them. The AI model still receives OCR text only; it does not receive file-system access.

DYMO label workflow:

- When Excel validation finds a matching order, the app generates a DYMO-compatible PNG label.
- Generated labels are saved in `generated_labels/`.
- Print job activity is logged in `print_jobs.csv`.
- Duplicate print jobs are blocked when the same invoice/PO/tracking lookup and matched Excel row were already sent to a printer.
- DYMO printing uses your computer's CUPS printer queue. First add the DYMO LabelWriter in your operating system (System Settings > Printers & Scanners on macOS), then enter the queue name in the web app and check `Send matched labels to DYMO printer`. The app sends the PNG label with `lp -d <queue-name> <label-file>`.

If the printer option is off, the app still generates the `.png` label file when a match is found. That is useful for testing before sending anything to a real printer.

Command-line app:

```
python invoice_ocr.py /path/to/invoice.png
python invoice_ocr.py /path/to/invoice.pdf
python invoice_ocr.py /path/to/invoice1.pdf /path/to/invoice2.png
python invoice_ocr.py /path/to/invoice.pdf --csv
python invoice_ocr.py /path/to/invoice.pdf --model llama3.3
python invoice_ocr.py /path/to/invoice.pdf --no-excel
python invoice_ocr.py /path/to/invoice.pdf --debug
```

The beginner script uses this workflow:

```
Image/PDF -> EasyOCR -> OCR Text -> Ollama Extraction -> Python Excel Lookup -> Ollama Verification -> CSV
```

By default it asks Ollama to use `llama3.1:8b`. You can use another local model with `--model`, for example `--model llama3.3`.

If Ollama is not running or the model does not return valid JSON, the script falls back to the simpler regex extraction.

Normal output is meant for end users. It only shows whether the order was found and, when found, the matched row plus `Ticket`, `Dept`, `Requester`, `Qty`, and `Item`. Use `--debug` when you want to see OCR fields, lookup candidates, Excel lookup messages, and AI verification details.

Secure Excel lookup:

Put the approved workbooks here:

```
approved_excel_files/Purchase Orders.xlsx
```

Then run:

```
python invoice_ocr.py /path/to/invoice.pdf
```

The script checks the approved workbook automatically after OCR extraction. Use `--no-excel` only when you want to skip this lookup. You can pass one file or multiple PDF/PNG files in the same command.

The AI model never receives file-system access. It extracts structured values from OCR text, and Python reads `Purchase Orders.xlsx`, uses only the `Current Year` sheet, and searches the `Order Number` column for an exact or partial match. The extracted `PO Number` stays literal: if the invoice has no visible PO value, the script keeps `PO Number` as `Needs Manual Review` and writes the value used for Excel into separate `Lookup Value` and `Lookup Value Source` columns. The lookup candidates are tried in this order: `PO Number`, packing-slip or delivery-receipt `Sales order`, FedEx `Tracking Number`, then `Invoice Number` fallback. After Python performs the workbook lookup, the AI model reviews the OCR text, extracted fields, lookup status, and matched row values to produce a verification status, confidence level, issues, recommended PO correction, and correction notes.

When a workbook match is found and AI verification returns `VERIFIED`, the script records a small local example in `learned_po_patterns.jsonl`. Future runs include recent successful PO examples in the Ollama prompt so the model gets better at the PO pattern over time without directly reading the workbook or file system.

Correction learning loop:

The script creates `corrections.csv` with these review columns:

- Lookup Value
- Lookup Value Source
- AI Verification Status
- AI Verification Confidence
- Corrected PO Number
- Corrected Invoice Number
- Corrected Lookup Value
- Corrected Lookup Value Source
- Correction Notes

When the Excel lookup does not match or AI verification is not `VERIFIED`, the script adds a row to `corrections.csv`. Review that row, fill in the corrected columns, and add a short note. On the next run, the script loads recent completed corrections and includes them in the AI extraction and verification prompts. That gives the local agent examples from your real invoices without letting the model read files by itself.

Expected Excel data:

- Workbook: `approved_excel_files/Purchase Orders.xlsx`
- Sheet: `Current Year`
- Column: `Order Number`

The script searches lookup candidates against the `Order Number` column. If the document does not contain a clear PO label, it can use a packing-slip `Sales order`, a FedEx `Tracking Number`, or finally the invoice number fallback. If none of those values are clear, the result is marked for manual review.

Extracted fields:

- Invoice Number
- PO Number
- Lookup Value
- Lookup Value Source
- Lookup Candidates
- Tracking Number
- Carrier Name
- Vendor Name
- Customer Name
- Invoice Date
- Order Number
- Item Number
- Quantity
- Total Amount
- Shipping Address
- Billing Address

Requirements:

```
pip install -r requirements.txt
```

Notes:
- PDF rendering requires PyMuPDF (`pymupdf`).
- For better results, supply higher-resolution PDFs or images.
- SharePoint, Microsoft Graph, authentication, audit logging, and direct printer sending are future production phases, not enabled in this local prototype.
