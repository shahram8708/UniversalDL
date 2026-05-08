const STATES = {
  LOADING: "loading",
  IDLE: "idle",
  ANALYZING: "analyzing",
  RESULT: "result",
  DOWNLOADING: "downloading",
  SUCCESS: "success",
  ERROR: "error",
  DRM: "drm",
  UNSUPPORTED: "unsupported",
  DISCONNECTED: "disconnected"
};

let currentState = STATES.LOADING;
let currentJobId = null;
let currentAnalyzeJobId = null;
let mediaInfo = null;
let currentTabUrl = "";
let currentTabTitle = "";
let currentAnalyzedUrl = "";
let pollInterval = null;
let loadingMessageInterval = null;
let successCloseTimer = null;
let cachedConfig = null;
let activeJobsCache = [];
const downloadedJobIds = new Set();
const IMAGE_FORMATS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp"]);
const AUDIO_FORMATS = new Set(["mp3", "flac", "m4a", "wav"]);
const VIDEO_FORMATS = new Set(["mp4", "mkv", "webm"]);
const ALL_FORMATS = [
  "mp4",
  "mkv",
  "webm",
  "mp3",
  "flac",
  "m4a",
  "wav",
  "jpg",
  "png",
  "webp",
  "gif"
];
let currentAllowedFormats = [...ALL_FORMATS];
const BUTTON_MIN_LOADING_MS = 300;
const buttonLoaderState = new WeakMap();
const activeLoadingButtons = new Set();

const LOADING_MESSAGES = [
  "Detecting platform...",
  "Analyzing media source...",
  "Checking available qualities..."
];

const elements = {};

function startButtonLoading(button, loadingText) {
  if (!button) {
    return false;
  }

  const current = buttonLoaderState.get(button);
  if (current && current.loading) {
    return false;
  }

  const isInput = button.tagName === "INPUT";
  const originalHTML = isInput ? null : button.innerHTML;
  const originalValue = isInput ? button.value : "";
  const originalDisabled = !!button.disabled;
  const originalMinWidth = button.style.minWidth || "";
  const width = button.offsetWidth;

  if (width) {
    button.style.minWidth = `${width}px`;
  }

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.classList.remove("is-success", "is-error");
  button.classList.add("is-loading");

  const explicitText = typeof loadingText === "string" ? loadingText : null;
  const nextText = explicitText !== null ? explicitText : (button.dataset.loadingText || "Loading...");
  if (isInput) {
    button.value = nextText || "Loading...";
  } else {
    button.innerHTML = "";
    const spinner = document.createElement("span");
    spinner.className = "udl-btn-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.appendChild(spinner);
    if (nextText) {
      const textNode = document.createElement("span");
      textNode.textContent = nextText;
      button.appendChild(textNode);
    }
  }

  const state = {
    loading: true,
    startAt: Date.now(),
    isInput,
    originalHTML,
    originalValue,
    originalDisabled,
    originalMinWidth,
    stoppingPromise: null
  };

  buttonLoaderState.set(button, state);
  activeLoadingButtons.add(button);
  return true;
}

function stopButtonLoading(button, options = {}) {
  if (!button) {
    return Promise.resolve();
  }

  const state = buttonLoaderState.get(button);
  if (!state || !state.loading) {
    return Promise.resolve();
  }

  if (state.stoppingPromise) {
    return state.stoppingPromise;
  }

  const elapsed = Date.now() - state.startAt;
  const waitMs = Math.max(0, BUTTON_MIN_LOADING_MS - elapsed);

  state.stoppingPromise = new Promise((resolve) => {
    window.setTimeout(() => {
      const feedbackText = options.success
        ? (options.successText || "Done")
        : (options.error ? (options.errorText || "Failed") : "");

      const applyFeedback = feedbackText && !options.skipFeedback;
      if (applyFeedback) {
        if (state.isInput) {
          button.value = feedbackText;
        } else {
          button.textContent = feedbackText;
        }
        button.classList.remove("is-loading");
        if (options.success) {
          button.classList.add("is-success");
        }
        if (options.error) {
          button.classList.add("is-error");
        }

        window.setTimeout(() => {
          restoreButtonState(button, state);
          resolve();
        }, 450);
        return;
      }

      restoreButtonState(button, state);
      resolve();
    }, waitMs);
  });

  buttonLoaderState.set(button, state);
  return state.stoppingPromise;
}

function restoreButtonState(button, state) {
  button.classList.remove("is-loading", "is-success", "is-error");
  button.removeAttribute("aria-busy");
  button.disabled = state.originalDisabled;

  if (state.isInput) {
    button.value = state.originalValue;
  } else {
    button.innerHTML = state.originalHTML;
  }

  button.style.minWidth = state.originalMinWidth;
  activeLoadingButtons.delete(button);
  buttonLoaderState.delete(button);
}

function stopAllButtonLoading(options) {
  const pending = [];
  activeLoadingButtons.forEach((button) => {
    pending.push(stopButtonLoading(button, options || { skipFeedback: true }));
  });
  return Promise.allSettled(pending);
}

document.addEventListener("DOMContentLoaded", async () => {
  cacheElements();
  bindEventListeners();

  setState(STATES.LOADING, { message: "Detecting platform..." });

  const connection = await checkServerConnection();

  const tabInfo = await sendMessage({ type: "GET_CURRENT_TAB_URL" });
  currentTabUrl = (tabInfo && tabInfo.url) || "";
  currentTabTitle = (tabInfo && tabInfo.title) || "";
  renderCurrentTabInfo();

  const activeJobsResponse = await sendMessage({ type: "GET_ALL_ACTIVE_JOBS" });
  activeJobsCache = (activeJobsResponse && activeJobsResponse.jobs) || [];
  renderActiveJobs(activeJobsCache);

  if (connection.connected) {
    setState(STATES.IDLE);
  } else {
    setState(STATES.DISCONNECTED);
  }
});

