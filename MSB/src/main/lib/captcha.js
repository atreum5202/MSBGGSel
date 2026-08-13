const BASE = 'https://2captcha.com';
const POLL_MS = 5_000;
const TIMEOUT_MS = 180_000;

export class TwoCaptcha {
  constructor(apiKey) {
    if (!apiKey) throw new Error('2Captcha apiKey required');
    this.apiKey = apiKey;
  }

  async _in(params) {
    const url = new URL(`${BASE}/in.php`);
    url.searchParams.set('key', this.apiKey);
    url.searchParams.set('json', '1');
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
    const res = await fetch(url, { method: 'GET' });
    const data = await res.json();
    if (data.status !== 1) throw new Error(`2Captcha in.php: ${data.request}`);
    return data.request;
  }

  async _res(id) {
    const start = Date.now();
    while (Date.now() - start < TIMEOUT_MS) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const url = new URL(`${BASE}/res.php`);
      url.searchParams.set('key', this.apiKey);
      url.searchParams.set('action', 'get');
      url.searchParams.set('id', id);
      url.searchParams.set('json', '1');
      const res = await fetch(url);
      const data = await res.json();
      if (data.status === 1) return data.request;
      if (data.request !== 'CAPCHA_NOT_READY') {
        throw new Error(`2Captcha res.php: ${data.request}`);
      }
    }
    throw new Error('2Captcha timeout');
  }

  solveRecaptchaV2({ sitekey, pageUrl, invisible = 0, enterprise = 0 }) {
    return this._in({
      method: 'userrecaptcha',
      googlekey: sitekey,
      pageurl: pageUrl,
      invisible,
      enterprise,
    }).then((id) => this._res(id));
  }

  solveRecaptchaV3({ sitekey, pageUrl, action, min_score = 0.7 }) {
    return this._in({
      method: 'userrecaptcha',
      version: 'v3',
      googlekey: sitekey,
      pageurl: pageUrl,
      action,
      min_score,
    }).then((id) => this._res(id));
  }

  solveTurnstile({ sitekey, pageUrl, action, cdata }) {
    return this._in({
      method: 'turnstile',
      sitekey,
      pageurl: pageUrl,
      action,
      data: cdata,
    }).then((id) => this._res(id));
  }

  solveHCaptcha({ sitekey, pageUrl }) {
    return this._in({
      method: 'hcaptcha',
      sitekey,
      pageurl: pageUrl,
    }).then((id) => this._res(id));
  }
}
