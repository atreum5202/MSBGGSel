import { importLegacyBulkExport } from '../../services/legacyProfileImport.js';
import { toProxyUrl } from '../../lib/proxy.js';
import { validateBody, validatePatch } from '../validate.js';

export function registerProfileRoutes({ app, profileManager, browserLauncher, cookieStore, logger }) {
  app.get('/profiles', {
    schema: {
      summary: 'List profiles',
      querystring: {
        type: 'object',
        properties: {
          group: { type: 'string', description: 'Filter by group name (exact match). Use "null" to get ungrouped profiles.' },
        },
        additionalProperties: false,
      },
    },
  }, async (req) => {
    const all = profileManager.list();
    const { group } = req.query || {};
    if (group === undefined || group === '') return all;
    if (group === 'null') return all.filter(p => !p.group);
    return all.filter(p => p.group === group);
  });

  app.get('/profiles/running', { schema: { summary: 'List running profile IDs' } }, async () => {
    const ids = browserLauncher.status().map(s => s.id);
    return { ok: true, data: ids };
  });

  app.post('/profiles', {
    schema: { summary: 'Create profile', body: { type: 'object', additionalProperties: true } },
    ...validateBody('profile-create'),
  }, async (req, reply) => {
    const created = await profileManager.create(req.body || {});
    logger?.info({ profileId: created.id, via: 'rest' }, 'profile created via API');
    reply.code(201).send(created);
  });

  app.post('/profiles/import-legacy-bulk', {
    schema: {
      summary: 'Bulk import profiles from legacy block-formatted TXT export',
      body: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
    },
  }, async (req, reply) => {
    const result = await importLegacyBulkExport(req.body.text, { profileManager, cookieStore, logger });
    logger?.info(
      { imported: result.imported.length, errors: result.errors.length, via: 'rest' },
      'legacy bulk profile import via API'
    );
    reply.code(201).send(result);
  });

  app.get('/profiles/:id', async (req, reply) => {
    const p = profileManager.get(req.params.id);
    if (!p) return reply.code(404).send({ error: 'not found' });
    return p;
  });

  app.patch('/profiles/:id', {
    schema: { summary: 'Update profile fields' },
    ...validatePatch('profile-patch'),
  }, async (req) => profileManager.update(req.params.id, req.body || {}));

  // ── Проверка прокси профиля ──────────────────────────────────────────────
  app.post('/profiles/:id/check-proxy', {
    schema: { summary: 'Check proxy connectivity for a profile' },
  }, async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });

    if (!profile.proxy) {
      return { ok: true, data: { hasProxy: false, status: 'direct', ip: null, country: null, latencyMs: null } };
    }

    const proxy = profile.proxy;
    const proxyUrl = toProxyUrl(proxy);

    const CHECK_URLS = [
      'https://api.ipify.org?format=json',
      'https://httpbin.org/ip',
      'https://ip.seeip.org/json',
    ];

    let lastErr = null;
    for (const checkUrl of CHECK_URLS) {
      try {
        const { ProxyAgent } = await import('undici');
        const agent = new ProxyAgent(proxyUrl);
        const t0 = Date.now();
        const res = await fetch(checkUrl, {
          signal: AbortSignal.timeout(10000),
          dispatcher: agent,
        });
        const latencyMs = Date.now() - t0;

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        const ip = json.ip || json.origin || null;

        // Определяем страну по IP через второй запрос (не через прокси — быстро)
        let country = null;
        let city = null;
        try {
          const geoRes = await fetch(`https://ip-api.com/json/${ip}?fields=country,city,countryCode`, {
            signal: AbortSignal.timeout(5000),
          });
          if (geoRes.ok) {
            const geo = await geoRes.json();
            country = geo.country || null;
            city = geo.city || null;
          }
        } catch { /* geo не критично */ }

        logger?.info({ profileId: req.params.id, ip, country, latencyMs }, 'proxy check ok');
        return {
          ok: true,
          data: {
            hasProxy: true,
            status: 'ok',
            ip,
            country,
            city,
            latencyMs,
            proxyLabel: `${proxy.protocol}://${proxy.host}:${proxy.port}`,
          },
        };
      } catch (err) {
        lastErr = err;
      }
    }

    logger?.warn({ profileId: req.params.id, err: lastErr?.message }, 'proxy check failed');
    return {
      ok: true,
      data: {
        hasProxy: true,
        status: 'error',
        ip: null,
        country: null,
        city: null,
        latencyMs: null,
        error: lastErr?.message || 'Connection failed',
        proxyLabel: `${proxy.protocol}://${proxy.host}:${proxy.port}`,
      },
    };
  });

  app.post('/profiles/bulk-delete', {
    schema: {
      summary: 'Bulk delete profiles',
      body: { type: 'object', properties: { ids: { type: 'array', items: { type: 'string' } } }, required: ['ids'] },
    },
  }, async (req, reply) => {
    const { ids = [] } = req.body;
    let deleted = 0;
    let errors = 0;
    for (const id of ids) {
      try {
        if (browserLauncher.isRunning(id)) await browserLauncher.stop(id);
        const ok = await profileManager.remove(id);
        if (ok) {
          deleted++;
          logger?.info({ profileId: id, via: 'rest' }, 'profile bulk-deleted via API');
        } else {
          errors++;
        }
      } catch (e) {
        errors++;
        logger?.warn({ profileId: id, err: e.message }, 'bulk-delete: error deleting profile');
      }
    }
    reply.code(200).send({ deleted, errors });
  });

  app.delete('/profiles/:id', async (req, reply) => {
    if (browserLauncher.isRunning(req.params.id)) await browserLauncher.stop(req.params.id);
    const ok = await profileManager.remove(req.params.id);
    if (ok) logger?.info({ profileId: req.params.id, via: 'rest' }, 'profile deleted via API');
    reply.code(ok ? 200 : 404).send(ok ? { deleted: true } : { ok: false, error: 'not found' });
  });

  // ─── Recycle bin endpoints ───────────────────────────────────────────────
  // Soft-delete: POST /profiles/:id/trash  → moves profile to <profilesDir>/.trash/<id>
  //   Retention: 7 days. UI should expose this with a "Restore" affordance.
  //   We do NOT auto-purge here; the API server runs purgeExpired() on a timer.

  app.post('/profiles/:id/trash', {
    schema: {
      summary: 'Soft-delete a profile (recycle bin, recoverable for 7 days)',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req, reply) => {
    if (browserLauncher.isRunning(req.params.id)) await browserLauncher.stop(req.params.id);
    const result = await profileManager.trash(req.params.id);
    if (!result) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'profile trashed via API');
    reply.code(200).send({ ok: true, data: result });
  });

  // GET /profiles/trash  → list trashed profiles
  app.get('/profiles/trash', {
    schema: { summary: 'List profiles in the recycle bin' },
  }, async () => {
    const items = await profileManager.listTrash();
    return { ok: true, data: items };
  });

  // POST /profiles/trash/:id/restore
  app.post('/profiles/trash/:id/restore', {
    schema: { summary: 'Restore a profile from the recycle bin' },
  }, async (req, reply) => {
    try {
      const ok = await profileManager.restore(req.params.id);
      if (!ok) return reply.code(404).send({ ok: false, error: 'Trash entry not found' });
      logger?.info({ profileId: req.params.id, via: 'rest' }, 'profile restored from trash via API');
      reply.code(200).send({ ok: true, restored: true });
    } catch (err) {
      if (err.statusCode === 409) return reply.code(409).send({ ok: false, error: err.message });
      throw err;
    }
  });

  // DELETE /profiles/trash/:id  → hard delete (purge)
  app.delete('/profiles/trash/:id', {
    schema: { summary: 'Permanently delete a profile from the recycle bin' },
  }, async (req, reply) => {
    const ok = await profileManager.purge(req.params.id);
    if (!ok) return reply.code(404).send({ ok: false, error: 'Trash entry not found' });
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'profile purged from trash via API');
    reply.code(200).send({ ok: true, purged: true });
  });

  // POST /profiles/trash/purge-expired  → maintenance endpoint
  app.post('/profiles/trash/purge-expired', {
    schema: { summary: 'Hard-delete every trash item past the 7-day retention window' },
  }, async () => {
    const result = await profileManager.purgeExpired();
    return { ok: true, data: result };
  });
}