function cacheElements() {
  elements.connectionDot = document.getElementById("connectionDot");
  elements.currentPageSection = document.getElementById("currentPageSection");
  elements.customUrlSection = document.getElementById("customUrlSection");
  elements.resultPanel = document.getElementById("resultPanel");
  elements.progressView = document.getElementById("progressView");
  elements.activeJobsSection = document.getElementById("activeJobsSection");

  elements.stateLoading = document.getElementById("stateLoading");
  elements.stateError = document.getElementById("stateError");
  elements.stateDrm = document.getElementById("stateDrm");
  elements.stateUnsupported = document.getElementById("stateUnsupported");
  elements.stateSuccess = document.getElementById("stateSuccess");
  elements.stateDisconnected = document.getElementById("stateDisconnected");

  elements.loadingMessage = document.getElementById("loadingMessage");
  elements.errorMessage = document.getElementById("errorMessage");
  elements.inlineValidationError = document.getElementById("inlineValidationError");

  elements.currentPageUrl = document.getElementById("currentPageUrl");
  elements.pageFavicon = document.getElementById("pageFavicon");
  elements.platformBadge = document.getElementById("platformBadge");

  elements.customUrlInput = document.getElementById("customUrlInput");

  elements.resultThumbnail = document.getElementById("resultThumbnail");
  elements.resultTitle = document.getElementById("resultTitle");
  elements.resultAuthor = document.getElementById("resultAuthor");
  elements.resultPlatformBadge = document.getElementById("resultPlatformBadge");
  elements.resultDurationBadge = document.getElementById("resultDurationBadge");
  elements.qualitySelect = document.getElementById("qualitySelect");
  elements.formatPills = document.getElementById("formatPills");
  elements.startDownloadBtn = document.getElementById("startDownloadBtn");
  elements.subtitleRow = document.getElementById("subtitleRow");
  elements.subtitleToggle = document.getElementById("subtitleToggle");
  elements.subtitleLanguageSelect = document.getElementById("subtitleLanguageSelect");

  elements.progressThumb = document.getElementById("progressThumb");
  elements.progressTitle = document.getElementById("progressTitle");
  elements.progressStatus = document.getElementById("progressStatus");
  elements.progressFill = document.getElementById("progressFill");
  elements.progressPercent = document.getElementById("progressPercent");
  elements.progressSpeed = document.getElementById("progressSpeed");
  elements.progressEta = document.getElementById("progressEta");

  elements.activeJobsList = document.getElementById("activeJobsList");
  elements.successFileInfo = document.getElementById("successFileInfo");
  elements.apiKeyNotice = document.getElementById("apiKeyNotice");
}

function bindEventListeners() {
  document.getElementById("openSettingsBtn").addEventListener("click", () => {
    const button = document.getElementById("openSettingsBtn");
    if (!startButtonLoading(button, "")) {
      return;
    }
    openOptionsPage();
    stopButtonLoading(button, { skipFeedback: true });
  });
  document.getElementById("settingsLink").addEventListener("click", (event) => {
    event.preventDefault();
    openOptionsPage();
  });
  document.getElementById("apiKeyNotice").addEventListener("click", (event) => {
    event.preventDefault();
    openOptionsPage();
  });

  document.getElementById("downloadPageBtn").addEventListener("click", handleDownloadCurrentPage);
  document.getElementById("analyzeBtn").addEventListener("click", handleAnalyzeCustomUrl);
  elements.customUrlInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      handleAnalyzeCustomUrl();
    }
  });

  elements.qualitySelect.addEventListener("change", updateDownloadButtonText);
  elements.formatPills.addEventListener("click", (event) => {
    const target = event.target.closest(".format-pill");
    if (!target) {
      return;
    }
    setActiveFormatPill(target.dataset.format);
    updateDownloadButtonText();
  });

  elements.startDownloadBtn.addEventListener("click", handleStartDownload);
  document.getElementById("cancelDownloadBtn").addEventListener("click", handleCancelDownload);

  document.getElementById("retryBtn").addEventListener("click", async () => {
    const retryBtn = document.getElementById("retryBtn");
    if (!startButtonLoading(retryBtn, "Retrying...")) {
      return;
    }

    if (currentAnalyzedUrl) {
      setState(STATES.ANALYZING, { message: "Retrying analysis..." });
      await analyzeUrl(currentAnalyzedUrl);
    } else {
      setState(STATES.IDLE);
    }
  });

  document.getElementById("openInBrowserBtn").addEventListener("click", () => {
    const button = document.getElementById("openInBrowserBtn");
    if (!startButtonLoading(button, "Opening...")) {
      return;
    }
    const sourceUrl = currentAnalyzedUrl || currentTabUrl || "";
    const target = `https://universaldl.com/download?url=${encodeURIComponent(sourceUrl)}`;
    chrome.tabs.create({ url: target });
    stopButtonLoading(button, { skipFeedback: true });
  });

  document.getElementById("openWebsiteFromDrmBtn").addEventListener("click", () => {
    const button = document.getElementById("openWebsiteFromDrmBtn");
    if (!startButtonLoading(button, "Opening...")) {
      return;
    }
    chrome.tabs.create({ url: "https://universaldl.com" });
    stopButtonLoading(button, { skipFeedback: true });
  });

  document.getElementById("requestSupportLink").addEventListener("click", (event) => {
    event.preventDefault();
    chrome.tabs.create({ url: "https://universaldl.com/contact" });
  });

  document.getElementById("downloadAnotherBtn").addEventListener("click", () => {
    const button = document.getElementById("downloadAnotherBtn");
    if (!startButtonLoading(button, "Preparing...")) {
      return;
    }
    clearSuccessTimer();
    resetAfterSuccess();
  });

  document.getElementById("openDownloadsFolderBtn").addEventListener("click", () => {
    const button = document.getElementById("openDownloadsFolderBtn");
    if (!startButtonLoading(button, "Opening...")) {
      return;
    }
    if (chrome.downloads && chrome.downloads.showDefaultFolder) {
      chrome.downloads.showDefaultFolder();
    }
    stopButtonLoading(button, { skipFeedback: true });
  });

  document.getElementById("checkAgainBtn").addEventListener("click", async () => {
    const button = document.getElementById("checkAgainBtn");
    if (!startButtonLoading(button, "Checking...")) {
      return;
    }

    const connection = await checkServerConnection();
    if (connection.connected) {
      setState(STATES.IDLE);
    } else {
      setState(STATES.DISCONNECTED);
    }
  });

  document.getElementById("configureServerBtn").addEventListener("click", () => {
    const button = document.getElementById("configureServerBtn");
    if (!startButtonLoading(button, "Opening...")) {
      return;
    }
    openOptionsPage();
    stopButtonLoading(button, { skipFeedback: true });
  });
  document.getElementById("openUniversaldlLink").addEventListener("click", (event) => {
    event.preventDefault();
    sendMessage({ type: "OPEN_UNIVERSALDL", path: "" });
  });
}

