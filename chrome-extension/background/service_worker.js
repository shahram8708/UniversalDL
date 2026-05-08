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
  [CONFIG_KEYS.DEFAULT_QUALITY]: "best",
  [CONFIG_KEYS.DEFAULT_FORMAT]: "mp4",
  [CONFIG_KEYS.AUTO_ANALYZE]: true,
  [CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]: true,
  [CONFIG_KEYS.BADGE_ACTIVE_JOBS]: true,
  [CONFIG_KEYS.SHOW_FLOATING_BUTTON]: true,
  [CONFIG_KEYS.SUBTITLE_PREF]: "download_if_available",
  [CONFIG_KEYS.EMBED_METADATA]: true
};

const EXTENSION_VERSION = "1.0.0";
const RECENT_JOBS_KEY = "udl_recent_jobs";
const ACTIVE_JOBS_KEY = "udl_active_jobs_cache";
const NOTIFICATION_ICON_FALLBACK = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
let notificationIconUrlPromise = null;

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  const chunkSize = 8192;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

async function buildNotificationIconUrl() {
  try {
    if (typeof OffscreenCanvas === "undefined") {
      return NOTIFICATION_ICON_FALLBACK;
    }

    const svgUrl = chrome.runtime.getURL("icons/icon128.svg");
    const response = await fetch(svgUrl);
    if (!response.ok) {
      return NOTIFICATION_ICON_FALLBACK;
    }

    const svgBlob = await response.blob();
    const bitmap = await createImageBitmap(svgBlob);
    const canvas = new OffscreenCanvas(128, 128);
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return NOTIFICATION_ICON_FALLBACK;
    }

    ctx.clearRect(0, 0, 128, 128);
    ctx.drawImage(bitmap, 0, 0, 128, 128);
    if (typeof bitmap.close === "function") {
      bitmap.close();
    }

    const pngBlob = await canvas.convertToBlob({ type: "image/png" });
    const base64 = arrayBufferToBase64(await pngBlob.arrayBuffer());
    return `data:image/png;base64,${base64}`;
  } catch {
    return NOTIFICATION_ICON_FALLBACK;
  }
}

function getNotificationIconUrl() {
  if (!notificationIconUrlPromise) {
    notificationIconUrlPromise = buildNotificationIconUrl();
  }
  return notificationIconUrlPromise;
}

function safeNotificationCreate(id, options) {
  try {
    chrome.notifications.create(id, options, () => {
      void chrome.runtime.lastError;
    });
  } catch {
  }
}

const activeJobs = new Map();
let alarmsInitialized = false;

async function getConfig() {
  const stored = await chrome.storage.sync.get(DEFAULT_CONFIG);
  return { ...DEFAULT_CONFIG, ...stored };
}

async function setConfig(key, value) {
  await chrome.storage.sync.set({ [key]: value });
}

function buildEndpointUrl(serverUrl, endpoint) {
  const cleanBase = String(serverUrl || "http://localhost:5000").replace(/\/+$/, "");
  const cleanEndpoint = String(endpoint || "").replace(/^\/+/, "");
  return `${cleanBase}/api/v1/${cleanEndpoint}`;
}

function buildServerUrl(serverUrl, path) {
  const cleanBase = String(serverUrl || "http://localhost:5000").replace(/\/+$/, "");
  const cleanPath = String(path || "").replace(/^\/+/, "");
  return `${cleanBase}/${cleanPath}`;
}

async function safeJson(response) {
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { message: text || "Unexpected server response" };
  }
}

