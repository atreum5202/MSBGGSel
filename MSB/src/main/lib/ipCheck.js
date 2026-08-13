// IP / proxy connectivity check.
// Used by:
//   1) profiles/:id/check-proxy endpoint (standalone)
//   2) BrowserLauncher.start() when closeCheckIPpage=true
//
// MoreLogin-compatible behaviour:
//   - closeCheckIPpage = true  → run check BEFORE start; on failure honour checkIPErrorHandle
//     (1 = abort start, 2 = proceed anyway with a warning in info.ipCheck.warning)
//
// Returns the same shape regardless of caller:
//   { status: 'ok' | 'error' | 'direct',
//     ip, country, city, latencyMs,
//     error?  }

import { toProxyUrl } from './proxy.js';

const CHECK_URLS = [
  'https://api.ipify.org?format=json',
  'https://httpbin.org/ip',
  'https://ip.seeip.org/json',
];

const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Run an IP check through a proxy (or direct if proxy is null).
 * @param {object|null} proxy — normalized proxy object (or null for direct)
 * @param {object} [opts]
 * @param {number} [opts.timeoutMs=10000]
 * @param {object} [opts.logger]
 * @returns {Promise<{
 *   status: 'ok' | 'error' | 'direct',
 *   ip: string|null,
 *   country: string|null,
 *   city: string|null,
 *   countryCode: string|null,
 *   latencyMs: number|null,
 *   error: string|null,
 *   viaProxy: boolean,
 * }>}
 */
export async function checkIp(proxy, { timeoutMs = DEFAULT_TIMEOUT_MS, logger = null } = {}) {
  // No proxy → resolve via the local machine. Some sites refuse direct fetches
  // from automation servers, so we still try the first CHECK_URL and report status.
  if (!proxy) {
    const t0 = Date.now();
    try {
      const res = await fetch(CHECK_URLS[0], { signal: AbortSignal.timeout(timeoutMs) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const ip = json.ip || json.origin || null;
      const geo = ip ? await safeGeoip(ip) : { country: null, city: null, countryCode: null };
      return {
        status: 'direct',
        ip,
        country: geo.country,
        city: geo.city,
        countryCode: geo.countryCode,
        latencyMs: Date.now() - t0,
        error: null,
        viaProxy: false,
      };
    } catch (err) {
      return {
        status: 'error',
        ip: null,
        country: null,
        city: null,
        countryCode: null,
        latencyMs: null,
        error: err.message,
        viaProxy: false,
      };
    }
  }

  const proxyUrl = toProxyUrl(proxy);
  let lastErr = null;
  for (const checkUrl of CHECK_URLS) {
    try {
      const { ProxyAgent } = await import('undici');
      const agent = new ProxyAgent(proxyUrl);
      const t0 = Date.now();
      const res = await fetch(checkUrl, {
        signal: AbortSignal.timeout(timeoutMs),
        dispatcher: agent,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const ip = json.ip || json.origin || null;
      const latencyMs = Date.now() - t0;
      const geo = ip ? await safeGeoip(ip) : { country: null, city: null, countryCode: null };
      logger?.info?.({ ip, country: geo.country, latencyMs, checkUrl }, 'ip check ok');
      return {
        status: 'ok',
        ip,
        country: geo.country,
        city: geo.city,
        countryCode: geo.countryCode,
        latencyMs,
        error: null,
        viaProxy: true,
      };
    } catch (err) {
      lastErr = err;
      logger?.debug?.({ checkUrl, err: err.message }, 'ip check try failed');
    }
  }
  logger?.warn?.({ err: lastErr?.message }, 'ip check failed all urls');
  return {
    status: 'error',
    ip: null,
    country: null,
    city: null,
    countryCode: null,
    latencyMs: null,
    error: lastErr?.message || 'Connection failed',
    viaProxy: true,
  };
}

async function safeGeoip(ip) {
  // Best-effort geoip via ip-api.com. Used only as a fallback when no local
  // GeoIP MMDB is configured. Errors are swallowed — IP check itself is the
  // primary signal.
  try {
    const r = await fetch(`https://ip-api.com/json/${ip}?fields=country,city,countryCode`, {
      signal: AbortSignal.timeout(5000),
    });
    if (r.ok) {
      const j = await r.json();
      return { country: j.country || null, city: j.city || null, countryCode: j.countryCode || null };
    }
  } catch {}
  return { country: null, city: null, countryCode: null };
}
