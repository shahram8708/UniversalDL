"use strict";

var toastContainer = null;
var deleteModal = null;
var deleteModalElement = null;
var pendingDeleteJobId = null;
var pendingDeleteButton = null;

function startActionButton(button, loadingText) {
  if (!button || !window.ButtonLoader) {
    return true;
  }
  if (window.ButtonLoader.isLoading(button)) {
    return false;
  }
  window.ButtonLoader.start(button, loadingText);
  return true;
}

function stopActionButton(button, options) {
  if (!button || !window.ButtonLoader) {
    return Promise.resolve();
  }
  return window.ButtonLoader.stop(button, options || { success: false, error: false });
}

function lockModalWhileLoading(modalEl, activeButton) {
  if (!modalEl) {
    return function () {
      return;
    };
  }

  var closeButton = modalEl.querySelector(".btn-close");
  var dismissButtons = modalEl.querySelectorAll("[data-bs-dismiss='modal']");
  var preventClose = function (event) {
    if (activeButton && window.ButtonLoader && window.ButtonLoader.isLoading(activeButton)) {
      event.preventDefault();
    }
  };

  modalEl.addEventListener("hide.bs.modal", preventClose);

  if (closeButton) {
    closeButton.disabled = true;
  }
  dismissButtons.forEach(function (button) {
    if (button !== activeButton) {
      button.disabled = true;
    }
  });

  return function unlockModal() {
    modalEl.removeEventListener("hide.bs.modal", preventClose);
    if (closeButton) {
      closeButton.disabled = false;
    }
    dismissButtons.forEach(function (button) {
      button.disabled = false;
    });
  };
}

function initHistory() {
  initToasts();
  initTooltips();
  bindSelectAllCheckbox();
  bindRowCheckboxes();
  bindDeleteButtons();
  bindBulkDelete();
  bindRedownloadButtons();
  bindFilterBadgeDismiss();
  bindBulkExport();
  bindBulkClear();
  bindExportCSV();
  bindFilterFormActions();
  updateBulkActionsBar();
}

function initTooltips() {
  var tooltipTriggerList = [].slice.call(document.querySelectorAll("[data-bs-toggle='tooltip']"));
  tooltipTriggerList.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });
}

function bindSelectAllCheckbox() {
  var selectAll = document.getElementById("select-all-checkbox");
  if (!selectAll) {
    return;
  }
  selectAll.addEventListener("change", function () {
    var checked = selectAll.checked;
    var checkboxes = document.querySelectorAll(".job-checkbox");
    checkboxes.forEach(function (box) {
      box.checked = checked;
    });
    updateBulkActionsBar();
  });
}

function bindRowCheckboxes() {
  document.addEventListener("change", function (event) {
    if (!event.target.classList.contains("job-checkbox")) {
      return;
    }
    var selectAll = document.getElementById("select-all-checkbox");
    if (selectAll) {
      var allBoxes = document.querySelectorAll(".job-checkbox");
      var checkedBoxes = document.querySelectorAll(".job-checkbox:checked");
      if (checkedBoxes.length === allBoxes.length && allBoxes.length > 0) {
        selectAll.indeterminate = false;
        selectAll.checked = true;
      } else if (checkedBoxes.length === 0) {
        selectAll.indeterminate = false;
        selectAll.checked = false;
      } else {
        selectAll.indeterminate = true;
      }
    }
    updateBulkActionsBar();
  });
}

function updateBulkActionsBar() {
  var bulkBar = document.getElementById("bulk-actions-bar");
  if (!bulkBar) {
    return;
  }
  var checkedBoxes = document.querySelectorAll(".job-checkbox:checked");
  var count = checkedBoxes.length;
  var countEl = document.getElementById("selected-count");
  if (countEl) {
    countEl.textContent = count;
  }
  if (count > 0) {
    bulkBar.classList.remove("d-none");
  } else {
    bulkBar.classList.add("d-none");
  }
}

