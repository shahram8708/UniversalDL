"use strict";

var queueSource = null;
var queueRetries = 0;
var toastContainer = null;

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

function initDashboard() {
  initToasts();
  initTooltips();
  connectQueueSSE();
  bindCancelButtons();
  bindDeleteHistoryButtons();
  bindRedownloadButtons();
  bindQuickAnalyzeForm();
}

function initTooltips() {
  var tooltipTriggerList = [].slice.call(document.querySelectorAll("[data-bs-toggle='tooltip']"));
  tooltipTriggerList.forEach(function (el) {
    new bootstrap.Tooltip(el);
  });
}

function connectQueueSSE() {
  var countEl = document.getElementById("active-queue-count");
  if (!countEl) {
    return;
  }
  var count = parseInt(countEl.getAttribute("data-count") || countEl.textContent, 10) || 0;
  if (count <= 0) {
    return;
  }

  queueSource = new EventSource("/dashboard/queue-status");
  queueSource.onmessage = function (event) {
    try {
      var data = JSON.parse(event.data);
      handleQueueUpdate(data);
    } catch (err) {
      return;
    }
  };
  queueSource.onerror = function () {
    queueRetries += 1;
    if (queueSource) {
      queueSource.close();
    }
    if (queueRetries <= 3) {
      setTimeout(connectQueueSSE, 5000);
    }
  };
}

function handleQueueUpdate(data) {
  if (!data || !data.jobs) {
    return;
  }
  var list = document.getElementById("queue-list");
  var emptyState = document.getElementById("queue-empty-state");
  var queueCount = data.active_count || 0;

  updateQueueCount(queueCount);

  if (queueCount > 0 && emptyState) {
    emptyState.classList.add("d-none");
  }

  if (!list) {
    return;
  }

  var jobs = data.jobs || [];
  var jobMap = {};
  jobs.forEach(function (job) {
    jobMap[job.job_id] = job;
  });

  jobs.forEach(function (job) {
    var row = list.querySelector(".queue-item[data-job-id='" + job.job_id + "']");
    if (row) {
      updateQueueRow(row, job);
    } else {
      var newRow = buildQueueRow(job);
      if (newRow) {
        list.insertBefore(newRow, list.firstChild);
      }
    }
  });

  var existingRows = list.querySelectorAll(".queue-item");
  existingRows.forEach(function (row) {
    var rowId = row.getAttribute("data-job-id");
    if (!jobMap[rowId]) {
      var titleEl = row.querySelector(".queue-title");
      var title = titleEl ? titleEl.textContent : "Download complete";
      var status = row.getAttribute("data-status") || "";
      if (status === "failed") {
        showToast("Download failed: " + title, "error");
      } else {
        showToast("Download complete: " + title, "success");
      }
      fadeOutAndRemove(row);
    }
  });

  if (queueCount <= 0) {
    if (queueSource) {
      queueSource.close();
    }
    if (emptyState) {
      emptyState.classList.remove("d-none");
    }
    list.innerHTML = "";
  }
}

function updateQueueRow(row, job) {
  var titleEl = row.querySelector(".queue-title");
  if (titleEl && job.title) {
    titleEl.textContent = job.title;
  }
  row.setAttribute("data-status", job.status || "");
  var statusEl = row.querySelector(".job-status-text");
  if (statusEl) {
    statusEl.textContent = formatStatus(job.status);
  }
  var speedEl = row.querySelector(".job-speed-text");
  if (speedEl) {
    speedEl.textContent = formatSpeed(job.speed_bps);
  }
  var etaEl = row.querySelector(".job-eta-text");
  if (etaEl) {
    etaEl.textContent = formatETA(job.eta_seconds);
  }
  var bar = row.querySelector(".progress-bar");
  if (bar) {
    bar.style.width = (job.progress_pct || 0) + "%";
  }
}

function buildQueueRow(job) {
  var row = document.createElement("div");
  row.className = "list-group-item d-flex align-items-start gap-3 queue-item";
  row.setAttribute("data-job-id", job.job_id);
  row.setAttribute("data-status", job.status || "");

  var thumbWrap = document.createElement("div");
  var img = document.createElement("img");
  img.className = "rounded size-40 obj-cover";
  if (job.thumbnail_url) {
    img.src = job.thumbnail_url;
  }
  img.onerror = function () {
    img.style.display = "none";
  };
  thumbWrap.appendChild(img);

  var content = document.createElement("div");
  content.className = "flex-grow-1";

  var title = document.createElement("div");
  title.className = "fw-semibold text-truncate queue-title mw-360";
  title.textContent = job.title || "Analyzing...";

  var meta = document.createElement("div");
  meta.className = "d-flex align-items-center gap-2 small text-muted mt-1";
  var badge = document.createElement("span");
  badge.className = "status-badge status-disabled";
  badge.textContent = (job.platform || "generic").toUpperCase();
  var quality = document.createElement("span");
  quality.className = "text-uppercase";
  quality.textContent = job.selected_quality || "auto";
  var format = document.createElement("span");
  format.textContent = (job.selected_format || "mp4").toUpperCase();
  meta.appendChild(badge);
  meta.appendChild(quality);
  meta.appendChild(format);

  var progress = document.createElement("div");
  progress.className = "progress mt-2 progress-h-6";
  var bar = document.createElement("div");
  bar.className = "progress-bar progress-bar-striped progress-bar-animated";
  bar.style.width = (job.progress_pct || 0) + "%";
  progress.appendChild(bar);

  var status = document.createElement("div");
  status.className = "small text-muted mt-1";
  var statusText = document.createElement("span");
  statusText.className = "job-status-text";
  statusText.textContent = formatStatus(job.status);
  var speedText = document.createElement("span");
  speedText.className = "ms-2 job-speed-text";
  speedText.textContent = formatSpeed(job.speed_bps);
  var etaText = document.createElement("span");
  etaText.className = "ms-2 job-eta-text";
  etaText.textContent = formatETA(job.eta_seconds);
  status.appendChild(statusText);
  status.appendChild(speedText);
  status.appendChild(etaText);

  content.appendChild(title);
  content.appendChild(meta);
  content.appendChild(progress);
  content.appendChild(status);

  var cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-sm btn-outline-danger cancel-job-btn";
  cancelBtn.setAttribute("data-job-id", job.job_id);
  cancelBtn.innerHTML = "<i class='bi bi-x'></i>";

  row.appendChild(thumbWrap);
  row.appendChild(content);
  row.appendChild(cancelBtn);
  return row;
}

