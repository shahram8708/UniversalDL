"use strict";

var MAX_URLS_FREE = 5;
var BATCH_START_URL = "/download/batch/start";
var BATCH_STATUS_URL = "/download/batch/status/";
var BATCH_ZIP_URL = "/download/batch/zip/";
var INFO_URL = "/download/info/";

var parsedUrls = [];
var batchId = null;
var batchEventSource = null;
var isPro = false;
var jobIndexMap = {};
var lastBatchJobs = [];

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  var match = document.cookie.match(/csrf_token=([^;]+)/);
  return match ? match[1] : "";
}

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

function initBatchPage() {
  var proMeta = document.querySelector("meta[name='is-pro']");
  isPro = proMeta && proMeta.getAttribute("content") === "1";

  var textarea = document.getElementById("url-textarea");
  if (textarea) {
    textarea.addEventListener("input", debounce(handleUrlInput, 300));
    textarea.addEventListener("scroll", syncHighlightScroll);
  }

  var fileInput = document.getElementById("txt-file-input");
  if (fileInput) {
    fileInput.addEventListener("change", handleFileUpload);
  }

  var txtUploadTrigger = document.getElementById("txt-upload-trigger");
  if (txtUploadTrigger) {
    txtUploadTrigger.addEventListener("click", handleTxtUploadTrigger);
  }

  var parseBtn = document.getElementById("parse-btn");
  if (parseBtn) {
    parseBtn.addEventListener("click", parseAndPreview);
  }

  var startBtn = document.getElementById("start-btn");
  if (startBtn) {
    startBtn.addEventListener("click", startBatch);
  }

  var pauseBtn = document.getElementById("pause-all-btn");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", pauseAll);
  }

  var cancelBtn = document.getElementById("cancel-all-btn");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", cancelAll);
  }

  var zipBtn = document.getElementById("download-zip-btn");
  if (zipBtn) {
    zipBtn.addEventListener("click", downloadZip);
  }
  var zipBtnSummary = document.getElementById("download-zip-btn-summary");
  if (zipBtnSummary) {
    zipBtnSummary.addEventListener("click", downloadZip);
  }

  initDragAndDrop("drop-zone");
  bindRowCancelButtons();
  handleUrlInput();
}

function handleUrlInput() {
  var textarea = document.getElementById("url-textarea");
  if (!textarea) {
    return;
  }
  var lines = textarea.value.split("\n");
  parsedUrls = lines.map(function (line) {
    var url = line.trim();
    return { url: url, valid: url && url.indexOf("http") === 0 };
  });

  updateHighlights(lines, parsedUrls);

  var validCount = parsedUrls.filter(function (item) { return item.valid; }).length;
  var invalidCount = parsedUrls.filter(function (item) { return item.url && !item.valid; }).length;

  var counter = document.getElementById("url-counter");
    if (counter) {
      counter.textContent = validCount + " URLs ready | " + invalidCount + " invalid";
  }

  var invalidList = document.getElementById("invalid-list");
  if (invalidList) {
    invalidList.innerHTML = "";
    parsedUrls.forEach(function (item, index) {
      if (item.url && !item.valid) {
        var li = document.createElement("div");
        li.textContent = "Line " + (index + 1) + ": " + item.url;
        invalidList.appendChild(li);
      }
    });
  }

  if (!isPro && validCount > MAX_URLS_FREE) {
    showWarning("Free plan limit: only first " + MAX_URLS_FREE + " URLs will be downloaded.");
  } else {
    showWarning("");
  }
}

function handleFileUpload(event) {
  var file = event.target.files[0];
  if (!file) {
    return;
  }
  var reader = new FileReader();
  reader.onload = function (e) {
    var textarea = document.getElementById("url-textarea");
    if (textarea) {
      textarea.value = e.target.result;
    }
    handleUrlInput();
  };
  reader.readAsText(file);
}

