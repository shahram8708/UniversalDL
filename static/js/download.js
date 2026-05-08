"use strict";

var ANALYZE_URL = "/download/analyze";
var START_URL = "/download/start";
var INFO_URL = "/download/info/";
var CANCEL_URL = "/download/cancel/";

var currentState = "input";
var currentJobId = null;
var mediaInfo = null;
var progressEventSource = null;
var analyzeInterval = null;
var progressRetries = 0;
var INFO_LOAD_MAX_ATTEMPTS = 2;
var infoLoadAttempts = 0;
var infoLoadStarted = false;
var analyzeButtonFallbackTimer = null;

function getCsrfToken() {
  var meta = document.querySelector("meta[name='csrf-token']");
  if (meta) {
    return meta.getAttribute("content");
  }
  var match = document.cookie.match(/csrf_token=([^;]+)/);
  return match ? match[1] : "";
}

function initDownloadPage() {
  var urlInput = document.getElementById("url-input");
  var analyzeBtn = document.getElementById("analyze-btn");
  var downloadBtn = document.getElementById("download-btn");
  var qualitySelect = document.getElementById("quality-select");
  var formatSelect = document.getElementById("format-select");
  var tryAnotherBtn = document.getElementById("try-another-btn");
  var downloadAgainBtn = document.getElementById("download-again-btn");
  var cancelBtn = document.getElementById("cancel-btn");
  var cancelBtnSecondary = document.getElementById("cancel-btn-secondary");
  var subtitleToggle = document.getElementById("subtitle-embed");
  var urlParam = new URLSearchParams(window.location.search).get("url");

  if (urlInput) {
    urlInput.addEventListener("paste", function () {
      setTimeout(handleAnalyze, 500);
    });
  }
  if (analyzeBtn) {
    analyzeBtn.addEventListener("click", handleAnalyze);
  }
  if (downloadBtn) {
    downloadBtn.addEventListener("click", handleDownload);
  }
  if (qualitySelect) {
    qualitySelect.addEventListener("change", updateFileSizeEstimate);
  }
  if (formatSelect) {
    formatSelect.addEventListener("change", updateFileSizeEstimate);
  }
  if (tryAnotherBtn) {
    tryAnotherBtn.addEventListener("click", handleTryAnother);
  }
  if (downloadAgainBtn) {
    downloadAgainBtn.addEventListener("click", handleDownloadAgain);
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", handleCancel);
  }
  if (cancelBtnSecondary) {
    cancelBtnSecondary.addEventListener("click", handleCancel);
  }
  if (subtitleToggle) {
    subtitleToggle.addEventListener("change", function () {
      toggleSubtitleModes(subtitleToggle.checked);
    });
  }

  if (urlParam && urlInput) {
    urlInput.value = urlParam;
    handleAnalyze();
  }
}

function clearAnalyzeButtonFallback() {
  if (analyzeButtonFallbackTimer) {
    clearTimeout(analyzeButtonFallbackTimer);
    analyzeButtonFallbackTimer = null;
  }
}

function handleAnalyze(event) {
  var analyzeBtn = document.getElementById("analyze-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }

  if (analyzeBtn && window.ButtonLoader) {
    if (window.ButtonLoader.isLoading(analyzeBtn)) {
      return;
    }
    window.ButtonLoader.start(analyzeBtn, "Analyzing...");
    clearAnalyzeButtonFallback();
    analyzeButtonFallbackTimer = setTimeout(function () {
      if (window.ButtonLoader.isLoading(analyzeBtn)) {
        window.ButtonLoader.stop(analyzeBtn, { success: false, error: false });
      }
    }, 30000);
  }

  var urlInput = document.getElementById("url-input");
  var url = urlInput ? urlInput.value.trim() : "";
  if (!url || url.indexOf("http") !== 0) {
    showUrlError("Please enter a valid URL starting with http:// or https://");
    if (analyzeBtn && window.ButtonLoader) {
      window.ButtonLoader.stop(analyzeBtn, { success: false, error: true, errorText: "Failed" });
    }
    return;
  }
  showUrlError("");
  infoLoadAttempts = 0;
  infoLoadStarted = false;
  if (progressEventSource) {
    progressEventSource.close();
    progressEventSource = null;
  }
  setState("analyzing");
  startAnalyzeMessages();
  updateAnalyzeProgress(5);

  fetch(ANALYZE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify({
      url: url,
      quality: getSelectedValue("quality-select-input") || getSelectedValue("quality-select"),
      format: getSelectedValue("format-select-input") || getSelectedValue("format-select")
    })
  })
    .then(function (response) {
      if (!response.ok) {
        return response.json().then(function (data) {
          throw new Error(data.message || "Analyze failed");
        });
      }
      return response.json();
    })
    .then(function (data) {
      currentJobId = data.job_id;
      connectProgressSSE(currentJobId);
    })
    .catch(function (error) {
      stopAnalyzeMessages();
      if (analyzeBtn && window.ButtonLoader) {
        window.ButtonLoader.stop(analyzeBtn, { success: false, error: true, errorText: "Failed" });
      }
      setState("error", { error_message: error && error.message ? error.message : "Network error" });
    });
}

