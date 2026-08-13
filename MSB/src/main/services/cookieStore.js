import fs from 'node:fs/promises';
import path from 'node:path';
import { profileDir } from '../core/paths.js';

export class CookieStore {
  constructor({ profilesDir, logger }) {
    this.profilesDir = profilesDir;
    this.logger = logger?.child?.({ mod: 'cookieStore' }) || logger || console;
  }

  _file(id) {
    return path.join(profileDir(this.profilesDir, id), 'cookies-snapshot.json');
  }

  async saveSnapshot(id, cookies) {
    const p = this._file(id);
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, JSON.stringify(cookies, null, 2));
    return p;
  }

  async loadSnapshot(id) {
    try {
      return JSON.parse(await fs.readFile(this._file(id), 'utf8'));
    } catch {
      return null;
    }
  }

  async exportRunning(id, browserLauncher, { format = 'json' } = {}) {
    const info = browserLauncher.getRunning(id);
    if (!info) throw new Error(`Profile ${id} is not running`);
    const cookies = await info.context.cookies();
    this.logger.debug({ profileId: id, format, count: cookies.length }, 'cookies exported');
    if (format === 'netscape') {
      return { format, data: toNetscape(cookies), count: cookies.length };
    }
    return { format: 'json', data: cookies, count: cookies.length };
  }

  async importRunning(id, payload, browserLauncher) {
    const info = browserLauncher.getRunning(id);
    if (!info) throw new Error(`Profile ${id} is not running`);

    let cookies = [];
    if (Array.isArray(payload.cookies)) cookies = payload.cookies;
    else if (typeof payload.netscape === 'string') cookies = fromNetscape(payload.netscape);
    else if (Array.isArray(payload)) cookies = payload;
    else throw new Error('Provide { cookies: [...] } or { netscape: "..." }');

    const normalized = cookies.map(normalizeCookie).filter(Boolean);
    await info.context.addCookies(normalized);
    this.logger.info({ profileId: id, imported: normalized.length, skipped: cookies.length - normalized.length }, 'cookies imported');
    return { imported: normalized.length };
  }

  async clearRunning(id, browserLauncher) {
    const info = browserLauncher.getRunning(id);
    if (!info) throw new Error(`Profile ${id} is not running`);
    const cookies = await info.context.cookies();
    if (cookies.length) {
      await info.context.clearCookies();
    }
    this.logger.info({ profileId: id, cleared: cookies.length }, 'cookies cleared');
    return { cleared: cookies.length };
  }
}

function toNetscape(cookies) {
  const lines = [
    '# Netscape HTTP Cookie File',
    '# Exported by MyStealthBrowser',
    `# Timestamp: ${new Date().toISOString()}`,
    '',
  ];
  for (const c of cookies) {
    const domain = c.domain?.startsWith('.') ? c.domain : c.domain || '';
    const includeSub = c.domain?.startsWith('.') ? 'TRUE' : 'FALSE';
    const expiry = c.expires && c.expires > 0 ? Math.floor(c.expires) : 0;
    lines.push(
      [domain, includeSub, c.path || '/', c.secure ? 'TRUE' : 'FALSE', expiry, c.name, c.value].join('\t')
    );
  }
  return lines.join('\n');
}

function fromNetscape(text) {
  const out = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const parts = line.split('\t');
    if (parts.length < 7) continue;
    const [domain, _includeSub, cpath, secure, expiry, name, value] = parts;
    out.push({
      domain,
      path: cpath || '/',
      secure: /^true$/i.test(secure),
      expires: Number(expiry) || -1,
      name,
      value,
    });
  }
  return out;
}

function normalizeCookie(c) {
  if (!c?.name || c.value == null) return null;
  const out = {
    name: c.name,
    value: String(c.value),
    domain: c.domain,
    path: c.path || '/',
    expires: typeof c.expires === 'number' ? c.expires : -1,
    httpOnly: !!c.httpOnly,
    secure: !!c.secure,
    sameSite: normalizeSameSite(c.sameSite),
  };
  if (!out.domain && c.url) out.url = c.url;
  return out;
}

function normalizeSameSite(v) {
  if (!v) return 'Lax';
  const s = String(v).toLowerCase();
  if (s === 'strict') return 'Strict';
  if (s === 'none' || s === 'no_restriction') return 'None';
  if (s === 'lax') return 'Lax';
  return 'Lax';
}
