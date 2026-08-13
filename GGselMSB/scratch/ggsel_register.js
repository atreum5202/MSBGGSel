import { writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

const PROFILE_ID = process.argv[2];
if (!PROFILE_ID) {
  console.error('Usage: node ggsel_register.js <PROFILE_ID> [action]');
  process.exit(1);
}
const action = process.argv[3] || 'full';

const API_BASE = 'http://127.0.0.1:17248';
const SCRATCH = 'C:\\Users\\Atreum\\Desktop\\MySoft\\scratch';

async function apiGet(p) { const r = await fetch(API_BASE+p); return r.json(); }
async function apiPost(p, body={}) { const r = await fetch(API_BASE+p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return r.json(); }
async function apiPatch(p, body) { const r = await fetch(API_BASE+p,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return r.json(); }

// Loaded dynamically in main()
let EMAIL = '';
let PROFILE_PROXY = null;
let username = '';
let mailProvider = 'outlook';

function sameProxy(a, b) {
  return ['protocol', 'host', 'port', 'username', 'password']
    .every(key => String(a?.[key] ?? '') === String(b?.[key] ?? ''));
}

async function screenshot(page, name) {
  const fp = SCRATCH + '\\' + name + '.png';
  await page.screenshot({ path: fp, fullPage: false });
  console.log('[screenshot]', fp);
}

async function msbScreenshot(name, { type = 'png', fullPage = false } = {}) {
  const query = new URLSearchParams({
    type,
    fullPage: fullPage ? '1' : '0',
  });
  const res = await fetch(`${API_BASE}/profiles/${PROFILE_ID}/screenshot?${query}`);
  if (!res.ok) {
    throw new Error(`MSB screenshot failed: ${res.status} ${res.statusText}`);
  }
  const ext = type === 'jpeg' ? 'jpg' : 'png';
  const fp = `${SCRATCH}\\${name}.${ext}`;
  const buffer = Buffer.from(await res.arrayBuffer());
  await writeFile(fp, buffer);
  console.log('[msb-screenshot]', fp);
}

async function releaseBrowser(browser) {
  if (!browser) return;
  if (typeof browser.disconnect === 'function') {
    await browser.disconnect();
    return;
  }
  if (typeof browser.close === 'function') {
    await browser.close();
  }
}

// Dump visible interactive elements
async function dumpElements(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('button,input,a[href]')]
      .filter(el => el.offsetParent !== null)
      .map(el => ({
        tag: el.tagName,
        type: el.type || '',
        text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 60),
        cls: el.className.slice(0, 80)
      }))
  );
}

function parseOutlookAgeMinutes(metaText) {
  const text = (metaText || '').toLowerCase();
  if (!text) return null;
  if (/just now|только что|сейчас/.test(text)) return 0;

  let m = text.match(/(\d+)\s*(?:мин|min)/);
  if (m) return Number(m[1]);

  m = text.match(/(\d+)\s*(?:ч|час|hour|hr)/);
  if (m) return Number(m[1]) * 60;

  m = text.match(/\b(\d{1,2}):(\d{2})\b/);
  if (m) {
    const now = new Date();
    const candidate = new Date(now);
    candidate.setHours(Number(m[1]), Number(m[2]), 0, 0);
    let diff = Math.round((now - candidate) / 60000);
    if (diff < 0) diff += 24 * 60;
    return diff;
  }

  return null;
}

// ── profile ───────────────────────────────────────────────────────────────────

async function ensureProfileRunning() {
  const s = await apiGet('/profiles/' + PROFILE_ID + '/status');
  if (s.ok && s.data?.cdpEndpoint) { console.log('Profile already running'); return s.data.cdpEndpoint; }
  console.log('Starting profile...');
  const st = await apiPost('/profiles/' + PROFILE_ID + '/start');
  if (!st.ok) throw new Error('Cannot start: ' + JSON.stringify(st));
  await new Promise(r => setTimeout(r, 3000));
  const s2 = await apiGet('/profiles/' + PROFILE_ID + '/status');
  if (!s2.ok || !s2.data?.cdpEndpoint) throw new Error('No CDP after start');
  console.log('Profile started');
  return s2.data.cdpEndpoint;
}

