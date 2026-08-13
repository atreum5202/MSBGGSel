/**
 * networkCapture.js — встроенный сборщик сетевых запросов через CDP Network domain
 *
 * Подписывается на Network.requestWillBeSent / responseReceived / loadingFinished
 * для всех страниц профиля и держит in-memory ring buffer (по умолчанию 5000).
 * Опционально сбрасывает на диск в <MSB>/network/<profileId>/<ts>.jsonl.
 *
 * Шаблонные пути: /api/v1/offers/123 → /api/v1/offers/{id}
 *
 * API:
 *   attach(profileId, cdpPage)            — подписаться на страницу
 *   detach(profileId)                     — отписаться, сбросить на диск
 *   list(profileId, opts)                 — фильтрованный список запросов
 *   get(profileId, n)                     — одна запись по индексу
 *   endpoints(profileId, opts)            — группировка по шаблонному пути
 *   toHar(profileId, opts)                — экспорт в HAR 1.2
 *   clear(profileId)                      — очистить буфер
 *   sessions()                            — все активные профили
 *   status(profileId)                     — { active, count, oldestAt, newestAt }
 *
 * Конфиг через env:
 *   NETWORK_CAPTURE_RING_SIZE   — размер кольца (default 5000)
 *   NETWORK_CAPTURE_FLUSH_AT    — при каком размере буфера сбрасывать на диск (default 5000)
 *   NETWORK_CAPTURES_DIR        — папка для архивов (default <MSB>/network)
 *   NETWORK_CAPTURE_BODY        — 'none' | 'small' (default small, до 64 КБ на ответ)
 */

import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const RING_SIZE = Number(process.env.NETWORK_CAPTURE_RING_SIZE || 5000);
const FLUSH_AT  = Number(process.env.NETWORK_CAPTURE_FLUSH_AT  || RING_SIZE);
const BODY_MODE = (process.env.NETWORK_CAPTURE_BODY || 'small').toLowerCase();
const MAX_BODY_BYTES = 64 * 1024; // only fetch small response bodies
const DEFAULT_CAPTURES_ROOT = path.resolve(__dirname, '..', '..', '..', 'network');
const CAPTURES_ROOT = process.env.NETWORK_CAPTURES_DIR || DEFAULT_CAPTURES_ROOT;

// ── helpers ────────────────────────────────────────────────────────────────

function _templatePath(p) {
  if (!p) return p;
  // /api/v1/offers/12345      → /api/v1/offers/{id}
  // /api/v1/users/abc/orders  → /api/v1/users/{id}/orders
  return p
    .replace(/\/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, '/{uuid}')
    .replace(/\/\d{1,20}(?=\/|$)/g, '/{id}')
    .replace(/\/[A-Za-z0-9_-]{16,}(?=\/|$)/g, '/{token}');
}

function _methodColor(method) { return method; } // placeholder if we ever want to color

function _nowIso() { return new Date().toISOString(); }

function _sessionDir(profileId) {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  return path.join(CAPTURES_ROOT, profileId, ts);
}

function _ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
  return p;
}

// ── Per-profile ring buffer ───────────────────────────────────────────────

class RingBuffer {
  constructor(max) {
    this.max = max;
    this._buf = [];
    this._seq = 0;
  }
  push(item) {
    this._seq += 1;
    item.n = this._seq;
    this._buf.push(item);
    if (this._buf.length > this.max) this._buf.shift();
    return item.n;
  }
  patch(predicate, patch) {
    // walk from the end (most recent) and patch first match
    for (let i = this._buf.length - 1; i >= 0; i -= 1) {
      if (predicate(this._buf[i])) {
        Object.assign(this._buf[i], patch);
        return this._buf[i];
      }
    }
    return null;
  }
  filter(fn) { return this._buf.filter(fn); }
  list() { return this._buf.slice(); }
  clear() { this._buf.length = 0; }
  get size() { return this._buf.length; }
  get seq() { return this._seq; }
}

// ── Service ───────────────────────────────────────────────────────────────