async function callUniversalDL(endpoint, method = "GET", body = null) {
  const config = await getConfig();
  const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
  const apiKey = (config[CONFIG_KEYS.API_KEY] || "").trim();
  const url = buildEndpointUrl(serverUrl, endpoint);

  const headers = {
    "Content-Type": "application/json",
    "X-Extension-Version": EXTENSION_VERSION
  };

  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 12000);

  try {
    const response = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
      signal: controller.signal,
      credentials: "omit"
    });

    const payload = await safeJson(response);
    if (!response.ok) {
      const message = payload.message || payload.error || `Request failed with status ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("UniversalDL server request timed out. Check if the server is reachable.");
    }
    if (error.status) {
      throw error;
    }
    throw new Error(`Unable to reach UniversalDL server at ${serverUrl}. ${error.message}`);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function callServerPath(path, method = "GET", body = null) {
  const config = await getConfig();
  const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
  const url = buildServerUrl(serverUrl, path);

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 12000);

  try {
    const response = await fetch(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Version": EXTENSION_VERSION
      },
      body: body ? JSON.stringify(body) : null,
      signal: controller.signal,
      credentials: "omit"
    });

    const payload = await safeJson(response);
    if (!response.ok) {
      const message = payload.message || payload.error || `Request failed with status ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("UniversalDL server request timed out. Check if the server is reachable.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

function normalizeJobData(raw, jobId) {
  if (!raw) {
    return null;
  }

  if (raw.job) {
    const job = raw.job;
    return {
      jobId: job.job_id || jobId,
      status: job.status,
      title: job.title || "Untitled",
      progress_pct: Number(job.progress_pct || 0),
      speed_bps: Number(job.speed_bps || 0),
      eta_seconds: Number(job.eta_seconds || 0),
      error_message: job.error_message || null,
      thumbnail_url: job.thumbnail_url || null,
      selected_quality: job.selected_quality || null,
      selected_format: job.selected_format || null,
      download_url: job.download_url || null,
      platform: job.platform || null,
      qualities: (raw.media_info && raw.media_info.qualities) || []
    };
  }

  return {
    jobId: raw.job_id || jobId,
    status: raw.status,
    title: raw.title || "Untitled",
    progress_pct: Number(raw.progress_pct || 0),
    speed_bps: Number(raw.speed_bps || 0),
    eta_seconds: Number(raw.eta_seconds || 0),
    error_message: raw.error_message || null,
    thumbnail_url: raw.thumbnail_url || null,
    selected_quality: raw.selected_quality || null,
    selected_format: raw.selected_format || null,
    download_url: raw.download_url || null,
    platform: raw.platform || null,
    qualities: raw.qualities || []
  };
}

async function fetchJobStatus(jobId) {
  const config = await getConfig();
  const apiKey = String(config[CONFIG_KEYS.API_KEY] || "").trim();

  if (!apiKey) {
    const fallback = await callServerPath(`download/info/${jobId}`, "GET");
    return normalizeJobData(fallback, jobId);
  }

  try {
    const payload = await callUniversalDL(`jobs/${jobId}`, "GET");
    return normalizeJobData(payload, jobId);
  } catch (error) {
    if ([401, 403].includes(Number(error.status || 0))) {
      const fallback = await callServerPath(`download/info/${jobId}`, "GET");
      return normalizeJobData(fallback, jobId);
    }
    throw error;
  }
}

async function pollJobStatus(jobId) {
  const existing = activeJobs.get(jobId);
  if (!existing) {
    return null;
  }

  try {
    const jobData = await fetchJobStatus(jobId);
    if (!jobData) {
      return null;
    }

    const merged = {
      ...existing,
      ...jobData,
      lastUpdated: Date.now()
    };
    activeJobs.set(jobId, merged);

    if (existing.jobType === "analyze" && ["pending_download", "complete"].includes(jobData.status)) {
      activeJobs.delete(jobId);
      await chrome.alarms.clear(`poll_${jobId}`);
    } else if (jobData.status === "complete") {
      await onJobComplete(jobId, merged);
      await chrome.alarms.clear(`poll_${jobId}`);
    } else if (["failed", "cancelled"].includes(jobData.status)) {
      await onJobFailed(jobId, jobData.error_message || "Download failed");
      await chrome.alarms.clear(`poll_${jobId}`);
    }

    await persistActiveJobs();
    await updateBadge();
    return merged;
  } catch (error) {
    const failCount = Number(existing.poll_failures || 0) + 1;
    if (failCount >= 3) {
      await onJobFailed(jobId, `Polling failed: ${error.message}`);
      await chrome.alarms.clear(`poll_${jobId}`);
    } else {
      activeJobs.set(jobId, { ...existing, poll_failures: failCount, lastError: error.message });
      await updateBadge();
    }
    return null;
  }
}

async function startPollingJob(jobId, pageTitle = "", metadata = {}) {
  activeJobs.set(jobId, {
    jobId,
    url: metadata.url || null,
    title: pageTitle || "Preparing download",
    status: metadata.status || "queued",
    startTime: Date.now(),
    progress_pct: Number(metadata.progress_pct || 0),
    speed_bps: 0,
    eta_seconds: 0,
    jobType: metadata.jobType || "download",
    thumbnail_url: metadata.thumbnail_url || null,
    platform: metadata.platform || null
  });

  chrome.alarms.create(`poll_${jobId}`, { periodInMinutes: 0.1 });
  await persistActiveJobs();
  await updateBadge();
}

async function onJobComplete(jobId, jobData) {
  activeJobs.delete(jobId);
  await persistActiveJobs();
  await updateBadge();

  const config = await getConfig();
  if (config[CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]) {
    const title = String(jobData.title || "Download complete").slice(0, 100);
    const iconUrl = await getNotificationIconUrl();
    safeNotificationCreate(`udl_done_${jobId}`, {
      type: "basic",
      iconUrl,
      title: "Download Complete! ✓",
      message: title
    });
  }

  const stored = await chrome.storage.local.get({ [RECENT_JOBS_KEY]: [] });
  const existing = Array.isArray(stored[RECENT_JOBS_KEY]) ? stored[RECENT_JOBS_KEY] : [];
  existing.unshift({
    id: jobId,
    status: "complete",
    title: jobData.title || "Untitled",
    download_url: jobData.download_url || null,
    file_size_bytes: jobData.file_size_bytes || null,
    completed_at: Date.now()
  });

  await chrome.storage.local.set({ [RECENT_JOBS_KEY]: existing.slice(0, 30) });
}

async function onJobFailed(jobId, errorMessage) {
  const existing = activeJobs.get(jobId) || {};
  activeJobs.delete(jobId);
  await persistActiveJobs();
  await updateBadge();

  const config = await getConfig();
  if (config[CONFIG_KEYS.NOTIFICATION_ON_COMPLETE]) {
    const iconUrl = await getNotificationIconUrl();
    safeNotificationCreate(`udl_fail_${jobId}`, {
      type: "basic",
      iconUrl,
      title: "Download Failed",
      message: String(errorMessage || "An unknown error occurred").slice(0, 180)
    });
  }

  const stored = await chrome.storage.local.get({ [RECENT_JOBS_KEY]: [] });
  const recent = Array.isArray(stored[RECENT_JOBS_KEY]) ? stored[RECENT_JOBS_KEY] : [];
  recent.unshift({
    id: jobId,
    status: "failed",
    title: existing.title || "Untitled",
    error_message: errorMessage || "Unknown error",
    failed_at: Date.now()
  });
  await chrome.storage.local.set({ [RECENT_JOBS_KEY]: recent.slice(0, 30) });
}

async function updateBadge() {
  const config = await getConfig();
  if (!config[CONFIG_KEYS.BADGE_ACTIVE_JOBS]) {
    chrome.action.setBadgeText({ text: "" });
    return;
  }

  const activeCount = activeJobs.size;
  if (activeCount === 0) {
    chrome.action.setBadgeText({ text: "" });
  } else {
    chrome.action.setBadgeText({ text: String(activeCount) });
    chrome.action.setBadgeBackgroundColor({ color: "#E94560" });
    if (chrome.action.setBadgeTextColor) {
      chrome.action.setBadgeTextColor({ color: "#FFFFFF" });
    }
  }
}

function createContextMenu() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "universaldl_download",
      title: "Download with UniversalDL",
      contexts: ["page", "video", "audio", "link", "image"]
    });
  });
}