async function ensureProfileProxy() {
  if (!PROFILE_PROXY) {
    console.log('No proxy configuration in profile, skipping proxy check');
    return;
  }

  const profile = await apiGet('/profiles/' + PROFILE_ID);
  if (!profile.ok || !profile.data) throw new Error('Cannot load profile config');

  if (sameProxy(profile.data.proxy, PROFILE_PROXY)) {
    console.log('Profile proxy already correct: ' + PROFILE_PROXY.host + ':' + PROFILE_PROXY.port);
    return;
  }

  const status = await apiGet('/profiles/' + PROFILE_ID + '/status');
  if (status.ok && status.data?.cdpEndpoint) {
    console.log('Stopping profile to apply proxy...');
    const stop = await apiPost('/profiles/' + PROFILE_ID + '/stop');
    if (!stop.ok) throw new Error('Cannot stop profile before proxy update: ' + JSON.stringify(stop));
    await new Promise(r => setTimeout(r, 2000));
  }

  console.log('Updating profile proxy to ' + PROFILE_PROXY.host + ':' + PROFILE_PROXY.port);
  const res = await apiPatch('/profiles/' + PROFILE_ID, { proxy: PROFILE_PROXY });
  if (!res.ok) throw new Error('Cannot update profile proxy: ' + JSON.stringify(res));
}

// ── STEP 1: ggsel.net — открыть модалку и запросить код ──────────────────────

