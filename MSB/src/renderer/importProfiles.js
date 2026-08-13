const DEFAULT_VIEWPORT = { width: 1366, height: 768 };

function blankProfile(name) {
  return {
    name,
    notes: '',
    engine: 'auto',
    humanize: true,
    aggressiveFingerprint: false,
    startUrl: 'https://bot.sannysoft.com',
    proxy: null,
    fingerprint: {
      platform: 'Win32',
      locale: 'en-US',
      timezone: 'America/New_York',
      viewport: { ...DEFAULT_VIEWPORT },
    },
  };
}

export function parseProxyString(raw) {
  const s = (raw || '').trim();
  if (!s) return null;

  const urlMatch = s.match(/^(https?|socks5?):\/\/(?:([^:@]+):([^@]+)@)?([^:@\s]+):(\d+)\/?$/i);
  if (urlMatch) {
    const [, protocol, username, password, host, port] = urlMatch;
    return { protocol: protocol.toLowerCase(), host, port: Number(port), username: username || '', password: password || '' };
  }

  const parts = s.split(':');
  if (parts.length === 2 && /^\d+$/.test(parts[1])) {
    return { protocol: 'http', host: parts[0], port: Number(parts[1]), username: '', password: '' };
  }
  if (parts.length === 4 && /^\d+$/.test(parts[1])) {
    const [host, port, username, password] = parts;
    return { protocol: 'http', host, port: Number(port), username, password };
  }
  return null;
}

function formatProxy(p) {
  if (!p) return null;
  const auth = p.username ? `${p.username}:${p.password}@` : '';
  return `${p.protocol}://${auth}${p.host}:${p.port}`;
}

export function isLegacyBulkFormat(text) {
  return /^\s*Profile name=/im.test(text) && /^\s*Cookie=/im.test(text);
}

export function parseTxtImport(text) {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
  const profiles = [];
  const skipped = [];

  lines.forEach((line, i) => {

    const sepMatch = line.match(/^(.+?)\s*(?:\|{1,2}|::)\s*(.+)$/);
    let name = null;
    let proxyRaw = line;

    if (sepMatch) {
      const left = sepMatch[1].trim();
      const right = sepMatch[2].trim();

      if (parseProxyString(right)) {
        name = left;
        proxyRaw = right;
      }
    }

    const proxy = parseProxyString(proxyRaw);
    if (!proxy) { skipped.push({ line: i + 1, value: line }); return; }

    const profileName = name || proxy.host;
    const p = blankProfile(profileName);
    p.proxy = formatProxy(proxy);
    profiles.push(p);
  });

  return { profiles, skipped };
}

function detectDelimiter(headerLine) {
  return headerLine.includes('\t') ? '\t' : ',';
}

function parseDelimited(text, delimiter) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"') { inQuotes = true; continue; }
    if (c === delimiter) { row.push(field); field = ''; continue; }
    if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    if (c === '\r') continue;
    field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ''));
}

export function parseTabularImport(text) {
  const rows = parseDelimited(text.trim(), detectDelimiter(text.split(/\r?\n/, 1)[0] || ''));
  if (!rows.length) return { profiles: [], skipped: [] };

  const header = rows[0].map((h) => h.trim().toLowerCase());
  const idx = (name) => header.indexOf(name);
  const iName = idx('name');
  const iProxy = idx('proxy');
  const iHost = idx('host');
  const iPort = idx('port');
  const iUser = idx('username') >= 0 ? idx('username') : idx('user');
  const iPass = idx('password') >= 0 ? idx('password') : idx('pass');
  const iEngine = idx('engine');
  const iStartUrl = idx('starturl') >= 0 ? idx('starturl') : idx('url');
  const iNotes = idx('notes');
  const iPlatform = idx('platform');
  const iLocale = idx('locale');
  const iTz = idx('timezone');
  const iW = idx('width');
  const iH = idx('height');

  const profiles = [];
  const skipped = [];

  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const get = (i) => (i >= 0 && row[i] !== undefined ? row[i].trim() : '');

    const name = get(iName) || `Imported ${r}`;
    let proxyRaw = get(iProxy);
    if (!proxyRaw && get(iHost) && get(iPort)) {
      proxyRaw = formatProxy({
        protocol: 'http', host: get(iHost), port: Number(get(iPort)),
        username: get(iUser), password: get(iPass),
      });
    }
    const proxy = proxyRaw ? (parseProxyString(proxyRaw) ? proxyRaw : null) : null;
    if (proxyRaw && !proxy) { skipped.push({ line: r + 1, value: proxyRaw }); continue; }

    const p = blankProfile(name);
    p.proxy = proxy || null;
    if (get(iEngine)) p.engine = get(iEngine);
    if (get(iStartUrl)) p.startUrl = get(iStartUrl);
    if (get(iNotes)) p.notes = get(iNotes);
    if (get(iPlatform)) p.fingerprint.platform = get(iPlatform);
    if (get(iLocale)) p.fingerprint.locale = get(iLocale);
    if (get(iTz)) p.fingerprint.timezone = get(iTz);
    if (get(iW)) p.fingerprint.viewport.width = Number(get(iW)) || DEFAULT_VIEWPORT.width;
    if (get(iH)) p.fingerprint.viewport.height = Number(get(iH)) || DEFAULT_VIEWPORT.height;
    profiles.push(p);
  }

  return { profiles, skipped };
}
