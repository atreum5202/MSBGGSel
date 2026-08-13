import { PROXY_PROTOCOLS } from '../core/constants.js';

const DEFAULT_PORT = { http: 8080, https: 8080, socks4: 1080, socks5: 1080 };

export function normalizeProxy(input) {
  if (!input) return null;
  if (typeof input === 'string') return parseProxyString(input);
  if (typeof input !== 'object') throw new Error('Unsupported proxy value');

  if (input.server && !input.host) {
    const parsed = parseProxyString(input.server);
    return {
      ...parsed,
      username: input.username || parsed.username,
      password: input.password || parsed.password,
      bypass: input.bypass || undefined,
    };
  }

  const protocol = (input.protocol || 'http').toLowerCase();
  if (!PROXY_PROTOCOLS.includes(protocol)) {
    throw new Error(`Unsupported proxy protocol: ${protocol}`);
  }
  if (!input.host) throw new Error('Proxy host is required');
  const port = Number(input.port);
  if (!Number.isFinite(port) || port <= 0) throw new Error('Invalid proxy port');
  return {
    protocol,
    host: String(input.host),
    port,
    username: input.username || undefined,
    password: input.password || undefined,
    bypass: input.bypass || undefined,
  };
}

export function toLaunchProxy(proxy) {
  if (!proxy) return undefined;
  return {
    server: `${proxy.protocol}://${proxy.host}:${proxy.port}`,
    username: proxy.username,
    password: proxy.password,
    bypass: proxy.bypass,
  };
}

export function toProxyUrl(proxy) {
  if (!proxy) return null;
  const auth =
    proxy.username && proxy.password
      ? `${encodeURIComponent(proxy.username)}:${encodeURIComponent(proxy.password)}@`
      : '';
  return `${proxy.protocol}://${auth}${proxy.host}:${proxy.port}`;
}

/**
 * attachProxyAuth — вешает CDP Fetch.authRequired ТОЛЬКО для HTTP/HTTPS прокси с авторизацией.
 *
 * Ключевые исправления:
 * 1. Fetch.enable теперь с patterns: [{ requestStage: 'Request' }] — перехватывает
 *    только запросы требующие auth-challenge, не все подряд.
 *    До фикса: каждый запрос паузился и ждал continueRequest → перегрев мобильного NAT.
 * 2. Fetch.requestPaused обрабатывается через continueRequest без задержки.
 * 3. CDP-сессия открывается на уже существующей первой странице, не создаёт новую.
 */
export async function attachProxyAuth(context, proxy) {
  if (!proxy?.username || !proxy?.password) return;

  // Берём первую страницу если есть, иначе создаём
  const page = context.pages()[0] || (await context.newPage());
  const client = await context.newCDPSession(page);

  // patterns: [] — перехватываем только auth-challenge запросы (не все request)
  await client.send('Fetch.enable', {
    handleAuthRequests: true,
    patterns: [],
  });

  client.on('Fetch.authRequired', async ({ requestId }) => {
    try {
      await client.send('Fetch.continueWithAuth', {
        requestId,
        authChallengeResponse: {
          response: 'ProvideCredentials',
          username: proxy.username,
          password: proxy.password,
        },
      });
    } catch (err) {
      // Запрос уже закрыт — нормально при быстрой навигации
    }
  });

  // Fetch.requestPaused срабатывает если мы попали в patterns — просто пропускаем
  client.on('Fetch.requestPaused', async ({ requestId }) => {
    try {
      await client.send('Fetch.continueRequest', { requestId });
    } catch {}
  });
}

const ROUTER_TAG = Symbol.for('msb.proxyRouter');

/**
 * switchProxy — используется ТОЛЬКО для socks5 с авторизацией.
 * HTTP/HTTPS прокси обрабатываются нативно через --proxy-server + attachProxyAuth.
 *
 * undici ProxyAgent не удаляется — используем динамический import чтобы не падать
 * если undici не установлен.
 */
export async function switchProxy(context, newProxy) {
  const previous = context[ROUTER_TAG];
  if (previous) {
    try {
      await context.unroute('**/*', previous);
    } catch {}
    context[ROUTER_TAG] = null;
  }

  if (!newProxy) return { proxy: null };

  const proxyUrl = toProxyUrl(newProxy);
  const { ProxyAgent } = await import('undici');
  const dispatcher = new ProxyAgent(proxyUrl);

  const handler = async (route) => {
    const req = route.request();
    try {
      const method = req.method();
      const headers = await req.allHeaders();
      const body = req.postDataBuffer();
      const res = await fetch(req.url(), {
        method,
        headers,
        body: body || undefined,
        redirect: 'manual',
        dispatcher,
      });
      const buf = Buffer.from(await res.arrayBuffer());
      const outHeaders = {};
      res.headers.forEach((v, k) => (outHeaders[k] = v));
      await route.fulfill({ status: res.status, headers: outHeaders, body: buf });
    } catch {
      await route.abort('failed').catch(() => {});
    }
  };

  await context.route('**/*', handler);
  context[ROUTER_TAG] = handler;
  return { proxy: newProxy };
}

function parseProxyString(str) {
  const url = new URL(str.includes('://') ? str : `http://${str}`);
  const protocol = url.protocol.replace(':', '').toLowerCase();
  if (!PROXY_PROTOCOLS.includes(protocol)) {
    throw new Error(`Unsupported proxy protocol: ${protocol}`);
  }
  return {
    protocol,
    host: url.hostname,
    port: Number(url.port) || DEFAULT_PORT[protocol] || 8080,
    username: decodeURIComponent(url.username) || undefined,
    password: decodeURIComponent(url.password) || undefined,
  };
}