async function initLogin(context) {
  console.log('\n[STEP 1] ggsel.net login');

  let page = context.pages().find(p => p.url().includes('ggsel.net'));
  if (!page) {
    page = context.pages()[0] || await context.newPage();
    const cdpSession = await context.newCDPSession(page);
    await cdpSession.send('Browser.setWindowBounds', {
      windowId: (await cdpSession.send('Browser.getWindowForTarget')).windowId,
      bounds: { width: 1440, height: 900 }
    });
    console.log('Navigating to ggsel.net...');
    await page.goto('https://ggsel.net', { waitUntil: 'domcontentloaded', timeout: 60000 });
  } else {
    console.log('Reusing ggsel tab: ' + page.url());
    await page.bringToFront();
    const cdpSession = await context.newCDPSession(page);
    await cdpSession.send('Browser.setWindowBounds', {
      windowId: (await cdpSession.send('Browser.getWindowForTarget')).windowId,
      bounds: { width: 1440, height: 900 }
    });
  }
  await page.waitForTimeout(2000);
  await screenshot(page, 'step1_initial');

  // Уже залогинен?
  const loggedIn = await page.evaluate(() => {
    const t = document.body.innerText;
    return t.includes('Выйти') || t.includes('Личный кабинет');
  });
  if (loggedIn) {
    console.log('Already logged in!');
    await handleOnboarding(page);
    return { page, alreadyDone: true };
  }

  // Модалка уже открыта?
  const modalOpen = await page.evaluate(() =>
    ['div[role="dialog"]']
      .some(sel => [...document.querySelectorAll(sel)].some(d => d.getBoundingClientRect().width > 0))
  );

  if (!modalOpen) {
    console.log('Opening login modal...');
    const btn = page.locator([
      '.AuthDesktop-module-scss-module__V343VW__button',
      'button:has-text("Войти")',
      'a:has-text("ВОЙТИ")',
      'span:has-text("ВОЙТИ")',
      'span:has-text("Войти")'
    ].join(', ')).first();
    await btn.waitFor({ timeout: 10000 });
    await btn.click().catch(() => {});
    await page.waitForTimeout(2000);

    const checkModal = async () => page.evaluate(() => 
      !!document.querySelector('div[role="dialog"]')
    );

    if (!await checkModal()) {
      console.log('Modal did not open via normal click, trying JS click...');
      await btn.evaluate(el => el.click()).catch(() => {});
      await page.waitForTimeout(2500);
    }
  } else {
    console.log('Modal already open, skipping');
  }

  // Diagnostic screenshot and DOM dump of modal
  await screenshot(page, 'diag_modal');
  const modalElements = await page.evaluate(() => {
    const modal = document.querySelector('div[role="dialog"]') || document.querySelector('[role="dialog"]');
    if (!modal) return { error: 'Modal container not found' };
    return [...modal.querySelectorAll('button,input,a,span,div')]
      .map(el => ({
        tag: el.tagName,
        type: el.type || '',
        text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 80),
        cls: el.className.slice(0, 80)
      }));
  });
  console.log('--- DIAGNOSTIC MODAL DOM DUMP ---');
  console.log(JSON.stringify(modalElements, null, 2));
  console.log('---------------------------------');

  await screenshot(page, 'step2_modal');

  // Если открыт экран выбора способа входа, переходим на email-форму
  const emailOpt = page.locator('div[role="dialog"] button, div[role="dialog"] span').filter({ hasText: /^email$/i }).first();
  if (await emailOpt.isVisible().catch(() => false)) {
    console.log('Selecting Email option...');
    await emailOpt.click();
    await page.waitForTimeout(2000);
    await screenshot(page, 'step2_email_selected');
  }

  const emailInputSel = [
    'div[role="dialog"] input[type="email"]',
    'div[role="dialog"] input[placeholder*="почт" i]',
    'div[role="dialog"] input[placeholder*="email" i]',
    'div[role="dialog"] input[class*="FilledInput"]',
    'div[role="dialog"] input[type="text"]'
  ].join(', ');

  // Заполнить email — только если поле пустое или содержит другое значение
  let inp = page.locator(emailInputSel).first();
  if (await inp.isVisible().catch(() => false)) {
    const val = await inp.inputValue().catch(() => '');
    if (val.toLowerCase().trim() !== EMAIL.toLowerCase()) {
      console.log('Filling email (was: "' + val + '")');
      await inp.click({ clickCount: 3 });  // выделить всё перед вводом
      await inp.fill(EMAIL);
    } else {
      console.log('Email already correct: ' + val);
    }
  } else {
    console.log('No email input visible. DOM dump:');
    console.log(JSON.stringify(await dumpElements(page), null, 2));
  }

  // Кнопка "Получить код"
  console.log('Pre-button diagnostics: MSB screenshot + DOM dump');
  await msbScreenshot('step2_before_get_code_msb');
  console.log(JSON.stringify(await dumpElements(page), null, 2));
  console.log('Clicking Получить код...');
  const codeBtn = page.getByRole('button', { name: /получить код/i })
    .or(page.getByText(/получить код/i))
    .first();
  await codeBtn.waitFor({ timeout: 10000 });
  await codeBtn.click();
  await page.waitForTimeout(3000);
  await screenshot(page, 'step3_code_sent');
  console.log('Code request sent');
  return { page, alreadyDone: false };
}

// ── STEP 2: получить код из Outlook ──────────────────────────────────────────

