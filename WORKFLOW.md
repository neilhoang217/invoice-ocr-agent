# Invoice OCR Agent — Workflow Diagram

## Main Processing Flow

```mermaid
flowchart TD
    A([User uploads invoice\nPDF / PNG / JPG / TIFF]) --> B[POST /api/process]

    B --> C[Save to uploads/ as temp file]
    C --> D{File type?}
    D -- PDF --> E[PyMuPDF renders pages\nEasyOCR reads each page]
    D -- Image --> F[EasyOCR reads image]
    E --> G[Raw OCR text]
    F --> G

    G --> H{Ollama running?}
    H -- Yes --> I[Ollama AI extracts fields\nInvoice No, PO No, Tracking No, etc.]
    H -- No --> J[Regex fallback extraction]
    I --> K[Regex validates & corrects\nPO Number, Order Number]
    J --> K

    K --> L[Excel lookup\napproved_excel_files/Purchase Orders.xlsx]
    L --> M[Try candidates in order:\n1 PO Number\n2 Order Number\n3 Tracking Number\n4 Invoice Number fallback]
    M --> N{Match found?}

    N -- No --> O[Needs Manual Review\nSave row to corrections.csv]
    N -- Yes --> P[Ollama AI verifies\nextracted values vs matched row]

    P --> Q{Verification status?}
    Q -- VERIFIED --> R[Save learned PO example\nto learned_po_patterns.jsonl]
    Q -- NEEDS_REVIEW\nor LIKELY_INCORRECT --> S[Save row to corrections.csv]

    R --> T[Build PNG label\nPillow 827x638px at 300 DPI\nITD Pickup Item layout]
    S --> T
    O --> SKIP([Return result — no label])

    T --> U{Duplicate check\nprint_jobs.csv}
    U -- Already printed --> V[Status: DUPLICATE_BLOCKED\nLog to print_jobs.csv\nLabel file still saved for Print Again]
    U -- First time --> W{Auto-print enabled?}

    W -- No --> X[Status: LABEL_GENERATED\nLog to print_jobs.csv]
    W -- Yes --> Y[Send PNG to DYMO via CUPS\nlp -d queue -o PageSize=w154h198]
    Y --> Z[Status: LABEL_SENT_TO_PRINTER\nLog to print_jobs.csv]

    V --> RESULT([Return JSON result to browser])
    X --> RESULT
    Z --> RESULT
```

---

## Manual Print Flow

```mermaid
flowchart TD
    A([User clicks Print Label\nor Print Again button]) --> B[POST /api/print\nlabel_path, printer_queue, override_duplicate]

    B --> C{Label file exists?}
    C -- No --> ERR1([404 File not found])
    C -- Yes --> D{override_duplicate = true?}

    D -- Yes --> PRINT[Send PNG to DYMO via CUPS\nlp -d queue -o PageSize=w154h198]
    D -- No --> E{Already printed?\ncheck print_jobs.csv}

    E -- Yes --> ERR2([409 Already printed\nButton changes to Print Again])
    E -- No --> PRINT

    PRINT --> F[Log LABEL_SENT_TO_PRINTER\nto print_jobs.csv]
    F --> OK([200 Sent to printer])
```

---

## Key Files

| File | Role |
|---|---|
| `web_app.py` | HTTP server, routes, concurrent file processing |
| `invoice_ocr.py` | OCR reading, field extraction, Ollama prompts, Excel lookup orchestration |
| `excel_lookup.py` | Searches `Purchase Orders.xlsx` Current Year sheet |
| `dymo_printing.py` | Builds PNG label, duplicate tracking, CUPS print command |
| `approved_excel_files/Purchase Orders.xlsx` | Source of truth for order lookups |
| `print_jobs.csv` | Append-only log of every label event (generated, sent, blocked) |
| `corrections.csv` | Rows needing human review; corrected rows feed back into AI prompts |
| `learned_po_patterns.jsonl` | Verified PO→Order matches used to improve future AI extractions |
| `generated_labels/` | Saved PNG label files |
| `web_static/` | Frontend HTML, CSS, JS |
