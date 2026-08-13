import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import websocket from '@fastify/websocket';
import swagger from '@fastify/swagger';
import swaggerUI from '@fastify/swagger-ui';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DEFAULTS } from '../core/constants.js';
import { makeAuthHook, makeWsAuth } from './auth.js';
import { registerProfileRoutes } from './routes/profiles.js';
import { registerProxyRoutes } from './routes/proxies.js';
import { registerBrowserRoutes } from './routes/browser.js';
import { registerCookieRoutes } from './routes/cookies.js';
import { registerStatsRoutes } from './routes/stats.js';
import { registerLogsRoutes } from './routes/logs.js';
import { registerStatusRoutes } from './routes/status.js';
import { registerAgentRoutes } from './routes/agents.js';
import { registerShutdownRoutes } from './routes/shutdown.js';
import { registerExtensionRoutes } from './routes/extensions.js';
import { registerAnthropicAdapter } from './routes/anthropicAdapter.js';
import { registerWarmerRoutes } from './routes/warmer.js';
import { registerNetworkRoutes } from './routes/network.js';
import { registerTrafficRoutes } from './routes/traffic.js';
import { registerNetworkTrafficRoutes } from './routes/networkTraffic.js';
import { registerAutomationRoutes } from './routes/automation.js';
import { registerCrawlerRoutes } from './routes/crawler.js';
import { registerStorageRoutes } from './routes/storage.js';
import { registerAuditRoutes } from './routes/audit.js';
import { registerGroupRoutes } from './routes/groups.js';
import { registerMonitoringRoutes } from './routes/monitoring.js';
import { registerHealthRoutes } from './routes/health.js';
import { registerWorkspaceRoutes } from './routes/workspace.js';
import { auditLogger } from '../lib/auditLogger.js';
import { createRateLimiter } from '../lib/rateLimiter.js';
import {
  wantsMoreLoginFormat,
  getRequestId,
  toMoreLoginEnvelope,
} from './envelope.js';
import { resolveRequestId, runWithRequestContext } from '../lib/requestContext.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const RENDERER_DIST = path.resolve(__dirname, '..', '..', '..', 'dist', 'renderer');