function openOptionsPage() {
  if (chrome.runtime.openOptionsPage) {
    chrome.runtime.openOptionsPage();
  }
}

function setState(newState, data = {}) {
  currentState = newState;
  hideAllSections();
  stopLoadingMessageCycle();

  if (newState !== STATES.ANALYZING) {
    stopAllButtonLoading({ skipFeedback: true });
  }

  if (successCloseTimer && newState !== STATES.SUCCESS) {
    clearSuccessTimer();
  }

  switch (newState) {
    case STATES.LOADING:
    case STATES.ANALYZING:
      show(elements.stateLoading);
      elements.loadingMessage.textContent = data.message || LOADING_MESSAGES[0];
      startLoadingMessageCycle();
      break;

    case STATES.IDLE:
      show(elements.currentPageSection);
      show(elements.customUrlSection);
      if (activeJobsCache.length > 0) {
        show(elements.activeJobsSection);
      }
      break;

    case STATES.RESULT:
      show(elements.currentPageSection);
      show(elements.customUrlSection);
      show(elements.resultPanel);
      if (activeJobsCache.length > 0) {
        show(elements.activeJobsSection);
      }
      populateResultPanel(data);
      break;

    case STATES.DOWNLOADING:
      show(elements.currentPageSection);
      show(elements.progressView);
      if (activeJobsCache.length > 0) {
        show(elements.activeJobsSection);
      }
      updateProgressView(data);
      break;

    case STATES.SUCCESS:
      show(elements.stateSuccess);
      handleSuccess(data);
      break;

    case STATES.ERROR:
      show(elements.stateError);
      showError(data.message || "Unexpected error occurred");
      break;

    case STATES.DRM:
      show(elements.stateDrm);
      break;

    case STATES.UNSUPPORTED:
      show(elements.stateUnsupported);
      break;

    case STATES.DISCONNECTED:
      show(elements.stateDisconnected);
      break;

    default:
      show(elements.currentPageSection);
      show(elements.customUrlSection);
      break;
  }
}

function hideAllSections() {
  const allSections = [
    elements.currentPageSection,
    elements.customUrlSection,
    elements.resultPanel,
    elements.progressView,
    elements.activeJobsSection,
    elements.stateLoading,
    elements.stateError,
    elements.stateDrm,
    elements.stateUnsupported,
    elements.stateSuccess,
    elements.stateDisconnected
  ];

  allSections.forEach((section) => {
    hide(section);
  });
}

function show(element) {
  if (!element) {
    return;
  }
  element.style.display = "";
}

function hide(element) {
  if (!element) {
    return;
  }
  element.style.display = "none";
}

function startLoadingMessageCycle() {
  stopLoadingMessageCycle();
  let index = 0;
  loadingMessageInterval = window.setInterval(() => {
    index = (index + 1) % LOADING_MESSAGES.length;
    if (elements.loadingMessage) {
      elements.loadingMessage.textContent = LOADING_MESSAGES[index];
    }
  }, 1200);
}

function stopLoadingMessageCycle() {
  if (!loadingMessageInterval) {
    return;
  }
  clearInterval(loadingMessageInterval);
  loadingMessageInterval = null;
}

