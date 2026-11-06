const patientModalElement = document.getElementById("patientModal");
const deleteModalElement = document.getElementById("deleteModal");
const modal = new bootstrap.Modal(patientModalElement);
const deleteModal = new bootstrap.Modal(deleteModalElement);
const form = document.getElementById("patientForm");
const rows = document.getElementById("patientRows");
const searchInput = document.getElementById("searchInput");

let editingId = null;
let pendingDeleteId = null;
let returnFocusElement = null;
let patients = [];
let allPatients = [];
let searchResults = [];
let activeFilter = "all";
let currentPage = 1;
let sortField = null;
let sortDirection = "asc";
let searchTimer = null;
let listController = null;

const pageSize = 8;
const patientFields = ["name", "dob", "email", "glucose", "haemoglobin", "cholesterol"];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function initials(name) {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function showToast(message, type = "success") {
  document.getElementById("toastMessage").textContent = message;
  const toast = document.getElementById("appToast");
  toast.dataset.kind = type;
  bootstrap.Toast.getOrCreateInstance(toast).show();
}

function clearValidationErrors() {
  form.querySelectorAll(".is-invalid").forEach((input) => {
    input.classList.remove("is-invalid");
    input.removeAttribute("aria-invalid");
    const describedBy = (input.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((id) => id && !id.endsWith("-error"));
    if (describedBy.length) input.setAttribute("aria-describedby", describedBy.join(" "));
    else input.removeAttribute("aria-describedby");
  });
  form.querySelectorAll(".field-error").forEach((node) => node.remove());
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.errors ? Object.values(data.errors).join(" ") : "Something went wrong. Please try again.");
    error.fields = data.errors || {};
    throw error;
  }
  return data;
}

async function loadPatients() {
  listController?.abort();
  listController = new AbortController();
  try {
    allPatients = await api("/api/patients", { signal: listController.signal });
    const query = searchInput.value.trim().toLocaleLowerCase();
    searchResults = allPatients.filter((patient) => `${patient.name} ${patient.email}`.toLocaleLowerCase().includes(query));
    applyFilter();
  } catch (error) {
    if (error.name !== "AbortError") showToast(error.message, "error");
  }
}

function hasOutOfRangeLab(patient) {
  return patient.glucose < 70 || patient.glucose > 99
    || patient.haemoglobin < 12 || patient.haemoglobin > 17.5
    || patient.cholesterol >= 200;
}

function needsAiReview(patient) {
  return /prediction unavailable|provider is not configured|provider rejected/i.test(patient.remarks);
}

function applyFilter() {
  const counts = {
    all: searchResults.length,
    "out-of-range": searchResults.filter(hasOutOfRangeLab).length,
    "high-risk": searchResults.filter((patient) => /\bHigh\b/i.test(patient.remarks)).length,
    "ai-review": searchResults.filter(needsAiReview).length,
  };
  document.getElementById("filterAllCount").textContent = counts.all;
  document.getElementById("filterLabCount").textContent = counts["out-of-range"];
  document.getElementById("filterRiskCount").textContent = counts["high-risk"];
  document.getElementById("filterAiCount").textContent = counts["ai-review"];
  document.querySelectorAll(".filter-chip").forEach((button) => {
    const selected = button.dataset.filter === activeFilter;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });

  patients = searchResults.filter((patient) => {
    if (activeFilter === "out-of-range") return hasOutOfRangeLab(patient);
    if (activeFilter === "high-risk") return /\bHigh\b/i.test(patient.remarks);
    if (activeFilter === "ai-review") return needsAiReview(patient);
    return true;
  });
  currentPage = 1;
  renderPatients();
}

function sortPatients(records) {
  if (!sortField) return records;
  const numericFields = ["glucose", "haemoglobin", "cholesterol"];
  return records.sort((left, right) => {
    let first = left[sortField];
    let second = right[sortField];
    if (sortField === "dob") {
      first = first.split("/").reverse().join("");
      second = second.split("/").reverse().join("");
    }
    const comparison = numericFields.includes(sortField)
      ? Number(first) - Number(second)
      : String(first).localeCompare(String(second), undefined, { sensitivity: "base" });
    return sortDirection === "asc" ? comparison : -comparison;
  });
}

