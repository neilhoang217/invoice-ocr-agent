import csv
import io
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
import warnings

import easyocr
import numpy as np
from PIL import Image

from excel_lookup import lookup_po_number, TRACKING_NUMBER_COLUMN_NAMES

DEFAULT_MODEL = "llama3.1:8b"
LEARNED_PO_PATTERNS_FILE = "learned_po_patterns.jsonl"
MAX_LEARNED_PO_EXAMPLES = 8
CORRECTIONS_FILE = "corrections.csv"
MAX_CORRECTION_EXAMPLES = 8
OLLAMA_RETRY_DELAY = 2

_DEBUG = "--debug" in sys.argv

CORRECTION_FIELD_NAMES = [
    "Source File",
    "Extracted PO Number",
    "Extracted Invoice Number",
    "Lookup Value",
    "Lookup Value Source",
    "Excel Lookup Status",
    "AI Verification Status",
    "AI Verification Confidence",
    "Corrected PO Number",
    "Corrected Invoice Number",
    "Corrected Lookup Value",
    "Corrected Lookup Value Source",
    "Correction Notes",
]

AGENT_EXTRACTION_LESSONS = [
    # --- General PO / Invoice rules ---
    (
        "If an invoice has a P.O. # / PO Number header but the cell below it is blank, "
        "do not use nearby table text such as Sales Rep. Name, Sales1, Ship Date, Terms, "
        "or Due Date as the PO Number."
    ),
    (
        "For blank PO fields, use the Invoice Number as the lookup value. Example: "
        "a blank P.O. # field with Invoice # INV1048 should use INV1048."
    ),
    (
        "City purchase order numbers are typically 5–6 digits (e.g., 546010, 67031) or "
        "hyphenated (e.g., 67030-11959, 67030-4955). If you see a number in this format "
        "next to a PO/P.O./Purchase Order label, it is almost certainly the city's PO Number."
    ),
    # --- Computerland ---
    (
        "For Computerland packing slips, the 'Sales order' value (e.g., 'ORD-16625-M7V2B1') is "
        "the primary lookup value — extract it into the Order Number field. "
        "The format is always ORD-NNNNN-XXXXXX (digits, dash, alphanumeric). "
        "OCR may confuse '2' with 'Z' or 'O' with '0' — read each character carefully. "
        "If a 'Requisition' number is also present (e.g., 'Requisition: 67030-4955'), extract "
        "it into the PO Number field as a fallback lookup."
    ),
    # --- FedEx shipping labels ---
    (
        "For FedEx shipping labels, the tracking number is labeled 'TRK#' followed by "
        "space-separated digit groups (e.g., 'TRK#:5263 3769 1880'). "
        "Extract only the digits without spaces into Tracking Number (e.g., '526337691880'). "
        "OCR sometimes reads 'TRK#' as 'TRKH' or 'TRK' — look for any variant near the large digits."
    ),
    (
        "For FedEx shipping labels, the 'Order#' field (e.g., 'Order#: 917994866', or OCR may "
        "read it as 'Ordern; 917994866') is the SHIPPER's internal order reference — it belongs "
        "to the vendor (e.g., B&H Photo), NOT to the city. Do NOT put it in the Order Number field. "
        "Leave Order Number as 'Needs Manual Review'."
    ),
    (
        "For FedEx shipping labels, 'PO#: 546010' is the city's Purchase Order reference — "
        "extract it into the PO Number field. "
        "OCR sometimes misreads 'PO#' as 'PC' — if you see 'PC: NNNNNN' near the bottom of a "
        "FedEx label, treat it as the PO Number."
    ),
    (
        "For FedEx shipping labels, 'REF:OP 67031' in the TO address block contains a city "
        "order reference number (e.g., 67031). If no other PO is found, extract the number "
        "after 'REF:OP' into the PO Number field."
    ),
    (
        "For FedEx documents (proof-of-delivery, invoices), the FedEx Tracking Number is the "
        "preferred lookup value. Extract it into the Tracking Number field."
    ),
    # --- ISSQUARED ---
    (
        "For ISSQUARED packing lists, the 'Customer PO#' field (e.g., 'Customer PO#: 67030-11959') "
        "is the PO Number. Extract it into the PO Number field. "
        "The format is typically NNNNN-NNNNN (two groups of digits separated by a dash). "
        "It also appears as 'PL Note 1: 67030-11959' at the bottom of the page — same value."
    ),
    (
        "For ISSQUARED packing lists, carton detail lines may contain a FedEx Track# "
        "(e.g., 'Track# 526661585750') and the document may say 'SHIPPED VIA: FedEx Ground'. "
        "These are shipment carrier details — do NOT treat the document as a FedEx document. "
        "Vendor Name is 'ISSQUARED', and the primary lookup value is the Customer PO#, not the track number."
    ),
    # --- Vendor Name ---
    (
        "Vendor Name should be the company that ISSUED or SHIPPED the document — the letterhead, "
        "the FROM address, or the carrier. For FedEx shipping labels, Vendor Name is 'FedEx', "
        "not the department that received the package (e.g., not 'Information Technology'). "
        "For Computerland documents, Vendor Name is 'Computerland'. "
        "For ISSQUARED documents, Vendor Name is 'ISSQUARED'."
    ),
]

VENDOR_SALES_ORDER_PRIORITY = {"COMPUTERLAND", "COMPUTERLAND PACKING SLIP", "COMPUTERLAND INVOICE", "ISSQUARED", "ISSQUARED PACKING LIST"}
VENDOR_TRACKING_PRIORITY = {"FEDEX"}