export class NetworkCaptureService extends EventEmitter {
  constructor({ profileManager, browserLauncher, logger }) {
    super();
    this.profileManager = profileManager;
    this.browserLauncher = browserLauncher;
    this.logger = logger?.child?.({ mod: 'networkCapture' }) || logger || console;
    this.ringSize = RING_SIZE;
    this.flushAt = FLUSH_AT;
    this.bodyMode = BODY_MODE;

    /** @type {Map<string, { buffer: RingBuffer, sessions: Set<any>, attached: number }>} */
    this._state = new Map();

    // Auto-attach when browserLauncher creates a CDP page session.
    if (browserLauncher?.on) {
      browserLauncher.on('page:cdpSession', ({ profileId, cdpPage, page } = {}) => {
        if (!profileId || !cdpPage) return;
        this.attach(profileId, cdpPage, page).catch((err) => {
          this.logger.warn({ profileId, err: err.message }, 'networkCapture attach failed');
        });
      });
      browserLauncher.on('stopped', ({ id } = {}) => {
        if (!id) return;
        this.detach(id).catch((err) => {
          this.logger.warn({ profileId: id, err: err.message }, 'networkCapture detach failed');
        });
      });
    }
  }

  // ── public ─────────────────────────────────────────────────────────────

  async attach(profileId, cdpPage, _page) {
    if (!profileId || !cdpPage) return;
    const st = this._stateFor(profileId);
    if (st.sessions.has(cdpPage)) return; // already attached

    try {
      await cdpPage.send('Network.enable', { maxTotalBufferSize: 50_000_000, maxResourceBufferSize: 10_000_000 });
    } catch (err) {
      this.logger.warn({ profileId, err: err.message }, 'Network.enable failed');
    }

    const onReq = (params) => this._onRequest(profileId, params);
    const onRes = (params) => this._onResponse(profileId, params);
    const onFin = (params) => this._onFinished(profileId, params);
    const onFail = (params) => this._onFailed(profileId, params);

    cdpPage.on('Network.requestWillBeSent', onReq);
    cdpPage.on('Network.responseReceived', onRes);
    cdpPage.on('Network.loadingFinished', onFin);
    cdpPage.on('Network.loadingFailed', onFail);

    st.sessions.add(cdpPage);
    st.handlers = st.handlers || new Map();
    st.handlers.set(cdpPage, { onReq, onRes, onFin, onFail });

    this.logger.info({ profileId, attached: st.sessions.size }, 'networkCapture attached to page');
    this.emit('attached', { profileId });
  }

  async detach(profileId) {
    const st = this._state.get(profileId);
    if (!st) return { flushed: 0 };
    // Remove handlers
    if (st.handlers) {
      for (const [cdp, h] of st.handlers.entries()) {
        try { cdp.off('Network.requestWillBeSent', h.onReq); } catch {}
        try { cdp.off('Network.responseReceived',   h.onRes); } catch {}
        try { cdp.off('Network.loadingFinished',    h.onFin); } catch {}
        try { cdp.off('Network.loadingFailed',      h.onFail); } catch {}
      }
    }
    st.sessions.clear();
    st.handlers?.clear();
    st.attached = 0;

    // Flush to disk
    const flushed = this._flushToDisk(profileId);
    this._state.delete(profileId);
    this.logger.info({ profileId, flushed }, 'networkCapture detached');
    this.emit('detached', { profileId });
    return { flushed };
  }

  status(profileId) {
    const st = this._state.get(profileId);
    if (!st) return { active: false, profileId };
    const buf = st.buffer.list();
    return {
      active: true,
      profileId,
      count: buf.length,
      oldestAt: buf[0]?.capturedAt || null,
      newestAt: buf[buf.length - 1]?.capturedAt || null,
      pages: st.sessions.size,
    };
  }

  sessions() {
    return Array.from(this._state.entries()).map(([id, st]) => this.status(id));
  }

  list(profileId, opts = {}) {
    const st = this._state.get(profileId);
    if (!st) return [];
    let arr = st.buffer.list();
    if (opts.method)   arr = arr.filter((r) => r.method === String(opts.method).toUpperCase());
    if (opts.host)     arr = arr.filter((r) => r.host === opts.host || (r.host || '').endsWith('.' + opts.host));
    if (opts.status)   arr = arr.filter((r) => r.status === Number(opts.status));
    if (opts.minStatus) arr = arr.filter((r) => (r.status || 0) >= Number(opts.minStatus));
    if (opts.maxStatus) arr = arr.filter((r) => (r.status || 0) <= Number(opts.maxStatus));
    if (opts.since)    arr = arr.filter((r) => r.capturedAt >= opts.since);
    if (opts.until)    arr = arr.filter((r) => r.capturedAt <= opts.until);
    if (opts.path)     arr = arr.filter((r) => r.path === opts.path);
    if (opts.pattern) {
      try {
        const re = new RegExp(opts.pattern);
        arr = arr.filter((r) => re.test(r.path) || re.test(r.url));
      } catch { /* bad regex → ignore */ }
    }
    if (opts.limit && arr.length > opts.limit) arr = arr.slice(arr.length - opts.limit);
    return arr;
  }

