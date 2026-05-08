(() => {
  const BUTTON_HOST_ID = "universaldl-float-btn-host";
  const YOUTUBE_BUTTON_ID = "universaldl-youtube-btn";

  let currentPlatform = null;
  let lastUrl = window.location.href;
  let observer = null;

  function detectPlatform() {
    const hostname = (window.location.hostname || "").toLowerCase();
    const pathname = (window.location.pathname || "").toLowerCase();

    const rules = [
      { key: "youtube", test: hostname.includes("youtube.com") || hostname.includes("youtu.be") },
      { key: "tiktok", test: hostname.includes("tiktok.com") },
      { key: "instagram", test: hostname.includes("instagram.com") },
      { key: "twitter", test: hostname.includes("twitter.com") || hostname.includes("x.com") },
      { key: "reddit", test: hostname.includes("reddit.com") },
      { key: "twitch", test: hostname.includes("twitch.tv") },
      { key: "vimeo", test: hostname.includes("vimeo.com") },
      { key: "soundcloud", test: hostname.includes("soundcloud.com") },
      { key: "bilibili", test: hostname.includes("bilibili.com") },
      { key: "facebook", test: hostname.includes("facebook.com") },
      { key: "dailymotion", test: hostname.includes("dailymotion.com") },
      { key: "behance", test: hostname.includes("behance.net") },
      { key: "imgur", test: hostname.includes("imgur.com") },
      { key: "coursera", test: hostname.includes("coursera.org") },
      { key: "anchor", test: hostname.includes("anchor.fm") },
      { key: "spotify", test: hostname.includes("spotify.com") }
    ];

    const platformMatch = rules.find((rule) => rule.test);
    const platform = platformMatch ? platformMatch.key : null;

    const hasPlayableMedia = Boolean(
      document.querySelector("video") ||
      document.querySelector("audio") ||
      (platform === "youtube" && pathname.startsWith("/watch"))
    );

    return { platform, hasPlayableMedia };
  }

  function removeInjectedButton() {
    const floating = document.getElementById(BUTTON_HOST_ID);
    if (floating) {
      floating.remove();
    }

    const youtubeBtn = document.getElementById(YOUTUBE_BUTTON_ID);
    if (youtubeBtn) {
      youtubeBtn.remove();
    }
  }

  function injectDownloadButton(platform) {
    removeInjectedButton();

    if (platform === "youtube" && injectYouTubeControlButton()) {
      return;
    }

    injectFloatingButton();
  }

  function injectFloatingButton() {
    if (!document.body) {
      return;
    }

    const host = document.createElement("div");
    host.id = BUTTON_HOST_ID;
    host.style.position = "fixed";
    host.style.bottom = "80px";
    host.style.right = "20px";
    host.style.zIndex = "2147483647";

    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
      .udl-btn-inner {
        display: flex;
        align-items: center;
        gap: 6px;
        background: #E94560;
        color: #FFFFFF;
        border-radius: 50px;
        padding: 10px 16px;
        font-size: 13px;
        font-weight: 600;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        cursor: pointer;
        user-select: none;
        transition: transform 0.2s ease, background 0.2s ease;
      }
      .udl-btn-inner:hover {
        background: #C73652;
        transform: scale(1.05);
      }
      .udl-progress-pill {
        margin-left: 6px;
        padding: 2px 6px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.2);
        font-size: 11px;
      }
      svg {
        width: 14px;
        height: 14px;
        flex-shrink: 0;
      }
    `;

    const wrapper = document.createElement("div");
    wrapper.className = "udl-btn-inner";
    wrapper.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M12 4V14" stroke="white" stroke-width="2" stroke-linecap="round"/>
        <path d="M8 11L12 15L16 11" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <rect x="6" y="18" width="12" height="2" rx="1" fill="white"/>
      </svg>
      <span>Download</span>
      <span class="udl-progress-pill" id="udl-progress-pill" style="display:none;"></span>
    `;

    wrapper.addEventListener("click", () => {
      chrome.runtime.sendMessage({
        type: "INJECT_BTN_CLICKED",
        url: window.location.href,
        title: document.title
      });
    });

    shadow.appendChild(style);
    shadow.appendChild(wrapper);
    document.body.appendChild(host);
  }

  function injectYouTubeControlButton() {
    const controls = document.querySelector(".ytp-right-controls");
    if (!controls) {
      return false;
    }

    const button = document.createElement("button");
    button.id = YOUTUBE_BUTTON_ID;
    button.type = "button";
    button.textContent = "Download";
    button.style.marginLeft = "8px";
    button.style.padding = "0 10px";
    button.style.height = "30px";
    button.style.border = "none";
    button.style.borderRadius = "16px";
    button.style.background = "#E94560";
    button.style.color = "#fff";
    button.style.fontSize = "12px";
    button.style.fontWeight = "600";
    button.style.cursor = "pointer";

    button.addEventListener("mouseenter", () => {
      button.style.background = "#C73652";
    });

    button.addEventListener("mouseleave", () => {
      button.style.background = "#E94560";
    });

    button.addEventListener("click", () => {
      chrome.runtime.sendMessage({
        type: "INJECT_BTN_CLICKED",
        url: window.location.href,
        title: document.title
      });
    });

    controls.appendChild(button);
    return true;
  }

  function updateProgressIndicator(progressText) {
    const host = document.getElementById(BUTTON_HOST_ID);
    if (!host || !host.shadowRoot) {
      return;
    }

    const pill = host.shadowRoot.getElementById("udl-progress-pill");
    if (!pill) {
      return;
    }

    if (!progressText) {
      pill.style.display = "none";
      pill.textContent = "";
      return;
    }

    pill.style.display = "inline-block";
    pill.textContent = progressText;
  }

  function evaluateAndInject() {
    const detection = detectPlatform();
    currentPlatform = detection.platform;

    if (detection.platform && detection.hasPlayableMedia) {
      injectDownloadButton(detection.platform);
    } else {
      removeInjectedButton();
    }
  }

  function waitForDynamicContent() {
    if (currentPlatform === "youtube") {
      const hasPlayer = document.querySelector("ytd-player") || document.querySelector("video");
      if (!hasPlayer) {
        return;
      }
    }

    if (currentPlatform === "tiktok") {
      const hasTikTokVideo = document.querySelector(".tiktok-player video") || document.querySelector("video");
      if (!hasTikTokVideo) {
        return;
      }
    }

    evaluateAndInject();
  }

  function initNavigationObserver() {
    if (observer) {
      observer.disconnect();
    }

    observer = new MutationObserver(() => {
      const nextUrl = window.location.href;
      if (nextUrl !== lastUrl) {
        lastUrl = nextUrl;
        removeInjectedButton();
        setTimeout(() => {
          evaluateAndInject();
        }, 350);
      }

      waitForDynamicContent();
    });

    if (document.body) {
      observer.observe(document.body, {
        childList: true,
        subtree: true
      });
    }
  }

  function initContentScript() {
    if (!chrome || !chrome.runtime || !chrome.runtime.id) {
      return;
    }

    evaluateAndInject();
    initNavigationObserver();

    window.addEventListener("beforeunload", () => {
      removeInjectedButton();
      if (observer) {
        observer.disconnect();
      }
    });

    chrome.runtime.onMessage.addListener((message) => {
      if (!message || !message.type) {
        return;
      }

      if (message.type === "REMOVE_DOWNLOAD_BUTTON") {
        removeInjectedButton();
      }

      if (message.type === "UPDATE_JOB_PROGRESS") {
        updateProgressIndicator(message.progressText || "");
      }
    });
  }

  initContentScript();
})();