function connectProgressSSE(jobId) {
  if (progressEventSource) {
    progressEventSource.close();
  }
  progressRetries = 0;
  progressEventSource = new EventSource("/download/progress/" + jobId);
  progressEventSource.onmessage = function (event) {
    handleProgressEvent(JSON.parse(event.data));
  };
  progressEventSource.onerror = function () {
    progressRetries += 1;
    if (progressEventSource) {
      progressEventSource.close();
    }
    if (progressRetries <= 3) {
      setTimeout(function () {
        connectProgressSSE(jobId);
      }, 2000);
    }
  };
}

function handleProgressEvent(data) {
  if (data.platform) {
    setText("detected-platform", data.platform.toUpperCase());
    setText("media-platform", data.platform.toUpperCase());
  }
  if (data.title) {
    setText("media-title", data.title);
    setText("download-title", data.title);
  }
  if (data.status === "queued") {
    setState("analyzing");
    updateAnalyzeProgress(data.progress_pct || 3);
    return;
  }
  if (data.status === "analyzing") {
    setState("analyzing");
    updateAnalyzeProgress(data.progress_pct || 10);
    return;
  }
  if (data.status === "pending_download") {
    updateAnalyzeProgress(100);
    stopAnalyzeMessages();

    if (currentState === "result") {
      return;
    }

    if (data.media_info && Array.isArray(data.media_info.qualities) && data.media_info.qualities.length) {
      mediaInfo = data.media_info;
      populateMediaInfo(mediaInfo, data || {});
      setState("result");
      return;
    }

    if (!infoLoadStarted && infoLoadAttempts < INFO_LOAD_MAX_ATTEMPTS) {
      infoLoadStarted = true;
      loadJobInfoWithRetry();
    }
    return;
  } else if (data.status === "downloading" || data.status === "converting") {
    setState("downloading");
    var progressValue = data.progress_pct;
    if (data.status === "converting" && (!progressValue || progressValue < 95)) {
      progressValue = 95;
    }
    updateProgress(progressValue, data.speed_bps, data.eta_seconds);
    if (data.status === "converting") {
      setText("status-text", "Converting to selected format...");
    }
  } else if (data.status === "complete") {
    if (progressEventSource) {
      progressEventSource.close();
    }
    setState("success", data);
  } else if (data.status === "failed") {
    if (progressEventSource) {
      progressEventSource.close();
    }
    stopAnalyzeMessages();
    var analyzeBtn = document.getElementById("analyze-btn");
    if (analyzeBtn && window.ButtonLoader && window.ButtonLoader.isLoading(analyzeBtn)) {
      window.ButtonLoader.stop(analyzeBtn, { success: false, error: true, errorText: "Failed" });
    }
    setState("error", data);
  }
}

function loadJobInfoWithRetry() {
  if (!currentJobId) {
    infoLoadStarted = false;
    return;
  }
  infoLoadAttempts += 1;
  fetch(INFO_URL + currentJobId)
    .then(function (res) {
      if (!res.ok) {
        throw new Error("Failed to load media info");
      }
      return res.json();
    })
    .then(function (payload) {
      mediaInfo = payload && payload.media_info ? payload.media_info : null;
      if (!mediaInfo || !Array.isArray(mediaInfo.qualities) || !mediaInfo.qualities.length) {
        throw new Error("Media info is still processing");
      }
      populateMediaInfo(mediaInfo, payload.job || {});
      setState("result");
      infoLoadStarted = false;
    })
    .catch(function () {
      infoLoadStarted = false;
      if (infoLoadAttempts >= INFO_LOAD_MAX_ATTEMPTS) {
        setState("error", { error_message: "Could not load media details. Please try again." });
      }
    });
}

