import { validateBody } from '../validate.js';

const requireProfile = (app, profileManager, id) => {
  const p = profileManager.get(id);
  if (!p) throw app.httpErrors ? app.httpErrors.notFound(`Profile ${id} not found`) : new Error(`Profile ${id} not found`);
  return p;
};

// Lazy-load profile crypto so routes stay cheap when the feature is unused.
let _profileCrypto = null;
async function getCrypto() {
  if (_profileCrypto) return _profileCrypto;
  _profileCrypto = await import('../../lib/profileCrypto.js');
  return _profileCrypto;
}

/**
 * Browser lifecycle routes.
 *
 * Two compatible API surfaces:
 *   - MSB native:
 *       POST /profiles/:id/start
 *       POST /profiles/:id/stop
 *       POST /profiles/:id/refreshFingerprint       (alias for POST /profiles/:id/fingerprint/refresh)
 *   - MoreLogin-compatible (selected by ?format=morelogin or Accept header):
 *       POST /api/env/start        → maps to /profiles/:id/start with { envId | id }
 *       POST /api/env/close        → maps to /profiles/:id/stop
 *       POST /api/env/closeAll     → close every running profile
 *       POST /api/env/status       → aggregate status of running profiles
 *       POST /api/env/getAllProcessIds  → list { envId, pid }
 *       POST /api/env/getAllDebugInfo   → list { envId, debugPort, cdpEndpoint, pid }
 *       POST /api/env/arrangeWindows   → auto-tile opened profile windows
 *
 * All "start" options match the MoreLogin surface so a ported client works as-is,
 * with MSB extension fields for launch policy:
 *   { envId | id, launchMode?, headless?, isHeadless?, cdpEvasion, closeCheckIPpage, checkIPErrorHandle, encryptKey, extraArgs }
 */
