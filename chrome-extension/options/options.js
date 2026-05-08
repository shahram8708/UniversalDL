const CONFIG_KEYS = {
  SERVER_URL: "udl_server_url",
  API_KEY: "udl_api_key",
  DEFAULT_QUALITY: "udl_default_quality",
  DEFAULT_FORMAT: "udl_default_format",
  AUTO_ANALYZE: "udl_auto_analyze",
  NOTIFICATION_ON_COMPLETE: "udl_notify_complete",
  BADGE_ACTIVE_JOBS: "udl_badge_jobs",
  SHOW_FLOATING_BUTTON: "udl_show_floating_button",
  SUBTITLE_PREF: "udl_subtitle_pref",
  EMBED_METADATA: "udl_embed_metadata"
};

const DEFAULT_CONFIG = {
  [CONFIG_KEYS.SERVER_URL]: "http://localhost:5000",
  [CONFIG_KEYS.API_KEY]: "",
  [CONFIG_KEYS.DEFAULT_QUALITY]: "best",
  [CONFIG_KEYS.DEFAULT_FORMAT]: "mp4",
  [CONFIG_KEYS.AUTO_ANALYZE]: true,
  [CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]: true,
  [CONFIG_KEYS.BADGE_ACTIVE_JOBS]: true,
  [CONFIG_KEYS.SHOW_FLOATING_BUTTON]: true,
  [CONFIG_KEYS.SUBTITLE_PREF]: "download_if_available",
  [CONFIG_KEYS.EMBED_METADATA]: true
};

const ui = {};
let saveFlashTimer = null;
const OPTIONS_BUTTON_MIN_MS = 300;
const optionsButtonState = new WeakMap();

function startOptionsButtonLoading(button, loadingText) {
  if (!button) {
    return false;
  }

  const state = optionsButtonState.get(button);
  if (state && state.loading) {
    return false;
  }

  const originalText = button.textContent;
  const originalDisabled = !!button.disabled;
  const originalMinWidth = button.style.minWidth || "";
  const width = button.offsetWidth;

  if (width) {
    button.style.minWidth = `${width}px`;
  }

  button.disabled = true;
  button.classList.add("is-loading");
  button.classList.remove("is-success", "is-error");
  button.innerHTML = "";

  const spinner = document.createElement("span");
  spinner.className = "udl-option-spinner";
  spinner.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = loadingText || "Loading...";

  button.appendChild(spinner);
  button.appendChild(label);

  optionsButtonState.set(button, {
    loading: true,
    startAt: Date.now(),
    originalText,
    originalDisabled,
    originalMinWidth,
    stoppingPromise: null
  });

  return true;
}

function stopOptionsButtonLoading(button, options = {}) {
  if (!button) {
    return Promise.resolve();
  }

  const state = optionsButtonState.get(button);
  if (!state || !state.loading) {
    return Promise.resolve();
  }

  if (state.stoppingPromise) {
    return state.stoppingPromise;
  }

  const waitMs = Math.max(0, OPTIONS_BUTTON_MIN_MS - (Date.now() - state.startAt));

  state.stoppingPromise = new Promise((resolve) => {
    window.setTimeout(() => {
      const feedbackText = options.success
        ? (options.successText || "Saved")
        : (options.error ? (options.errorText || "Failed") : "");

      if (feedbackText && !options.skipFeedback) {
        button.classList.remove("is-loading");
        if (options.success) {
          button.classList.add("is-success");
        }
        if (options.error) {
          button.classList.add("is-error");
        }
        button.textContent = feedbackText;

        window.setTimeout(() => {
          restoreOptionsButton(button, state);
          resolve();
        }, 450);
        return;
      }

      restoreOptionsButton(button, state);
      resolve();
    }, waitMs);
  });

  optionsButtonState.set(button, state);
  return state.stoppingPromise;
}

function restoreOptionsButton(button, state) {
  button.classList.remove("is-loading", "is-success", "is-error");
  button.textContent = state.originalText;
  button.disabled = state.originalDisabled;
  button.style.minWidth = state.originalMinWidth;
  optionsButtonState.delete(button);
}