function riskClass(remarks) {
  const level = remarks.match(/\b(Low|Moderate|High)\b/i)?.[1] || "info";
  return `risk-${level.toLowerCase()}`;
}

function renderPatientRow(patient) {
  const glucoseAlert = patient.glucose < 70 || patient.glucose > 99 ? "lab-alert" : "";
  const haemoglobinAlert = patient.haemoglobin < 12 || patient.haemoglobin > 17.5 ? "lab-alert" : "";
  const cholesterolAlert = patient.cholesterol >= 200 ? "lab-alert" : "";
  return `
    <tr>
      <td>
        <div class="patient-cell">
          <div class="patient-avatar">${escapeHtml(initials(patient.name))}</div>
          <div><div class="patient-name">${escapeHtml(patient.name)}</div><div class="patient-email">${escapeHtml(patient.email)}</div></div>
        </div>
      </td>
      <td>${escapeHtml(patient.dob)}</td>
      <td><span class="lab-value ${glucoseAlert}">${Number(patient.glucose).toLocaleString()}</span> <span class="text-muted">mg/dL</span></td>
      <td><span class="lab-value ${haemoglobinAlert}">${Number(patient.haemoglobin).toLocaleString()}</span> <span class="text-muted">g/dL</span></td>
      <td><span class="lab-value ${cholesterolAlert}">${Number(patient.cholesterol).toLocaleString()}</span> <span class="text-muted">mg/dL</span></td>
      <td><span class="remark-pill ${riskClass(patient.remarks)}"><i class="bi bi-stars" aria-hidden="true"></i>${escapeHtml(patient.remarks)}</span></td>
      <td>
        <div class="row-actions">
          <button class="icon-button" type="button" title="Edit patient" aria-label="Edit ${escapeHtml(patient.name)}" data-action="edit" data-id="${patient.id}"><i class="bi bi-pencil" aria-hidden="true"></i></button>
          <button class="rerun-button" type="button" title="Re-run AI prediction" aria-label="Re-run prediction for ${escapeHtml(patient.name)}" data-action="rerun" data-id="${patient.id}"><i class="bi bi-arrow-repeat" aria-hidden="true"></i> Re-run AI</button>
          <button class="icon-button delete" type="button" title="Delete patient" aria-label="Delete ${escapeHtml(patient.name)}" data-action="delete" data-id="${patient.id}"><i class="bi bi-trash3" aria-hidden="true"></i></button>
        </div>
      </td>
    </tr>`;
}

