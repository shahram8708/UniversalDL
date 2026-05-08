const CACHE_VERSION = "v1.5.0";
const STATIC_CACHE = "universaldl-static-" + CACHE_VERSION;
const DYNAMIC_CACHE = "universaldl-dynamic-" + CACHE_VERSION;

const STATIC_ASSETS = [
  "/",
  "/download",
  "/features",
  "/platforms",
  "/pricing",
  "/blog",
  "/docs",
  "/changelog",
  "/contact",
  "/static/css/main.css",
  "/static/css/components.css",
  "/static/css/admin.css",
  "/static/js/main.js",
  "/static/js/download.js",
  "/static/js/progress.js",
  "/static/js/batch.js",
  "/static/js/dashboard.js",
  "/static/js/history.js",
  "/static/js/subscriptions.js",
  "/static/js/settings.js",
  "/static/js/api_settings.js",
  "/static/js/onboarding.js",
  "/static/js/upgrade.js",
  "/static/js/pwa.js",
  "/static/manifest.json",
  "/offline.html"
];

const NETWORK_ONLY_PREFIXES = [
  "/api/",
  "/download/progress/",
  "/download/batch/status/",
  "/dashboard/queue-status",
  "/auth/",
  "/admin/"
];

const API_PREFIX = "/api/v1/";

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(STATIC_CACHE);
      await Promise.all(
        STATIC_ASSETS.map(async (asset) => {
          try {
            await cache.add(asset);
          } catch (err) {
            return;
          }
        })
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.map((key) => {
          if (key !== STATIC_CACHE && key !== DYNAMIC_CACHE) {
            return caches.delete(key);
          }
          return null;
        })
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

function isNetworkOnly(url) {
  const path = url.pathname || "";
  if (NETWORK_ONLY_PREFIXES.some((prefix) => path.startsWith(prefix))) {
    return true;
  }
  const haystack = url.href.toLowerCase();
  return (
    haystack.includes("sse") ||
    haystack.includes("stream") ||
    haystack.includes("event-stream")
  );
}

function isStaticAsset(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return false;
  }
  if (url.pathname.startsWith("/static/")) {
    return true;
  }
  return ["style", "script", "image", "font"].includes(request.destination);
}

async function networkOnly(request, url) {
  try {
    return await fetch(request);
  } catch (err) {
    if (request.mode === "navigate") {
      const offline = await caches.match("/offline.html");
      if (offline) {
        return offline;
      }
    }

    const accept = (request.headers.get("Accept") || "").toLowerCase();
    if (
      accept.includes("application/json") ||
      url.pathname.startsWith("/api/") ||
      url.pathname.startsWith("/download/progress/") ||
      url.pathname.startsWith("/download/batch/status/") ||
      url.pathname.startsWith("/dashboard/queue-status")
    ) {
      return new Response(
        JSON.stringify({
          error: "offline",
          message: "No internet connection. Please reconnect."
        }),
        {
          status: 503,
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": "no-store"
          }
        }
      );
    }

    return new Response("Offline", {
      status: 503,
      statusText: "Service Unavailable",
      headers: {
        "Content-Type": "text/plain",
        "Cache-Control": "no-store"
      }
    });
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok && request.method === "GET") {
      const cache = await caches.open(DYNAMIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cache = await caches.open(DYNAMIC_CACHE);
    const cached = await cache.match(request);
    if (cached) {
      return cached;
    }
    return caches.match("/offline.html");
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    return;
  }

  if (isNetworkOnly(url)) {
    event.respondWith(networkOnly(request, url));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }

  if (url.pathname.startsWith(API_PREFIX)) {
    event.respondWith(
      (async () => {
        try {
          return await fetch(request);
        } catch (err) {
          return new Response(
            JSON.stringify({
              error: "offline",
              message: "No internet connection. Please reconnect."
            }),
            { status: 503, headers: { "Content-Type": "application/json" } }
          );
        }
      })()
    );
    return;
  }

  if (isStaticAsset(request)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(STATIC_CACHE);
        const cached = await cache.match(request);
        if (cached) {
          return cached;
        }
        try {
          const response = await fetch(request);
          if (response && response.ok && request.method === "GET") {
            cache.put(request, response.clone());
          }
          return response;
        } catch (err) {
          return undefined;
        }
      })()
    );
    return;
  }

  event.respondWith(
    (async () => {
      try {
        const response = await fetch(request);
        if (response && response.ok && request.method === "GET") {
          const cache = await caches.open(DYNAMIC_CACHE);
          cache.put(request, response.clone());
        }
        return response;
      } catch (err) {
        const cache = await caches.open(DYNAMIC_CACHE);
        const cached = await cache.match(request);
        if (cached) {
          return cached;
        }
        return caches.match("/offline.html");
      }
    })()
  );
});

self.addEventListener("sync", (event) => {
  if (event.tag === "background-download-sync") {
    event.waitUntil(syncPendingDownloads());
  }
});

const PENDING_DB = "universaldl-pending";
const PENDING_STORE = "downloads";

function openPendingDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(PENDING_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(PENDING_STORE)) {
        db.createObjectStore(PENDING_STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function getAllPending() {
  const db = await openPendingDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(PENDING_STORE, "readonly");
    const store = tx.objectStore(PENDING_STORE);
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function deletePending(id) {
  const db = await openPendingDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(PENDING_STORE, "readwrite");
    const store = tx.objectStore(PENDING_STORE);
    const req = store.delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function syncPendingDownloads() {
  const pending = await getAllPending();
  if (!pending.length) {
    return;
  }
  for (const item of pending) {
    try {
      const url = "/download?url=" + encodeURIComponent(item.url || "");
      const response = await fetch(url, { method: "GET" });
      if (response && response.ok) {
        await deletePending(item.id);
      }
    } catch (err) {
      return;
    }
  }
}

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || "UniversalDL";
  const options = {
    body: data.body || "You have a new update.",
    icon: "/static/icons/icon-192x192.png",
    badge: "/static/icons/icon-96x96.png",
    data: { url: data.url || "/" }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(targetUrl) && "focus" in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
      return null;
    })
  );
});