function parseAndPreview(event) {
  var triggerButton = event && event.currentTarget ? event.currentTarget : document.getElementById("parse-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!startActionButton(triggerButton, "Parsing URLs...")) {
    return;
  }

  var validUrls = parsedUrls.filter(function (item) { return item.valid; }).map(function (item) { return item.url; });
  if (!validUrls.length) {
    showWarning("Please enter at least one valid URL.");
    stopActionButton(triggerButton, { success: false, error: true, errorText: "No valid URLs" });
    return;
  }

  buildPreviewTable(validUrls);
  document.getElementById("preview-section").classList.remove("d-none");
  document.getElementById("start-btn").classList.remove("d-none");

  fetchPlatformHints(validUrls);
  stopActionButton(triggerButton, { success: true, successText: "Parsed!" });
}

function buildPreviewTable(urls) {
  var tbody = document.getElementById("preview-table-body");
  if (!tbody) {
    return;
  }
  tbody.innerHTML = "";
  urls.slice(0, 10).forEach(function (url, index) {
    var row = document.createElement("tr");
    row.setAttribute("data-index", index);
    row.innerHTML = buildTableRow(index + 1, url);
    tbody.appendChild(row);
  });

  var expand = document.getElementById("expand-btn");
  if (expand) {
    expand.textContent = "Show all " + urls.length + " URLs";
    expand.classList.toggle("d-none", urls.length <= 10);
    expand.onclick = function () {
      if (tbody.children.length < urls.length) {
        urls.slice(10).forEach(function (url, index) {
          var row = document.createElement("tr");
          row.setAttribute("data-index", index + 10);
          row.innerHTML = buildTableRow(index + 11, url);
          tbody.appendChild(row);
        });
        expand.textContent = "Collapse";
      } else {
        tbody.innerHTML = "";
        urls.slice(0, 10).forEach(function (url, index) {
          var row = document.createElement("tr");
          row.setAttribute("data-index", index);
          row.innerHTML = buildTableRow(index + 1, url);
          tbody.appendChild(row);
        });
        expand.textContent = "Show all " + urls.length + " URLs";
      }
    };
  }
}

function buildTableRow(index, url) {
  return (
    "<td>" + index + "</td>" +
    "<td><img class='rounded size-48 obj-cover' src='' alt=''></td>" +
    "<td class='text-truncate mw-240'><div class='fw-semibold'>" + url + "</div>" +
    "<div class='small text-muted'>Waiting for analysis</div>" +
    "<div class='progress mt-2 progress-h-6 d-none'><div class='progress-bar progress-zero'></div></div>" +
    "</td>" +
    "<td><span class='status-badge status-disabled'>Detecting</span></td>" +
    "<td><select class='form-select form-select-sm quality-override'><option>Default</option></select></td>" +
    "<td><select class='form-select form-select-sm format-override'><option>Default</option></select></td>" +
    "<td><span class='status-badge status-disabled'>Queued</span></td>" +
    "<td><button type='button' class='btn btn-sm btn-outline-danger batch-row-cancel-btn'>Cancel</button></td>"
  );
}

function fetchPlatformHints(urls) {
  var queue = urls.slice(0, 10);
  var batchSize = 3;
  var index = 0;

  function nextBatch() {
    var batch = queue.slice(index, index + batchSize);
    if (!batch.length) {
      return;
    }
    Promise.all(batch.map(function (url, batchIndex) {
      return fetch("/download/analyze", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ url: url })
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          var rowIndex = index + batchIndex;
          setTimeout(function () {
            fetch(INFO_URL + data.job_id)
              .then(function (infoRes) { return infoRes.json(); })
              .then(function (payload) {
                updateRow(rowIndex, {
                  title: payload.job ? payload.job.title : "",
                  platform: payload.job ? payload.job.platform : "",
                  status: "queued",
                  progress_pct: 0
                });
              });
          }, 1500);
        });
    })).then(function () {
      index += batchSize;
      nextBatch();
    });
  }

  nextBatch();
}