# Each entry: (display name, [patterns]) — ALL patterns must match to identify the vendor.
# More specific entries (more patterns) must come before broader fallbacks.
VENDOR_SIGNATURES = [
    ("Computerland Packing Slip", [r"\bcomputerland\b", r"\bpacking\s+slip\b"]),
    ("Computerland Invoice",      [r"\bcomputerland\b", r"\binvoice\b"]),
    ("Computerland",              [r"\bcomputerland\b"]),
    ("ISSQUARED Packing List",    [r"\bissquared\b", r"\bpacking\s+list\b"]),
    ("ISSQUARED",                 [r"\bissquared\b"]),
    ("FedEx",                     [r"\bfed\s*ex\b"]),
]

# PyMuPDF is only needed when the input file is a PDF.
# Images like PNG/JPG do not use this library.
try:
    import fitz  # PyMuPDF, used only for PDF files
except Exception:
    fitz = None


# This list tells the program which invoice fields to look for.
# Each item has:
# 1. The name we want to show in the final results.
# 2. A regex pattern, which is a text-search rule.
#
# Example:
# The "Invoice Number" pattern looks for text like:
# Invoice Number: INV-12345
FIELDS = [
    ("Invoice Number", r"\binvoice\s*(?:number|no\.?|#)\s*[:#-]?\s*(#?[A-Z0-9]*[0-9][A-Z0-9-]*)\b"),
    ("PO Number", r"\bp\s*\.?\s*[o0]\s*\.?\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{2,})\b"),
    ("Tracking Number", r"\btracking\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{5,})\b"),
    ("Carrier Name", r"\b(UPS|FEDEX|USPS|DHL)\b"),
    ("Vendor Name", r"\bvendor\s*(?:name)?\s*[:#-]?\s*([A-Z][A-Z0-9 &.,'-]{1,60})"),
    ("Customer Name", r"\bcustomer\s*(?:name)?\s*[:#-]?\s*([A-Z][A-Z0-9 &.,'-]{1,60})"),
    ("Invoice Date", r"\binvoice\s*date\s*[:#-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})\b"),
    ("Order Number", r"\border\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{2,})\b"),
    ("Item Number", r"\bitem\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{1,})\b"),
    ("Quantity", r"\b(?:qty|quantity)\s*[:#-]?\s*([0-9]+)\b"),
    ("Total Amount", r"\b(?:total amount|balance due|total)\s*[:#-]?\s*([$S]?\s*[0-9][0-9,]*(?:\.[0-9]{2})?)\b"),
    ("Shipping Address", r"\bship\s*to\s*[:#-]?\s*(.{5,120}?)(?=\s+(?:bill to|invoice|order|tracking|po|purchase|description|qty|total|$))"),
    ("Billing Address", r"\bbill\s*to\s*[:#-]?\s*(.{5,120}?)(?=\s+(?:ship to|invoice|order|tracking|po|purchase|description|qty|total|$))"),
]

FIELD_NAMES = [field_name for field_name, pattern in FIELDS]

PO_NUMBER_PATTERNS = [
    r"\bcustomer\s+p\s*\.?\s*[o0]\s*\.?\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{1,})\b",
    r"\bpurchase\s+order\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{1,})\b",
    r"\bp\s*\.?\s*[o0]\s*\.?\s*(?:number|no\.?|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{1,})\b",
    r"\bp\s*\.?\s*[o0]\s*\.?\s+([A-Z0-9][A-Z0-9-]{2,})\b",
    # Computerland packing slip requisition number — used as PO Number fallback
    r"\brequisition\s*(?:number|no\.?|#)?\s*[:#-]?\s*([0-9][A-Z0-9-]{3,})\b",
    # FedEx label "PC: 546010" — OCR misread of "PO#: 546010" at bottom of label
    r"\bPC\s*[:#-]\s*([0-9]{4,})\b",
    # FedEx label "REF:OP 67031" in the TO address — city order reference fallback
    r"\bREF\s*[:#-]?\s*OP\s+([0-9]{4,})\b",
]

INVOICE_NUMBER_PATTERNS = [
    r"\binvoice\s*(?:number|no\.?|#)\s*[:#-]?\s*(#?[A-Z0-9]*[0-9][A-Z0-9-]*)\b",
    r"\binvoice\b.{0,100}?\bnumber\s*[:#-]?\s*([0-9][A-Z0-9-]{2,})\b",
    r"\binvoice\s+[#:]?\s*([0-9][A-Z0-9-]{2,})\b",
    r"\binvoice\s+([A-Z]{2,}[A-Z0-9-]*[0-9][A-Z0-9-]*)\b",
    r"\binv\b\s*(?:number|no\.?|#)?\s*[:#-]?\s*(#?[A-Z0-9]*[0-9][A-Z0-9-]*)\b",
]

ORDER_NUMBER_PATTERNS = [
    r"\bsales\s+order\s*[:#-]?\s*(ORD-[A-Z0-9-]+)\b",
    r"\bsales\s+order\b.{0,120}?\b(ORD-[A-Z0-9-]+)\b",
    r"\bpacking\s+slip\b.{0,180}?\bsales\s+order\b.{0,160}?\b(ORD-[A-Z0-9-]+)\b",
    r"\border\s*(?:number|no\.?|#)?\s*[:#-]?\s*(ORD-[A-Z0-9-]+|[A-Z0-9][A-Z0-9-]{4,})\b",
]

TRACKING_NUMBER_PATTERNS = [
    # FedEx shipping label: "TRK#: 5263 3769 1880"
    # OCR often misreads "#" as "H" or drops it entirely — accept both.
    r"\bTRK\s*[#H]?\s*[:#.]?\s*(\d[\d ]{10,16}\d)\b",
    r"\b(?:fedex\s+)?tracking\s*(?:number|no\.?|#)?\s*[:#-]?\s*([0-9]{10,22}|[A-Z0-9][A-Z0-9-]{8,})\b",
    r"\bproof-of-delivery\s+for\s+tracking\s+number\s*[:#-]?\s*([0-9]{10,22}|[A-Z0-9][A-Z0-9-]{8,})\b",
]

