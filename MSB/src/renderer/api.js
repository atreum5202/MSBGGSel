let _apiToken = null;
let _tokenReady = false;
let _tokenPromise = null;

async function _initToken() {
  if (_tokenReady) return;
  if (_tokenPromise) return _tokenPromise;
  _tokenPromise = fetch('/ui-config')
    .then((r) => r.json())
    .then((cfg) => {

      const payload = (cfg && cfg.data !== undefined) ? cfg.data : cfg;
      _apiToken = payload && payload.token ? payload.token : null;
      _tokenReady = true;
    })
    .catch(() => {

      _apiToken = null;
      _tokenReady = true;
    });
  return _tokenPromise;
}

_initToken();

async function request(method, path, body) {

  await _initToken();

  const opts = { method, headers: {} };
  if (_apiToken) {
    opts.headers['Authorization'] = `Bearer ${_apiToken}`;
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let json = null;
  const text = await res.text();
  if (text) {
    try { json = JSON.parse(text); } catch {  }
  }
  if (!res.ok) {
    throw new Error((json && json.error) || `HTTP ${res.status}`);
  }
  if (json && typeof json === 'object' && 'ok' in json) {
    if (json.ok === false) throw new Error(json.error || 'Request failed');
    return Object.prototype.hasOwnProperty.call(json, 'data') ? json.data : json;
  }
  return json;
}

export const api = {
  proxies: {
    list: ()              => request('GET', '/proxies'),
    add: (data)           => request('POST', '/proxies', data),
    bulk: (lines)         => request('POST', '/proxies/bulk', { lines }),
    remove: (id)          => request('DELETE', `/proxies/${id}`),
    assign: (id, pid)     => request('POST', `/proxies/${id}/assign/${pid}`),
  },
  profiles: {
    list: () => request('GET', '/profiles'),
    get: (id) => request('GET', `/profiles/${encodeURIComponent(id)}`),
    create: (data) => request('POST', '/profiles', data),
    update: (id, patch) => request('PATCH', `/profiles/${encodeURIComponent(id)}`, patch),
    remove: (id) => request('DELETE', `/profiles/${encodeURIComponent(id)}`),
    importLegacyBulk: (text) => request('POST', '/profiles/import-legacy-bulk', { text }),
    bulkRemove: (ids) => request('POST', '/profiles/bulk-delete', { ids }),
    checkProxy: (id) => request('POST', `/profiles/${encodeURIComponent(id)}/check-proxy`),
  },
  extensions: {
    list:        ()              => request('GET',    '/extensions'),
    add:         (path)          => request('POST',   '/extensions', { path }),
    remove:      (path)          => request('DELETE', '/extensions', { path }),
    clear:       ()              => request('DELETE', '/extensions/all'),
    pickFolder:  ()              => window.msb?.extensions?.pickFolder?.() ?? Promise.resolve({ canceled: true, error: 'not in Electron' }),
    installCrx:  (name, data)    => window.msb?.extensions?.installCrx
      ? window.msb.extensions.installCrx(name, data)
      : request('POST', '/extensions/install-crx', { name, data }),
    addTampermonkey: ()          => window.msb?.extensions?.addTampermonkey
      ? window.msb.extensions.addTampermonkey()
      : request('POST', '/extensions/add-tampermonkey'),
  },
  browser: {
    status: () => request('GET', '/browser/status'),
    start: (id, options) => request('POST', `/profiles/${encodeURIComponent(id)}/start`, options || {}),
    stop: (id) => request('POST', `/profiles/${encodeURIComponent(id)}/stop`),
  },
  groups: {
    list:           ()                    => request('GET',   '/groups'),
    sessionsSummary: ()                   => request('GET',   '/groups/sessions-summary'),
    create:         (name)                => request('POST',  '/groups', { name }),
    rename:         (oldName, newName)    => request('PATCH', `/groups/${encodeURIComponent(oldName)}`, { name: newName }),
    delete:         (name)                => request('DELETE',`/groups/${encodeURIComponent(name)}`),
    addProfile:     (groupName, profileId)=> request('POST',  `/groups/${encodeURIComponent(groupName)}/profiles/${encodeURIComponent(profileId)}`),
    bulkMove:       (groupName, profileIds)=> request('POST', `/groups/${encodeURIComponent(groupName)}/bulk-move`, { profileIds }),
    updateMeta:     (name, patch)         => request('PATCH', `/groups/${encodeURIComponent(name)}/meta`, patch),
  },
  trash: {
    list:     ()                   => request('GET',    '/profiles/trash'),
    send:     (id)                 => request('POST',   `/profiles/${encodeURIComponent(id)}/trash`),
    restore:  (id)                 => request('POST',   `/profiles/trash/${encodeURIComponent(id)}/restore`),
    purge:    (id)                 => request('DELETE', `/profiles/trash/${encodeURIComponent(id)}`),
    sweep:    ()                   => request('POST',   '/profiles/trash/purge-expired'),
  },
  traffic: {
    start:        (id, opts = {})   => request('POST',   `/profiles/${encodeURIComponent(id)}/traffic/start`, opts),
    stop:         (id)             => request('POST',   `/profiles/${encodeURIComponent(id)}/traffic/stop`),
    status:       (id)             => request('GET',    `/profiles/${encodeURIComponent(id)}/traffic/status`),
    listCaptures: (id)             => request('GET',    `/profiles/${encodeURIComponent(id)}/traffic/captures`),
    sessions:     ()               => request('GET',    '/traffic/sessions'),
  },
  network: {
    status:    (id)                => request('GET',    `/profiles/${encodeURIComponent(id)}/network/status`),
    endpoints: (id, q = {})        => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
      const s = sp.toString();
      return request('GET', `/profiles/${encodeURIComponent(id)}/network/endpoints${s ? `?${s}` : ''}`);
    },
    requests:  (id, q = {})        => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
      const s = sp.toString();
      return request('GET', `/profiles/${encodeURIComponent(id)}/network/requests${s ? `?${s}` : ''}`);
    },
    request:   (id, n)             => request('GET',    `/profiles/${encodeURIComponent(id)}/network/requests/${n}`),
    har:       (id, q = {})        => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
      const s = sp.toString();
      return request('GET', `/profiles/${encodeURIComponent(id)}/network/har${s ? `?${s}` : ''}`);
    },
    clear:     (id)                => request('POST',   `/profiles/${encodeURIComponent(id)}/network/clear`),
    sessions:  ()                  => request('GET',    '/network/captures'),
  },
  scrapers: {
    list:       ()    => window.msb?.scrapers?.list?.()     ?? Promise.reject(new Error('not in Electron')),
    get:        (id)  => window.msb?.scrapers?.get?.(id)    ?? Promise.reject(new Error('not in Electron')),
    readText:   (rel) => window.msb?.scrapers?.readText?.(rel) ?? Promise.reject(new Error('not in Electron')),
    readJsonl:  (id, file, limit) => window.msb?.scrapers?.readJsonl?.(id, file, limit) ?? Promise.reject(new Error('not in Electron')),
    openPath:   (rel) => window.msb?.scrapers?.openPath?.(rel) ?? Promise.reject(new Error('not in Electron')),
    run:        (id, command) => window.msb?.scrapers?.run?.(id, command) ?? Promise.reject(new Error('not in Electron')),
    readOutput: (logFile, offset) => window.msb?.scrapers?.readOutput?.(logFile, offset) ?? Promise.reject(new Error('not in Electron')),
    kill:       (id)  => window.msb?.scrapers?.kill?.(id)   ?? Promise.reject(new Error('not in Electron')),
  },
  automation: {
    profileCreate: (spec)         => request('POST', '/automation/profile/create', spec),
    profileStart:  (profileId, opts = {}) => request('POST', '/automation/profile/start', { profileId, ...opts }),
    profileStop:   (profileId)     => request('POST', '/automation/profile/stop',  { profileId }),
    pipelineRun:   (spec)          => request('POST', '/automation/pipeline/run',  spec),
    jobs:          ()              => request('GET',  '/automation/jobs'),
    job:           (id)            => request('GET',  `/automation/jobs/${encodeURIComponent(id)}`),
    jobLog:        (id, opts = {}) => {
      const sp = new URLSearchParams();
      if (opts.sinceTs) sp.set('sinceTs', String(opts.sinceTs));
      if (opts.limit)   sp.set('limit',   String(opts.limit));
      const s = sp.toString();
      return request('GET', `/automation/jobs/${encodeURIComponent(id)}/log${s ? `?${s}` : ''}`);
    },
    crawlerCapabilities: () => request('GET', '/automation/crawl/capabilities'),
    crawlerStart:       (spec) => request('POST', '/automation/crawl/start', spec),
    crawlerJobs:        () => request('GET', '/automation/crawl/jobs'),
    crawlerJob:         (id) => request('GET', `/automation/crawl/jobs/${encodeURIComponent(id)}`),
    crawlerJobLog:      (id, opts = {}) => {
      const sp = new URLSearchParams();
      if (opts.sinceTs) sp.set('sinceTs', String(opts.sinceTs));
      if (opts.limit)   sp.set('limit',   String(opts.limit));
      const s = sp.toString();
      return request('GET', `/automation/crawl/jobs/${encodeURIComponent(id)}/log${s ? `?${s}` : ''}`);
    },
    crawlerJobResults:  (id, opts = {}) => {
      const sp = new URLSearchParams();
      if (opts.limit) sp.set('limit', String(opts.limit));
      const s = sp.toString();
      return request('GET', `/automation/crawl/jobs/${encodeURIComponent(id)}/results${s ? `?${s}` : ''}`);
    },
    crawlerAbort:       (id) => request('POST', `/automation/crawl/jobs/${encodeURIComponent(id)}/abort`),
  },
  workspace: {
    launch: () => request('POST', '/workspace/launch'),
    status: () => request('GET',  '/workspace/status'),
  },
};

export function connectStatusSocket(onMessage) {
  let socket = null;
  let closedByCaller = false;
  let retryMs = 1000;

  const connect = () => {
    if (closedByCaller) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';

    const tokenParam = _apiToken ? `?token=${encodeURIComponent(_apiToken)}` : '';
    socket = new WebSocket(`${proto}//${location.host}/ws/status${tokenParam}`);

    socket.addEventListener('open', () => {
      retryMs = 1000;
    });
    socket.addEventListener('message', (ev) => {
      try {
        onMessage(JSON.parse(ev.data));
      } catch {  }
    });
    socket.addEventListener('close', () => {
      if (closedByCaller) return;
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 1.5, 10000);
    });
    socket.addEventListener('error', () => {
      try { socket.close(); } catch {  }
    });
  };

  connect();

  return () => {
    closedByCaller = true;
    try { socket && socket.close(); } catch {  }
  };
}