function renderPatients() {
  const hasPatients = patients.length > 0;
  const emptyState = document.getElementById("emptyState");

  emptyState.classList.toggle("d-none", hasPatients);
  const emptyTitle = document.querySelector("#emptyState h3");
  const emptyMessage = document.querySelector("#emptyState p");
  const filterHasNoMatches = searchResults.length > 0 && !hasPatients;
  emptyTitle.textContent = !allPatients.length
    ? "No patient records yet"
    : filterHasNoMatches
      ? "No records match this filter"
      : "No matching patients";
  emptyMessage.textContent = !allPatients.length
    ? "Add a patient to get started with your health workspace."
    : filterHasNoMatches
      ? "Choose another quick filter or show all patients."
      : "Try a different name or email search.";
  document.getElementById("emptyAdd").classList.toggle("d-none", allPatients.length > 0);
  document.querySelector(".table-responsive").classList.toggle("d-none", !hasPatients);
  document.querySelector(".table-footer").classList.toggle("d-none", !hasPatients);

  const sortedPatients = sortPatients([...patients]);
  const pageCount = Math.max(1, Math.ceil(sortedPatients.length / pageSize));
  currentPage = Math.min(currentPage, pageCount);
  const startIndex = (currentPage - 1) * pageSize;
  const visiblePatients = sortedPatients.slice(startIndex, startIndex + pageSize);
  rows.innerHTML = visiblePatients.map(renderPatientRow).join("");

  document.getElementById("recordCount").textContent = patients.length
    ? `Showing ${startIndex + 1}–${Math.min(startIndex + pageSize, patients.length)} of ${patients.length} records`
    : "Showing 0 records";
  document.getElementById("pageLabel").textContent = `Page ${currentPage} of ${pageCount}`;
  document.getElementById("previousPage").disabled = currentPage === 1;
  document.getElementById("nextPage").disabled = currentPage === pageCount;
  document.getElementById("exportCsv").disabled = patients.length === 0;

  document.querySelectorAll(".sort-button").forEach((button) => {
    const active = button.dataset.sort === sortField;
    button.querySelector("i").className = active
      ? `bi bi-arrow-${sortDirection === "asc" ? "up" : "down"}`
      : "bi bi-arrow-down-up";
    button.setAttribute("aria-pressed", String(active));
    button.closest("th").setAttribute("aria-sort", active ? (sortDirection === "asc" ? "ascending" : "descending") : "none");
  });

  const total = allPatients.length;
  const average = (field) => total
    ? allPatients.reduce((sum, patient) => sum + Number(patient[field]), 0) / total
    : null;
  document.getElementById("totalCount").textContent = total;
  document.getElementById("glucoseStat").innerHTML = `${average("glucose")?.toFixed(1) ?? "—"} <small>mg/dL</small>`;
  document.getElementById("haemoglobinStat").innerHTML = `${average("haemoglobin")?.toFixed(1) ?? "—"} <small>g/dL</small>`;
  document.getElementById("highRiskStat").textContent = allPatients.filter((patient) => /\bHigh\b/i.test(patient.remarks)).length;
  document.getElementById("aiNotice").classList.toggle(
    "d-none",
    !allPatients.some((patient) => /prediction unavailable|provider is not configured/i.test(patient.remarks)),
  );
}

function csvCell(value) {
  let text = String(value ?? "");
  if (/^[\t\r ]*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function exportPatientsCsv() {
  const headings = ["Name", "Date of birth", "Email", "Glucose (mg/dL)", "Haemoglobin (g/dL)", "Cholesterol (mg/dL)", "AI remark"];
  const data = sortPatients([...patients]).map((patient) => [
    patient.name,
    patient.dob,
    patient.email,
    patient.glucose,
    patient.haemoglobin,
    patient.cholesterol,
    patient.remarks,
  ]);
  const csv = [headings, ...data].map((line) => line.map(csvCell).join(",")).join("\r\n");
  const file = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = `carewell-patients-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  showToast(`Exported ${data.length} filtered patient record${data.length === 1 ? "" : "s"}.`);
}

function openForm(patient = null) {
  returnFocusElement = document.activeElement;
  editingId = patient?.id ?? null;
  form.reset();
  clearValidationErrors();
  document.getElementById("formError").classList.add("d-none");
  document.getElementById("modalTitle").textContent = editingId ? "Edit patient" : "Add patient";
  document.querySelector(".save-label").textContent = editingId ? "Save changes" : "Save patient";
  if (patient) {
    for (const key of patientFields) form.elements[key].value = patient[key];
  }
  modal.show();
}

patientModalElement.addEventListener("shown.bs.modal", () => form.elements.name.focus());
patientModalElement.addEventListener("hidden.bs.modal", () => {
  if (returnFocusElement instanceof HTMLElement && returnFocusElement.isConnected) returnFocusElement.focus();
});

document.getElementById("addPatient").addEventListener("click", () => openForm());
document.getElementById("emptyAdd").addEventListener("click", () => openForm());
searchInput.addEventListener("input", () => {
  currentPage = 1;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadPatients, 160);
});

document.querySelectorAll(".filter-chip").forEach((button) => {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter;
    applyFilter();
  });
});

document.getElementById("exportCsv").addEventListener("click", exportPatientsCsv);

document.querySelectorAll(".sort-button").forEach((button) => {
  button.addEventListener("click", () => {
    if (sortField === button.dataset.sort) sortDirection = sortDirection === "asc" ? "desc" : "asc";
    else {
      sortField = button.dataset.sort;
      sortDirection = "asc";
    }
    currentPage = 1;
    renderPatients();
  });
});

document.getElementById("previousPage").addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    renderPatients();
  }
});

document.getElementById("nextPage").addEventListener("click", () => {
  if (currentPage * pageSize < patients.length) {
    currentPage += 1;
    renderPatients();
  }
});

rows.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const patient = patients.find((item) => item.id === Number(button.dataset.id));
  if (!patient) return;

  if (button.dataset.action === "edit") {
    openForm(patient);
    return;
  }
  if (button.dataset.action === "rerun") {
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Checking';
    try {
      const updated = await api(`/api/patients/${patient.id}/prediction`, { method: "POST" });
      const unavailable = updated.remarks.toLowerCase().includes("unavailable");
      showToast(unavailable ? updated.remarks : "AI prediction refreshed.", unavailable ? "warning" : "success");
      await loadPatients();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
      button.innerHTML = '<i class="bi bi-arrow-repeat" aria-hidden="true"></i> Re-run AI';
    }
    return;
  }
  if (button.dataset.action === "delete") {
    returnFocusElement = button;
    pendingDeleteId = patient.id;
    document.getElementById("deleteMessage").textContent = `Delete ${patient.name}'s record? This action cannot be undone.`;
    deleteModal.show();
  }
});