async function handleContextMenuClick(info, tab) {
  const targetUrl = info.linkUrl || info.srcUrl || info.pageUrl || (tab && tab.url);
  if (!targetUrl) {
    return;
  }

  await chrome.storage.local.set({
    udl_last_context_url: targetUrl,
    udl_last_context_title: (tab && tab.title) || ""
  });

  try {
    if (chrome.action && chrome.action.openPopup) {
      await chrome.action.openPopup();
      return;
    }
  } catch (error) {
    console.warn("Popup open failed from context menu", error);
  }

  try {
    await startAnalyzeFlow(targetUrl, (tab && tab.title) || "Context Download");
  } catch (error) {
    const iconUrl = await getNotificationIconUrl();
    safeNotificationCreate(`udl_context_error_${Date.now()}`, {
      type: "basic",
      iconUrl,
      title: "UniversalDL",
      message: String(error.message || "Unable to start download")
    });
  }
}

async function startAnalyzeFlow(url, title = "") {
  const config = await getConfig();
  const apiKey = String(config[CONFIG_KEYS.API_KEY] || "").trim();

  if (!apiKey) {
    const fallback = await callServerPath("download/analyze", "POST", { url });
    const jobId = fallback.job_id;
    if (!jobId) {
      throw new Error("Analyze response did not include a job id");
    }
    await startPollingJob(jobId, title, {
      url,
      status: "analyzing",
      jobType: "analyze"
    });
    return { jobId, source: "web" };
  }

  try {
    const response = await callUniversalDL("analyze", "POST", { url });
    const jobId = response.job_id || response.jobId;
    if (!jobId) {
      throw new Error("Analyze response did not include a job id");
    }

    await startPollingJob(jobId, title, {
      url,
      status: "analyzing",
      jobType: "analyze"
    });

    return { jobId, source: "api" };
  } catch (error) {
    if ([401, 403].includes(Number(error.status || 0))) {
      const fallback = await callServerPath("download/analyze", "POST", { url });
      const jobId = fallback.job_id;
      if (!jobId) {
        throw new Error("Analyze response did not include a job id");
      }
      await startPollingJob(jobId, title, {
        url,
        status: "analyzing",
        jobType: "analyze"
      });
      return { jobId, source: "web" };
    }
    throw error;
  }
}