def detect_vendor_from_text(text):
    """Return a vendor display name if all patterns for a known vendor are found in the OCR text."""
    for vendor_name, patterns in VENDOR_SIGNATURES:
        if all(re.search(p, text, re.IGNORECASE) for p in patterns):
            return vendor_name
    return None


PO_REJECT_PREFIXES = (
    "SALES",
    "SHIP",
    "TERMS",
    "DUE",
    "DATE",
    "DESCRIPTION",
    "QUANTITY",
    "UNIT",
    "LINE",
    "TOTAL",
    "PRICE",
    "BILL",
    "INVOICE",
)


def is_missing_value(value):
    return not value or str(value).strip().lower() == "needs manual review"


def is_debug_mode():
    return _DEBUG


def debug_print(message):
    if _DEBUG:
        print(message)


def configure_console_output():
    if _DEBUG:
        return

    logging.getLogger("easyocr").setLevel(logging.ERROR)
    logging.getLogger("excel_lookup").setLevel(logging.ERROR)
    logging.getLogger("openpyxl").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=UserWarning)


def load_learned_po_examples():
    if not os.path.exists(LEARNED_PO_PATTERNS_FILE):
        return []

    examples = []
    with open(LEARNED_PO_PATTERNS_FILE, "r", encoding="utf-8") as patterns_file:
        for line in patterns_file:
            try:
                example = json.loads(line)
            except json.JSONDecodeError:
                continue

            if example.get("po_number") and example.get("excel_order_number"):
                examples.append(example)

    return examples[-MAX_LEARNED_PO_EXAMPLES:]


def format_learned_po_examples(examples):
    lines = [f"- {lesson}" for lesson in AGENT_EXTRACTION_LESSONS]

    for example in examples:
        lines.append(
            "- Extracted PO Number "
            f"{example.get('po_number')} matched Excel Order Number "
            f"{example.get('excel_order_number')}."
        )

    return "\n".join(lines)


def read_csv_rows(csv_path):
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, "r", newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def ensure_corrections_file():
    if os.path.exists(CORRECTIONS_FILE):
        return

    with open(CORRECTIONS_FILE, "w", newline="", encoding="utf-8") as corrections_file:
        writer = csv.DictWriter(corrections_file, fieldnames=CORRECTION_FIELD_NAMES)
        writer.writeheader()


def load_correction_examples():
    examples = []
    for row in read_csv_rows(CORRECTIONS_FILE):
        corrected_lookup_value = str(row.get("Corrected Lookup Value") or "").strip()
        corrected_lookup_source = str(row.get("Corrected Lookup Value Source") or "").strip()

        if not corrected_lookup_value:
            continue

        examples.append(
            {
                "source_file": row.get("Source File") or "",
                "extracted_po_number": row.get("Extracted PO Number") or "",
                "extracted_invoice_number": row.get("Extracted Invoice Number") or "",
                "lookup_value": row.get("Lookup Value") or "",
                "lookup_value_source": row.get("Lookup Value Source") or "",
                "corrected_po_number": row.get("Corrected PO Number") or "",
                "corrected_invoice_number": row.get("Corrected Invoice Number") or "",
                "corrected_lookup_value": corrected_lookup_value,
                "corrected_lookup_source": corrected_lookup_source,
                "correction_notes": row.get("Correction Notes") or "",
            }
        )

    return examples[-MAX_CORRECTION_EXAMPLES:]


def format_correction_examples(examples):
    if not examples:
        return "- No human correction examples have been completed yet."

    lines = []
    for example in examples:
        source_file = example.get("source_file") or "unknown file"
        corrected_lookup_value = example.get("corrected_lookup_value")
        corrected_lookup_source = example.get("corrected_lookup_source") or "corrected source"
        notes = example.get("correction_notes") or "No notes."
        lines.append(
            "- Human correction for "
            f"{source_file}: use lookup value {corrected_lookup_value!r} "
            f"from {corrected_lookup_source!r}. Notes: {notes}"
        )

    return "\n".join(lines)


def load_correction_source_files():
    return {
        os.path.basename(row.get("Source File") or "")
        for row in read_csv_rows(CORRECTIONS_FILE)
    }


def append_correction_review_row(file_path, results, lookup_result, verification_result, existing_sources=None):
    ensure_corrections_file()

    source_file = os.path.basename(file_path)
    if existing_sources is None:
        existing_sources = load_correction_source_files()
    if source_file in existing_sources:
        return False

    row = {
        "Source File": source_file,
        "Extracted PO Number": results.get("PO Number") or "",
        "Extracted Invoice Number": results.get("Invoice Number") or "",
        "Lookup Value": results.get("Lookup Value") or "",
        "Lookup Value Source": results.get("Lookup Value Source") or "",
        "Excel Lookup Status": (lookup_result or {}).get("status") or "SKIPPED",
        "AI Verification Status": (verification_result or {}).get("verification_status") or "SKIPPED",
        "AI Verification Confidence": (verification_result or {}).get("confidence") or "",
        "Corrected PO Number": "",
        "Corrected Invoice Number": "",
        "Corrected Lookup Value": "",
        "Corrected Lookup Value Source": "",
        "Correction Notes": (verification_result or {}).get("correction_notes") or "",
    }

    with open(CORRECTIONS_FILE, "a", newline="", encoding="utf-8") as corrections_file:
        writer = csv.DictWriter(corrections_file, fieldnames=CORRECTION_FIELD_NAMES)
        writer.writerow(row)

    return True