function bindDeleteButtons() {
  var modalEl = document.getElementById("deleteConfirmModal");
  if (modalEl) {
    deleteModal = new bootstrap.Modal(modalEl);
    deleteModalElement = modalEl;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".delete-job-btn");
    if (!button) {
      return;
    }
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) {
      return;
    }
    pendingDeleteJobId = jobId;
    pendingDeleteButton = button;
    if (deleteModal) {
      deleteModal.show();
    }
  });

  var confirmBtn = document.getElementById("confirm-delete-btn");
  if (!confirmBtn) {
    return;
  }

  confirmBtn.addEventListener("click", function () {
    if (!pendingDeleteJobId) {
      return;
    }

    if (!startActionButton(confirmBtn, "Deleting...")) {
      return;
    }

    if (pendingDeleteButton) {
      startActionButton(pendingDeleteButton, "");
    }

    var unlockModal = lockModalWhileLoading(deleteModalElement, confirmBtn);

    deleteJob(pendingDeleteJobId, confirmBtn, pendingDeleteButton)
      .finally(function () {
        pendingDeleteJobId = null;
        pendingDeleteButton = null;
        unlockModal();
      });
  });
}

function deleteJob(jobId, confirmBtn, rowDeleteBtn) {
  return fetch("/history/delete/" + jobId, {
    method: "POST",
    headers: {
      "X-CSRFToken": getCsrfToken()
    }
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("Delete failed");
      }
      return response.json();
    })
    .then(function () {
      var stops = [];
      if (confirmBtn) {
        stops.push(stopActionButton(confirmBtn, { success: true, successText: "Deleted" }));
      }
      if (rowDeleteBtn) {
        stops.push(stopActionButton(rowDeleteBtn, { success: false, error: false }));
      }
      return Promise.all(stops);
    })
    .then(function () {
      if (deleteModal) {
        deleteModal.hide();
      }
      var row = document.querySelector("tr[data-job-id='" + jobId + "']");
      if (row) {
        fadeOutAndRemove(row);
      }
      updateTotalCount(-1);
      showToast("Download record deleted", "success");
      updateBulkActionsBar();
    })
    .catch(function () {
      var restores = [];
      if (confirmBtn) {
        restores.push(stopActionButton(confirmBtn, { success: false, error: true, errorText: "Failed" }));
      }
      if (rowDeleteBtn) {
        restores.push(stopActionButton(rowDeleteBtn, { success: false, error: false }));
      }
      return Promise.all(restores).then(function () {
        showToast("Unable to delete record", "error");
      });
    });
}

function bindBulkDelete() {
  var bulkBtn = document.getElementById("bulk-delete-btn");
  if (!bulkBtn) {
    return;
  }

  bulkBtn.addEventListener("click", function () {
    var ids = getSelectedJobIds();
    if (!ids.length) {
      showToast("Select items first", "info");
      return;
    }

    var ok = window.confirm("Delete " + ids.length + " downloads? This cannot be undone.");
    if (!ok) {
      return;
    }

    if (!startActionButton(bulkBtn, "Deleting...")) {
      return;
    }

    fetch("/history/delete-bulk", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({ job_ids: ids })
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json().then(function (data) {
            throw new Error(data.message || "Bulk delete failed");
          });
        }
        return response.json();
      })
      .then(function (data) {
        ids.forEach(function (id) {
          var row = document.querySelector("tr[data-job-id='" + id + "']");
          if (row) {
            fadeOutAndRemove(row);
          }
        });
        updateTotalCount(-1 * (data.deleted_count || ids.length));
        showToast((data.deleted_count || ids.length) + " downloads deleted", "success");
        clearSelection();
        return stopActionButton(bulkBtn, { success: true, successText: "Deleted!" });
      })
      .catch(function (err) {
        stopActionButton(bulkBtn, { success: false, error: true, errorText: "Failed" }).then(function () {
          showToast(err.message || "Unable to delete selected downloads", "error");
        });
      });
  });
}