async function checkServerConnection() {
  try {
    const config = await sendMessage({ type: "GET_CONFIG" });
    cachedConfig = config || {};
    const serverUrl = (cachedConfig.udl_server_url || "http://localhost:5000").replace(/\/+$/, "");

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const response = await fetch(`${serverUrl}/health`, {
      method: "GET",
      headers: {
        "X-Extension-Version": "1.0.0"
      },
      signal: controller.signal,
      credentials: "omit"
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      updateConnectionDot(false);
      return { connected: false };
    }

    const payload = await response.json();
    updateConnectionDot(true);
    return {
      connected: payload.status === "healthy",
      version: payload.app_version || "1.0.0"
    };
  } catch (error) {
    updateConnectionDot(false);
    return { connected: false };
  }
}

function updateConnectionDot(connected) {
  if (!elements.connectionDot) {
    return;
  }
  elements.connectionDot.classList.remove("connected", "disconnected", "checking");
  elements.connectionDot.classList.add(connected ? "connected" : "disconnected");
  elements.connectionDot.title = connected ? "Connected" : "Disconnected";
}

function renderCurrentTabInfo() {
  const visibleUrl = currentTabUrl || "No active page detected";
  elements.currentPageUrl.textContent = visibleUrl;
  elements.currentPageUrl.title = visibleUrl;

  if (currentTabUrl) {
    const faviconUrl = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(currentTabUrl)}&sz=16`;
    elements.pageFavicon.src = faviconUrl;
  } else {
    elements.pageFavicon.src = "../icons/icon16.svg";
  }

  if (elements.customUrlInput && currentTabUrl) {
    elements.customUrlInput.value = currentTabUrl;
  }
}

async function handleDownloadCurrentPage() {
  const triggerButton = document.getElementById("downloadPageBtn");
  if (!startButtonLoading(triggerButton, "Analyzing...")) {
    return;
  }

  if (!currentTabUrl) {
    await stopButtonLoading(triggerButton, { success: false, error: true, errorText: "Invalid URL" });
    setState(STATES.ERROR, { message: "No active tab URL found" });
    return;
  }
  setState(STATES.ANALYZING, { message: "Analyzing current page..." });
  await analyzeUrl(currentTabUrl);
}

async function handleAnalyzeCustomUrl() {
  const triggerButton = document.getElementById("analyzeBtn");
  if (!startButtonLoading(triggerButton, "Analyzing...")) {
    return;
  }

  const url = (elements.customUrlInput.value || "").trim();
  currentAnalyzedUrl = url;

  if (!url.startsWith("http")) {
    elements.inlineValidationError.style.display = "block";
    elements.inlineValidationError.textContent = "Please enter a valid URL starting with http or https";
    await stopButtonLoading(triggerButton, { success: false, error: true, errorText: "Invalid URL" });
    return;
  }

  elements.inlineValidationError.style.display = "none";
  setState(STATES.ANALYZING, { message: "Analyzing URL..." });
  await analyzeUrl(url);
}

async function analyzeUrl(url) {
  clearPolling();
  currentAnalyzedUrl = url;

  const response = await sendMessage({ type: "START_ANALYZE", url, title: currentTabTitle || "Analyze" });
  if (!response || response.error) {
    handleAnalyzeError((response && response.error) || "Unable to start analysis");
    return;
  }

  const jobId = response.jobId;
  currentAnalyzeJobId = jobId;

  pollInterval = window.setInterval(async () => {
    const status = await sendMessage({ type: "GET_JOB_STATUS", jobId });
    if (!status) {
      return;
    }

    const state = String(status.status || "").toLowerCase();

    if (["pending_download", "complete"].includes(state) || Array.isArray(status.qualities)) {
      clearPolling();

      let fetchedMedia = await fetchMediaInfo(jobId);
      const preferredQuality = (cachedConfig && cachedConfig.udl_default_quality) || "best";
      const fallbackQuality = {
        label: preferredQuality === "best" ? "Best Available" : preferredQuality,
        selector: preferredQuality
      };

      if (!fetchedMedia || !Array.isArray(fetchedMedia.qualities) || fetchedMedia.qualities.length === 0) {
        if (state === "complete" && status.download_url) {
          await triggerDownloadIfReady(status);
          setState(STATES.SUCCESS, status);
          return;
        }

        fetchedMedia = {
          title: (status && status.title) || currentTabTitle || "Untitled",
          author: "",
          thumbnail: (status && status.thumbnail_url) || "",
          platform: (status && status.platform) || detectPlatformFromUrl(url),
          duration: null,
          subtitles: [],
          qualities: [fallbackQuality],
          url
        };
      }
      mediaInfo = fetchedMedia;
      setPlatformBadgeText(detectPlatformFromUrl(url));
      setState(STATES.RESULT, mediaInfo);
      return;
    }

    if (state === "failed") {
      clearPolling();
      handleAnalyzeError(status.error_message || "Analysis failed");
      return;
    }

    if (state === "unsupported") {
      clearPolling();
      setState(STATES.UNSUPPORTED);
    }
  }, 1500);
}

async function fetchMediaInfo(jobId) {
  try {
    const apiConfig = await sendMessage({ type: "GET_CONFIG" });
    cachedConfig = apiConfig || cachedConfig || {};
    const serverUrl = (cachedConfig.udl_server_url || "http://localhost:5000").replace(/\/+$/, "");
    const apiKey = (cachedConfig.udl_api_key || "").trim();

    if (!apiKey) {
      const fallbackResponse = await fetch(`${serverUrl}/download/info/${jobId}`, {
        method: "GET",
        headers: {
          "X-Extension-Version": "1.0.0"
        },
        credentials: "omit"
      });

      if (!fallbackResponse.ok) {
        return null;
      }

      const fallbackPayload = await fallbackResponse.json();
      const job = fallbackPayload.job || {};
      const media = fallbackPayload.media_info || {};

      return {
        title: media.title || job.title || "Untitled",
        author: media.author || "",
        thumbnail: media.thumbnail || job.thumbnail_url || "",
        platform: job.platform || detectPlatformFromUrl(currentAnalyzedUrl),
        duration: media.duration || null,
        subtitles: media.subtitles || [],
        qualities: media.qualities || [],
        url: currentAnalyzedUrl
      };
    }

    const apiResponse = await fetch(`${serverUrl}/api/v1/jobs/${jobId}`, {
      method: "GET",
      headers: {
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        "X-Extension-Version": "1.0.0"
      },
      credentials: "omit"
    });

    if (apiResponse.ok) {
      const payload = await apiResponse.json();
      if (Array.isArray(payload.qualities) && payload.qualities.length > 0) {
        return {
          title: payload.title,
          author: payload.author,
          thumbnail: payload.thumbnail_url,
          platform: payload.platform,
          duration: payload.duration,
          subtitles: payload.subtitles || [],
          qualities: payload.qualities,
          url: currentAnalyzedUrl
        };
      }
    }
  } catch {
  }

  try {
    const config = cachedConfig || (await sendMessage({ type: "GET_CONFIG" })) || {};
    const serverUrl = (config.udl_server_url || "http://localhost:5000").replace(/\/+$/, "");

    const fallbackResponse = await fetch(`${serverUrl}/download/info/${jobId}`, {
      method: "GET",
      headers: {
        "X-Extension-Version": "1.0.0"
      },
      credentials: "omit"
    });

    if (!fallbackResponse.ok) {
      return null;
    }

    const fallbackPayload = await fallbackResponse.json();
    const job = fallbackPayload.job || {};
    const media = fallbackPayload.media_info || {};

    return {
      title: media.title || job.title || "Untitled",
      author: media.author || "",
      thumbnail: media.thumbnail || job.thumbnail_url || "",
      platform: job.platform || detectPlatformFromUrl(currentAnalyzedUrl),
      duration: media.duration || null,
      subtitles: media.subtitles || [],
      qualities: media.qualities || [],
      url: currentAnalyzedUrl
    };
  } catch {
    return null;
  }
}

function populateResultPanel(info) {
  const safeInfo = info || {};
  const title = String(safeInfo.title || "Untitled").trim();
  const author = String(safeInfo.author || "Unknown author").trim();

  elements.resultThumbnail.src = safeInfo.thumbnail || "../icons/icon48.svg";
  elements.resultThumbnail.onerror = () => {
    elements.resultThumbnail.src = "../icons/icon48.svg";
  };

  elements.resultTitle.textContent = title.slice(0, 70);
  elements.resultTitle.title = title;
  elements.resultAuthor.textContent = author;

  const platformText = formatPlatformName(safeInfo.platform || detectPlatformFromUrl(currentAnalyzedUrl));
  elements.resultPlatformBadge.textContent = platformText;

  if (safeInfo.duration) {
    elements.resultDurationBadge.style.display = "inline-flex";
    elements.resultDurationBadge.textContent = formatDuration(safeInfo.duration);
  } else {
    elements.resultDurationBadge.style.display = "none";
  }

  elements.qualitySelect.innerHTML = "";
  const qualityList = Array.isArray(safeInfo.qualities) ? safeInfo.qualities : [];

  qualityList.forEach((quality, index) => {
    const value = quality.selector || quality.label || `quality_${index}`;
    const sizePart = quality.size_bytes ? ` · ${formatBytes(quality.size_bytes)}` : "";
    const optionLabel = `${quality.label || "Quality"}${sizePart}`;
    const option = new Option(optionLabel, value);
    option.dataset.qualityIndex = String(index);
    elements.qualitySelect.add(option);
  });

  const preferredQuality = (cachedConfig && cachedConfig.udl_default_quality) || "best";
  applyDefaultQuality(preferredQuality, qualityList);

  currentAllowedFormats = getAllowedFormats(safeInfo);
  updateFormatPills(currentAllowedFormats);

  const preferredFormat = (cachedConfig && cachedConfig.udl_default_format) || "mp4";
  const nextFormat = currentAllowedFormats.includes(preferredFormat)
    ? preferredFormat
    : (currentAllowedFormats[0] || "mp4");
  setActiveFormatPill(nextFormat);

  const subtitles = Array.isArray(safeInfo.subtitles) ? safeInfo.subtitles : [];
  if (subtitles.length > 0) {
    elements.subtitleRow.style.display = "block";
    elements.subtitleLanguageSelect.innerHTML = "";
    subtitles.forEach((subtitle, index) => {
      const value = subtitle.lang || subtitle.label || `sub_${index}`;
      const label = subtitle.label || subtitle.lang || `Subtitle ${index + 1}`;
      elements.subtitleLanguageSelect.add(new Option(label, value));
    });
  } else {
    elements.subtitleRow.style.display = "none";
    elements.subtitleToggle.checked = false;
  }

  updateDownloadButtonText();
}

function applyDefaultQuality(defaultQuality, qualityList) {
  if (!Array.isArray(qualityList) || qualityList.length === 0) {
    return;
  }

  if (defaultQuality === "best") {
    elements.qualitySelect.selectedIndex = 0;
    return;
  }

  const desired = String(defaultQuality).toLowerCase();
  const index = qualityList.findIndex((quality) => {
    const label = String(quality.label || "").toLowerCase();
    return label.includes(desired);
  });

  elements.qualitySelect.selectedIndex = index >= 0 ? index : 0;
}

async function handleStartDownload() {
  const triggerButton = elements.startDownloadBtn;
  if (!startButtonLoading(triggerButton, "Starting...")) {
    return;
  }

  if (!mediaInfo || !currentAnalyzedUrl) {
    await stopButtonLoading(triggerButton, { success: false, error: true, errorText: "Analyze First" });
    setState(STATES.ERROR, { message: "Analyze a URL before downloading" });
    return;
  }

  const selectedOption = elements.qualitySelect.selectedOptions[0];
  const qualityIndex = selectedOption ? Number(selectedOption.dataset.qualityIndex || 0) : 0;
  const selectedQuality = (mediaInfo.qualities && mediaInfo.qualities[qualityIndex]) || mediaInfo.qualities[0];
  const quality = (selectedQuality && (selectedQuality.selector || selectedQuality.label)) || "best";
  const format = getSelectedFormat();

  const subtitle_language = elements.subtitleRow.style.display !== "none" && elements.subtitleToggle.checked
    ? elements.subtitleLanguageSelect.value
    : null;

  const response = await sendMessage({
    type: "START_DOWNLOAD",
    url: currentAnalyzedUrl,
    quality,
    format,
    analyzeJobId: currentAnalyzeJobId,
    subtitle_language,
    subtitle_embed: Boolean(subtitle_language)
  });

  if (!response || response.error) {
    await stopButtonLoading(triggerButton, { success: false, error: true, errorText: "Failed" });
    setState(STATES.ERROR, { message: (response && response.error) || "Unable to start download" });
    return;
  }

  await stopButtonLoading(triggerButton, { skipFeedback: true });
  currentJobId = response.jobId;
  setState(STATES.DOWNLOADING, {
    title: mediaInfo.title,
    thumbnail_url: mediaInfo.thumbnail,
    progress_pct: 0,
    speed_bps: 0,
    eta_seconds: 0,
    status: "downloading"
  });

  clearPolling();
  pollInterval = window.setInterval(async () => {
    const status = await sendMessage({ type: "GET_JOB_STATUS", jobId: currentJobId });
    if (!status) {
      return;
    }

    updateProgressView(status);

    const normalizedStatus = String(status.status || "").toLowerCase();
    if (normalizedStatus === "complete") {
      clearPolling();
      await triggerDownloadIfReady(status);
      setState(STATES.SUCCESS, status);
      return;
    }

    if (normalizedStatus === "failed") {
      clearPolling();
      setState(STATES.ERROR, { message: status.error_message || "Download failed" });
      return;
    }
  }, 1500);
}

function updateProgressView(jobData) {
  if (!jobData) {
    return;
  }

  const pct = Number(jobData.progress_pct || 0);
  elements.progressFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  elements.progressStatus.textContent = buildStatusText(jobData);
  elements.progressPercent.textContent = `${Math.round(pct)}%`;
  elements.progressSpeed.textContent = formatSpeed(jobData.speed_bps || 0);
  elements.progressEta.textContent = formatETA(jobData.eta_seconds || 0);

  if (jobData.thumbnail_url) {
    elements.progressThumb.src = jobData.thumbnail_url;
    elements.progressThumb.onerror = () => {
      elements.progressThumb.src = "../icons/icon48.svg";
    };
  }

  elements.progressTitle.textContent = String(jobData.title || mediaInfo?.title || "Downloading...").slice(0, 60);
}

async function handleCancelDownload() {
  if (!currentJobId) {
    return;
  }

  const triggerButton = document.getElementById("cancelDownloadBtn");
  if (!startButtonLoading(triggerButton, "Cancelling...")) {
    return;
  }

  try {
    const config = cachedConfig || (await sendMessage({ type: "GET_CONFIG" })) || {};
    const serverUrl = (config.udl_server_url || "http://localhost:5000").replace(/\/+$/, "");

    await fetch(`${serverUrl}/download/cancel/${currentJobId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Version": "1.0.0"
      },
      credentials: "omit"
    });
  } catch {
  }

  clearPolling();
  currentJobId = null;
  setState(STATES.IDLE);
}

