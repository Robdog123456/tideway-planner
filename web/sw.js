const C='tideguide-v2';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(clients.claim()));
self.addEventListener('fetch',e=>{
  const req=e.request; if(req.method!=='GET')return;
  const netFirst=req.mode==='navigate'||req.url.includes('grid_v2.json');
  e.respondWith((async()=>{
    const cache=await caches.open(C);
    if(netFirst){
      try{const r=await fetch(req); if(r.ok)cache.put(req,r.clone()); return r;}
      catch(err){const c=await cache.match(req); if(c)return c; throw err;}
    }
    const c=await cache.match(req); if(c)return c;
    const r=await fetch(req); if(r.ok)cache.put(req,r.clone()); return r;
  })());
});