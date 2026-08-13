// MSB Profile Badge — content script
//
// Рисует плашку "<номер> | <email>" в верхнем левом углу страницы.
// По дизайну — как у MoreLogin на скрине: компактная, с лёгкой тенью,
// поверх контента, не мешает вёрстке. Не кликается, не мешает событиям
// под собой (pointer-events: none).
//
// Берёт контекст у background через message API. Если контекст
// ещё не готов (background не успел) — повторяет запрос по событию
// msb:ready, которое background шлёт после инициализации.

(function () {
  if (window.__msbBadgeMounted) return;
  window.__msbBadgeMounted = true;

  const ROOT_ID = 'msb-profile-badge-overlay';
  let el = null;
  let lastCtx = null;

  mountShell();
  requestContext();
  bindEvents();

  function mountShell() {
    if (document.getElementById(ROOT_ID)) return;
    el = document.createElement('div');
    el.id = ROOT_ID;
    el.setAttribute('aria-hidden', 'true');
    el.dataset.state = 'loading';
    el.innerHTML =
      '<span class="msb-pb-num">…</span>' +
      '<span class="msb-pb-sep">|</span>' +
      '<span class="msb-pb-email">MSB</span>';
    // Втыкаем как можно раньше, чтобы на document_start уже был
    (document.body || document.documentElement).appendChild(el);
  }

  function bindEvents() {
    // При готовности background пушит событие через message
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === 'msb:ready' && msg.ctx) {
        render(msg.ctx);
      }
      if (msg && msg.type === 'msb:contextUpdated' && msg.ctx) {
        render(msg.ctx);
      }
    });
  }

  function requestContext() {
    try {
      chrome.runtime.sendMessage({ type: 'msb:getContext' }, (res) => {
        if (chrome.runtime.lastError) {
          // background ещё не загрузился — оставим "loading" и подождём событие
          return;
        }
        if (res && res.ctx) render(res.ctx);
      });
    } catch {
      // расширение недоступно (например, в incognito отключено) — оставляем плашку скрытой
      if (el) el.style.display = 'none';
    }
  }

  function render(ctx) {
    if (!el || !ctx) return;
    lastCtx = ctx;
    const num = ctx.number || ctx.id || '?';
    const email = ctx.email || ctx.name || '';

    el.querySelector('.msb-pb-num').textContent = String(num);
    el.querySelector('.msb-pb-email').textContent = email || '—';

    el.title = buildTooltip(ctx);
    el.dataset.state = 'ready';
  }

  function buildTooltip(ctx) {
    const parts = [`MSB Profile #${ctx.number || ctx.id}`];
    if (ctx.email) parts.push(ctx.email);
    if (ctx.name && ctx.name !== ctx.email) parts.push(ctx.name);
    if (ctx.group) parts.push('group: ' + ctx.group);
    if (ctx.country) parts.push('country: ' + ctx.country);
    return parts.join(' · ');
  }
})();
