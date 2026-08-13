const FIELD_ALIASES = {
  'profile name': 'name',
  platform: 'platform',
  'user-defined platform domain name': 'platformDomain',
  'login account': 'loginAccount',
  'login password': 'loginPassword',
  '2fa key': 'twoFaKey',
  'password protection': 'passwordProtection',
  'profile id': 'legacyId',
  cookie: 'cookieRaw',
  'proxy information': 'proxyInfo',
  'proxy number': 'proxyNumber',
  'profile group': 'group',
  'profile tag': 'tag',
  'profile note': 'note',
  ua: 'userAgent',
  'end-to-end encryption': 'e2ee',
  'custom number': 'customNumber',
};

function splitBlocks(text) {
  return text
    .replace(/\r\n/g, '\n')
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter(Boolean);
}

function parseBlock(block) {
  const raw = {};
  for (const line of block.split('\n')) {
    if (!line.trim()) continue;
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    const mapped = FIELD_ALIASES[key];
    if (mapped) raw[mapped] = value;
  }
  return raw;
}

function mapLegacyCookie(c) {
  if (!c || !c.name) return null;
  const sameSiteMap = { '-1': 'Lax', 0: 'None', 1: 'Lax', 2: 'Strict' };
  return {
    name: c.name,
    value: c.value != null ? String(c.value) : '',
    domain: c.domain || undefined,
    path: c.path || '/',
    secure: !!c.secure,
    httpOnly: !!(c.http_only ?? c.httpOnly),
    expires: c.expires ? Math.floor(new Date(c.expires).getTime() / 1000) : -1,
    sameSite: sameSiteMap[String(c.same_site)] || 'Lax',
  };
}

function parseCookieField(cookieRaw) {
  if (!cookieRaw) return [];
  let arr;
  try {
    arr = JSON.parse(cookieRaw);
  } catch {
    return [];
  }
  if (!Array.isArray(arr)) return [];
  return arr.map(mapLegacyCookie).filter(Boolean);
}

function toCreateInput(raw) {
  const notesParts = [];
  if (raw.loginAccount) notesParts.push(`Login: ${raw.loginAccount}`);
  if (raw.group) notesParts.push(`Group: ${raw.group}`);
  if (raw.tag) notesParts.push(`Tag: ${raw.tag}`);
  if (raw.note) notesParts.push(raw.note);

  const input = {
    name: raw.name || raw.platform || 'Imported profile',
    notes: notesParts.join(' | '),
    startUrl: undefined,
    fingerprint: raw.userAgent ? { userAgent: raw.userAgent } : undefined,
  };

  if (raw.proxyInfo) {
    input.proxy = parseLegacyProxy(raw.proxyInfo);
  }

  return input;
}

function parseLegacyProxy(str) {
  if (!str || !str.trim()) return null;
  const parts = str.split(':').map((s) => s.trim());
  if (parts.length >= 4) {
    const [protocol, host, port, username, ...passParts] = parts;
    return { protocol: protocol || 'http', host, port: Number(port) || port, username, password: passParts.join(':') };
  }
  if (parts.length === 2) {
    const [host, port] = parts;
    return { protocol: 'http', host, port: Number(port) || port };
  }
  return null;
}

export function parseLegacyBulkExport(text) {
  const blocks = splitBlocks(text);
  const profiles = [];
  const errors = [];

  blocks.forEach((block, i) => {
    try {
      const raw = parseBlock(block);
      if (!Object.keys(raw).length) return;
      const input = toCreateInput(raw);
      const cookies = parseCookieField(raw.cookieRaw);
      profiles.push({ input, cookies, legacyId: raw.legacyId });
    } catch (err) {
      errors.push(`block ${i}: ${err.message}`);
    }
  });

  return { profiles, errors };
}

export async function importLegacyBulkExport(text, { profileManager, cookieStore, logger }) {
  const { profiles, errors } = parseLegacyBulkExport(text);
  const created = [];

  for (const p of profiles) {
    const profile = await profileManager.create(p.input);
    if (p.cookies.length && cookieStore) {
      await cookieStore.saveSnapshot(profile.id, p.cookies);
    }
    created.push({ id: profile.id, name: profile.name, cookieCount: p.cookies.length });
  }

  logger?.info?.(
    { imported: created.length, errors: errors.length },
    'legacy bulk profile import finished'
  );

  return { imported: created, errors };
}
