"use strict";

var deferredPrompt = null;

function shouldDisableServiceWorker() {
  var params = new URLSearchParams(window.location.search || "");
  if (params.get("sw") === "off") {
    return true;
  }
  try {
    return window.localStorage.getItem("udl-sw-disabled") === "1";
  } catch (e) {
    return false;
  }
}

function showToast(message, variant, actionLabel, actionHandler) {
  var container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container position-fixed bottom-0 end-0 p-3";
    document.body.appendChild(container);
  }

  var toast = document.createElement("div");
  var tone = variant || "info";
  if (tone === "danger") {
    tone = "error";
  }
  toast.className = "toast align-items-center theme-toast theme-toast-" + tone;
  toast.setAttribute("role", "alert");
  toast.innerHTML =
    '<div class="d-flex">' +
    '<div class="toast-body">' + message + "</div>" +
    (actionLabel
      ? '<button class="btn btn-sm btn-outline-primary ms-2" type="button">' + actionLabel + "</button>"
      : "") +
    '<button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>' +
    "</div>";

  container.appendChild(toast);
  if (actionLabel && actionHandler) {
    var actionBtn = toast.querySelector("button.btn.btn-sm");
    if (actionBtn) {
      actionBtn.addEventListener("click", actionHandler);
    }
  }

  if (window.bootstrap && bootstrap.Toast) {
    var toastInstance = new bootstrap.Toast(toast, { delay: 5000 });
    toastInstance.show();
  }
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  if (shouldDisableServiceWorker()) {
    if (navigator.serviceWorker.getRegistrations) {
      navigator.serviceWorker.getRegistrations().then(function (registrations) {
        registrations.forEach(function (registration) {
          registration.unregister();
        });
      });
    }
    if (window.caches && caches.keys) {
      caches.keys().then(function (keys) {
        keys.forEach(function (key) {
          caches.delete(key);
        });
      });
    }
    return;
  }

  navigator.serviceWorker
    .register("/sw.js", { scope: "/" })
    .then(function (registration) {
      registration.update();

      function handleUpdate() {
        var waiting = registration.waiting;
        if (waiting) {
          showToast("New version available!", "info", "Update Now", function () {
            waiting.postMessage({ type: "SKIP_WAITING" });
            window.location.reload();
          });
        }
      }

      if (registration.waiting) {
        handleUpdate();
      }

      registration.addEventListener("updatefound", function () {
        var newWorker = registration.installing;
        if (!newWorker) {
          return;
        }
        newWorker.addEventListener("statechange", function () {
          if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
            handleUpdate();
          }
        });
      });
    })
    .catch(function (error) {
      console.log("Service worker registration failed:", error);
    });
}

function handleInstallPrompt() {
  var installBtn = document.getElementById("install-pwa-btn");
  if (installBtn) {
    installBtn.addEventListener("click", function () {
      if (!deferredPrompt) {
        return;
      }
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function (choice) {
        if (choice && choice.outcome === "accepted") {
          showToast("UniversalDL added to home screen!", "success");
        }
        installBtn.classList.add("d-none");
        deferredPrompt = null;
      });
    });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    if (!installBtn) {
      return;
    }
    event.preventDefault();
    deferredPrompt = event;
    installBtn.classList.remove("d-none");
  });

  window.addEventListener("appinstalled", function () {
    if (installBtn) {
      installBtn.classList.add("d-none");
    }
    deferredPrompt = null;
  });
}

function handleShareTarget() {
  var params = new URLSearchParams(window.location.search);
  var sharedUrl = params.get("url") || params.get("text") || params.get("title");
  if (!sharedUrl) {
    return;
  }
  if (window.location.pathname === "/download") {
    var input = document.getElementById("url-input");
    if (input) {
      input.value = sharedUrl;
    }
  }
}

function showOfflineBar() {
  if (document.getElementById("offline-bar")) {
    return;
  }
  var bar = document.createElement("div");
  bar.id = "offline-bar";
  bar.className = "alert alert-danger text-center mb-0 rounded-0";
  bar.textContent = "⚠ You are offline. UniversalDL requires internet for downloads.";
  document.body.insertBefore(bar, document.body.firstChild);
}

function hideOfflineBar() {
  var bar = document.getElementById("offline-bar");
  if (bar && bar.parentNode) {
    bar.parentNode.removeChild(bar);
  }
}

function checkOnlineStatus() {
  if (!navigator.onLine) {
    showOfflineBar();
  }
  window.addEventListener("online", function () {
    hideOfflineBar();
    showToast("Back online!", "success");
  });
  window.addEventListener("offline", function () {
    showOfflineBar();
  });
}

var PENDING_DB = "universaldl-pending";
var PENDING_STORE = "downloads";

function openPendingDb() {
  return new Promise(function (resolve, reject) {
    var request = indexedDB.open(PENDING_DB, 1);
    request.onupgradeneeded = function () {
      var db = request.result;
      if (!db.objectStoreNames.contains(PENDING_STORE)) {
        db.createObjectStore(PENDING_STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    request.onsuccess = function () {
      resolve(request.result);
    };
    request.onerror = function () {
      reject(request.error);
    };
  });
}

function savePendingDownload(url) {
  if (!url) {
    return Promise.resolve();
  }
  return openPendingDb().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(PENDING_STORE, "readwrite");
      var store = tx.objectStore(PENDING_STORE);
      var req = store.add({ url: url, created_at: new Date().toISOString() });
      req.onsuccess = function () {
        resolve();
      };
      req.onerror = function () {
        reject(req.error);
      };
    });
  });
}

function registerBackgroundSync(url) {
  if (!navigator.serviceWorker || !navigator.serviceWorker.ready) {
    return;
  }
  savePendingDownload(url).then(function () {
    navigator.serviceWorker.ready.then(function (registration) {
      if (registration.sync) {
        registration.sync.register("background-download-sync");
      }
    });
  });
}

function watchOfflineDownloadSubmit() {
  var analyzeBtn = document.getElementById("analyze-btn");
  var urlInput = document.getElementById("url-input");
  if (!analyzeBtn || !urlInput) {
    return;
  }
  analyzeBtn.addEventListener(
    "click",
    function (event) {
      if (navigator.onLine) {
        return;
      }
      var urlValue = (urlInput.value || "").trim();
      if (!urlValue) {
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      registerBackgroundSync(urlValue);
      showToast("You are offline. We will retry this download when you are back online.", "warning");
    },
    true
  );
}

document.addEventListener("DOMContentLoaded", function () {
  registerServiceWorker();
  handleInstallPrompt();
  handleShareTarget();
  checkOnlineStatus();
  watchOfflineDownloadSubmit();
});