function setToggleInputsDisabled(disabled) {
  const toggles = [
    ui.autoAnalyze,
    ui.showFloatingButton,
    ui.notifyComplete,
    ui.badgeJobs,
    ui.embedMetadata
  ];

  toggles.forEach((toggle) => {
    if (!toggle) {
      return;
    }
    toggle.disabled = disabled;
    const row = toggle.closest(".toggle-row");
    if (row) {
      row.classList.toggle("is-disabled", disabled);
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  cacheElements();
  bindEventListeners();
  await loadSettings();
  await testConnection();
});

function cacheElements() {
  ui.serverUrl = document.getElementById("serverUrl");
  ui.apiKey = document.getElementById("apiKey");

  ui.defaultQuality = document.getElementById("defaultQuality");
  ui.defaultFormatPills = document.getElementById("defaultFormatPills");
  ui.subtitlePreference = document.getElementById("subtitlePreference");

  ui.autoAnalyze = document.getElementById("autoAnalyze");
  ui.showFloatingButton = document.getElementById("showFloatingButton");
  ui.notifyComplete = document.getElementById("notifyComplete");
  ui.badgeJobs = document.getElementById("badgeJobs");
  ui.embedMetadata = document.getElementById("embedMetadata");

  ui.testConnectionBtn = document.getElementById("testConnectionBtn");
  ui.connectionStatusDot = document.getElementById("connectionStatusDot");
  ui.connectionStatusText = document.getElementById("connectionStatusText");
  ui.planStatus = document.getElementById("planStatus");

  ui.toggleApiKeyVisibility = document.getElementById("toggleApiKeyVisibility");
  ui.saveApiKeyBtn = document.getElementById("saveApiKeyBtn");

  ui.saveStatus = document.getElementById("saveStatus");
  ui.shortcutLink = document.getElementById("shortcutLink");
  ui.openPopupBtn = document.getElementById("openPopupBtn");
  ui.quickDownloadBtn = document.getElementById("quickDownloadBtn");
  ui.openWebsiteBtn = document.getElementById("openWebsiteBtn");
}

async function loadSettings() {
  const settings = await chrome.storage.sync.get(DEFAULT_CONFIG);

  ui.serverUrl.value = settings[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
  ui.apiKey.value = settings[CONFIG_KEYS.API_KEY] || "";

  ui.defaultQuality.value = settings[CONFIG_KEYS.DEFAULT_QUALITY] || "best";
  setActiveFormatPill(settings[CONFIG_KEYS.DEFAULT_FORMAT] || "mp4");
  ui.subtitlePreference.value = settings[CONFIG_KEYS.SUBTITLE_PREF] || "download_if_available";

  ui.autoAnalyze.checked = Boolean(settings[CONFIG_KEYS.AUTO_ANALYZE]);
  ui.showFloatingButton.checked = Boolean(settings[CONFIG_KEYS.SHOW_FLOATING_BUTTON]);
  ui.notifyComplete.checked = Boolean(settings[CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]);
  ui.badgeJobs.checked = Boolean(settings[CONFIG_KEYS.BADGE_ACTIVE_JOBS]);
  ui.embedMetadata.checked = Boolean(settings[CONFIG_KEYS.EMBED_METADATA]);
}

async function saveSettings() {
  const payload = collectFormValues();
  await chrome.storage.sync.set(payload);

  const updates = Object.entries(payload).map(([key, value]) => sendMessage({ type: "SET_CONFIG", key, value }));
  await Promise.allSettled(updates);

  autoSaveWithFeedback();
}

function collectFormValues() {
  return {
    [CONFIG_KEYS.SERVER_URL]: normalizeServerUrl(ui.serverUrl.value),
    [CONFIG_KEYS.API_KEY]: ui.apiKey.value.trim(),
    [CONFIG_KEYS.DEFAULT_QUALITY]: ui.defaultQuality.value,
    [CONFIG_KEYS.DEFAULT_FORMAT]: getSelectedFormat(),
    [CONFIG_KEYS.SUBTITLE_PREF]: ui.subtitlePreference.value,
    [CONFIG_KEYS.AUTO_ANALYZE]: ui.autoAnalyze.checked,
    [CONFIG_KEYS.SHOW_FLOATING_BUTTON]: ui.showFloatingButton.checked,
    [CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]: ui.notifyComplete.checked,
    [CONFIG_KEYS.BADGE_ACTIVE_JOBS]: ui.badgeJobs.checked,
    [CONFIG_KEYS.EMBED_METADATA]: ui.embedMetadata.checked
  };
}

async function testConnection() {
  const serverUrl = normalizeServerUrl(ui.serverUrl.value || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL]);
  const apiKey = ui.apiKey.value.trim();

  updateConnectionStatus("checking", "Checking connection...");

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000);

  try {
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
      updateConnectionStatus("failed", `Connection failed (${response.status})`);
      ui.planStatus.textContent = "Unable to verify plan status";
      return false;
    }

    const payload = await response.json();
    const version = payload.app_version || "1.0.0";
    updateConnectionStatus("connected", `Connected — UniversalDL v${version}`);

    if (!apiKey) {
      ui.planStatus.textContent = "✓ Free Plan";
      return true;
    }

    const planCheck = await fetch(`${serverUrl}/api/v1/platforms`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "X-Extension-Version": "1.0.0"
      },
      credentials: "omit"
    });

    if (planCheck.ok) {
      ui.planStatus.textContent = "⭐ Pro Plan";
      return true;
    }

    if (planCheck.status === 401) {
      updateConnectionStatus("failed", "Connected, but API key is invalid");
      ui.planStatus.textContent = "Invalid API key";
      return false;
    }

    ui.planStatus.textContent = "✓ Free Plan";
    return true;
  } catch (error) {
    clearTimeout(timeoutId);
    const mapped = mapConnectionError(error);
    updateConnectionStatus("failed", mapped);
    ui.planStatus.textContent = "Connection failed";
    return false;
  }
}