function populateMediaInfo(info, job) {
  if (!info) {
    return;
  }
  setImage("media-thumbnail", info.thumbnail);
  setText("media-title", info.title || "Untitled");
  setText("media-author", info.author || "");
  setText("media-duration", formatDuration(info.duration));
  setText("media-views", formatCount(info.view_count));
  setText("media-upload-date", info.upload_date || "");

  var qualitySelect = document.getElementById("quality-select");
  if (qualitySelect) {
    qualitySelect.innerHTML = "";
    var selectedQualityValue = job && job.selected_quality ? String(job.selected_quality) : "";
    (info.qualities || []).forEach(function (quality) {
      var option = document.createElement("option");
      var optionValue = quality.selector || quality.format_id || quality.label;
      option.value = optionValue;
      option.dataset.qualityLabel = quality.label || "";
      var sizeText = quality.size_bytes ? formatBytes(quality.size_bytes) : "Size varies";
      var displayLabel = quality.display_label || quality.label || "Unknown";
      option.textContent = displayLabel + " | " + sizeText;
      option.dataset.sizeBytes = quality.size_bytes || "";
      if (
        selectedQualityValue &&
        (
          selectedQualityValue === String(optionValue) ||
          selectedQualityValue.toLowerCase() === String(quality.label || "").toLowerCase()
        )
      ) {
        option.selected = true;
      }
      qualitySelect.appendChild(option);
    });
  }

  var subtitleSection = document.getElementById("subtitle-section");
  var subtitleSelect = document.getElementById("subtitle-language");
  if (subtitleSelect) {
    subtitleSelect.innerHTML = "";
  }
  if (info.subtitles && info.subtitles.length > 0) {
    if (subtitleSection) {
      subtitleSection.classList.remove("d-none");
    }
    toggleSubtitleModes(getChecked("subtitle-embed"));
    info.subtitles.forEach(function (sub) {
      var option = document.createElement("option");
      option.value = sub.lang;
      option.textContent = sub.label || sub.lang;
      subtitleSelect.appendChild(option);
    });
  } else if (subtitleSection) {
    subtitleSection.classList.add("d-none");
    toggleSubtitleModes(false);
  }

  var chaptersToggle = document.getElementById("chapters-toggle");
  if (chaptersToggle) {
    chaptersToggle.checked = !!(info.chapters && info.chapters.length);
  }

  var resolvedContentType = resolveContentType(info, job ? job.content_type : "");
  updateFormatOptions(resolvedContentType);
  var formatSelect = document.getElementById("format-select");
  var selectedFormat = job && job.selected_format ? String(job.selected_format).toLowerCase() : "";
  if (formatSelect && selectedFormat) {
    var matchingOption = Array.prototype.find.call(formatSelect.options, function (option) {
      return String(option.value).toLowerCase() === selectedFormat;
    });
    if (matchingOption && !matchingOption.disabled) {
      formatSelect.value = matchingOption.value;
    } else {
      var firstAllowed = Array.prototype.find.call(formatSelect.options, function (option) {
        return !option.disabled;
      });
      if (firstAllowed) {
        formatSelect.value = firstAllowed.value;
      }
    }
  }
  updateFileSizeEstimate();
}

function resolveContentType(info, fallbackType) {
  var declaredType = String((info && info.content_type) || "").trim().toLowerCase();
  if (declaredType) {
    return declaredType;
  }

  var qualities = info && Array.isArray(info.qualities) ? info.qualities : [];
  if (!qualities.length) {
    return String(fallbackType || "").trim().toLowerCase();
  }

  var imageFormats = { jpg: true, jpeg: true, png: true, gif: true, webp: true, bmp: true };
  var audioFormats = { mp3: true, m4a: true, flac: true, wav: true, aac: true, ogg: true, opus: true };
  var imageCount = 0;
  var audioCount = 0;

  qualities.forEach(function (quality) {
    var formatValue = String((quality && quality.format) || "").trim().toLowerCase();
    var labelValue = String((quality && quality.label) || "").trim().toLowerCase();
    var urlValue = String((quality && quality.url) || "").trim().toLowerCase();

    var isImage = !!imageFormats[formatValue] || /\.(jpg|jpeg|png|gif|webp|bmp)(\?|$)/.test(urlValue) || labelValue.indexOf("image") === 0;
    var isAudio = !!audioFormats[formatValue] || /\.(mp3|m4a|flac|wav|aac|ogg|opus)(\?|$)/.test(urlValue) || labelValue === "audio" || labelValue === "audio_only" || labelValue === "audio only";

    if (isImage) {
      imageCount += 1;
    }
    if (isAudio) {
      audioCount += 1;
    }
  });

  if (imageCount === qualities.length) {
    return "image";
  }
  if (audioCount === qualities.length) {
    return "audio";
  }

  return String(fallbackType || "").trim().toLowerCase();
}