function bindRowCancelButtons() {
  var tbody = document.getElementById("preview-table-body");
  if (!tbody) {
    return;
  }

  tbody.addEventListener("click", function (event) {
    var button = event.target.closest(".batch-row-cancel-btn");
    if (!button) {
      return;
    }

    var row = button.closest("tr");
    if (!row) {
      return;
    }

    var rowIndex = parseInt(row.getAttribute("data-index"), 10);
    if (isNaN(rowIndex)) {
      return;
    }

    var job = lastBatchJobs[rowIndex];
    if (!job || !job.job_id) {
      showWarning("This item is not ready to cancel yet.");
      return;
    }

    if (!startActionButton(button, "Cancelling...")) {
      return;
    }

    fetch("/download/cancel/" + job.job_id, {
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
        var updatedJob = Object.assign({}, job, {
          status: "cancelled",
          error_message: "Cancelled",
          progress_pct: 0
        });
        lastBatchJobs[rowIndex] = updatedJob;
        updateRow(rowIndex, updatedJob);
        stopActionButton(button, { success: true, successText: "Cancelled" });
      })
      .catch(function () {
        stopActionButton(button, { success: false, error: true, errorText: "Failed" });
      });
  });
}

function startBatch(event) {
  var startBtn = event && event.currentTarget ? event.currentTarget : document.getElementById("start-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!startActionButton(startBtn, "Starting Queue...")) {
    return;
  }

  var validUrls = parsedUrls.filter(function (item) { return item.valid; }).map(function (item) { return item.url; });
  if (!validUrls.length) {
    showWarning("Please enter at least one valid URL.");
    stopActionButton(startBtn, { success: false, error: true, errorText: "No valid URLs" });
    return;
  }

  if (!isPro && validUrls.length > MAX_URLS_FREE) {
    validUrls = validUrls.slice(0, MAX_URLS_FREE);
  }

  var defaultQuality = getSelectedValue("default-quality");
  var defaultFormat = getSelectedValue("default-format");
  var notifyEmail = getChecked("notify-email");

  fetch(BATCH_START_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({
      urls: validUrls,
      default_quality: defaultQuality,
      default_format: defaultFormat,
      notify_email: notifyEmail
    })
  })
    .then(function (response) {
      if (response.status === 403) {
        throw new Error("limit");
      }
      return response.json();
    })
    .then(function (data) {
      batchId = data.batch_id;
      document.getElementById("queue-summary").classList.remove("d-none");
      connectBatchSSE(batchId);
      stopActionButton(startBtn, { success: true, successText: "Queue Started" }).then(function () {
        var startButtonElement = document.getElementById("start-btn");
        if (startButtonElement) {
          startButtonElement.disabled = true;
          startButtonElement.textContent = "Queue Running";
        }
      });
    })
    .catch(function () {
      showWarning("Plan limit reached. Upgrade to Pro for larger batches.");
      stopActionButton(startBtn, { success: false, error: true, errorText: "Failed to Start" });
    });
}

function connectBatchSSE(id) {
  if (batchEventSource) {
    batchEventSource.close();
  }
  batchEventSource = new EventSource(BATCH_STATUS_URL + id);
  batchEventSource.onmessage = function (event) {
    handleBatchUpdate(JSON.parse(event.data));
  };
  batchEventSource.onerror = function () {
    if (batchEventSource) {
      batchEventSource.close();
    }
  };
}