def save_learned_po_example(file_path, results, lookup_result, existing_examples=None):
    if not lookup_result or lookup_result.get("status") != "MATCH_FOUND":
        return None

    po_number = results.get("PO Number")
    if is_missing_value(po_number):
        return None

    record = lookup_result.get("record") or {}
    excel_order_number = record.get("Order Number")
    if is_missing_value(excel_order_number):
        return None

    example = {
        "source_file": os.path.basename(file_path),
        "po_number": str(po_number).strip(),
        "excel_order_number": str(excel_order_number).strip(),
        "matched_row_number": lookup_result.get("matched_row_number"),
        "vendor": str(record.get("Vendor") or "").strip(),
        "item": str(record.get("Item") or "").strip(),
    }

    if existing_examples is None:
        existing_examples = load_learned_po_examples()

    for existing in existing_examples:
        if (
            existing.get("po_number") == example["po_number"]
            and existing.get("excel_order_number") == example["excel_order_number"]
        ):
            return example

    with open(LEARNED_PO_PATTERNS_FILE, "a", encoding="utf-8") as patterns_file:
        patterns_file.write(json.dumps(example, default=str) + "\n")

    return example


def get_option(option_name, default_value):
    # This lets us support simple options like:
    # python invoice_ocr.py invoice.pdf --model llama3.3
    if option_name not in sys.argv:
        return default_value

    option_index = sys.argv.index(option_name)
    value_index = option_index + 1

    if value_index >= len(sys.argv):
        return default_value

    return sys.argv[value_index]


def get_file_paths():
    # Find every command-line argument that is not an option.
    # Example:
    # python invoice_ocr.py invoice1.pdf invoice2.png --csv
    # The file paths are invoice1.pdf and invoice2.png.
    skip_next_value = False
    file_paths = []

    for value in sys.argv[1:]:
        if skip_next_value:
            skip_next_value = False
            continue

        if value == "--model":
            skip_next_value = True
            continue

        if not value.startswith("--"):
            file_paths.append(value)

    if file_paths:
        return file_paths

    user_input = input("Enter image or PDF path(s), separated by commas: ").strip()
    if "," in user_input:
        return [file_path.strip() for file_path in user_input.split(",") if file_path.strip()]

    return [user_input]


def create_ocr_reader():
    try:
        return easyocr.Reader(["en"], gpu=True)
    except Exception:
        return easyocr.Reader(["en"], gpu=False)


def read_image_text(file_path, reader):
    from PIL import ImageOps

    image = Image.open(file_path)
    image = ImageOps.exif_transpose(image)
    image_array = np.array(image.convert("RGB"))
    words = reader.readtext(image_array, detail=0, rotation_info=[90, 180, 270])
    return " ".join(words)


MIN_EMBEDDED_TEXT_LENGTH = 100


def read_pdf_text(file_path, reader):
    # PDF files must be converted into images before EasyOCR can read them.
    if fitz is None:
        print("PDF support requires PyMuPDF. Install it with: pip install pymupdf")
        sys.exit(1)

    document = fitz.open(file_path)
    all_text = []

    for page in document:
        embedded_text = page.get_text()
        stripped = embedded_text.strip()
        if stripped:
            all_text.append(embedded_text)
            if len(stripped) >= MIN_EMBEDDED_TEXT_LENGTH:
                # Page has sufficient embedded text — no need to run OCR on it.
                continue

        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        image_array = np.array(image.convert("RGB"))
        words = reader.readtext(image_array, detail=0)
        all_text.extend(words)

    return " ".join(all_text)


