import logging
import threading
from pathlib import Path

import pandas as pd


# Excel lookup results. Using constants helps avoid spelling mistakes.
MATCH_FOUND = "MATCH_FOUND"
NO_MATCH = "NO_MATCH"
NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"

# This project only allows Excel access from this one folder.
# Put Purchase Orders.xlsx here:
# /Users/neil.hoang/invoice-ocr-agent/approved_excel_files/Purchase Orders.xlsx
APPROVED_EXCEL_FOLDER = Path(__file__).resolve().parent / "approved_excel_files"
PURCHASE_ORDER_EXCEL_FILE = "Purchase Orders.xlsx"
CURRENT_YEAR_SHEET_NAME = "Current Year"
MIN_PARTIAL_MATCH_LENGTH = 5

# These are the Excel columns this module knows how to search.
ORDER_NUMBER_COLUMN_NAMES = ["Order Number", "Order No", "Order #", "Order number"]
TRACKING_NUMBER_COLUMN_NAMES = ["Tracking #", "Tracking Number", "Tracking No.", "Track #", "TRK#"]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_excel_cache: dict = {"df": None, "mtime": None}
_excel_lock = threading.Lock()


def validate_excel_path(file_name=PURCHASE_ORDER_EXCEL_FILE):
    # Never accept a full path from the AI model or from extracted invoice data.
    # The caller may only choose a file name, and this function forces that file
    # to live inside APPROVED_EXCEL_FOLDER.
    approved_folder = APPROVED_EXCEL_FOLDER.resolve()
    requested_path = (approved_folder / file_name).resolve()

    try:
        requested_path.relative_to(approved_folder)
    except ValueError:
        raise PermissionError(f"Blocked Excel access outside approved folder: {requested_path}")

    if requested_path.name != PURCHASE_ORDER_EXCEL_FILE:
        raise PermissionError(f"Only {PURCHASE_ORDER_EXCEL_FILE} is approved for lookup.")

    if not requested_path.exists():
        raise FileNotFoundError(f"Excel file not found: {requested_path}")

    if not requested_path.is_file():
        raise FileNotFoundError(f"Excel path is not a file: {requested_path}")

    return requested_path


def normalize_value(value):
    # Make matching easier by ignoring spaces, dashes, and punctuation.
    # Example: "PO-123" and "PO 123" both become "PO123".
    if pd.isna(value):
        return ""

    if str(value).strip().lower() == "needs manual review":
        return ""

    value = str(value).upper()
    return "".join(character for character in value if character.isalnum())


# Maps visually ambiguous OCR characters to a canonical form so that, e.g.,
# "ORD-16625-M7VZB1" (OCR misread Z for 2) matches "ORD-16625-M7V2B1".
_OCR_CHAR_TABLE = str.maketrans("ZO", "20")


def ocr_normalize(normalized_value):
    """Apply OCR character confusion folding on top of normalize_value output."""
    return normalized_value.translate(_OCR_CHAR_TABLE)


def normalize_header(value):
    if pd.isna(value):
        return ""

    value = str(value).lower()
    return "".join(character for character in value if character.isalnum())


def values_match(document_value, excel_value):
    if not document_value or not excel_value:
        return False

    if document_value == excel_value:
        return True

    shorter_length = min(len(document_value), len(excel_value))
    if shorter_length < MIN_PARTIAL_MATCH_LENGTH:
        return False

    if document_value in excel_value or excel_value in document_value:
        return True

    # OCR commonly confuses visually similar characters (e.g., Z↔2, O↔0).
    # Fold both sides and try again so "M7VZB1" matches "M7V2B1".
    doc_ocr = ocr_normalize(document_value)
    exc_ocr = ocr_normalize(excel_value)
    if doc_ocr == exc_ocr:
        return True
    return doc_ocr in exc_ocr or exc_ocr in doc_ocr


def find_column(dataframe, possible_names):
    # Excel files often use slightly different column names.
    # This lets us support names like "Order Number" and "Order #".
    normalized_columns = {
        normalize_header(column_name): column_name for column_name in dataframe.columns
    }

    for possible_name in possible_names:
        column_name = normalized_columns.get(normalize_header(possible_name))
        if column_name is not None:
            return column_name

    return None


def load_purchase_order_excel():
    excel_path = validate_excel_path()
    mtime = excel_path.stat().st_mtime

    with _excel_lock:
        if _excel_cache["df"] is not None and _excel_cache["mtime"] == mtime:
            return _excel_cache["df"]

        try:
            logger.info("Loading approved Excel file: %s", excel_path)
            with pd.ExcelFile(excel_path, engine="openpyxl") as xl:
                if CURRENT_YEAR_SHEET_NAME not in xl.sheet_names:
                    raise ValueError(f"Excel file is missing sheet: {CURRENT_YEAR_SHEET_NAME}")
                dataframe = xl.parse(CURRENT_YEAR_SHEET_NAME)

            logger.info("Loaded %s rows from %s sheet.", len(dataframe), CURRENT_YEAR_SHEET_NAME)
            _excel_cache["df"] = dataframe
            _excel_cache["mtime"] = mtime
            return dataframe
        except Exception:
            logger.exception("Failed to load purchase order Excel file.")
            raise


def lookup_po_number(po_number=None, column_names=None):
    # The AI model should only give us extracted text values.
    # Python owns this lookup step and decides whether the Excel file has a match.
    document_po = normalize_value(po_number)

    if not document_po:
        logger.warning("Lookup needs manual review because the lookup value is missing.")
        return {
            "status": NEEDS_MANUAL_REVIEW,
            "message": "Missing lookup value.",
            "matched_by": None,
            "matched_row_number": None,
            "record": None,
        }

    if column_names is None:
        column_names = ORDER_NUMBER_COLUMN_NAMES

    try:
        dataframe = load_purchase_order_excel()

        order_number_column = find_column(dataframe, column_names)
        column_label = column_names[0] if column_names else "Order Number"

        if order_number_column is None:
            logger.warning("Purchase order Excel is missing the %s column.", column_label)
            return {
                "status": NEEDS_MANUAL_REVIEW,
                "message": f"Purchase Orders.xlsx Current Year sheet is missing a {column_label} column.",
                "matched_by": None,
                "matched_row_number": None,
                "record": None,
            }

        normalized_col = dataframe[order_number_column].apply(normalize_value)
        match_mask = normalized_col.apply(lambda v: values_match(document_po, v))
        matched_indices = dataframe.index[match_mask]

        if len(matched_indices) > 0:
            all_records = [
                {
                    "matched_row_number": int(idx) + 2,
                    "record": dataframe.loc[idx].fillna("").to_dict(),
                }
                for idx in matched_indices
            ]
            first = all_records[0]
            logger.info("Excel match found by %s. %d row(s).", column_label, len(all_records))
            return {
                "status": MATCH_FOUND,
                "message": f"Matched extracted lookup value against Excel {column_label}. {len(all_records)} row(s) found.",
                "matched_by": column_label,
                "matched_row_number": first["matched_row_number"],
                "record": first["record"],
                "all_records": all_records,
            }

        logger.info("No Excel match found.")
        return {
            "status": NO_MATCH,
            "message": "No Current Year order number matched the extracted lookup value.",
            "matched_by": None,
            "matched_row_number": None,
            "record": None,
        }
    except Exception as error:
        logger.exception("Excel lookup failed.")
        return {
            "status": NEEDS_MANUAL_REVIEW,
            "message": f"Excel lookup failed: {error}",
            "matched_by": None,
            "matched_row_number": None,
            "record": None,
        }
