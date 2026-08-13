// MSB Profile Badge — background service worker (MV3)
//
// Читает контекст профиля, который MSB пишет в файл msb-context.json
// в директории расширения ДО запуска браузера. Дальше:
//   - ставит бейдж "N" в тулбаре (через chrome.action.setBadgeText)
//   - ставит title="email" для тултипа
//   - отдаёт контекст popup'у и content-скриптам по message

const CONTEXT_FILE = 'msb-context.json';
const DEFAULT_CONTEXT = { id: '?', name: 'MSB', email: '', number: null };

// Один раз при старте — подтягиваем контекст и рисуем бейдж
(async function init() {
  const ctx = await loadContext();
  applyBadge(ctx);
})();

// Контекст может обновиться — реагируем на alarm + явный signal
chrome.alarms?.create?.('msb-refresh', { periodInMinutes: 1 });
chrome.alarms?.onAlarm?.addListener?.(async (alarm) => {
  if (alarm?.name === 'msb-refresh') {
    const ctx = await loadContext();
    applyBadge(ctx);
  }
});

// Кто-то (лаунчер) может дёрнуть storage — перечитываем
chrome.storage?.onChanged?.addListener?.(async (changes, area) => {
  if (area === 'local' && changes.__msb_context_bumped) {
    const ctx = await loadContext();
    applyBadge(ctx);
  }
});

async function loadContext() {
  try {
    const url = chrome.runtime.getURL(CONTEXT_FILE);
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    return normalizeContext(data);
  } catch (e) {
    return { ...DEFAULT_CONTEXT };
  }
}

function normalizeContext(raw) {
  const id = String(raw?.id ?? DEFAULT_CONTEXT.id);
  const name = String(raw?.name ?? DEFAULT_CONTEXT.name);
  const email = String(raw?.email ?? '');
  const number = raw?.number != null ? String(raw.number) : null;
  const group = raw?.group ? String(raw.group) : '';
  const country = raw?.country ? String(raw.country) : '';
  const startedAt = raw?.startedAt ? Number(raw.startedAt) : Date.now();
  return { id, name, email, number, group, country, startedAt };
}

function applyBadge(ctx) {
  const text = ctx.number || shortId(ctx.id);
  const titleParts = [`MSB Profile #${ctx.number || ctx.id}`];
  if (ctx.email) titleParts.push(ctx.email);
  if (ctx.name && ctx.name !== ctx.email) titleParts.push(ctx.name);
  if (ctx.group) titleParts.push('group: ' + ctx.group);
  if (ctx.country) titleParts.push('country: ' + ctx.country);

  try {
    chrome.action.setBadgeText({ text });
    chrome.action.setBadgeBackgroundColor({ color: '#2F6FED' });
    chrome.action.setTitle({ title: titleParts.join(' · ') });
  } catch (e) {
    // action API не доступен — игнор
  }

  // Кэш для popup/content (без обращения к файлу каждый раз)
  chrome.storage?.local?.set?.({ __msb_ctx: ctx });
}

function shortId(id) {
  const s = String(id || '');
  return s.length > 4 ? s.slice(0, 4) : s || '?';
}

// ── API для popup и content scripts ──────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg !== 'object') return;

  if (msg.type === 'msb:getContext') {
    loadContext().then((ctx) => sendResponse({ ok: true, ctx }));
    return true; // async response
  }

  if (msg.type === 'msb:reloadContext') {
    loadContext().then((ctx) => {
      applyBadge(ctx);
      sendResponse({ ok: true, ctx });
    });
    return true;
  }
});