export function registerBrowserRoutes({ app, profileManager, browserLauncher, logger }) {

  // ─── MSB-native routes ─────────────────────────────────────────────────────
  app.get('/browser/status', async () => browserLauncher.status());

  app.post('/profiles/:id/start', {
    schema: { summary: 'Start a browser profile' },
    ...validateBody('browser-start'),
  }, async (req) => {
    const profile = requireProfile(app, profileManager, req.params.id);
    const result = await browserLauncher.start(profile, req.body || {});
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'browser start requested via API');
    return result;
  });

  app.post('/profiles/:id/stop', async (req, reply) => {
    const ok = await browserLauncher.stop(req.params.id);
    logger?.info({ profileId: req.params.id, ok, via: 'rest' }, 'browser stop requested via API');
    reply.code(ok ? 200 : 404).send(ok ? { stopped: true } : { ok: false, error: 'not running' });
  });

  app.get('/profiles/:id/status', async (req) => {
    return browserLauncher.status().find((r) => r.id === req.params.id) || { running: false };
  });

  app.post('/profiles/:id/goto', async (req) => {
    if (!req.body?.url) throw new Error('url required');
    const result = await browserLauncher.goto(req.params.id, req.body.url);
    logger?.debug({ profileId: req.params.id, url: req.body.url, via: 'rest' }, 'goto requested via API');
    return result;
  });

  app.post('/profiles/:id/runScenario', async (req) => {
    const { scenario, params } = req.body || {};
    if (!scenario) throw new Error('scenario required');
    logger?.info({ profileId: req.params.id, scenario, via: 'rest' }, 'scenario requested via API');
    return browserLauncher.runScenario(req.params.id, scenario, params || {});
  });

  // MSB-native: /profiles/:id/refreshFingerprint
  app.post('/profiles/:id/refreshFingerprint', async (req) => {
    const result = await browserLauncher.refreshFingerprint(req.params.id, req.body?.fingerprint || null);
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'fingerprint refresh requested via API');
    return result;
  });

  // MoreLogin-style: POST /profiles/:id/fingerprint/refresh
  app.post('/profiles/:id/fingerprint/refresh', async (req) => {
    const result = await browserLauncher.refreshFingerprint(req.params.id, req.body?.fingerprint || req.body?.advancedSetting || null);
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'fingerprint refresh (morelogin) requested via API');
    return result;
  });

  app.post('/profiles/:id/switchProxy', async (req) => {
    if (req.body?.proxy === undefined) throw new Error('proxy required (null to clear)');
    const result = await browserLauncher.switchProxy(req.params.id, req.body.proxy);
    logger?.info({ profileId: req.params.id, hasProxy: !!req.body.proxy, via: 'rest' }, 'proxy switch requested via API');
    return result;
  });

  app.get('/profiles/:id/screenshot', async (req, reply) => {
    const type = req.query?.type === 'jpeg' ? 'jpeg' : 'png';
    const fullPage = req.query?.fullPage === 'true' || req.query?.fullPage === '1';
    const { buffer, mimeType } = await browserLauncher.screenshot(req.params.id, { type, fullPage });
    logger?.debug({ profileId: req.params.id, type, fullPage, via: 'rest' }, 'screenshot requested via API');
    reply.type(mimeType).send(buffer);
  });

  app.get('/profiles/:id/console-log', async (req) => {
    const limit = req.query?.limit ? Number(req.query.limit) : undefined;
    const entries = browserLauncher.getConsoleLog(req.params.id, { limit });
    logger?.debug({ profileId: req.params.id, count: entries.length, via: 'rest' }, 'console log requested via API');
    return { entries };
  });

  app.post('/profiles/:id/execute', async (req) => {
    const { commands = [], waitFor = null } = req.body || {};
    const results = await browserLauncher.executeCommands(req.params.id, commands, waitFor);
    logger?.debug({ profileId: req.params.id, commandCount: commands.length, via: 'rest' }, 'execute commands requested via API');
    return { success: true, results };
  });

  // ─── MoreLogin-compatible aliases ──────────────────────────────────────────
  // Mounted under /api/env/* so a ported client can hit them as documented.
  // Each accepts either envId (preferred) or uniqueId (profile.number).

  // POST /api/env/start
  app.post('/api/env/start', {
    schema: { summary: 'MoreLogin-compatible start alias' },
    ...validateBody('browser-start'),
  }, async (req) => {
    const { envId, uniqueId, ...rest } = req.body || {};
    const id = envId || resolveIdByNumber(profileManager, uniqueId);
    if (!id) throw new Error('envId or uniqueId required');
    const profile = requireProfile(app, profileManager, id);
    const result = await browserLauncher.start(profile, rest);
    logger?.info({ profileId: id, via: 'ml-alias' }, '/api/env/start');
    return result;
  });

  // POST /api/env/close
  app.post('/api/env/close', async (req, reply) => {
    const { envId, uniqueId } = req.body || {};
    const id = envId || resolveIdByNumber(profileManager, uniqueId);
    if (!id) throw new Error('envId or uniqueId required');
    const ok = await browserLauncher.stop(id);
    logger?.info({ profileId: id, ok, via: 'ml-alias' }, '/api/env/close');
    reply.code(ok ? 200 : 404).send(ok ? { stopped: true } : { ok: false, error: 'not running' });
  });

  // POST /api/env/closeAll
  app.post('/api/env/closeAll', async () => {
    const count = browserLauncher.status().length;
    await browserLauncher.closeAll();
    logger?.info({ count, via: 'ml-alias' }, '/api/env/closeAll');
    return { closed: count };
  });

  // POST /api/env/status  → returns MoreLogin-shaped list + ipCheck
  app.post('/api/env/status', async (req) => {
    const { envId, uniqueId } = req.body || {};
    const all = browserLauncher.status();
    if (!envId && !uniqueId) return all;
    const id = envId || resolveIdByNumber(profileManager, uniqueId);
    const item = all.find((r) => r.id === id);
    if (!item) return { running: false, envId: id };
    return item;
  });

  // POST /api/env/getAllProcessIds
  app.post('/api/env/getAllProcessIds', async () => {
    const all = browserLauncher.status();
    return all.map((r) => ({
      envId: r.id,
      pid: r.pid || null,
    }));
  });

  // POST /api/env/getAllDebugInfo
  app.post('/api/env/getAllDebugInfo', async () => {
    const all = browserLauncher.status();
    return all.map((r) => ({
      envId: r.id,
      debugPort: r.cdpPort != null ? String(r.cdpPort) : null,
      cdpEndpoint: r.cdpEndpoint,
      pid: r.pid || null,
    }));
  });

  // POST /api/env/arrangeWindows — auto-tile opened browser windows via Electron
  app.post('/api/env/arrangeWindows', async (req, reply) => {
    const running = browserLauncher.status();
    if (running.length === 0) {
      return reply.code(400).send({ ok: false, error: 'no running profiles' });
    }
    // Defer to the main process — the API server is in the main process so
    // it can call BrowserWindow APIs directly.
    const { BrowserWindow, screen } = await import('electron');
    const displays = screen.getAllDisplays();
    const primary = displays.find((d) => d.id === screen.getPrimaryDisplay().id) || displays[0];
    const { width: sw, height: sh } = primary.workAreaSize;
    const cols = Math.ceil(Math.sqrt(running.length));
    const rows = Math.ceil(running.length / cols);
    const cw = Math.floor(sw / cols);
    const rh = Math.floor(sh / rows);
    let i = 0;
    let arranged = 0;
    for (const r of running) {
      const wins = BrowserWindow.getAllWindows().filter((w) => !w.isDestroyed() && w.getTitle?.()?.includes(r.id));
      // Fallback: any window whose ownerProfileId matches; if no match,
      // we just take a slice in order (best-effort).
      const targets = wins.length ? wins : BrowserWindow.getAllWindows().filter((w) => !w.isDestroyed());
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = col * cw;
      const y = row * rh;
      for (const w of targets) {
        try {
          w.setBounds({ x, y, width: cw, height: rh });
          arranged++;
        } catch (err) {
          logger?.warn({ err: err.message }, 'arrangeWindows: setBounds failed');
        }
      }
      i++;
    }
    logger?.info({ count: running.length, arranged, via: 'ml-alias' }, '/api/env/arrangeWindows');
    return { arranged, total: running.length };
  });

  // POST /api/env/getAllScreen  → monitor layout
  app.post('/api/env/getAllScreen', async () => {
    const { screen } = await import('electron');
    return screen.getAllDisplays().map((d, idx) => ({
      index: idx,
      id: d.id,
      bounds: d.bounds,
      workArea: d.workArea,
      scaleFactor: d.scaleFactor,
      primary: d.id === screen.getPrimaryDisplay().id,
    }));
  });

  // POST /api/env/removeLocalCache  — wipe cookies/localStorage/IndexedDB without deleting the profile
  app.post('/api/env/removeLocalCache', async (req) => {
    const { envId } = req.body || {};
    if (!envId) throw new Error('envId required');
    const info = browserLauncher.getRunning(envId);
    if (!info) throw new Error(`Profile ${envId} is not running`);
    try {
      // Clear cookies only — localStorage/IndexedDB require a page visit
      // to enumerate; full reset is best handled by stop + wipe + restart.
      await info.context.clearCookies();
      logger?.info({ envId, via: 'ml-alias' }, 'cookies cleared via removeLocalCache');
      return { cleared: ['cookies'] };
    } catch (err) {
      logger?.warn({ envId, err: err.message }, 'removeLocalCache failed');
      throw err;
    }
  });

  // POST /api/env/encrypt-key  — register an encryptKey against a profile.
  //   First call: write marker + encrypts sensitive fields in meta.json
  //   Subsequent calls: validate against existing marker (key must match)
  //   { envId, encryptKey, action?: 'enable' | 'verify' | 'disable' }
  // Returns { ok, fingerprint, kind, action } or { ok:false, reason }.
  app.post('/api/env/encrypt-key', async (req) => {
    const { envId, encryptKey, action = 'enable' } = req.body || {};
    if (!envId) throw new Error('envId required');
    const profile = requireProfile(app, profileManager, envId);
    const crypto = await getCrypto();
    const userDataDir = profileManager.userDataDir(envId);

    if (action === 'verify') {
      const mat = crypto.materialise(encryptKey, { profileId: envId });
      if (!mat) return { ok: false, reason: 'invalid_key' };
      const fp = crypto.fingerprint(mat.key);
      const marker = await crypto.readMarker(userDataDir);
      return { ok: true, fingerprint: fp, kind: mat.kind, action: 'verify', matches: marker?.fingerprint === fp };
    }

    if (action === 'disable') {
      await crypto.deleteMarker(userDataDir);
      // NB: previously-encrypted sensitive fields are NOT auto-decrypted
      // because the key isn't known. Caller is responsible for re-creating
      // the profile or using a real backup procedure.
      logger?.info({ envId, via: 'ml-alias' }, 'crypto marker removed');
      return { ok: true, action: 'disabled' };
    }

    // action === 'enable' (default)
    const mat = crypto.materialise(encryptKey, { profileId: envId });
    if (!mat) return { ok: false, reason: 'invalid_key' };
    const fp = crypto.fingerprint(mat.key);
    const existing = await crypto.readMarker(userDataDir);
    if (existing) {
      if (existing.fingerprint !== fp) {
        return { ok: false, reason: 'key_mismatch', existingKind: existing.kind };
      }
      return { ok: true, action: 'already_enabled', fingerprint: fp, kind: mat.kind };
    }

    // First-time enable: encrypt sensitive fields in the in-memory profile
    // and persist via profileManager.update so meta.json gets re-written.
    const updated = crypto.encryptSensitiveFields({ ...profile }, mat.key);
    updated.crypto = { fingerprint: fp, kind: mat.kind, enabledAt: Date.now() };
    await profileManager.update(envId, updated);
    await crypto.writeMarker(userDataDir, { fingerprint: fp, kind: mat.kind });
    logger?.info({ envId, kind: mat.kind, via: 'ml-alias' }, 'crypto enabled for profile');
    return { ok: true, action: 'enabled', fingerprint: fp, kind: mat.kind };
  });
}

function resolveIdByNumber(profileManager, num) {
  if (num == null) return null;
  const n = Number(num);
  if (!Number.isFinite(n) || n <= 0) return null;
  const all = profileManager.list();
  const found = all.find((p) => p.number === n);
  return found?.id || null;
}