async function startDownloadFlow({ url, quality, format, analyzeJobId, subtitle_language, subtitle_embed }) {
  const config = await getConfig();
  const apiKey = String(config[CONFIG_KEYS.API_KEY] || "").trim();

  if (!apiKey) {
    if (!analyzeJobId) {
      throw new Error("Download requires an analyze job id");
    }

    const fallback = await callServerPath("download/start", "POST", {
      job_id: analyzeJobId,
      quality,
      format,
      subtitle_language,
      subtitle_embed
    });
    const jobId = fallback.job_id;
    if (!jobId) {
      throw new Error("Download response did not include a job id");
    }

    await startPollingJob(jobId, "Download", {
      url,
      status: fallback.status || "queued",
      jobType: "download"
    });

    return { jobId, source: "web" };
  }

  try {
    const response = await callUniversalDL("download", "POST", {
      url,
      quality,
      format,
      subtitle_language,
      subtitle_embed
    });

    const jobId = response.job_id || response.jobId;
    if (!jobId) {
      throw new Error("Download response did not include a job id");
    }

    await startPollingJob(jobId, "Download", {
      url,
      status: response.status || "queued",
      jobType: "download"
    });

    return { jobId, source: "api" };
  } catch (error) {
    if ([401, 403].includes(Number(error.status || 0)) && analyzeJobId) {
      const fallback = await callServerPath("download/start", "POST", {
        job_id: analyzeJobId,
        quality,
        format,
        subtitle_language,
        subtitle_embed
      });
      const jobId = fallback.job_id;
      if (!jobId) {
        throw new Error("Download response did not include a job id");
      }

      await startPollingJob(jobId, "Download", {
        url,
        status: fallback.status || "queued",
        jobType: "download"
      });

      return { jobId, source: "web" };
    }
    throw error;
  }
}

async function getCurrentTabInfo() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  return {
    url: tab ? tab.url : "",
    title: tab ? tab.title : ""
  };
}

async function persistActiveJobs() {
  const serialized = Array.from(activeJobs.entries()).map(([jobId, jobData]) => ({ jobId, jobData }));
  await chrome.storage.local.set({ [ACTIVE_JOBS_KEY]: serialized });
}

