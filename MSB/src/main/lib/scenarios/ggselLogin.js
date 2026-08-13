/**
 * Сценарий: ggsel-login
 * Открывает https://ggsel.net, ждёт прохождения Cloudflare + Qrator JS-челленджа,
 * добавляет человеческое поведение (скролл, движение мыши),
 * сохраняет снапшот куков через cookieStore (если передан),
 * возвращает куки домена ggsel.net для Python-парсера.
 */

import { humanDelay } from '../humanize.js';

const GGSEL_URL = 'https://ggsel.net';

/**
 * Проверяет — висит ли WAF-челлендж ИЛИ жёсткий 403-блок.
 *
 * Два сценария блока:
 *   A) JS-челлендж: "just a moment", "__cf_chl" — браузер проходит сам за 5-15 сек
 *   B) Hard 403: "guru meditation", "forbidden", "access denied" — IP заблочен,
 *      cf_clearance не будет выдана никогда, нужен reload/другой профиль
 *
 * Оба случая возвращают true — страница НЕ готова.
 */
function isBlockedPage(html) {
  const lower = html.toLowerCase();
  return (
    // Cloudflare JS-челлендж
    lower.includes('__cf_chl') ||
    lower.includes('challenge-form') ||
    lower.includes('cf-spinner') ||
    lower.includes('checking your browser') ||
    lower.includes('just a moment') ||
    lower.includes('enable javascript and cookies') ||
    // Cloudflare hard 403 / Guru Meditation
    lower.includes('guru meditation') ||
    lower.includes('error 403') ||
    lower.includes('403 forbidden') ||
    lower.includes('access denied') ||
    lower.includes('cf-error-code') ||
    lower.includes('ray id') ||               // CF добавляет Ray ID в 403
    // Qrator
    lower.includes('/__qrator/qauth.js') ||
    lower.includes('__qrator')
  );
}

/**
 * Проверяет — это реально страница ggsel.net (не блок и не заглушка).
 * Ищем уникальные маркеры сайта.
 */
function isRealGgselPage(html) {
  const lower = html.toLowerCase();
  return (
    lower.includes('ggsel') ||
    lower.includes('digital goods') ||
    lower.includes('steam') ||
    lower.includes('__nuxt') ||        // nuxt.js — фреймворк ggsel
    lower.includes('application/json') // API-ответы тоже ок
  );
}

/**
 * Определяет тип текущей страницы для логирования.
 */
function pageType(html) {
  const lower = html.toLowerCase();
  if (lower.includes('guru meditation'))      return 'CF_HARD_403';
  if (lower.includes('__cf_chl'))             return 'CF_CHALLENGE';
  if (lower.includes('just a moment'))        return 'CF_WAIT';
  if (lower.includes('/__qrator/qauth.js'))   return 'QRATOR_CHALLENGE';
  if (lower.includes('access denied'))        return 'ACCESS_DENIED';
  if (lower.includes('403') && lower.includes('forbidden')) return 'FORBIDDEN_403';
  if (isRealGgselPage(lower))                 return 'GGSEL_OK';
  if (html.length < 500)                      return 'EMPTY_OR_SHORT';
  return 'UNKNOWN';
}

/**
 * Проверяет наличие cf_clearance в куках контекста для ggsel.net.
 */
async function hasCfClearance(context) {
  const cookies = await context.cookies('https://ggsel.net');
  return cookies.some((c) => c.name === 'cf_clearance');
}

/**
 * Имитирует живое поведение пользователя после загрузки страницы.
 */
async function humanBrowse(page) {
  try {
    const scrollY = 200 + Math.floor(Math.random() * 400);
    await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'smooth' }), scrollY);
    await humanDelay(600, 1400);

    const viewportSize = page.viewportSize() || { width: 1280, height: 800 };
    const mx = 100 + Math.floor(Math.random() * (viewportSize.width - 200));
    const my = 100 + Math.floor(Math.random() * (viewportSize.height - 200));
    await page.mouse.move(mx, my, { steps: 8 + Math.floor(Math.random() * 8) });
    await humanDelay(300, 700);

    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
    await humanDelay(400, 900);
  } catch {
    // Ошибки поведения не критичны
  }
}