  get(profileId, n) {
    const st = this._state.get(profileId);
    if (!st) return null;
    return st.buffer.list().find((r) => r.n === Number(n)) || null;
  }

  endpoints(profileId, opts = {}) {
    const st = this._state.get(profileId);
    if (!st) return [];
    /** @type {Map<string,{method,path,count,statuses:Set,firstAt,lastAt,sampleN}>} */
    const byKey = new Map();
    for (const r of st.buffer.list()) {
      const t = _templatePath(r.path);
      const key = `${r.method} ${t}`;
      let e = byKey.get(key);
      if (!e) {
        e = { method: r.method, path: t, count: 0, statuses: new Set(), firstAt: r.capturedAt, lastAt: r.capturedAt, sampleN: r.n };
        byKey.set(key, e);
      }
      e.count += 1;
      if (r.status) e.statuses.add(r.status);
      if (r.capturedAt < e.firstAt) e.firstAt = r.capturedAt;
      if (r.capturedAt > e.lastAt)  e.lastAt  = r.capturedAt;
    }
    let out = Array.from(byKey.values()).map((e) => ({ ...e, statuses: Array.from(e.statuses).sort() }));
    if (opts.pattern) {
      try {
        const re = new RegExp(opts.pattern);
        out = out.filter((e) => re.test(e.path));
      } catch {}
    }
    out.sort((a, b) => b.count - a.count);
    if (opts.limit) out = out.slice(0, opts.limit);
    return out;
  }

  /**
   * Экспорт в HAR 1.2 (HTTP Archive spec).
   * https://w3c.github.io/web-performance/specs/HAR/Overview.html
   */
  toHar(profileId, opts = {}) {
    const requests = this.list(profileId, opts);
    const log = {
      log: {
        version: '1.2',
        creator: { name: 'MSB networkCapture', version: '1.0.0' },
        browser: { name: 'Chromium', version: 'CDP' },
        pages: [],
        entries: requests.map((r) => this._toHarEntry(r)),
      },
    };
    return log;
  }

  clear(profileId) {
    const st = this._state.get(profileId);
    if (!st) return { cleared: 0 };
    const n = st.buffer.size;
    st.buffer.clear();
    return { cleared: n };
  }

  // ── internals ──────────────────────────────────────────────────────────

  _stateFor(profileId) {
    let st = this._state.get(profileId);
    if (!st) {
      st = { buffer: new RingBuffer(this.ringSize), sessions: new Set(), handlers: new Map(), attached: 0 };
      this._state.set(profileId, st);
    }
    return st;
  }

  _onRequest(profileId, params) {
    const req = params.request;
    if (!req) return;
    const url = req.url || '';
    let host = '', path = '';
    try { const u = new URL(url); host = u.host; path = u.pathname; } catch { path = url.split('?')[0]; }
    const st = this._stateFor(profileId);
    st.buffer.push({
      capturedAt: _nowIso(),
      requestId: params.requestId,
      method: (req.method || 'GET').toUpperCase(),
      url,
      scheme: (() => { try { return new URL(url).protocol.replace(':', ''); } catch { return ''; } })(),
      host,
      path,
      type: params.type || params.initiator?.type || null,
      initiator: params.initiator?.url || params.initiator?.type || null,
      request: {
        headers: req.headers || {},
        cookies: this._cookieHeader(req.headers),
        postData: req.postData || null,
      },
      response: null,
      status: null,
      durationMs: null,
      failed: false,
    });
    if (st.buffer.size >= this.flushAt) this._flushToDisk(profileId);
  }

  _onResponse(profileId, params) {
    const resp = params.response;
    if (!resp) return;
    const st = this._state.get(profileId);
    if (!st) return;
    st.buffer.patch((r) => r.requestId === params.requestId, {
      response: {
        status: resp.status,
        statusText: resp.statusText,
        headers: resp.headers || {},
        mimeType: resp.mimeType,
        protocol: resp.protocol,
        remoteIPAddress: resp.remoteIPAddress,
        remotePort: resp.remotePort,
        fromDiskCache: !!resp.fromDiskCache,
        fromServiceWorker: !!resp.fromServiceWorker,
      },
      status: resp.status,
    });
  }