async function restoreActiveJobs() {
  const stored = await chrome.storage.local.get({ [ACTIVE_JOBS_KEY]: [] });
  const items = Array.isArray(stored[ACTIVE_JOBS_KEY]) ? stored[ACTIVE_JOBS_KEY] : [];
  activeJobs.clear();

  for (const item of items) {
    if (!item || !item.jobId || !item.jobData) {
      continue;
    }
    activeJobs.set(item.jobId, item.jobData);
    chrome.alarms.create(`poll_${item.jobId}`, { periodInMinutes: 0.1 });
  }

  await updateBadge();
}

async function openPopupOrFallback() {
  try {
    await chrome.action.openPopup();
    return { success: true, method: "popup" };
  } catch (error) {
    try {
      await chrome.windows.create({
        url: chrome.runtime.getURL("popup/popup.html"),
        type: "popup",
        width: 420,
        height: 720,
        focused: true
      });
      return { success: true, method: "window" };
    } catch (fallbackError) {
      throw new Error(fallbackError.message || error.message || "Unable to open popup");
    }
  }
}

async function resolveQuickDownloadUrl() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const activeTab = tabs[0];
  const activeUrl = String((activeTab && activeTab.url) || "");

  if (activeUrl.startsWith("http://") || activeUrl.startsWith("https://")) {
    return {
      url: activeUrl,
      title: (activeTab && activeTab.title) || "Quick Download"
    };
  }

  const sameWindowTabs = await chrome.tabs.query({ currentWindow: true });
  const webTab = sameWindowTabs.find((tab) => {
    const tabUrl = String(tab && tab.url ? tab.url : "");
    return tabUrl.startsWith("http://") || tabUrl.startsWith("https://");
  });

  if (webTab) {
    return {
      url: String(webTab.url || ""),
      title: String(webTab.title || "Quick Download")
    };
  }

  const stored = await chrome.storage.local.get({
    udl_last_injected_url: "",
    udl_last_context_url: "",
    udl_last_injected_title: "",
    udl_last_context_title: ""
  });

  const fallbackUrl = String(stored.udl_last_injected_url || stored.udl_last_context_url || "");
  if (fallbackUrl.startsWith("http://") || fallbackUrl.startsWith("https://")) {
    return {
      url: fallbackUrl,
      title: String(stored.udl_last_injected_title || stored.udl_last_context_title || "Quick Download")
    };
  }

  throw new Error("Open a supported webpage first, then try quick download again.");
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  handleContextMenuClick(info, tab).catch((error) => {
    console.error("Context menu error", error);
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {
        case "GET_CURRENT_TAB_URL": {
          const tabInfo = await getCurrentTabInfo();
          sendResponse(tabInfo);
          return;
        }

        case "START_ANALYZE": {
          const result = await startAnalyzeFlow(message.url, message.title || "Analyze");
          sendResponse({ jobId: result.jobId, source: result.source });
          return;
        }

        case "START_DOWNLOAD": {
          const result = await startDownloadFlow({
            url: message.url,
            quality: message.quality,
            format: message.format,
            analyzeJobId: message.analyzeJobId,
            subtitle_language: message.subtitle_language,
            subtitle_embed: Boolean(message.subtitle_embed)
          });
          sendResponse({ jobId: result.jobId, source: result.source });
          return;
        }

        case "GET_JOB_STATUS": {
          const inMemory = activeJobs.get(message.jobId);
          if (inMemory) {
            const refreshed = await pollJobStatus(message.jobId);
            sendResponse(refreshed || inMemory);
            return;
          }

          const fetched = await fetchJobStatus(message.jobId);
          sendResponse(fetched || null);
          return;
        }

        case "GET_ALL_ACTIVE_JOBS": {
          const jobs = Array.from(activeJobs.entries()).map(([jobId, jobData]) => ({
            jobId,
            ...jobData
          }));
          sendResponse({ jobs });
          return;
        }

        case "GET_CONFIG": {
          const config = await getConfig();
          sendResponse(config);
          return;
        }

        case "SET_CONFIG": {
          await setConfig(message.key, message.value);
          sendResponse({ success: true });
          return;
        }

        case "OPEN_UNIVERSALDL": {
          const config = await getConfig();
          const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
          const target = buildServerUrl(serverUrl, message.path || "");
          await chrome.tabs.create({ url: target });
          sendResponse({ success: true });
          return;
        }

        case "OPEN_POPUP": {
          const result = await openPopupOrFallback();
          sendResponse(result);
          return;
        }

        case "QUICK_DOWNLOAD_CURRENT_PAGE": {
          const { url, title } = await resolveQuickDownloadUrl();
          const result = await startAnalyzeFlow(url, title || "Quick Download");

          try {
            await openPopupOrFallback();
          } catch {
            const config = await getConfig();
            const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
            await chrome.tabs.create({ url: buildServerUrl(serverUrl, `download?url=${encodeURIComponent(url)}`) });
          }

          sendResponse({ success: true, jobId: result.jobId, source: result.source, url });
          return;
        }

        case "GET_PLATFORMS": {
          const payload = await callUniversalDL("platforms", "GET");
          sendResponse({ platforms: payload.platforms || [] });
          return;
        }

        case "INJECT_BTN_CLICKED": {
          await chrome.storage.local.set({
            udl_last_injected_url: message.url || "",
            udl_last_injected_title: message.title || ""
          });
          try {
            await chrome.action.openPopup();
          } catch {
            const config = await getConfig();
            const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
            await chrome.tabs.create({ url: buildServerUrl(serverUrl, `download?url=${encodeURIComponent(message.url || "")}`) });
          }
          sendResponse({ success: true });
          return;
        }

        default:
          sendResponse({ error: `Unknown message type: ${message.type}` });
      }
    } catch (error) {
      sendResponse({ error: error.message || "Unknown extension error" });
    }
  })();

  return true;
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command === "quick_download") {
    try {
      const { url, title } = await resolveQuickDownloadUrl();
      await startAnalyzeFlow(url, title || "Quick Download");

      try {
        await openPopupOrFallback();
      } catch {
        const config = await getConfig();
        const serverUrl = config[CONFIG_KEYS.SERVER_URL] || DEFAULT_CONFIG[CONFIG_KEYS.SERVER_URL];
        await chrome.tabs.create({ url: buildServerUrl(serverUrl, `download?url=${encodeURIComponent(url)}`) });
      }
    } catch (error) {
      const iconUrl = await getNotificationIconUrl();
      safeNotificationCreate(`udl_quick_error_${Date.now()}`, {
        type: "basic",
        iconUrl,
        title: "Quick Download Failed",
        message: String(error.message || "Unable to start quick download")
      });
    }
  }
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name.startsWith("poll_")) {
    const jobId = alarm.name.replace("poll_", "");
    await pollJobStatus(jobId);
  }
});

