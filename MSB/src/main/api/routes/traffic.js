/**
 * traffic.js — REST роуты для управления перехватом трафика
 *
 * POST   /profiles/:id/traffic/start      — запустить перехват
 * POST   /profiles/:id/traffic/stop       — остановить перехват
 * GET    /profiles/:id/traffic/status     — статус текущей сессии
 * GET    /profiles/:id/traffic/captures   — список записанных сессий
 * GET    /traffic/sessions                — все активные сессии
 */

export function registerTrafficRoutes({ app, trafficCapture, logger }) {
  if (!trafficCapture) {
    logger?.warn('TrafficCaptureService not provided — /traffic routes disabled');
    return;
  }

  // ── POST /profiles/:id/traffic/start ──────────────────────────────────────
  app.post('/profiles/:id/traffic/start', {
    schema: {
      summary: 'Start traffic capture (mitmproxy) for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
      body: {
        type: 'object',
        properties: {
          saveFlow:   { type: 'boolean', default: true,  description: 'Write .mitm flow file' },
          saveHar:    { type: 'boolean', default: false, description: 'Write .har file (human-readable)' },
          filterHost: { type: 'string',  description: 'Only capture traffic to this host' },
        },
      },
    },
  }, async (req, reply) => {
    try {
      const info = await trafficCapture.start(req.params.id, req.body || {});
      return { ok: true, data: info };
    } catch (err) {
      return reply.code(err.statusCode || 500).send({ ok: false, error: err.message });
    }
  });

  // ── POST /profiles/:id/traffic/stop ───────────────────────────────────────
  app.post('/profiles/:id/traffic/stop', {
    schema: {
      summary: 'Stop traffic capture for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req, reply) => {
    try {
      const result = await trafficCapture.stop(req.params.id);
      return { ok: true, data: result };
    } catch (err) {
      return reply.code(err.statusCode || 500).send({ ok: false, error: err.message });
    }
  });

  // ── GET /profiles/:id/traffic/status ─────────────────────────────────────
  app.get('/profiles/:id/traffic/status', {
    schema: {
      summary: 'Get traffic capture status for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req) => {
    const status = trafficCapture.status(req.params.id);
    return { ok: true, data: status };
  });

  // ── GET /profiles/:id/traffic/captures ────────────────────────────────────
  app.get('/profiles/:id/traffic/captures', {
    schema: {
      summary: 'List recorded capture sessions for a profile',
      params: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    },
  }, async (req) => {
    const sessions = trafficCapture.listCaptures(req.params.id);
    return { ok: true, data: sessions };
  });

  // ── GET /traffic/sessions ─────────────────────────────────────────────────
  app.get('/traffic/sessions', {
    schema: { summary: 'List all currently active capture sessions' },
  }, async () => {
    const sessions = trafficCapture.activeSessions();
    return { ok: true, data: sessions };
  });

  logger?.info('traffic capture routes registered');
}