function handleSuccess(jobData) {
  const title = String(jobData.title || mediaInfo?.title || "Downloaded media");
  const size = jobData.file_size_bytes ? formatBytes(jobData.file_size_bytes) : "Size unknown";
  elements.successFileInfo.textContent = `${title.slice(0, 80)} · ${size}`;

  startSuccessTimer();
}

function startSuccessTimer() {
  clearSuccessTimer();
  successCloseTimer = window.setTimeout(() => {
    window.close();
  }, 8000);
}

function clearSuccessTimer() {
  if (!successCloseTimer) {
    return;
  }
  clearTimeout(successCloseTimer);
  successCloseTimer = null;
}

function resetAfterSuccess() {
  currentJobId = null;
  currentAnalyzeJobId = null;
  mediaInfo = null;
  setState(STATES.IDLE);
}

function showError(message) {
  elements.errorMessage.textContent = message;
}

function handleAnalyzeError(message) {
  const safeMessage = String(message || "Analysis failed");
  const lowered = safeMessage.toLowerCase();

  if (lowered.includes("drm") || lowered.includes("widevine")) {
    setState(STATES.DRM);
    return;
  }

  if (lowered.includes("not supported") || lowered.includes("unsupported")) {
    setState(STATES.UNSUPPORTED);
    return;
  }

  if (lowered.includes("failed to fetch") || lowered.includes("unable to reach") || lowered.includes("server")) {
    setState(STATES.DISCONNECTED);
    return;
  }

  setState(STATES.ERROR, { message: safeMessage });
}

