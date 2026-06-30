import cgi
import concurrent.futures
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import threading
import traceback
import tempfile
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import invoice_ocr
import dymo_printing


ROOT_FOLDER = Path(__file__).resolve().parent
STATIC_FOLDER = ROOT_FOLDER / "web_static"
UPLOAD_FOLDER = ROOT_FOLDER / "uploads"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7860
MAX_UPLOAD_SIZE = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

OCR_READER = None
OCR_LOCK = threading.Lock()
WRITE_LOCK = threading.Lock()

_examples_cache: dict = {
    "learned": [], "learned_mtime": None,
    "corrections": [], "corrections_mtime": None,
}
_examples_lock = threading.Lock()


def setup_file_logging():
    log_path = ROOT_FOLDER / "invoice_ocr.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if not root.level or root.level >= logging.WARNING:
        root.setLevel(logging.INFO)


def find_duplicate_source(original_name):
    """Return 'corrections' if this filename was previously seen there, or None."""
    if original_name in invoice_ocr.load_correction_source_files():
        return "corrections"
    return None


def get_ocr_reader():
    global OCR_READER
    if OCR_READER is None:
        with OCR_LOCK:
            if OCR_READER is None:
                OCR_READER = invoice_ocr.create_ocr_reader()
    return OCR_READER


def _file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def get_cached_examples():
    learned_mtime = _file_mtime(invoice_ocr.LEARNED_PO_PATTERNS_FILE)
    corrections_mtime = _file_mtime(invoice_ocr.CORRECTIONS_FILE)

    with _examples_lock:
        if _examples_cache["learned_mtime"] != learned_mtime:
            _examples_cache["learned"] = invoice_ocr.load_learned_po_examples()
            _examples_cache["learned_mtime"] = learned_mtime
        if _examples_cache["corrections_mtime"] != corrections_mtime:
            _examples_cache["corrections"] = invoice_ocr.load_correction_examples()
            _examples_cache["corrections_mtime"] = corrections_mtime
        return list(_examples_cache["learned"]), list(_examples_cache["corrections"])


def safe_file_name(file_name):
    file_name = os.path.basename(file_name or "uploaded-file")
    file_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._")
    return file_name or "uploaded-file"


