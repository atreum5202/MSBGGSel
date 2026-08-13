import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import https from 'node:https';
import { SocksProxyAgent } from 'socks-proxy-agent';
import { HttpsProxyAgent } from 'https-proxy-agent';
import { toProxyUrl } from '../lib/proxy.js';
import { DEFAULTS } from '../core/constants.js';

const TTL = DEFAULTS.GEOIP_TTL_MS;

const COUNTRY_LOCALE = {
  US: 'en-US', GB: 'en-GB', CA: 'en-CA', AU: 'en-AU', NZ: 'en-NZ', IE: 'en-IE',
  DE: 'de-DE', AT: 'de-AT', CH: 'de-CH',
  FR: 'fr-FR', BE: 'fr-BE', LU: 'fr-LU',
  ES: 'es-ES', MX: 'es-MX', AR: 'es-AR', CL: 'es-CL', CO: 'es-CO', PE: 'es-PE',
  IT: 'it-IT', PT: 'pt-PT', BR: 'pt-BR',
  RU: 'ru-RU', BY: 'ru-BY', KZ: 'ru-KZ', UA: 'uk-UA',
  PL: 'pl-PL', CZ: 'cs-CZ', SK: 'sk-SK', HU: 'hu-HU', RO: 'ro-RO', BG: 'bg-BG',
  NL: 'nl-NL', SE: 'sv-SE', NO: 'nb-NO', DK: 'da-DK', FI: 'fi-FI', IS: 'is-IS',
  GR: 'el-GR', TR: 'tr-TR',
  JP: 'ja-JP', KR: 'ko-KR', CN: 'zh-CN', HK: 'zh-HK', TW: 'zh-TW',
  SG: 'en-SG', TH: 'th-TH', VN: 'vi-VN', ID: 'id-ID', PH: 'en-PH', MY: 'en-MY',
  IN: 'en-IN',
  IL: 'he-IL', SA: 'ar-SA', AE: 'ar-AE', EG: 'ar-EG',
  ZA: 'en-ZA',
};

function guessLocale(cc) {
  return COUNTRY_LOCALE[(cc || '').toUpperCase()] || 'en-US';
}

export class GeoIP {
  constructor({ cacheFile, logger }) {
    this.cacheFile = cacheFile;
    this.logger = logger?.child?.({ mod: 'geoip' }) || logger || console;
    this.memory = new Map();
    this._loaded = this._loadCache();
  }

  async _loadCache() {
    try {
      const raw = await fs.readFile(this.cacheFile, 'utf8');
      const parsed = JSON.parse(raw);
      const now = Date.now();
      for (const [ip, entry] of Object.entries(parsed)) {
        if (now - (entry.savedAt || 0) < TTL) this.memory.set(ip, entry);
      }
    } catch {}
  }

  async _persist() {
    const obj = Object.fromEntries(this.memory);
    await fs.mkdir(path.dirname(this.cacheFile), { recursive: true });
    await fs.writeFile(this.cacheFile, JSON.stringify(obj, null, 2));
  }

  async lookupIp(ip) {
    await this._loaded;
    const cached = this.memory.get(ip);
    if (cached && Date.now() - cached.savedAt < TTL) {
      this.logger.debug({ ip }, 'geoip cache hit');
      return cached.data;
    }
    const data =
      (await queryIpApi(ip).catch(() => null)) ||
      (await queryIpinfo(ip).catch(() => null));
    if (!data) {
      this.logger.warn({ ip }, 'geoip lookup failed');
      throw new Error(`GeoIP lookup failed for ${ip}`);
    }
    this.memory.set(ip, { savedAt: Date.now(), data });
    this._persist().catch((err) => this.logger.warn({ err: err.message }, 'failed to persist geoip cache'));
    this.logger.debug({ ip, country: data.countryCode }, 'geoip lookup ok');
    return data;
  }

  async lookupViaProxy(proxy) {
    if (!proxy) return null;
    const cacheKey = `via:${proxy.host}:${proxy.port}`;
    await this._loaded;
    const cached = this.memory.get(cacheKey);
    if (cached && Date.now() - cached.savedAt < TTL) {
      this.logger.debug({ proxy: cacheKey }, 'geoip cache hit (via proxy)');
      return cached.data;
    }

    const proxyUrl = toProxyUrl(proxy);
    const agent = proxy.protocol.startsWith('socks')
      ? new SocksProxyAgent(proxyUrl)
      : new HttpsProxyAgent(proxyUrl);

    let data;
    try {
      data = normalizeGeo(await fetchViaAgent('https://ipinfo.io/json', agent));
    } catch {
      data = normalizeGeo(await fetchViaAgent('http://ip-api.com/json/', agent));
    }
    if (!data) {
      this.logger.warn({ proxy: cacheKey }, 'geoip-via-proxy lookup failed');
      throw new Error('GeoIP-via-proxy failed');
    }
    this.memory.set(cacheKey, { savedAt: Date.now(), data });
    this._persist().catch((err) => this.logger.warn({ err: err.message }, 'failed to persist geoip cache'));
    this.logger.debug({ proxy: cacheKey, country: data.countryCode }, 'geoip lookup via proxy ok');
    return data;
  }
}

function normalizeGeo(raw) {
  if (!raw) return null;
  if (raw.query || raw.status) {
    return {
      ip: raw.query,
      country: raw.country,
      countryCode: raw.countryCode,
      region: raw.regionName,
      city: raw.city,
      timezone: raw.timezone,
      locale: guessLocale(raw.countryCode),
    };
  }
  return {
    ip: raw.ip,
    country: raw.country_name || raw.country,
    countryCode: raw.country,
    region: raw.region,
    city: raw.city,
    timezone: raw.timezone,
    locale: guessLocale(raw.country),
  };
}

async function queryIpApi(ip) {
  const res = await fetch(`http://ip-api.com/json/${encodeURIComponent(ip)}`);
  const data = await res.json();
  if (data.status !== 'success') throw new Error(data.message || 'ip-api failed');
  return normalizeGeo(data);
}

async function queryIpinfo(ip) {
  const res = await fetch(`https://ipinfo.io/${encodeURIComponent(ip)}/json`);
  return normalizeGeo(await res.json());
}

function fetchViaAgent(url, agent, timeoutMs = 8_000) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https:') ? https : http;
    const req = lib.get(url, { agent, timeout: timeoutMs }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('geoip request timeout')));
    req.on('error', reject);
  });
}
