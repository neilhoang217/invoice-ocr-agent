import csv
import hashlib
import os
import platform
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT_FOLDER = Path(__file__).resolve().parent
LABEL_OUTPUT_FOLDER = ROOT_FOLDER / "generated_labels"
PRINT_LOG_FILE = ROOT_FOLDER / "print_jobs.csv"

PRINT_LOG_FIELDS = [
    "timestamp",
    "status",
    "duplicate_key",
    "source_file",
    "invoice_number",
    "po_number",
    "tracking_number",
    "lookup_value",
    "lookup_value_source",
    "matched_row_number",
    "printer_queue",
    "label_path",
    "message",
]

LABEL_GENERATED = "LABEL_GENERATED"
LABEL_SENT_TO_PRINTER = "LABEL_SENT_TO_PRINTER"
DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED"
PRINT_FAILED = "PRINT_FAILED"
LABEL_SKIPPED = "LABEL_SKIPPED"

# 54mm x 70mm at 300 dpi, landscape (70mm wide, 54mm tall)
LABEL_WIDTH_PX = 827
LABEL_HEIGHT_PX = 638
LABEL_DPI = 300


def clean_label_text(value, fallback=""):
    text = str(value if value is not None else fallback).strip()
    return " ".join(text.split())[:70]


def normalize_key_part(value):
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def get_record_value(record, column_name):
    for key, value in (record or {}).items():
        if str(key).strip().lower() == column_name.lower():
            return value
    return ""


def build_duplicate_key(source_file, fields, lookup_result):
    record = (lookup_result or {}).get("record") or {}
    parts = [
        normalize_key_part(fields.get("Invoice Number")),
        normalize_key_part(fields.get("PO Number")),
        normalize_key_part(fields.get("Tracking Number")),
        normalize_key_part(fields.get("Lookup Value")),
        normalize_key_part((lookup_result or {}).get("matched_row_number")),
        normalize_key_part(get_record_value(record, "Order Number")),
    ]
    raw_key = "|".join(part for part in parts if part)
    if not raw_key:
        raw_key = normalize_key_part(source_file) or str(time.time())
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]