  _onFinished(profileId, params) {
    const st = this._state.get(profileId);
    if (!st) return;
    // Best-effort: mark finished. Body fetch is expensive — only for 'small' mode.
    const entry = st.buffer.patch((r) => r.requestId === params.requestId, { finishedAt: _nowIso() });
    if (!entry) return;
    const startedAt = entry._startedAtMs ?? (entry._startedAtMs = Date.parse(entry.capturedAt));
    entry.durationMs = Date.now() - startedAt;

    if (this.bodyMode === 'small' && entry.response?.mimeType) {
      const mime = String(entry.response.mimeType).toLowerCase();
      if (mime.includes('json') || mime.includes('text') || mime.includes('xml')) {
        // We do not await body fetch — fire-and-forget.
        this._maybeFetchBody(profileId, params.requestId, entry).catch(() => {});
      }
    }
  }

  _onFailed(profileId, params) {
    const st = this._state.get(profileId);
    if (!st) return;
    st.buffer.patch((r) => r.requestId === params.requestId, {
      failed: true,
      failure: { errorText: params.errorText, canceled: params.canceled, blockedReason: params.blockedReason },
      status: null,
      finishedAt: _nowIso(),
    });
  }

  async _maybeFetchBody(_profileId, requestId, entry) {
    // Hook for future: store session reference, fetch body via Network.getResponseBody.
    // We don't have a stable session reference in this design; left as a no-op stub
    // so the API surface is stable.
  }

  _cookieHeader(headers) {
    if (!headers) return null;
    if (headers.cookie) return headers.cookie;
    if (headers.Cookie) return headers.Cookie;
    return null;
  }

  _flushToDisk(profileId) {
    const st = this._state.get(profileId);
    if (!st || st.buffer.size === 0) return 0;
    const dir = _ensureDir(_sessionDir(profileId));
    const file = path.join(dir, 'requests.jsonl');
    const lines = st.buffer.list().map((r) => JSON.stringify(r)).join('\n') + '\n';
    try {
      fs.writeFileSync(file, lines, 'utf-8');
      this.logger.info({ profileId, file, count: st.buffer.size }, 'networkCapture flushed to disk');
      this.emit('flushed', { profileId, file, count: st.buffer.size });
      return st.buffer.size;
    } catch (err) {
      this.logger.error({ profileId, err: err.message }, 'networkCapture flush failed');
      return 0;
    }
  }

  _toHarEntry(r) {
    /** @type {any} */
    const e = {
      startedDateTime: r.capturedAt,
      time: r.durationMs || 0,
      request: {
        method: r.method,
        url: r.url,
        httpVersion: 'HTTP/1.1',
        cookies: this._harCookies(r.request?.cookies),
        headers: this._harHeaders(r.request?.headers),
        queryString: this._harQuery(r.url),
        postData: r.request?.postData
          ? { mimeType: r.response?.mimeType || 'application/octet-stream', text: r.request.postData }
          : undefined,
        headersSize: -1,
        bodySize: r.request?.postData ? r.request.postData.length : 0,
      },
      response: {
        status: r.status || 0,
        statusText: r.response?.statusText || '',
        httpVersion: r.response?.protocol || 'HTTP/1.1',
        cookies: [],
        headers: this._harHeaders(r.response?.headers),
        content: { size: 0, mimeType: r.response?.mimeType || 'application/octet-stream' },
        redirectURL: r.response?.headers?.location || '',
        headersSize: -1,
        bodySize: 0,
      },
      cacheControl: { beforeRequest: null, afterRequest: null },
      timings: { send: 0, wait: r.durationMs || 0, receive: 0 },
      serverIPAddress: r.response?.remoteIPAddress,
      connection: r.response?.remotePort ? String(r.response.remotePort) : undefined,
      _failure: r.failure || undefined,
      _failed: r.failed || undefined,
      _requestId: r.requestId,
      _n: r.n,
    };
    return e;
  }

  _harHeaders(headers) {
    if (!headers) return [];
    return Object.entries(headers).map(([name, value]) => ({ name, value: String(value) }));
  }

  _harCookies(cookieHeader) {
    if (!cookieHeader) return [];
    return String(cookieHeader).split(/;\s*/).filter(Boolean).map((kv) => {
      const eq = kv.indexOf('=');
      const name = eq >= 0 ? kv.slice(0, eq) : kv;
      const value = eq >= 0 ? kv.slice(eq + 1) : '';
      return { name, value };
    });
  }

  _harQuery(url) {
    try {
      const u = new URL(url);
      return Array.from(u.searchParams.entries()).map(([name, value]) => ({ name, value }));
    } catch { return []; }
  }
}
