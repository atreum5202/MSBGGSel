import { humanDelay } from '../humanize.js';
import { TwoCaptcha } from '../captcha.js';

const SIGNIN_URL =
  'https://accounts.google.com/signin/v2/identifier?hl=en&flowName=GlifWebSignIn';

export default async function googleLogin({ page, wayfern, params }) {
  const { email, password, twoCaptchaKey } = params;
  if (!email || !password) throw new Error('email and password required');

  await page.goto(SIGNIN_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await humanDelay(1200, 2200);

  const emailInput = 'input[type="email"]';
  await page.waitForSelector(emailInput, { timeout: 30_000 });
  await humanType(page, wayfern, emailInput, email);
  await humanDelay(400, 900);
  await humanClick(page, wayfern, '#identifierNext button, button:has-text("Next")');

  const pwInput = 'input[type="password"]';
  await page.waitForSelector(pwInput, { timeout: 30_000, state: 'visible' });
  await humanDelay(800, 1600);
  await humanType(page, wayfern, pwInput, password);
  await humanDelay(500, 1000);
  await humanClick(page, wayfern, '#passwordNext button, button:has-text("Next")');

  const outcome = await Promise.race([
    page.waitForURL(/myaccount|accounts\.google\.com\/(ManageAccount|b|a)/, { timeout: 45_000 }).then(() => 'success'),
    page.waitForSelector('iframe[src*="recaptcha"]', { timeout: 45_000 }).then(() => 'captcha'),
    page.waitForSelector('input[name="idvPin"], input[name="idvPhoneNumber"], form[action*="challenge"]', { timeout: 45_000 }).then(() => '2fa'),
    page.waitForSelector('div[jsname="B34EJ"], div[data-error-code]', { timeout: 45_000 }).then(() => 'error'),
  ]).catch(() => 'timeout');

  if (outcome === 'captcha') {
    if (!twoCaptchaKey) {
      return { status: 'captcha-required', message: 'Captcha detected. Provide twoCaptchaKey to auto-solve.' };
    }
    await solveRecaptchaOnPage(page, twoCaptchaKey);
    await humanClick(page, wayfern, '#passwordNext button, button:has-text("Next")').catch(() => {});
    await page.waitForURL(/myaccount/, { timeout: 60_000 }).catch(() => {});
  }

  return { status: outcome, url: page.url() };
}

async function humanType(page, wf, selector, text) {
  if (wf?.typeText) {
    await page.focus(selector);
    await wf.typeText(text);
  } else {
    await page.type(selector, text, { delay: 60 + Math.random() * 90 });
  }
}

async function humanClick(page, wf, selector) {
  const first = selector.split(',')[0].trim();
  try {
    if (wf?.hover && wf?.click) {
      await wf.hover(first);
      await wf.click(first);
      return;
    }
  } catch {}
  await page.click(first, { delay: 40 + Math.random() * 100 });
}

async function solveRecaptchaOnPage(page, apiKey) {
  const solver = new TwoCaptcha(apiKey);
  const sitekey = await page.evaluate(() => {
    const el = document.querySelector('[data-sitekey]');
    if (el) return el.getAttribute('data-sitekey');
    const iframe = document.querySelector('iframe[src*="recaptcha"]');
    if (iframe) {
      const url = new URL(iframe.src);
      return url.searchParams.get('k');
    }
    return null;
  });
  if (!sitekey) throw new Error('Could not extract reCAPTCHA sitekey');
  const token = await solver.solveRecaptchaV2({ sitekey, pageUrl: page.url() });
  await page.evaluate((t) => {
    const el = document.querySelector('[name="g-recaptcha-response"]');
    if (el) el.value = t;
    for (const ta of document.querySelectorAll('textarea')) {
      if (ta.name === 'g-recaptcha-response') ta.value = t;
    }
  }, token);
}