function mapConnectionError(error) {
  const message = String((error && error.message) || "Unknown error");

  if (error && error.name === "AbortError") {
    return "Server is too slow to respond";
  }

  if (message.includes("Failed to fetch")) {
    return "Server not running. Start with: python run.py";
  }

  if (message.includes("401")) {
    return "Invalid API key";
  }

  if (message.toLowerCase().includes("cors")) {
    return "CORS error — check server is running correctly";
  }

  return "Connection failed";
}

function updateConnectionStatus(kind, text) {
  ui.connectionStatusDot.classList.remove("connected", "failed", "checking");

  if (kind === "connected") {
    ui.connectionStatusDot.classList.add("connected");
  } else if (kind === "failed") {
    ui.connectionStatusDot.classList.add("failed");
  } else {
    ui.connectionStatusDot.classList.add("checking");
  }

  ui.connectionStatusText.textContent = text;
}

function bindEventListeners() {
  const saveOnChangeElements = [
    ui.serverUrl,
    ui.defaultQuality,
    ui.subtitlePreference,
    ui.autoAnalyze,
    ui.showFloatingButton,
    ui.notifyComplete,
    ui.badgeJobs,
    ui.embedMetadata,
    ui.apiKey
  ];

  saveOnChangeElements.forEach((element) => {
    element.addEventListener("change", saveSettings);
  });

  ui.defaultFormatPills.addEventListener("click", async (event) => {
    const target = event.target.closest(".format-pill");
    if (!target) {
      return;
    }
    setActiveFormatPill(target.dataset.format);
    await saveSettings();
  });

  ui.testConnectionBtn.addEventListener("click", async () => {
    if (!startOptionsButtonLoading(ui.testConnectionBtn, "Testing...")) {
      return;
    }

    setToggleInputsDisabled(true);
    try {
      await saveSettings();
      const connected = await testConnection();
      await stopOptionsButtonLoading(
        ui.testConnectionBtn,
        connected
          ? { success: true, successText: "Connected" }
          : { success: false, error: true, errorText: "Failed" }
      );
    } catch {
      await stopOptionsButtonLoading(ui.testConnectionBtn, { success: false, error: true, errorText: "Failed" });
    } finally {
      setToggleInputsDisabled(false);
    }
  });

  ui.saveApiKeyBtn.addEventListener("click", async () => {
    if (!startOptionsButtonLoading(ui.saveApiKeyBtn, "Saving...")) {
      return;
    }

    setToggleInputsDisabled(true);
    try {
      await saveSettings();
      const connected = await testConnection();
      await stopOptionsButtonLoading(
        ui.saveApiKeyBtn,
        connected
          ? { success: true, successText: "Saved!" }
          : { success: false, error: true, errorText: "Check Server" }
      );
    } catch {
      await stopOptionsButtonLoading(ui.saveApiKeyBtn, { success: false, error: true, errorText: "Failed" });
    } finally {
      setToggleInputsDisabled(false);
    }
  });

  ui.toggleApiKeyVisibility.addEventListener("click", toggleApiKeyVisibility);

  ui.shortcutLink.addEventListener("click", (event) => {
    event.preventDefault();
    chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
  });

  ui.openPopupBtn.addEventListener("click", async () => {
    if (!startOptionsButtonLoading(ui.openPopupBtn, "Opening...")) {
      return;
    }

    try {
      const response = await sendMessage({ type: "OPEN_POPUP" });
      await stopOptionsButtonLoading(
        ui.openPopupBtn,
        response && !response.error
          ? { success: true, successText: "Opened" }
          : { success: false, error: true, errorText: "Failed" }
      );
    } catch {
      await stopOptionsButtonLoading(ui.openPopupBtn, { success: false, error: true, errorText: "Failed" });
    }
  });

  ui.quickDownloadBtn.addEventListener("click", async () => {
    if (!startOptionsButtonLoading(ui.quickDownloadBtn, "Starting...")) {
      return;
    }

    try {
      const response = await sendMessage({ type: "QUICK_DOWNLOAD_CURRENT_PAGE" });
      const failureText = String((response && response.error) || "").trim();
      await stopOptionsButtonLoading(
        ui.quickDownloadBtn,
        response && !response.error
          ? { success: true, successText: "Started" }
          : { success: false, error: true, errorText: failureText || "Open a supported webpage first" }
      );
    } catch {
      await stopOptionsButtonLoading(ui.quickDownloadBtn, {
        success: false,
        error: true,
        errorText: "Open a supported webpage first"
      });
    }
  });

  ui.openWebsiteBtn.addEventListener("click", () => {
    chrome.tabs.create({ url: "https://universaldl.onrender.com" });
  });
}