def clean_text(text):
    # Make the OCR text easier to search by removing extra spacing.
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_extracted_value(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def is_valid_po_candidate(value):
    normalized_value = normalize_extracted_value(value)
    if len(normalized_value) < 2:
        return False

    return not any(
        normalized_value.startswith(reject_prefix) for reject_prefix in PO_REJECT_PREFIXES
    )


def is_valid_invoice_candidate(value):
    return bool(re.search(r"[0-9]", str(value or "")))


def extract_po_number_from_text(text):
    for pattern in PO_NUMBER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .,:;")
            if is_valid_po_candidate(candidate):
                return candidate

    return "Needs Manual Review"


def extract_invoice_number_from_text(text):
    for pattern in INVOICE_NUMBER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .,:;")
            if is_valid_invoice_candidate(candidate):
                return candidate

    return "Needs Manual Review"


def extract_order_number_from_text(text):
    for pattern in ORDER_NUMBER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,:;")

    return "Needs Manual Review"


def extract_tracking_number_from_text(text):
    for pattern in TRACKING_NUMBER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip(" .,:;")
            # TRK# format produces space-separated digit groups — compact to one number
            compact = re.sub(r"\s+", "", value)
            if compact.isdigit():
                value = compact
            return value
    return "Needs Manual Review"


def add_lookup_candidate(candidates, value, source):
    if is_missing_value(value):
        return

    normalized_value = normalize_extracted_value(value)
    if not normalized_value:
        return

    for candidate in candidates:
        if normalize_extracted_value(candidate["value"]) == normalized_value:
            return

    candidates.append({"value": str(value).strip(), "source": source})


def get_lookup_candidates(results):
    candidates = []
    vendor = str(results.get("Vendor Name") or "").upper()
    sales_order_first = any(name in vendor for name in VENDOR_SALES_ORDER_PRIORITY)
    tracking_first = any(name in vendor for name in VENDOR_TRACKING_PRIORITY)

    if tracking_first:
        add_lookup_candidate(candidates, results.get("Tracking Number"), "FedEx Tracking Number")
        add_lookup_candidate(candidates, results.get("PO Number"), "PO Number")
        # Order Number is deliberately excluded for FedEx — it's the shipper's
        # internal reference (e.g., B&H Order#) and is not in the city's Excel.
    elif sales_order_first:
        add_lookup_candidate(candidates, results.get("Order Number"), "Sales Order")
        add_lookup_candidate(candidates, results.get("PO Number"), "PO Number")
    else:
        add_lookup_candidate(candidates, results.get("PO Number"), "PO Number")
        add_lookup_candidate(candidates, results.get("Order Number"), "Sales Order")

    add_lookup_candidate(candidates, results.get("Invoice Number"), "Invoice Number Fallback")
    return candidates


def set_lookup_value_from_candidates(results):
    candidates = get_lookup_candidates(results)
    results["Lookup Candidates"] = "; ".join(
        f"{candidate['source']}: {candidate['value']}" for candidate in candidates
    )

    if not candidates:
        results["Lookup Value"] = "Needs Manual Review"
        results["Lookup Value Source"] = "Needs Manual Review"
        return results

    results["Lookup Value"] = candidates[0]["value"]
    results["Lookup Value Source"] = candidates[0]["source"]
    return results


def validate_po_number(results, text):
    text = clean_text(text)
    text_po_number = extract_po_number_from_text(text)
    ai_po_number = results.get("PO Number")
    text_order_number = extract_order_number_from_text(text)
    text_tracking_number = extract_tracking_number_from_text(text)
    text_invoice_number = extract_invoice_number_from_text(text)

    if not is_missing_value(text_po_number):
        if normalize_extracted_value(ai_po_number) != normalize_extracted_value(text_po_number):
            debug_print(
                f"Corrected PO Number from AI value {ai_po_number!r} to OCR label value {text_po_number!r}."
            )
        results["PO Number"] = text_po_number
    else:
        if not is_missing_value(ai_po_number):
            debug_print(
                f"PO Number {ai_po_number!r} was not found next to a PO label, so it needs review."
            )
        results["PO Number"] = "Needs Manual Review"

    if not is_missing_value(text_order_number):
        results["Order Number"] = text_order_number

    if not is_missing_value(text_tracking_number):
        results["Tracking Number"] = text_tracking_number

    if not is_missing_value(text_invoice_number):
        results["Invoice Number"] = text_invoice_number

    # Always run signature detection — it overrides whatever the AI guessed because
    # the AI often reads the shipper name (e.g., "B&H Photo Video") instead of the
    # carrier/vendor we care about for lookup priority (e.g., "FedEx").
    detected_vendor = detect_vendor_from_text(text)
    if detected_vendor:
        results["Vendor Name"] = detected_vendor
        debug_print(f"Vendor detected from document signature: {detected_vendor}")
    elif not is_missing_value(text_tracking_number) and re.search(r"\bTRK\b", text, re.IGNORECASE):
        # TRK# is a FedEx-specific label format — infer vendor even when the
        # stylized FedEx logo isn't readable by OCR.
        results["Vendor Name"] = "FedEx"
        debug_print("Vendor inferred as FedEx from TRK# label in text.")

    results = set_lookup_value_from_candidates(results)

    if results["Lookup Value Source"] == "Invoice Number Fallback":
        debug_print(
            f"No stronger lookup value found; using Invoice Number {results['Lookup Value']!r} for lookup."
        )

    return results


def find_value(text, pattern):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return "Needs Manual Review"
    return match.group(1).strip(" .,:;")


_DEDICATED_FIELDS = {"Invoice Number", "PO Number", "Order Number", "Tracking Number"}


def extract_fields_with_regex(text):
    text = clean_text(text)
    results = {}

    for field_name, pattern in FIELDS:
        if field_name in _DEDICATED_FIELDS:
            continue
        value = find_value(text, pattern)
        # OCR sometimes reads "$" as "S" in dollar amounts.
        if field_name == "Total Amount" and value not in ("Needs Manual Review",) and value.startswith("S"):
            value = "$" + value[1:]
        results[field_name] = value

    results["Invoice Number"] = extract_invoice_number_from_text(text)
    results["PO Number"] = extract_po_number_from_text(text)
    results["Order Number"] = extract_order_number_from_text(text)
    results["Tracking Number"] = extract_tracking_number_from_text(text)
    return results


def build_ollama_prompt(text, learned_po_examples=None, correction_examples=None):
    # This prompt asks the local AI model to return only JSON.
    # JSON is useful because Python can turn it into a dictionary.
    field_list = "\n".join(f"- {field_name}" for field_name in FIELD_NAMES)
    learned_examples_text = format_learned_po_examples(learned_po_examples or [])
    correction_examples_text = format_correction_examples(correction_examples or [])

    return f"""
You extract structured data from OCR text for invoices and shipping documents.

Return only valid JSON. Do not include explanations or markdown.

Use these exact JSON keys:
{{
  "Invoice Number": "",
  "PO Number": "",
  "Tracking Number": "",
  "Carrier Name": "",
  "Vendor Name": "",
  "Customer Name": "",
  "Invoice Date": "",
  "Order Number": "",
  "Item Number": "",
  "Quantity": "",
  "Total Amount": "",
  "Shipping Address": "",
  "Billing Address": ""
}}

Rules:
- If a value is missing or unclear, use "Needs Manual Review".
- Do not guess values that are not in the OCR text.
- Keep dates, money amounts, and tracking numbers exactly as written when possible.
- If both a PO Number and an Order Number appear, keep them in their separate JSON fields.
- Do not use item number, ticket/RITM/SCTASK number, date, quantity, or dollar amount as the PO Number.

PO Number rules:
- PO Number may be labeled: PO, P.O., PO#, P.O. #, P.O. NO., Purchase Order, Customer PO, Customer P.O., or Cust PO.
- City purchase order numbers are typically 5–6 digits (e.g., 546010, 67031) or hyphenated (e.g., 67030-11959).
- If the PO field header exists but the value below it is blank, do not fill it from nearby unrelated fields.
- If no PO label exists, leave PO Number as "Needs Manual Review".

Invoice Number rules:
- If a number appears directly under or immediately after the standalone word INVOICE, treat it as the Invoice Number.
- If INVOICE is a large heading and a nearby field says Number, that value is the Invoice Number.
- If no PO Number is found, the Invoice Number may be used as a fallback lookup value.

Tracking Number rules:
- FedEx shipping labels: tracking number follows "TRK#" in space-separated groups (e.g., "TRK#:5263 3769 1880"). Extract digits only, no spaces → "526337691880".
- OCR may read "TRK#" as "TRKH" or "TRK" — still extract the digit groups that follow.
- FedEx proof-of-delivery and invoices may also contain a tracking number — extract it into Tracking Number.
- UPS tracking numbers start with "1Z" (e.g., "1Z999AA10123456784").
- Do NOT use barcode digit strings (e.g., "9632 0019 6 000 000 0000 ...") as the tracking number unless they match TRK# or a known carrier format.

Order Number rules (Sales Order):
- Computerland packing slips: "Sales order" field contains an ORD-XXXXX-XXXXXX value (e.g., "ORD-16625-M7V2B1"). Extract into Order Number.
- OCR may confuse "2" with "Z" or "0" with "O" in these codes — read carefully.
- Delivery receipts and packing slips may use Sales order as the order lookup value.

Vendor-specific rules:

COMPUTERLAND PACKING SLIP:
- Order Number → "Sales order" value (e.g., "ORD-16625-M7V2B1")
- PO Number → "Requisition" number if present (e.g., "67030-4955")
- Vendor Name → "Computerland"

ISSQUARED PACKING LIST:
- PO Number → "Customer PO#" value (e.g., "67030-11959")
- Vendor Name → "ISSQUARED"

FEDEX SHIPPING LABEL:
- Tracking Number → digits after "TRK#" (e.g., "526337691880")
- PO Number → "PO#" value (e.g., "546010"). OCR may misread "PO#" as "PC" — if you see "PC: NNNNNN" at the bottom, treat it as PO Number.
- PO Number fallback → number after "REF:OP" in the TO address (e.g., "REF:OP 67031" → PO Number "67031")
- Order Number → leave as "Needs Manual Review". The "Order#" field on FedEx labels (e.g., "917994866") is the SHIPPER's internal reference, not a city order.
- Vendor Name → "FedEx" (not the receiving department like "Information Technology")

Vendor Name rules:
- Set Vendor Name to the company that issued or shipped the document (letterhead, FROM address, carrier name).
- For FedEx labels, Vendor Name is "FedEx" — not the TO: recipient department.
- For Computerland documents, Vendor Name is "Computerland".
- For ISSQUARED documents, Vendor Name is "ISSQUARED".

Successful PO extraction examples learned from previous workbook matches:
{learned_examples_text}

Human correction examples learned from corrections.csv:
{correction_examples_text}

Fields to extract:
{field_list}

OCR text:
{text}
""".strip()


def _call_ollama(request_body, timeout=120):
    request = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _call_ollama_with_retry(request_body, timeout=120):
    result = _call_ollama(request_body, timeout)
    if result is not None:
        return result
    time.sleep(OLLAMA_RETRY_DELAY)
    return _call_ollama(request_body, timeout)


def check_ollama_health(model_name=DEFAULT_MODEL):
    """Return (ok, message) — whether Ollama is running and the model is available."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))

        available = [m.get("name", "") for m in data.get("models", [])]
        if any(m == model_name or m.split(":")[0] == model_name.split(":")[0] for m in available):
            return True, f"Ollama is running. Model '{model_name}' is available."

        listed = ", ".join(available) if available else "none"
        return False, f"Model '{model_name}' is not pulled. Run: ollama pull {model_name}  (available: {listed})"

    except (urllib.error.URLError, TimeoutError):
        return False, "Ollama is not running. Start it with: ollama serve"
    except Exception as exc:
        return False, f"Ollama health check failed: {exc}"


def ask_ollama_for_fields(text, model_name, learned_po_examples=None, correction_examples=None):
    prompt = build_ollama_prompt(clean_text(text), learned_po_examples, correction_examples)
    request_body = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    ollama_result = _call_ollama_with_retry(request_body)
    if ollama_result is None:
        return None

    try:
        extracted = json.loads(ollama_result["response"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None

    results = {}
    for field_name in FIELD_NAMES:
        value = extracted.get(field_name) or "Needs Manual Review"
        results[field_name] = str(value).strip()

    return results


def build_verification_prompt(text, results, lookup_result, correction_examples=None):
    matched_record = lookup_result.get("record") if lookup_result else None
    learned_lessons_text = "\n".join(f"- {lesson}" for lesson in AGENT_EXTRACTION_LESSONS)
    correction_examples_text = format_correction_examples(correction_examples or [])

    return f"""
You are a careful invoice OCR verification agent.

Return only valid JSON. Do not include explanations or markdown.

Use these exact JSON keys:
{{
  "verification_status": "VERIFIED, NEEDS_MANUAL_REVIEW, or LIKELY_INCORRECT",
  "confidence": "High, Medium, or Low",
  "summary": "",
  "issues": [],
  "recommended_po_number": "",
  "correction_notes": ""
}}

Rules:
- Check whether the extracted PO Number is visibly supported by the OCR text.
- Prefer a PO/P.O./Purchase Order label for PO Number.
- If no PO label exists, Sales Order, FedEx Tracking Number, or Invoice Number can be separate lookup values.
- The PO Number should not come from an item number, date, quantity, amount, tracking number, ticket, RITM, or SCTASK.
- Review the Python Excel lookup result. Python already performed the workbook comparison; do not invent a match.
- If Python found a match, verify whether the matched row's Order Number reasonably matches the Lookup Value.
- If the extracted PO Number is missing but Lookup Value Source is Sales Order, FedEx Tracking Number, or Invoice Number Fallback, judge whether that fallback is visibly supported by the OCR text.
- If the Lookup Value is missing, unclear, or not supported by a PO label, sales-order label, tracking-number label, or invoice-number fallback, use NEEDS_MANUAL_REVIEW or LIKELY_INCORRECT.
- Keep recommended_po_number empty unless the OCR text clearly shows a better PO Number.
- Use correction_notes to explain what a human should correct when review is needed.

Learned extraction lessons:
{learned_lessons_text}

Human correction examples learned from corrections.csv:
{correction_examples_text}

Extracted fields:
{json.dumps(results, indent=2, default=str)}

Python Excel lookup result:
{json.dumps(lookup_result or {}, indent=2, default=str)}

Matched Excel row, if any:
{json.dumps(matched_record or {}, indent=2, default=str)}

OCR text:
{clean_text(text)}
""".strip()


def should_skip_verification(results, lookup_result, always_verify=False):
    """Return True when the match is unambiguous and the second LLM call adds no value."""
    if always_verify:
        return False
    return (
        (lookup_result or {}).get("status") == "MATCH_FOUND"
        and results.get("Lookup Value Source") == "PO Number"
    )


def get_pending_corrections():
    """Return corrections.csv rows that have not yet been corrected."""
    return [
        row for row in read_csv_rows(CORRECTIONS_FILE)
        if not str(row.get("Corrected Lookup Value") or "").strip()
    ]


def save_correction(source_file, corrected_po, corrected_invoice,
                    corrected_lookup_value, corrected_lookup_source, notes):
    """Write human corrections back into corrections.csv. Returns True if a row was found."""
    rows = read_csv_rows(CORRECTIONS_FILE)
    updated = False
    for row in rows:
        if os.path.basename(str(row.get("Source File") or "")) == source_file:
            row["Corrected PO Number"] = corrected_po
            row["Corrected Invoice Number"] = corrected_invoice
            row["Corrected Lookup Value"] = corrected_lookup_value
            row["Corrected Lookup Value Source"] = corrected_lookup_source
            row["Correction Notes"] = notes
            updated = True

    if not updated:
        return False

    with open(CORRECTIONS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CORRECTION_FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)

    return True


def auto_verification_result():
    """Pre-filled VERIFIED result used when verification is skipped."""
    return {
        "verification_status": "VERIFIED",
        "confidence": "High",
        "summary": "PO Number matched Excel directly. Verification skipped.",
        "issues": [],
        "recommended_po_number": "",
        "correction_notes": "",
    }


def ask_ollama_for_verification(text, results, lookup_result, model_name, correction_examples=None):
    prompt = build_verification_prompt(text, results, lookup_result, correction_examples)
    request_body = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    ollama_result = _call_ollama_with_retry(request_body)
    if ollama_result is None:
        return {
            "verification_status": "NEEDS_MANUAL_REVIEW",
            "confidence": "Low",
            "summary": "AI verification was not available.",
            "issues": ["Ollama verification request failed."],
            "recommended_po_number": "",
            "correction_notes": "Check the extracted values manually because AI verification was not available.",
        }

    try:
        verification = json.loads(ollama_result["response"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {
            "verification_status": "NEEDS_MANUAL_REVIEW",
            "confidence": "Low",
            "summary": "AI verification returned invalid JSON.",
            "issues": ["Could not parse AI verification response."],
            "recommended_po_number": "",
            "correction_notes": "Check the extracted values manually because AI verification returned invalid JSON.",
        }

    verification_status = str(
        verification.get("verification_status") or "NEEDS_MANUAL_REVIEW"
    ).strip().upper().replace(" ", "_")

    if verification_status not in {"VERIFIED", "NEEDS_MANUAL_REVIEW", "LIKELY_INCORRECT"}:
        verification_status = "NEEDS_MANUAL_REVIEW"

    return {
        "verification_status": verification_status,
        "confidence": str(verification.get("confidence") or "Low").strip(),
        "summary": str(verification.get("summary") or "").strip(),
        "issues": verification.get("issues") if isinstance(verification.get("issues"), list) else [],
        "recommended_po_number": str(verification.get("recommended_po_number") or "").strip(),
        "correction_notes": str(verification.get("correction_notes") or "").strip(),
    }


def extract_fields(text, model_name, learned_po_examples=None, correction_examples=None):
    # First choice: ask the local AI model to understand the OCR text.
    results = ask_ollama_for_fields(text, model_name, learned_po_examples, correction_examples)
    if results is not None:
        debug_print(f"Extracted fields with Ollama model: {model_name}")
        return validate_po_number(results, text)

    # Backup choice: use the simpler regex extraction if Ollama is not available.
    debug_print("Ollama was not available, so regex extraction was used instead.")
    return validate_po_number(extract_fields_with_regex(text), text)


def print_results(results):
    # Show the extracted values in the terminal.
    print("\nOCR Results:")
    for field_name, value in results.items():
        print(f"{field_name}: {value}")


def print_matched_excel_row(lookup_result):
    matched_row_number = lookup_result.get("matched_row_number")
    record = lookup_result.get("record") or {}

    print(f"Matched Excel Row Number: {matched_row_number}")
    print("Matched Excel Row:")
    for column_name, value in record.items():
        if value == "":
            continue
        print(f"{column_name}: {value}")


def print_excel_lookup_result(lookup_result):
    print("\nExcel Lookup:")

    if lookup_result["status"] == "MATCH_FOUND":
        if lookup_result.get("matched_by") == "Order Number":
            print("Lookup value FOUND in Purchase Orders.xlsx Current Year sheet.")
        else:
            print("Match found in Purchase Orders.xlsx Current Year sheet.")
        print_matched_excel_row(lookup_result)
    elif lookup_result["status"] == "NO_MATCH":
        print("Lookup value NOT FOUND in Purchase Orders.xlsx Current Year sheet.")
    else:
        print("Lookup value needs manual review.")

    print(f"Status: {lookup_result['status']}")
    print(f"Message: {lookup_result['message']}")


def get_record_value(record, column_name):
    for key, value in record.items():
        if str(key).strip().lower() == column_name.lower():
            return value

    return ""


def print_end_user_result(file_path, results, lookup_result):
    print(f"\nFile: {os.path.basename(file_path)}")

    if not lookup_result or lookup_result.get("status") != "MATCH_FOUND":
        print("Order Found: NO")
        return

    record = lookup_result.get("record") or {}
    print("Order Found: YES")
    print(f"Matched Row: {lookup_result.get('matched_row_number')}")
    print(f"Ticket: {get_record_value(record, 'Ticket')}")
    print(f"Dept: {get_record_value(record, 'Dept')}")
    print(f"Requester: {get_record_value(record, 'Requester')}")
    print(f"Qty: {get_record_value(record, 'Qty')}")
    print(f"Item: {get_record_value(record, 'Item')}")


def lookup_order_candidates(results):
    candidates = get_lookup_candidates(results)
    if not candidates:
        return lookup_po_number(po_number=None)

    final_lookup_result = None
    for candidate in candidates:
        debug_print(f"\nTrying Lookup Value: {candidate['value']}")
        debug_print(f"Lookup Value Source: {candidate['source']}")
        col_names = TRACKING_NUMBER_COLUMN_NAMES if candidate["source"] == "FedEx Tracking Number" else None
        lookup_result = lookup_po_number(po_number=candidate["value"], column_names=col_names)
        lookup_result["lookup_value"] = candidate["value"]
        lookup_result["lookup_value_source"] = candidate["source"]

        if lookup_result.get("status") == "MATCH_FOUND":
            results["Lookup Value"] = candidate["value"]
            results["Lookup Value Source"] = candidate["source"]
            return lookup_result

        if final_lookup_result is None:
            final_lookup_result = lookup_result

    first_candidate = candidates[0]
    results["Lookup Value"] = first_candidate["value"]
    results["Lookup Value Source"] = first_candidate["source"]
    return final_lookup_result


def print_ai_verification_result(verification_result):
    print("\nAI Verification:")
    print(f"Status: {verification_result['verification_status']}")
    print(f"Confidence: {verification_result['confidence']}")
    print(f"Summary: {verification_result['summary']}")

    issues = verification_result.get("issues") or []
    if issues:
        print("Issues:")
        for issue in issues:
            print(f"- {issue}")

    recommended_po_number = verification_result.get("recommended_po_number")
    if recommended_po_number:
        print(f"Recommended PO Number: {recommended_po_number}")

    correction_notes = verification_result.get("correction_notes")
    if correction_notes:
        print(f"Correction Notes: {correction_notes}")


def process_file(file_path, reader, model_name, learned_po_examples, correction_examples):
    # Stop early if the file path is wrong.
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return False

    # Choose the correct reading function based on the file type.
    debug_print(f"\nProcessing file: {file_path}")
    debug_print("Reading document...")
    if file_path.lower().endswith(".pdf"):
        text = read_pdf_text(file_path, reader)
    else:
        text = read_image_text(file_path, reader)

    # Extract and print the invoice fields.
    # The script tries Ollama first because AI handles different invoice layouts
    # better than regex. Regex is still kept as a backup.
    results = extract_fields(text, model_name, learned_po_examples, correction_examples)
    if is_debug_mode():
        print_results(results)

    # Always validate the extracted PO Number value against Purchase Orders.xlsx.
    # The AI model does not read Excel. Python performs this lookup securely.
    if "--no-excel" not in sys.argv:
        debug_print(f"\nLookup Candidates: {results.get('Lookup Candidates')}")
        lookup_result = lookup_order_candidates(results)
        if is_debug_mode():
            print_excel_lookup_result(lookup_result)
        if should_skip_verification(results, lookup_result, always_verify="--always-verify" in sys.argv):
            debug_print("Verification skipped: PO Number matched Excel directly.")
            verification_result = auto_verification_result()
        else:
            verification_result = ask_ollama_for_verification(
                text,
                results,
                lookup_result,
                model_name,
                correction_examples,
            )
        if is_debug_mode():
            print_ai_verification_result(verification_result)
        learned_example = None
        if verification_result.get("verification_status") == "VERIFIED":
            learned_example = save_learned_po_example(file_path, results, lookup_result)
        if learned_example:
            learned_po_examples.append(learned_example)
            del learned_po_examples[:-MAX_LEARNED_PO_EXAMPLES]
            debug_print("Learned PO pattern for future AI extraction.")
        needs_correction = (
            lookup_result.get("status") != "MATCH_FOUND"
            or verification_result.get("verification_status") != "VERIFIED"
        )
        if needs_correction and append_correction_review_row(
            file_path,
            results,
            lookup_result,
            verification_result,
        ):
            debug_print(f"Added review row to {CORRECTIONS_FILE}.")
        print_end_user_result(file_path, results, lookup_result)
    else:
        lookup_result = None
        verification_result = None
        debug_print("\nExcel Lookup:")
        debug_print("Skipped because --no-excel was used.")
        print(f"\nFile: {os.path.basename(file_path)}")
        print("Order Lookup: SKIPPED")

    return True


def main():
    # The user can give one or more file paths in the command:
    # python invoice_ocr.py invoice1.pdf invoice2.png
    #
    # If they do not, we ask them to type the path or paths.
    file_paths = get_file_paths()
    model_name = get_option("--model", DEFAULT_MODEL)
    configure_console_output()

    existing_file_paths = []
    for file_path in file_paths:
        if os.path.exists(file_path):
            existing_file_paths.append(file_path)
        else:
            print(f"File not found: {file_path}")

    if not existing_file_paths:
        sys.exit(1)

    debug_print("Loading OCR reader...")
    try:
        reader = easyocr.Reader(["en"], gpu=True)
    except Exception:
        reader = easyocr.Reader(["en"], gpu=False)
    ensure_corrections_file()
    learned_po_examples = load_learned_po_examples()
    correction_examples = load_correction_examples()
    if correction_examples:
        debug_print(f"Loaded {len(correction_examples)} correction example(s) for AI prompts.")

    processed_any_file = False
    for file_path in existing_file_paths:
        processed_any_file = (
            process_file(
                file_path,
                reader,
                model_name,
                learned_po_examples,
                correction_examples,
            )
            or processed_any_file
        )

    if not processed_any_file:
        sys.exit(1)


# Python starts here when you run this file directly.
if __name__ == "__main__":
    main()