deleteModalElement.addEventListener("hidden.bs.modal", () => {
  pendingDeleteId = null;
  const target = returnFocusElement?.isConnected ? returnFocusElement : document.getElementById("addPatient");
  target.focus();
});

document.getElementById("confirmDelete").addEventListener("click", async () => {
  if (pendingDeleteId === null) return;
  const patientId = pendingDeleteId;
  const confirmButton = document.getElementById("confirmDelete");
  confirmButton.disabled = true;
  try {
    await api(`/api/patients/${patientId}`, { method: "DELETE" });
    deleteModal.hide();
    showToast("Patient record deleted.");
    await loadPatients();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    pendingDeleteId = null;
    confirmButton.disabled = false;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearValidationErrors();
  const errorBox = document.getElementById("formError");
  errorBox.classList.add("d-none");
  const saveButton = document.getElementById("saveButton");
  const payload = Object.fromEntries(patientFields.map((key) => [key, form.elements[key].value.trim()]));
  saveButton.disabled = true;
  saveButton.querySelector(".save-label").classList.add("d-none");
  saveButton.querySelector(".save-loading").classList.remove("d-none");
  try {
    const saved = await api(editingId ? `/api/patients/${editingId}` : "/api/patients", {
      method: editingId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    modal.hide();
    const unavailable = saved.remarks.toLowerCase().includes("unavailable");
    const message = unavailable ? saved.remarks : (editingId ? "Patient record updated." : "Patient record added.");
    showToast(message, unavailable ? "warning" : "success");
    await loadPatients();
  } catch (error) {
    if (error.fields) {
      for (const [field, message] of Object.entries(error.fields)) {
        const input = form.elements[field];
        if (!input) {
          errorBox.textContent = message;
          errorBox.classList.remove("d-none");
          continue;
        }
        input.classList.add("is-invalid");
        input.setAttribute("aria-invalid", "true");
        const hint = document.createElement("div");
        hint.id = `${field}-error`;
        hint.className = "invalid-feedback field-error";
        hint.textContent = message;
        const describedBy = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
        describedBy.add(hint.id);
        input.setAttribute("aria-describedby", [...describedBy].join(" "));
        input.insertAdjacentElement("afterend", hint);
      }
      form.querySelector(".is-invalid")?.focus();
    } else {
      errorBox.textContent = error.message;
      errorBox.classList.remove("d-none");
    }
    showToast(error.message, "error");
  } finally {
    saveButton.disabled = false;
    saveButton.querySelector(".save-label").classList.remove("d-none");
    saveButton.querySelector(".save-loading").classList.add("d-none");
  }
});

document.getElementById("today").textContent = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date());
loadPatients();
