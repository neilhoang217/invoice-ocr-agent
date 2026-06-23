# Invoice OCR Agent — Workflow Diagrams

## Invoice Upload Flow

```mermaid
flowchart TD
    A([User uploads invoice\nPDF / PNG / JPG / TIFF]) --> B[POST /api/process-stream]

    B --> C[Save to uploads/ as temp file]
    C --> D{File type?}
    D -- PDF --> E[PyMuPDF renders pages\nEasyOCR reads each page]
    D -- Image --> F[EasyOCR reads image]
    E --> G[Raw OCR text]
    F --> G

    G --> H{Ollama running?}
    H -- Yes --> I[Ollama extracts fields\nInvoice No, PO No, Tracking No, etc.]
    H -- No --> J[Regex fallback extraction]
    I --> K[Regex validates PO / Order / Tracking]
    J --> K

    K --> VD[Vendor signature detection\nComputerland Packing Slip / Invoice\nFedEx / others]
    VD --> L[Set lookup priority by vendor]

    L --> M[Try candidates in order:\n1 PO Number\n2 Sales Order\n3 Tracking Number\n4 Invoice Number fallback]
    M --> N{Match found in\nPurchase Orders.xlsx?}

    N -- No --> O[Needs Manual Review\nSave row to corrections.csv]
    N -- Yes --> P[ALL matching rows returned\none per ticket]

    P --> Q[Ollama verifies extracted\nvalues vs matched row]
    Q --> R{Verification?}
    R -- VERIFIED --> S[Save learned PO example\nto learned_po_patterns.jsonl]
    R -- NEEDS_REVIEW\nor LIKELY_INCORRECT --> T[Save row to corrections.csv]

    S --> LABELS
    T --> LABELS
    O --> SKIP([Return result — no label])

    LABELS[Build one PNG label per matched row\nPillow 827x638px 300 DPI\nITD Pickup Item layout] --> U

    U{Duplicate check\nprint_jobs.csv} -- Already printed --> V[DUPLICATE_BLOCKED\nLog to print_jobs.csv]
    U -- File exists, not printed --> W[Return existing file\nno regeneration]
    U -- First time --> X{Auto-print enabled?}

    X -- No --> Y[LABEL_GENERATED\nLog to print_jobs.csv\nFile saved in generated_labels/]
    X -- Yes --> Z[Send PNG to DYMO via CUPS\nlp -d queue -o PageSize=w154h198]
    Z --> AA[LABEL_SENT_TO_PRINTER\nLog to print_jobs.csv\nPNG file deleted]

    V --> RESULT
    W --> RESULT
    Y --> RESULT
    AA --> RESULT([SSE event streamed to browser\nper file as it completes])
```

---

## Manual Order Lookup Flow

```mermaid
flowchart TD
    A([User enters order number\nclicks Look Up & Print]) --> B[POST /api/lookup\norder_number, send_to_printer, printer_queue]

    B --> C[Search Purchase Orders.xlsx\nOrder Number column]
    C --> D{Match found?}

    D -- No --> ERR([Return no match error])
    D -- Yes --> E[Return ALL matching rows\none per ticket]

    E --> F[Build one label per row\nduplicate check per row]
    F --> G{Auto-print enabled?}

    G -- No --> H[LABEL_GENERATED\nFile saved in generated_labels/\nPrint button shown per row]
    G -- Yes --> I[Send each label to DYMO\nPNG file deleted after print]

    H --> RESULT([Return records + label results\nto browser])
    I --> RESULT

    RESULT --> J([User clicks Print button\nper row if needed])
    J --> K[POST /api/print\nlabel_path, printer_queue]
    K --> L[Send to DYMO\nPNG file deleted\nLog LABEL_SENT_TO_PRINTER]
```

---

## Manual Print Flow

```mermaid
flowchart TD
    A([User clicks Print Label\nor Print button]) --> B[POST /api/print\nlabel_path, printer_queue, override_duplicate]

    B --> C{Label file exists?}
    C -- No --> ERR1([404 File not found])
    C -- Yes --> D{override_duplicate = true?}

    D -- Yes --> PRINT
    D -- No --> E{Already printed?\ncheck print_jobs.csv}

    E -- Yes --> ERR2([409 Duplicate blocked])
    E -- No --> PRINT[Send PNG to DYMO via CUPS]

    PRINT --> F[Log LABEL_SENT_TO_PRINTER\nto print_jobs.csv]
    F --> G[Delete PNG file\nfrom generated_labels/]
    G --> OK([200 Sent to printer])
```

---

## Correction Learning Loop

```mermaid
flowchart TD
    A[Lookup fails or AI not VERIFIED] --> B[Row saved to corrections.csv]
    B --> C([User opens Corrections panel\nin web app])
    C --> D[User enters corrected\nlookup value and notes]
    D --> E[POST /api/corrections\nSave corrected values]
    E --> F[Next invoice upload\nloads recent corrections]
    F --> G[Corrections included\nin Ollama extraction prompt]
    G --> H[Better extraction\non similar invoices]
```

---

## Key Files

| File | Role |
|---|---|
| `web_app.py` | HTTP server, API routes, SSE streaming, concurrent processing |
| `invoice_ocr.py` | OCR, field extraction, vendor detection, Ollama prompts, lookup orchestration |
| `excel_lookup.py` | Searches `Purchase Orders.xlsx`, returns all matching rows |
| `dymo_printing.py` | Builds PNG labels, duplicate tracking, CUPS print, file cleanup after print |
| `approved_excel_files/Purchase Orders.xlsx` | Source of truth for order lookups |
| `print_jobs.csv` | Append-only log of every label event (generated, sent, blocked) |
| `corrections.csv` | Rows needing human review; completed corrections feed into AI prompts |
| `learned_po_patterns.jsonl` | Verified PO→Order matches used to improve future AI extractions |
| `generated_labels/` | Temporary PNG label files (deleted after successful print) |
| `web_static/` | Frontend HTML, CSS, JS |