function bindRedownloadButtons() {
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".redownload-btn");
    if (!button) {
      return;
    }
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) {
      return;
    }

    if (!startActionButton(button, "")) {
      return;
    }

    fetch("/history/redownload/" + jobId, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken()
      }
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json().then(function (data) {
            throw new Error(data.message || "Redownload failed");
          });
        }
        return response.json();
      })
      .then(function () {
        return stopActionButton(button, { success: true, successText: "Queued" }).then(function () {
          showToast("Re-downloading. <a href='/dashboard' class='text-accent text-decoration-none ms-2'>View in Dashboard</a>", "success");
        });
      })
      .catch(function (err) {
        stopActionButton(button, { success: false, error: true, errorText: "Failed" }).then(function () {
          showToast(err.message || "Unable to re-download", "error");
        });
      });
  });
}

function bindFilterBadgeDismiss() {
  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".filter-badge-dismiss");
    if (!btn) {
      return;
    }
    var filterKey = btn.getAttribute("data-filter");
    var url = new URL(window.location.href);
    url.searchParams.delete(filterKey);
    url.searchParams.delete("page");
    window.location.href = url.toString();
  });
}

function bindExportCSV() {
  var exportBtn = document.getElementById("export-csv-btn");
  if (!exportBtn) {
    return;
  }

  exportBtn.addEventListener("click", function (event) {
    event.preventDefault();
    if (!startActionButton(exportBtn, "Preparing...")) {
      return;
    }
    window.location.href = exportBtn.getAttribute("href") || "/history/export";
    setTimeout(function () {
      stopActionButton(exportBtn, { success: false, error: false });
    }, 2000);
  });
}

function bindBulkExport() {
  var exportBtn = document.getElementById("bulk-export-btn");
  if (!exportBtn) {
    return;
  }

  exportBtn.addEventListener("click", function () {
    var rows = getSelectedRows();
    if (!rows.length) {
      showToast("Select items first", "info");
      return;
    }

    if (!startActionButton(exportBtn, "Preparing...")) {
      return;
    }

    exportSelectedToCsv();
    setTimeout(function () {
      stopActionButton(exportBtn, { success: false, error: false });
    }, 2000);
  });
}

function bindBulkClear() {
  var clearBtn = document.getElementById("bulk-clear-btn");
  if (!clearBtn) {
    return;
  }

  clearBtn.addEventListener("click", function () {
    if (!startActionButton(clearBtn, "Clearing...")) {
      return;
    }
    clearSelection();
    stopActionButton(clearBtn, { success: false, error: false });
  });
}

function bindFilterFormActions() {
  var filterForm = document.querySelector("form[action='/history']");
  if (!filterForm) {
    return;
  }

  filterForm.addEventListener("submit", function (event) {
    if (!filterForm.checkValidity()) {
      filterForm.reportValidity();
      return;
    }

    var applyButton = filterForm.querySelector("button[type='submit']");
    if (!applyButton || !window.ButtonLoader) {
      return;
    }

    if (window.ButtonLoader.isLoading(applyButton)) {
      event.preventDefault();
      return;
    }

    window.ButtonLoader.start(applyButton, "Filtering...");
  });

  var clearLink = filterForm.querySelector("a[href='/history']");
  if (clearLink) {
    clearLink.addEventListener("click", function (event) {
      event.preventDefault();
      if (window.ButtonLoader) {
        window.ButtonLoader.start(clearLink, "Clearing...");
      }
      window.location.href = clearLink.getAttribute("href") || "/history";
    });
  }
}

