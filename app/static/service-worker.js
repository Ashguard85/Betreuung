const APP_VERSION = "56";
const VERSION = `betreuung-pwa-v${APP_VERSION}`;
const SHELL_CACHE = `${VERSION}-shell`;
const DATA_CACHE = "betreuung-private-data-v1";
const CACHE_PREFIX = "betreuung-pwa-v";
const INDEX_URL = "/";

const ESSENTIAL_SHELL = [
  "/",
  "/static/app.css?v=56",
  "/static/app.js?v=56",
  "/manifest.webmanifest"
];

const OPTIONAL_SHELL = [
  "/static/offline.html",
  "/static/icon.svg",
  "/favicon-v17.png",
  "/pwa-icon-192-v17.png",
  "/pwa-icon-512-v17.png",
  "/pwa-icon-maskable-512-v17.png",
  "/apple-touch-icon-v17.png"
];

async function fetchFresh(url) {
  const response = await fetch(url, {cache: "reload", credentials: "same-origin"});
  if (!response.ok || response.redirected) throw new Error(`Precache failed: ${url} (${response.status})`);
  return response;
}

async function precacheShell() {
  const cache = await caches.open(SHELL_CACHE);
  // Essential shell files are atomic. A partial new frontend must never replace
  // a working installed version.
  for (const url of ESSENTIAL_SHELL) {
    const response = await fetchFresh(url);
    await cache.put(url, response);
  }
  for (const url of OPTIONAL_SHELL) {
    try {
      const response = await fetchFresh(url);
      await cache.put(url, response);
    } catch (error) {
      console.warn("Optional PWA asset could not be cached", url, error);
    }
  }
}

async function needsOneTimeCacheRecovery(){
  const keys=await caches.keys();
  return keys.some(key => /^betreuung-pwa-v(?:43|44|45)-shell$/.test(key));
}

self.addEventListener("install", event => {
  event.waitUntil((async()=>{
    await precacheShell();
    // One-time migration from the v43-v45 cache-first architecture. The new
    // worker becomes active, but does not claim/reload the currently open client.
    if(await needsOneTimeCacheRecovery()) await self.skipWaiting();
  })());
});

async function migrateLegacyDataCaches(keys) {
  const target = await caches.open(DATA_CACHE);
  for (const key of keys) {
    if (!/^betreuung-pwa-v\d+-data$/.test(key)) continue;
    const legacy = await caches.open(key);
    for (const request of await legacy.keys()) {
      const response = await legacy.match(request);
      if (response) await target.put(request, response);
    }
  }
}

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    // Preserve the previously cached read-only API state when migrating from the
    // old version-coupled data cache to the stable private data cache.
    await migrateLegacyDataCaches(keys);
    for (const key of keys) {
      if (key.startsWith(CACHE_PREFIX) && key !== SHELL_CACHE) await caches.delete(key);
    }
    // Keep DATA_CACHE across frontend versions and do not clients.claim().
  })());
});

self.addEventListener("message", event => {
  const data = event.data || {};
  if (data.type === "GET_VERSION") {
    event.ports?.[0]?.postMessage({version: APP_VERSION});
    return;
  }
  // Ignore the legacy generic SKIP_WAITING message from older app.js versions.
  if (data.type === "ACTIVATE_UPDATE" && data.userInitiated === true) {
    self.skipWaiting();
    return;
  }
  if (data.type === "CLEAR_PRIVATE_DATA") {
    event.waitUntil(caches.delete(DATA_CACHE));
  }
});

async function cachedOfflineResponse(response) {
  if (!response) return Response.error();
  const headers = new Headers(response.headers);
  headers.set("X-PWA-Offline-Cache", "1");
  const body = await response.arrayBuffer();
  return new Response(body, {status: response.status, statusText: response.statusText, headers});
}

async function networkFirstData(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok && !response.redirected) await cache.put(request, response.clone());
    return response;
  } catch (_error) {
    return cachedOfflineResponse(await cache.match(request));
  }
}

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Only the SPA root is an app-shell navigation. Login/export/calendar routes must
  // keep their normal server semantics.
  if (request.mode === "navigate" && url.pathname === "/") {
    event.respondWith((async () => {
      const cache = await caches.open(SHELL_CACHE);
      const shell = await cache.match(INDEX_URL);
      if (shell) return shell;
      try {
        const response = await fetch(request);
        if (response.ok && !response.redirected) await cache.put(INDEX_URL, response.clone());
        return response;
      } catch (_error) {
        return (await caches.match("/static/offline.html")) || Response.error();
      }
    })());
    return;
  }

  if (url.pathname.startsWith("/api/") && url.pathname !== "/api/config") {
    event.respondWith(networkFirstData(request));
    return;
  }

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest" ||
      url.pathname === "/apple-touch-icon-v17.png" || url.pathname === "/apple-touch-icon.png" ||
      url.pathname === "/apple-touch-icon-precomposed.png" || url.pathname === "/favicon-v17.png" ||
      url.pathname === "/favicon.ico" || url.pathname.startsWith("/pwa-icon-")) {
    event.respondWith((async () => {
      const cache = await caches.open(SHELL_CACHE);
      const cached = await cache.match(request);
      if (cached) return cached;
      try { return await fetch(request); } catch (_error) { return Response.error(); }
    })());
  }
});