function renderActiveJobs(jobs) {
  const normalizedJobs = Array.isArray(jobs) ? jobs : [];
  activeJobsCache = normalizedJobs;
  elements.activeJobsList.innerHTML = "";

  if (normalizedJobs.length === 0) {
    hide(elements.activeJobsSection);
    return;
  }

  show(elements.activeJobsSection);

  normalizedJobs.forEach((job) => {
    const item = document.createElement("div");
    item.className = "job-item";

    const thumb = document.createElement("img");
    thumb.className = "job-thumb";
    thumb.src = job.thumbnail_url || "../icons/icon32.svg";
    thumb.alt = "";
    thumb.onerror = () => {
      thumb.src = "../icons/icon32.svg";
    };

    const info = document.createElement("div");
    info.className = "job-info";

    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = String(job.title || "Untitled").slice(0, 50);

    const status = document.createElement("div");
    status.className = "job-status";
    status.textContent = buildStatusText(job);

    const miniTrack = document.createElement("div");
    miniTrack.className = "job-progress-mini";

    const miniFill = document.createElement("div");
    miniFill.className = "job-progress-fill";
    miniFill.style.width = `${Math.max(0, Math.min(100, Number(job.progress_pct || 0)))}%`;

    miniTrack.appendChild(miniFill);
    info.appendChild(title);
    info.appendChild(status);
    info.appendChild(miniTrack);

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "job-cancel-btn";
    cancelBtn.type = "button";
    cancelBtn.textContent = "X";
    cancelBtn.title = "Cancel download";
    cancelBtn.addEventListener("click", () => {
      cancelSpecificJob(job.jobId || job.job_id, cancelBtn);
    });

    item.appendChild(thumb);
    item.appendChild(info);
    item.appendChild(cancelBtn);

    elements.activeJobsList.appendChild(item);
  });
}

