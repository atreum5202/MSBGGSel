import { timingSafeEqual } from 'node:crypto';

function safeEqual(a, b) {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

export function makeAuthHook(token) {
  if (!token) return async () => {};
  const expected = `Bearer ${token}`;
  return async (req, reply) => {
    const p = req.url;
    if (p === '/health' || p === '/ui-config' || p.startsWith('/docs') || p.startsWith('/ws/') || p.startsWith('/ui')) return;
    const header = req.headers['authorization'] || '';
    if (!safeEqual(header, expected)) {
      reply.code(401).send({ error: 'Unauthorized' });
    }
  };
}

export function makeWsAuth(token) {
  if (!token) return () => true;
  return (req) => {
    const provided = req.query?.token || (req.headers['sec-websocket-protocol'] || '');
    return safeEqual(provided, token);
  };
}
