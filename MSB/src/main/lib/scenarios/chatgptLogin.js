import { humanDelay } from '../humanize.js';

const CHATGPT_URL = 'https://chatgpt.com/';
const DEFAULT_PASSWORD = 'Professor.2000';

export default async function chatgptLogin({ page, wayfern, params }) {
  const { email, password = DEFAULT_PASSWORD } = params;
  if (!email) throw new Error('email required');

  // Открываем chatgpt.com
  await page.goto(CHATGPT_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await humanDelay(1500, 2500);

  // Проверяем — может уже залогинен
  const alreadyLoggedIn = await page.locator('[data-testid="composer"], textarea, nav[aria-label]').count().catch(() => 0);
  if (alreadyLoggedIn) {
    return { status: 'already-logged-in', url: page.url() };
  }

  // Кликаем кнопку Log in
  const loginBtn = page.locator('button:has-text("Log in"), a:has-text("Log in"), [data-testid="login-button"]').first();
  await loginBtn.waitFor({ state: 'visible', timeout: 20_000 });
  await humanDelay(600, 1200);
  await humanClick(page, wayfern, loginBtn);
  await humanDelay(1000, 2000);

  // Ждём поле email
  const emailSelector = 'input[name="username"], input[type="email"], input[name="email"]';
  await page.waitForSelector(emailSelector, { timeout: 30_000, state: 'visible' });
  await humanDelay(500, 1000);
  await humanTypeSelector(page, wayfern, emailSelector, email);
  await humanDelay(400, 800);

  // Кнопка Continue / Next
  const continueBtn = page.locator('button:has-text("Continue"), button[type="submit"]').first();
  await continueBtn.waitFor({ state: 'visible', timeout: 15_000 });
  await humanDelay(300, 700);
  await humanClick(page, wayfern, continueBtn);
  await humanDelay(1000, 2000);

  // Ждём поле пароля
  const pwSelector = 'input[type="password"]';
  await page.waitForSelector(pwSelector, { timeout: 30_000, state: 'visible' });
  await humanDelay(600, 1200);
  await humanTypeSelector(page, wayfern, pwSelector, password);
  await humanDelay(400, 900);

  // Кнопка Continue / Submit
  const submitBtn = page.locator('button:has-text("Continue"), button[type="submit"]').first();
  await submitBtn.waitFor({ state: 'visible', timeout: 15_000 });
  await humanDelay(300, 700);
  await humanClick(page, wayfern, submitBtn);

  // Ждём результат
  const outcome = await Promise.race([
    page.waitForURL(/chatgpt\.com\/(c\/|$)/, { timeout: 60_000 }).then(() => 'success'),
    page.waitForSelector('[data-testid="composer"], textarea', { timeout: 60_000 }).then(() => 'success'),
    page.waitForSelector('input[name="code"], input[name="mfa"]', { timeout: 60_000 }).then(() => '2fa'),
    page.waitForSelector('[class*="error"], [data-error], #error-element-password', { timeout: 60_000 }).then(() => 'error'),
  ]).catch(() => 'timeout');

  return { status: outcome, url: page.url() };
}

async function humanTypeSelector(page, wf, selector, text) {
  await page.focus(selector);
  if (wf?.typeText) {
    await wf.typeText(text);
  } else {
    await page.type(selector, text, { delay: 55 + Math.random() * 85 });
  }
}

async function humanClick(page, wf, locatorOrSelector) {
  try {
    if (typeof locatorOrSelector === 'string') {
      const el = page.locator(locatorOrSelector).first();
      if (wf?.hover && wf?.click) {
        await el.hover();
        await el.click();
      } else {
        await el.click({ delay: 40 + Math.random() * 80 });
      }
    } else {
      // Playwright locator объект
      if (wf?.hover && wf?.click) {
        await locatorOrSelector.hover();
        await locatorOrSelector.click();
      } else {
        await locatorOrSelector.click({ delay: 40 + Math.random() * 80 });
      }
    }
  } catch (e) {
    // fallback
    if (typeof locatorOrSelector === 'string') {
      await page.click(locatorOrSelector, { delay: 40 + Math.random() * 80 });
    }
  }
}