export default async function ggselLogin({ page, context, profile, cookieStore, params = {} }) {
  const timeoutMs = params.timeoutMs ?? 70_000;
  const maxRetries = params.maxRetries ?? 5;

  // Шаг 1: Открываем ggsel.net
  await page.goto(GGSEL_URL, {
    waitUntil: 'domcontentloaded',
    timeout: timeoutMs,
  });

  let passed = false;
  let lastPageType = 'UNKNOWN';
  let hardBlockCount = 0;  // сколько раз подряд получили hard 403

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    // Прогрессивная пауза
    const waitMs = 3000 + attempt * 2000;
    await page.waitForTimeout(waitMs);

    const html = await page.content();
    const cfOk = await hasCfClearance(context);
    const blocked = isBlockedPage(html);
    const realPage = isRealGgselPage(html);
    lastPageType = pageType(html);

    console.log(
      `[ggselLogin] attempt=${attempt}/${maxRetries}` +
      ` cfClearance=${cfOk}` +
      ` pageType=${lastPageType}` +
      ` htmlLen=${html.length}` +
      ` url=${page.url()}`
    );

    // Успех: реальная страница + кука
    if (!blocked && cfOk) {
      passed = true;
      break;
    }

    // Успех без куки? Такое бывает если ggsel не требует cf на конкретной странице
    // Но нам нужна кука для Python-парсера — продолжаем ждать
    if (realPage && !blocked && !cfOk) {
      console.log('[ggselLogin] страница ggsel открылась, но cf_clearance ещё нет — ждём');
      await page.waitForTimeout(5000);
      const cfOk2 = await hasCfClearance(context);
      if (cfOk2) {
        passed = true;
        break;
      }
    }

    if (attempt >= maxRetries) break;

    // Hard 403 (Guru Meditation, Access Denied) — простой waitForNavigation не поможет,
    // нужен reload. После 2 подряд hard блоков — бросаем, IP заблочен.
    const isHardBlock = (
      lastPageType === 'CF_HARD_403' ||
      lastPageType === 'ACCESS_DENIED' ||
      lastPageType === 'FORBIDDEN_403'
    );

    if (isHardBlock) {
      hardBlockCount++;
      console.warn(`[ggselLogin] Hard block #${hardBlockCount}: ${lastPageType} — делаю reload`);
      if (hardBlockCount >= 2) {
        console.error('[ggselLogin] 2 hard block подряд — IP заблочен, прерываю попытки');
        break;
      }
      // Ждём подольше и перезагружаем
      await page.waitForTimeout(4000);
      await page.reload({ waitUntil: 'domcontentloaded', timeout: timeoutMs }).catch(() => {});
    } else {
      hardBlockCount = 0;
      // JS-челлендж или неизвестная страница — ждём редиректа от CF/Qrator
      await Promise.race([
        page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: timeoutMs }),
        page.waitForTimeout(6000),
      ]).catch(() => {});
    }
  }

  // Финальная попытка через waitForFunction
  if (!passed) {
    try {
      await page.waitForFunction(
        () => {
          const html = document.documentElement.innerHTML.toLowerCase();
          return (
            !html.includes('__cf_chl') &&
            !html.includes('__qrator') &&
            !html.includes('challenge-form') &&
            !html.includes('guru meditation') &&
            !html.includes('just a moment') &&
            (html.includes('ggsel') || html.includes('__nuxt') || html.includes('steam'))
          );
        },
        { timeout: timeoutMs }
      );
      const cfOk = await hasCfClearance(context);
      if (cfOk) {
        passed = true;
      } else {
        await page.waitForTimeout(5000);
        passed = await hasCfClearance(context);
      }
    } catch {
      console.warn(`[ggselLogin] финальная waitForFunction не прошла, lastPageType=${lastPageType}`);
    }
  }

  if (!passed) {
    const html = await page.content().catch(() => '');
    const type = pageType(html);
    const hint =
      type === 'CF_HARD_403' || type === 'ACCESS_DENIED'
        ? 'IP заблочен Cloudflare (hard 403) — смени прокси на residential'
        : type === 'CF_CHALLENGE' || type === 'CF_WAIT'
        ? 'CF JS-челлендж не прошёл за отведённое время — увеличь timeoutMs'
        : `неизвестная страница (${type}, len=${html.length})`;

    throw new Error(`[ggselLogin] cf_clearance не получена. Причина: ${hint}`);
  }

  // Живое поведение
  await humanBrowse(page);

  // Забираем куки
  const allCookies = await context.cookies();
  const ggselCookies = allCookies.filter(
    (c) => c.domain && c.domain.endsWith('ggsel.net')
  );

  // Сохраняем снапшот
  if (cookieStore && profile?.id) {
    try {
      await cookieStore.saveSnapshot(profile.id, allCookies);
    } catch (err) {
      console.warn('[ggselLogin] cookieStore.saveSnapshot failed:', err.message);
    }
  }

  const cfCookie   = ggselCookies.find((c) => c.name === 'cf_clearance');
  const qratorCookie = ggselCookies.find((c) => c.name === 'qrator_jsid' || c.name === '__qrator_jsid');
  console.log(
    `[ggselLogin] done: total=${ggselCookies.length}` +
    ` cf_clearance=${!!cfCookie}` +
    ` qrator=${!!qratorCookie}` +
    ` url=${page.url()}`
  );

  return {
    status: 'ok',
    url: page.url(),
    cookies: ggselCookies,
    cookieCount: ggselCookies.length,
  };
}
