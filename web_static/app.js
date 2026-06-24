const fileInput = document.querySelector("#fileInput");
const dropZone = document.querySelector("#dropZone");
const selectedFiles = document.querySelector("#selectedFiles");
const excelCheck = document.querySelector("#excelCheck");
const printerCheck = document.querySelector("#printerCheck");
const printerQueueInput = document.querySelector("#printerQueueInput");
const manualOrderInput = document.querySelector("#manualOrderInput");
const manualLookupButton = document.querySelector("#manualLookupButton");
const manualLookupResult = document.querySelector("#manualLookupResult");
const processButton = document.querySelector("#processButton");
const resultsBody = document.querySelector("#resultsBody");
const detailsArea = document.querySelector("#detailsArea");
const expandButton = document.querySelector("#expandButton");
const correctionsButton = document.querySelector("#correctionsButton");
const correctionsArea = document.querySelector("#correctionsArea");
const correctionsList = document.querySelector("#correctionsList");
const refreshCorrectionsButton = document.querySelector("#refreshCorrectionsButton");

let chosenFiles = [];
let latestResults = [];
let expandedIndexes = new Set();

function fieldValue(result, name) {
  return result.fields?.[name] || "-";
}

function statusInfo(result) {
  if (result.pending) return ["Processing…", "status-processing"];

  if (!result.ok) {
    return ["Error", "status-error"];
  }

  const finalStatus = result.final_status || "Needs Manual Review";
  if (finalStatus === "Label Sent to Printer") return [finalStatus, "status-sent"];
  if (finalStatus === "Label Generated") return [finalStatus, "status-found"];
  if (finalStatus === "Order Found") return [finalStatus, "status-found"];
  if (finalStatus === "Duplicate Print Blocked") return [finalStatus, "status-duplicate"];
  if (finalStatus === "Print Failed") return [finalStatus, "status-error"];

  return ["Needs Manual Review", "status-review"];
}

function renderSelectedFiles() {
  if (!chosenFiles.length) {
    selectedFiles.classList.remove("visible");
    selectedFiles.textContent = "";
    return;
  }

  selectedFiles.classList.add("visible");
  selectedFiles.innerHTML = chosenFiles
    .map((file) => `<div>${escapeHtml(file.name)} (${formatBytes(file.size)})</div>`)
    .join("");
}

function setFiles(files) {
  chosenFiles = Array.from(files || []);
  renderSelectedFiles();
}

