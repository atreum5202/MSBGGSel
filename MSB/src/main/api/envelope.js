// Response envelope helpers.
// Two compatible formats:
//   - MSB native (default):  { ok: true, data, error? } | { ok: false, error }
//   - MoreLogin-compatible:  { code, msg, data, requestId }   (code 0 = success)
//
// Client can opt into MoreLogin format by either:
//   - Query string:    ?format=morelogin
//   - Header:          X-MSB-Format: morelogin
//   - Accept:          application/vnd.msb+morelogin  (or vnd.morelogin+json)
//
// The default keeps every existing UI / automation client working.

import { randomUUID } from 'node:crypto';

export const MORE_LOGIN_FORMAT = 'morelogin';

export function wantsMoreLoginFormat(req) {
  if (!req) return false;
  const q = req.query;
  if (q && (q.format === MORE_LOGIN_FORMAT || q.envelope === MORE_LOGIN_FORMAT)) return true;
  const h = req.headers || {};
  const xfmt = (h['x-msb-format'] || h['x-ml-format'] || '').toLowerCase();
  if (xfmt === MORE_LOGIN_FORMAT) return true;
  const accept = (h['accept'] || '').toLowerCase();
  if (accept.includes('vnd.msb+morelogin') || accept.includes('vnd.morelogin+json')) return true;
  return false;
}

/**
 * Build a requestId for the MoreLogin-shaped envelope.
 * Prefers inbound X-Request-Id / X-Ml-Request-Id so external automation can correlate logs.
 */
export function getRequestId(req) {
  if (!req) return randomUUID();
  const h = req.headers || {};
  return (
    h['x-request-id'] ||
    h['x-ml-request-id'] ||
    h['x-msb-request-id'] ||
    `req_${Date.now().toString(36)}_${randomUUID().slice(0, 8)}`
  );
}

/**
 * Standard MSB success envelope.
 *  { ok: true, data: <payload> }
 */
export function ok(payload = null) {
  return { ok: true, data: payload };
}

/**
 * Standard MSB error envelope.
 *  { ok: false, error: <message> }
 */
export function fail(message, statusCode = 400) {
  const err = new Error(message);
  err.statusCode = statusCode;
  return err;
}

/**
 * Translate a Fastify reply (already serialized payload) into MoreLogin-shaped envelope.
 * Only called when the client explicitly asked for the morelogin format.
 *
 * - For 2xx with our { ok: true, data } → { code: 0, msg: null, data, requestId }
 * - For 4xx/5xx with our { ok: false, error } → { code: <mapped>, msg: <error>, data: null, requestId }
 * - For non-enveloped payloads (legacy routes) → { code: 0, msg: null, data: payload, requestId }
 */
export function toMoreLoginEnvelope({ req, reply, payload, requestId }) {
  // Errors (4xx/5xx)
  if (reply.statusCode >= 400 || (payload && payload.ok === false)) {
    const msg =
      (payload && payload.error) ||
      (payload && payload.message) ||
      reply.statusCode >= 500
        ? 'Internal Server Error'
        : 'Request failed';
    return {
      code: mapStatusToCode(reply.statusCode),
      msg,
      data: payload && payload.data !== undefined ? payload.data : null,
      requestId,
    };
  }

  // Already in MSB envelope shape
  if (payload && typeof payload === 'object' && 'ok' in payload && 'data' in payload) {
    return {
      code: 0,
      msg: null,
      data: payload.data ?? null,
      requestId,
    };
  }

  // Raw payload (legacy route, screenshot, html, etc.)
  return {
    code: 0,
    msg: null,
    data: payload ?? null,
    requestId,
  };
}

/**
 * Map HTTP status → MoreLogin-style business code.
 * MoreLogin uses positive codes for business errors (99001, 99002…) starting at 99001.
 * We mirror that range so any client written against their docs will keep working.
 */
export function mapStatusToCode(status) {
  if (!status || status < 400) return 0;
  if (status === 400) return 99001; // invalid params
  if (status === 401) return 99002; // unauthorized
  if (status === 403) return 99003; // forbidden
  if (status === 404) return 99004; // not found
  if (status === 409) return 99005; // conflict
  if (status === 429) return 99006; // rate limited
  if (status >= 500) return 99999; // server error
  return 99000 + status;
}