function updateFormatOptions(contentType) {
  var formatSelect = document.getElementById("format-select");
  if (!formatSelect) {
    return;
  }
  var options = Array.prototype.slice.call(formatSelect.options);
  var normalizedType = String(contentType || "").trim().toLowerCase();
  var preferredOrder = ["mp4", "mkv", "webm", "mp3", "m4a", "flac", "jpg", "jpeg", "png"];

  if (normalizedType === "audio") {
    preferredOrder = ["mp3", "m4a", "flac"];
  } else if (normalizedType === "image") {
    preferredOrder = ["jpg", "jpeg", "png"];
  }

  options.forEach(function (option) {
    var value = String(option.value || "").trim().toLowerCase();
    var isAllowed = preferredOrder.indexOf(value) !== -1;
    option.classList.toggle("d-none", !isAllowed);
    option.disabled = !isAllowed;
  });

  if (preferredOrder.indexOf(formatSelect.value) === -1 && preferredOrder.length) {
    formatSelect.value = preferredOrder[0];
  }
}

function updateFileSizeEstimate() {
  var qualitySelect = document.getElementById("quality-select");
  var estimate = document.getElementById("file-size-estimate");
  if (!qualitySelect || !estimate) {
    return;
  }
  var option = qualitySelect.options[qualitySelect.selectedIndex];
  if (!option || !option.dataset.sizeBytes) {
    estimate.textContent = "Size varies by format";
    return;
  }
  var bytes = parseInt(option.dataset.sizeBytes, 10);
  estimate.textContent = "Estimated size: " + formatBytes(bytes);

  var downloadBtn = document.getElementById("download-btn");
  if (downloadBtn) {
    var formatSelect = document.getElementById("format-select");
    var qualityLabel = option.dataset.qualityLabel || option.value;
    var label = qualityLabel + " " + (formatSelect ? formatSelect.value.toUpperCase() : "");
    downloadBtn.textContent = "Download " + label;
  }
}

function toggleSubtitleModes(enabled) {
  var soft = document.getElementById("subtitle-embed-soft");
  var hard = document.getElementById("subtitle-embed-hard");
  if (soft) {
    soft.disabled = !enabled;
  }
  if (hard) {
    hard.disabled = !enabled;
  }
}

function handleDownload(event) {
  var downloadBtn = document.getElementById("download-btn");
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!currentJobId) {
    return;
  }
  if (downloadBtn && window.ButtonLoader) {
    if (window.ButtonLoader.isLoading(downloadBtn)) {
      return;
    }
    window.ButtonLoader.start(downloadBtn, "Starting Download...");
  }

  var payload = {
    job_id: currentJobId,
    quality: getSelectedValue("quality-select"),
    format: getSelectedValue("format-select"),
    subtitle_language: getSelectedValue("subtitle-language"),
    subtitle_embed: getChecked("subtitle-embed"),
    subtitle_embed_mode: getRadioValue("subtitle-embed-mode"),
    embed_metadata: getChecked("metadata-toggle")
  };

  fetch(START_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken()
    },
    body: JSON.stringify(payload)
  })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("Download start failed");
      }
      return response.json();
    })
    .then(function () {
      setState("downloading");
    })
    .catch(function () {
      if (downloadBtn && window.ButtonLoader) {
        window.ButtonLoader.stop(downloadBtn, { success: false, error: true, errorText: "Failed to start" });
      }
      showToast("Failed to start download. Please try again.");
    });
}