function handleBatchUpdate(data) {
  if (!data || data.error) {
    return;
  }
  lastBatchJobs = data.jobs || [];
  updateBatchStats(data);

  if (data.jobs && data.jobs.length) {
    data.jobs.forEach(function (job, index) {
      if (!jobIndexMap[job.job_id]) {
        jobIndexMap[job.job_id] = index;
      }
      updateRow(index, job);
    });
  }

  updateRecentCompleted(data.jobs || []);
  updateFailedItems(data.jobs || []);

  if (data.status === "complete" || data.status === "partial") {
    if (batchEventSource) {
      batchEventSource.close();
    }
    var zipBtn = document.getElementById("download-zip-btn");
    if (zipBtn) {
      zipBtn.classList.remove("d-none");
    }
    var zipBtnSummary = document.getElementById("download-zip-btn-summary");
    if (zipBtnSummary) {
      zipBtnSummary.classList.remove("d-none");
    }
  }
}

function updateBatchStats(data) {
  setText("queue-total", data.total_jobs);
  setText("queue-completed", data.completed_jobs);
  setText("queue-failed", data.failed_jobs);
  var progress = document.getElementById("queue-progress-bar");
  if (progress) {
    progress.style.width = data.overall_pct + "%";
  }
  var speedEl = document.getElementById("queue-speed");
  if (speedEl) {
    var speeds = (data.jobs || []).map(function (job) { return job.speed_bps || 0; }).filter(function (val) { return val > 0; });
    var avgSpeed = speeds.length ? speeds.reduce(function (a, b) { return a + b; }, 0) / speeds.length : 0;
    var etaValues = (data.jobs || []).map(function (job) { return job.eta_seconds || 0; }).filter(function (val) { return val > 0; });
    var avgEta = etaValues.length ? Math.round(etaValues.reduce(function (a, b) { return a + b; }, 0) / etaValues.length) : 0;
    speedEl.textContent = "Average speed: " + formatSpeed(avgSpeed) + " | ETA: " + formatEta(avgEta);
  }
}

function updateRow(index, job) {
  var tbody = document.getElementById("preview-table-body");
  if (!tbody) {
    return;
  }
  var row = tbody.querySelector("tr[data-index='" + index + "']");
  if (!row) {
    return;
  }
  var thumb = row.querySelector("td:nth-child(2) img");
  if (thumb && job.thumbnail_url) {
    thumb.src = job.thumbnail_url;
  }
  var statusCell = row.querySelector("td:nth-child(7) span");
  if (statusCell) {
    statusCell.textContent = job.status;
    statusCell.className = "status-badge status-disabled";
    if (job.status === "complete") {
      statusCell.className = "status-badge status-active";
    } else if (job.status === "failed") {
      statusCell.className = "status-badge status-down";
    } else if (job.status === "cancelled") {
      statusCell.className = "status-badge status-disabled";
    } else if (job.status === "downloading") {
      statusCell.className = "status-badge status-info";
    }
  }
  var titleCell = row.querySelector("td:nth-child(3) div.fw-semibold");
  if (titleCell && job.title) {
    titleCell.textContent = job.title;
  }
  var subtitleCell = row.querySelector("td:nth-child(3) div.small");
  if (subtitleCell && job.error_message) {
    subtitleCell.textContent = job.error_message;
  }
  var badge = row.querySelector("td:nth-child(4) span");
  if (badge && job.platform) {
    badge.textContent = job.platform.toUpperCase();
    badge.className = "status-badge status-disabled";
  }
  var progressBar = row.querySelector(".progress");
  var bar = row.querySelector(".progress-bar");
  if (progressBar && bar) {
    progressBar.classList.toggle("d-none", !job.progress_pct);
    bar.style.width = (job.progress_pct || 0) + "%";
  }

  var rowCancelButton = row.querySelector(".batch-row-cancel-btn");
  if (rowCancelButton) {
    var terminal = job.status === "complete" || job.status === "failed" || job.status === "cancelled";
    rowCancelButton.disabled = terminal;
  }
}

