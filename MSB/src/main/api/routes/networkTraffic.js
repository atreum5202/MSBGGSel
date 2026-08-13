/**
 * networkTraffic.js — REST API для встроенного сборщика сетевых запросов.
 *
 *   GET    /profiles/:id/network/requests           — фильтрованный список
 *   GET    /profiles/:id/network/requests/:n        — одна запись по индексу
 *   GET    /profiles/:id/network/endpoints          — группировка по шаблонному пути
 *   GET    /profiles/:id/network/har                — экспорт в HAR 1.2
 *   POST   /profiles/:id/network/clear              — очистить буфер
 *   GET    /profiles/:id/network/status             — счётчики / окно
 *   GET    /network/captures                        — все активные профили
 *
 * Если сервис не зарегистрирован (старый build) — 503 с подсказкой.
 */

const BOOL_OPTS = new Set(['pretty']);

function pickOpts(req) {
  const q = req.query || {};
  const out = {};
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue;
    if (k === 'limit' || k === 'minStatus' || k === 'maxStatus' || k === 'status') {
      out[k] = Number(v);
    } else if (BOOL_OPTS.has(k)) {
      out[k] = v === '1' || v === 'true';
    } else {
      out[k] = v;
    }
  }
  return out;
}

export function registerNetworkTrafficRoutes({ app, networkCapture, logger }) {
  if (!networkCapture) {
    logger?.warn('NetworkCaptureService not provided — /networkTraffic routes disabled');
    return;
  }

  // ── GET /profiles/:id/network/requests ───────────────────────────────
  app.get('/profiles/:id/network/requests', {
    schema: {
      summary: 'List captured network requests for a profile (in-memory ring buffer)',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      querystring: {
        type: 'object',
        properties: {
          method:    { type: 'string',  description: 'Filter by HTTP method (GET, POST, ...)' },
          host:      { type: 'string',  description: 'Filter by host (exact or suffix match)' },
          path:      { type: 'string',  description: 'Filter by exact path' },
          pattern:   { type: 'string',  description: 'Regex applied to path/url' },
          status:    { type: 'integer', description: 'Filter by exact status code' },
          minStatus: { type: 'integer' },
          maxStatus: { type: 'integer' },
          since:     { type: 'string',  description: 'ISO timestamp, only requests captured after this' },
          until:     { type: 'string',  description: 'ISO timestamp, only requests captured before this' },
          limit:     { type: 'integer', description: 'Return only the last N matches' },
          pretty:    { type: 'boolean' },
        },
      },
    },
  }, async (req) => {
    const opts = pickOpts(req);
    const list = networkCapture.list(req.params.id, opts);
    return { ok: true, count: list.length, data: list };
  });

  // ── GET /profiles/:id/network/requests/:n ────────────────────────────
  app.get('/profiles/:id/network/requests/:n', {
    schema: {
      summary: 'Get one captured request by its sequence number',
      params: { type: 'object', properties: { id: { type: 'string' }, n: { type: 'integer' } }, required: ['id', 'n'] },
    },
  }, async (req, reply) => {
    const entry = networkCapture.get(req.params.id, Number(req.params.n));
    if (!entry) return reply.code(404).send({ ok: false, error: 'request not found' });
    return { ok: true, data: entry };
  });

  // ── GET /profiles/:id/network/endpoints ──────────────────────────────
  app.get('/profiles/:id/network/endpoints', {
    schema: {
      summary: 'Group captured requests by templated path (replaces numeric/uuid segments with {id}/{uuid})',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      querystring: {
        type: 'object',
        properties: {
          pattern: { type: 'string' },
          limit:   { type: 'integer' },
        },
      },
    },
  }, async (req) => {
    const opts = pickOpts(req);
    const data = networkCapture.endpoints(req.params.id, opts);
    return { ok: true, count: data.length, data };
  });

  // ── GET /profiles/:id/network/har ────────────────────────────────────
  app.get('/profiles/:id/network/har', {
    schema: {
      summary: 'Export captured network requests as a HAR 1.2 archive (JSON)',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      querystring: {
        type: 'object',
        properties: {
          method:    { type: 'string' },
          host:      { type: 'string' },
          pattern:   { type: 'string' },
          status:    { type: 'integer' },
          minStatus: { type: 'integer' },
          maxStatus: { type: 'integer' },
          since:     { type: 'string' },
          until:     { type: 'string' },
          limit:     { type: 'integer' },
        },
      },
    },
  }, async (req) => {
    const opts = pickOpts(req);
    const har = networkCapture.toHar(req.params.id, opts);
    return { ok: true, data: har };
  });

  // ── POST /profiles/:id/network/clear ─────────────────────────────────
  app.post('/profiles/:id/network/clear', {
    schema: {
      summary: 'Clear the in-memory ring buffer for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req) => {
    const r = networkCapture.clear(req.params.id);
    return { ok: true, data: r };
  });

  // ── GET /profiles/:id/network/status ─────────────────────────────────
  app.get('/profiles/:id/network/status', {
    schema: {
      summary: 'Status of the network capture for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req) => {
    return { ok: true, data: networkCapture.status(req.params.id) };
  });

  // ── GET /network/captures ────────────────────────────────────────────
  app.get('/network/captures', {
    schema: { summary: 'List all profiles currently capturing network traffic' },
  }, async () => {
    const arr = networkCapture.sessions();
    return { ok: true, count: arr.length, data: arr };
  });

  logger?.info('network traffic capture routes registered');
}