function handleCancel(event) {
  var cancelButton = event && event.currentTarget ? event.currentTarget : null;
  if (event && event.preventDefault) {
    event.preventDefault();
  }
  if (!currentJobId) {
    return;
  }

  if (cancelButton && window.ButtonLoader) {
    if (window.ButtonLoader.isLoading(cancelButton)) {
      return;
    }
    window.ButtonLoader.start(cancelButton, "Cancelling...");
  }

  fetch(CANCEL_URL + currentJobId, {
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
      if (cancelButton && window.ButtonLoader) {
        return window.ButtonLoader.stop(cancelButton, { success: true, successText: "Cancelled" }).then(function () {
          resetToInput();
        });
      }
      resetToInput();
      return null;
    })
    .catch(function () {
      if (cancelButton && window.ButtonLoader) {
        window.ButtonLoader.stop(cancelButton, { success: false, error: true, errorText: "Error" });
      }
      showToast("Unable to cancel download. Please try again.");
    });
}

function handleDownloadAgain(event) {
  var btn = event && event.currentTarget ? event.currentTarget : null;
  if (event && event.preventDefault) {
    event.preventDefault();
  }

  if (btn && window.ButtonLoader) {
    if (window.ButtonLoader.isLoading(btn)) {
      return;
    }
    window.ButtonLoader.start(btn, "Loading...");
  }

  resetToInput();

  if (btn && window.ButtonLoader) {
    setTimeout(function () {
      if (window.ButtonLoader.isLoading(btn)) {
        window.ButtonLoader.stop(btn, { success: false, error: false });
      }
    }, 1000);
  }
}

function handleTryAnother(event) {
  var btn = event && event.currentTarget ? event.currentTarget : null;
  if (event && event.preventDefault) {
    event.preventDefault();
  }

  if (btn && window.ButtonLoader) {
    if (window.ButtonLoader.isLoading(btn)) {
      return;
    }
    window.ButtonLoader.start(btn, "Resetting...");
  }

  resetToInput();

  if (btn && window.ButtonLoader) {
    window.ButtonLoader.stop(btn, { success: false, error: false });
  }
}

function setState(state, data) {
  var states = ["input", "analyzing", "result", "downloading", "success", "error"];
  states.forEach(function (item) {
    var section = document.querySelector(".state-" + item);
    if (section) {
      section.classList.add("d-none");
    }
  });
  var target = document.querySelector(".state-" + state);
  if (target) {
    target.classList.remove("d-none");
  }
  currentState = state;

  if (state === "success") {
    setText("success-filename", data && data.title ? data.title : "Download ready");
    var downloadLink = document.getElementById("download-link");
    var downloadAgain = document.getElementById("download-again-btn");
    if (downloadLink && data && data.download_url) {
      downloadLink.href = data.download_url;
    }
    if (downloadAgain && data && data.download_url) {
      downloadAgain.href = data.download_url;
    }
  }
  if (state === "error") {
    renderErrorState(data);
  }
}

function resetToInput() {
  if (progressEventSource) {
    progressEventSource.close();
    progressEventSource = null;
  }
  clearAnalyzeButtonFallback();
  var analyzeBtn = document.getElementById("analyze-btn");
  if (analyzeBtn && window.ButtonLoader && window.ButtonLoader.isLoading(analyzeBtn)) {
    window.ButtonLoader.stop(analyzeBtn, { success: false, error: false });
  }
  stopAnalyzeMessages();
  currentJobId = null;
  mediaInfo = null;
  infoLoadAttempts = 0;
  infoLoadStarted = false;
  updateAnalyzeProgress(0);
  var urlInput = document.getElementById("url-input");
  if (urlInput) {
    urlInput.value = "";
  }
  setState("input");
}

function updateProgress(pct, speed_bps, eta_seconds) {
  var safePct = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  var bar = document.getElementById("download-progress-bar");
  if (bar) {
    bar.style.width = safePct + "%";
    bar.setAttribute("aria-valuenow", safePct);
  }
  var text = "Downloading | " + safePct + "% | " + formatSpeed(speed_bps) + " | " + formatETA(eta_seconds);
  setText("status-text", text);
}

function updateAnalyzeProgress(pct) {
  var safePct = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  var bar = document.getElementById("analyze-progress-bar");
  if (bar) {
    bar.style.width = safePct + "%";
    bar.setAttribute("aria-valuenow", safePct);
  }
  setText("analyze-progress-text", safePct + "%");
  setText("analyze-status", analyzeStageMessage(safePct));
}

