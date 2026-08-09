const VERSION = "betreuung-pwa-v16";
const STATIC_CACHE = `${VERSION}-static`;
const DATA_CACHE = `${VERSION}-data`;
const PAGE_CACHE = `${VERSION}-pages`;
const STATIC_ASSETS = [
  "/static/app.css",
  "/static/app.js",
  "/static/icon.svg",
  "/static/favicon-64.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-512.png",
  "/static/apple-touch-icon.png",
  "/static/offline.html",
  "/manifest.webmanifest"
];

self.addEventListener("install", event => {
  event.waitUntil((async()=>{
    const cache=await caches.open(STATIC_CACHE);
    for(const url of STATIC_ASSETS){
      try{
        const response=await fetch(url,{cache:"reload"});
        if(response.ok) await cache.put(url,response);
      }catch(_e){}
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async()=>{
    const keep=new Set([STATIC_CACHE,DATA_CACHE,PAGE_CACHE]);
    for(const key of await caches.keys()) if(!keep.has(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("message", event => {
  if(!event.data) return;
  if(event.data.type==="SKIP_WAITING") self.skipWaiting();
  if(event.data.type==="CLEAR_PRIVATE_DATA") {
    event.waitUntil(Promise.all([caches.delete(DATA_CACHE),caches.delete(PAGE_CACHE)]));
  }
});

async function networkFirst(request, cacheName, fallback){
  const cache=await caches.open(cacheName);
  try{
    const response=await fetch(request);
    if(response.ok && !response.redirected) await cache.put(request,response.clone());
    return response;
  }catch(_e){
    return (await cache.match(request)) || (fallback ? await caches.match(fallback) : Response.error());
  }
}

async function staleWhileRevalidate(request){
  const cache=await caches.open(STATIC_CACHE);
  const cached=await cache.match(request);
  const refresh=fetch(request,{cache:"no-cache"}).then(response=>{
    if(response.ok) cache.put(request,response.clone());
    return response;
  }).catch(()=>null);
  return cached || (await refresh) || Response.error();
}

self.addEventListener("fetch", event => {
  const request=event.request;
  if(request.method!=="GET") return;
  const url=new URL(request.url);
  if(url.origin!==self.location.origin) return;

  if(request.mode==="navigate"){
    event.respondWith(networkFirst(request,PAGE_CACHE,"/static/offline.html"));
    return;
  }

  if(url.pathname.startsWith("/api/") && url.pathname!=="/api/config"){
    event.respondWith(networkFirst(request,DATA_CACHE));
    return;
  }

  if(url.pathname.startsWith("/static/") || url.pathname==="/manifest.webmanifest"){
    event.respondWith(staleWhileRevalidate(request));
  }
});