async function getOutlookCode(context) {
  console.log('\n[STEP 2] Get code from Outlook');

  let page = context.pages().find(p => p.url().includes('outlook.live.com'));
  if (!page) {
    page = await context.newPage();
    console.log('Opening Outlook...');
    await page.goto('https://outlook.live.com/mail/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  } else {
    console.log('Reloading Outlook tab...');
    await page.bringToFront();
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  await page.waitForTimeout(4000);
  await screenshot(page, 'step4_outlook');

  const candidates = await page.evaluate(() => {
    const isVisible = (el) => {
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    };

    const selectors = [
      '[role="option"]',
      '[role="listitem"]',
      '[data-convid]',
      '[data-conversation-id]',
    ];

    let seq = 0;
    const out = [];
    for (const node of document.querySelectorAll(selectors.join(','))) {
      const text = (node.innerText || '').trim();
      const attrs = [...node.attributes].map(a => `${a.name}=${a.value}`).join(' ');
      const haystack = `${text} ${attrs}`.toLowerCase();
      if (!/ggsel|код авторизации|authorization code/.test(haystack)) continue;

      let target = node;
      while (target && target !== document.body) {
        const role = (target.getAttribute('role') || '').toLowerCase();
        if (role === 'option' || role === 'listitem' || target.hasAttribute('data-convid') || target.hasAttribute('data-conversation-id')) break;
        target = target.parentElement;
      }
      if (!isVisible(target)) continue;

      const marker = `ggsel-mail-${seq++}`;
      target.setAttribute('data-ggsel-mail-candidate', marker);

      const relevantAttrs = [...target.attributes]
        .filter(a =>
          /time|date|label|title|datetime/i.test(a.name) ||
          /today|yesterday|minute|min|hour|am|pm|сегодня|вчера|мин|час/i.test(a.value)
        )
        .map(a => `${a.name}=${a.value}`);

      out.push({
        marker,
        text: text.slice(0, 200),
        meta: relevantAttrs.join(' | '),
        top: target.getBoundingClientRect().top,
      });
    }

    return out.sort((a, b) => a.top - b.top);
  });

  const normalizedCandidates = candidates.map(candidate => ({
    ...candidate,
    ageMinutes: parseOutlookAgeMinutes(`${candidate.text} ${candidate.meta}`),
  }));

  console.log('Outlook candidates:', JSON.stringify(normalizedCandidates, null, 2));

  const picked = normalizedCandidates.find(c => c.ageMinutes !== null && c.ageMinutes <= 10)
    || normalizedCandidates[0];

  if (picked) {
    console.log('Opening Outlook candidate:', JSON.stringify(picked));
    const loc = page.locator(`[data-ggsel-mail-candidate="${picked.marker}"]`).first();
    await loc.waitFor({ timeout: 10000 });
    await loc.click();
    await page.waitForTimeout(2000);
    await screenshot(page, 'step5_email_opened');

    const txt = await page.evaluate(() => document.body.innerText);
    const codes = txt.match(/\b\d{4,8}\b/g);
    console.log('Codes found:', codes);
    const code = codes?.find(c => c.length >= 4 && c.length <= 8 && !['2024', '2025', '2026'].includes(c));
    if (code) { console.log('Code extracted: ' + code); return code; }
    console.log('Could not extract code from email');
    return null;
  }

  console.log('GGsel email not found in inbox');
  await screenshot(page, 'step4_outlook_no_email');
  return null;
}

// ── STEP 2 (Gmail): получить код из Gmail ────────────────────────────────────

async function getGmailCode(context) {
  console.log('\n[STEP 2] Get code from Gmail');

  let page = context.pages().find(p => p.url().includes('mail.google.com'));
  if (!page) {
    page = await context.newPage();
    console.log('Opening Gmail...');
    await page.goto('https://mail.google.com', { waitUntil: 'domcontentloaded', timeout: 60000 });
  } else {
    console.log('Reloading Gmail tab...');
    await page.bringToFront();
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  await page.waitForTimeout(4000);
  await screenshot(page, 'step4_gmail');

  const candidates = await page.evaluate(() => {
    const isVisible = (el) => {
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    };

    const selectors = [
      'tr[role="row"]',
      '.zA',
      '[role="option"]',
      '[role="listitem"]'
    ];

    let seq = 0;
    const out = [];
    const allElements = document.querySelectorAll(selectors.join(','));
    for (const node of allElements) {
      const text = (node.innerText || '').trim();
      const attrs = [...node.attributes].map(a => `${a.name}=${a.value}`).join(' ');
      const haystack = `${text} ${attrs}`.toLowerCase();
      if (!/ggsel|код авторизации|authorization code/.test(haystack)) continue;

      let target = node;
      if (!isVisible(target)) continue;

      const marker = `ggsel-gmail-${seq++}`;
      target.setAttribute('data-ggsel-mail-candidate', marker);

      out.push({
        marker,
        text: text.slice(0, 200),
        top: target.getBoundingClientRect().top,
      });
    }

    return out;
  });

  console.log('Gmail candidates:', JSON.stringify(candidates, null, 2));

  const picked = candidates[0];

  if (picked) {
    console.log('Opening Gmail candidate:', JSON.stringify(picked));
    const loc = page.locator(`[data-ggsel-mail-candidate="${picked.marker}"]`).first();
    await loc.waitFor({ timeout: 10000 });
    await loc.click();
    await page.waitForTimeout(4000);
    await screenshot(page, 'step5_gmail_opened');

    const txt = await page.evaluate(() => document.body.innerText);
    const codes = txt.match(/\b\d{4,8}\b/g);
    console.log('Codes found in Gmail email:', codes);
    const code = codes?.find(c => c.length >= 4 && c.length <= 8 && !['2024', '2025', '2026'].includes(c));
    if (code) { console.log('Code extracted: ' + code); return code; }
    console.log('Could not extract code from email');
    return null;
  }

  console.log('GGsel email not found in Gmail inbox');
  return null;
}

async function handleOnboarding(page) {
  // Проверяем есть ли модалка "Знакомство"
  const onboarding = page.locator('[role="dialog"], [class*="Modal"], [class*="modal"]')
    .filter({ hasText: /знакомство|заполните данные/i }).first();
  
  const isVisible = await onboarding.isVisible().catch(() => false);
  if (!isVisible) { console.log('No onboarding modal'); return; }
  
  console.log('Onboarding modal detected, filling...');
  await page.screenshot({ path: SCRATCH + '\\onboarding.png' });

  // Поле Имя — очистить и вписать нормальное имя
  const nameInput = page.locator('input[placeholder*="Имя" i], input[placeholder*="имя" i]').first();
  if (await nameInput.isVisible().catch(() => false)) {
    await nameInput.click({ clickCount: 3 });
    const capitalizedUser = username.charAt(0).toUpperCase() + username.slice(1);
    await nameInput.fill(capitalizedUser);
  }

  // Никнейм
  const nickInput = page.locator('input[placeholder*="Никнейм" i], input[placeholder*="никнейм" i]').first();
  if (await nickInput.isVisible().catch(() => false)) {
    await nickInput.click({ clickCount: 3 });
    await nickInput.fill(username);
  }

  // Дата рождения — оставляем пустой (необязательно)

  // Кнопка Продолжить
  const continueBtn = page.locator('button:has-text("Продолжить"), button:has-text("Продолжить")').first();
  await continueBtn.waitFor({ timeout: 5000 });
  await continueBtn.click();
  await page.waitForTimeout(2000);
  
  console.log('Onboarding completed');
  await page.screenshot({ path: SCRATCH + '\\onboarding_done.png' });
}

// ── STEP 3: ввести код на ggsel.net ──────────────────────────────────────────

async function submitCode(context, code) {
  console.log('\n[STEP 3] Submit code: ' + code);

  let page = context.pages().find(p => p.url().includes('ggsel.net'));
  if (!page) throw new Error('ggsel.net tab not found');
  await page.bringToFront();

  // Дамп DOM перед вводом
  console.log('DOM:', JSON.stringify((await dumpElements(page)).slice(0, 20)));
  await screenshot(page, 'step6_code_page');

  const dialog = page.locator('[role="dialog"]').filter({ hasText: /введите код|выслали на/i }).first();
  await dialog.waitFor({ timeout: 10000 });

  const inp = page.locator('div[role="dialog"] input, [class*="modal"] input, [class*="Modal"] input')
    .or(page.locator('input[aria-label="code"]'))
    .first();
  await inp.waitFor({ timeout: 5000 });
  if (!await inp.isVisible().catch(() => false)) {
    throw new Error('Code input field not found in dialog');
  }

  const before = await inp.inputValue().catch(() => '');
  console.log('Current code field value before fill:', before);
  await inp.click({ clickCount: 3 });
  await inp.fill(code);

  const after = await inp.inputValue().catch(() => '');
  console.log('Current code field value after fill:', after);
  if (after !== code) {
    throw new Error(`Code field value mismatch: expected ${code}, got ${after}`);
  }

  await page.waitForTimeout(500);
  const confirmBtn = dialog.getByRole('button', { name: /войти|подтвердить|верифицировать|confirm/i }).first();
  await confirmBtn.waitFor({ timeout: 5000 });
  await confirmBtn.click();
  await page.waitForTimeout(4000);
  await screenshot(page, 'step7_after_submit');

  await handleOnboarding(page);

  const success = await page.evaluate(() => {
    const t = document.body.innerText;
    return t.includes('Выйти') || t.includes('Личный кабинет') || 
           t.includes('Профиль') || !document.querySelector('button[class*="AuthDesktop"]');
  });
  console.log(success ? 'Login successful!' : 'Login unclear — check step7_after_submit.png');
  return success;
}

// ── STEP 4: обновить notes профиля ───────────────────────────────────────────

async function updateNotes() {
  console.log('\n[STEP 4] Update profile notes');
  const providerDisplay = mailProvider.charAt(0).toUpperCase() + mailProvider.slice(1);
  const notesString = `Login: ${EMAIL} | Group: ${providerDisplay} | GGsel: registered | GGsel_user: ${username}`;
  const res = await apiPatch('/profiles/' + PROFILE_ID, {
    notes: notesString
  });
  console.log(res.ok ? 'Notes updated OK: ' + notesString : 'Notes failed: ' + JSON.stringify(res));
}

// ── main ──────────────────────────────────────────────────────────────────────

async function main() {
  // 1. Load Profile info
  console.log('Loading profile data for ID:', PROFILE_ID);
  const profileRes = await apiGet('/profiles/' + PROFILE_ID);
  if (!profileRes.ok || !profileRes.data) {
    throw new Error('Cannot load profile data for ID: ' + PROFILE_ID + '. Response: ' + JSON.stringify(profileRes));
  }

  const profile = profileRes.data;
  EMAIL = profile.account?.email || '';
  if (!EMAIL) {
    throw new Error('Profile account has no email configured');
  }

  PROFILE_PROXY = profile.proxy;
  username = EMAIL.split('@')[0];

  const accountType = (profile.account?.type || '').toLowerCase();
  if (/outlook|hotmail|live/i.test(EMAIL) || /outlook|hotmail|live/i.test(accountType)) {
    mailProvider = 'outlook';
  } else if (/gmail/i.test(EMAIL) || /gmail/i.test(accountType)) {
    mailProvider = 'gmail';
  } else {
    mailProvider = 'outlook'; // Default
  }

  console.log(`Profile Info:
  Email: ${EMAIL}
  Username: ${username}
  Mail Provider: ${mailProvider}
  Proxy: ${PROFILE_PROXY ? `${PROFILE_PROXY.host}:${PROFILE_PROXY.port}` : 'None'}
  Action: ${action}`);

  if (action === 'start') {
    await ensureProfileProxy();
    await ensureProfileRunning();
    return;
  }

  // Force restart profile to clear Qrator session
  console.log('Force restarting profile to clear Qrator session...');
  await apiPost('/profiles/' + PROFILE_ID + '/stop');
  await new Promise(r => setTimeout(r, 3000));

  await ensureProfileProxy();
  const ep = await ensureProfileRunning();
  const browser = await chromium.connectOverCDP(ep);
  const ctx = browser.contexts()[0];

  // Set viewport
  await ctx.setDefaultNavigationTimeout(60000);
  for (const p of ctx.pages()) {
    await p.setViewportSize({ width: 1440, height: 900 }).catch(() => {});
  }

  try {
    if (action === 'full' || action === 'login') {
      const { alreadyDone } = await initLogin(ctx);
      if (alreadyDone) { await updateNotes(); return; }
      if (action === 'login') return;
    }

    if (action === 'full' || action === 'code') {
      let code = null;
      if (mailProvider === 'gmail') {
        code = await getGmailCode(ctx);
      } else {
        code = await getOutlookCode(ctx);
      }

      if (!code) {
        console.log('\nManual intervention needed:');
        console.log(`  node ggsel_register.js ${PROFILE_ID} submit-code XXXXXX`);
        return;
      }
      if (await submitCode(ctx, code)) await updateNotes();
    }

    if (action === 'submit-code') {
      const code = process.argv[4];
      if (!code) throw new Error(`Usage: node ggsel_register.js ${PROFILE_ID} submit-code 123456`);
      if (await submitCode(ctx, code)) await updateNotes();
    }

  } finally {
    await releaseBrowser(browser);
  }
}

main().catch(err => { console.error('ERROR:', err.message); process.exit(1); });