function toggleApiKeyVisibility() {
  const isPassword = ui.apiKey.type === "password";
  ui.apiKey.type = isPassword ? "text" : "password";
  ui.toggleApiKeyVisibility.textContent = isPassword ? "Hide" : "Show";
}

function setActiveFormatPill(format) {
  const selected = String(format || "mp4").toLowerCase();
  const pills = ui.defaultFormatPills.querySelectorAll(".format-pill");
  pills.forEach((pill) => {
    pill.classList.toggle("active", pill.dataset.format === selected);
  });
}

function getSelectedFormat() {
  const active = ui.defaultFormatPills.querySelector(".format-pill.active");
  return active ? active.dataset.format : "mp4";
}

function normalizeServerUrl(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
  }
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed.replace(/\/+$/, "");
  }
  return `http://${trimmed}`.replace(/\/+$/, "");
}

function autoSaveWithFeedback() {
  if (saveFlashTimer) {
    clearTimeout(saveFlashTimer);
  }

  ui.saveStatus.textContent = "✓ Settings saved";
  ui.saveStatus.classList.add("saved-flash");

  saveFlashTimer = setTimeout(() => {
    ui.saveStatus.textContent = "Settings saved automatically";
    ui.saveStatus.classList.remove("saved-flash");
  }, 2000);
}

async function sendMessage(payload) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(payload, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ error: chrome.runtime.lastError.message });
          return;
        }
        resolve(response || {});
      });
    } catch (error) {
      resolve({ error: error.message || "Extension message failed" });
    }
  });
}