chrome.runtime.onInstalled.addListener(async (details) => {
  const current = await chrome.storage.sync.get(DEFAULT_CONFIG);
  await chrome.storage.sync.set({ ...DEFAULT_CONFIG, ...current });

  createContextMenu();
  await updateBadge();

  if (details.reason === "install") {
    await chrome.runtime.openOptionsPage();
    const iconUrl = await getNotificationIconUrl();
    safeNotificationCreate("udl_welcome", {
      type: "basic",
      iconUrl,
      title: "Welcome to UniversalDL",
      message: "Extension installed. Configure your server URL to start downloading."
    });
  }

  if (details.reason === "update") {
    const iconUrl = await getNotificationIconUrl();
    safeNotificationCreate("udl_update", {
      type: "basic",
      iconUrl,
      title: "UniversalDL Updated",
      message: "UniversalDL extension was updated to v1.0.0. Open options for changelog and settings."
    });
  }
});

chrome.runtime.onStartup.addListener(async () => {
  if (!alarmsInitialized) {
    await restoreActiveJobs();
    alarmsInitialized = true;
  }
  createContextMenu();
  await updateBadge();
});

(async () => {
  await chrome.storage.sync.set(await chrome.storage.sync.get(DEFAULT_CONFIG));
  await restoreActiveJobs();
  createContextMenu();
  await updateBadge();
})();
