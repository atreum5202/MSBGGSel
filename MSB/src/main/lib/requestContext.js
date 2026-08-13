// Request-context propagation (X-Request-Id → logger → service).
//
// Goal: every log line emitted while handling a request carries the same
// `requestId` so a user pasting one ID into a log search finds the entire
// chain (API → service → browser process). Also surfaces the ID back to
// the client in `X-Request-Id` response header (already done by envelope.js
// for morelogin format, but here we set it unconditionally for *all*
// responses so every client gets correlation out of the box).
//
// Implementation: use Fastify's per-request `req.id` (Fastify already
// generates one). The pre-handler hook attaches it to a `requestContext`
// AsyncLocalStorage so logger calls anywhere in the stack pick it up via
// `requestContext.get()`.

import { AsyncLocalStorage } from 'node:async_hooks';

const storage = new AsyncLocalStorage();

/**
 * Run a callback inside a request context. The context is automatically
 * carried across awaits because of AsyncLocalStorage.
 */
export function runWithRequestContext(ctx, fn) {
  return storage.run(ctx, fn);
}

/**
 * Get the current request context, or `null` if outside any request.
 */
export function getRequestContext() {
  return storage.getStore() || null;
}

/**
 * Convenience: returns just the requestId, or a placeholder outside requests.
 */
export function getRequestIdOrAnon() {
  const ctx = storage.getStore();
  return ctx?.requestId || 'no-req';
}

export const REQUEST_ID_HEADER = 'x-request-id';
export const REQUEST_ID_ALT_HEADERS = ['x-mlb-request-id', 'x-msb-request-id', 'x-ml-request-id'];

/**
 * Extract a request id from the inbound headers, falling back to Fastify's
 * auto-generated `req.id` (uuid v4) when nothing is provided.
 */
export function resolveRequestId(req) {
  const h = req?.headers || {};
  for (const name of [REQUEST_ID_HEADER, ...REQUEST_ID_ALT_HEADERS]) {
    const v = h[name];
    if (typeof v === 'string' && v.trim().length > 0 && v.length <= 200) return v.trim();
  }
  return req?.id || `anon_${Date.now().toString(36)}`;
}