def json_response(handler, status_code, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_result(file_name, message):
    return {
        "file_name": file_name,
        "ok": False,
        "error": message,
        "fields": {},
        "lookup_result": None,
        "verification_result": None,
        "label_results": [],
        "final_status": "Needs Manual Review",
        "processed_at": time.strftime("%I:%M:%S %p"),
    }


def format_error(error):
    message = str(error).strip()
    if message:
        return message
    return error.__class__.__name__


def summarize_matched_records(lookup_result):
    wanted_columns = ["Ticket", "Dept", "Requester", "Qty", "Item"]
    all_records = (lookup_result or {}).get("all_records") or []
    if not all_records:
        record = (lookup_result or {}).get("record") or {}
        if record:
            all_records = [{"record": record}]
    return [
        {col: invoice_ocr.get_record_value(match["record"], col) for col in wanted_columns}
        for match in all_records
    ]


def get_final_status(result):
    if not result.get("ok"):
        return "Needs Manual Review"

    statuses = [r.get("status") for r in (result.get("label_results") or [])]
    lookup_status = (result.get("lookup_result") or {}).get("status")

    if dymo_printing.LABEL_SENT_TO_PRINTER in statuses:
        return "Label Sent to Printer"
    if dymo_printing.LABEL_GENERATED in statuses:
        return "Label Generated"
    if dymo_printing.DUPLICATE_BLOCKED in statuses:
        return "Duplicate Print Blocked"
    if dymo_printing.PRINT_FAILED in statuses:
        return "Print Failed"
    if lookup_status == "MATCH_FOUND":
        return "Order Found"
    return "Needs Manual Review"


def process_uploaded_file(
    upload_path,
    original_name,
    model_name,
    use_excel,
    send_to_printer,
    printer_queue,
):
    duplicate_source = find_duplicate_source(original_name)
    reader = get_ocr_reader()
    suffix = upload_path.suffix.lower()

    with OCR_LOCK:
        if suffix == ".pdf":
            ocr_text = invoice_ocr.read_pdf_text(str(upload_path), reader)
        else:
            ocr_text = invoice_ocr.read_image_text(str(upload_path), reader)

    print("\n===== RAW OCR TEXT =====")
    print(ocr_text)
    print("========================\n")

    learned_po_examples, correction_examples = get_cached_examples()

    fields = invoice_ocr.extract_fields(
        ocr_text,
        model_name,
        learned_po_examples,
        correction_examples,
    )

    lookup_result = None
    verification_result = None
    label_results = []

    if use_excel:
        lookup_result = invoice_ocr.lookup_order_candidates(fields)
        if invoice_ocr.should_skip_verification(fields, lookup_result, always_verify=False):
            verification_result = invoice_ocr.auto_verification_result()
        else:
            verification_result = invoice_ocr.ask_ollama_for_verification(
                ocr_text,
                fields,
                lookup_result,
                model_name,
                correction_examples,
            )

        if verification_result.get("verification_status") == "VERIFIED":
            with WRITE_LOCK:
                learned_example = invoice_ocr.save_learned_po_example(
                    str(upload_path),
                    fields,
                    lookup_result,
                    existing_examples=learned_po_examples,
                )
            if learned_example:
                learned_po_examples.append(learned_example)

        needs_correction = (
            lookup_result.get("status") != "MATCH_FOUND"
            or verification_result.get("verification_status") != "VERIFIED"
        )
        if needs_correction:
            with WRITE_LOCK:
                existing_sources = invoice_ocr.load_correction_source_files()
                invoice_ocr.append_correction_review_row(
                    str(upload_path),
                    fields,
                    lookup_result,
                    verification_result,
                    existing_sources=existing_sources,
                )

        with WRITE_LOCK:
            label_results = dymo_printing.create_and_maybe_print_label(
                source_file=original_name,
                fields=fields,
                lookup_result=lookup_result,
                send_to_printer=send_to_printer,
                printer_queue=printer_queue,
            )

    result = {
        "file_name": original_name,
        "ok": True,
        "error": None,
        "fields": fields,
        "lookup_result": lookup_result,
        "verification_result": verification_result,
        "label_results": label_results,
        "matched_record_summaries": summarize_matched_records(lookup_result),
        "processed_at": time.strftime("%I:%M:%S %p"),
        "duplicate_warning": f"Previously seen in {duplicate_source}" if duplicate_source else None,
    }
    result["final_status"] = get_final_status(result)
    return result


class InvoiceOCRHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Without this, browsers may serve stale HTML/CSS/JS from heuristic
        # cache after a file changes on disk, since SimpleHTTPRequestHandler
        # doesn't send a Cache-Control header by default.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            return str(STATIC_FOLDER / "index.html")
        return str(STATIC_FOLDER / path.lstrip("/"))

    def do_GET(self):
        if self.path == "/api/health":
            ollama_ok, ollama_message = invoice_ocr.check_ollama_health()
            json_response(self, 200, {
                "ok": True,
                "status": "local",
                "ollama": {"ok": ollama_ok, "message": ollama_message},
            })
            return
        if self.path == "/api/corrections":
            self.handle_corrections_get()
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self):
        try:
            if self.path == "/api/process":
                self.handle_process_request()
            elif self.path == "/api/process-stream":
                self.handle_process_stream_request()
            elif self.path == "/api/print":
                self.handle_print_request()
            elif self.path == "/api/corrections":
                self.handle_corrections_post()
            elif self.path == "/api/lookup":
                self.handle_lookup_request()
            else:
                json_response(self, 404, {"ok": False, "error": "Unknown endpoint."})
        except Exception as error:
            traceback.print_exc()
            json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": f"Server error: {format_error(error)}",
                },
            )

    def handle_print_request(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            json_response(self, 400, {"ok": False, "error": "Empty request body."})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            json_response(self, 400, {"ok": False, "error": "Invalid JSON."})
            return

        label_path_str = str(body.get("label_path") or "").strip()
        printer_queue = str(body.get("printer_queue") or "").strip()
        override_duplicate = bool(body.get("override_duplicate", False))

        if not label_path_str:
            json_response(self, 400, {"ok": False, "error": "label_path is required."})
            return

        if not printer_queue:
            json_response(self, 400, {"ok": False, "error": "Enter a DYMO printer queue name first."})
            return

        label_path = Path(label_path_str)
        try:
            label_path.resolve().relative_to(dymo_printing.LABEL_OUTPUT_FOLDER.resolve())
        except ValueError:
            json_response(self, 400, {"ok": False, "error": "Invalid label path."})
            return

        if not label_path.exists():
            json_response(self, 404, {"ok": False, "error": "Label file not found."})
            return

        duplicate_key = dymo_printing.extract_duplicate_key_from_path(label_path)
        if duplicate_key and not override_duplicate and dymo_printing.was_already_printed(duplicate_key):
            json_response(self, 409, {"ok": False, "duplicate_blocked": True, "error": "This label was already printed."})
            return

        try:
            dymo_printing.print_existing_label(label_path, printer_queue)
            label_path.unlink(missing_ok=True)
            if duplicate_key:
                with WRITE_LOCK:
                    dymo_printing.append_print_log({
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": dymo_printing.LABEL_SENT_TO_PRINTER,
                        "duplicate_key": duplicate_key,
                        "label_path": str(label_path),
                        "printer_queue": printer_queue,
                        "message": f"Label manually sent to printer queue {printer_queue} and file cleaned up.",
                    })
            json_response(self, 200, {"ok": True, "message": f"Label sent to {printer_queue}."})
        except Exception as error:
            json_response(self, 500, {"ok": False, "error": f"Print failed: {format_error(error)}"})

    def handle_lookup_request(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            json_response(self, 400, {"ok": False, "error": "Empty request body."})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            json_response(self, 400, {"ok": False, "error": "Invalid JSON."})
            return

        order_number = str(body.get("order_number") or "").strip()
        send_to_printer = bool(body.get("send_to_printer", False))
        printer_queue = str(body.get("printer_queue") or "").strip()

        if not order_number:
            json_response(self, 400, {"ok": False, "error": "order_number is required."})
            return

        from excel_lookup import lookup_po_number, TRACKING_NUMBER_COLUMN_NAMES
        lookup_result = lookup_po_number(order_number)
        if lookup_result.get("status") != "MATCH_FOUND":
            lookup_result = lookup_po_number(order_number, column_names=TRACKING_NUMBER_COLUMN_NAMES)

        if lookup_result.get("status") != "MATCH_FOUND":
            json_response(self, 200, {"ok": False, "error": f"No match found for '{order_number}'."})
            return

        with WRITE_LOCK:
            label_results = dymo_printing.create_and_maybe_print_label(
                source_file=f"manual-lookup-{order_number}",
                fields={"Lookup Value": order_number, "Lookup Value Source": "Manual"},
                lookup_result=lookup_result,
                send_to_printer=send_to_printer,
                printer_queue=printer_queue,
            )

        wanted = ["Ticket", "Dept", "Requester", "Qty", "Item"]
        records = [
            {col: invoice_ocr.get_record_value(m["record"], col) for col in wanted}
            for m in (lookup_result.get("all_records") or [{"record": lookup_result.get("record")}])
        ]

        json_response(self, 200, {
            "ok": True,
            "order_number": order_number,
            "records": records,
            "label_results": label_results,
        })

    def handle_process_request(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        content_type = self.headers.get("Content-Type") or ""
        if content_length <= 0 or "multipart/form-data" not in content_type:
            json_response(self, 400, {"ok": False, "error": "Upload one or more files."})
            return

        if content_length > MAX_UPLOAD_SIZE * 20:
            json_response(self, 413, {"ok": False, "error": "Upload request is too large."})
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
        )

        model_name = (form.getfirst("model") or invoice_ocr.DEFAULT_MODEL).strip()
        use_excel = form.getfirst("excel_validation", "true") == "true"
        send_to_printer = form.getfirst("send_to_printer", "false") == "true"
        printer_queue = (form.getfirst("printer_queue") or "").strip()


        file_fields = form["files"] if "files" in form else []
        if not isinstance(file_fields, list):
            file_fields = [file_fields]

        if not file_fields:
            json_response(self, 400, {"ok": False, "error": "No files were uploaded."})
            return

        UPLOAD_FOLDER.mkdir(exist_ok=True)

        pending = self._save_uploads(file_fields)

        def process_one(item):
            upload_path, original_name, immediate_error = item
            if immediate_error is not None:
                return immediate_error
            try:
                return process_uploaded_file(
                    upload_path, original_name, model_name,
                    use_excel, send_to_printer, printer_queue,
                )
            except (Exception, SystemExit) as error:
                traceback.print_exc()
                return error_result(original_name, format_error(error))
            finally:
                if upload_path is not None:
                    upload_path.unlink(missing_ok=True)

        valid_count = sum(1 for path, _, _ in pending if path is not None)
        if valid_count <= 1:
            results = [process_one(item) for item in pending]
        else:
            max_workers = min(valid_count, 4)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(process_one, pending))

        json_response(self, 200, {"ok": True, "results": results})

    def _save_uploads(self, file_fields):
        """Save multipart file fields to temp files. Returns list of (path, name, error_or_none)."""
        pending = []
        for file_field in file_fields:
            original_name = safe_file_name(file_field.filename)
            suffix = Path(original_name).suffix.lower()

            if suffix not in ALLOWED_EXTENSIONS:
                pending.append((None, original_name, error_result(original_name, f"Unsupported file type: {suffix}")))
                continue

            with tempfile.NamedTemporaryFile(
                delete=False, dir=UPLOAD_FOLDER, suffix=suffix, prefix="upload_",
            ) as temp_file:
                uploaded_bytes = file_field.file.read(MAX_UPLOAD_SIZE + 1)
                if len(uploaded_bytes) > MAX_UPLOAD_SIZE:
                    pending.append((None, original_name, error_result(original_name, "File is larger than 50 MB.")))
                    continue
                temp_file.write(uploaded_bytes)
                upload_path = Path(temp_file.name)

            pending.append((upload_path, original_name, None))
        return pending

    def handle_process_stream_request(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        content_type = self.headers.get("Content-Type") or ""
        if content_length <= 0 or "multipart/form-data" not in content_type:
            json_response(self, 400, {"ok": False, "error": "Upload one or more files."})
            return
        if content_length > MAX_UPLOAD_SIZE * 20:
            json_response(self, 413, {"ok": False, "error": "Upload request is too large."})
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
        )

        model_name = (form.getfirst("model") or invoice_ocr.DEFAULT_MODEL).strip()
        use_excel = form.getfirst("excel_validation", "true") == "true"
        send_to_printer = form.getfirst("send_to_printer", "false") == "true"
        printer_queue = (form.getfirst("printer_queue") or "").strip()


        file_fields = form["files"] if "files" in form else []
        if not isinstance(file_fields, list):
            file_fields = [file_fields]

        if not file_fields:
            json_response(self, 400, {"ok": False, "error": "No files were uploaded."})
            return

        UPLOAD_FOLDER.mkdir(exist_ok=True)
        pending = self._save_uploads(file_fields)

        def process_one(item):
            upload_path, original_name, immediate_error = item
            if immediate_error is not None:
                return immediate_error
            try:
                return process_uploaded_file(
                    upload_path, original_name, model_name,
                    use_excel, send_to_printer, printer_queue,
                )
            except (Exception, SystemExit) as error:
                traceback.print_exc()
                return error_result(original_name, format_error(error))
            finally:
                if upload_path is not None:
                    upload_path.unlink(missing_ok=True)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def send_sse(data):
            try:
                self.wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        valid_count = sum(1 for path, _, _ in pending if path is not None)
        if valid_count <= 1:
            for item in pending:
                send_sse(process_one(item))
        else:
            max_workers = min(valid_count, 4)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(process_one, item): item for item in pending}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        send_sse(future.result())
                    except Exception:
                        traceback.print_exc()

        send_sse({"done": True})

        send_sse({"done": True})

    def handle_corrections_get(self):
        corrections = invoice_ocr.get_pending_corrections()
        json_response(self, 200, {"ok": True, "corrections": corrections})

    def handle_corrections_post(self):
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            json_response(self, 400, {"ok": False, "error": "Empty request body."})
            return
        try:
            body = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            json_response(self, 400, {"ok": False, "error": "Invalid JSON."})
            return

        source_file = str(body.get("source_file") or "").strip()
        if not source_file:
            json_response(self, 400, {"ok": False, "error": "source_file is required."})
            return

        with WRITE_LOCK:
            saved = invoice_ocr.save_correction(
                source_file=source_file,
                corrected_po=str(body.get("corrected_po_number") or "").strip(),
                corrected_invoice=str(body.get("corrected_invoice_number") or "").strip(),
                corrected_lookup_value=str(body.get("corrected_lookup_value") or "").strip(),
                corrected_lookup_source=str(body.get("corrected_lookup_value_source") or "").strip(),
                notes=str(body.get("correction_notes") or "").strip(),
            )

        if saved:
            json_response(self, 200, {"ok": True, "message": f"Correction saved for {source_file}."})
        else:
            json_response(self, 404, {"ok": False, "error": f"No pending correction found for {source_file}."})


def main():
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    mimetypes.add_type("text/javascript", ".js")

    setup_file_logging()
    invoice_ocr.configure_console_output()
    invoice_ocr.ensure_corrections_file()

    print("Loading OCR reader...")
    get_ocr_reader()
    print("OCR reader ready.")

    ollama_ok, ollama_message = invoice_ocr.check_ollama_health()
    if ollama_ok:
        print(f"Ollama: {ollama_message}")
    else:
        print(f"Ollama warning: {ollama_message}")

    server = ThreadingHTTPServer((DEFAULT_HOST, port), InvoiceOCRHandler)
    print(f"Invoice OCR web app running at http://{DEFAULT_HOST}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