function safeFileName(name) {
  return (name || "uploaded-file").replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^[._]+|[._]+$/g, "") || "uploaded-file";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderResults() {
  if (!latestResults.length) {
    resultsBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="8">Upload invoices to see extracted fields and validation results.</td>
      </tr>
    `;
    detailsArea.innerHTML = "";
    expandButton.disabled = true;
    return;
  }

  resultsBody.innerHTML = latestResults
    .map((result, index) => {
      const [label, className] = statusInfo(result);
      const expanded = expandedIndexes.has(index);
      const pending = result.pending;
      const dupBadge = result.duplicate_warning
        ? ` <span class="duplicate-badge">Duplicate</span>`
        : "";
      return `
        <tr>
          <td><button class="toggle-button" type="button" data-index="${index}" ${pending ? "disabled" : ""}>${expanded ? "-" : "+"}</button></td>
          <td>${escapeHtml(result.file_name)}${dupBadge}</td>
          <td><span class="status ${className}">${label}</span></td>
          <td>${escapeHtml(fieldValue(result, "Invoice Number"))}</td>
          <td>${escapeHtml(fieldValue(result, "PO Number"))}</td>
          <td>${escapeHtml(fieldValue(result, "Lookup Value"))}</td>
          <td>${escapeHtml(labelSummary(result))}</td>
          <td>${escapeHtml(result.processed_at || "-")}</td>
        </tr>
      `;
    })
    .join("");

  detailsArea.innerHTML = latestResults.map(renderDetail).join("");

  const doneCount = latestResults.filter((r) => !r.pending).length;
  expandButton.disabled = doneCount === 0;
  expandButton.textContent = expandedIndexes.size === doneCount && doneCount > 0 ? "Collapse All" : "Expand All";
}

function renderDetail(result, index) {
  if (result.pending) return "";

  const active = expandedIndexes.has(index) ? "active" : "";
  const lookup = result.lookup_result || {};
  const verification = result.verification_result || {};
  const labelResults = result.label_results || [];
  const summaries = result.matched_record_summaries || [];

  const dupWarning = result.duplicate_warning
    ? `<p class="duplicate-notice">${escapeHtml(result.duplicate_warning)}</p>`
    : "";

  if (!result.ok) {
    return `
      <article class="result-detail ${active}" id="detail-${index}">
        <div class="detail-section">
          <h3>${escapeHtml(result.file_name)}</h3>
          ${dupWarning}
          <p class="status status-error">${escapeHtml(result.error || "Processing failed.")}</p>
        </div>
      </article>
    `;
  }

  return `
    <article class="result-detail ${active}" id="detail-${index}">
      ${dupWarning}
      <div class="detail-grid">
        <section class="detail-section">
          <h3>OCR Extracted Fields</h3>
          <div class="kv-list">
            ${kv("Invoice Number", fieldValue(result, "Invoice Number"))}
            ${kv("PO Number", fieldValue(result, "PO Number"))}
            ${kv("Lookup Value", fieldValue(result, "Lookup Value"))}
            ${kv("Lookup Source", fieldValue(result, "Lookup Value Source"))}
            ${kv("Tracking Number", fieldValue(result, "Tracking Number"))}
            ${kv("Order Number", fieldValue(result, "Order Number"))}
          </div>
        </section>
        <section class="detail-section">
          <h3>Excel Validation Summary</h3>
          <div class="kv-list">
            ${kv("Lookup Status", lookup.status || "SKIPPED")}
            ${kv("Lookup Message", lookup.message || "-")}
            ${kv("Matched By", lookup.matched_by || "-")}
            ${kv("Matched Row", lookup.matched_row_number || "-")}
            ${kv("AI Verification", verification.verification_status || "SKIPPED")}
            ${kv("Confidence", verification.confidence || "-")}
          </div>
        </section>
      </div>
      <section class="matched-row">
        <h3>Label & Print Status</h3>
        ${kv("Final Status", result.final_status || "Needs Manual Review")}
        ${labelResults.map((label, i) => `
          <div class="label-result-block">
            <div class="kv-list">
              ${kv(`Label ${labelResults.length > 1 ? i + 1 : ""}Status`.trim(), label.status || "SKIPPED")}
              ${kv("Print Message", label.message || "-")}
            </div>
            ${label.label_path ? `<button class="secondary-button print-label-button" type="button" data-label-path="${escapeHtml(label.label_path)}" data-override="${label.status === "DUPLICATE_BLOCKED" ? "true" : "false"}">${label.status === "DUPLICATE_BLOCKED" ? "Print Again" : "Print Label"}</button>` : ""}
          </div>
        `).join("")}
      </section>
      <section class="matched-row">
        <h3>Matched Row Details</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Ticket</th><th>Dept</th><th>Requester</th><th>Qty</th><th>Item</th></tr>
            </thead>
            <tbody>
              ${summaries.map(s => `
                <tr>
                  <td>${escapeHtml(s.Ticket || "-")}</td>
                  <td>${escapeHtml(s.Dept || "-")}</td>
                  <td>${escapeHtml(s.Requester || "-")}</td>
                  <td>${escapeHtml(s.Qty || "-")}</td>
                  <td>${escapeHtml(s.Item || "-")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  `;
}

function labelSummary(result) {
  if (result.pending) return "-";
  const labels = result.label_results || [];
  const count = labels.length;
  const statuses = labels.map(l => l.status);
  if (statuses.includes("LABEL_SENT_TO_PRINTER")) return count > 1 ? `Sent (${count})` : "Sent";
  if (statuses.includes("LABEL_GENERATED")) return count > 1 ? `Generated (${count})` : "Generated";
  if (statuses.includes("DUPLICATE_BLOCKED")) return "Duplicate blocked";
  if (statuses.includes("PRINT_FAILED")) return "Print failed";
  if (result.lookup_result?.status === "MATCH_FOUND") return "Ready";
  return "-";
}

function kv(label, value) {
  return `
    <div class="kv">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "-")}</strong>
    </div>
  `;
}

async function processFiles() {
  if (!chosenFiles.length) {
    selectedFiles.classList.add("visible");
    selectedFiles.textContent = "Choose one or more invoice files first.";
    return;
  }

  const formData = new FormData();
  chosenFiles.forEach((file) => formData.append("files", file));
  formData.append("model", "llama3.1:8b");
  formData.append("excel_validation", excelCheck.checked ? "true" : "false");
  formData.append("send_to_printer", printerCheck.checked ? "true" : "false");
  formData.append("printer_queue", printerQueueInput.value.trim());


  processButton.disabled = true;
  processButton.textContent = "Processing…";
  fileInput.disabled = true;
  dropZone.classList.add("drop-disabled");

  // Initialise a pending placeholder for every chosen file so rows appear immediately.
  latestResults = chosenFiles.map((file) => ({
    file_name: safeFileName(file.name),
    pending: true,
    ok: false,
    fields: {},
    processed_at: "",
  }));
  expandedIndexes = new Set();
  renderResults();

  try {
    if (window.location.protocol === "file:") {
      throw new Error("Open the app from http://127.0.0.1:7860 instead of opening index.html directly.");
    }

    const response = await fetch("/api/process-stream", { method: "POST", body: formData });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || "Processing failed.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamDone = false;

    while (!streamDone) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double newlines.
      const parts = buffer.split("\n\n");
      buffer = parts.pop();

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        let data;
        try { data = JSON.parse(line.slice(6)); } catch { continue; }
        if (data.done) { streamDone = true; break; }

        // Replace the first pending slot matching this file name.
        const idx = latestResults.findIndex((r) => r.pending && r.file_name === data.file_name);
        if (idx >= 0) {
          latestResults[idx] = data;
          if (idx === 0) expandedIndexes.add(0);
        } else {
          latestResults.push(data);
        }
        renderResults();
      }
    }
  } catch (error) {
    const message = error.message === "Failed to fetch"
      ? "Could not reach the local Python server. Make sure web_app.py is still running and open the app at http://127.0.0.1:7860."
      : error.message;
    latestResults = [{
      file_name: "Upload request",
      ok: false,
      error: message,
      fields: {},
      processed_at: new Date().toLocaleTimeString(),
    }];
    expandedIndexes.add(0);
    renderResults();
  } finally {
    processButton.disabled = false;
    processButton.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 16V4"></path>
        <path d="M7 9l5-5 5 5"></path>
        <path d="M5 20h14"></path>
      </svg>
      Process Files
    `;
    fileInput.disabled = false;
    dropZone.classList.remove("drop-disabled");
  }
}

// ── Corrections panel ─────────────────────────────────────────────────────────

async function loadCorrections() {
  correctionsList.innerHTML = '<p class="corrections-loading">Loading…</p>';
  try {
    const res = await fetch("/api/corrections");
    const data = await res.json().catch(() => ({ ok: false }));
    if (!data.ok) throw new Error(data.error || "Failed to load.");
    renderCorrections(data.corrections || []);
    correctionsButton.textContent = `Corrections (${(data.corrections || []).length})`;
  } catch (err) {
    correctionsList.innerHTML = `<p class="corrections-error">${escapeHtml(err.message)}</p>`;
  }
}

function renderCorrections(corrections) {
  if (!corrections.length) {
    correctionsList.innerHTML = '<p class="corrections-empty">No pending corrections.</p>';
    return;
  }

  correctionsList.innerHTML = corrections.map((row) => {
    const sf = row["Source File"] || "";
    const extPO = row["Extracted PO Number"] || "-";
    const extInv = row["Extracted Invoice Number"] || "-";
    const lookupStatus = row["Excel Lookup Status"] || "-";
    const aiStatus = row["AI Verification Status"] || "-";
    const notes = row["Correction Notes"] || "";
    return `
      <article class="correction-card" data-source-file="${escapeHtml(sf)}">
        <div class="correction-meta">
          <strong>${escapeHtml(sf)}</strong>
          <span>Extracted PO: ${escapeHtml(extPO)} &nbsp;|&nbsp; Invoice: ${escapeHtml(extInv)} &nbsp;|&nbsp; Lookup: ${escapeHtml(lookupStatus)} &nbsp;|&nbsp; AI: ${escapeHtml(aiStatus)}</span>
        </div>
        <div class="correction-form">
          <label>Corrected PO Number<input type="text" class="corr-po" placeholder="e.g. PO-12345"></label>
          <label>Corrected Lookup Value<input type="text" class="corr-lookup" placeholder="e.g. PO-12345"></label>
          <label>Correction Notes<input type="text" class="corr-notes" value="${escapeHtml(notes)}" placeholder="Optional notes"></label>
          <button class="secondary-button save-correction-button" type="button">Save</button>
        </div>
      </article>
    `;
  }).join("");
}

async function saveCorrection(card) {
  const sourceFile = card.dataset.sourceFile;
  const correctedPO = card.querySelector(".corr-po").value.trim();
  const correctedLookup = card.querySelector(".corr-lookup").value.trim();
  const notes = card.querySelector(".corr-notes").value.trim();
  const btn = card.querySelector(".save-correction-button");

  if (!correctedLookup) {
    btn.textContent = "Enter a lookup value first";
    setTimeout(() => { btn.textContent = "Save"; }, 2500);
    return;
  }

  btn.disabled = true;
  btn.textContent = "Saving…";

  try {
    const res = await fetch("/api/corrections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_file: sourceFile,
        corrected_po_number: correctedPO,
        corrected_invoice_number: "",
        corrected_lookup_value: correctedLookup,
        corrected_lookup_value_source: correctedPO ? "PO Number" : "Manual",
        correction_notes: notes,
      }),
    });
    const data = await res.json().catch(() => ({ ok: false, error: "Server error." }));
    if (data.ok) {
      card.classList.add("correction-saved");
      btn.textContent = "Saved!";
      setTimeout(() => loadCorrections(), 1000);
    } else {
      btn.textContent = `Error: ${data.error}`;
      btn.disabled = false;
    }
  } catch {
    btn.textContent = "Network error";
    btn.disabled = false;
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────

fileInput.addEventListener("change", () => setFiles(fileInput.files));

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragging");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  if (fileInput.disabled) return;
  fileInput.files = event.dataTransfer.files;
  setFiles(event.dataTransfer.files);
});

processButton.addEventListener("click", processFiles);

expandButton.addEventListener("click", () => {
  const doneResults = latestResults.filter((r) => !r.pending);
  if (expandedIndexes.size === doneResults.length && doneResults.length > 0) {
    expandedIndexes = new Set();
  } else {
    expandedIndexes = new Set(
      latestResults.map((r, i) => (!r.pending ? i : null)).filter((i) => i !== null)
    );
  }
  renderResults();
});

correctionsButton.addEventListener("click", () => {
  const hidden = correctionsArea.classList.toggle("hidden");
  if (!hidden) loadCorrections();
});

refreshCorrectionsButton.addEventListener("click", loadCorrections);

correctionsList.addEventListener("click", (event) => {
  const btn = event.target.closest(".save-correction-button");
  if (!btn) return;
  saveCorrection(btn.closest(".correction-card"));
});

resultsBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-index]");
  if (!button) return;
  const index = Number(button.dataset.index);
  if (expandedIndexes.has(index)) {
    expandedIndexes.delete(index);
  } else {
    expandedIndexes.add(index);
  }
  renderResults();
});

detailsArea.addEventListener("click", async (event) => {
  const button = event.target.closest(".print-label-button");
  if (!button) return;

  const labelPath = button.dataset.labelPath;
  const printerQueue = printerQueueInput.value.trim();

  if (!printerQueue) {
    button.textContent = "Enter a queue name first";
    setTimeout(() => { button.textContent = "Print Label"; }, 2500);
    return;
  }

  const overrideDuplicate = button.dataset.override === "true";
  button.disabled = true;
  button.textContent = "Printing…";

  try {
    const response = await fetch("/api/print", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label_path: labelPath, printer_queue: printerQueue, override_duplicate: overrideDuplicate }),
    });
    const data = await response.json().catch(() => ({ ok: false, error: "Server error." }));
    if (data.duplicate_blocked) {
      button.textContent = "Already printed!";
      button.dataset.override = "true";
      setTimeout(() => { button.textContent = "Print Again"; }, 2500);
    } else {
      button.textContent = data.ok ? "Sent!" : `Failed: ${data.error}`;
      setTimeout(() => { button.textContent = overrideDuplicate ? "Print Again" : "Print Label"; }, 3000);
    }
  } catch {
    button.textContent = "Network error";
    setTimeout(() => { button.textContent = overrideDuplicate ? "Print Again" : "Print Label"; }, 3000);
  } finally {
    button.disabled = false;
  }
});

// ── Manual order lookup ───────────────────────────────────────────────────────

async function manualLookup() {
  const orderNumber = manualOrderInput.value.trim();
  if (!orderNumber) {
    manualLookupResult.className = "manual-lookup-result error";
    manualLookupResult.innerHTML = "Enter an order number first.";
    return;
  }

  manualLookupButton.disabled = true;
  manualLookupButton.textContent = "Looking up…";
  manualLookupResult.className = "manual-lookup-result hidden";

  try {
    const response = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        order_number: orderNumber,
        send_to_printer: printerCheck.checked,
        printer_queue: printerQueueInput.value.trim(),
      }),
    });

    const data = await response.json().catch(() => ({ ok: false, error: "Server error." }));

    if (!data.ok) {
      manualLookupResult.className = "manual-lookup-result error";
      manualLookupResult.innerHTML = escapeHtml(data.error || "No match found.");
      return;
    }

    const rows = data.records || [];
    const labels = data.label_results || [];

    manualLookupResult.className = "manual-lookup-result success";
    manualLookupResult.innerHTML = `
      <strong>${rows.length} match${rows.length !== 1 ? "es" : ""} found for order ${escapeHtml(orderNumber)}</strong>
      <table>
        <thead>
          <tr><th>#</th><th>Ticket</th><th>Dept</th><th>Requester</th><th>Qty</th><th>Item</th><th>Label</th><th></th></tr>
        </thead>
        <tbody>
          ${rows.map((r, i) => {
            const label = labels[i] || {};
            const canPrint = !!label.label_path;
            return `<tr>
              <td>${i + 1}</td>
              <td>${escapeHtml(r.Ticket || "-")}</td>
              <td>${escapeHtml(r.Dept || "-")}</td>
              <td>${escapeHtml(r.Requester || "-")}</td>
              <td>${escapeHtml(r.Qty || "-")}</td>
              <td>${escapeHtml(r.Item || "-")}</td>
              <td>${escapeHtml(label.status || "-")}</td>
              <td>${canPrint ? `<button class="secondary-button manual-print-button" type="button" data-label-path="${escapeHtml(label.label_path)}">${label.status === "DUPLICATE_BLOCKED" ? "Print Again" : "Print"}</button>` : "-"}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    `;
  } catch {
    manualLookupResult.className = "manual-lookup-result error";
    manualLookupResult.innerHTML = "Could not reach the server.";
  } finally {
    manualLookupButton.disabled = false;
    manualLookupButton.textContent = "Look Up & Print";
  }
}

manualLookupButton.addEventListener("click", manualLookup);
manualOrderInput.addEventListener("keydown", (e) => { if (e.key === "Enter") manualLookup(); });

manualLookupResult.addEventListener("click", async (event) => {
  const button = event.target.closest(".manual-print-button");
  if (!button) return;

  const labelPath = button.dataset.labelPath;
  const printerQueue = printerQueueInput.value.trim();
  if (!printerQueue) {
    button.textContent = "Enter queue name first";
    setTimeout(() => { button.textContent = "Print"; }, 2500);
    return;
  }

  button.disabled = true;
  button.textContent = "Printing…";

  try {
    const response = await fetch("/api/print", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label_path: labelPath, printer_queue: printerQueue, override_duplicate: false }),
    });
    const data = await response.json().catch(() => ({ ok: false, error: "Server error." }));
    if (data.duplicate_blocked) {
      button.textContent = "Already printed";
      button.dataset.labelPath = "";
    } else if (data.ok) {
      button.textContent = "Sent!";
      button.dataset.labelPath = "";
      setTimeout(() => { button.disabled = true; button.textContent = "Printed"; }, 2000);
      return;
    } else {
      button.textContent = `Failed: ${data.error}`;
      setTimeout(() => { button.textContent = "Print"; button.disabled = false; }, 3000);
      return;
    }
  } catch {
    button.textContent = "Network error";
    setTimeout(() => { button.textContent = "Print"; button.disabled = false; }, 3000);
    return;
  }
  button.disabled = false;
});