async function cancelSpecificJob(jobId, triggerButton) {
  if (!jobId) {
    return;
  }

  if (triggerButton && !startButtonLoading(triggerButton, "")) {
    mediaInfo = {
      title: jobData.title || "Downloaded media",
      author: "",
      thumbnail: jobData.thumbnail_url || "",
      platform: jobData.platform || detectPlatformFromUrl(currentAnalyzedUrl),
      duration: null,
      subtitles: [],
      qualities: [],
      url: currentAnalyzedUrl
    };
    return;
  }

  try {
    const config = cachedConfig || (await sendMessage({ type: "GET_CONFIG" })) || {};
    const serverUrl = (config.udl_server_url || "http://localhost:5000").replace(/\/+$/, "");

    await fetch(`${serverUrl}/download/cancel/${jobId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Version": "1.0.0"
      },
      credentials: "omit"
    });

    const updated = await sendMessage({ type: "GET_ALL_ACTIVE_JOBS" });
    renderActiveJobs((updated && updated.jobs) || []);
    if (triggerButton) {
      await stopButtonLoading(triggerButton, { success: true, successText: "Cancelled" });
    }
  } catch {
    if (triggerButton) {
      await stopButtonLoading(triggerButton, { success: false, error: true, errorText: "Failed" });
    }
  }
}

function setActiveFormatPill(format) {
  const selected = String(format || "mp4").toLowerCase();
  const pills = elements.formatPills.querySelectorAll(".format-pill");
  pills.forEach((pill) => {
    const isActive = pill.dataset.format === selected;
    pill.classList.toggle("active", isActive);
  });
}

function updateDownloadButtonText() {
  const qualityText = elements.qualitySelect.selectedOptions[0]
    ? elements.qualitySelect.selectedOptions[0].text.split(" · ")[0]
    : "selected quality";
  const formatText = getSelectedFormat().toUpperCase();
  elements.startDownloadBtn.textContent = `Download ${qualityText} ${formatText}`;
}

function getSelectedFormat() {
  const active = elements.formatPills.querySelector(".format-pill.active");
  return active ? active.dataset.format : "mp4";
}

function updateFormatPills(allowedFormats) {
  const allowed = new Set((allowedFormats || []).map((item) => String(item).toLowerCase()));
  const pills = elements.formatPills.querySelectorAll(".format-pill");
  pills.forEach((pill) => {
    const format = String(pill.dataset.format || "").toLowerCase();
    pill.style.display = allowed.has(format) ? "" : "none";
    if (!allowed.has(format)) {
      pill.classList.remove("active");
    }
  });
}

function isImageQuality(quality) {
  if (!quality) {
    return false;
  }
  const format = String(quality.format || "").toLowerCase();
  if (IMAGE_FORMATS.has(format)) {
    return true;
  }
  const label = String(quality.label || "").toLowerCase();
  if (label.startsWith("image")) {
    return true;
  }
  const url = String(quality.url || "").toLowerCase();
  return url.endsWith(".jpg") || url.endsWith(".jpeg") || url.endsWith(".png") || url.endsWith(".gif") || url.endsWith(".webp") || url.endsWith(".bmp");
}

function isAudioQuality(quality) {
  if (!quality) {
    return false;
  }
  const format = String(quality.format || "").toLowerCase();
  if (AUDIO_FORMATS.has(format)) {
    return true;
  }
  const label = String(quality.label || "").toLowerCase();
  if (label.includes("audio")) {
    return true;
  }
  const url = String(quality.url || "").toLowerCase();
  return url.endsWith(".mp3") || url.endsWith(".m4a") || url.endsWith(".flac") || url.endsWith(".wav");
}

function getAllowedFormats(mediaInfoValue) {
  const qualities = Array.isArray(mediaInfoValue && mediaInfoValue.qualities) ? mediaInfoValue.qualities : [];
  if (qualities.length === 0) {
    return [...ALL_FORMATS];
  }

  const allImages = qualities.every((quality) => isImageQuality(quality));
  if (allImages) {
    return ["jpg", "png", "webp", "gif"];
  }

  const allAudio = qualities.every((quality) => isAudioQuality(quality));
  if (allAudio) {
    return ["mp3", "flac", "m4a", "wav"];
  }

  return ["mp4", "mkv", "webm", "mp3", "flac", "m4a", "wav"];
}

async function triggerDownloadIfReady(jobData) {
  if (!jobData || !jobData.download_url || !chrome.downloads || !chrome.downloads.download) {
    return;
  }

  const jobId = jobData.jobId || jobData.job_id || currentJobId;
  if (jobId && downloadedJobIds.has(jobId)) {
    return;
  }

  try {
    const downloadUrl = await resolveDownloadUrl(jobData.download_url);
    await downloadWithChrome(downloadUrl);
    if (jobId) {
      downloadedJobIds.add(jobId);
    }
  } catch {
  }
}

async function resolveDownloadUrl(rawUrl) {
  const value = String(rawUrl || "").trim();
  if (!value) {
    return "";
  }

  if (value.startsWith("http://") || value.startsWith("https://")) {
    return value;
  }

  const config = cachedConfig || (await sendMessage({ type: "GET_CONFIG" })) || {};
  cachedConfig = config || cachedConfig || {};
  const serverUrl = String((cachedConfig.udl_server_url || "http://localhost:5000")).replace(/\/+$/, "");
  const path = value.startsWith("/") ? value : `/${value}`;
  return `${serverUrl}${path}`;
}

function downloadWithChrome(url) {
  return new Promise((resolve, reject) => {
    chrome.downloads.download(
      {
        url,
        conflictAction: "uniquify",
        saveAs: false
      },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message || "Download failed"));
          return;
        }
        if (!downloadId && downloadId !== 0) {
          reject(new Error("Download did not start"));
          return;
        }
        resolve(downloadId);
      }
    );
  });
}

function buildStatusText(jobData) {
  const pct = Math.round(Number(jobData.progress_pct || 0));
  const speed = formatSpeed(jobData.speed_bps || 0);
  const eta = formatETA(jobData.eta_seconds || 0);
  return `Downloading · ${pct}% · ${speed} · ${eta}`;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) {
    return "0 B";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  if (unitIndex === 0) {
    return `${Math.round(size)} ${units[unitIndex]}`;
  }

  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

function formatSpeed(bps) {
  const speed = Number(bps || 0);
  if (!speed) {
    return "0 KB/s";
  }
  return `${formatBytes(speed)}/s`;
}

function formatETA(seconds) {
  const value = Number(seconds || 0);
  if (!value || value < 1) {
    return "calculating";
  }
  if (value < 60) {
    return `${Math.round(value)}s`;
  }

  const mins = Math.floor(value / 60);
  const secs = Math.round(value % 60);
  if (mins < 60) {
    return `${mins}m ${secs}s`;
  }

  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

function formatDuration(durationSeconds) {
  const value = Number(durationSeconds || 0);
  if (!value) {
    return "";
  }

  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function detectPlatformFromUrl(url) {
  const source = String(url || "").toLowerCase();
  if (source.includes("youtube.com") || source.includes("youtu.be")) {
    return "youtube";
  }
  if (source.includes("tiktok.com")) {
    return "tiktok";
  }
  if (source.includes("instagram.com")) {
    return "instagram";
  }
  if (source.includes("twitter.com") || source.includes("x.com")) {
    return "twitter";
  }
  if (source.includes("reddit.com")) {
    return "reddit";
  }
  if (source.includes("twitch.tv")) {
    return "twitch";
  }
  if (source.includes("vimeo.com")) {
    return "vimeo";
  }
  if (source.includes("soundcloud.com")) {
    return "soundcloud";
  }
  if (source.includes("bilibili.com")) {
    return "bilibili";
  }
  if (source.includes("facebook.com")) {
    return "facebook";
  }
  if (source.includes("dailymotion.com")) {
    return "dailymotion";
  }
  if (source.includes("behance.net")) {
    return "behance";
  }
  if (source.includes("imgur.com")) {
    return "imgur";
  }
  if (source.includes("coursera.org")) {
    return "coursera";
  }
  if (source.includes("spotify.com") || source.includes("anchor.fm")) {
    return "audio";
  }
  if (source.includes("netflix.com")) {
    return "netflix";
  }
  return "media";
}

function formatPlatformName(platform) {
  const value = String(platform || "media").toLowerCase();
  const mapping = {
    youtube: "YouTube",
    tiktok: "TikTok",
    instagram: "Instagram",
    twitter: "Twitter/X",
    reddit: "Reddit",
    twitch: "Twitch",
    vimeo: "Vimeo",
    soundcloud: "SoundCloud",
    bilibili: "Bilibili",
    facebook: "Facebook",
    dailymotion: "Dailymotion",
    behance: "Behance",
    imgur: "Imgur",
    coursera: "Coursera",
    netflix: "Netflix",
    audio: "Podcast"
  };
  return mapping[value] || "Media";
}

function setPlatformBadgeText(platform) {
  if (!platform) {
    elements.platformBadge.style.display = "none";
    return;
  }
  elements.platformBadge.style.display = "inline-flex";
  elements.platformBadge.textContent = formatPlatformName(platform);
}

function clearPolling() {
  if (!pollInterval) {
    return;
  }
  clearInterval(pollInterval);
  pollInterval = null;
}

async function sendMessage(data) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(data, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ error: chrome.runtime.lastError.message });
          return;
        }
        resolve(response);
      });
    } catch (error) {
      resolve({ error: error.message || "Extension disconnected" });
    }
  });
}

