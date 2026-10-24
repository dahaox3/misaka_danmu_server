// Private Docker-network HTTP bridge. No credentials or comment text in logs.
// GPL-3.0-or-later, 2026-10-20.
import http from 'node:http';
import { Worker } from 'node:worker_threads';
import { createHash } from 'node:crypto';

const worker = new Worker(new URL('./engine.mjs', import.meta.url), {
  resourceLimits: { maxOldGenerationSizeMb: 160 },
});
let ready = false, nextId = 0, cacheBytes = 0;
const pending = new Map(), cache = new Map(), inflight = new Map();
worker.on('message', message => {
  if (message.ready) { ready = true; return; }
  const p = pending.get(message.id);
  if (!p) return;
  clearTimeout(p.timer); pending.delete(message.id);
  if (message.error) { const error = Error(message.error); error.invalidSettings = message.invalidSettings; p.reject(error); }
  else p.resolve(message.result);
});
worker.on('error', () => process.exit(1));
worker.on('exit', () => process.exit(1));

function run(payload) {
  if (!ready || pending.size >= 4) return Promise.reject(Error('busy'));
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    // A stuck WASM job is killed by container restart, not left consuming CPU.
    const timer = setTimeout(() => process.exit(1), 20000);
    pending.set(id, { resolve, reject, timer });
    worker.postMessage({ id, payload });
  });
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') return json(res, ready ? 200 : 503, { ready });
  if (req.method !== 'POST' || req.url !== '/merge') return json(res, 404, { error: 'not found' });
  let size = 0;
  try {
    if (Number(req.headers['content-length']) > 32 * 1024 * 1024) return json(res, 413, { error: 'too large' });
    const chunks = [];
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 32 * 1024 * 1024) { json(res, 413, { error: 'too large' }); return; }
      chunks.push(chunk);
    }
    const body = Buffer.concat(chunks);
    const key = createHash('sha256').update(body).digest('hex');
    const cached = cache.get(key);
    if (cached && cached.expires > Date.now()) {
      cache.delete(key); cache.set(key, cached); return json(res, 200, cached.body);
    }
    if (cached) { cacheBytes -= cached.size; cache.delete(key); }
    let promise = inflight.get(key);
    if (!promise) {
      const payload = JSON.parse(body.toString('utf8'));
      promise = run(payload).then(result => {
        const output = JSON.stringify(result), bytes = Buffer.byteLength(output);
        if (bytes < 16 * 1024 * 1024) {
          while (cache.size && (cacheBytes + bytes > 24 * 1024 * 1024 || cache.size >= 16)) {
            const k = cache.keys().next().value; cacheBytes -= cache.get(k).size; cache.delete(k);
          }
          cache.set(key, { body: output, size: bytes, expires: Date.now() + 300000 }); cacheBytes += bytes;
        }
        return output;
      }).finally(() => inflight.delete(key));
      inflight.set(key, promise);
    }
    json(res, 200, await promise);
  } catch (error) { json(res, error.invalidSettings ? 400 : 503, { error: error.invalidSettings ? error.message : 'merge unavailable' }); }
});
server.requestTimeout = 25000;
server.headersTimeout = 10000;
server.listen(Number(process.env.PORT || 17769), process.env.BIND || '0.0.0.0');