function updateRecentCompleted(jobs) {
  var container = document.getElementById("recent-completed");
  if (!container) {
    return;
  }
  var completed = jobs.filter(function (job) { return job.status === "complete"; }).slice(-5).reverse();
  container.innerHTML = "";
  if (!completed.length) {
    var empty = document.createElement("div");
    empty.className = "text-muted small";
    empty.textContent = "No completed items yet";
    container.appendChild(empty);
    return;
  }
  completed.forEach(function (job) {
    var item = document.createElement("div");
    item.className = "list-group-item d-flex align-items-center justify-content-between";
    var left = document.createElement("div");
    left.className = "d-flex align-items-center gap-2";
    var img = document.createElement("img");
    img.src = job.thumbnail_url || "";
    img.alt = "";
    img.width = 32;
    img.height = 32;
    img.className = "rounded";
    var title = document.createElement("div");
    title.className = "small";
    title.textContent = job.title || "Completed item";
    left.appendChild(img);
    left.appendChild(title);
    var right = document.createElement("a");
    right.className = "btn btn-sm btn-success";
    right.href = job.download_url || "#";
    right.textContent = "Download";
    item.appendChild(left);
    item.appendChild(right);
    container.appendChild(item);
  });
}

function updateFailedItems(jobs) {
  var container = document.getElementById("failed-items");
  if (!container) {
    return;
  }
  var failed = jobs.filter(function (job) { return job.status === "failed"; }).slice(-5).reverse();
  container.innerHTML = "";
  if (!failed.length) {
    var empty = document.createElement("div");
    empty.className = "text-muted small";
    empty.textContent = "No failed items";
    container.appendChild(empty);
    return;
  }
  failed.forEach(function (job) {
    var item = document.createElement("div");
    item.className = "list-group-item d-flex align-items-center justify-content-between";
    var left = document.createElement("div");
    left.className = "small";
    left.textContent = (job.title || "Failed item") + " - " + (job.error_message || "") ;
    var right = document.createElement("button");
    right.className = "btn btn-sm btn-outline-danger";
    right.textContent = "Retry";
    right.addEventListener("click", function () {
      if (!startActionButton(right, "Retrying...")) {
        return;
      }
      retryJob(job)
        .then(function () {
          stopActionButton(right, { success: true, successText: "Requeued!" });
        })
        .catch(function () {
          stopActionButton(right, { success: false, error: true, errorText: "Failed" });
        });
    });
    item.appendChild(left);
    item.appendChild(right);
    container.appendChild(item);
  });
}

function downloadZip(event) {
  var zipButton = event && event.currentTarget ? event.currentTarget : null;
  if (event && event.preventDefault) {
    event.preventDefault();
  }

  if (!startActionButton(zipButton, "Preparing ZIP...")) {
    return;
  }

  if (!batchId) {
    stopActionButton(zipButton, { success: false, error: true, errorText: "No batch" });
    return;
  }
  window.location.href = BATCH_ZIP_URL + batchId;
  setTimeout(function () {
    stopActionButton(zipButton, { success: false, error: false });
  }, 3000);
}

function pauseAll(event) {
  var pauseButton = event && event.currentTarget ? event.currentTarget : document.getElementById("pause-all-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!startActionButton(pauseButton, "Pausing...")) {
    return;
  }

  cancelAllJobs()
    .then(function () {
      stopActionButton(pauseButton, { success: true, successText: "Paused" });
    })
    .catch(function () {
      stopActionButton(pauseButton, { success: false, error: true, errorText: "Error" });
    });
}

function cancelAll(event) {
  var cancelButton = event && event.currentTarget ? event.currentTarget : document.getElementById("cancel-all-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!startActionButton(cancelButton, "Cancelling All...")) {
    return;
  }

  cancelAllJobs()
    .then(function () {
      stopActionButton(cancelButton, { success: true, successText: "Cancelled" });
    })
    .catch(function () {
      stopActionButton(cancelButton, { success: false, error: true, errorText: "Error" });
    });
}