def ensure_print_log():
    if PRINT_LOG_FILE.exists():
        return
    with open(PRINT_LOG_FILE, "w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=PRINT_LOG_FIELDS)
        writer.writeheader()


def read_print_jobs():
    if not PRINT_LOG_FILE.exists():
        return []
    with open(PRINT_LOG_FILE, "r", newline="", encoding="utf-8") as log_file:
        return list(csv.DictReader(log_file))


def extract_duplicate_key_from_path(label_path):
    import re
    m = re.search(r'_([0-9a-f]{24})$', Path(label_path).stem)
    return m.group(1) if m else None


def was_already_printed(duplicate_key):
    for row in read_print_jobs():
        if (
            row.get("duplicate_key") == duplicate_key
            and row.get("status") == LABEL_SENT_TO_PRINTER
        ):
            return True
    return False


def append_print_log(row):
    ensure_print_log()
    with open(PRINT_LOG_FILE, "a", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=PRINT_LOG_FIELDS)
        writer.writerow({field: row.get(field, "") for field in PRINT_LOG_FIELDS})


def _load_font(size):
    candidates = [
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def build_label_image(fields, lookup_result):
    record = (lookup_result or {}).get("record") or {}
    order_number = get_record_value(record, "Order Number") or fields.get("Lookup Value")
    ticket = get_record_value(record, "Ticket")
    dept = get_record_value(record, "Dept")
    requester = get_record_value(record, "Requester")
    quantity = get_record_value(record, "Qty")
    item = get_record_value(record, "Item")

    image = Image.new("RGB", (LABEL_WIDTH_PX, LABEL_HEIGHT_PX), color="white")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(52)
    body_font = _load_font(42)

    draw.text((0, 20), "ITD Pickup Item", fill="black", font=title_font)
    draw.line([(0, 95), (200, 95)], fill="black", width=3)

    max_x = LABEL_WIDTH_PX

    def draw_row(text, y):
        if body_font.getbbox(text)[2] <= max_x:
            draw.text((0, y), text, fill="black", font=body_font)
            return
        # Find where the value starts (after the prefix up to first non-space after colon)
        colon_pos = text.find(":") + 1
        prefix = text[:colon_pos]
        while colon_pos < len(text) and text[colon_pos] == " ":
            prefix += " "
            colon_pos += 1
        prefix_w = body_font.getbbox(prefix)[2]
        value_text = text[colon_pos:]
        value_x = prefix_w
        available_w = max_x - value_x
        # Word-wrap the value
        words = value_text.split()
        lines, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            if body_font.getbbox(candidate)[2] <= available_w:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        draw.text((0, y), prefix + lines[0], fill="black", font=body_font)
        for extra in lines[1:]:
            y += 46
            draw.text((value_x, y), extra, fill="black", font=body_font)

    y = 110
    if order_number:
        draw_row(f"Order: {clean_label_text(order_number)}", y)
        y += 46
    if ticket:
        draw_row(f"Ticket: {clean_label_text(ticket)}", y)
        y += 46
    if dept:
        draw_row(f"Dept: {clean_label_text(dept)}", y)
        y += 46
    if requester:
        draw_row(f"Requester: {clean_label_text(requester)}", y)
        y += 46
    if quantity:
        draw_row(f"Qty: {clean_label_text(quantity)}", y)
        y += 46

    # Item: label on its own line, value wraps below with full width
    item_y = max(y + 20, 340)
    draw.text((0, item_y), "Item:", fill="black", font=body_font)
    item_value = clean_label_text(item, "N/A")
    words = item_value.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if body_font.getbbox(candidate)[2] <= max_x:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    for i, line in enumerate(lines):
        draw.text((0, item_y + 46 + i * 46), line, fill="black", font=body_font)

    return image


def get_label_path(source_file, duplicate_key):
    LABEL_OUTPUT_FOLDER.mkdir(exist_ok=True)
    base_name = Path(source_file).stem or "invoice"
    safe_base_name = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in base_name
    )[:60]
    return LABEL_OUTPUT_FOLDER / f"{safe_base_name}_{duplicate_key}.png"


def write_label_file(source_file, duplicate_key, image):
    label_path = get_label_path(source_file, duplicate_key)
    image.save(str(label_path), "PNG", dpi=(LABEL_DPI, LABEL_DPI))
    return label_path


def validate_queue_name(printer_queue):
    printer_queue = str(printer_queue or "").strip()
    if not printer_queue:
        raise ValueError("DYMO printer queue name is required.")
    if platform.system() == "Windows":
        forbidden = set('<>"|&;')
        if any(c in forbidden for c in printer_queue):
            raise ValueError("Printer name contains invalid characters.")
    else:
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        if any(character not in allowed for character in printer_queue):
            raise ValueError(
                "DYMO printer queue name may only contain letters, numbers, dots, dashes, and underscores."
            )
    return printer_queue


def print_existing_label(label_path, printer_queue):
    _send_label_to_printer(label_path, printer_queue)


def _send_label_to_printer_windows(label_path, printer_queue):
    try:
        import win32print
        import win32ui
        from PIL import ImageWin
    except ImportError:
        raise OSError(
            "pywin32 is required for printing on Windows. Run: pip install pywin32"
        )

    img = Image.open(str(label_path))
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_queue)

    printable_w = hdc.GetDeviceCaps(8)
    printable_h = hdc.GetDeviceCaps(10)
    img_w, img_h = img.size
    scale = min(printable_w / img_w, printable_h / img_h)
    print_w = int(img_w * scale)
    print_h = int(img_h * scale)

    hdc.StartDoc(Path(label_path).name)
    hdc.StartPage()
    dib = ImageWin.Dib(img)
    dib.draw(hdc.GetHandleOutput(), (0, 0, print_w, print_h))
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