function exportSelectedToCsv() {
  var rows = getSelectedRows();
  if (!rows.length) {
    showToast("No items selected", "info");
    return;
  }
  var headers = [
    "ID",
    "Title",
    "Platform",
    "Content Type",
    "Quality",
    "Format",
    "Status",
    "File Size (MB)",
    "Created At",
    "Completed At",
    "URL"
  ];
  var lines = [headers.join(",")];
  rows.forEach(function (row) {
    var values = [
      row.getAttribute("data-job-id") || "",
      row.getAttribute("data-title") || "",
      row.getAttribute("data-platform") || "",
      row.getAttribute("data-content-type") || "",
      row.getAttribute("data-quality") || "",
      (row.getAttribute("data-format") || "").toUpperCase(),
      row.getAttribute("data-status") || "",
      row.getAttribute("data-size-mb") || "",
      row.getAttribute("data-created-at") || "",
      row.getAttribute("data-completed-at") || "",
      row.getAttribute("data-url") || ""
    ];
    lines.push(values.map(csvEscape).join(","));
  });
  var csv = lines.join("\n");
  var blob = new Blob([csv], { type: "text/csv" });
  var link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "universaldl_history_selected.csv";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast("Export ready", "success");
}

function csvEscape(value) {
  var text = (value || "").toString();
  if (text.indexOf(",") >= 0 || text.indexOf("\n") >= 0 || text.indexOf("\"") >= 0) {
    return "\"" + text.replace(/\"/g, "\"\"") + "\"";
  }
  return text;
}

function getSelectedJobIds() {
  var checkboxes = document.querySelectorAll(".job-checkbox:checked");
  var ids = [];
  checkboxes.forEach(function (box) {
    ids.push(box.value);
  });
  return ids;
}

function getSelectedRows() {
  var ids = getSelectedJobIds();
  var rows = [];
  ids.forEach(function (id) {
    var row = document.querySelector("tr[data-job-id='" + id + "']");
    if (row) {
      rows.push(row);
    }
  });
  return rows;
}

function clearSelection() {
  var checkboxes = document.querySelectorAll(".job-checkbox");
  checkboxes.forEach(function (box) {
    box.checked = false;
  });
  var selectAll = document.getElementById("select-all-checkbox");
  if (selectAll) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  }
  updateBulkActionsBar();
}

function updateTotalCount(delta) {
  var countEl = document.getElementById("total-count");
  if (!countEl) {
    return;
  }
  var current = parseInt(countEl.textContent, 10) || 0;
  var next = Math.max(0, current + delta);
  countEl.textContent = next;
}

function initToasts() {
  toastContainer = document.getElementById("toast-container");
  if (!toastContainer) {
    toastContainer = document.createElement("div");
    toastContainer.className = "toast-container position-fixed top-0 end-0 p-3";
    toastContainer.id = "toast-container";
    document.body.appendChild(toastContainer);
  }
}

function showToast(message, type) {
  if (!toastContainer) {
    initToasts();
  }
  var toastEl = document.createElement("div");
  var tone = "success";
  if (type === "error") {
    tone = "error";
  } else if (type === "info") {
    tone = "info";
  }
  toastEl.className = "toast align-items-center theme-toast theme-toast-" + tone;
  toastEl.setAttribute("role", "alert");
  toastEl.setAttribute("aria-live", "assertive");
  toastEl.setAttribute("aria-atomic", "true");
  toastEl.innerHTML = "<div class='d-flex'><div class='toast-body'>" + message + "</div><button type='button' class='btn-close me-2 m-auto' data-bs-dismiss='toast' aria-label='Close'></button></div>";
  toastContainer.appendChild(toastEl);
  var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
  toast.show();
  toastEl.addEventListener("hidden.bs.toast", function () {
    toastEl.remove();
  });
}

function fadeOutAndRemove(element) {
  if (!element) {
    return;
  }
  element.style.transition = "opacity 0.4s ease";
  element.style.opacity = "0";
  setTimeout(function () {
    if (element.parentNode) {
      element.parentNode.removeChild(element);
    }
  }, 400);
}

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  return "";
}

document.addEventListener("DOMContentLoaded", initHistory);