function cancelAllJobs() {
  if (!lastBatchJobs.length) {
    return Promise.resolve();
  }
  var requests = [];
  lastBatchJobs.forEach(function (job) {
    if (job.status === "downloading" || job.status === "analyzing" || job.status === "queued") {
      requests.push(fetch("/download/cancel/" + job.job_id, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrfToken()
        }
      }));
    }
  });
  return Promise.all(requests);
}

function handleTxtUploadTrigger(event) {
  var trigger = event && event.currentTarget ? event.currentTarget : document.getElementById("txt-upload-trigger");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!startActionButton(trigger, "Loading...")) {
    return;
  }
  var fileInput = document.getElementById("txt-file-input");
  if (fileInput) {
    fileInput.click();
  }
  setTimeout(function () {
    stopActionButton(trigger, { success: false, error: false });
  }, 500);
}

function retryJob(job) {
  if (!job || !job.job_id) {
    return Promise.reject(new Error("Missing job id"));
  }
  return fetch("/download/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({
      job_id: job.job_id,
      quality: job.selected_quality || "best",
      format: job.selected_format || "mp4"
    })
  }).then(function (response) {
    if (!response.ok) {
      throw new Error("Retry failed");
    }
    connectBatchSSE(batchId);
  });
}

function updateHighlights(lines, parsed) {
  var highlight = document.getElementById("url-highlight");
  if (!highlight) {
    return;
  }
  var html = lines.map(function (line, idx) {
    var escaped = escapeHtml(line || " ");
    var isInvalid = parsed[idx] && parsed[idx].url && !parsed[idx].valid;
    if (isInvalid) {
      return "<span class='invalid-line'>" + escaped + "</span>";
    }
    return escaped;
  }).join("\n");
  highlight.innerHTML = html;
}

function syncHighlightScroll() {
  var textarea = document.getElementById("url-textarea");
  var highlight = document.getElementById("url-highlight");
  if (textarea && highlight) {
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
  }
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;");
}

function formatSpeed(bps) {
  if (!bps || bps <= 0) {
    return "-- MB/s";
  }
  if (bps < 1048576) {
    return (bps / 1024).toFixed(1) + " KB/s";
  }
  return (bps / 1048576).toFixed(2) + " MB/s";
}

function formatEta(seconds) {
  if (!seconds || seconds <= 0) {
    return "--";
  }
  if (seconds < 60) {
    return seconds + "s";
  }
  var mins = Math.floor(seconds / 60);
  var secs = seconds % 60;
  return mins + "m " + secs + "s";
}

function initDragAndDrop(dropZoneId) {
  var dropZone = document.getElementById(dropZoneId);
  if (!dropZone) {
    return;
  }
  dropZone.addEventListener("dragover", function (event) {
    event.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", function () {
    dropZone.classList.remove("drag-over");
  });
  dropZone.addEventListener("drop", function (event) {
    event.preventDefault();
    dropZone.classList.remove("drag-over");
    var files = event.dataTransfer.files;
    if (files && files[0]) {
      handleFileUpload({ target: { files: files } });
    }
  });
}

function debounce(fn, delay) {
  var timer = null;
  return function () {
    var args = arguments;
    clearTimeout(timer);
    timer = setTimeout(function () {
      fn.apply(null, args);
    }, delay);
  };
}

function showWarning(message) {
  var banner = document.getElementById("batch-warning");
  if (!banner) {
    return;
  }
  if (!message) {
    banner.classList.add("d-none");
    banner.textContent = "";
    return;
  }
  banner.textContent = message;
  banner.classList.remove("d-none");
}

function setText(id, value) {
  var el = document.getElementById(id);
  if (el) {
    el.textContent = value;
  }
}

function getSelectedValue(id) {
  var el = document.getElementById(id);
  return el ? el.value : "";
}

function getChecked(id) {
  var el = document.getElementById(id);
  return el ? el.checked : false;
}

document.addEventListener("DOMContentLoaded", initBatchPage);