function analyzeStageMessage(pct) {
  if (pct >= 100) {
    return "Analysis complete";
  }
  if (pct >= 60) {
    return "Extracting media info...";
  }
  if (pct >= 25) {
    return "Validating URL and platform...";
  }
  return "Detecting platform...";
}

function renderErrorState(data) {
  var message = (data && data.error_message) || "Something went wrong.";
  var icon = "bi-exclamation-triangle";
  if (message.toLowerCase().indexOf("drm") !== -1) {
    message = "This content is DRM protected and cannot be downloaded legally.";
    icon = "bi-shield-lock";
  } else if (message.toLowerCase().indexOf("widevine") !== -1 || message.toLowerCase().indexOf("playready") !== -1 || message.toLowerCase().indexOf("fairplay") !== -1) {
    message = "This content is DRM protected and cannot be downloaded legally.";
    icon = "bi-shield-lock";
  } else if (message.toLowerCase().indexOf("login") !== -1 || message.toLowerCase().indexOf("private") !== -1) {
    message = "This content requires login to the platform. Only public content can be downloaded.";
    icon = "bi-lock";
  } else if (message.toLowerCase().indexOf("not supported") !== -1 || message.toLowerCase().indexOf("generic") !== -1) {
    message = "This platform is not yet supported. Request support.";
  } else if (message.toLowerCase().indexOf("geo") !== -1) {
    message = "This content may be geo restricted in your region.";
  } else if (message.toLowerCase().indexOf("timeout") !== -1) {
    message = "Download timed out. The file may be too large or the server is slow. Please try again.";
  }
  setText("error-message", message);
  var iconEl = document.getElementById("error-icon");
  if (iconEl) {
    iconEl.className = "bi " + icon;
  }
  var supportLink = document.getElementById("error-support-link");
  if (supportLink) {
    if (message.toLowerCase().indexOf("not yet supported") !== -1 || message.toLowerCase().indexOf("request support") !== -1) {
      supportLink.href = "/contact?subject=Platform%20support%20request";
      supportLink.classList.remove("d-none");
    } else {
      supportLink.classList.add("d-none");
    }
  }
}

function startAnalyzeMessages() {
  stopAnalyzeMessages();
  updateAnalyzeProgress(5);
}

function stopAnalyzeMessages() {
  if (analyzeInterval) {
    clearInterval(analyzeInterval);
    analyzeInterval = null;
  }
}

function showUrlError(message) {
  var errorEl = document.getElementById("url-error");
  if (errorEl) {
    errorEl.textContent = message || "";
  }
}

function showToast(message) {
  var toast = document.getElementById("download-toast");
  if (!toast) {
    alert(message);
    return;
  }
  toast.querySelector(".toast-body").textContent = message;
  var instance = bootstrap.Toast.getOrCreateInstance(toast);
  instance.show();
}

function setText(id, text) {
  var el = document.getElementById(id);
  if (el) {
    el.textContent = text || "";
  }
}

function setImage(id, src) {
  var el = document.getElementById(id);
  if (el && src) {
    el.src = src;
  }
}

function getSelectedValue(id) {
  var el = document.getElementById(id);
  if (!el) {
    return "";
  }
  return el.value;
}

function getChecked(id) {
  var el = document.getElementById(id);
  return el ? el.checked : false;
}

function getRadioValue(name) {
  var selected = document.querySelector("input[name='" + name + "']:checked");
  return selected ? selected.value : "soft";
}

function formatBytes(bytes) {
  if (!bytes || isNaN(bytes)) {
    return "Unknown";
  }
  var sizes = ["B", "KB", "MB", "GB", "TB"];
  var i = 0;
  var value = bytes;
  while (value >= 1024 && i < sizes.length - 1) {
    value /= 1024;
    i += 1;
  }
  return value.toFixed(1) + " " + sizes[i];
}

function formatDuration(seconds) {
  if (!seconds) {
    return "";
  }
  var sec = parseInt(seconds, 10);
  var hrs = Math.floor(sec / 3600);
  var mins = Math.floor((sec % 3600) / 60);
  var secs = sec % 60;
  if (hrs > 0) {
    return hrs + ":" + String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
  }
  return mins + ":" + String(secs).padStart(2, "0");
}

function formatCount(number) {
  if (!number) {
    return "";
  }
  var num = Number(number);
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1) + "M";
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1) + "K";
  }
  return num.toString();
}

document.addEventListener("DOMContentLoaded", initDownloadPage);