def _send_label_to_printer(label_path, printer_queue):
    printer_queue = validate_queue_name(printer_queue)
    if platform.system() == "Windows":
        _send_label_to_printer_windows(label_path, printer_queue)
    else:
        subprocess.run(
            ["lp", "-d", printer_queue, "-o", "PageSize=w154h198", str(label_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )


def base_log_row(source_file, fields, lookup_result, duplicate_key, printer_queue):
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duplicate_key": duplicate_key,
        "source_file": source_file,
        "invoice_number": fields.get("Invoice Number") or "",
        "po_number": fields.get("PO Number") or "",
        "tracking_number": fields.get("Tracking Number") or "",
        "lookup_value": fields.get("Lookup Value") or "",
        "lookup_value_source": fields.get("Lookup Value Source") or "",
        "matched_row_number": (lookup_result or {}).get("matched_row_number") or "",
        "printer_queue": printer_queue or "",
    }


def _create_single_label(source_file, fields, lookup_result, send_to_printer, printer_queue):
    duplicate_key = build_duplicate_key(source_file, fields, lookup_result)
    log_row = base_log_row(source_file, fields, lookup_result, duplicate_key, printer_queue)
    label_path = get_label_path(source_file, duplicate_key)

    if was_already_printed(duplicate_key):
        message = "Duplicate print blocked because this document/match was already sent to a printer."
        append_print_log({**log_row, "status": DUPLICATE_BLOCKED, "label_path": str(label_path), "message": message})
        return {
            "status": DUPLICATE_BLOCKED,
            "message": message,
            "label_path": str(label_path) if label_path.exists() else None,
            "duplicate_key": duplicate_key,
        }

    # Skip regeneration if the label file already exists (e.g. double-click).
    if not label_path.exists():
        image = build_label_image(fields, lookup_result)
        write_label_file(source_file, duplicate_key, image)

    if not send_to_printer:
        message = f"Label generated and saved to {label_path.name}."
        append_print_log({**log_row, "status": LABEL_GENERATED, "label_path": str(label_path), "message": message})
        return {
            "status": LABEL_GENERATED,
            "message": message,
            "label_path": str(label_path),
            "duplicate_key": duplicate_key,
        }

    if not printer_queue:
        message = "Label generated, but DYMO printer queue was missing so it was not sent."
        append_print_log({**log_row, "status": PRINT_FAILED, "label_path": str(label_path), "message": message})
        return {
            "status": PRINT_FAILED,
            "message": message,
            "label_path": str(label_path),
            "duplicate_key": duplicate_key,
        }

    try:
        _send_label_to_printer(label_path, printer_queue)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        message = f"Label generated, but printer send failed: {error}"
        append_print_log({**log_row, "status": PRINT_FAILED, "label_path": str(label_path), "message": message})
        return {
            "status": PRINT_FAILED,
            "message": message,
            "label_path": str(label_path),
            "duplicate_key": duplicate_key,
        }

    label_path.unlink(missing_ok=True)
    message = f"Label sent to DYMO printer queue {printer_queue} and file cleaned up."
    append_print_log({
        **log_row,
        "status": LABEL_SENT_TO_PRINTER,
        "label_path": str(label_path),
        "message": message,
    })
    return {
        "status": LABEL_SENT_TO_PRINTER,
        "message": message,
        "label_path": None,
        "duplicate_key": duplicate_key,
    }


def create_and_maybe_print_label(
    source_file,
    fields,
    lookup_result,
    send_to_printer=False,
    printer_queue="",
):
    if not lookup_result or lookup_result.get("status") != "MATCH_FOUND":
        return [{
            "status": LABEL_SKIPPED,
            "message": "Label skipped because no Excel match was found.",
            "label_path": None,
            "duplicate_key": None,
        }]

    all_records = lookup_result.get("all_records") or [{
        "matched_row_number": lookup_result.get("matched_row_number"),
        "record": lookup_result.get("record"),
    }]

    results = []
    for match in all_records:
        single_lookup = {**lookup_result, **match}
        results.append(_create_single_label(source_file, fields, single_lookup, send_to_printer, printer_queue))
    return results