function updateQueueCount(count) {
  var countEl = document.getElementById("active-queue-count");
  if (countEl) {
    countEl.textContent = count;
    countEl.setAttribute("data-count", count);
    var card = countEl.closest(".card");
    if (card) {
      var icon = card.querySelector(".bi-hourglass-split");
      if (icon) {
        icon.classList.toggle("text-warning", count > 0);
        icon.classList.toggle("text-success", count <= 0);
      }
    }
  }
}

function bindCancelButtons() {
  var list = document.getElementById("queue-list");
  if (!list) {
    return;
  }
  list.addEventListener("click", function (event) {
    var target = event.target;
    var button = target.closest(".cancel-job-btn");
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

    fetch("/download/cancel/" + jobId, {
      method: "POST",
      headers: {
        "X-CSRFToken": getCsrfToken()
      }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Cancel failed");
        }
        return response.json();
      })
      .then(function () {
        var row = list.querySelector(".queue-item[data-job-id='" + jobId + "']");
        if (row) {
          fadeOutAndRemove(row);
        }
        return stopActionButton(button, { success: true, successText: "Cancelled" }).then(function () {
          showToast("Download cancelled", "info");
        });
      })
      .catch(function () {
        stopActionButton(button, { success: false, error: true, errorText: "Error" }).then(function () {
          showToast("Unable to cancel download", "error");
        });
      });
  });
}

function bindDeleteHistoryButtons() {
  var table = document.getElementById("recent-downloads-table");
  if (!table) {
    return;
  }
  table.addEventListener("click", function (event) {
    var button = event.target.closest(".delete-job-btn");
    if (!button) {
      return;
    }
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) {
      return;
    }
    var ok = window.confirm("Delete this download record?");
    if (!ok) {
      return;
    }

    if (!startActionButton(button, "")) {
      return;
    }

    fetch("/history/delete/" + jobId, {
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
        var row = table.querySelector("tr[data-job-id='" + jobId + "']");
        if (row) {
          fadeOutAndRemove(row);
        }
        return stopActionButton(button, { success: true, successText: "Deleted" }).then(function () {
          showToast("Download record deleted", "success");
        });
      })
      .catch(function () {
        stopActionButton(button, { success: false, error: true, errorText: "Failed" }).then(function () {
          showToast("Unable to delete record", "error");
        });
      });
  });
}

function bindRedownloadButtons() {
  var table = document.getElementById("recent-downloads-table");
  if (!table) {
    return;
  }
  table.addEventListener("click", function (event) {
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
          showToast("Re-downloading. View it in Dashboard.", "success");
        });
      })
      .catch(function (err) {
        stopActionButton(button, { success: false, error: true, errorText: "Failed" }).then(function () {
          showToast(err.message || "Unable to re-download", "error");
        });
      })
      .finally(function () {
        return;
      });
  });
}

function bindQuickAnalyzeForm() {
  var form = document.querySelector("form[action='/download'][method='get']");
  if (!form) {
    return;
  }

  form.addEventListener("submit", function (event) {
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    var submitButton = form.querySelector("button[type='submit']");
    if (!submitButton || !window.ButtonLoader) {
      return;
    }

    if (window.ButtonLoader.isLoading(submitButton)) {
      event.preventDefault();
      return;
    }

    window.ButtonLoader.start(submitButton, "Analyzing...");
  });
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

function formatSpeed(bps) {
  if (!bps || bps < 1024) {
    return (bps || 0) + " B/s";
  }
  if (bps < 1048576) {
    return (bps / 1024).toFixed(1) + " KB/s";
  }
  return (bps / 1048576).toFixed(2) + " MB/s";
}

function formatETA(seconds) {
  if (!seconds || isNaN(seconds) || seconds <= 0) {
    return "Calculating...";
  }
  if (seconds < 60) {
    return seconds + "s remaining";
  }
  if (seconds < 3600) {
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return mins + "m " + secs + "s remaining";
  }
  var hours = Math.floor(seconds / 3600);
  var remainder = Math.floor((seconds % 3600) / 60);
  return hours + "h " + remainder + "m";
}

function formatStatus(status) {
  if (!status) {
    return "Queued";
  }
  return status.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

document.addEventListener("DOMContentLoaded", initDashboard);