export async function startApiServer({
  port = Number(process.env.MSB_API_PORT || DEFAULTS.API_PORT),
  host = DEFAULTS.API_HOST,
  token = process.env.MSB_API_TOKEN || null,
  profileManager,
  browserLauncher,
  logBroker,
  statistics,
  cookieStore,
  profilesDir,
  logger,
  shutdownController = null,
  commonExtensionsManager = null,
  getMainWindow = null,
  showWindow = null,
  proxyStore,
  trafficCapture = null,
  networkCapture = null,
  automation = null,
  crawler = null,
}) {
  const app = Fastify({ logger: false, bodyLimit: DEFAULTS.API_BODY_LIMIT });
  
  // Initialize audit logger
  await auditLogger.init();
  
  // Create rate limiter
  // 60 req/min per (IP + token) bucket — matches MoreLogin Local API limit.
  // Disable with MSB_RATE_LIMIT=0 (unlimited).
  const rateLimitMax = Number(process.env.MSB_RATE_LIMIT ?? DEFAULTS.RATE_LIMIT_MAX_REQUESTS);
  const rateLimiter = rateLimitMax > 0
    ? createRateLimiter({
        windowMs: DEFAULTS.RATE_LIMIT_WINDOW_MS,
        maxRequests: rateLimitMax,
      })
    : null;
  if (rateLimiter) {
    // Mount AFTER auth so the rate-limit bucket includes the bearer token in
    // the key (different clients have separate quotas). 429 with the standard
    // envelope + X-RateLimit-* headers.
    app.addHook('onRequest', async (req, reply) => {
      const result = rateLimiter.isAllowed(req);
      reply.header('X-RateLimit-Limit', rateLimitMax);
      reply.header('X-RateLimit-Remaining', result.remaining);
      if (result.resetAt) reply.header('X-RateLimit-Reset', result.resetAt);
      if (!result.allowed) {
        const err = new Error(`Rate limit exceeded: ${rateLimitMax} req/${Math.round(DEFAULTS.RATE_LIMIT_WINDOW_MS / 1000)}s`);
        err.statusCode = 429;
        err.headers = { 'Retry-After': Math.ceil((result.resetAt - Date.now()) / 1000) };
        throw err;
      }
    });
  }

  await app.register(swagger, {
    openapi: {
      info: {
        title: 'MyStealthBrowser API',
        version: '1.2.0',
        description: 'Local control-plane for MyStealthBrowser profiles.',
      },
      servers: [{ url: `http://${host}:${port}` }],
      components: token ? { securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } } } : undefined,
      security: token ? [{ bearerAuth: [] }] : undefined,
    },
  });
  await app.register(swaggerUI, { routePrefix: '/docs', uiConfig: { deepLinking: true } });
  await app.register(websocket, { options: { maxPayload: DEFAULTS.WS_MAX_PAYLOAD } });

  try {
    await app.register(fastifyStatic, {
      root: RENDERER_DIST,
      prefix: '/ui/',
      decorateReply: false,
    });
  } catch (err) {
    logger.warn({ err: err.message, dir: RENDERER_DIST }, 'dashboard (dist/renderer) not found — /ui disabled until `npm run build:renderer`');
  }

  app.addHook('onRequest', makeAuthHook(token));

  // Request-id propagation: every request gets an id (inbound X-Request-Id
  // header or auto-generated), it's echoed in the response, and a
  // per-request context is set up so child loggers can attach it.
  app.addHook('onRequest', (req, _reply, done) => {
    const requestId = resolveRequestId(req);
    req.requestId = requestId;
    // We don't reply.send here — onSend hook adds the header. (See below.)
    done();
  });
  app.addHook('onSend', async (req, reply, payload) => {
    if (req.requestId) reply.header('X-Request-Id', req.requestId);
    return payload;
  });
  // Wrap every request in a context. AsyncLocalStorage carries it through
  // service calls and into logger bindings without manual plumbing.
  app.addHook('preHandler', (req, _reply, done) => {
    runWithRequestContext({ requestId: req.requestId, method: req.method, url: req.url }, () => done());
  });

  app.addHook('preSerialization', async (req, reply, payload) => {
    // ── 1) Normalise into MSB envelope: { ok, data } or { ok:false, error } ─────
    if (reply.statusCode >= 400) {
      // Pass errors through; the errorHandler has already shaped them.
    } else if (
      payload &&
      typeof payload === 'object' &&
      'ok' in payload &&
      ('data' in payload || 'error' in payload)
    ) {
      // Already MSB-shaped.
    } else {
      payload = { ok: true, data: payload ?? null };
    }

    // ── 2) Opt-in: re-shape into MoreLogin-compatible envelope ─────────────────
    //    { code, msg, data, requestId }  — only when client asks.
    if (wantsMoreLoginFormat(req)) {
      const requestId = getRequestId(req);
      reply.header('X-Request-Id', requestId);
      reply.header('X-MSB-Format', 'morelogin');
      return toMoreLoginEnvelope({ req, reply, payload, requestId });
    }
    return payload;
  });
  app.setErrorHandler((err, _req, reply) => {
    logger.error({ err: err.message, stack: err.stack }, 'api error');
    reply.code(err.statusCode || 500).send({ ok: false, error: err.message });
  });

  const ctx = { app, profileManager, browserLauncher, logBroker, statistics, cookieStore, profilesDir, logger, commonExtensionsManager, proxyStore, trafficCapture, networkCapture, automation, crawler };
  app.addHook('onRequest', (req, _reply, done) => {
    logger.debug({ method: req.method, url: req.url }, 'request');
    done();
  });

  app.get('/ui-config', { schema: { summary: 'Public UI config (token for dashboard)' } }, async () => ({
    token: token || null,
  }));

  // Эндпоинт для показа окна (вызывается повторным запуском silent_run.vbs)
  app.post('/api/show-window', { schema: { summary: 'Show the main Electron window' } }, async () => {
    if (showWindow) showWindow();
    return { ok: true };
  });

  registerHealthRoutes(ctx);
  registerProfileRoutes(ctx);
  registerProxyRoutes(ctx);
  registerBrowserRoutes(ctx);
  registerCookieRoutes(ctx);
  registerStatsRoutes(ctx);
  registerLogsRoutes(ctx, makeWsAuth(token));
  registerStatusRoutes(ctx, makeWsAuth(token));
  registerAgentRoutes(ctx);
  registerExtensionRoutes(ctx);
  registerAnthropicAdapter(ctx);
  registerWarmerRoutes(ctx);
  registerNetworkRoutes(ctx);
  registerTrafficRoutes(ctx);
  registerNetworkTrafficRoutes(ctx);
  registerAutomationRoutes(ctx);
  registerCrawlerRoutes(ctx);
  registerStorageRoutes(ctx);
  registerAuditRoutes({ app, logger });
  registerGroupRoutes(ctx);
  registerMonitoringRoutes(ctx);
  registerWorkspaceRoutes(ctx);
  if (shutdownController) {
    registerShutdownRoutes({ app, shutdownController, logger });
  }

  await app.ready();
  await app.listen({ port, host });
  logger.info({ port, host, docs: `http://${host}:${port}/docs`, ui: `http://${host}:${port}/ui/`, ws: `ws://${host}:${port}/ws/logs`, wsStatus: `ws://${host}:${port}/ws/status` }, 'REST API listening');

  // ── Recycle-bin retention sweep ─────────────────────────────────────────
  // Run once at boot, then every 6 hours. Hard-deletes any trash entries
  // older than the retention window (default 7 days, MoreLogin parity).
  let trashTimer = null;
  if (profileManager?.purgeExpired) {
    const SIX_HOURS = 6 * 60 * 60 * 1000;
    const sweep = async () => {
      try {
        const r = await profileManager.purgeExpired();
        if (r?.purged) logger.info(r, 'recycle-bin retention sweep');
      } catch (err) {
        logger.warn({ err: err.message }, 'recycle-bin sweep failed (non-fatal)');
      }
    };
    // Fire-and-forget first run; ignore failure on boot.
    setTimeout(sweep, 5000).unref?.();
    trashTimer = setInterval(sweep, SIX_HOURS);
    trashTimer.unref?.();
  }

  return {
    fastify: app,
    async close() {
      if (trashTimer) clearInterval(trashTimer);
      if (rateLimiter) rateLimiter.destroy?.();
      await app.close();
    },
  };
}
