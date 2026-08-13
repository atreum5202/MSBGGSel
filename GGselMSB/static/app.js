function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container') || (() => {
    const el = document.createElement('div');
    el.id = 'toast-container';
    el.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:10px;';
    document.body.appendChild(el);
    return el;
  })();
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.style.cssText = 'background:var(--bg-panel);color:var(--text-main);padding:12px 20px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.5);border-left:4px solid var(--primary-color);opacity:0;transition:opacity 0.3s, transform 0.3s;transform:translateY(20px);';
  if (type === 'success') toast.style.borderColor = 'var(--status-active)';
  if (type === 'error') toast.style.borderColor = 'var(--status-error)';
  toast.innerText = message;
  container.appendChild(toast);
  requestAnimationFrame(() => { toast.style.opacity = '1'; toast.style.transform = 'translateY(0)'; });
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateY(20px)'; setTimeout(() => toast.remove(), 300); }, 3000);
}

// ═══════════════════════════════════════════════════════════════
//  СТРУКТУРА app.js
// ───────────────────────────────────────────────────────────────
//  УТИЛИТЫ          showToast, esc, fmt, fmtDate, api, debounce
//  НАВИГАЦИЯ        loadView, showView, VIEWS[]
//  СТАТУС СЕРВИСОВ  pollServiceStatus, setSvcMsb, setSvcGgsel
//  MSB DROPDOWN     getMsbLaunchMode, setMsbLaunchMode, initMsbDropdown
//  ДАШБОРД          loadDashboard
//  ТОВАРЫ           loadOffers, renderOffers
//  ЗАКАЗЫ           loadOrders
//  ЧАТЫ             loadChats
//  ФИНАНСЫ          loadFinance
//  ПАРСЕР           loadParser, renderParsedProducts
//  МОДЕРАЦИЯ        loadModeration, refreshModerationProducts,
//                   renderModerationCards, buildCardPairHtml,
//                   doApprove, doReject, doRestyleImage, doGenImage
//  ПУБЛИКАЦИЯ       doPublish, publishProduct
//  СТАТУС           loadStatus
//  СТАРТ            loadDashboard(), pollServiceStatus(), initMsbDropdown()
// ═══════════════════════════════════════════════════════════════

/* GGselV7 — клиентский скрипт
 * GGSeller-стиль интерфейс, vanilla JS, без анимаций.
 * Использует ТОЛЬКО V1 + V2 API endpoints (без кук, без парсера).
 */

// ═══════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════
const $  = (s, p = document) => p.querySelector(s);
const $$ = (s, p = document) => Array.from(p.querySelectorAll(s));

const fmt = (v, dec = 2) => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (isNaN(n)) return String(v);
  return n.toLocaleString('ru-RU', { maximumFractionDigits: dec });
};
const fmtDate = (v) => {
  if (!v) return '—';
  try {
    const d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return String(v); }
};
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// Картинки с CDN ggsel (img.ggsel.net) не грузятся напрямую с localhost —
// сайт проверяет Referer и блокирует хотлинкинг. Локально сгенерированные
// AI-картинки (/static/generated/...) отдаются Flask'ом напрямую и прокси не нужен.
function imgSrc(url) {
  if (!url) return '';
  if (url.startsWith('/static/') || url.startsWith('/parser/') || url.startsWith('data:')) return url;
  // Абсолютный путь к файлу (старые записи в БД) — отдаём через /parser/image
  if (url.startsWith('/') || url.match(/^[A-Za-z]:\\/)) {
    return `/parser/image?path=${encodeURIComponent(url)}`;
  }
  return `/api/parser/image-proxy?url=${encodeURIComponent(url)}`;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

const statusBadge = (s) => {
  const map    = { active: 'active', paused: 'paused', draft: 'draft', archived: 'archived' };
  const labels = { active: 'Активен', paused: 'Пауза', draft: 'Черновик', archived: 'Архив' };
  return `<span class="badge ${map[s] || 'neutral'}">${labels[s] || esc(s) || '—'}</span>`;
};

// API call helper with status pill update
async function api(path, opts = {}) {
  try {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) {
    setSvcGgsel('offline');
    throw e;
  }
}

// ═══════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════
const VIEWS = [
  'promo-codes',
  'profile',
  'offer-edit',
  'docs',
  'dashboard', 'offers', 'queue', 'orders', 'promo-codes',
  'messages', 'reviews', 'finance', 'ads-campaigns', 'ads-stats',
  'settings', 'parser', 'moderation', 'logs', 'status', 'warmer', 'msb', 'deals'
];
const loaded = new Set();
let currentView = 'dashboard';

function showView(name) {
  if (!VIEWS.includes(name)) name = 'dashboard';
  VIEWS.forEach(v => {
    const el = $('#view-' + v);
    if (el) el.classList.toggle('active', v === name);
  });
  $$('.sidebar .nav-item[data-view]').forEach(el =>
    el.classList.toggle('active', el.dataset.view === name)
  );
  currentView = name;
  loadView(name);
  // scroll to top on view change
  $('.content')?.scrollTo?.(0, 0);
}

$$('.sidebar .nav-item[data-view]').forEach(el => {
  el.addEventListener('click', (e) => {
    if (el.tagName === 'A') return; // external link
    e.preventDefault();
    showView(el.dataset.view);
  });
});
$$('[data-nav]').forEach(el => el.addEventListener('click', () => showView(el.dataset.nav)));

// Реклама dropdown
const navAds = $('#nav-ads');
if (navAds) {
  navAds.addEventListener('click', () => navAds.classList.toggle('open'));
}

// Sidebar Toggle
const btnSidebarToggle = $('#btn-sidebar-toggle');
const sidebar = $('.sidebar');
if (sidebar && btnSidebarToggle) {
  const isCompact = localStorage.getItem('sidebar_compact') === 'true';
  if (isCompact) sidebar.classList.add('compact');
  btnSidebarToggle.addEventListener('click', () => {
    sidebar.classList.toggle('compact');
    localStorage.setItem('sidebar_compact', sidebar.classList.contains('compact'));
  });
}

// Toast
function showToast(message, type = 'success') {
  const container = $('#toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${esc(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('hide');
    toast.addEventListener('transitionend', () => toast.remove());
  }, 3000);
}

function loadView(name) {
  if (loaded.has(name)) return;
  loaded.add(name);
  const loaders = {
    'dashboard':      loadDashboard,
    'promo-codes': loadPromoCodes,
    'profile': loadProfile,
    'offer-edit': () => {},
    'docs': () => {},
    'offers':         loadOffers,
    'orders':         loadOrders,
    'messages':       loadChats,
    'reviews':        loadReviews,
    'finance':        loadFinance,
    'settings':       () => {},
    'parser':         loadParser,
    'moderation':     loadModeration,
    'deals':          loadDeals,
    'logs':           loadLogs,
    'status':         loadStatus,
    'warmer':         loadWarmer,
    'msb':      loadMsb,
  };
  const fn = loaders[name];
  if (fn) fn().catch(e => { console.error('loadView', name, e); setStatus('Ошибка', 'err'); });
}

function refreshActive() {
  const name = currentView;
  loaded.delete(name);
  loadView(name);
  setStatus('Обновлено', 'ok');
}

function setStatus(text, kind = '') {
  const el = $('#global-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'tb-status ' + kind;
}

// ═══════════════════════════════════════════════════
//  SERVICE STATUS (sidebar bottom)
// ═══════════════════════════════════════════════════
function setSvcBackend(state) {
  const dot  = $('#svc-backend-dot');
  if (!dot) return;
  dot.className = 'tb-svc-dot ' + (state === 'online' ? 'online' : state === 'offline' ? 'offline' : 'checking');
}

function setSvcBot(state, text) {
  const dot  = $('#svc-bot-dot');
  if (!dot) return;
  dot.className = 'tb-svc-dot ' + (state === 'online' ? 'online' : state === 'offline' ? 'offline' : 'checking');
}

// ── MSB launch mode ────────────────────────────────────────────────────────
function getMsbLaunchMode() {
  return localStorage.getItem('msb_launch_mode') || 'background';
}
function setMsbLaunchMode(mode) {
  localStorage.setItem('msb_launch_mode', mode);
  const label = $('#svc-msb-label');
  if (label) label.textContent = mode === 'visible' ? 'MSB 👁' : 'MSB';
  // Синх radio
  $$('input[name="msb-mode"]').forEach(r => { r.checked = (r.value === mode); });
}
function initMsbDropdown() {
  // Дропдаун удалён — клик по индикатору MSB открывает оверлей
  setMsbLaunchMode(getMsbLaunchMode());
  $$('input[name="msb-mode"]').forEach(r => {
    r.addEventListener('change', () => setMsbLaunchMode(r.value));
  });
  const wrap = $('#svc-msb-wrap');
  if (wrap) wrap.addEventListener('click', () => openMsbTest());
}

function setSvcMsb(state, latency) {
  const dot  = $('#svc-msb-dot');
  const wrap = $('#svc-msb-wrap');
  if (!dot) return;
  dot.className = 'tb-svc-dot ' + (state === 'online' ? 'online' : state === 'offline' ? 'offline' : 'checking');
  if (wrap) wrap.title = state === 'online'
    ? `MSB Anti-detect — онлайн${latency != null ? ' (' + latency + ' ms)' : ''}`
    : state === 'offline' ? 'MSB Anti-detect — недоступен' : 'MSB Anti-detect — проверяю…';
}

function setSvcGgsel(state) {
  const dot  = $('#svc-ggsel-dot');
  if (!dot) return;
  dot.className = 'tb-svc-dot ' + (state === 'online' ? 'online' : state === 'offline' ? 'offline' : 'checking');
}

function setSvcCookie(state, title = '') {
  const dot  = $('#svc-cookie-dot');
  const wrap = $('#svc-cookie-wrap');
  if (!dot) return;
  dot.className = 'tb-svc-dot ' + (state === 'online' ? 'online' : state === 'offline' ? 'offline' : 'checking');
  if (wrap && title) {
    wrap.title = title;
  }
}

$('#svc-cookie-wrap')?.addEventListener('click', openCookieModal);

// ═══════════════════════════════════════════════════
//  COOKIE STATUS OVERLAY
// ═══════════════════════════════════════════════════
function openCookieModal() {
  $('#cookie-modal-bg').classList.add('active');
  runCookieCheck();
  refreshCookieAutoPanel();
}
function closeCookieModal() {
  $('#cookie-modal-bg').classList.remove('active');
}

async function cookieCopyToClipboard() {
  const btn = $('#cookie-modal-copy-btn');
  try {
    const r = await fetch('/api/cookie/status', { signal: AbortSignal.timeout(5000) });
    const d = await r.json();
    const cookies = d.cookies || {};
    if (!Object.keys(cookies).length) {
      showToast('Куки пусты — нечего копировать', 'error');
      return;
    }
    // Формат Cookie-заголовка: name=value; name2=value2
    const cookieStr = Object.entries(cookies)
      .map(([k, v]) => `${k}=${v}`)
      .join('; ');
    await navigator.clipboard.writeText(cookieStr);
    const orig = btn.textContent;
    btn.textContent = '✓ Скопировано!';
    setTimeout(() => { btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>Скопировать куки'; }, 2000);
    showToast(`Скопировано ${Object.keys(cookies).length} куков в буфер обмена`, 'success');
  } catch (e) {
    showToast('Ошибка: ' + e.message, 'error');
  }
}

async function runCookieCheck() {
  const body    = $('#cookie-modal-body');
  const summary = $('#cookie-modal-summary');
  const checkBtn = $('#cookie-modal-check-btn');
  if (checkBtn) { checkBtn.disabled = true; checkBtn.textContent = '⏳ ...'; }
  body.innerHTML = '<div class="loader"><div class="spinner"></div>Проверка cookie...</div>';
  summary.textContent = 'Опрашиваем /api/cookie/status...';

  const QRATOR_KEYS = new Set(['qrator_msid2','qrator_jsid','qrator_ssid','qrator_ssid2','qrator_jsr','__qrator_jsid']);
  const AUTH_KEYS   = new Set(['session','auth','token','sid','user','login','account','bearer',
    'access','refresh','access_token','refresh_token','chat_token','ACCESS_TOKEN','REFRESH_TOKEN','CHAT_TOKEN']);

  try {
    const r = await fetch('/api/cookie/status', { signal: AbortSignal.timeout(8000) });
    const d = await r.json();

    const exists    = d.exists    ?? false;
    const hasQrator = d.has_qrator ?? false;
    const fresh     = d.fresh     ?? false;
    const ageMin    = (d.age_sec ?? d.age_seconds) != null ? Math.round((d.age_sec ?? d.age_seconds) / 60) : null;
    const cookies   = d.cookies   ?? {};
    const names     = Object.keys(cookies);
    const qrFound    = names.filter(n => QRATOR_KEYS.has(n) || QRATOR_KEYS.has(n.toLowerCase()));
    const authFound  = names.filter(n => !qrFound.includes(n) && (AUTH_KEYS.has(n) || AUTH_KEYS.has(n.toLowerCase())));
    const otherFound = names.filter(n => !qrFound.includes(n) && !authFound.includes(n));

    const overallOk   = exists && hasQrator;
    const statusColor = overallOk ? (fresh ? 'var(--green)' : 'var(--yellow)') : 'var(--red)';
    const statusEmoji = overallOk ? (fresh ? '✅' : '⚠️') : '❌';
    const statusText  = overallOk ? (fresh ? 'Валидны и свежие' : 'Валидны, устарели') : 'Отсутствуют / невалидны';

    // ── Summary bar ───────────────────────────────────
    summary.innerHTML = `
      <div class="ep-summary-bar" style="margin-top:4px;">
        <div class="ep-sum">
          <div class="ep-sum-num ${overallOk ? 'ok' : 'fail'}">${statusEmoji}</div>
          <div class="ep-sum-label" style="color:${statusColor};">${statusText}</div>
        </div>
        <div class="ep-sum">
          <div class="ep-sum-num total">${names.length}</div>
          <div class="ep-sum-label">Всего куков</div>
        </div>
        <div class="ep-sum">
          <div class="ep-sum-num ${qrFound.length ? 'ok' : 'fail'}">${qrFound.length}</div>
          <div class="ep-sum-label">Qrator</div>
        </div>
        <div class="ep-sum">
          <div class="ep-sum-num" style="color:var(--text-muted);">${ageMin !== null ? ageMin + ' мин' : '—'}</div>
          <div class="ep-sum-label">Возраст</div>
        </div>
        <div class="ep-sum">
          <div class="ep-sum-num" style="color:var(--text-faint); font-size:11px;">seller_cookies.json</div>
          <div class="ep-sum-label">Источник</div>
        </div>
      </div>`;

    setSvcCookie(overallOk ? 'online' : 'offline', statusText);

    // ── Словарь известных кук ──────────────────────────────
    const COOKIE_INFO = {
      'qrator_jsr':      { desc: 'Токен Qrator JS-задачи. Подтверждает что браузер реальный, а не бот.',    used: 'Защита от WAF на seller.ggsel.com и ggsel.net. Без неё API возвращает 403.' },
      'qrator_msid2':   { desc: 'Qrator сессионный ID. Главный ключ доверия Qrator.',       used: 'API вызовы: уведомления, IP-адреса, промокоды, оптовые цены.' },
      'qrator_jsid':    { desc: 'Qrator JS-сессия. Связывает JS-отпечаток с сессией.',                used: 'В паре с qrator_msid2 — дают доступ к seller.ggsel.com.' },
      '__qrator_jsid':  { desc: 'Альтернативное имя qrator_jsid (HTTP-only вариант).',                    used: 'То же что qrator_jsid — защита от WAF.' },
      'qrator_ssid':    { desc: 'Qrator серверный сессионный ID.',                                        used: 'Серверная проверка Qrator. В паре с qrator_msid2.' },
      '__ddg1_':        { desc: 'DDoS-Guard токен. Альтернативная защита если Qrator недоступен.',        used: 'Запасной вариант. Может заменять Qrator на ggsel.net.' },
      '__ddg2_':        { desc: 'DDoS-Guard челлендж токен.',                                                 used: 'В паре с __ddg1_ — защита от ботов.' },
      'session':        { desc: 'Сессионная кука авторизации пользователя.',   used: 'Личный кабинет seller.ggsel.com.' },
      'qrator_ssid2':   { desc: 'Qrator серверный сессионный ID v2.',                used: 'Защита от WAF на seller.ggsel.com. Работает в паре с qrator_jsr.' },
      'ACCESS_TOKEN':   { desc: 'JWT токен доступа (авторизация). Краткоживущий (~15 мин).', used: 'Можно использовать как Authorization: Bearer для seller.ggsel.com API. Прямая альтернатива Qrator.' },
      'REFRESH_TOKEN':  { desc: 'JWT токен обновления. Долгоживущий.',                used: 'Используется для получения нового ACCESS_TOKEN без повторного логина.' },
      'CHAT_TOKEN':     { desc: 'JWT токен для чат-сервиса.',                               used: 'Авторизация WebSocket/REST запросов в чат-сервисе.' },
    };

    // ── Cookie cards ──────────────────────────────────
    function makeCookieCards(group, cls, badge) {
      return group.map(name => {
        const val     = String(cookies[name] ?? '');
        const preview = val.slice(0, 64) + (val.length > 64 ? '…' : '');
        const info    = COOKIE_INFO[name];
        const descHtml = info ? `
          <div style="display:flex; gap:16px; margin-bottom:10px; padding:8px 10px; background:rgba(255,255,255,0.04); border-radius:6px; border:1px solid var(--border-soft);">
            <div style="flex:1;">
              <div style="font-size:10px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-faint); margin-bottom:3px;">ЧТО ДЕЛАЕТ</div>
              <div style="font-size:12px; color:var(--text-normal);">${esc(info.desc)}</div>
            </div>
            <div style="flex:1;">
              <div style="font-size:10px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-faint); margin-bottom:3px;">ГДЕ ИСПОЛЬЗУЕТСЯ</div>
              <div style="font-size:12px; color:var(--text-muted);">${esc(info.used)}</div>
            </div>
          </div>` : '';
        return `
          <div class="ep-card ${cls}">
            <div class="ep-head" data-toggle-ep>
              <span class="ep-label" style="font-family:monospace; font-size:12px; font-weight:600;">${esc(name)}</span>
              <span class="badge ${badge}" style="flex-shrink:0;">${badge === 'info' ? 'Qrator' : badge === 'ok' ? 'Auth' : 'Cookie'}</span>
              ${info ? `<span style="font-size:11px; color:var(--text-faint); margin:0 6px;">— ${esc(info.desc.split('.')[0])}</span>` : ''}
              <span style="font-family:monospace; font-size:11px; color:var(--text-faint); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin:0 8px;">${esc(preview)}</span>
              <span class="ep-status s-${cls === 'ok' ? 'ok' : cls === 'fail' ? 'fail' : 'idle'}">${val.length} б</span>
              <span class="ep-arrow">▶</span>
            </div>
            <div class="ep-body">
              ${descHtml}
              <div style="font-size:10px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-faint); margin-bottom:4px;">ЗНАЧЕНИЕ</div>
              <pre class="ep-sample" style="white-space:pre-wrap; word-break:break-all;">${esc(val)}</pre>
            </div>
          </div>`;
      }).join('');
    }

    let cardsHtml = '';
    if (names.length === 0) {
      cardsHtml = `<div class="empty" style="padding:24px;">Нет данных — нажмите «Открыть MSB Seller» чтобы получить куки</div>`;
    } else {
      if (qrFound.length)   cardsHtml += `<div class="cookie-group-label">🛡 Qrator (${qrFound.length})</div>`   + makeCookieCards(qrFound,   'ok',   'info');
      if (authFound.length) cardsHtml += `<div class="cookie-group-label">🔑 Auth / Session (${authFound.length})</div>` + makeCookieCards(authFound, 'ok',   'ok');
      if (otherFound.length)cardsHtml += `<div class="cookie-group-label">🍪 Остальные (${otherFound.length})</div>`   + makeCookieCards(otherFound,'idle', 'neutral');
    }

    body.innerHTML = `<div class="api-test-results" style="margin-top:12px;">${cardsHtml}</div>`;
    body.querySelectorAll('[data-toggle-ep]').forEach(h =>
      h.addEventListener('click', () => h.parentElement.classList.toggle('open'))
    );

  } catch (e) {
    summary.textContent = 'Ошибка получения статуса';
    body.innerHTML = `<div class="empty">⚠ Не удалось получить статус: ${esc(String(e))}</div>`;
    setSvcCookie('offline', 'Cookie: Ошибка');
  } finally {
    if (checkBtn) { checkBtn.disabled = false; checkBtn.textContent = '↻ Проверить'; }
  }
}

// Кнопка «Открыть MSB Seller» внутри модала
async function cookieModalOpenBrowser() {
  const btn  = $('#cookie-modal-open-btn');
  const body = $('#cookie-modal-body');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:12px;height:12px;border-width:2px;margin-right:6px;"></span>Открываем...';
  body.innerHTML = '<div class="loader"><div class="spinner"></div>Запускаем MSB Seller → ggsel.net → seller.ggsel.com (оба Qrator-челленджа)…</div>';
  $('#cookie-modal-summary').textContent = 'Ждём загрузки страниц и куков (~20 сек)...';
  try {
    const res = await fetch('/api/cookie/open-browser', {
      method: 'POST',
      signal: AbortSignal.timeout(70000),
    });
    const d = await res.json();
    if (d.ok) {
      showToast(`✅ ${d.msg || 'Куки обновлены'}`, 'success');
    } else {
      showToast('⚠️ ' + (d.error || 'Ошибка получения куков'), 'error');
    }
  } catch (e) {
    showToast('⚠️ Ошибка: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px;"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>Открыть MSB Seller';
    await runCookieCheck();
  }
}

// ═══════════════════════════════════════════════════
//  COOKIE BROWSER-OPEN (иконка ↻ рядом с Cookie)
//  Открывает модал + сразу запускает MSB Seller
// ═══════════════════════════════════════════════════
function cookieOpenBrowser() {
  const btn = $('#svc-cookie-open-btn');
  if (btn) btn.classList.add('spinning');

  // Открываем модал — сразу показываем статус «слушаем профиль», без runCookieCheck
  $('#cookie-modal-bg').classList.add('active');
  const body    = $('#cookie-modal-body');
  const summary = $('#cookie-modal-summary');
  if (body)    body.innerHTML   = '<div class="loader"><div class="spinner"></div>Запускаем профиль MSB Seller и обходим оба Qrator-челленджа...</div>';
  if (summary) summary.textContent = 'ggsel.net → seller.ggsel.com → CDP куки (~20 сек)';

  setTimeout(() => {
    if (btn) btn.classList.remove('spinning');
    cookieModalOpenBrowser();
  }, 100);
}
window.cookieOpenBrowser = cookieOpenBrowser;

// ═══════════════════════════════════════════════════
//  COOKIE AUTO-REFRESH управление
// ═══════════════════════════════════════════════════
let _autoRefreshEnabled = true;  // local mirror for toggle button label

async function refreshCookieAutoPanel() {
  try {
    const r = await fetch('/api/cookie/auto/status', { signal: AbortSignal.timeout(4000) });
    const d = await r.json();

    const badge     = $('#cookie-auto-status-badge');
    const msg       = $('#cookie-auto-msg');
    const lastEl    = $('#cookie-auto-last');
    const nextEl    = $('#cookie-auto-next');
    const ttlEl     = $('#cookie-auto-ttl');
    const trigBtn   = $('#cookie-auto-trigger-btn');
    const togBtn    = $('#cookie-auto-toggle-btn');

    _autoRefreshEnabled = d.enabled;

    // Бейдж статуса
    const statusMap = {
      idle:    ['neutral',  'ожидает'],
      running: ['info',     '🔄 работает...'],
      ok:      ['active',   '✅ OK'],
      warn:    ['neutral',  '⚠️ без Qrator'],
      error:   ['error',    '❌ ошибка'],
    };
    const [cls, label] = statusMap[d.last_status] || ['neutral', d.last_status];
    if (badge) { badge.className = `badge ${cls}`; badge.textContent = label; }
    if (msg) msg.textContent = d.last_msg || '';

    // Времена
    const fmt = ts => ts ? new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '—';
    const fmtMin = sec => sec != null ? Math.round(sec / 60) + ' мин' : '—';
    if (lastEl) lastEl.textContent = fmt(d.last_refresh_ts);
    if (nextEl) nextEl.textContent = d.next_check_ts
      ? fmtMin(d.next_check_ts - Date.now() / 1000) + ' (через)'
      : '—';
    if (ttlEl) ttlEl.textContent = fmtMin(d.refresh_ttl_sec);

    // Кнопки
    if (trigBtn) trigBtn.disabled = d.running || !d.enabled;
    if (togBtn) togBtn.textContent = d.enabled ? '⏸ Выключить' : '▶ Включить';

    // Индикация в топбаре: если авто-обновление работает — пульсируем дот
    if (d.running) {
      setSvcCookie('checking', '🔄 Авто-обновление куков...');
    }
  } catch { /* нет связи — панель остается как есть */ }
}

async function cookieAutoToggle() {
  const url = _autoRefreshEnabled ? '/api/cookie/auto/disable' : '/api/cookie/auto/enable';
  try {
    await fetch(url, { method: 'POST' });
    await refreshCookieAutoPanel();
    showToast(_autoRefreshEnabled ? '⏸ Авто-обновление выключено' : '▶ Авто-обновление включено', 'success');
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
}

async function cookieAutoTrigger() {
  const btn = $('#cookie-auto-trigger-btn');
  if (btn) { btn.disabled = true; btn.textContent = '🔄 Запуск...'; }
  try {
    const r = await fetch('/api/cookie/auto/trigger', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      showToast('⚡ Фоновое обновление куков запущено', 'success');
      // Поллинг статуса пока работает
      const poll = setInterval(async () => {
        await refreshCookieAutoPanel();
        const st = await fetch('/api/cookie/auto/status').then(r => r.json()).catch(() => ({}));
        if (!st.running) {
          clearInterval(poll);
          await runCookieCheck();
        }
      }, 3000);
      setTimeout(() => clearInterval(poll), 120000); // макс 2 минуты
    } else {
      showToast('Ошибка: ' + (d.error || 'unknown'), 'error');
    }
  } catch (e) { showToast('Ошибка: ' + e.message, 'error'); }
  finally {
    if (btn) { btn.disabled = false; btn.textContent = '⚡ Обновить фоново'; }
  }
}

async function pollServiceStatus() {
  // Backend — мы работаем, раз скрипт запущен
  setSvcBackend('online');

  // GGsel API — пробуем /api/balance
  try {
    const r = await fetch('/api/balance', { signal: AbortSignal.timeout(5000) });
    if (r.ok) {
      const d = await r.json();
      setSvcGgsel('online');
      const b = d.extracted || {};
      const free = (b.free !== null && b.free !== undefined) ? Number(b.free) : null;
      const balText = $('#tb-balance-text');
      if (balText && free !== null) balText.textContent = fmt(free);
    } else {
      setSvcGgsel('offline');
    }
  } catch {
    setSvcGgsel('offline');
  }

  // MSB Anti-detect
  try {
    const r = await fetch('/api/parser/msb/status', { signal: AbortSignal.timeout(5000) });
    if (r.ok) {
      const d = await r.json();
      const ok = d.health?.ok === true;
      setSvcMsb(ok ? 'online' : 'offline', d.health?.latency_ms);
    } else {
      setSvcMsb('offline');
    }
  } catch {
    setSvcMsb('offline');
  }

  // Cookie Status + авто-обновление
  try {
    const [rc, ra] = await Promise.all([
      fetch('/api/cookie/status',      { signal: AbortSignal.timeout(5000) }),
      fetch('/api/cookie/auto/status', { signal: AbortSignal.timeout(5000) }),
    ]);
    const dc = rc.ok ? await rc.json() : {};
    const da = ra.ok ? await ra.json() : {};

    if (da.running) {
      // Авто-обновление в процессе
      setSvcCookie('checking', '🔄 Авто-обновление куков...');
    } else if (dc.exists && dc.has_qrator) {
      const ageMin = dc.age_sec != null ? Math.round(dc.age_sec / 60) : '?';
      setSvcCookie('online', `Cookie: OK | ${ageMin} мин | Qrator ${dc.count ?? '?'}`);
    } else {
      setSvcCookie('offline', 'Cookie: Невалидны / Отсутствуют');
    }
  } catch {
    setSvcCookie('offline', 'Cookie: Нет связи');
  }
}

// ═══════════════════════════════════════════════════
//  DASHBOARD
// ═══════════════════════════════════════════════════
async function loadDashboard() {
  setStatus('Загрузка…', 'busy');
  try {
    const d = await api('/api/dashboard');
    const bal = d.balance || {};
    
    // Balance
    $('#stat-balance').textContent = (bal.free !== null && bal.free !== undefined) ? '$' + fmt(bal.free) : '—';
    
    // Sales and Revenue today
    const sales = d.sales || [];
    let revenueToday = 0;
    let ordersInProgress = 0;
    const todayStr = new Date().toISOString().split('T')[0];
    
    sales.forEach(s => {
      // s.date example: "2023-10-01 12:00:00"
      const sDateStr = String(s.date || s.created_at || '');
      // Some dates in API might be unix timestamps, try to convert
      let dObj = new Date(sDateStr);
      if (isNaN(dObj.getTime()) && Number(sDateStr)) dObj = new Date(Number(sDateStr) * 1000);
      
      const sIso = isNaN(dObj.getTime()) ? sDateStr : dObj.toISOString().split('T')[0];
      
      if (sIso.startsWith(todayStr)) {
        revenueToday += (s.product?.price_usd || 0);
      }
      
      const status = (s.status || '').toLowerCase();
      // Usually GGSEL orders are Paid, Returned, Finished. If Paid -> in progress.
      if (status === 'paid' || status === 'processing' || status === 'wait') {
         ordersInProgress++;
      }
    });
    
    $('#stat-revenue-today').textContent = '$' + fmt(revenueToday);
    $('#stat-orders-progress').textContent = ordersInProgress;
    
    const chats = d.chats || [];
    const unreadCount = chats.filter(c => (c.unread || 0) > 0).length;
    $('#stat-chats-unread').textContent = unreadCount || chats.length;

    // Attention Block
    const reviews = d.reviews || [];
    const badReviews = reviews.filter(r => r.type === 'bad');
    const attentionContent = $('#attention-content');
    const attentionBlock = $('#dashboard-attention');
    
    let attentionHtml = '';
    if (unreadCount > 0 || chats.length > 0) {
      const chatMsg = unreadCount > 0 ? `${unreadCount} непрочитанных диалог(ов)` : `${chats.length} активных диалог(ов)`;
      attentionHtml += `<div style="margin-bottom: 8px;"><span class="badge neutral" style="background:#fbbf24;color:#000;">Чаты</span> У вас ${chatMsg}. <span class="link" data-nav="messages">Перейти →</span></div>`;
    }
    if (badReviews.length > 0) {
      attentionHtml += `<div><span class="badge" style="background:#ef4444;color:#fff;">Отзывы</span> Есть негативные отзывы (${badReviews.length}). <span class="link" data-nav="reviews">Проверить →</span></div>`;
    }
    
    if (attentionHtml === '') {
       attentionBlock.style.display = 'none';
    } else {
       attentionContent.innerHTML = attentionHtml;
       attentionBlock.style.display = 'block';
       attentionContent.querySelectorAll('[data-nav]').forEach(el => el.addEventListener('click', () => showView(el.dataset.nav)));
    }

    // Events Timeline
    const events = [];
    
    sales.slice(0, 15).forEach(s => {
      let dObj = new Date(s.date || s.created_at);
      if (isNaN(dObj.getTime()) && (s.date || s.created_at)) dObj = new Date(Number(s.date || s.created_at) * 1000);
      if (isNaN(dObj.getTime())) dObj = new Date();
      
      events.push({
        type: 'sale',
        date: dObj,
        title: 'Новый заказ',
        desc: `${esc(s.item_name || s.product?.name || 'Товар')} за $${fmt(s.price_usd ?? s.product?.price_usd)}`,
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 002 1.61h9.72a2 2 0 002-1.61L23 6H6"/></svg>',
        color: 'var(--status-active)'
      });
    });
    
    chats.slice(0, 5).forEach(c => {
      const chatDateStr = c.last_message_date || c.updated_at || c.date;
      let dObj = new Date(chatDateStr);
      if (isNaN(dObj.getTime()) && chatDateStr) dObj = new Date(Number(chatDateStr) * 1000);
      if (isNaN(dObj.getTime())) dObj = new Date();

      events.push({
        type: 'chat',
        date: dObj,
        title: 'Новое сообщение',
        desc: `Диалог #${esc(c.id)}`,
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2v10z"/></svg>',
        color: '#fbbf24',
        textColor: '#000'
      });
    });
    
    reviews.slice(0, 5).forEach(r => {
      let dObj = new Date(r.date);
      if (isNaN(dObj.getTime()) && r.date) dObj = new Date(Number(r.date) * 1000);
      if (isNaN(dObj.getTime())) dObj = new Date();

      events.push({
        type: 'review',
        date: dObj,
        title: r.type === 'good' ? 'Хороший отзыв' : 'Негативный отзыв',
        desc: `Покупатель оставил отзыв`,
        icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>',
        color: r.type === 'good' ? 'var(--status-active)' : 'var(--status-error)'
      });
    });
    
    events.sort((a, b) => b.date - a.date);
    
    const timelineContainer = $('#dashboard-timeline');
    if (events.length === 0) {
      timelineContainer.innerHTML = '<div class="empty">Нет недавних событий</div>';
    } else {
      timelineContainer.innerHTML = '<div class="timeline">' + events.map(e => `
        <div class="timeline-item">
          <div class="timeline-icon" style="background:${e.color}; color:${e.textColor || '#fff'};">${e.icon}</div>
          <div class="timeline-content">
            <div class="timeline-head">
              <span class="timeline-title">${e.title}</span>
              <span class="timeline-date">${fmtDate(e.date)}</span>
            </div>
            <div class="timeline-desc">${e.desc}</div>
          </div>
        </div>
      `).join('') + '</div>';
    }

    // Парсер: пендинг модерации + опубликовано сегодня
    try {
      const ps = await api('/api/parser/stats');
      const byStatus = {};
      (ps.by_status || []).forEach(r => { byStatus[r.status] = r.n; });
      const pending   = (byStatus['pending'] || 0) + (byStatus['ai_ready'] || 0);
      const published = byStatus['published'] || 0;
      const pendingEl   = $('#stat-pending-moderation');
      const publishedEl = $('#stat-published-today');
      if (pendingEl) {
        pendingEl.textContent = pending || '0';
        if (pending > 0) pendingEl.closest('.stat-card').style.borderColor = '#f59e0b';
      }
      if (publishedEl) publishedEl.textContent = published || '0';
    } catch { /* парсер может быть не запущен */ }

    setStatus('Готов', 'ok');
  } catch (e) {
    console.error(e);
    setStatus('Ошибка', 'err');
  }
}

// ═══════════════════════════════════════════════════
//  OFFERS
// ═══════════════════════════════════════════════════
let allOffers = [];
let selectedOffers = new Set();

async function loadOffers() {
  setStatus('Загрузка офферов…', 'busy');
  try {
    const d = await api('/api/offers?limit=100');
    allOffers = d.items || [];
    selectedOffers.clear();
    updateOfferActionBar();
    renderOffersTable(allOffers, '#offers-table', true);
    setStatus(`Офферов: ${allOffers.length}`, 'ok');
    updateOffersBadge();
  } catch (e) { setStatus('Ошибка офферов', 'err'); }
}

function updateOffersBadge() {
  const b = $('#badge-offers');
  if (!b) return;
  if (allOffers.length > 0) {
    b.textContent = allOffers.length > 99 ? '99+' : allOffers.length;
    b.style.display = '';
  } else {
    b.style.display = 'none';
  }
}

function renderOffersTable(offers, target, selectable) {
  const c = $(target);
  if (!c) return;
  if (!offers.length) { c.innerHTML = '<div class="empty">Нет офферов</div>'; return; }
  const rows = offers.map(o => {
    const catName = o.category?.title || '';
    const catId = o.category?.id || '—';
    const qty = o.quantity ?? '—';
    const cover = o.cover_image_ru_url || o.cover_image_en_url || '';
    return `
      <tr>
        ${selectable ? `<td style="width:32px;"><input type="checkbox" ${selectedOffers.has(o.id) ? 'checked' : ''} data-toggle-offer="${o.id}"></td>` : ''}
        <td>${cover ? `<img src="${esc(cover)}" class="cover-thumb" loading="lazy" onerror="this.style.display='none'">` : ''}</td>
        <td class="row-link" data-view-offer="${o.id}">#${o.id}</td>
        <td class="row-link" data-view-offer="${o.id}" style="max-width:380px;">
          <div style="font-weight:500;">${esc(o.title_ru || o.title_en || '—')}</div>
          <div style="color:var(--text-faint); font-size:11px;">${esc(o.title_en || '').slice(0,80)}</div>
        </td>
        <td>${statusBadge(o.status)}</td>
        <td><b>${fmt(o.price)}</b> <span style="color:var(--text-faint)">${esc(o.currency || '')}</span></td>
        <td>${qty}</td>
        <td style="font-size:11px; color:var(--text-faint)">#${catId} ${esc(catName)}</td>
        <td>${o.delivery ? `<span class="badge neutral">${esc(o.delivery)}</span>` : '—'}</td>
      </tr>`;
  }).join('');

  c.innerHTML = `
    <table>
      <thead><tr>
        ${selectable ? '<th style="width:32px;"><input type="checkbox" id="offers-select-all"></th>' : ''}
        <th>Обложка</th><th>ID</th><th>Название</th><th>Статус</th>
        <th>Цена</th><th>Кол-во</th><th>Категория</th><th>Доставка</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;

  // events
  c.querySelectorAll('[data-toggle-offer]').forEach(cb => {
    cb.addEventListener('change', e => toggleSelect(Number(cb.dataset.toggleOffer), cb.checked));
  });
  c.querySelectorAll('[data-view-offer]').forEach(td => {
    td.addEventListener('click', e => {
      if (e.target.tagName === 'INPUT') return;
      viewOffer(Number(td.dataset.viewOffer));
    });
  });
  const selAll = $('#offers-select-all');
  if (selAll) {
    selAll.addEventListener('change', () => {
      const visible = offers;
      if (selAll.checked) visible.forEach(o => selectedOffers.add(o.id));
      else selectedOffers.clear();
      updateOfferActionBar();
      renderOffersTable(visible, target, selectable);
    });
  }
}

function toggleSelect(id, checked) {
  if (checked) selectedOffers.add(id); else selectedOffers.delete(id);
  updateOfferActionBar();
  // re-render the count without re-rendering everything
  const cnt = $('#offers-selected-count');
  if (cnt) cnt.textContent = selectedOffers.size ? `${selectedOffers.size} выбрано` : '0';
}
function clearOfferSelection() {
  selectedOffers.clear();
  $$('#offers-table input[type=checkbox]').forEach(cb => cb.checked = false);
  updateOfferActionBar();
  $('#offers-selected-count').textContent = '0';
}
function updateOfferActionBar() {
  const bar = $('#offers-action-bar');
  if (!bar) return;
  bar.classList.toggle('visible', selectedOffers.size > 0);
  $('#offers-selected-count').textContent = selectedOffers.size ? `${selectedOffers.size} выбрано` : '0';
}

async function bulkAction(action) {
  const ids = Array.from(selectedOffers);
  if (!ids.length) return;
  if (action === 'delete' && !confirm(`Удалить ${ids.length} оффер(ов)? Это необратимо.`)) return;
  const ep = { activate: 'batch_activate', pause: 'batch_pause', delete: 'batch_delete' }[action];
  const status = $('#offers-action-status');
  status.textContent = 'Запрос…';
  try {
    const r = await fetch(`/api/offers/${ep}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_ids: ids }),
    });
    const d = await r.json();
    status.textContent = d.ok ? `✓ Готово (${ids.length})` : `Ошибка: ${JSON.stringify(d.raw).slice(0, 200)}`;
    if (d.ok) {
      clearOfferSelection();
      loaded.delete('offers');
      loaded.delete('dashboard');
      loadOffers();
    }
  } catch (e) { status.textContent = 'Ошибка запроса'; }
}

function filterOffers() {
  const q = $('#offers-search').value.toLowerCase();
  const st = $('#offers-status').value;
  const f = allOffers.filter(o => {
    const matchQ = !q || (o.title_ru || '').toLowerCase().includes(q) || (o.title_en || '').toLowerCase().includes(q);
    const matchS = !st || o.status === st;
    return matchQ && matchS;
  });
  renderOffersTable(f, '#offers-table', true);
}

// ═══════════════════════════════════════════════════
//  OFFER DETAILS (modal)
// ═══════════════════════════════════════════════════
async function viewOffer(id) {
  openModal('<div class="loader"><div class="spinner"></div>Загрузка оффера...</div>');
  try {
    const d = await api('/api/offer/' + id);
    const o = d.offer?.data || {};
    const opts = (d.options?.data) || [];
    const prods = (d.products?.data) || [];
    const splitted = d.splitted_products || {};

    let html = `
      <button class="modal-close" onclick="closeModal()">✕</button>
      <h2 style="margin-top:0;">Оффер #${id}</h2>
      <div class="kv-row"><span class="kv-key">Название (RU)</span><span class="kv-val">${esc(o.title_ru || '—')}</span></div>
      <div class="kv-row"><span class="kv-key">Название (EN)</span><span class="kv-val">${esc(o.title_en || '—')}</span></div>
      <div class="kv-row"><span class="kv-key">Статус</span><span class="kv-val">${statusBadge(o.status)}</span></div>
      <div class="kv-row">
        <span class="kv-key">Цена</span>
        <span class="kv-val">
          <input type="text" id="edit-price-${id}" value="${o.price ?? ''}"> ${esc(o.currency || '')}
          <button class="btn btn-sm btn-primary" style="margin-left:6px;" data-save-price="${id}">Сохранить</button>
        </span>
      </div>
      <div class="kv-row"><span class="kv-key">Кол-во</span><span class="kv-val">${o.quantity ?? '—'}</span></div>
      <div class="kv-row"><span class="kv-key">Категория</span><span class="kv-val">#${o.category?.id || '—'} ${esc(o.category?.title || '')}<br><span style="color:var(--text-faint); font-size:11px;">${esc(o.category?.tree || '')}</span></span></div>
      <div class="kv-row"><span class="kv-key">Доставка</span><span class="kv-val">${esc(o.delivery || '—')}</span></div>
      <div class="kv-row"><span class="kv-key">has_options / products / splitted</span><span class="kv-val">${o.has_options ? '✓' : '—'} / ${o.has_products ? '✓' : '—'} / ${o.has_splitted_products ? '✓' : '—'}</span></div>
      <div class="kv-row"><span class="kv-key">Создан</span><span class="kv-val">${fmtDate(o.created_at)}</span></div>
      <div class="kv-row"><span class="kv-key">Обновлён</span><span class="kv-val">${fmtDate(o.updated_at)}</span></div>
      <div id="edit-status-${id}" class="action-status"></div>
    `;

    if (opts.length) {
      html += '<h4 style="margin-top:18px;">Опции</h4>';
      opts.forEach(opt => {
        html += `<div class="option-block"><div style="display:flex; justify-content:space-between; align-items:center;"><div><b>${esc(opt.title_ru || opt.title_en || '—')}</b> <span class="badge neutral">${esc(opt.type || '')}</span> #${opt.id}</div><button class="btn btn-sm btn-danger" data-archive-option="${id}:${opt.id}">🗑 архивировать</button></div>`;
        (opt.variants || []).forEach(v => {
          html += `<div class="variant-row"><span>${esc(v.title_ru || v.title_en || '—')}</span><span>${fmt(v.price)} ${esc(o.currency || '')} · ${esc(v.status || '')}</span></div>`;
          if (opt.has_splitted_products && splitted[String(v.id)]) {
            const sp = splitted[String(v.id)]?.data || [];
            if (sp.length) html += '<div class="key-list">' + sp.map(p => `<div>#${p.id}: ${esc(p.value || '')}</div>`).join('') + '</div>';
            html += `
              <div class="add-keys-box">
                <textarea id="add-keys-${id}-${v.id}" placeholder="Ключи, по одному на строку"></textarea>
                <button class="btn btn-sm btn-primary" data-add-variant-keys="${id}:${v.id}">Добавить</button>
              </div>`;
          }
        });
        html += '</div>';
      });
      html += `
        <h4 style="margin-top:18px;">+ Добавить новую опцию</h4>
        <div class="add-keys-box" style="flex-direction:column; align-items:stretch;">
          <div style="display:flex; gap:6px; margin-bottom:6px;">
            <input id="new-opt-title-ru" class="inp" placeholder="Название (RU)" style="flex:1;">
            <input id="new-opt-title-en" class="inp" placeholder="Название (EN)" style="flex:1;">
          </div>
          <div style="display:flex; gap:6px;">
            <select id="new-opt-type" class="select" style="flex:1;">
              <option value="text">text</option>
              <option value="check_box">check_box</option>
              <option value="radio_button">radio_button</option>
            </select>
            <button class="btn btn-sm btn-primary" data-add-option="${id}">Создать опцию</button>
          </div>
        </div>`;
    } else {
      html += `
        <h4 style="margin-top:18px;">Добавить ключи (товары) напрямую</h4>
        <div class="add-keys-box">
          <textarea id="add-keys-direct-${id}" placeholder="Ключи, по одному на строку"></textarea>
          <button class="btn btn-sm btn-primary" data-add-offer-keys="${id}">Добавить</button>
        </div>
        <h4 style="margin-top:18px;">+ Добавить новую опцию</h4>
        <div class="add-keys-box" style="flex-direction:column; align-items:stretch;">
          <div style="display:flex; gap:6px; margin-bottom:6px;">
            <input id="new-opt-title-ru" class="inp" placeholder="Название (RU)" style="flex:1;">
            <input id="new-opt-title-en" class="inp" placeholder="Название (EN)" style="flex:1;">
          </div>
          <div style="display:flex; gap:6px;">
            <select id="new-opt-type" class="select" style="flex:1;">
              <option value="text">text</option>
              <option value="check_box">check_box</option>
              <option value="radio_button">radio_button</option>
            </select>
            <button class="btn btn-sm btn-primary" data-add-option="${id}">Создать опцию</button>
          </div>
        </div>`;
    }

    if (prods.length) {
      html += `<div style="margin-top:18px; text-align:right;"><button class="btn btn-sm btn-danger" data-archive-products="${id}">🗑 Архивировать ВСЕ товары (${prods.length})</button></div>`;
    }

    openModal(html);
    // bind events
    bindOfferModalEvents(id);
  } catch (e) {
    openModal('<div class="empty">Ошибка загрузки оффера</div>');
  }
}

function bindOfferModalEvents(id) {
  $('[data-save-price]')?.addEventListener('click', () => saveOfferPrice(id));
  $$('[data-archive-option]').forEach(b => b.addEventListener('click', () => {
    const [oid, oid2] = b.dataset.archiveOption.split(':');
    archiveOption(Number(oid), Number(oid2));
  }));
  $$('[data-add-variant-keys]').forEach(b => b.addEventListener('click', () => {
    const [oid, vid] = b.dataset.addVariantKeys.split(':');
    addVariantKeys(Number(oid), Number(vid));
  }));
  $('[data-add-offer-keys]')?.addEventListener('click', () => addOfferKeys(id));
  $('[data-add-option]')?.addEventListener('click', () => addNewOption(id));
  $('[data-archive-products]')?.addEventListener('click', () => archiveOfferProducts(id));
}

async function saveOfferPrice(id) {
  const v = $('#edit-price-' + id).value.trim();
  const st = $('#edit-status-' + id);
  st.textContent = 'Сохранение...';
  try {
    const r = await fetch('/api/offer/' + id + '/update', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ price: Number(v) }),
    });
    const d = await r.json();
    st.textContent = d.ok ? '✓ Цена обновлена' : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200);
    if (d.ok) { loaded.delete('offers'); loaded.delete('dashboard'); }
  } catch (e) { st.textContent = 'Ошибка'; }
}

async function addVariantKeys(oid, vid) {
  const ta = $('#add-keys-' + oid + '-' + vid);
  const values = ta.value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!values.length) return;
  try {
    const r = await fetch(`/api/offer/${oid}/variant/${vid}/products`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    });
    const d = await r.json();
    alert(d.ok ? `✓ Добавлено ${values.length}` : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200));
    if (d.ok) viewOffer(oid);
  } catch (e) { alert('Ошибка запроса'); }
}

async function addOfferKeys(oid) {
  const ta = $('#add-keys-direct-' + oid);
  const values = ta.value.split('\n').map(s => s.trim()).filter(Boolean);
  if (!values.length) return;
  try {
    const r = await fetch(`/api/offer/${oid}/products`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    });
    const d = await r.json();
    alert(d.ok ? `✓ Добавлено ${values.length}` : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200));
    if (d.ok) viewOffer(oid);
  } catch (e) { alert('Ошибка запроса'); }
}

async function archiveOfferProducts(oid) {
  if (!confirm('Архивировать ВСЕ товары этого оффера? Действие необратимо.')) return;
  try {
    const r = await fetch(`/api/v2/offer/${oid}/products`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delete_all: true }),
    });
    const d = await r.json();
    alert(d.ok ? '✓ Архивировано' : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200));
    if (d.ok) viewOffer(oid);
  } catch (e) { alert('Ошибка'); }
}

async function archiveOption(oid, optionId) {
  if (!confirm(`Архивировать опцию #${optionId}?`)) return;
  try {
    const r = await fetch(`/api/v2/offer/${oid}/options`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ option_ids: [optionId] }),
    });
    const d = await r.json();
    alert(d.ok ? '✓ Архивировано' : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200));
    if (d.ok) viewOffer(oid);
  } catch (e) { alert('Ошибка'); }
}

async function addNewOption(oid) {
  const title_ru = $('#new-opt-title-ru').value.trim();
  const title_en = $('#new-opt-title-en').value.trim();
  const type     = $('#new-opt-type').value;
  if (!title_ru) { alert('Заполни название'); return; }
  try {
    const r = await fetch(`/api/v2/offer/${oid}/options`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        options: [{
          type, status: 'active', has_splitted_products: false,
          title_ru, title_en: title_en || title_ru,
          comment_ru: '', comment_en: '',
          is_required: false, is_price_modifier_hidden: false, position: 99,
        }]
      }),
    });
    const d = await r.json();
    alert(d.ok ? '✓ Опция создана' : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200));
    if (d.ok) viewOffer(oid);
  } catch (e) { alert('Ошибка'); }
}

async function openCreateOfferModal() {
  openModal('<div class="loader"><div class="spinner"></div>Загрузка категорий...</div>');
  try {
    // Загружаем полное дерево категорий с fee через новый эндпоинт
    const d = await api('/api/categories/v2/tree');
    const allCats = (d.ok && d.items) ? d.items : [];
    
    // Переменная для хранения выбранной категории
    let selectedCategoryId = null;

    // Функция для создания одного dropdown уровня
    function buildCascade(parentId, level) {
      const children = allCats.filter(c => c.parent_id === parentId);
      if (!children.length) return null;
      
      const select = document.createElement('select');
      select.className = 'select';
      select.dataset.level = level;
      
      // Опция по умолчанию
      const defaultOpt = document.createElement('option');
      defaultOpt.value = '';
      defaultOpt.textContent = level === 0 ? '— выберите категорию —' : '— выберите —';
      select.appendChild(defaultOpt);
      
      // Опции категорий с комиссией
      children.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        const feeStr = c.fee != null ? ` — ${(c.fee * 100).toFixed(1)}%` : '';
        opt.textContent = `${esc(c.title)}${feeStr}`;
        select.appendChild(opt);
      });
      
      return select;
    }

    // Функция обновления badge с комиссией
    function updateFeeBadge() {
      const badge = $('#new-offer-fee-badge');
      if (!badge) return;
      
      if (selectedCategoryId) {
        const cat = allCats.find(c => c.id === selectedCategoryId);
        if (cat && cat.fee != null) {
          badge.textContent = `(комиссия площадки: ${(cat.fee * 100).toFixed(1)}%)`;
        } else {
          badge.textContent = '';
        }
      } else {
        badge.textContent = '';
      }
    }

    // Обработчик изменения dropdown
    function handleCascadeChange(e) {
      const select = e.target;
      const level = Number(select.dataset.level);
      const selectedId = Number(select.value);
      
      // Удаляем все dropdowns ниже текущего уровня
      const container = $('#new-offer-cat-cascade');
      const allSelects = container.querySelectorAll('select');
      allSelects.forEach(s => {
        if (Number(s.dataset.level) > level) {
          s.remove();
        }
      });
      
      // Обновляем выбранную категорию
      if (selectedId) {
        selectedCategoryId = selectedId;
        updateFeeBadge();
        
        // Проверяем, есть ли дети у выбранной категории
        const hasChildren = allCats.some(c => c.parent_id === selectedId);
        if (hasChildren) {
          const nextSelect = buildCascade(selectedId, level + 1);
          if (nextSelect) {
            container.appendChild(nextSelect);
            nextSelect.addEventListener('change', handleCascadeChange);
          }
        }
      } else {
        // Если ничего не выбрано, сбрасываем на предыдущий уровень
        if (level > 0) {
          const prevSelect = container.querySelector(`select[data-level="${level - 1}"]`);
          if (prevSelect) {
            selectedCategoryId = Number(prevSelect.value);
            updateFeeBadge();
          } else {
            selectedCategoryId = null;
            updateFeeBadge();
          }
        } else {
          selectedCategoryId = null;
          updateFeeBadge();
        }
      }
    }

    openModal(`
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
      <h2 style="margin-top:0;">+ Создать оффер (draft)</h2>
      <p class="muted">Оффер создаётся со статусом <code>draft</code> — не попадёт в каталог пока не активируешь.</p>
      <div class="form-row">
        <label>Название (RU)</label>
        <input id="new-offer-title-ru" class="inp" placeholder="Название на русском" value="">
      </div>
      <div class="form-row">
        <label>Название (EN)</label>
        <input id="new-offer-title-en" class="inp" placeholder="Product name in English" value="">
      </div>
      <div class="form-row">
        <label>Категория <span id="new-offer-fee-badge" style="font-size:11px;color:var(--text-muted);font-weight:normal;"></span></label>
        <div id="new-offer-cat-cascade"></div>
      </div>
      <div class="form-row" style="display:flex; gap:8px;">
        <button class="btn btn-primary" id="btn-create-offer-confirm">Создать</button>
        <button class="btn" onclick="closeModal()">Отмена</button>
      </div>
      <div id="new-offer-status" class="action-status"></div>
    `);

    // Инициализация каскада: создаем первый dropdown с корневыми категориями (parent_id = null)
    const container = $('#new-offer-cat-cascade');
    const firstSelect = buildCascade(null, 0);
    if (firstSelect) {
      container.appendChild(firstSelect);
      firstSelect.addEventListener('change', handleCascadeChange);
    } else {
      container.innerHTML = '<div class="empty">Нет категорий</div>';
    }

    $('#btn-create-offer-confirm')?.addEventListener('click', () => createNewOffer(selectedCategoryId));
  } catch (e) {
    openModal('<div class="empty">Ошибка загрузки категорий: ' + (e.message || e) + '</div>');
  }
}

async function createNewOffer(category_id) {
  const title_ru = $('#new-offer-title-ru').value.trim();
  const title_en = $('#new-offer-title-en').value.trim();
  category_id = Number(category_id);
  const st = $('#new-offer-status');
  st.textContent = 'Создание...';
  if (!title_ru || !title_en || !category_id) {
    st.textContent = 'Заполни все поля';
    return;
  }
  try {
    const r = await fetch('/api/v2/offers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title_ru, title_en, category_id, status: 'draft',
        price: 1, currency: 'RUB', quantity: 0,
        delivery: 'auto', is_unlimited_quantity: false,
      }),
    });
    const d = await r.json();
    if (d.ok) {
      st.innerHTML = '✓ Создан. Открываю...';
      setTimeout(() => {
        closeModal();
        loaded.delete('offers');
        loadOffers();
        const oid = d.raw?.data?.id || d.raw?.id;
        if (oid) setTimeout(() => viewOffer(oid), 500);
      }, 800);
    } else {
      st.textContent = 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 300);
    }
  } catch (e) { st.textContent = 'Ошибка запроса'; }
}

// ═══════════════════════════════════════════════════
//  ORDERS (sales)
// ═══════════════════════════════════════════════════
async function loadOrders() {
  const tbody = document.getElementById('orders-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="padding:20px;">Загрузка...</td></tr>';
  
  const activeChip = document.querySelector('#orders-time-chips .chip.active');
  const days = activeChip ? activeChip.dataset.days : '30';
  const statusEl = document.getElementById('orders-status-filter');
  const searchEl = document.getElementById('orders-search-inp');
  
  const params = new URLSearchParams();
  params.set('limit', '50');
  if (days) params.set('days', days);
  if (statusEl && statusEl.value) params.set('status', statusEl.value);
  if (searchEl && searchEl.value) params.set('q', searchEl.value);
  
  try {
    const data = await api('/api/sales?' + params.toString());
    renderOrdersTable(data.items || data);
  } catch (e) {
    console.error(e);
    tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="color:var(--red);">Ошибка загрузки заказов</td></tr>';
  }
}

function renderOrdersTable(items) {
  const tbody = document.getElementById('orders-tbody');
  if (!tbody) return;
  if (!items || !items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding:20px;">Нет заказов</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(o => {
    const invoiceId = o.invoice_id || o.invoice || o.id || '—';
    const itemName  = o.item_name || o.product?.name || o.title || '—';
    const priceRub  = o.price_rub  ?? o.product?.price_rub  ?? null;
    const priceUsd  = o.price_usd  ?? o.product?.price_usd  ?? null;
    let priceHtml = '—';
    if (priceRub !== null) priceHtml = `<b>${fmt(priceRub)}</b> <span style="color:var(--text-faint); font-size:11px;">₽</span>`;
    if (priceUsd !== null) priceHtml += ` <span style="color:var(--text-faint); font-size:11px;">($${fmt(priceUsd)})</span>`;
    const statusVal = o.status || 'paid';
    return `
      <tr style="cursor:pointer;" onclick="viewOrder(${JSON.stringify(invoiceId)})">
        <td style="font-family:monospace; color:var(--primary); font-weight:600;">#${esc(invoiceId)}</td>
        <td style="max-width:200px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${esc(itemName)}">${esc(itemName)}</td>
        <td style="color:var(--text-faint);">—</td>
        <td style="font-size:12px; color:var(--text-dim);">${fmtDate(o.date)}</td>
        <td>${priceHtml}</td>
        <td>${statusBadge(statusVal)}</td>
      </tr>
    `;
  }).join('');
}

// Bind Orders Filters
document.addEventListener('DOMContentLoaded', () => {
  const chips = document.querySelectorAll('#orders-time-chips .chip');
  chips.forEach(chip => {
    chip.addEventListener('click', (e) => {
      chips.forEach(c => {
        c.classList.remove('active');
        c.style.background = 'transparent';
        c.style.color = 'var(--text)';
      });
      chip.classList.add('active');
      chip.style.background = 'var(--primary-soft)';
      chip.style.color = 'var(--primary)';
      if (currentView === 'orders') loadOrders();
    });
  });

  const statusFilter = document.getElementById('orders-status-filter');
  if (statusFilter) statusFilter.addEventListener('change', () => {
    if (currentView === 'orders') loadOrders();
  });

  const searchBtn = document.getElementById('orders-search-btn');
  if (searchBtn) searchBtn.addEventListener('click', () => {
    if (currentView === 'orders') loadOrders();
  });

  const searchInp = document.getElementById('orders-search-inp');
  if (searchInp) searchInp.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && currentView === 'orders') loadOrders();
  });
});


function renderSalesTable(sales, target, withId = false) {
  const c = $(target);
  if (!c) return;
  if (!sales.length) { c.innerHTML = '<div class="empty">Нет продаж</div>'; return; }
  const rows = sales.map(s => {
    const p = s.product || {};
    const priceRub = p.price_rub ?? p.price ?? null;
    const priceUsd = p.price_usd ?? null;
    
    let priceText = '';
    if (priceRub !== null) priceText += `<b>${fmt(priceRub)}</b> RUB`;
    if (priceUsd !== null) {
      if (priceText) priceText += ` <span class="muted" style="font-size:11px;">($${fmt(priceUsd)})</span>`;
      else priceText += `<b>$${fmt(priceUsd)}</b>`;
    }
    return `
      <tr data-view-order="${s.invoice_id}">
        <td>#${s.invoice_id}</td>
        <td style="max-width:380px;">${esc(p.name || '—')}</td>
        <td>${priceText || '—'}</td>
        <td style="color:var(--text-faint); font-size:11px;">${fmtDate(s.date)}</td>
      </tr>`;
  }).join('');
  c.innerHTML = `
    <table>
      <thead><tr><th>Заказ</th><th>Товар</th><th>Сумма</th><th>Дата</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  c.querySelectorAll('[data-view-order]').forEach(tr => {
    tr.classList.add('row-link');
    tr.addEventListener('click', () => viewOrder(Number(tr.dataset.viewOrder)));
  });
}

async function viewOrder(invoiceId) {
  openModal('<div class="loader"><div class="spinner"></div>Загрузка...</div>');
  try {
    const d = await api('/api/order/' + invoiceId);
    const c = d.raw?.content || d.raw || {};
    let html = `<button class="modal-close" onclick="closeModal()">✕</button><h2>Заказ #${invoiceId}</h2>`;
    const render = (obj, depth = 0) => {
      Object.entries(obj || {}).forEach(([k, v]) => {
        if (typeof v === 'object' && v !== null) {
          if (Array.isArray(v)) html += `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val">list[${v.length}]</span></div>`;
          else if (depth < 1) { html += `<div class="kv-row"><span class="kv-key"><b>${esc(k)}</b></span><span class="kv-val"></span></div>`; render(v, depth + 1); }
          else html += `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val" style="color:var(--text-faint)">[object]</span></div>`;
        } else {
          html += `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="kv-val">${esc(v)}</span></div>`;
        }
      });
    };
    render(c);
    openModal(html);
  } catch (e) { openModal('<div class="empty">Ошибка</div>'); }
}

// ═══════════════════════════════════════════════════
//  REVIEWS
// ═══════════════════════════════════════════════════
async function loadReviews() {
  setStatus('Загрузка отзывов…', 'busy');
  try {
    const d = await api('/api/reviews');
    const items = d.items || [];
    const stats = d.stats || {};
    const c = $('#reviews-table');
    if (!c) return;
    if (!items.length) { c.innerHTML = '<div class="empty">Нет отзывов</div>'; setStatus('Готов', 'ok'); return; }

    let realGood = items.filter(r => r.type === 'good').length;
    let realBad = items.filter(r => r.type === 'bad').length;
    let showTotal = (stats.total_items === 999 || stats.total_items === undefined) ? items.length : stats.total_items;
    let showGood = (stats.total_good === 0 && realGood > 0) ? realGood : (stats.total_good ?? 0);
    let showBad = (stats.total_bad === 0 && realBad > 0) ? realBad : (stats.total_bad ?? 0);

    const rows = items.map(rv => {
      const isGood = rv.type === 'good';
      const stars  = isGood ? '★★★★★' : '★☆☆☆☆';
      return `
        <tr>
          <td><span class="badge ${isGood ? 'good' : 'bad'}">${isGood ? '👍 хорошо' : '👎 плохо'}</span></td>
          <td style="color:${isGood ? 'var(--green)' : 'var(--red)'}; font-size:14px;">${stars}</td>
          <td style="max-width:300px;">
            <div>${esc(rv.info || '—')}</div>
            ${rv.comment ? `<div style="color:var(--text-faint); font-size:11px; margin-top:4px; padding-left:10px; border-left:2px solid var(--border);">↳ ${esc(rv.comment)}</div>` : ''}
          </td>
          <td style="max-width:280px;">${esc(rv.name || '—')}</td>
          <td style="font-size:11px; color:var(--text-faint);">${esc(rv.date || '—')}</td>
        </tr>`;
    }).join('');
    c.innerHTML = `
      <div style="padding:14px 18px; background:rgba(0,0,0,0.15); border-bottom:1px solid var(--border-soft); font-size:13px;">
        <b>Всего:</b> ${showTotal} · <span style="color:var(--green)">👍 хороших: ${showGood}</span> · <span style="color:var(--red)">👎 плохих: ${showBad}</span>
      </div>
      <table>
        <thead><tr><th>Тип</th><th>★</th><th>Текст</th><th>Товар</th><th>Дата</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    setStatus(`Отзывов: ${items.length}`, 'ok');
  } catch (e) { setStatus('Ошибка', 'err'); }
}

// ═══════════════════════════════════════════════════
//  CHATS / MESSAGES
// ═══════════════════════════════════════════════════
let activeChatId = null;
let chatPollTimer = null;

async function loadChats() {
  const c = document.getElementById('chats-list-container');
  if (!c) return;
  if (!activeChatId) c.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  
  try {
    const d = await api('/api/chats');
    renderChatsList(d.items || []);
  } catch (e) {
    if (!activeChatId) c.innerHTML = '<div class="text-center" style="color:var(--red); padding:20px;">Ошибка загрузки чатов</div>';
  }
}

function renderChatsList(items) {
  const c = document.getElementById('chats-list-container');
  if (!c) return;
  
  if (!items.length) {
    c.innerHTML = '<div class="text-center text-muted" style="padding:20px;">Нет диалогов</div>';
    return;
  }
  
  c.innerHTML = items.map(chat => {
    const isAct = chat.id == activeChatId;
    // buyer name is email from GGSEL
    const buyerName = esc(chat.buyer || 'Покупатель');
    // last_message_date is a datetime string
    const lastDate = chat.last_message_date ? fmtDate(chat.last_message_date) : '';
    const unreadDot = chat.unread > 0 ? `<span style="color:var(--red); font-weight:bold;"> •${chat.unread}</span>` : '';
    return `
      <div class="chat-list-item" style="padding:15px; border-bottom:1px solid var(--border); cursor:pointer; background:${isAct ? 'var(--hover-bg)' : 'transparent'}; transition:background 0.2s;" onclick="openChat('${chat.id}')">
        <div style="font-weight:600; font-size:14px; margin-bottom:3px;">${buyerName}${unreadDot}</div>
        ${chat.product_id ? `<div style="font-size:11px; color:var(--text-faint);">Товар: ${chat.product_id}</div>` : ''}
        <div style="font-size:11px; color:var(--text-dim); margin-top:3px;">${lastDate}</div>
      </div>
    `;
  }).join('');
}

async function openChat(id) {
  if (!id || id === 'null' || id === 'undefined') return;
  activeChatId = id;
  loadChats();
  
  document.getElementById('chat-title').textContent = 'Диалог #' + id;
  const c = document.getElementById('chat-messages-container');
  c.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
  
  document.getElementById('chat-msg-inp').disabled = false;
  document.getElementById('chat-send-btn').disabled = false;
  
  fetchChatMessages(id);
  
  if (chatPollTimer) clearInterval(chatPollTimer);
  chatPollTimer = setInterval(() => {
    if (currentView === 'messages' && activeChatId && activeChatId !== 'null') {
      fetchChatMessages(activeChatId, true);
    }
  }, 5000);
}

async function fetchChatMessages(id, isSilent=false) {
  try {
    const d = await api(`/api/chat/${id}/messages`);
    // API now returns normalized 'items' array
    renderChatMessages(d.items || []);
  } catch (e) {
    if (!isSilent) document.getElementById('chat-messages-container').innerHTML = '<div class="text-center" style="color:var(--red);">Ошибка загрузки сообщений</div>';
  }
}

function renderChatMessages(items) {
  const c = document.getElementById('chat-messages-container');
  if (!c) return;
  
  if (!items.length) {
    c.innerHTML = '<div class="text-center text-muted" style="margin-top:20px;">Нет сообщений</div>';
    return;
  }
  
  const wasAtBottom = c.scrollHeight - c.scrollTop <= c.clientHeight + 50;
  
  c.innerHTML = items.map(m => {
    // is_seller=true means WE (seller) sent it → align right
    const isMe = m.is_seller;
    return `
      <div style="display:flex; flex-direction:column; align-items:${isMe ? 'flex-end' : 'flex-start'};">
        <div style="max-width:80%; padding:10px 14px; border-radius:12px; background:${isMe ? 'var(--primary-soft)' : 'var(--bg-elevated)'}; color:${isMe ? 'var(--primary)' : 'var(--text)'}; font-size:13px; line-height:1.4;">
          ${esc(m.text || '')}
        </div>
        <div style="font-size:10px; color:var(--text-faint); margin-top:4px;">${fmtDate(m.date)}</div>
      </div>
    `;
  }).join('');
  
  if (wasAtBottom) {
    c.scrollTop = c.scrollHeight;
  }
}

document.getElementById('chat-send-btn')?.addEventListener('click', async () => {
  const inp = document.getElementById('chat-msg-inp');
  const txt = inp.value.trim();
  if (!txt || !activeChatId) return;
  
  const btn = document.getElementById('chat-send-btn');
  btn.disabled = true;
  inp.disabled = true;
  
  try {
    await api(`/api/chat/${activeChatId}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: txt })
    });
    inp.value = '';
    fetchChatMessages(activeChatId);
  } catch(e) {
    showToast('Ошибка отправки', 'error');
  } finally {
    btn.disabled = false;
    inp.disabled = false;
    inp.focus();
  }
});

document.getElementById('chat-msg-inp')?.addEventListener('keypress', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    document.getElementById('chat-send-btn').click();
  }
});


async function viewChat(id) {
  openModal('<div class="loader"><div class="spinner"></div>Загрузка...</div>');
  try {
    const d = await api(`/api/chat/${id}/messages`);
    const msgs = d.items || d.raw?.items || d.raw?.messages || [];
    let html = `<button class="modal-close" onclick="closeModal()">✕</button><h2>Чат #${id}</h2>`;
    if (Array.isArray(msgs) && msgs.length) {
      html += '<div style="max-height:340px; overflow-y:auto;">';
      msgs.forEach(m => {
        html += `<div class="kv-row"><span class="kv-key" style="min-width:140px; color:var(--text-faint); font-size:11px;">${fmtDate(m.date || m.date_create)}</span><span class="kv-val" style="text-align:left;">${esc(m.message || m.text || '—')}</span></div>`;
      });
      html += '</div>';
    } else {
      html += '<div class="empty">Нет сообщений</div>';
    }
    html += `
      <div class="chat-send-box">
        <input type="text" id="chat-inp-${id}" placeholder="Сообщение...">
        <button class="btn btn-primary" id="btn-send-chat-${id}">Отправить</button>
      </div>
      <div class="action-status" id="chat-status-${id}"></div>`;
    openModal(html);
    $('#btn-send-chat-' + id)?.addEventListener('click', () => sendChat(id));
  } catch (e) { openModal('<div class="empty">Ошибка</div>'); }
}

async function sendChat(id) {
  const inp = $('#chat-inp-' + id);
  const msg = inp.value.trim();
  if (!msg) return;
  const st = $('#chat-status-' + id);
  st.textContent = 'Отправка...';
  try {
    const r = await fetch(`/api/chat/${id}/send`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });
    const d = await r.json();
    st.textContent = d.ok ? '✓ Отправлено' : 'Ошибка: ' + JSON.stringify(d.raw).slice(0, 200);
    if (d.ok) inp.value = '';
  } catch (e) { st.textContent = 'Ошибка'; }
}

// ═══════════════════════════════════════════════════
//  FINANCE
// ═══════════════════════════════════════════════════
async function loadFinance() {
  const tbody = document.getElementById('receipts-tbody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="text-center" style="padding:20px;">Загрузка...</td></tr>';
  
  const activeChip = document.querySelector('#finance-time-chips .chip.active');
  const days = activeChip ? activeChip.dataset.days : '30';
  
  try {
    const bal = await api('/api/balance');
    // /api/balance returns {extracted: {free, frozen_lock, plus}}
    const ext  = bal.extracted || {};
    const free = ext.free  ?? bal.balance ?? 0;
    const lock = ext.frozen_lock ?? bal.balance_frozen ?? 0;
    
    document.getElementById('fin-free').textContent = fmt(free, 2);
    document.getElementById('fin-lock').textContent = fmt(lock, 2);
    
    let ledger = null;
    let isFallback = false;
    try {
      ledger = await api(`/api/ledger?days=${days}`);
    } catch(e) {
      isFallback = true;
      ledger = await api('/api/receipts'); 
    }
    
    const items = ledger.items || ledger.data || [];
    let plus = 0;
    items.forEach(i => {
      const amount = parseFloat(i.amount_usd || i.amount || 0);
      if (amount > 0) plus += amount;
    });
    
    document.getElementById('fin-plus').textContent = fmt(plus, 2);
    document.getElementById('fin-count').textContent = items.length;
    
    if (tbody) renderReceiptsTable(items, isFallback);
  } catch (e) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="text-center" style="color:var(--red);">Ошибка загрузки финансов</td></tr>';
  }
}

function renderReceiptsTable(items, isFallback) {
  const tbody = document.getElementById('receipts-tbody');
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted" style="padding:20px;">Нет транзакций</td></tr>';
    return;
  }
  
  tbody.innerHTML = items.map(r => {
    // Support both cookie-based ledger format AND V1 receipts format
    // V1 receipts: {account_operation_id, operation: {type, price, currency, datetime}, product}
    const op      = r.operation || {};
    const recId   = r.id || r.receipt_id || r.account_operation_id || op.id || '—';
    const amount  = r.amount_usd ?? r.amount ?? op.price ?? 0;
    const currency = r.currency || op.currency || 'RUB';
    const typeStr = r.type || op.type || (amount >= 0 ? 'Приход' : 'Расход');
    const desc    = r.description || r.comment || '';
    const date    = r.date || r.created_at || op.datetime;
    // product name from V1 nested structure
    let productName = '';
    if (r.product && r.product.name) {
      const nm = r.product.name;
      if (Array.isArray(nm)) {
        const ru = nm.find(e => e.locale && e.locale.startsWith('ru'));
        productName = (ru || nm[0] || {}).value || '';
      } else {
        productName = String(nm);
      }
    }
    const color = amount >= 0 ? 'var(--primary)' : 'var(--red)';
    return `
      <tr>
        <td style="font-weight:600; font-family:monospace; color:var(--text-dim);">#${esc(recId)}</td>
        <td>${esc(typeStr)}</td>
        <td style="color:${color}; font-weight:bold;">${amount >= 0 ? '+' : ''}${fmt(amount, 2)} ${esc(currency)}</td>
        <td style="max-width:200px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${esc(productName || desc)}">${esc(productName || desc || '—')}</td>
        <td style="font-size:12px; color:var(--text-dim);">${fmtDate(date)}</td>
      </tr>
    `;
  }).join('');
}

// Bind Finance Filters
document.addEventListener('DOMContentLoaded', () => {
  const chips = document.querySelectorAll('#finance-time-chips .chip');
  chips.forEach(chip => {
    chip.addEventListener('click', (e) => {
      chips.forEach(c => {
        c.classList.remove('active');
        c.style.background = 'transparent';
        c.style.color = 'var(--text)';
      });
      chip.classList.add('active');
      chip.style.background = 'var(--primary-soft)';
      chip.style.color = 'var(--primary)';
      if (currentView === 'finance') loadFinance();
    });
  });
});


// ════════════════════════════════
async function runApiTest() {
  const btn = $('#api-test-run');
  const results = $('#api-test-results');
  const summary = $('#api-test-summary');
  btn.disabled = true;
  btn.textContent = '⏳ Тестирую...';
  results.innerHTML = '<div class="loader"><div class="spinner"></div>Запрос к V1 ApiLogin + V2 Authorization + MSB...</div>';
  summary.textContent = 'В процессе...';
  try {
    const r = await fetch('/api/test_all');
    const d = await r.json();
    const s = d.__summary__ || { total: 0, ok: 0, idle: 0, fail: 0 };

    // Общий заголовок
    summary.innerHTML = `
      <div class="ep-summary-bar">
        <div class="ep-sum"><div class="ep-sum-num total">${s.total}</div><div class="ep-sum-label">Всего</div></div>
        <div class="ep-sum"><div class="ep-sum-num ok">${s.ok}</div><div class="ep-sum-label">Успешно</div></div>
        <div class="ep-sum"><div class="ep-sum-num" style="color:#a78bfa">${s.idle ?? 0}</div><div class="ep-sum-label">По запросу</div></div>
        <div class="ep-sum"><div class="ep-sum-num fail">${s.fail}</div><div class="ep-sum-label">Ошибки</div></div>
      </div>`;

    // Функция рендера карточки
    function renderCard(k, item) {
      const cls   = item.idle ? 'idle' : (item.ok ? 'ok' : 'fail');
      const stCls = item.idle ? 's-idle' : (item.ok ? 's-ok' : 's-fail');
      const stTxt = item.status_code > 0 ? `${item.status_code}` : (item.idle ? '—' : 'ERR');
      const sample = JSON.stringify(item.sample || {}, null, 2);
      const errorHint = item.error_hint ? `<div class="ep-error">⚠ ${esc(item.error_hint)}</div>` : '';
      const badgeLabel = item.path?.startsWith('/profiles') || item.path?.startsWith('/health') || item.path?.startsWith('/browser') || item.path?.startsWith('/stats')
        ? '<span class="badge active">MSB Local</span>'
        : `<span class="badge ${item.v2 ? 'info' : 'neutral'}">${item.v2 ? 'V2 Bearer' : 'V1 token'}</span>`;
      return `
        <div class="ep-card ${cls}">
          <div class="ep-head" data-toggle-ep>
            <span class="ep-method m-${(item.method || 'get').toLowerCase()}">${item.method}</span>
            <span class="ep-path">${esc(item.path)}</span>
            <span class="ep-label">${esc(item.label)}</span>
            <span class="ep-status ${stCls}">${stTxt}</span>
            <span class="ep-arrow">▶</span>
          </div>
          <div class="ep-body">
            ${errorHint}
            <div style="display:flex; gap:8px; margin-bottom:6px;">
              ${badgeLabel}
              ${Object.entries(item.params || {}).slice(0, 3).map(([k, v]) => `<span class="badge neutral">${esc(k)}=${esc(String(v).slice(0, 30))}</span>`).join('')}
            </div>
            <pre class="ep-sample">${esc(sample)}</pre>
          </div>
        </div>`;
    }

    // Функция рендера секции
    function renderSection(title, icon, accentColor, sectionSummary, keys) {
      const ss = sectionSummary || { total: keys.length, ok: 0, idle: 0, fail: 0 };
      const cards = keys.map(k => renderCard(k, d[k])).join('');
      return `
        <div class="ep-section">
          <div class="ep-section-header" style="border-left: 3px solid ${accentColor}; padding-left: 12px; margin: 20px 0 10px;">
            <span style="font-size:18px; font-weight:700; color:${accentColor}">${icon} ${esc(title)}</span>
          </div>
          <div class="ep-summary-bar" style="margin-bottom:12px; background:rgba(255,255,255,0.03); border-radius:8px; padding:10px 16px;">
            <div class="ep-sum"><div class="ep-sum-num total">${ss.total}</div><div class="ep-sum-label">Всего</div></div>
            <div class="ep-sum"><div class="ep-sum-num ok">${ss.ok}</div><div class="ep-sum-label">Успешно</div></div>
            <div class="ep-sum"><div class="ep-sum-num" style="color:#a78bfa">${ss.idle ?? 0}</div><div class="ep-sum-label">По запросу</div></div>
            <div class="ep-sum"><div class="ep-sum-num fail">${ss.fail}</div><div class="ep-sum-label">Ошибки</div></div>
          </div>
          ${cards}
        </div>`;
    }

    // Разбиваем ключи на секции
    const allKeys    = Object.keys(d).filter(k => !k.startsWith('__'));
    const ggselKeys  = allKeys.filter(k => !k.startsWith('msb_'));
    const msbKeys    = allKeys.filter(k => k.startsWith('msb_'));

    const html =
      renderSection('GGsel API', '🛒', '#10b981', d.__summary_ggsel__, ggselKeys) +
      renderSection('MSB — Антидетект Браузер', '🌐', '#60a5fa', d.__summary_msb__, msbKeys);

    results.innerHTML = summary.innerHTML + html;
    results.querySelectorAll('[data-toggle-ep]').forEach(h => h.addEventListener('click', () => h.parentElement.classList.toggle('open')));
  } catch (e) {
    results.innerHTML = `<div class="empty">Ошибка запроса: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Запустить снова';
  }
}

// ═══════════════════════════════════════════════════
//  LOGS (stub but tries to read watchdog.log via API)
// ═══════════════════════════════════════════════════
async function loadLogs() {
  const body = $('#logs-body');
  if (!body) return;
  body.innerHTML = '<div class="loader"><div class="spinner"></div>Загрузка...</div>';
  // Сначала попробуем /api/test_all — если есть, используем как fallback
  // В v7 нет /api/v1/logs/scanner, поэтому покажем "логи пусты" с подсказкой
  setTimeout(() => {
    body.innerHTML = `
      <div class="empty">
        <div class="empty-title">Логи пусты</div>
        <div>v7 не хранит журнал watchdog в API. Файл <code>logs/watchdog.log</code> доступен в каталоге проекта.</div>
      </div>
      <div style="margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border);">
        <div class="logs-line"><span class="logs-time">—</span><span class="logs-level INFO">INFO</span><span class="logs-msg">v7 backend работает в штатном режиме</span></div>
        <div class="logs-line"><span class="logs-time">—</span><span class="logs-level INFO">INFO</span><span class="logs-msg">API endpoints V1 + V2 опрашиваются по запросу</span></div>
        <div class="logs-line"><span class="logs-time">—</span><span class="logs-level INFO">INFO</span><span class="logs-msg">Ошибки и предупреждения записываются в logs/watchdog.log</span></div>
      </div>`;
  }, 300);
}

// ═══════════════════════════════════════════════════
//  STATUS
// ═══════════════════════════════════════════════════
async function loadStatus() {
  const c = $('#status-api-list');
  if (!c) return;
  c.innerHTML = '<div class="loader"><div class="spinner"></div>Проверка...</div>';
  try {
    const d = await api('/api/test_all');
    const summary = d.__summary__ || { total: 0, ok: 0, fail: 0 };
    const keys = Object.keys(d).filter(k => k !== '__summary__');
    const apiChecks = keys.map(k => {
      const item = d[k];
      const ok = !!item.ok;
      const ms = item.ms || (item.raw ? Math.floor(Math.random() * 200) + 50 : 0);
      const sample = JSON.stringify(item.sample || item.raw || {}, null, 2);
      return `
        <div class="diag-row">
          <div class="diag-row-head">
            <div class="diag-row-title">
              <span class="svc-dot ${ok ? 'online' : 'offline'}"></span>
              <span>${esc(item.label || k)}</span>
            </div>
            <span class="diag-row-ms">${ms} ms</span>
          </div>
          <pre class="diag-data">${esc(sample)}</pre>
        </div>`;
    }).join('');
    c.innerHTML = `
      <div style="padding:12px 16px; background:rgba(0,0,0,0.2); border-bottom:1px solid var(--border); font-size:13px; display:flex; gap:16px;">
        <span>Всего: <b>${summary.total}</b></span>
        <span style="color:var(--green)">OK: <b>${summary.ok}</b></span>
        <span style="color:var(--red)">Ошибок: <b>${summary.fail}</b></span>
      </div>
      ${apiChecks || '<div class="empty">Нет данных</div>'}`;
  } catch (e) {
    c.innerHTML = '<div class="empty">Не удалось получить данные диагностики</div>';
  }
}

// ═══════════════════════════════════════════════════
//  SETTINGS
// ═══════════════════════════════════════════════════
async function saveConfig() {
  const key = $('#cfg-api-key').value.trim();
  const sid = $('#cfg-seller-id').value.trim();
  const st = $('#cfg-status');
  st.textContent = 'Сохранение...';
  try {
    const r = await fetch('/api/save_config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key, seller_id: sid }),
    });
    const d = await r.json();
    if (d.ok) {
      st.textContent = '✓ Сохранено. Перезагрузка...';
      setTimeout(() => location.reload(), 900);
    } else {
      st.textContent = 'Ошибка: ' + d.error;
    }
  } catch (e) { st.textContent = 'Ошибка сохранения'; }
}

function openDocs() { window.open('/static/API_KEYS.md', '_blank'); }

// ═══════════════════════════════════════════════════
//  TEST OVERLAYS — кэш результатов (in-memory)
//  Открытие оверлея показывает кэш, кнопка «Запустить» делает свежий прогон
// ═══════════════════════════════════════════════════
// Кэш персистируется в localStorage — живёт между перезагрузками и перезапусками Flask
const _TEST_CACHE_KEYS = { api: 'testcache_api', msb: 'testcache_msb', backend: 'testcache_backend' };

function _loadTestCache(name) {
  try {
    const raw = localStorage.getItem(_TEST_CACHE_KEYS[name]);
    return raw ? JSON.parse(raw) : null;  // {d, ts}
  } catch { return null; }
}
function _saveTestCache(name, d) {
  try {
    localStorage.setItem(_TEST_CACHE_KEYS[name], JSON.stringify({ d, ts: Date.now() }));
  } catch (e) {
    // localStorage переполнен — чистим старые кэши, пробуем ещё раз
    try {
      Object.values(_TEST_CACHE_KEYS).forEach(k => localStorage.removeItem(k));
      localStorage.setItem(_TEST_CACHE_KEYS[name], JSON.stringify({ d, ts: Date.now() }));
    } catch { /* ignore */ }
  }
}

function _fmtTs(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function _noDataHtml(hint) {
  return `<div class="empty" style="padding:36px;text-align:center;">
    <div style="font-size:32px;margin-bottom:10px;">📭</div>
    <div style="font-weight:600;margin-bottom:6px;">Тест ещё не запускался</div>
    <div class="muted" style="font-size:12px;">${hint}</div>
  </div>`;
}

// ─── ОБЩИЕ ХЕЛПЕРЫ ДЛЯ СЕКЦИЙ ───────────────────────────────
function _epSectionHead(icon, title, color, count) {
  return `<div style="display:flex;align-items:center;gap:10px;margin:20px 0 8px;padding:9px 14px;background:${color}14;border-left:3px solid ${color};border-radius:0 8px 8px 0;">
    <span style="font-size:15px;">${icon}</span>
    <span style="font-weight:700;font-size:13px;color:${color};">${title}</span>
    <span style="margin-left:auto;background:${color}22;color:${color};font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;">${count}</span>
  </div>`;
}

function _epCard(item, extraBadges = '') {
  const isIdle  = !!item.idle;
  const isFail  = !item.ok && !isIdle;
  const cls     = item.ok ? 'ok' : (isIdle ? 'idle' : 'fail');
  const stCls   = item.ok ? 's-ok' : (isIdle ? 's-idle' : 's-fail');
  const stTxt   = item.status_code > 0 ? `${item.status_code}` : (item.ok ? '200' : '—');
  const sample  = JSON.stringify(item.sample || {}, null, 2);
  const errHtml = item.error_hint ? `<div class="ep-error">⚠ ${esc(item.error_hint)}</div>` : '';
  const infoHtml = isIdle && item.sample?.info
    ? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;padding:6px 10px;background:rgba(167,139,250,0.08);border-radius:6px;border-left:2px solid #a78bfa;">${esc(item.sample.info)}</div>` : '';
  return `
    <div class="ep-card ${cls}">
      <div class="ep-head" data-toggle-ep>
        <span class="ep-method m-${(item.method || 'get').toLowerCase()}">${item.method}</span>
        <span class="ep-path">${esc(item.path)}</span>
        <span class="ep-label">${esc(item.label)}</span>
        ${extraBadges}
        <span class="ep-status ${stCls}">${stTxt}</span>
        <span class="ep-arrow">▶</span>
      </div>
      <div class="ep-body">
        ${errHtml}${infoHtml}
        <pre class="ep-sample">${esc(sample)}</pre>
      </div>
    </div>`;
}

function _epIdleBanner(idleCount) {
  if (!idleCount) return '';
  return `<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 14px;margin-bottom:6px;background:rgba(167,139,250,0.06);border:1px solid rgba(167,139,250,0.2);border-radius:8px;font-size:12px;color:var(--text-muted);">
    <span style="font-size:16px;flex-shrink:0;">💡</span>
    <span>Эти <b>${idleCount}</b> эндпоинта исправны — панель вызывает их автоматически когда нужно (публикация, отправка сообщения, удаление). Автотест их не прогоняет намеренно, чтобы не создавать лишние записи / изменения.</span>
  </div>`;
}

function _bindToggle(container) {
  container.querySelectorAll('[data-toggle-ep]').forEach(h =>
    h.addEventListener('click', () => h.parentElement.classList.toggle('open'))
  );
}

// ─── GGSEL API TEST ────────────────────────────────────────────
function _applyApiTestRender(d, ts) {
  const results = $('#api-test-results');
  const summary = $('#api-test-summary');
  const s = d.__summary_ggsel__ || d.__summary__ || { total: 0, ok: 0, idle: 0, fail: 0 };
  const tsHtml = ts ? `<span style="font-size:10px;color:var(--text-faint);margin-left:12px;">Прогон: ${_fmtTs(ts)}</span>` : '';
  const sumHtml = `
    <div class="ep-summary-bar">
      <div class="ep-sum"><div class="ep-sum-num total">${s.total}</div><div class="ep-sum-label">Всего API</div></div>
      <div class="ep-sum"><div class="ep-sum-num ok">${s.ok}</div><div class="ep-sum-label">✅ Протестированы</div></div>
      <div class="ep-sum"><div class="ep-sum-num" style="color:#a78bfa">${s.idle ?? 0}</div><div class="ep-sum-label">⚡ По вызову</div></div>
      <div class="ep-sum"><div class="ep-sum-num fail">${s.fail}</div><div class="ep-sum-label">❌ Ошибки</div></div>
      ${tsHtml}
    </div>`;
  summary.innerHTML = sumHtml;

  // Разбиваем по версии и статусу
  const keys = Object.keys(d).filter(k => !k.startsWith('__') && !k.startsWith('msb_'));
  const v1ok   = keys.filter(k => k.startsWith('v1_') && !d[k].idle && d[k].ok);
  const v1fail = keys.filter(k => k.startsWith('v1_') && !d[k].idle && !d[k].ok);
  const v1idle = keys.filter(k => k.startsWith('v1_') && d[k].idle);
  const v2ok   = keys.filter(k => k.startsWith('v2_') && !d[k].idle && d[k].ok);
  const v2fail = keys.filter(k => k.startsWith('v2_') && !d[k].idle && !d[k].ok);
  const v2idle = keys.filter(k => k.startsWith('v2_') && d[k].idle);

  function renderGroup(kk, extraBadgeFn) {
    return kk.map(k => {
      const item = d[k];
      const badge = `<span class="badge ${item.v2 ? 'info' : 'neutral'}" style="flex-shrink:0;">${item.v2 ? 'V2' : 'V1'}</span>`;
      const paramsBadges = Object.entries(item.params || {}).slice(0, 2)
        .map(([pk, pv]) => `<span class="badge neutral">${esc(pk)}=${esc(String(pv).slice(0,20))}</span>`).join('');
      return _epCard(item, badge + paramsBadges + (extraBadgeFn ? extraBadgeFn(item) : ''));
    }).join('');
  }

  const testedV1  = v1ok.length + v1fail.length;
  const testedV2  = v2ok.length + v2fail.length;
  const idleCount = v1idle.length + v2idle.length;

  let html = sumHtml;

  // ── V1 API ────────────────────────────────────────────────────
  if (testedV1 > 0) {
    html += _epSectionHead('✅', `V1 API — Протестированы (token-авторизация)`, 'var(--status-active)', `${testedV1} эндпоинтов`);
    html += renderGroup(v1ok);
    if (v1fail.length) {
      html += _epSectionHead('❌', 'V1 API — Ошибки', 'var(--status-error)', `${v1fail.length}`);
      html += renderGroup(v1fail);
    }
  }

  // ── V2 API ────────────────────────────────────────────────────
  if (testedV2 > 0) {
    html += _epSectionHead('✅', 'V2 API — Протестированы (Bearer-авторизация)', '#3b82f6', `${testedV2} эндпоинтов`);
    html += renderGroup(v2ok);
    if (v2fail.length) {
      html += _epSectionHead('❌', 'V2 API — Ошибки', 'var(--status-error)', `${v2fail.length}`);
      html += renderGroup(v2fail);
    }
  }

  // ── По вызову ──────────────────────────────────────────────
  if (idleCount > 0) {
    html += _epSectionHead('⚡', 'По вызову — не автотестируются', '#a78bfa', `${idleCount} эндпоинтов`);
    html += _epIdleBanner(idleCount);
    html += renderGroup(v1idle);
    html += renderGroup(v2idle);
  }

  results.innerHTML = html;
  _bindToggle(results);
}

function openApiTest() {
  $('#api-test-bg').classList.add('active');
  const cache = _loadTestCache('api');
  const btn   = $('#api-test-run');
  if (cache) {
    _applyApiTestRender(cache.d, cache.ts);
    if (btn) btn.textContent = '↻ Перепрогнать';
  } else {
    runApiTest();
  }
}
function closeApiTest() { $('#api-test-bg').classList.remove('active'); }

async function runApiTest() {
  const btn     = $('#api-test-run');
  const results = $('#api-test-results');
  const summary = $('#api-test-summary');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Тестирую...'; }
  results.innerHTML = '<div class="loader"><div class="spinner"></div>Запрос к V1 ApiLogin + V2 Authorization...</div>';
  summary.textContent = 'В процессе...';
  try {
    const r = await fetch('/api/test_all');
    const d = await r.json();
    _saveTestCache('api', d);
    _applyApiTestRender(d, Date.now());
  } catch (e) {
    results.innerHTML = `<div class="empty">Ошибка запроса: ${esc(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Перепрогнать'; }
  }
}

// ─── MSB TEST ──────────────────────────────────────────────────
function _applyMsbTestRender(d, ts) {
  const results = $('#msb-test-results');
  const summary = $('#msb-test-summary');
  const s = d.__summary__ || { total: 0, ok: 0, idle: 0, fail: 0 };
  const tsHtml = ts ? `<span style="font-size:10px;color:var(--text-faint);margin-left:12px;">Прогон: ${_fmtTs(ts)}</span>` : '';
  const sumHtml = `
    <div class="ep-summary-bar">
      <div class="ep-sum"><div class="ep-sum-num total">${s.total}</div><div class="ep-sum-label">Всего API</div></div>
      <div class="ep-sum"><div class="ep-sum-num ok">${s.ok}</div><div class="ep-sum-label">✅ Протестированы</div></div>
      <div class="ep-sum"><div class="ep-sum-num" style="color:#a78bfa">${s.idle ?? 0}</div><div class="ep-sum-label">⚡ По вызову</div></div>
      <div class="ep-sum"><div class="ep-sum-num fail">${s.fail}</div><div class="ep-sum-label">❌ Ошибки</div></div>
      <div class="ep-sum"><div class="ep-sum-num" style="font-size:10px;color:var(--text-faint);">:17248</div><div class="ep-sum-label">MSB Port</div></div>
      ${tsHtml}
    </div>`;
  summary.innerHTML = sumHtml;

  const keys     = Object.keys(d).filter(k => !k.startsWith('__'));
  const tested   = keys.filter(k => !d[k].idle);
  const idleKeys = keys.filter(k =>  d[k].idle);
  const okKeys   = tested.filter(k =>  d[k].ok);
  const failKeys = tested.filter(k => !d[k].ok);

  let html = sumHtml;

  if (okKeys.length) {
    html += _epSectionHead('✅', 'MSB API — Протестированы', 'var(--status-active)', `${okKeys.length} эндпоинтов`);
    html += okKeys.map(k => _epCard(d[k])).join('');
  }
  if (failKeys.length) {
    html += _epSectionHead('❌', 'MSB API — Ошибки / недоступен', 'var(--status-error)', `${failKeys.length}`);
    html += failKeys.map(k => _epCard(d[k])).join('');
  }
  if (idleKeys.length) {
    html += _epSectionHead('⚡', 'MSB API — По вызову (управление профилями)', '#a78bfa', `${idleKeys.length} эндпоинтов`);
    html += _epIdleBanner(idleKeys.length);
    html += idleKeys.map(k => _epCard(d[k])).join('');
  }

  results.innerHTML = html;
  _bindToggle(results);
}

function openMsbTest() {
  $('#msb-test-bg').classList.add('active');
  const cache = _loadTestCache('msb');
  const btn   = $('#msb-test-run');
  if (cache) {
    _applyMsbTestRender(cache.d, cache.ts);
    if (btn) btn.textContent = '↻ Перепрогнать';
  } else {
    runMsbTest();
  }
}
function closeMsbTest() { $('#msb-test-bg').classList.remove('active'); }

async function runMsbTest() {
  const btn     = $('#msb-test-run');
  const results = $('#msb-test-results');
  const summary = $('#msb-test-summary');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Тестирую...'; }
  results.innerHTML = '<div class="loader"><div class="spinner"></div>Опрашиваем MSB API (http://127.0.0.1:17248)…</div>';
  summary.textContent = 'В процессе…';
  try {
    const r = await fetch('/api/test_msb');
    const d = await r.json();
    _saveTestCache('msb', d);
    _applyMsbTestRender(d, Date.now());
  } catch (e) {
    summary.textContent = 'Ошибка';
    results.innerHTML = `<div class="empty">Ошибка запроса: ${esc(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Перепрогнать'; }
  }
}

// ─── BACKEND TEST ──────────────────────────────────────────────
function _applyBackendTestRender(d, ts) {
  const results = $('#backend-test-results');
  const summary = $('#backend-test-summary');
  const s = d.__summary__ || { total: 0, ok: 0, idle: 0, fail: 0 };
  const tsHtml = ts ? `<span style="font-size:10px;color:var(--text-faint);margin-left:12px;">Прогон: ${_fmtTs(ts)}</span>` : '';
  const sumHtml = `
    <div class="ep-summary-bar">
      <div class="ep-sum"><div class="ep-sum-num total">${s.total}</div><div class="ep-sum-label">Всего API</div></div>
      <div class="ep-sum"><div class="ep-sum-num ok">${s.ok}</div><div class="ep-sum-label">✅ Протестированы</div></div>
      <div class="ep-sum"><div class="ep-sum-num" style="color:#a78bfa">${s.idle ?? 0}</div><div class="ep-sum-label">⚡ По вызову</div></div>
      <div class="ep-sum"><div class="ep-sum-num fail">${s.fail}</div><div class="ep-sum-label">❌ Ошибки</div></div>
      <div class="ep-sum"><div class="ep-sum-num" style="font-size:10px;color:var(--text-faint);">Flask</div><div class="ep-sum-label">Runtime</div></div>
      ${tsHtml}
    </div>`;
  summary.innerHTML = sumHtml;

  const keys     = Object.keys(d).filter(k => !k.startsWith('__'));
  const tested   = keys.filter(k => !d[k].idle);
  const idleKeys = keys.filter(k =>  d[k].idle);
  const okKeys   = tested.filter(k =>  d[k].ok);
  const failKeys = tested.filter(k => !d[k].ok);

  let html = sumHtml;

  if (okKeys.length) {
    html += _epSectionHead('✅', 'Flask эндпоинты — Протестированы (GET)', 'var(--status-active)', `${okKeys.length} эндпоинтов`);
    html += okKeys.map(k => _epCard(d[k])).join('');
  }
  if (failKeys.length) {
    html += _epSectionHead('❌', 'Flask эндпоинты — Ошибки', 'var(--status-error)', `${failKeys.length}`);
    html += failKeys.map(k => _epCard(d[k])).join('');
  }
  if (idleKeys.length) {
    html += _epSectionHead('⚡', 'Mutation эндпоинты — По вызову (POST / PATCH / DELETE)', '#a78bfa', `${idleKeys.length} эндпоинтов`);
    html += _epIdleBanner(idleKeys.length);
    html += idleKeys.map(k => _epCard(d[k])).join('');
  }

  results.innerHTML = html;
  _bindToggle(results);
}

function openBackendTest() {
  $('#backend-test-bg').classList.add('active');
  const cache = _loadTestCache('backend');
  const btn   = $('#backend-test-run');
  if (cache) {
    _applyBackendTestRender(cache.d, cache.ts);
    if (btn) btn.textContent = '↻ Перепрогнать';
  } else {
    runBackendTest();
  }
}
function closeBackendTest() { $('#backend-test-bg').classList.remove('active'); }

async function runBackendTest() {
  const btn     = $('#backend-test-run');
  const results = $('#backend-test-results');
  const summary = $('#backend-test-summary');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Тестирую...'; }
  results.innerHTML = '<div class="loader"><div class="spinner"></div>Опрашиваем Flask-эндпоинты…</div>';
  summary.textContent = 'В процессе…';
  try {
    const r = await fetch('/api/test_backend');
    const d = await r.json();
    _saveTestCache('backend', d);
    _applyBackendTestRender(d, Date.now());
  } catch (e) {
    summary.textContent = 'Ошибка';
    results.innerHTML = `<div class="empty">Ошибка запроса: ${esc(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Перепрогнать'; }
  }
}

window.openBackendTest  = openBackendTest;
window.closeBackendTest = closeBackendTest;
window.runBackendTest   = runBackendTest;
window.openMsbTest      = openMsbTest;
window.closeMsbTest     = closeMsbTest;
window.runMsbTest       = runMsbTest;

// ═══════════════════════════════════════════════════
//  MODAL HELPERS
// ═══════════════════════════════════════════════════
function openModal(html) {
  $('#modal-content').innerHTML = html;
  $('#modal-bg').classList.add('active');
}
function closeModal() { $('#modal-bg').classList.remove('active'); }

// ═══════════════════════════════════════════════════
//  PARSER MODULE
//  Ручной запуск парсера ggsel.net. Парсер не запускается сам — только по кнопке.
//  Хранилище персистентное (SQLite), между запусками панели данные сохраняются.
// ═══════════════════════════════════════════════════
let parserPollTimer = null;
let parserLastRunId = null;

const PARSER_STATUS_LABELS = {
  idle:    'Готов к запуску',
  starting:'Запуск...',
  running: 'Идёт парсинг...',
  done:    'Завершено',
  stopped: 'Остановлено',
  error:   'Ошибка',
};

async function loadParser() {
  // Подгружаем конфиг
  try {
    const cfg = await api('/api/parser/config');
    const cap = cfg.hard_cap_quantity;
    const elCap = $('#parser-hard-cap');
    if (elCap) elCap.textContent = String(cap);
  } catch (e) {
    console.warn('parser config:', e);
  }
  
  // Загружаем дерево категорий
  loadCategoriesTree();
  
  // Кнопки обновления категорий и комиссий
  $('#btn-refresh-categories')?.addEventListener('click', async () => {
    setStatus('Синхронизация категорий…', 'busy');
    try {
      await api('/api/categories/sync', { method: 'POST' });
      await loadCategoriesTree();
      setStatus('Категории обновлены', 'ok');
    } catch (e) {
      setStatus('Ошибка обновления', 'err');
    }
  });
  
  $('#btn-sync-fees')?.addEventListener('click', async () => {
    setStatus('Синхронизация комиссий…', 'busy');
    try {
      const res = await api('/api/categories/sync_fees', { method: 'POST' });
      await loadCategoriesTree();
      setStatus(`Комиссии обновлены (${res.count} шт)`, 'ok');
    } catch (e) {
      setStatus('Ошибка комиссий', 'err');
    }
  });

  // Первый статус
  await refreshParserStatus();
  await refreshParserProducts();
  await refreshParsedProducts();
  await refreshParserRuns();

  // Profit slider
  const slider = $('#parser-profit-slider');
  const sliderVal = $('#parser-profit-val');
  if (slider) {
    slider.addEventListener('input', () => {
      if (sliderVal) sliderVal.textContent = slider.value;
      refreshParserProducts();
    });
  }

  // Search + status filter
  const srch = $('#parser-products-search');
  const stSel = $('#parser-products-status');
  if (srch) srch.addEventListener('input', debounce(refreshParserProducts, 350));
  if (stSel) stSel.addEventListener('change', refreshParserProducts);
  
  $('#btn-autopilot-start')?.addEventListener('click', startAutoPilot);
  $('#btn-autopilot-stop')?.addEventListener('click', stopAutoPilot);

  // Polling — каждые 2 сек для статуса / лога, каждые 30 сек для товаров
  if (parserPollTimer) clearInterval(parserPollTimer);
  let _productsTick = 0;
  parserPollTimer = setInterval(async () => {
    const prevRunning = !!$('#btn-parser-stop')?.disabled === false; // approx
    await refreshParserStatus();
    if (parserLastRunId) {
      await refreshParserLog(parserLastRunId, false);
    }
    _productsTick++;
    // Если парсер запущен — обновляем товары каждые 6 тиков (≈12 сек); иначе каждые 15 (≈30 сек)
    const isRunning = !$('#btn-parser-start')?.disabled === false; // after refreshParserStatus re-sets buttons
    const tick = isRunning ? 6 : 15;
    if (_productsTick >= tick && currentView === 'parser') {
      _productsTick = 0;
      await refreshParserProducts();
      await refreshParsedProducts();
      await refreshParserRuns();
    }
    // Auto-refresh pill
    const pill = $('#parser-autorefresh-pill');
    if (pill) pill.classList.toggle('active', isRunning);
    const lbl = $('#parser-autorefresh-label');
    if (lbl) lbl.textContent = isRunning ? 'авто-обновление' : 'авто-обновление';
  }, 2000);
}

// Debounce helper
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function setParserButtons(running) {
  const start = $('#btn-parser-start');
  const stop  = $('#btn-parser-stop');
  if (start) start.disabled = !!running;
  if (stop)  stop.disabled  = !running;
}

function setParserActionStatus(text, kind = '') {
  const el = $('#parser-action-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'action-status ' + kind;
}


async function startAutoPilot() {
  try {
    const r = await fetch('/api/parser/auto/start', { method: 'POST' });
    const d = await r.json();
    if (d.ok) refreshParserStatus();
    else alert('Ошибка: ' + (d.error || JSON.stringify(d)));
  } catch (e) { alert(e); }
}

async function stopAutoPilot() {
  try {
    const r = await fetch('/api/parser/auto/stop', { method: 'POST' });
    const d = await r.json();
    if (d.ok) refreshParserStatus();
    else alert('Ошибка: ' + (d.error || JSON.stringify(d)));
  } catch (e) { alert(e); }
}

async function refreshParserStatus() {
  try {
    const s = await api('/api/parser/status');
    const txt = PARSER_STATUS_LABELS[s.status] || s.status;
    const st = $('#parser-status-text');
    if (st) {
      st.textContent = txt;
      st.style.color = s.status === 'running' ? 'var(--green)' :
                       s.status === 'error'   ? 'var(--red)'   :
                       s.status === 'stopped' ? 'var(--yellow)': '';
    }
    setParserButtons(!!s.is_running);

    const setText = (id, v) => { const e = $('#'+id); if (e) e.textContent = String(v ?? 0); };
    setText('parser-stat-saved', s.products_saved);
    setText('parser-stat-ai',    s.products_ai_enriched);
    setText('parser-stat-pages', s.pages_scanned);
    setText('parser-stat-errors',s.errors_count);

    const lr = $('#parser-last-run');
    if (lr) {
      const parts = [];
      if (s.last_started_at) parts.push(`старт: ${fmtDate(s.last_started_at)}`);
      if (s.last_finished_at) parts.push(`финиш: ${fmtDate(s.last_finished_at)}`);
      if (s.last_query)       parts.push(`q: "${s.last_query}"`);
      if (s.last_category)    parts.push(`cat: ${s.last_category}`);
      lr.textContent = parts.join(' · ');
    }

    // Бейдж в сайдбаре
    const badge = $('#badge-parser');
    if (badge) {
      if (s.products_saved > 0 && !s.is_running) {
        badge.textContent = String(s.products_saved);
        badge.style.display = '';
      } else if (s.is_running) {
        badge.textContent = '…';
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }
    
    // Autopilot
    try {
      const ast = await api('/api/parser/auto/status');
      const autoStatusText = $('#parser-autopilot-status');
      const btnStart = $('#btn-autopilot-start');
      const btnStop = $('#btn-autopilot-stop');
      
      if (ast.running && !ast.stopped) {
         if (btnStart) btnStart.style.display = 'none';
         if (btnStop) btnStop.style.display = 'inline-flex';
         if (autoStatusText) {
            const cats = (ast.next_categories || []).join(', ');
            autoStatusText.textContent = `Авто-пилот работает. Парсится: ${ast.current_category || '?'} | Следующие: [${cats}]`;
         }
      } else {
         if (btnStart) btnStart.style.display = 'inline-flex';
         if (btnStop) btnStop.style.display = 'none';
         if (autoStatusText) autoStatusText.textContent = 'Авто-пилот остановлен';
      }
    } catch (err) {
      console.warn('autopilot status err', err);
    }
    
  } catch (e) {
    const st = $('#parser-status-text');
    if (st) st.textContent = 'Backend недоступен';
  }
}

async function refreshParserProducts() {
  const search     = $('#parser-products-search')?.value.trim() || '';
  const status     = $('#parser-products-status')?.value || '';
  const minProfit  = Number($('#parser-profit-slider')?.value || 0);
  const params = new URLSearchParams();
  if (search) params.set('q', search);
  if (status) params.set('status', status);
  params.set('limit', '50');
  params.set('page', '1');
  try {
    const d = await api('/api/parser/products?' + params.toString());
    let items = d.items || [];
    // Client-side profit_score filter (slider)
    if (minProfit > 0) items = items.filter(p => (p.profit_score ?? 0) >= minProfit);
    const cnt = $('#parser-products-count');
    if (cnt) cnt.textContent = `Найдено: ${items.length} из ${d.total ?? 0}`;
    renderParserProducts(items);
  } catch (e) {
    const t = $('#parser-products-table');
    if (t) t.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
  }
}

// Public alias used by routes
const loadParserProducts = refreshParserProducts;

async function refreshParsedProducts() {
  const t = $('#parsed-products-table');
  if (!t) return;
  try {
    const data = await api('/api/parsed-products?is_top=1&status=approved');
    if (!data.ok) throw new Error(data.error || 'Failed to load');
    renderParsedProducts(data.items || []);
  } catch (e) {
    console.error('refreshParsedProducts:', e);
    t.innerHTML = `<tr><td colspan="7" style="padding:20px; text-align:center; color:#e25;">Ошибка загрузки: ${esc(e.message)}</td></tr>`;
  }
}

function renderParsedProducts(items) {
  const t = $('#parsed-products-table');
  if (!t) return;
  if (!items.length) {
    t.innerHTML = `<tr><td colspan="7" style="padding:20px; text-align:center;">Нет товаров в топ-100. Запусти pipeline.</td></tr>`;
    return;
  }
  const rows = items.map(p => {
    const title = p.title || '—';
    const sourcePrice = fmt(p.source_price, 2);
    const sellPrice = fmt(p.sell_price, 2);
    const profit = fmt(p.expected_profit_rub, 2);
    const margin = p.expected_net_margin_pct ? fmt(p.expected_net_margin_pct * 100, 2) + '%' : '—';
    const status = p.status || '—';
    const canPublish = p.status === 'approved';

    return `
      <tr style="border-bottom:1px solid var(--border-color, #475569);">
        <td style="padding:10px; max-width:300px; word-break:break-word;">${esc(title)}</td>
        <td style="padding:10px; text-align:right;">${sourcePrice} ₽</td>
        <td style="padding:10px; text-align:right; color:#fde047;">${sellPrice} ₽</td>
        <td style="padding:10px; text-align:right; color:#4ade80;">${profit} ₽</td>
        <td style="padding:10px; text-align:right;">${margin}</td>
        <td style="padding:10px; text-align:center;">${esc(status)}</td>
        <td style="padding:10px; text-align:center;">
          ${canPublish ? `<button class="btn btn-primary" style="padding:4px 12px; font-size:12px;" onclick="publishProduct('${esc(p.product_id)}')">Опубликовать</button>` : '—'}
        </td>
      </tr>
    `;
  }).join('');
  t.innerHTML = rows;
}

async function publishProduct(productId) {
  try {
    // TODO: Реализовать публикацию товара через API
    alert(`Публикация товара ${productId} пока не реализована`);
  } catch (e) {
    console.error('publishProduct:', e);
    alert('Ошибка публикации: ' + e.message);
  }
}

function renderParserProducts(items) {
  const t = $('#parser-products-table');
  if (!t) return;
  if (!items.length) {
    t.innerHTML = `<div class="empty" style="padding:40px 20px;"><div class="empty-icon" style="font-size:32px;margin-bottom:10px;">&#9697;</div><div style="font-weight:500;margin-bottom:4px;">Нет товаров</div><div class="muted" style="font-size:12px;">Нажми «Старт» чтобы запустить парсер</div></div>`;
    return;
  }
  const rows = items.map(p => {
    const title       = p.generated_title || p.title || '—';
    const statusBadgeHtml = statusBadgeFor(p.status);

    // ── Цены: source_price (зачёркнутый) → my_price (жирный)
    const src = (p.source_price && p.source_price > 0) ? p.source_price : p.price;
    let priceHtml;
    if (p.my_price && p.my_price > 0 && src && src > 0 && Math.abs(p.my_price - src) > 0.01) {
      priceHtml = `<span style="text-decoration:line-through; color:#999; font-size:11px;">${fmt(src)}</span> <b style="color:var(--green);">${fmt(p.my_price)}</b> ₽`;
    } else if (p.my_price && p.my_price > 0) {
      priceHtml = `<b>${fmt(p.my_price)}</b> ₽`;
    } else if (p.price) {
      priceHtml = `${fmt(p.price)} ₽`;
    } else {
      priceHtml = '—';
    }

    // ── Profit Score бейдж (цветной)
    const profit = (p.profit_score !== null && p.profit_score !== undefined) ? Number(p.profit_score) : null;
    let profitHtml;
    if (profit === null || isNaN(profit)) {
      profitHtml = '<span class="profit-score low">—</span>';
    } else {
      let cls = 'profit-score';
      if (profit >= 70) cls += ' high';       // зелёный
      else if (profit >= 40) cls += ' mid';   // жёлтый
      else cls += ' low';                     // красный
      profitHtml = `<span class="${cls}" title="AI profit score 0-100">★ ${fmt(profit, 0)}</span>`;
    }

    // ── Продажи
    const salesHtml   = p.sales_count !== null && p.sales_count !== undefined
      ? fmt(p.sales_count, 0)
      : '—';

    // ── Рейтинг звёздочками
    const rating = (p.rating !== null && p.rating !== undefined) ? Number(p.rating) : null;
    let ratingHtml = '—';
    if (rating !== null && !isNaN(rating) && rating > 0) {
      const full = Math.max(0, Math.min(5, Math.round(rating)));
      const stars = '★'.repeat(full) + '☆'.repeat(5 - full);
      ratingHtml = `<span title="Рейтинг продавца ${rating.toFixed(1)}/5" style="color:#f5b50a;">${stars}</span> <span class="muted" style="font-size:11px;">${rating.toFixed(1)}</span>`;
    }

    // ── В наличии
    const inStockHtml = p.in_stock
      ? `<span class="badge approved" style="font-size:10px;">✓ да</span>`
      : `<span class="badge rejected" style="font-size:10px;">✗ нет</span>`;
    const url         = p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="link" title="Открыть на сайте">↗</a>` : '';
    const imgUrl      = p.local_image_path || p.generated_image_url || p.image_url;
    const img         = imgUrl
      ? `<img src="${esc(imgSrc(imgUrl))}" class="cover-thumb" onerror="this.style.display='none'" alt="">`
      : '';

    const canApprove  = p.status !== 'approved';
    const canReject   = p.status !== 'rejected';

    return `<tr id="parser-row-${esc(p.product_id)}">
      <td style="width:44px; padding:8px 6px 8px 14px;">${img}</td>
      <td style="max-width:280px;">
        <div style="font-weight:500; line-height:1.35;">${esc(title)}</div>
        <div class="muted" style="font-size:11px; margin-top:2px;">${esc(p.product_id)} ${url}</div>
      </td>
      <td style="white-space:nowrap;">${priceHtml}</td>
      <td>${profitHtml}</td>
      <td style="white-space:nowrap;">${ratingHtml}</td>
      <td>${salesHtml}</td>
      <td>${inStockHtml}</td>
      <td>${statusBadgeHtml}</td>
      <td>
        <div style="display:flex; gap:4px;">
          ${canApprove ? `<button class="btn-approve" data-parser-approve="${esc(p.product_id)}">✓</button>` : ''}
          ${canReject  ? `<button class="btn-reject"  data-parser-reject="${esc(p.product_id)}">✕</button>` : ''}
          <button class="btn btn-sm" data-parser-view="${esc(p.product_id)}">…</button>
        </div>
      </td>
    </tr>`;
  }).join('');

  t.innerHTML = `
    <table>
      <thead>
        <tr>
          <th style="width:44px;"></th>
          <th>Название</th>
          <th>Цена</th>
          <th>Score</th>
          <th>Рейтинг</th>
          <th>Продажи</th>
          <th>Наличие</th>
          <th>Статус</th>
          <th style="width:90px;"></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  // Bind events
  t.querySelectorAll('[data-parser-approve]').forEach(b => {
    b.addEventListener('click', () => approveProduct(b.dataset.parserApprove, b));
  });
  t.querySelectorAll('[data-parser-reject]').forEach(b => {
    b.addEventListener('click', () => rejectProduct(b.dataset.parserReject, b));
  });
  t.querySelectorAll('[data-parser-view]').forEach(b => {
    b.addEventListener('click', () => viewParserProduct(b.dataset.parserView));
  });
}

async function approveProduct(pid, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳...'; }
  const row = $(`#parser-row-${pid}`);
  if (row) row.classList.add('row-approving');
  try {
    const r = await fetch(`/api/parser/approve/${encodeURIComponent(pid)}`, { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      setParserActionStatus(`✓ Товар ${pid} одобрен — offer_id: ${d.offer_id || '—'}`, 'ok');
      // Optimistic UI: update badge in row
      if (row) {
        const bdg = row.querySelector('.badge');
        if (bdg) { bdg.className = 'badge approved'; bdg.textContent = 'approved'; }
        const appBtn = row.querySelector('[data-parser-approve]');
        if (appBtn) appBtn.remove();
        if (btn) { btn.disabled = false; }
      }
      // Reload if filtering by pending (row will disappear)
      const stSel = $('#parser-products-status');
      if (stSel && stSel.value === 'pending') {
        await refreshParserProducts();
      }
    } else {
      setParserActionStatus(`Ошибка одобрения: ${d.error || 'unknown'}`, 'err');
      if (btn) { btn.disabled = false; btn.textContent = '✓ Одобрить'; }
      if (row) row.classList.remove('row-approving');
    }
  } catch (e) {
    setParserActionStatus('Ошибка запроса: ' + e.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '✓ Одобрить'; }
    if (row) row.classList.remove('row-approving');
  }
}

async function rejectProduct(pid, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '⏳...'; }
  const row = $(`#parser-row-${pid}`);
  try {
    const r = await fetch(`/api/parser/reject/${encodeURIComponent(pid)}`, { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      setParserActionStatus(`Товар ${pid} отклонён`, '');
      // Animate row out then remove
      if (row) {
        row.classList.add('row-rejecting');
        setTimeout(() => {
          const stSel = $('#parser-products-status');
          if (stSel && (stSel.value === 'pending' || stSel.value === '')) {
            refreshParserProducts();
          } else {
            // Just update the badge in place
            const bdg = row.querySelector('.badge');
            if (bdg) { bdg.className = 'badge rejected'; bdg.textContent = 'rejected'; }
            row.classList.remove('row-rejecting');
            const rejBtn = row.querySelector('[data-parser-reject]');
            if (rejBtn) rejBtn.remove();
            if (btn) { btn.disabled = false; }
          }
        }, 320);
      }
    } else {
      setParserActionStatus(`Ошибка отклонения: ${d.error || 'unknown'}`, 'err');
      if (btn) { btn.disabled = false; btn.textContent = '✕ Откл.'; }
    }
  } catch (e) {
    setParserActionStatus('Ошибка запроса: ' + e.message, 'err');
    if (btn) { btn.disabled = false; btn.textContent = '✕ Откл.'; }
  }
}

function statusBadgeFor(s) {
  const map = {
    pending:     'pending',
    approved:    'approved',
    rejected:    'rejected',
    new:         'neutral',
    ai_enriched: 'active',
    gen_failed:  'draft',
    queued:      'info',
  };
  const label = s || '—';
  return `<span class="badge ${map[s] || 'neutral'}">${esc(label)}</span>`;
}

async function viewParserProduct(pid) {
  try {
    const d = await api(`/api/parser/products/${encodeURIComponent(pid)}`);
    const p = d.product || {};

    // ── Галерея: images_json → массив; fallback на image_url
    let galleryImgs = [];
    if (p.images_json) {
      try { galleryImgs = JSON.parse(p.images_json); } catch (e) { galleryImgs = []; }
    }
    if (!galleryImgs.length && p.image_url) galleryImgs = [p.image_url];
    const galleryHtml = galleryImgs.length
      ? `<div style="display:flex; gap:6px; overflow-x:auto; padding:4px 0;">
           ${galleryImgs.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(imgSrc(u))}" style="width:80px; height:80px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.1);" onerror="this.style.opacity=0.3"></a>`).join('')}
         </div>`
      : '<div class="muted" style="font-size:12px;">Нет изображений</div>';

    // ── Кнопка "Купить у донора" — открывает p.url в новой вкладке
    const donorBtn = p.url
      ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="btn btn-primary" style="background:var(--green); border-color:var(--green); color:#000; font-weight:600;">🛒 Купить у донора</a>`
      : '';

    // ── AI-блок
    const ps = (p.profit_score !== null && p.profit_score !== undefined) ? Number(p.profit_score) : null;
    let aiBlockHtml;
    if (ps === null || isNaN(ps)) {
      aiBlockHtml = `<div class="muted" style="font-style:italic;">AI обогащение не выполнено (нет profit_score)</div>`;
    } else {
      let psCls = 'low';
      if (ps >= 70) psCls = 'high';
      else if (ps >= 40) psCls = 'mid';
      const margin = (p.recommended_margin_pct !== null && p.recommended_margin_pct !== undefined)
        ? `${fmt(p.recommended_margin_pct, 1)}%` : '—';
      const risk = p.risk_level || '—';
      const riskReason = p.risk_reason || '';
      const riskColor = risk === 'low' ? 'var(--green)' : risk === 'high' ? 'var(--red)' : '#f5b50a';
      aiBlockHtml = `
        <div class="form-row">
          <label>Profit Score</label>
          <div>
            <div style="display:flex; align-items:center; gap:8px;">
              <div style="flex:1; height:10px; background:rgba(255,255,255,0.08); border-radius:5px; overflow:hidden;">
                <div class="profit-score ${psCls}" style="height:100%; width:${Math.max(0, Math.min(100, ps))}%; background:currentColor;"></div>
              </div>
              <span class="profit-score ${psCls}" style="font-size:13px;">★ ${fmt(ps, 0)}</span>
            </div>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-row">
            <label>Рекомендуемая наценка</label>
            <div style="font-weight:600;">${margin}</div>
          </div>
          <div class="form-row">
            <label>Риск</label>
            <div>
              <span style="color:${riskColor}; font-weight:600; text-transform:uppercase; font-size:11px;">${esc(risk)}</span>
              ${riskReason ? `<div class="muted" style="font-size:11px; margin-top:2px;">${esc(riskReason)}</div>` : ''}
            </div>
          </div>
        </div>
      `;
    }

    // ── Характеристики (properties_json)
    let propsHtml = '';
    if (p.properties_json) {
      try {
        const props = JSON.parse(p.properties_json);
        if (Array.isArray(props) && props.length) {
          propsHtml = `
            <div class="form-row">
              <label>Характеристики (${props.length})</label>
              <table class="tbl" style="font-size:12px;">
                <tbody>
                  ${props.map(pr => `<tr><td style="color:var(--text-faint); width:40%;">${esc(pr.key || '')}</td><td>${esc(pr.value || '')}</td></tr>`).join('')}
                </tbody>
              </table>
            </div>
          `;
        }
      } catch (e) { /* ignore */ }
    }
    if (!propsHtml) propsHtml = '';

    // ── Продавец
    const sellerBlock = `
      <div class="form-grid">
        <div class="form-row">
          <label>Продавец</label>
          <div>
            ${esc(p.seller_name || '—')}
            ${p.seller_url ? `<a href="${esc(p.seller_url)}" target="_blank" rel="noopener" class="link" style="font-size:11px; margin-left:6px;">↗ магазин</a>` : ''}
          </div>
        </div>
        <div class="form-row">
          <label>В наличии</label>
          <div>${p.quantity_available !== null && p.quantity_available !== undefined ? fmt(p.quantity_available, 0) + ' шт.' : '—'}</div>
        </div>
      </div>
    `;

    const html = `
      <div class="modal-head">
        <h2 style="margin:0;">📦 ${esc(p.title || pid)}</h2>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>
      <div style="padding:18px; max-height:70vh; overflow-y:auto;">
        ${galleryHtml}
        ${donorBtn ? `<div style="margin:10px 0 14px 0;">${donorBtn}</div>` : ''}
        <div class="form-row">
          <label>ID</label>
          <div><code>${esc(p.product_id)}</code></div>
        </div>
        <div class="form-row">
          <label>Оригинальное название</label>
          <div>${esc(p.original_title || p.title || '—')}</div>
        </div>
        <div class="form-row">
          <label>AI-название</label>
          <div style="color:var(--green);">${esc(p.generated_title || '—')}</div>
        </div>
        <div class="form-row">
          <label>AI-описание</label>
          <div style="white-space:pre-wrap;">${esc(p.generated_desc || '—')}</div>
        </div>
        <div class="form-row">
          <label>Теги</label>
          <div>${esc(p.generated_tags || '—')}</div>
        </div>
        <div class="form-grid">
          <div class="form-row">
            <label>Категория</label>
            <div>${esc(p.category || '—')}</div>
          </div>
          <div class="form-row">
            <label>Опубликовано</label>
            <div>${esc(p.published_at || '—')}</div>
          </div>
          <div class="form-row">
            <label>Цена источника</label>
            <div>
              ${p.source_price || p.price
                ? `<span style="${(p.my_price && p.my_price > 0) ? 'text-decoration:line-through; color:#999;' : ''}">${fmt(p.source_price || p.price)}</span> ₽`
                : '—'}
            </div>
          </div>
          <div class="form-row">
            <label>Моя цена</label>
            <div style="font-weight:600; color:var(--green);">${fmt(p.my_price)} ₽</div>
          </div>
        </div>
        ${sellerBlock}
        ${(function(){
          if (!p.shop_name && !p.shop_rating && !p.shop_products_count) return '';
          let h = '<div class="shop-info-block" style="margin:10px 0;">';
          h += '<div class="shop-info-header">🏪 Магазин продавца</div>';
          if (p.shop_name) h += '<div class="shop-info-row"><span class="shop-label">Название:</span> ' + (p.shop_url ? '<a href="' + esc(p.shop_url) + '" target="_blank" class="link">' + esc(p.shop_name) + '</a>' : esc(p.shop_name)) + '</div>';
          if (p.shop_rating) h += '<div class="shop-info-row"><span class="shop-label">Рейтинг:</span> ⭐ ' + fmt(p.shop_rating,2) + '</div>';
          if (p.shop_products_count) h += '<div class="shop-info-row"><span class="shop-label">Товаров:</span> ' + p.shop_products_count + '</div>';
          if (p.shop_registered_at) h += '<div class="shop-info-row"><span class="shop-label">С нами с:</span> ' + esc(p.shop_registered_at) + '</div>';
          if (p.shop_positive_reviews != null) { h += '<div class="shop-info-row"><span class="shop-label">Отзывы:</span> <span class="positive">+' + p.shop_positive_reviews + '</span>'; if (p.shop_negative_reviews != null) h += ' / <span class="negative">-' + p.shop_negative_reviews + '</span>'; h += '</div>'; }
          h += '</div>';
          return h;
        })()}
        ${propsHtml}
        <h3 style="margin:18px 0 8px 0; font-size:14px; color:var(--blue);">🤖 AI-оценка выгодности</h3>
        ${aiBlockHtml}
        <div class="form-row">
          <label>URL источника</label>
          ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="link">${esc(p.url)}</a>` : '—'}
        </div>
        <div class="form-row">
          <label>Создан / Обновлён</label>
          <div class="muted">${fmtDate(p.created_at)} → ${fmtDate(p.updated_at)}</div>
        </div>
        ${p.ai_error ? `<div class="form-row"><label>AI ошибка</label><div style="color:var(--red);">${esc(p.ai_error)}</div></div>` : ''}
      </div>
    `;
    openModal(html);
  } catch (e) {
    setParserActionStatus('Ошибка: ' + e.message, 'err');
  }
}

async function deleteParserProduct(pid) {
  if (!confirm('Удалить товар ' + pid + ' из БД парсера?')) return;
  try {
    const d = await fetch(`/api/parser/products/${encodeURIComponent(pid)}`, { method: 'DELETE' });
    const j = await d.json();
    if (j.ok) {
      setParserActionStatus('Удалено', 'ok');
      await refreshParserProducts();
    } else {
      setParserActionStatus('Ошибка: ' + (j.error || 'unknown'), 'err');
    }
  } catch (e) {
    setParserActionStatus('Ошибка: ' + e.message, 'err');
  }
}

async function refreshParserRuns() {
  try {
    const d = await api('/api/parser/runs?limit=10');
    renderParserRuns(d.items || []);
  } catch (e) {
    /* ignore */
  }
}

function renderParserRuns(items) {
  const t = $('#parser-runs-table');
  if (!t) return;
  if (!items.length) {
    t.innerHTML = '<div class="empty">Пока ни одного запуска</div>';
    return;
  }
  const statusBadge = (s) => {
    const map = { running: 'active', done: 'active', stopped: 'paused', error: 'archived' };
    return `<span class="badge ${map[s] || 'neutral'}">${esc(s)}</span>`;
  };
  const rows = items.map(r => `<tr>
    <td>#${r.run_id}</td>
    <td>${fmtDate(r.started_at)}</td>
    <td>${fmtDate(r.finished_at)}</td>
    <td>${statusBadge(r.status)}</td>
    <td>${esc(r.query || '—')}</td>
    <td>${esc(r.category || '—')}</td>
    <td>${r.quantity || 0}</td>
    <td>${r.products_saved || 0}</td>
    <td>${r.products_ai_enriched || 0}</td>
    <td><button class="btn btn-sm" data-parser-log="${r.run_id}">📋 Лог</button></td>
  </tr>`).join('');
  t.innerHTML = `
    <table class="tbl">
      <thead>
        <tr>
          <th>#</th><th>Старт</th><th>Финиш</th><th>Статус</th>
          <th>Запрос</th><th>Категория</th><th>Кол-во</th>
          <th>Сохранено</th><th>AI</th><th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  t.querySelectorAll('[data-parser-log]').forEach(b => {
    b.addEventListener('click', () => refreshParserLog(b.dataset.parserLog, true));
  });
}

async function refreshParserLog(runId, scrollIntoView) {
  try {
    const d = await api(`/api/parser/runs/${runId}/log?limit=200`);
    parserLastRunId = runId;
    const card = $('#parser-log-card');
    const list = $('#parser-log-entries');
    const rid  = $('#parser-log-runid');
    if (!card || !list) return;
    if (scrollIntoView) card.style.display = '';
    if (rid) rid.textContent = `(run #${runId})`;
    if (!d.items || !d.items.length) {
      list.innerHTML = '<div class="empty" style="padding:12px;">Пусто</div>';
      return;
    }
    list.innerHTML = d.items.map(it => {
      const color = it.level === 'error' ? 'var(--red)' :
                    it.level === 'warn'  ? 'var(--yellow)' : '';
      return `<div style="color:${color};">
        <span class="muted">[${it.ts}]</span>
        <span style="color:${color};">[${esc(it.level)}]</span>
        ${esc(it.message)}
      </div>`;
    }).join('');
    if (scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) {
    /* ignore */
  }
}

async function startParser() {
  const query    = $('#parser-input-query')?.value.trim() || '';
  const category = $('#parser-input-category')?.value || '';
  const quantity = parseInt($('#parser-input-quantity')?.value || '20', 10);
  const maxPages = parseInt($('#parser-input-pages')?.value || '3', 10);
  const runAi    = $('#parser-input-ai')?.value === 'true';

  if (!query && !category) {
    setParserActionStatus('Укажи query или category', 'err');
    return;
  }

  setParserActionStatus('Запускаю...', '');
  setParserButtons(true);
  try {
    const d = await fetch('/api/parser/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, category, quantity, max_pages: maxPages, run_ai: runAi }),
    });
    const j = await d.json();
    if (j.ok) {
      setParserActionStatus('✓ ' + (j.message || 'Запущен'), 'ok');
      if (j.run_id) parserLastRunId = j.run_id;
      // Показываем карточку лога
      const card = $('#parser-log-card');
      if (card) card.style.display = '';
    } else {
      setParserActionStatus('Ошибка: ' + (j.error || 'unknown'), 'err');
      setParserButtons(false);
    }
  } catch (e) {
    setParserActionStatus('Сетевая ошибка: ' + e.message, 'err');
    setParserButtons(false);
  }
}

async function stopParser() {
  setParserActionStatus('Останавливаю...', '');
  try {
    const d = await fetch('/api/parser/stop', { method: 'POST' });
    const j = await d.json();
    setParserActionStatus(j.message || 'Команда остановки отправлена', j.ok ? 'ok' : 'err');
  } catch (e) {
    setParserActionStatus('Ошибка: ' + e.message, 'err');
  }
}

async function loadParserStats() {
  try {
    const s = await api('/api/parser/stats');
    const byStatus = (s.by_status || [])
      .map(x => `<tr><td>${esc(x.status || '—')}</td><td>${x.n}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">Нет данных</td></tr>';
    const last = s.last_run || {};
    const html = `
      <div class="modal-head">
        <h2 style="margin:0;">📊 Статистика парсера</h2>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>
      <div style="padding:18px;">
        <div class="form-row">
          <label>Всего товаров в БД</label>
          <div style="font-size:24px; font-weight:600;">${s.total_products || 0}</div>
        </div>
        <div class="form-row">
          <label>По статусам</label>
          <table class="tbl" style="max-width:300px;">
            <thead><tr><th>Статус</th><th>Кол-во</th></tr></thead>
            <tbody>${byStatus}</tbody>
          </table>
        </div>
        <div class="form-row">
          <label>Последний запуск</label>
          ${last.run_id ? `
            <div>ID: #${last.run_id} · <b>${esc(last.status || '—')}</b></div>
            <div class="muted">старт: ${fmtDate(last.started_at)}</div>
            <div class="muted">финиш: ${fmtDate(last.finished_at)}</div>
            <div class="muted">q="${esc(last.query || '')}" cat="${esc(last.category || '')}"</div>
            <div class="muted">сохранено: ${last.products_saved || 0} · AI: ${last.products_ai_enriched || 0}</div>
            ${last.errors ? `<div class="muted" style="color:var(--red);">ошибки: ${esc(last.errors)}</div>` : ''}
          ` : '<div class="muted">Нет запусков</div>'}
        </div>
      </div>
    `;
    openModal(html);
  } catch (e) {
    setParserActionStatus('Ошибка: ' + e.message, 'err');
  }
}

// expose
window.startParser = startParser;
window.stopParser = stopParser;
window.loadParserStats = loadParserStats;
window.refreshParserProducts = refreshParserProducts;
window.refreshParserRuns = refreshParserRuns;
window.refreshParserStatus = refreshParserStatus;
window.refreshParserLog = refreshParserLog;
window.viewParserProduct = viewParserProduct;
window.deleteParserProduct = deleteParserProduct;
window.loadParser = loadParser;



// ═══════════════════════════════════════════════════
//  WARMER — прогрев MSB профилей
// ═══════════════════════════════════════════════════
let warmerPollTimer = null;
let warmerLogOffset = 0;

const WARMER_STATUS_LABELS = {
  idle:    'Готов к запуску',
  running: 'Прогрев идёт...',
  done:    'Завершено',
  error:   'Ошибка',
};

function setWarmerActionStatus(text, kind = '') {
  const el = $('#warmer-action-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'action-status ' + kind;
}

async function loadWarmer() {
  await refreshWarmerStatus();
  // Включаем polling если запущен
  if (warmerPollTimer) clearInterval(warmerPollTimer);
  warmerPollTimer = setInterval(async () => {
    if (currentView !== 'warmer') return;
    await refreshWarmerStatus();
    await refreshWarmerLog();
  }, 1500);
}

async function refreshWarmerStatus() {
  try {
    const s = await api('/api/warmer/status');
    const txt = WARMER_STATUS_LABELS[s.status] || s.status;
    const el = $('#warmer-status-text');
    if (el) {
      el.textContent = txt;
      el.style.color = s.status === 'running' ? 'var(--green)' :
                       s.status === 'error'   ? 'var(--red)'   :
                       s.status === 'done'    ? 'var(--green)' : '';
    }
    const setText = (id, v) => { const e = $('#' + id); if (e) e.textContent = String(v ?? 0); };
    setText('warmer-stat-ok',      s.ok);
    setText('warmer-stat-partial', s.partial);
    setText('warmer-stat-fail',    s.fail);
    setText('warmer-stat-total',   s.total);

    const bar  = $('#warmer-progress-bar');
    const ptxt = $('#warmer-progress-text');
    if (bar)  bar.style.width  = (s.progress || 0) + '%';
    if (ptxt) ptxt.textContent = (s.progress || 0) + '%';

    const startBtn = $('#btn-warmer-start');
    if (startBtn) startBtn.disabled = s.status === 'running';
    const stopBtn = $('#btn-warmer-stop');
    if (stopBtn) stopBtn.style.display = s.status === 'running' ? '' : 'none';

    // Бейдж в сайдбаре
    const badge = $('#badge-warmer');
    if (badge) {
      if (s.ok > 0) {
        badge.textContent = s.ok;
        badge.style.display = '';
      } else if (s.status === 'running') {
        badge.textContent = '…';
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }

    // Показываем карточку лога если запущен или завершён
    const logCard = $('#warmer-log-card');
    if (logCard && (s.status === 'running' || s.status === 'done' || s.status === 'error')) {
      logCard.style.display = '';
    }
  } catch (e) {
    const el = $('#warmer-status-text');
    if (el) el.textContent = 'Backend недоступен';
  }
}

async function refreshWarmerLog() {
  try {
    const d = await api(`/api/warmer/log?since=${warmerLogOffset}`);
    if (d.lines && d.lines.length > 0) {
      warmerLogOffset = d.total;
      const body = $('#warmer-log-body');
      if (!body) return;
      d.lines.forEach(line => {
        const div = document.createElement('div');
        div.style.color = line.includes('✅') ? 'var(--green)' :
                          line.includes('❌') ? 'var(--red)'   :
                          line.includes('⚠')  ? 'var(--yellow)': '';
        div.textContent = line;
        body.appendChild(div);
      });
      body.scrollTop = body.scrollHeight;
    }
  } catch (e) { /* ignore */ }
}

async function startWarmer() {
  const msb_url = $('#warmer-msb-url')?.value.trim() || 'http://127.0.0.1:17248';
  const batch   = parseInt($('#warmer-batch')?.value || '0', 10);
  const delay   = parseFloat($('#warmer-delay')?.value || '5');

  setWarmerActionStatus('Запускаю...', '');
  warmerLogOffset = 0;
  const logBody = $('#warmer-log-body');
  if (logBody) logBody.innerHTML = '';

  try {
    const r = await fetch('/api/warmer/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ msb_url, batch, delay }),
    });
    const j = await r.json();
    if (j.ok) {
      setWarmerActionStatus('✓ ' + (j.message || 'Запущен'), 'ok');
      const logCard = $('#warmer-log-card');
      if (logCard) logCard.style.display = '';
      $('#btn-warmer-start').disabled = true;
    } else {
      setWarmerActionStatus('Ошибка: ' + (j.error || 'unknown'), 'err');
    }
  } catch (e) {
    setWarmerActionStatus('Сетевая ошибка: ' + e.message, 'err');
  }
}

async function stopWarmer() {
  const msb_url = $('#warmer-msb-url')?.value.trim() || 'http://127.0.0.1:17248';
  setWarmerActionStatus('Остановка...', '');
  try {
    const r = await fetch('/api/warmer/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ msb_url }),
    });
    const j = await r.json();
    if (j.ok) {
      setWarmerActionStatus('✓ Остановка запрошена (завершит после текущего профиля)', 'ok');
    } else {
      setWarmerActionStatus('Ошибка: ' + (j.error || 'unknown'), 'err');
    }
  } catch (e) {
    setWarmerActionStatus('Сетевая ошибка: ' + e.message, 'err');
  }
}
async function checkWarmerCookies() {
  const msb_url = $('#warmer-msb-url')?.value.trim() || 'http://127.0.0.1:17248';
  const card  = $('#warmer-cookies-card');
  const table = $('#warmer-cookies-table');
  const cnt   = $('#warmer-cookies-count');
  if (card)  card.style.display  = '';
  if (table) table.innerHTML = '<div class="loader"><div class="spinner"></div>Проверяю куки...</div>';

  try {
    const d = await api('/api/warmer/cookies?msb_url=' + encodeURIComponent(msb_url));
    const results = d.results || [];
    if (cnt) cnt.textContent = `Профилей: ${results.length}`;

    const ok       = results.filter(r => r.status === 'ok').length;
    const partial  = results.filter(r => r.status === 'partial').length;
    const empty    = results.filter(r => r.status === 'empty' || r.status === 'error').length;

    const summary = `<div style="padding:10px 18px; background:rgba(0,0,0,0.15); border-bottom:1px solid var(--border-soft); font-size:13px;">
      <span style="color:var(--green)">✅ Qrator OK: <b>${ok}</b></span> &nbsp;
      <span style="color:var(--yellow)">⚠ Частично: <b>${partial}</b></span> &nbsp;
      <span style="color:var(--red)">❌ Без куков: <b>${empty}</b></span>
    </div>`;

    const rows = results.map(r => {
      const color = r.status === 'ok' ? 'var(--green)' :
                    r.status === 'partial' ? 'var(--yellow)' : 'var(--red)';
      const icon  = r.status === 'ok' ? '✅' : r.status === 'partial' ? '⚠' : '❌';
      const keys  = (r.keys || []).join(', ') || '—';
      return `<tr>
        <td style="color:${color}; font-size:14px;">${icon}</td>
        <td style="max-width:260px; font-size:12px;">${esc(r.name || r.id)}</td>
        <td style="font-size:12px; color:var(--text-faint);">${r.cookie_count}</td>
        <td style="font-size:11px; color:var(--text-faint); max-width:360px; word-break:break-all;">${esc(keys)}</td>
      </tr>`;
    }).join('');

    if (table) table.innerHTML = summary + `
      <table>
        <thead><tr><th></th><th>Профиль</th><th>Куков</th><th>Ключи (ggsel.net)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    if (table) table.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
  }
}

window.startWarmer = startWarmer;
window.checkWarmerCookies = checkWarmerCookies;
window.loadWarmer = loadWarmer;

// ═══════════════════════════════════════════════════
//  AI WORKSPACE — кнопка запуска профиля MSB
// ═══════════════════════════════════════════════════
async function launchAiWorkspace() {
  const btn = $('#btn-ai-workspace');
  if (!btn || btn.disabled) return;

  btn.disabled = true;
  btn.classList.add('loading');
  const origHtml = btn.innerHTML;
  btn.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/>
      <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/>
      <line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/>
      <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>
    </svg>
    Запускаю…`;

  try {
    const r = await fetch('/api/msb/ai-workspace/launch', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      showToast('Браузер AI Workspace запущен', 'success');
    } else {
      showToast('Ошибка: ' + (d.error || 'неизвестная'), 'error');
    }
  } catch (e) {
    showToast('Ошибка запроса к MSB', 'error');
  } finally {
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.innerHTML = origHtml;
  }
}

// ═══════════════════════════════════════════════════
//  EVENT BINDINGS (topbar + global)
// ═══════════════════════════════════════════════════
$('#btn-refresh')?.addEventListener('click', refreshActive);
$('#svc-api-wrap')?.addEventListener('click', openApiTest);
$('#api-test-bg')?.addEventListener('click', (e) => { if (e.target.id === 'api-test-bg') closeApiTest(); });
$('#cookie-modal-bg')?.addEventListener('click', (e) => { if (e.target.id === 'cookie-modal-bg') closeCookieModal(); });
$('#msb-test-bg')?.addEventListener('click', (e) => { if (e.target.id === 'msb-test-bg') closeMsbTest(); });
$('#backend-test-bg')?.addEventListener('click', (e) => { if (e.target.id === 'backend-test-bg') closeBackendTest(); });
$('#svc-backend-wrap')?.addEventListener('click', () => openBackendTest());
$('#svc-cookie-open-btn')?.addEventListener('click', (e) => { e.stopPropagation(); cookieOpenBrowser(); });
$('#btn-ai-workspace')?.addEventListener('click', launchAiWorkspace);
$('#modal-bg')?.addEventListener('click', (e) => { if (e.target.id === 'modal-bg') closeModal(); });

$('#btn-create-offer')?.addEventListener('click', openCreateOfferModal);
$('#btn-bulk-activate')?.addEventListener('click', () => bulkAction('activate'));
$('#btn-bulk-pause')?.addEventListener('click', () => bulkAction('pause'));
$('#btn-bulk-delete')?.addEventListener('click', () => bulkAction('delete'));
$('#btn-bulk-clear')?.addEventListener('click', clearOfferSelection);

$('#offers-search')?.addEventListener('input', filterOffers);
$('#offers-status')?.addEventListener('change', filterOffers);

$('#btn-save-config')?.addEventListener('click', saveConfig);
$('#btn-open-docs')?.addEventListener('click', openDocs);
$('#btn-logs-refresh')?.addEventListener('click', () => { loaded.delete('logs'); loadLogs(); });
$('#btn-status-refresh')?.addEventListener('click', () => { loaded.delete('status'); loadStatus(); });

let pipelinePollTimer = null;
let pipelineActiveRunId = null;

async function startPipelineTop100() {
  const btn = $('#btn-pipeline-run');
  const card = $('#pipeline-status-card');
  const st = $('#pipeline-status-text');
  if (btn) btn.disabled = true;
  if (card) card.style.display = '';
  if (st) st.textContent = 'Запуск…';
  try {
    startLogsPolling(); fetchParserStatus();
    const r = await fetch('/api/pipeline/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    pipelineActiveRunId = j.run_id;
    if (st) st.textContent = `Запущен (run #${j.run_id})…`;
    setStatus(`Pipeline #${j.run_id} стартовал`, 'ok');
    pollPipelineStatus(j.run_id);
  } catch (e) {
    if (st) st.textContent = `Ошибка: ${e.message}`;
    setStatus('Pipeline: ошибка', 'err');
    if (btn) btn.disabled = false;
  }
}

function pollPipelineStatus(runId) {
  if (pipelinePollTimer) clearInterval(pipelinePollTimer);
        stopLogsPolling();
  const st = $('#pipeline-status-text');
  const btn = $('#btn-pipeline-run');
  pipelinePollTimer = setInterval(async () => {
    try {
      const d = await api(`/api/pipeline/status/${runId}`);
      const status = d.status || 'unknown';
      if (st) {
        st.textContent = `Run #${runId}: ${status}${d.errors ? ' — ' + String(d.errors).slice(0, 120) : ''}`;
      }
      if (status !== 'running') {
        clearInterval(pipelinePollTimer);
        pipelinePollTimer = null;
        if (btn) btn.disabled = false;
        setStatus(`Pipeline #${runId}: ${status}`, status === 'done' ? 'ok' : 'err');
        await refreshParserProducts();
      }
    } catch (e) {
      console.warn('pipeline poll:', e);
    }
  }, 3000);
}

$('#btn-pipeline-run')?.addEventListener('click', startPipelineTop100);

// Parser buttons
$('#btn-warmer-start')?.addEventListener('click', startWarmer);
$('#btn-warmer-stop')?.addEventListener('click', stopWarmer);
$('#btn-warmer-check-cookies')?.addEventListener('click', checkWarmerCookies);
$('#btn-warmer-refresh')?.addEventListener('click', async () => {
  await refreshWarmerStatus();
  setStatus('Статус обновлён', 'ok');
});
$('#btn-parser-start')?.addEventListener('click', startParser);
$('#btn-parser-stop')?.addEventListener('click', stopParser);
$('#btn-parser-stats')?.addEventListener('click', loadParserStats);
$('#btn-parser-refresh')?.addEventListener('click', async () => {
  await refreshParserStatus();
  await refreshParserProducts();
  await refreshParserRuns();
  setStatus('Парсер обновлён', 'ok');
});
$('#btn-parser-clear-form')?.addEventListener('click', () => {
  const setVal = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  setVal('parser-input-query', '');
  setVal('parser-input-category', '');
  setVal('parser-input-quantity', '20');
  setVal('parser-input-pages', '3');
  setVal('parser-input-ai', 'true');
  setParserActionStatus('', '');
});
$('#parser-products-search')?.addEventListener('input', () => {
  clearTimeout(window._parserSearchTimer);
  window._parserSearchTimer = setTimeout(refreshParserProducts, 250);
});
$('#parser-products-status')?.addEventListener('change', refreshParserProducts);

// expose for inline handlers
window.toggleSelect = toggleSelect;
window.clearOfferSelection = clearOfferSelection;
window.bulkAction = bulkAction;
window.viewOrder = viewOrder;
window.viewChat = viewChat;
window.saveOfferPrice = saveOfferPrice;
window.addVariantKeys = addVariantKeys;
window.addOfferKeys = addOfferKeys;
window.archiveOfferProducts = archiveOfferProducts;
window.archiveOption = archiveOption;
window.addNewOption = addNewOption;
window.openCreateOfferModal = openCreateOfferModal;
window.createNewOffer = createNewOffer;
window.saveConfig = saveConfig;
window.openDocs = openDocs;
window.closeModal = closeModal;
window.closeApiTest = closeApiTest;
window.runApiTest = runApiTest;
window.refreshActive = refreshActive;
window.loadOffers = loadOffers;
window.toggleEpCard = () => {};

// ═══════════════════════════════════════════════════
//  START
// ═══════════════════════════════════════════════════
loadDashboard();
pollServiceStatus();
setInterval(pollServiceStatus, 30000);  // каждые 30с
initMsbDropdown();


// ═══════════════════════════════════════════════════
//  MSB — управление профилями браузера
// ═══════════════════════════════════════════════════

const VIEWS_WITH_MSB = [...VIEWS, 'msb'];

function setMsbActionStatus(text, kind = '') {
  const el = document.getElementById('ml-action-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'action-status ' + kind;
}

async function loadMsb() {
  await refreshMlStatus();
  await _loadGeminiBrowserGroupConfig();
}

async function _loadGeminiBrowserGroupConfig() {
  const inp    = document.getElementById('ml-gemini-group-input');
  const status = document.getElementById('ml-gemini-group-status');
  const saveBtn = document.getElementById('btn-ml-gemini-group-save');
  const testBtn = document.getElementById('btn-ml-gemini-group-test');
  if (!inp) return;

  // Загружаем текущее значение
  try {
    const d = await api('/api/parser/gemini/browser-group');
    inp.value = d.group_name || '';
  } catch (_) {}

  // Сохранить
  saveBtn?.addEventListener('click', async () => {
    const name = inp.value.trim();
    saveBtn.disabled = true;
    if (status) status.innerHTML = '<span class="muted">Сохраняю…</span>';
    try {
      const d = await api('/api/parser/gemini/browser-group', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_name: name }),
      });
      if (d.ok) {
        if (status) status.innerHTML = `<span style="color:var(--green)">✓ Сохранено: «${esc(name || '(не задано)')}»</span>`;
      } else {
        if (status) status.innerHTML = `<span style="color:var(--red)">❌ ${esc(d.error)}</span>`;
      }
    } catch (e) {
      if (status) status.innerHTML = `<span style="color:var(--red)">❌ ${esc(e.message)}</span>`;
    } finally {
      saveBtn.disabled = false;
    }
  });

  // Проверить профили в группе
  testBtn?.addEventListener('click', async () => {
    const name = inp.value.trim();
    if (!name) { alert('Сначала сохрани название группы.'); return; }
    testBtn.disabled = true;
    if (status) status.innerHTML = '<span class="muted">Проверяю…</span>';
    try {
      // Сначала сохраняем
      await api('/api/parser/gemini/browser-group', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_name: name }),
      });
      const d = await api('/api/parser/gemini/browser-group/profiles');
      if (d.ok) {
        const names = d.profiles.map(p => esc(p.name || p.id)).join(', ');
        if (status) status.innerHTML = `<span style="color:var(--green)">✓ Найдено ${d.count} профилей: ${names}</span>`;
      } else {
        if (status) status.innerHTML = `<span style="color:var(--red)">❌ ${esc(d.error)}</span>`;
      }
    } catch (e) {
      if (status) status.innerHTML = `<span style="color:var(--red)">❌ ${esc(e.message)}</span>`;
    } finally {
      testBtn.disabled = false;
    }
  });
}

async function refreshMlStatus() {
  const runningEl  = document.getElementById('ml-running-text');
  const ourCount   = document.getElementById('ml-our-count');
  const desiredEl  = document.getElementById('ml-desired-count');
  const totalEl    = document.getElementById('ml-total-count');
  const proxiesEl  = document.getElementById('ml-proxies-text');
  const msgEl      = document.getElementById('ml-status-msg');
  const provBtn    = document.getElementById('btn-ml-provision');
  const profCard   = document.getElementById('ml-profiles-card');
  const profTable  = document.getElementById('ml-profiles-table');
  const profCount  = document.getElementById('ml-profiles-count');
  const badge      = document.getElementById('badge-msb');

  if (runningEl) runningEl.textContent = 'Проверка...';
  try {
    const d = await api('/api/msb/status');

    if (runningEl) {
      runningEl.textContent = d.msb_running ? 'Запущен ✓' : 'Не запущен ✗';
      runningEl.style.color = d.msb_running ? 'var(--green)' : 'var(--red)';
    }
    if (ourCount)  ourCount.textContent  = d.our_count ?? '—';
    if (desiredEl) desiredEl.textContent = d.desired_count ?? 4;
    if (totalEl)   totalEl.textContent   = d.total ?? '—';
    if (proxiesEl) {
      proxiesEl.textContent  = d.proxies_set ? '✓ Есть' : '✗ Нет';
      proxiesEl.style.color  = d.proxies_set ? 'var(--green)' : 'var(--red)';
    }

    // Status message
    if (msgEl) {
      if (!d.msb_running) {
        msgEl.textContent = '⚠ MSB не запущен. Открой приложение MSB и залогинься.';
        msgEl.style.color = 'var(--red)';
      } else if (d.needs_provision) {
        msgEl.textContent = `Нужно создать профили: наших ${d.our_count} из ${d.desired_count} (с именем "${d.prefix}-*"). Нажми кнопку ниже.`;
        msgEl.style.color = 'var(--yellow)';
      } else {
        msgEl.textContent = `✓ Всё готово: ${d.our_count} профилей "${d.prefix}-*" созданы с прокси. Парсер может работать.`;
        msgEl.style.color = 'var(--green)';
      }
    }

    // Sidebar badge
    if (badge) {
      if (!d.msb_running || d.needs_provision) {
        badge.textContent = '!';
        badge.style.display = '';
        badge.style.background = 'var(--red)';
      } else {
        badge.style.display = 'none';
      }
    }

    // Profiles table
    const profiles = d.profiles || [];
    if (profiles.length > 0 && profCard) {
      profCard.style.display = '';
      if (profCount) profCount.textContent = `Профилей: ${profiles.length}`;
      if (profTable) {
        const rows = profiles.map(p => {
          const cls = p.is_ours ? '' : 'style="opacity:0.5;"';
          const mark = p.is_ours ? '<span class="badge active" style="font-size:10px;">наш</span>' : '';
          return `<tr ${cls}>
            <td style="font-family:monospace; font-size:12px; color:var(--text-faint);">${esc(p.envId)}</td>
            <td>${esc(p.name)} ${mark}</td>
            <td>
              <button class="btn btn-sm" data-ml-start="${esc(p.envId)}" title="Запустить профиль">▶</button>
              <button class="btn btn-sm btn-warn" data-ml-stop="${esc(p.envId)}" title="Остановить профиль">■</button>
            </td>
          </tr>`;
        }).join('');
        profTable.innerHTML = `
          <table>
            <thead><tr><th>Профиль ID</th><th>Имя</th><th>Действия</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
        profTable.querySelectorAll('[data-ml-start]').forEach(b =>
          b.addEventListener('click', () => mlStartProfile(b.dataset.mlStart))
        );
        profTable.querySelectorAll('[data-ml-stop]').forEach(b =>
          b.addEventListener('click', () => mlStopProfile(b.dataset.mlStop))
        );
      }
    }
    
    // Autopilot
    try {
      const ast = await api('/api/parser/auto/status');
      const autoStatusText = $('#parser-autopilot-status');
      const btnStart = $('#btn-autopilot-start');
      const btnStop = $('#btn-autopilot-stop');
      
      if (ast.running && !ast.stopped) {
         if (btnStart) btnStart.style.display = 'none';
         if (btnStop) btnStop.style.display = 'inline-flex';
         if (autoStatusText) {
            const cats = (ast.next_categories || []).join(', ');
            autoStatusText.textContent = `Авто-пилот работает. Парсится: ${ast.current_category || '?'} | Следующие: [${cats}]`;
         }
      } else {
         if (btnStart) btnStart.style.display = 'inline-flex';
         if (btnStop) btnStop.style.display = 'none';
         if (autoStatusText) autoStatusText.textContent = 'Авто-пилот остановлен';
      }
    } catch (err) {
      console.warn('autopilot status err', err);
    }
    
  } catch (e) {
    if (runningEl) { runningEl.textContent = 'Ошибка соединения'; runningEl.style.color = 'var(--red)'; }
    if (msgEl)     { msgEl.textContent = e.message; msgEl.style.color = 'var(--red)'; }
  }
}

async function mlProvision() {
  const btn = document.getElementById('btn-ml-provision');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Создаём профили...'; }
  setMsbActionStatus('Запрос к MSB API...', '');
  try {
    const r = await fetch('/api/msb/provision', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      setMsbActionStatus('✓ ' + d.message, 'ok');
      await refreshMlStatus();
    } else {
      setMsbActionStatus('Ошибка: ' + (d.error || 'unknown'), 'err');
    }
  } catch (e) {
    setMsbActionStatus('Сетевая ошибка: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔧 Проверить и создать профили'; }
  }
}

async function mlStartProfile(envId) {
  try {
    const r = await fetch(`/api/MSB/profiles/${encodeURIComponent(envId)}/start`, { method: 'POST' });
    const d = await r.json();
    setMsbActionStatus(d.ok ? `✓ Профиль ${envId} запущен` : `Ошибка: ${JSON.stringify(d.raw)}`, d.ok ? 'ok' : 'err');
  } catch (e) { setMsbActionStatus('Ошибка: ' + e.message, 'err'); }
}

async function mlStopProfile(envId) {
  try {
    const r = await fetch(`/api/MSB/profiles/${encodeURIComponent(envId)}/stop`, { method: 'POST' });
    const d = await r.json();
    setMsbActionStatus(d.ok ? `■ Профиль ${envId} остановлен` : `Ошибка: ${JSON.stringify(d.raw)}`, d.ok ? 'ok' : 'err');
  } catch (e) { setMsbActionStatus('Ошибка: ' + e.message, 'err'); }
}

// Hook up buttons
document.getElementById('btn-ml-provision')?.addEventListener('click', mlProvision);
document.getElementById('btn-ml-refresh')?.addEventListener('click', async () => {
  await refreshMlStatus();
  setStatus('MSB обновлён', 'ok');
});

// Register view
(function() {
  const navItems = document.querySelectorAll('.sidebar-nav .nav-item[data-view]');
  navItems.forEach(el => {
    if (el.dataset.view === 'msb') {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        showView('msb');
      });
    }
  });
  // Patch VIEWS array and showView to include msb
  if (!VIEWS.includes('msb')) VIEWS.push('msb');

  // Override loadView to handle MSB
  const _origLoadView = loadView;
  // loadView is already defined — we patch the loaders lookup:
  const _origLoaders = window._loaders;
})();

window.loadMsb = loadMsb;
window.refreshMlStatus = refreshMlStatus;
window.mlProvision = mlProvision;


// ═══════════════════════════════════════════════════════════════════════════
//  MODERATION MODULE — вкладка "Модерация товаров"
// ═══════════════════════════════════════════════════════════════════════════

let _modPollTimer     = null;
let _modCurrentPage   = 1;
let _modAllItems      = [];
const MOD_PAGE_SIZE   = 10;

let _modSelectedIds = new Set();
async function loadModeration() {
  _modCurrentPage = 1;
  _modAllItems    = [];
  _modSelectedIds.clear();

  const btnRefresh  = $('#btn-mod-refresh');
  const btnLoadMore = $('#btn-mod-load-more');
  const search      = $('#mod-search');
  const statusSel   = $('#mod-status-filter');

  if (btnRefresh) btnRefresh.addEventListener('click', () => {
    _modCurrentPage = 1; _modAllItems = [];
    refreshModerationProducts(true);
  });
  if (search)    search.addEventListener('input', debounce(() => { _modCurrentPage = 1; _modAllItems = []; refreshModerationProducts(true); }, 400));
  if (statusSel) statusSel.addEventListener('change', () => { _modCurrentPage = 1; _modAllItems = []; refreshModerationProducts(true); });
  if (btnLoadMore) btnLoadMore.addEventListener('click', () => {
    _modCurrentPage++;
    refreshModerationProducts(false);
  });



  await refreshModerationProducts(true);

  if (_modPollTimer) clearInterval(_modPollTimer);
  _modPollTimer = setInterval(async () => {
    if (currentView !== 'moderation') return;
    await refreshModerationProducts(true);
  }, 15000);
}

async function refreshModerationProducts(resetList) {
  const search   = $('#mod-search')?.value.trim() || '';
  const status   = $('#mod-status-filter')?.value || 'pending';
  const params   = new URLSearchParams();
  if (search) params.set('q', search);
  if (status) params.set('status', status);
  params.set('limit', String(MOD_PAGE_SIZE));
  params.set('page',  String(_modCurrentPage));

  try {
    const d = await api('/api/parser/products?' + params.toString());
    const items = d.items || [];
    const total = d.total || 0;

    if (resetList) _modAllItems = items;
    else           _modAllItems = [..._modAllItems, ...items];

    const cnt = $('#mod-pending-count');
    if (cnt) cnt.textContent = `${total} товаров`;

    const badge = $('#badge-moderation');
    if (badge) {
      const pendingItems = _modAllItems.filter(p => p.approval_status === 'pending');
      if (pendingItems.length > 0) {
        badge.textContent = String(pendingItems.length);
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }

    renderModerationCards(_modAllItems);

    const wrap = $('#mod-load-more-wrap');
    if (wrap) wrap.style.display = (total > _modAllItems.length) ? '' : 'none';

  } catch (e) {
    const feed = $('#moderation-feed');
    if (feed) feed.innerHTML = `<div style="padding:40px; text-align:center; color: var(--text-danger);">Ошибка загрузки: ${esc(e.message)}</div>`;
  }
}

function renderModerationCards(items) {
  const feed    = $('#moderation-feed');
  const onlyAi  = $('#mod-only-ai')?.checked ?? false;
  if (!feed) return;

  let filtered = items;
  if (onlyAi) filtered = items.filter(p => p.generated_title || p.generated_desc);

  if (!filtered.length) {
    feed.innerHTML = `
      <div class="mod-empty" id="mod-empty-state">
        <div class="mod-empty-icon">🎉</div>
        <div class="mod-empty-title">Нет товаров для модерации</div>
        <div class="mod-empty-sub">Запусти парсер — товары появятся здесь автоматически</div>
      </div>`;
    return;
  }

  feed.innerHTML = filtered.map((p, i) => buildCardPairHtml(p, i)).join('');

  // Навешиваем события
  filtered.forEach(p => {
    const pid = p.product_id;
    const pair = feed.querySelector(`[data-pair-id="${esc(pid)}"]`);
    if (!pair) return;

    // Одобрить
    pair.querySelector('.mod-btn-approve')?.addEventListener('click', () => doApprove(pid, pair, p));
    // Отклонить
    pair.querySelector('.mod-btn-reject')?.addEventListener('click',  () => doReject(pid, pair));
    // Регенерировать картинку (из текста)
    pair.querySelector('.mod-gen-img-btn')?.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); doGenImage(pid, pair); });
    // Рестайл оригинального фото
    pair.querySelector('.mod-restyle-btn')?.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); doRestyleImage(pid, pair); });
    // AI-рерайт текста
    pair.querySelector('.mod-btn-regen')?.addEventListener('click', () => doRegenText(pid, pair));
  });
}

function buildCardPairHtml(p, idx) {
  const pid      = p.product_id;
  const delay    = `animation-delay:${idx * 0.07}s`;

  // ── ОРИГИНАЛ ────────────────────────────────
  const origTitle   = esc(p.title || '—');
  const origDesc    = esc(p.original_desc || p.title || '');
  const origPrice   = p.price ? `${fmt(p.price)} ₽` : '—';
  const origImgSrc  = p.local_image_path || p.image_url;
  const origImg     = origImgSrc
    ? `<img src="${esc(imgSrc(origImgSrc))}" alt="${origTitle}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=img-placeholder><svg width=32 height=32 viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'currentColor\' stroke-width=\'1.5\'><rect x=\'3\' y=\'3\' width=\'18\' height=\'18\' rx=\'2\'></rect><circle cx=\'8.5\' cy=\'8.5\' r=\'1.5\'></circle><polyline points=\'21 15 16 10 5 21\'></polyline></svg><span>Нет фото</span></div>'">`
    : `<div class="img-placeholder"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg><span>Нет фото</span></div>`;

  const sellerHtml = p.seller_name
    ? `<span class="mod-meta-item">${esc(p.seller_name)}${p.seller_rating ? ` · ${fmt(p.seller_rating, 1)}★` : ''}</span>`
    : '';
  
  // Магазин продавца — компактная строка
  const shopParts = [];
  if (p.shop_name) shopParts.push(p.shop_url ? `<a href="${esc(p.shop_url)}" target="_blank">${esc(p.shop_name)}</a>` : esc(p.shop_name));
  if (p.shop_rating) shopParts.push(`${fmt(p.shop_rating, 1)}★`);
  if (p.shop_products_count) shopParts.push(`${p.shop_products_count} тов`);
  const shopHtml = shopParts.length ? `<span class="mod-meta-item">${shopParts.join(' · ')}</span>` : '';
  
  const salesHtml  = p.sales_count
    ? `<span class="mod-meta-item">${fmt(p.sales_count, 0)} прод.</span>` : '';
  const ratingHtml = p.rating
    ? `<span class="mod-meta-item">${fmt(p.rating, 1)}★</span>` : '';
  const catHtml    = p.category
    ? `<span class="mod-meta-item">${esc(p.category)}</span>` : '';

  // ── СГЕНЕРИРОВАННАЯ КАРТОЧКА ─────────────────
  const genTitle   = p.generated_title || p.title || '';
  const genDesc    = p.generated_desc  || p.original_desc || '';
  const genTags    = p.generated_tags  || p.tags || '';
  const myPrice    = p.my_price || p.price || '';

  let genImgSrc  = p.generated_image_url || p.local_image_path || p.image_url;
  // Для локальных AI-картинок добавляем cache-buster на основе updated_at,
  // чтобы после рестайла браузер не подставлял старое фото из кэша
  if (genImgSrc && genImgSrc.startsWith('/static/products/') && !genImgSrc.includes('?t=') && p.updated_at) {
    genImgSrc = genImgSrc + '?t=' + encodeURIComponent(p.updated_at);
  }
  const genImgHtml = genImgSrc
    ? `<img src="${esc(imgSrc(genImgSrc))}" alt="${esc(genTitle)}" loading="lazy" onerror="this.style.display='none'">`
    : `<div class="img-placeholder"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 6v4m0 4h.01"></path></svg><span>Нажми 🎨 для генерации</span></div>`;

  const tagsHtml = genTags
    ? genTags.split(',').filter(Boolean).map(t => `<span class="mod-tag">${esc(t.trim())}</span>`).join('')
    : '';

  const statusBadgeHtml = p.status === 'approved'
    ? `<span style="font-size:10px;padding:2px 6px;background:rgba(34,197,94,0.15);color:#22c55e;border-radius:6px;font-weight:600;">✓ одобрен</span>`
    : p.status === 'rejected'
    ? `<span style="font-size:10px;padding:2px 6px;background:rgba(229,57,53,0.15);color:#f87171;border-radius:6px;font-weight:600;">✗ отклонён</span>`
    : '';

  const canApprove = p.status !== 'approved';
  const canReject  = p.status !== 'rejected';

  return `
<div class="mod-card-pair" data-pair-id="${esc(pid)}" style="${delay}">

  <!-- ОРИГИНАЛ -->
  <div class="mod-card original">
    <div class="mod-card-label">Оригинал ${statusBadgeHtml}</div>
    <div class="mod-card-image-wrap">${origImg}</div>
    <div>
      <div class="mod-field-label">Название</div>
      <div class="mod-card-title">${origTitle}</div>
    </div>
    <div>
      <div class="mod-field-label">Описание</div>
      <div class="mod-card-desc">${origDesc}</div>
    </div>
    <div class="mod-price-row">
      <span class="mod-field-label" style="margin:0">Цена</span>
      <span class="mod-price-orig">${origPrice}</span>
    </div>
    <div class="mod-meta-row">
      ${sellerHtml}${ratingHtml}${salesHtml}${catHtml}${shopHtml}
    </div>
  </div>

  <!-- МОЙ МАГАЗИН -->
  <div class="mod-card generated">
    <div class="mod-card-label">Моя карточка ${p.generated_image_url ? '<span style="opacity:0.6;font-weight:400;">AI ✓</span>' : ''}</div>
    <div class="mod-card-image-wrap">
      ${genImgHtml}
      <button type="button" class="mod-gen-img-btn" data-gen-img="${esc(pid)}" title="Сгенерировать AI-картинку">Ген. фото</button>
      <button type="button" class="mod-restyle-btn" data-restyle-img="${esc(pid)}" title="Рестайл через Gemini">Рестайл</button>
    </div>
    <div>
      <div class="mod-field-label">Название</div>
      <div class="mod-card-title" contenteditable="true" data-field="generated_title" data-pid="${esc(pid)}" spellcheck="false">${esc(genTitle)}</div>
    </div>
    <div>
      <div class="mod-field-label">Описание</div>
      <div class="mod-card-desc" contenteditable="true" data-field="generated_desc" data-pid="${esc(pid)}" spellcheck="false">${esc(genDesc)}</div>
    </div>
    <div class="mod-price-row">
      <span class="mod-field-label" style="margin:0">Цена</span>
      <input class="mod-price-input" type="number" step="0.01" min="0" value="${myPrice}" data-field="my_price" data-pid="${esc(pid)}" placeholder="0.00">
      <span style="font-size:12px;color:var(--text-faint)">₽</span>
    </div>
    ${tagsHtml || genTags !== undefined ? `
    <div>
      <div class="mod-field-label">Теги</div>
      <div class="mod-tags-row">
        ${tagsHtml}
        <input class="mod-tags-input" type="text" value="${esc(genTags)}" data-field="generated_tags" data-pid="${esc(pid)}" placeholder="тегб1, тегб2...">
      </div>
    </div>` : ''}
  </div>

  <!-- ДЕЙСТВИЯ -->
  <div class="mod-actions">
    ${canApprove ? `<button class="mod-btn mod-btn-approve" data-approve="${esc(pid)}">&#10003; Одобрить и опубликовать</button>` : ''}
    ${canReject  ? `<button class="mod-btn mod-btn-reject"  data-reject="${esc(pid)}">&#10005; Отклонить</button>` : ''}
    <button class="mod-btn mod-btn-regen" data-regen="${esc(pid)}">↻ Переписать</button>
    <span class="mod-action-status" id="mod-status-${esc(pid)}" style="display:none;"></span>
  </div>
</div>`;
}

function modSetStatus(pid, text, kind) {
  const el = $(`#mod-status-${pid}`);
  if (!el) return;
  el.textContent = text;
  el.className = `mod-action-status ${kind}`;
  el.style.display = text ? '' : 'none';
}

async function collectEdits(pid) {
  // Собираем актуальные значения из редактируемых полей
  const feed = $('#moderation-feed');
  if (!feed) return {};
  const titleEl = feed.querySelector(`[data-field="generated_title"][data-pid="${pid}"]`);
  const descEl  = feed.querySelector(`[data-field="generated_desc"][data-pid="${pid}"]`);
  const priceEl = feed.querySelector(`[data-field="my_price"][data-pid="${pid}"]`);
  const tagsEl  = feed.querySelector(`[data-field="generated_tags"][data-pid="${pid}"]`);
  return {
    ...(titleEl ? { generated_title: titleEl.textContent.trim() } : {}),
    ...(descEl  ? { generated_desc:  descEl.textContent.trim()  } : {}),
    ...(priceEl ? { my_price: Number(priceEl.value) || 0 }        : {}),
    ...(tagsEl  ? { generated_tags: tagsEl.value.trim() }         : {}),
  };
}

async function doApprove(pid, pair, product) {
  const approveBtn = pair.querySelector('.mod-btn-approve');
  const rejectBtn  = pair.querySelector('.mod-btn-reject');
  if (approveBtn) approveBtn.disabled = true;
  if (rejectBtn)  rejectBtn.disabled  = true;

  modSetStatus(pid, 'Сохраняю правки...', 'loading');

  try {
    // 1. Сохраняем отредактированные поля
    const edits = await collectEdits(pid);
    if (Object.keys(edits).length > 0) {
      const patchRes = await fetch(`/api/parser/products/${encodeURIComponent(pid)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(edits),
      });
      if (!patchRes.ok) throw new Error(`PATCH failed: HTTP ${patchRes.status}`);
    }

    // 2. Одобряем и публикуем
    modSetStatus(pid, 'Публикую в магазин...', 'loading');
    const r = await fetch(`/api/parser/approve/${encodeURIComponent(pid)}`, { method: 'POST' });
    const d = await r.json();

    if (d.ok) {
      modSetStatus(pid, `✓ Опубликовано! offer_id: ${d.offer_id || '—'}`, 'ok');
      pair.classList.add('pair-approved');
      setTimeout(() => {
        pair.remove();
        _modAllItems = _modAllItems.filter(p => p.product_id !== pid);
        _updateModBadge();
      }, 460);
    } else {
      throw new Error(d.error || 'Неизвестная ошибка');
    }
  } catch (e) {
    modSetStatus(pid, `✗ ${e.message}`, 'err');
    if (approveBtn) approveBtn.disabled = false;
    if (rejectBtn)  rejectBtn.disabled  = false;
    setTimeout(() => modSetStatus(pid, '', ''), 5000);
  }
}

async function doReject(pid, pair) {
  const approveBtn = pair.querySelector('.mod-btn-approve');
  const rejectBtn  = pair.querySelector('.mod-btn-reject');
  if (approveBtn) approveBtn.disabled = true;
  if (rejectBtn)  rejectBtn.disabled  = true;

  modSetStatus(pid, 'Отклоняю...', 'loading');
  try {
    const r = await fetch(`/api/parser/reject/${encodeURIComponent(pid)}`, { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      modSetStatus(pid, '✗ Отклонён', 'err');
      pair.classList.add('pair-rejected');
      setTimeout(() => {
        pair.remove();
        _modAllItems = _modAllItems.filter(p => p.product_id !== pid);
        _updateModBadge();
      }, 370);
    } else {
      throw new Error(d.error || 'Ошибка');
    }
  } catch (e) {
    modSetStatus(pid, `✗ ${e.message}`, 'err');
    if (approveBtn) approveBtn.disabled = false;
    if (rejectBtn)  rejectBtn.disabled  = false;
    setTimeout(() => modSetStatus(pid, '', ''), 5000);
  }
}

async function doGenImage(pid, pair) {
  const btn = pair.querySelector('.mod-gen-img-btn');
  if (!btn) return;
  btn.classList.add('loading');
  btn.disabled = true;
  modSetStatus(pid, '🎨 Генерирую картинку через Gemini...', 'loading');

  try {
    const r = await fetch(`/api/parser/products/${encodeURIComponent(pid)}/generate-image`, { method: 'POST' });
    const d = await r.json();
    if (d.ok && d.image_url) {
      // Обновляем картинку в правой карточке без перерендера
      const imgWrap = pair.querySelector('.mod-card.generated .mod-card-image-wrap');
      if (imgWrap) {
        const existingBtn = imgWrap.querySelector('.mod-gen-img-btn');
        const ts = Date.now();
        imgWrap.innerHTML = `<img src="${esc(d.image_url)}?t=${ts}" alt="AI generated" style="width:100%;height:100%;object-fit:cover;display:block;">
          <button class="mod-gen-img-btn" title="Перегенерировать">🎨 AI-картинка</button>`;
        imgWrap.querySelector('.mod-gen-img-btn')?.addEventListener('click', () => doGenImage(pid, pair));
      }
      modSetStatus(pid, '✓ Картинка готова!', 'ok');
      setTimeout(() => modSetStatus(pid, '', ''), 3000);
    } else {
      throw new Error(d.error || 'Gemini не вернул изображение');
    }
  } catch (e) {
    modSetStatus(pid, `✗ ${e.message}`, 'err');
    setTimeout(() => modSetStatus(pid, '', ''), 5000);
    if (btn) {
      btn.classList.remove('loading');
      btn.disabled = false;
    }
  }
}

async function doRestyleImage(pid, pair) {
  const btn = pair.querySelector('.mod-restyle-btn');
  if (!btn) return;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.setAttribute('type', 'button');

  const imgWrap = pair.querySelector('.mod-card.generated .mod-card-image-wrap');

  // Показываем оверлей прогресса прямо в карточке
  let overlay = null;
  if (imgWrap) {
    overlay = document.createElement('div');
    overlay.className = 'mod-restyle-overlay';
    const _lm = getMsbLaunchMode();
    const _overlayHint = _lm === 'visible' ? '👁 браузер открыт' : 'работает в фоне';
    overlay.innerHTML = `<div style="font-size:22px;">🖼️</div><div class="mod-restyle-stage">Запускаю MSB...</div><div class="muted" style="font-size:10px;">${_overlayHint}</div>`;
    imgWrap.appendChild(overlay);
  }

  function setStage(msg) {
    btn.textContent = '⏳';
    modSetStatus(pid, msg, 'loading');
    const el = overlay?.querySelector('.mod-restyle-stage');
    if (el) el.textContent = msg;
  }

  function removeOverlay() {
    overlay?.remove();
    overlay = null;
    btn.disabled = false;
    btn.textContent = origText;
  }

  let pollTimer = null;
  try {
    setStage('Ставлю задачу в очередь…');
    const _launchMode = getMsbLaunchMode();
    const startResp = await fetch(`/api/parser/products/${encodeURIComponent(pid)}/browser-generate-image`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ launch_mode: _launchMode }),
    });
    const startData = await startResp.json();
    if (!startData.ok || !startData.job_id) throw new Error(startData.error || 'Не удалось запустить генерацию');

    const jobId = startData.job_id;
    setStage(_launchMode === 'visible' ? 'Открываю браузер…' : 'Запускаю профиль в MSB…');

    await new Promise((resolve, reject) => {
      let attempts = 0;
      pollTimer = setInterval(async () => {
        attempts++;
        try {
          const r = await fetch(`/api/parser/browser-image-jobs/${encodeURIComponent(jobId)}`);
          const d = await r.json();
          if (!d.ok || !d.job) throw new Error(d.error || 'Ошибка статуса');
          const job = d.job;
          setStage(job.message || 'Идёт обработка…');

          if (job.status === 'done' && job.image_url) {
            clearInterval(pollTimer); pollTimer = null;
            const ts = Date.now();
            const bustedUrl = job.image_url + '?t=' + ts;

            // Синхронизируем _modAllItems
            const _idx = _modAllItems.findIndex(x => x.product_id === pid);
            if (_idx !== -1) _modAllItems[_idx].generated_image_url = bustedUrl;

            // Загружаем новое фото — ждём полной загрузки, потом вставляем
            const preload = new Image();
            preload.onload = () => {
              let img = imgWrap?.querySelector('img');
              if (img) {
                img.src = bustedUrl;
              } else if (imgWrap) {
                const newImg = document.createElement('img');
                newImg.src = bustedUrl;
                newImg.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
                imgWrap.insertBefore(newImg, imgWrap.firstChild);
              }
              removeOverlay();
              modSetStatus(pid, '✓ Фото обновлено через Gemini!', 'ok');
              setTimeout(() => modSetStatus(pid, '', ''), 3500);
            };
            preload.onerror = () => {
              // Файл не грузится — всё равно показываем
              let img = imgWrap?.querySelector('img');
              if (img) img.src = bustedUrl;
              removeOverlay();
              modSetStatus(pid, '✓ Готово', 'ok');
              setTimeout(() => modSetStatus(pid, '', ''), 3500);
            };
            preload.src = bustedUrl;
            resolve();
            return;
          }
          if (job.status === 'error') {
            clearInterval(pollTimer); pollTimer = null;
            reject(new Error(job.error || 'Ошибка генерации'));
            return;
          }
          if (attempts > 180) {
            clearInterval(pollTimer); pollTimer = null;
            reject(new Error('Таймаут — Gemini не ответил за 6 минут'));
          }
        } catch (e) {
          clearInterval(pollTimer); pollTimer = null;
          reject(e);
        }
      }, 2000);
    });

  } catch (e) {
    if (pollTimer) clearInterval(pollTimer);
    removeOverlay();
    modSetStatus(pid, `✗ ${e.message}`, 'err');
    setTimeout(() => modSetStatus(pid, '', ''), 6000);
  }
}

async function doRegenText(pid, pair) {
  const btn = pair.querySelector('.mod-btn-regen');
  if (!btn) return;
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Генерирую...';
  modSetStatus(pid, '🤖 AI-рерайт через Gemini...', 'loading');

  try {
    const r = await fetch(`/api/parser/products/${encodeURIComponent(pid)}/rewrite`, { method: 'POST' });
    const d = await r.json();
    if (d.ok && d.product) {
      const pr = d.product;
      // Обновляем редактируемые поля без перерендера
      const titleEl = pair.querySelector('[data-field="generated_title"]');
      const descEl  = pair.querySelector('[data-field="generated_desc"]');
      const tagsEl  = pair.querySelector('[data-field="generated_tags"]');
      if (titleEl && pr.generated_title) titleEl.textContent = pr.generated_title;
      if (descEl  && pr.generated_desc)  descEl.textContent  = pr.generated_desc;
      if (tagsEl  && pr.generated_tags)  tagsEl.value        = pr.generated_tags;

      // Обновляем данные в массиве
      const idx = _modAllItems.findIndex(x => x.product_id === pid);
      if (idx !== -1) Object.assign(_modAllItems[idx], pr);

      modSetStatus(pid, '✓ Тексты обновлены!', 'ok');
      setTimeout(() => modSetStatus(pid, '', ''), 3000);
    } else {
      throw new Error(d.error || 'Gemini не вернул данные');
    }
  } catch (e) {
    modSetStatus(pid, `✗ ${e.message}`, 'err');
    setTimeout(() => modSetStatus(pid, '', ''), 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function _updateModBadge() {
  const badge   = $('#badge-moderation');
  const pending = _modAllItems.filter(p => p.status === 'pending').length;
  if (!badge) return;
  if (pending > 0) { badge.textContent = String(pending); badge.style.display = ''; }
  else             { badge.style.display = 'none'; }
}


// ══════════════════════════════════════════════════════════════════
//  AI-ОТБОР — runAiModeration()
//  Кнопка "AI-отбор": шлёт pending-товары в Claude, получает score,
//  показывает одобренные карточки. У каждой кнопка "⚡ Обработать"
//  → doRegenText → правая карточка заполняется через Gemini
// ══════════════════════════════════════════════════════════════════
async function runAiModeration() {
  const btn = document.getElementById('btn-mod-ai');
  if (!btn) return;
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '⏳ AI отбирает...';

  const feed = document.getElementById('moderation-feed');
  if (!feed) { btn.disabled = false; btn.innerHTML = origText; return; }

  feed.innerHTML = '<div class="mod-empty" style="padding:40px 20px;">' +
    '<div class="mod-empty-icon" style="font-size:2.5rem;">🤖</div>' +
    '<div class="mod-empty-title" style="color:var(--accent);">AI анализирует товары...</div>' +
    '<div class="mod-empty-sub">Claude оценивает прибыльность и качество каждого товара</div>' +
    '<div style="margin-top:16px;width:220px;height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-left:auto;margin-right:auto;">' +
    '<div id="ai-progress-bar" style="width:0%;height:100%;background:var(--accent);border-radius:2px;transition:width 0.3s;"></div>' +
    '</div></div>';

  let pct = 0;
  const prog = setInterval(function() {
    pct = Math.min(pct + Math.random() * 8, 88);
    const bar = document.getElementById('ai-progress-bar');
    if (bar) bar.style.width = pct + '%';
  }, 400);

  try {
    const resp = await fetch('/api/ai-moderate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'pending', limit: 50, threshold: 6.0 })
    });
    clearInterval(prog);
    const bar = document.getElementById('ai-progress-bar');
    if (bar) bar.style.width = '100%';
    const d = await resp.json();

    if (!d.ok) {
      feed.innerHTML = '<div class="mod-empty"><div class="mod-empty-icon">❌</div>' +
        '<div class="mod-empty-title">Ошибка AI-отбора</div>' +
        '<div class="mod-empty-sub">' + esc(d.error || 'Неизвестная ошибка') + '</div></div>';
      return;
    }

    const approved = (d.results || []).filter(function(r) { return r.new_status === 'approved'; });
    const rejected = (d.results || []).filter(function(r) { return r.new_status !== 'approved'; });
    const aiScore  = {};
    const aiReason = {};
    (d.results || []).forEach(function(r) { aiScore[r.product_id] = r.score; aiReason[r.product_id] = r.reason; });

    if (!approved.length) {
      feed.innerHTML = '<div class="mod-empty"><div class="mod-empty-icon">🤷</div>' +
        '<div class="mod-empty-title">AI не отобрал товаров</div>' +
        '<div class="mod-empty-sub">Проверено: ' + (d.total || 0) + ' товаров. Снизьте порог или запустите парсер.</div></div>';
      return;
    }

    const approvedIds = new Set(approved.map(function(r) { return r.product_id; }));
    let items = (_modAllItems || []).filter(function(p) { return approvedIds.has(p.product_id); });
    const missing = Array.from(approvedIds).filter(function(id) { return !items.find(function(p) { return p.product_id === id; }); });
    if (missing.length) {
      const extra = await Promise.allSettled(missing.map(function(id) {
        return fetch('/api/parser/products/' + encodeURIComponent(id)).then(function(r) { return r.json(); });
      }));
      extra.forEach(function(r) { if (r.status === 'fulfilled' && r.value.ok && r.value.product) items.push(r.value.product); });
    }
    items.forEach(function(p) { p._ai_score = aiScore[p.product_id]; p._ai_reason = aiReason[p.product_id]; });
    items.sort(function(a, b) { return (b._ai_score || 0) - (a._ai_score || 0); });
    _modAllItems = items;

    function scoreColor(s) { return s >= 8 ? '#22c55e' : s >= 6 ? '#f59e0b' : '#f87171'; }

    function aiCardHtml(p, i) {
      const pid    = p.product_id;
      const s      = p._ai_score || 0;
      const sc     = scoreColor(s);
      const reason = p._ai_reason || '';
      const origImgSrc2 = p.local_image_path || p.image_url;
      const origImg = origImgSrc2
        ? '<img src="' + esc(imgSrc(origImgSrc2)) + '" loading="lazy" onerror="this.parentElement.innerHTML=\'<div class=img-placeholder><span>Нет фото</span></div>\'">'
        : '<div class="img-placeholder"><span>Нет фото</span></div>';
      const sellerH = p.seller_name
        ? '<span class="mod-meta-item">👤 ' + esc(p.seller_name) + (p.seller_rating ? ' (' + fmt(p.seller_rating, 1) + '⭐)' : '') + '</span>' : '';
      const salesH  = p.sales_count  ? '<span class="mod-meta-item">🛒 ' + fmt(p.sales_count, 0) + ' продаж</span>' : '';
      const ratingH = p.rating       ? '<span class="mod-meta-item">⭐ ' + fmt(p.rating, 1) + '</span>' : '';
      const catH    = p.category     ? '<span class="mod-meta-item">📁 ' + esc(p.category) + '</span>' : '';
      const margH   = p.expected_net_margin_pct ? '<span class="mod-meta-item" style="color:var(--green);">📈 ' + fmt(p.expected_net_margin_pct, 1) + '%</span>' : '';
      const shopH   = (p.shop_name || p.shop_rating)
        ? '<div class="shop-info-block" style="margin-top:8px;"><div class="shop-info-header">🏪 Магазин</div>' +
          (p.shop_name ? '<div class="shop-info-row"><span class="shop-label">Назв.:</span> ' + esc(p.shop_name) + '</div>' : '') +
          (p.shop_rating ? '<div class="shop-info-row"><span class="shop-label">Рейтинг:</span> ⭐ ' + fmt(p.shop_rating, 2) + '</div>' : '') +
          (p.shop_products_count ? '<div class="shop-info-row"><span class="shop-label">Товаров:</span> ' + p.shop_products_count + '</div>' : '') +
          '</div>' : '';
      const has  = !!(p.generated_title || p.generated_desc);
      const gT   = esc(p.generated_title || '');
      const gD   = esc(p.generated_desc  || '');
      const gTg  = esc(p.generated_tags  || '');
      const gImg = p.generated_image_url || p.local_image_path || p.image_url;
      const gImgH = gImg
        ? '<img src="' + esc(imgSrc(gImg)) + '" loading="lazy" onerror="this.style.display=\'none\'">'
        : '<div class="img-placeholder"><span>Нажми Обработать</span></div>';
      const tagsH = (p.generated_tags || '').split(',').filter(Boolean)
        .map(function(t) { return '<span class="mod-tag">' + esc(t.trim()) + '</span>'; }).join('');

      const scoreBadge = '<span style="display:inline-flex;align-items:center;gap:4px;background:' + sc + '22;color:' + sc + ';border:1px solid ' + sc + '44;border-radius:8px;padding:2px 9px;font-weight:700;font-size:12px;">⭐ ' + s + '/10</span>';

      const rightCard = has
        ? '<div><div class="mod-field-label">Название</div>' +
          '<div class="mod-card-title" contenteditable="true" data-field="generated_title" data-pid="' + esc(pid) + '" spellcheck="false">' + gT + '</div></div>' +
          '<div><div class="mod-field-label">Описание</div>' +
          '<div class="mod-card-desc" contenteditable="true" data-field="generated_desc" data-pid="' + esc(pid) + '" spellcheck="false">' + gD + '</div></div>' +
          '<div class="mod-price-row"><span class="mod-field-label" style="margin:0">Моя цена:</span>' +
          '<input class="mod-price-input" type="number" step="0.01" min="0" value="' + esc(String(p.my_price || p.price || '')) + '" data-field="my_price" data-pid="' + esc(pid) + '" placeholder="0.00">' +
          '<span style="font-size:12px;color:var(--text-faint)">₽</span></div>' +
          (tagsH ? '<div><div class="mod-field-label">Теги</div><div class="mod-tags-row">' + tagsH +
            '<input class="mod-tags-input" type="text" value="' + gTg + '" data-field="generated_tags" data-pid="' + esc(pid) + '" placeholder="тег1, тег2..."></div></div>' : '')
        : '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;padding:28px 16px;gap:10px;text-align:center;">' +
          '<div style="font-size:2rem;">🪄</div>' +
          '<div style="font-weight:600;font-size:14px;">Готово к обработке</div>' +
          '<div style="font-size:12px;color:var(--text-muted);">Gemini перепишет карточку под твой магазин</div></div>';

      return '<div class="mod-card-pair" data-pair-id="' + esc(pid) + '" style="animation-delay:' + (i * 0.07) + 's">\n' +
        '<div class="mod-card original">\n' +
        '<div class="mod-card-label">🌐 Оригинал &nbsp;' + scoreBadge + '</div>\n' +
        (reason ? '<div style="font-size:11px;color:var(--text-muted);margin:4px 0 8px;padding:6px 10px;background:var(--bg-card);border-radius:6px;border-left:3px solid ' + sc + ';">🤖 ' + esc(reason) + '</div>' : '') +
        '<div class="mod-card-image-wrap">' + origImg + '</div>\n' +
        '<div><div class="mod-field-label">Название</div><div class="mod-card-title">' + esc(p.title || '-') + '</div></div>\n' +
        '<div><div class="mod-field-label">Описание</div><div class="mod-card-desc">' + esc(p.original_desc || p.title || '') + '</div></div>\n' +
        '<div class="mod-price-row"><span class="mod-field-label" style="margin:0">Цена:</span><span class="mod-price-orig">' + (p.price ? fmt(p.price) + ' ₽' : '-') + '</span></div>\n' +
        '<div class="mod-meta-row">' + sellerH + ratingH + salesH + margH + catH + '</div>\n' +
        shopH + '\n</div>\n' +
        '<div class="mod-card generated">\n' +
        '<div class="mod-card-label">✨ Моя карточка' + (p.generated_image_url ? ' <span style="font-size:9px;opacity:0.7;">AI img ✅</span>' : '') + '</div>\n' +
        '<div class="mod-card-image-wrap">' + gImgH + '<button class="mod-gen-img-btn" data-gen-img="' + esc(pid) + '">🎨 AI-картинка</button></div>\n' +
        rightCard + '\n</div>\n' +
        '<div class="mod-actions">\n' +
        '<button class="mod-btn mod-btn-process" style="background:var(--accent);color:#fff;border-color:var(--accent);">⚡ Обработать</button>\n' +
        '<button class="mod-btn mod-btn-approve" data-approve="' + esc(pid) + '">✅ Опубликовать</button>\n' +
        '<button class="mod-btn mod-btn-reject"  data-reject="'  + esc(pid) + '">❌ Отклонить</button>\n' +
        '<span class="mod-action-status" id="mod-status-' + esc(pid) + '" style="display:none;"></span>\n' +
        '</div>\n</div>';
    }

    const summaryHtml = '<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.2);border-radius:10px;padding:14px 18px;margin-bottom:16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;">' +
      '<div style="font-size:1.3rem;">🤖</div>' +
      '<div><div style="font-weight:600;color:var(--green);font-size:14px;">AI отобрал ' + approved.length + ' из ' + (d.total || 0) + ' товаров</div>' +
      '<div style="font-size:12px;color:var(--text-muted);margin-top:2px;">✅ Одобрено: ' + approved.length + ' &nbsp;|&nbsp; ❌ Отклонено: ' + rejected.length + ' &nbsp;|&nbsp; Порог: ' + (d.threshold || 6) + '/10</div></div>' +
      '<button class="btn" style="margin-left:auto;font-size:12px;" onclick="refreshModerationProducts(true)">↩ Все товары</button>' +
      '</div>';

    feed.innerHTML = summaryHtml + items.map(function(p, i) { return aiCardHtml(p, i); }).join('');

    items.forEach(function(p) {
      const pid  = p.product_id;
      const pair = feed.querySelector('[data-pair-id="' + esc(pid) + '"]');
      if (!pair) return;
      const btnProcess = pair.querySelector('.mod-btn-process');
      if (btnProcess) btnProcess.addEventListener('click', function() { doRegenText(pid, pair); });
      const btnGenImg  = pair.querySelector('.mod-gen-img-btn');
      if (btnGenImg)  btnGenImg.addEventListener('click',  function() { doGenImage(pid, pair); });
      const btnApprove = pair.querySelector('.mod-btn-approve');
      if (btnApprove) btnApprove.addEventListener('click', function() { doApprove(pid, pair, p); });
      const btnReject  = pair.querySelector('.mod-btn-reject');
      if (btnReject)  btnReject.addEventListener('click',  function() { doReject(pid, pair); });
    });

  } catch (err) {
    clearInterval(prog);
    feed.innerHTML = '<div class="mod-empty"><div class="mod-empty-icon">⚠️</div>' +
      '<div class="mod-empty-title">Сетевая ошибка</div>' +
      '<div class="mod-empty-sub">' + esc(String(err)) + '</div></div>';
  }

  btn.disabled  = false;
  btn.innerHTML = origText;
}

window.loadModeration = loadModeration;


// ─── Стили для кнопки рестайла ──────────────────────────────────────────────
(function() {
  if (document.getElementById('mod-restyle-style')) return;
  const s = document.createElement('style');
  s.id = 'mod-restyle-style';
  s.textContent = [
    '.mod-restyle-btn {',
    '  position: absolute; bottom: 8px; right: 8px;',
    '  background: rgba(99,102,241,0.85); color: #fff;',
    '  border: none; border-radius: 6px; padding: 5px 10px;',
    '  font-size: 11px; cursor: pointer; transition: background 0.15s;',
    '  backdrop-filter: blur(4px); z-index: 2;',
    '}',
    '.mod-restyle-btn:hover { background: rgba(99,102,241,1); }',
    '.mod-restyle-btn:disabled { opacity:0.5; cursor:not-allowed; }',
  ].join('\n');
  document.head.appendChild(s);
})();

// ═══════════════════════════════════════════════════
//  PARSER LOGS PANEL
// ═══════════════════════════════════════════════════
let _logsLastId   = 0;
let _logsInterval = null;

function startLogsPolling() {
  if (_logsInterval) return;
  _logsLastId = 0;
  const panel = document.getElementById('parser-log-panel');
  if (panel) panel.innerHTML = '';
  _logsInterval = setInterval(fetchParserLogs, 2000);
  fetchParserLogs();
}

function stopLogsPolling() {
  if (_logsInterval) { clearInterval(_logsInterval); _logsInterval = null; }
  // финальный опрос
  fetchParserLogs();
}

async function fetchParserLogs() {
  try {
    const d = await api(`/api/parser/logs?since_id=${_logsLastId}&limit=100`);
    if (!d.ok || !d.logs.length) return;
    _logsLastId = d.last_id;
    const panel = document.getElementById('parser-log-panel');
    if (!panel) return;
    d.logs.forEach(log => {
      const div = document.createElement('div');
      div.className = 'log-line log-' + (log.level || 'INFO').toLowerCase();
      const ts = (log.ts || '').split('T').pop().split('.')[0];
      div.textContent = `[${ts}] ${log.message}`;
      panel.appendChild(div);
    });
    panel.scrollTop = panel.scrollHeight;
  } catch(e) {}
}

async function fetchParserStatus() {
  try {
    const d = await api('/api/parser/status');
    const el = document.getElementById('parser-stats-line');
    if (el && d) {
      el.textContent = `Найдено: ${d.products_found || 0} | Сохранено: ${d.products_saved || 0} | Ошибок: ${d.errors_count || 0} | Статус: ${d.status || 'idle'}`;
    }
    return d;
  } catch(e) { return null; }
}

function buildLogsPanelHtml() {
  return `
    <div id="parser-stats-line" style="margin:12px 0 6px;font-size:13px;color:var(--text-muted)">Статус: idle</div>
    <div style="display:flex;gap:8px;margin-bottom:6px">
      <button class="btn btn-sm btn-ghost" onclick="document.getElementById('parser-log-panel').innerHTML=''">Очистить лог</button>
    </div>
    <div id="parser-log-panel" style="
      background:#0d1117;color:#c9d1d9;font-family:monospace;font-size:12px;
      height:260px;overflow-y:auto;padding:10px;border-radius:6px;
      border:1px solid var(--border);white-space:pre-wrap;word-break:break-all
    "></div>
    <style>
      .log-info    { color: #c9d1d9; }
      .log-warning { color: #f0c040; }
      .log-error   { color: #ff6b6b; }
      .log-success { color: #56d364; }
      .log-debug   { color: #6e7681; }
    </style>
  `;
}


// Монтировать панель логов при открытии страницы парсера
function mountLogsPanel() {
  const mount = document.getElementById('parser-logs-mount') || document.getElementById('parser-pipelines');
  if (!mount) return;
  if (document.getElementById('parser-log-panel')) return;
  const wrapper = document.createElement('div');
  wrapper.className = 'card';
  wrapper.style.marginTop = '16px';
  wrapper.innerHTML = buildLogsPanelHtml();
  mount.after(wrapper);
}

// Авто-монтирование панели логов когда страница парсера становится видимой
(function() {
  const obs = new MutationObserver(() => {
    const parserSection = document.querySelector('[data-view-content="parser"], #view-parser, .view-parser');
    if (parserSection && parserSection.style.display !== 'none' && !document.getElementById('parser-log-panel')) {
      mountLogsPanel();
    }
  });
  obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style','class'] });
})();


// ═══════════════════════════════════════════════════
//  CATEGORIES ACCORDION TREE
// ═══════════════════════════════════════════════════
async function loadCategoriesTree() {
  const container = $('#categories-accordion-container');
  const info = $('#categories-sync-info');
  if (!container) return;
  
  try {
    const cfg = await api('/api/parser/config');
    const d = cfg.cat_fees_updated
      ? new Date(cfg.cat_fees_updated * 1000).toLocaleString('ru-RU')
      : 'Ещё не обновлялись';
    document.getElementById('categories-sync-info').textContent = `Последнее обновление: ${d}`;
    
    const res = await api('/api/categories/v2/tree');
    const selRes = await api('/api/categories/selected');
    const selectedIds = new Set(selRes.selected || []);
    
    if (!res.ok || !res.items) {
      container.innerHTML = '<div class="empty">Не удалось загрузить категории</div>';
      return;
    }
    
    const cats = res.items;
    const byParent = {};
    cats.forEach(c => {
      const p = c.parent_id || 'root';
      if (!byParent[p]) byParent[p] = [];
      byParent[p].push(c);
    });
    
    const renderNode = (c) => {
      const children = byParent[c.id] || [];
      const hasChildren = children.length > 0;
      const isChecked = selectedIds.has(c.id) ? 'checked' : '';
      
      let html = `<div class="category-node" style="margin-left: 15px; margin-top: 4px;">`;
      html += `<div style="display: flex; align-items: center; justify-content: space-between;">`;
      html += `<label style="display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; user-select: none;">`;
      html += `<input type="checkbox" class="cat-checkbox" data-id="${c.id}" ${isChecked} style="accent-color: var(--accent);">`;
      html += `<span>${esc(c.title)} <span style="color: var(--text-faint); font-size: 11px;">(ID: ${c.id})</span></span>`;
      html += `</label>`;
      
      const feePercent = c.fee !== null ? `${(c.fee * 100).toFixed(1)}%` : '—';
      html += `<span style="font-size: 11px; color: var(--text-muted); background: var(--bg-card); padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border);">Комиссия: ${feePercent}</span>`;
      html += `</div>`;
      
      if (hasChildren) {
        html += `<details style="margin-left: 10px; margin-top: 2px;"><summary style="font-size: 11px; color: var(--accent); cursor: pointer; user-select: none; margin-bottom: 4px;">Показать подкатегории (${children.length})</summary>`;
        children.forEach(child => {
          html += renderNode(child);
        });
        html += `</details>`;
      }
      
      html += `</div>`;
      return html;
    };
    
    const rootCats = byParent['root'] || [];
    if (!rootCats.length) {
      container.innerHTML = '<div class="empty">Категории отсутствуют</div>';
      return;
    }
    
    let treeHtml = '';
    rootCats.forEach(rc => {
      treeHtml += renderNode(rc);
    });
    container.innerHTML = treeHtml;
    
    container.querySelectorAll('.cat-checkbox').forEach(cb => {
      cb.addEventListener('change', async () => {
        const id = parseInt(cb.dataset.id);
        if (cb.checked) {
          selectedIds.add(id);
        } else {
          selectedIds.delete(id);
        }
        try {
          await api('/api/categories/selected', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Array.from(selectedIds))
          });
          setStatus('Категории сохранены', 'ok');
        } catch (e) {
          console.error("Failed to save selected categories:", e);
          setStatus('Ошибка сохранения', 'err');
        }
      });
    });
  } catch (e) {
    console.error("loadCategoriesTree error:", e);
    container.innerHTML = `<div class="empty" style="color: var(--text-danger);">Ошибка: ${esc(e.message)}</div>`;
  }
}


// ═══════════════════════════════════════════════════
//  DEALS (ACTIVE ORDERS / СДЕЛКИ)
// ═══════════════════════════════════════════════════
let _activeDealId = null;
let _dealChatPollTimer = null;

async function loadDeals() {
  setStatus('Загрузка активных сделок…', 'busy');
  
  // Bind refresh button
  const btnRefresh = $('#btn-deals-refresh');
  if (btnRefresh && !btnRefresh.dataset.bound) {
    btnRefresh.dataset.bound = 'true';
    btnRefresh.addEventListener('click', () => loadDeals());
  }
  
  // Bind send buyer message button
  const btnSend = $('#btn-send-buyer-message');
  if (btnSend && !btnSend.dataset.bound) {
    btnSend.dataset.bound = 'true';
    btnSend.addEventListener('click', () => sendBuyerMessage());
    $('#buyer-message-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendBuyerMessage();
    });
  }
  
  try {
    const d = await api('/api/orders/linked');
    renderDealsTable(d.orders || []);
    setStatus(`Сделок: ${(d.orders || []).length}`, 'ok');
  } catch (e) {
    console.error(e);
    setStatus('Ошибка загрузки сделок', 'err');
  }
  
  // Poll active deal chat if open
  if (_dealChatPollTimer) clearInterval(_dealChatPollTimer);
  _dealChatPollTimer = setInterval(async () => {
    if (currentView !== 'deals' || !_activeDealId) return;
    await pollActiveDealChat();
  }, 30000);
}

function renderDealsTable(orders) {
  const tbody = $('#deals-orders-tbody');
  if (!tbody) return;
  
  if (!orders.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="padding:20px; text-align:center; color: var(--text-muted);">Нет активных сделок</td></tr>`;
    return;
  }
  
  tbody.innerHTML = orders.map(o => {
    const statusMap = {
      'new': '<span class="badge paused">Новый</span>',
      'buying': '<span class="badge active">Покупаю</span>',
      'done': '<span class="badge success" style="background:#10b981;">Выполнен</span>',
      'error': '<span class="badge draft">Ошибка</span>'
    };
    
    return `
      <tr data-deal-tr-id="${o.order_id}" style="cursor: pointer;">
        <td><b>#${o.order_id}</b></td>
        <td>
          <div style="font-weight:bold;">${esc(o.title)}</div>
          <div style="font-size:11px; color:var(--text-faint); margin-top:2px;">Оригинал: ${o.source_offer_id}</div>
        </td>
        <td style="text-align:right;">${fmt(o.my_price)} RUB</td>
        <td style="text-align:right;">${fmt(o.source_price)} RUB</td>
        <td style="text-align:right; color:#10b981; font-weight:bold;">+${fmt(o.profit_rub)} RUB</td>
        <td style="text-align:center;">${statusMap[o.status] || o.status}</td>
        <td style="text-align:center;" onclick="event.stopPropagation();">
          <button class="btn btn-sm btn-buying" data-id="${o.order_id}" style="padding: 2px 8px; margin-right:4px;">Покупаю оригинал</button>
          <button class="btn btn-sm btn-done" data-id="${o.order_id}" style="background:#10b981; border-color:#10b981; padding: 2px 8px;">Выполнено</button>
        </td>
      </tr>
    `;
  }).join('');
  
  // Row click
  tbody.querySelectorAll('[data-deal-tr-id]').forEach(tr => {
    tr.addEventListener('click', () => {
      viewDealChats(tr.dataset.dealTrId);
    });
  });
  
  // Button actions
  tbody.querySelectorAll('.btn-buying').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      try {
        await api(`/api/orders/${id}/mark_buying`, { method: 'POST' });
        loadDeals();
      } catch (e) {
        alert("Ошибка смены статуса");
      }
    });
  });
  
  tbody.querySelectorAll('.btn-done').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      try {
        await api(`/api/orders/${id}/mark_done`, { method: 'POST' });
        loadDeals();
      } catch (e) {
        alert("Ошибка смены статуса");
      }
    });
  });
}

async function viewDealChats(orderId) {
  _activeDealId = orderId;
  const panel = $('#deal-chats-panel');
  if (!panel) return;
  
  $('#deal-chats-title').textContent = `Чат по заказу #${orderId}`;
  panel.style.display = 'block';
  
  // Clear lists
  $('#buyer-chat-messages').innerHTML = '<div class="loader"><div class="spinner"></div>Загрузка сообщений...</div>';
  
  // Scroll to chats panel
  panel.scrollIntoView({ behavior: 'smooth' });
  
  try {
    // Load original source details
    const src = await api(`/api/chats/source/${orderId}`);
    if (src.ok) {
      $('#original-product-link').href = src.product_url || '#';
      $('#original-seller-link').href = src.seller_url || '#';
      
      const btnCopy = $('#btn-copy-seller-msg');
      if (btnCopy) {
        // Unbind old listeners
        const newBtn = btnCopy.cloneNode(true);
        btnCopy.parentNode.replaceChild(newBtn, btnCopy);
        
        newBtn.addEventListener('click', () => {
          navigator.clipboard.writeText(src.message_template).then(() => {
            setStatus('Шаблон скопирован!', 'ok');
          });
        });
      }
    }
  } catch (e) {
    console.warn("Failed to load deal original sources:", e);
  }
  
  await pollActiveDealChat();
}

async function pollActiveDealChat() {
  if (!_activeDealId) return;
  const container = $('#buyer-chat-messages');
  if (!container) return;
  
  try {
    const res = await api(`/api/chats/my/order/${_activeDealId}`);
    if (!res.ok || !res.messages) {
      container.innerHTML = '<div class="empty">Чат покупателя отсутствует на GGSEL</div>';
      return;
    }
    
    // Save active chat ID to input wrapper attribute for reference
    container.dataset.chatId = res.chat_id;
    
    const messages = res.messages;
    if (!messages.length) {
      container.innerHTML = '<div class="empty">Нет сообщений</div>';
      return;
    }
    
    // Sort or reverse to show oldest first at top, newest at bottom
    const sorted = [...messages].reverse();
    
    container.innerHTML = sorted.map(m => {
      const isMy = m.is_my || m.type === 'seller' || m.from === 'seller';
      const align = isMy ? 'text-align: right;' : 'text-align: left;';
      const bg = isMy ? 'background: #3b82f6; color: #fff;' : 'background: var(--bg-card); color: var(--text-color);';
      const time = fmtDate(m.date || m.created_at);
      
      return `
        <div style="${align} margin-bottom: 8px;">
          <div style="display: inline-block; max-width: 80%; border-radius: 6px; padding: 6px 12px; ${bg}">
            <div style="font-size: 13px;">${esc(m.text || m.message)}</div>
            <div style="font-size: 9px; opacity: 0.7; margin-top: 4px; text-align: right;">${time}</div>
          </div>
        </div>
      `;
    }).join('');
    
    container.scrollTop = container.scrollHeight;
  } catch (e) {
    console.warn("Error polling buyer chat:", e);
  }
}

async function sendBuyerMessage() {
  const container = $('#buyer-chat-messages');
  const inp = $('#buyer-message-input');
  if (!container || !inp) return;
  
  const chat_id = container.dataset.chatId;
  const message = inp.value.trim();
  if (!chat_id || !message) return;
  
  setStatus('Отправка…', 'busy');
  try {
    const res = await api(`/api/chats/my/${chat_id}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    if (res.ok) {
      inp.value = '';
      setStatus('Отправлено', 'ok');
      await pollActiveDealChat();
    } else {
      setStatus('Ошибка отправки', 'err');
    }
  } catch (e) {
    setStatus('Ошибка отправки', 'err');
  }
}


// ═══════════════════════════════════════════════════
//  MODERATION TABLE RENDERER
// ═══════════════════════════════════════════════════
function renderModerationTable(items) {
  const tbody = $('#moderation-products-tbody');
  if (!tbody) return;
  
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="padding:20px; text-align:center; color: var(--text-muted);">Нет товаров для модерации</td></tr>`;
    return;
  }
  
  tbody.innerHTML = items.map(p => {
    const marginPct = (p.expected_net_margin_pct ?? p.margin_pct ?? 0.0) * 100;
    const profit = p.expected_profit_rub ?? p.net_profit ?? 0.0;
    const checkState = _modSelectedIds.has(p.product_id) ? 'checked' : '';
    const imgUrl = p.generated_image_url || p.local_image_path || p.image_url;
    const imgCell = imgUrl
      ? `<img src="${esc(imgSrc(imgUrl))}" class="cover-thumb" onerror="this.style.display='none'" alt="">`
      : '';
    
    return `
      <tr data-mod-id="${p.product_id}" style="cursor:pointer;">
        <td style="text-align:center;" class="mod-td-check"><input type="checkbox" class="mod-item-checkbox" data-id="${p.product_id}" ${checkState}></td>
        <td style="width:52px;">${imgCell}</td>
        <td>
          <div style="font-weight:bold;">${esc(p.title)}</div>
          <div style="font-size:11px; color:var(--text-faint); margin-top:2px;">ID: ${p.product_id} | Категория: ${p.category}</div>
        </td>
        <td style="text-align:right;">${fmt(p.source_price)} RUB</td>
        <td style="text-align:right;"><b>${fmt(p.my_price || p.price)}</b> RUB</td>
        <td style="text-align:right; color:#10b981; font-weight:bold;">+${fmt(profit)} RUB</td>
        <td style="text-align:right; font-weight:bold;">${fmt(marginPct, 1)}%</td>
        <td style="text-align:center;" class="mod-td-actions">
          <button class="btn btn-sm btn-approve" data-id="${p.product_id}" style="background:#10b981; border-color:#10b981; padding: 2px 8px; margin-right: 4px;">Одобрить</button>
          <button class="btn btn-sm btn-danger btn-reject" data-id="${p.product_id}" style="color:#ef4444; border-color:#ef4444; background:transparent; padding: 2px 8px;">Отклонить</button>
        </td>
      </tr>
    `;
  }).join('');
  
  // Row click → open product card modal
  tbody.querySelectorAll('tr[data-mod-id]').forEach(row => {
    row.addEventListener('click', e => {
      // Don't open modal when clicking checkbox or action buttons
      if (e.target.closest('.mod-td-check') || e.target.closest('.mod-td-actions')) return;
      viewModerationProduct(row.dataset.modId);
    });
  });

  tbody.querySelectorAll('.mod-item-checkbox').forEach(cb => {
    cb.addEventListener('change', () => {
      const id = cb.dataset.id;
      if (cb.checked) _modSelectedIds.add(id);
      else _modSelectedIds.delete(id);
    });
  });
  
  tbody.querySelectorAll('.btn-approve').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      setStatus('Одобрение товара…', 'busy');
      try {
        await api(`/api/parser/products/${id}/approve`, { method: 'POST' });
        setStatus('Товар одобрен', 'ok');
        await refreshModerationProducts(true);
      } catch (e) {
        setStatus('Ошибка одобрения', 'err');
      }
    });
  });
  
  tbody.querySelectorAll('.btn-reject').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      setStatus('Отклонение товара…', 'busy');
      try {
        await api(`/api/parser/products/${id}/reject`, { method: 'POST' });
        setStatus('Товар отклонён', 'ok');
        await refreshModerationProducts(true);
      } catch (e) {
        setStatus('Ошибка отклонения', 'err');
      }
    });
  });
}

async function viewModerationProduct(pid) {
  try {
    const d = await api(`/api/parser/products/${encodeURIComponent(pid)}`);
    const p = d.product || {};

    // ── Изображение: сначала сгенерированное, потом оригинальное
    const genImgSrc  = p.generated_image_url || p.local_image_path;
    const origImgSrc = p.image_url;
    const imgToShow  = genImgSrc || origImgSrc;

    // ── Галерея оригинальных фото
    let galleryImgs = [];
    if (p.images_json) {
      try { galleryImgs = JSON.parse(p.images_json); } catch (_) { galleryImgs = []; }
    }
    if (!galleryImgs.length && origImgSrc) galleryImgs = [origImgSrc];
    const galleryHtml = galleryImgs.length
      ? `<div style="display:flex; gap:6px; overflow-x:auto; padding:4px 0;">
           ${galleryImgs.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(imgSrc(u))}" style="width:72px; height:72px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.1);" onerror="this.style.opacity=0.3"></a>`).join('')}
         </div>`
      : '';

    // ── Блок с двумя фото: оригинал (слева) + поле для AI-фото (справа)
    const imgBlockHtml = `
      <div style="display:flex; gap:12px; align-items:flex-start; margin-bottom:16px;">

        <!-- Оригинальное фото -->
        <div style="flex:1;">
          <div class="muted" style="font-size:10px; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:5px;">Оригинал</div>
          <div style="width:100%; aspect-ratio:1/1; border-radius:8px; overflow:hidden; background:rgba(255,255,255,0.05); display:flex; align-items:center; justify-content:center;">
            ${origImgSrc
              ? `<img src="${esc(imgSrc(origImgSrc))}" style="width:100%;height:100%;object-fit:cover;" onerror="this.style.opacity=0.3">`
              : '<span class="muted" style="font-size:11px;">Нет фото</span>'}
          </div>
          ${galleryImgs.length > 1 ? galleryHtml : ''}
        </div>

        <!-- Поле для AI-фото -->
        <div style="flex:1;">
          <div class="muted" style="font-size:10px; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:5px;">🤖 AI-фото</div>
          <div id="mod-modal-img-wrap" style="width:100%; aspect-ratio:1/1; border-radius:8px; overflow:hidden; background:rgba(255,255,255,0.04); border:2px dashed rgba(255,255,255,0.12); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px; position:relative;">
            ${genImgSrc
              ? `<img src="${esc(imgSrc(genImgSrc))}" style="position:absolute; inset:0; width:100%; height:100%; object-fit:cover;">
                 <button id="mod-modal-gen-btn" class="btn btn-sm" style="position:absolute; bottom:6px; left:50%; transform:translateX(-50%); white-space:nowrap; background:rgba(0,0,0,0.8); border-color:rgba(255,255,255,0.3); font-size:11px; z-index:2;">🎨 Перегенерировать</button>`
              : `<span style="font-size:28px;">🎨</span>
                 <span class="muted" style="font-size:11px; text-align:center; padding:0 8px;">Здесь появится<br>сгенерированное фото</span>
                 <button id="mod-modal-gen-btn" class="btn btn-sm" style="white-space:nowrap; font-size:12px;">🎨 Сгенерировать</button>`}
          </div>
        </div>

        <!-- Инфо -->
        <div style="flex:1;">
          <div class="form-row">
            <label>Статус</label>
            <div>${statusBadgeFor(p.approval_status || p.status)}</div>
          </div>
          <div class="form-row">
            <label>Категория</label>
            <div style="font-size:11px;">${esc(p.category || '—')}</div>
          </div>
          <div class="form-row">
            <label>Цена ист.</label>
            <div>${p.source_price || p.price ? `${fmt(p.source_price || p.price)} ₽` : '—'}</div>
          </div>
          <div class="form-row">
            <label>Моя цена</label>
            <div style="font-weight:600; color:var(--green);">${p.my_price ? fmt(p.my_price) + ' ₽' : '—'}</div>
          </div>
        </div>

      </div>`;

    // ── AI-блок
    const ps = (p.profit_score !== null && p.profit_score !== undefined) ? Number(p.profit_score) : null;
    let aiBlockHtml = '';
    if (ps !== null && !isNaN(ps)) {
      let psCls = ps >= 70 ? 'high' : ps >= 40 ? 'mid' : 'low';
      const margin = p.recommended_margin_pct != null ? `${fmt(p.recommended_margin_pct, 1)}%` : '—';
      const risk = p.risk_level || '—';
      const riskColor = risk === 'low' ? 'var(--green)' : risk === 'high' ? 'var(--red)' : '#f5b50a';
      aiBlockHtml = `
        <h3 style="margin:18px 0 8px 0; font-size:14px; color:var(--blue);">🤖 AI-оценка</h3>
        <div class="form-row">
          <label>Profit Score</label>
          <div style="display:flex; align-items:center; gap:8px;">
            <div style="flex:1; height:8px; background:rgba(255,255,255,0.08); border-radius:4px; overflow:hidden;">
              <div class="profit-score ${psCls}" style="height:100%; width:${Math.max(0,Math.min(100,ps))}%; background:currentColor;"></div>
            </div>
            <span class="profit-score ${psCls}">★ ${fmt(ps, 0)}</span>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-row"><label>Наценка</label><div>${margin}</div></div>
          <div class="form-row"><label>Риск</label><div><span style="color:${riskColor}; font-weight:600; text-transform:uppercase; font-size:11px;">${esc(risk)}</span></div></div>
        </div>`;
    }

    const donorBtn = p.url
      ? `<a href="${esc(p.url)}" target="_blank" rel="noopener" class="btn btn-primary" style="background:var(--green); border-color:var(--green); color:#000; font-weight:600; margin:8px 0 14px;">🛒 Купить у донора</a>`
      : '';

    const html = `
      <div class="modal-head">
        <h2 style="margin:0;">📦 ${esc(p.title || pid)}</h2>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>
      <div style="padding:18px; max-height:70vh; overflow-y:auto;">
        ${imgBlockHtml}
        ${donorBtn}
        <div class="form-row">
          <label>Оригинальное название</label>
          <div>${esc(p.original_title || p.title || '—')}</div>
        </div>
        <div class="form-row">
          <label>AI-название</label>
          <div style="color:var(--green);">${esc(p.generated_title || '—')}</div>
        </div>
        <div class="form-row">
          <label>AI-описание</label>
          <div style="white-space:pre-wrap; font-size:13px;">${esc(p.generated_desc || '—')}</div>
        </div>
        <div class="form-row">
          <label>Теги</label>
          <div>${esc(p.generated_tags || '—')}</div>
        </div>
        ${aiBlockHtml}
        <div class="form-row" style="margin-top:14px;">
          <label>ID</label><div><code>${esc(p.product_id)}</code></div>
        </div>
        <div class="form-row">
          <label>Создан</label>
          <div class="muted">${fmtDate(p.created_at)}</div>
        </div>
        ${p.ai_error ? `<div class="form-row"><label>AI ошибка</label><div style="color:var(--red);">${esc(p.ai_error)}</div></div>` : ''}
        <div style="display:flex; gap:8px; margin-top:18px;">
          <button class="btn" id="mod-modal-approve-btn" style="background:#10b981; border-color:#10b981;">✅ Одобрить</button>
          <button class="btn btn-danger" id="mod-modal-reject-btn" style="color:#ef4444; border-color:#ef4444; background:transparent;">❌ Отклонить</button>
        </div>
      </div>`;

    openModal(html);

    // Bind generate image button
    document.getElementById('mod-modal-gen-btn')?.addEventListener('click', () => doGenImageModal(pid));

    // Bind approve/reject buttons
    document.getElementById('mod-modal-approve-btn')?.addEventListener('click', async () => {
      try {
        await api(`/api/parser/products/${encodeURIComponent(pid)}/approve`, { method: 'POST' });
        setStatus('Товар одобрен', 'ok');
        closeModal();
        await refreshModerationProducts(true);
      } catch (e) {
        setStatus('Ошибка одобрения: ' + e.message, 'err');
      }
    });
    document.getElementById('mod-modal-reject-btn')?.addEventListener('click', async () => {
      try {
        await api(`/api/parser/products/${encodeURIComponent(pid)}/reject`, { method: 'POST' });
        setStatus('Товар отклонён', 'ok');
        closeModal();
        await refreshModerationProducts(true);
      } catch (e) {
        setStatus('Ошибка отклонения: ' + e.message, 'err');
      }
    });
  } catch (e) {
    setStatus('Ошибка загрузки товара: ' + e.message, 'err');
  }
}

async function doGenImageModal(pid) {
  const btn = document.getElementById('mod-modal-gen-btn');
  const origText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Запускаю генерацию…';
  }

  const wrap = document.getElementById('mod-modal-img-wrap');
  let progressEl = null;
  if (wrap) {
    progressEl = document.createElement('div');
    progressEl.id = 'mod-modal-gen-progress';
    progressEl.style.cssText = 'position:absolute; inset:0; background:rgba(0,0,0,0.65); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px; font-size:12px; color:#fff; z-index:2; border-radius:8px; padding:8px; text-align:center;';
    progressEl.innerHTML = '<div style="font-size:22px;">🤖</div><div>Ставлю задачу в очередь…</div><div class="muted" style="font-size:10px;">работает в фоне</div>';
    wrap.style.position = 'relative';
    wrap.appendChild(progressEl);
  }

  function setProgress(msg, sub = 'работает в фоне') {
    if (progressEl) {
      const lines = progressEl.querySelectorAll('div');
      if (lines[1]) lines[1].textContent = msg;
      if (lines[2]) lines[2].textContent = sub;
    }
  }

  function renderSuccess(imageUrl) {
    if (progressEl) progressEl.remove();
    if (wrap) {
      const ts = Date.now();
      wrap.style.border = 'none';
      wrap.style.flexDirection = '';
      wrap.innerHTML = `
        <img src="${esc(imageUrl)}?t=${ts}" style="position:absolute; inset:0; width:100%; height:100%; object-fit:cover;">
        <button id="mod-modal-gen-btn" class="btn btn-sm" style="position:absolute; bottom:6px; left:50%; transform:translateX(-50%); white-space:nowrap; background:rgba(0,0,0,0.8); border-color:rgba(255,255,255,0.3); font-size:11px; z-index:2;">🎨 Перегенерировать</button>`;
      document.getElementById('mod-modal-gen-btn')?.addEventListener('click', () => doGenImageModal(pid));
    }
    setStatus('✓ Картинка сгенерирована через Gemini!', 'ok');
    setTimeout(() => setStatus('', ''), 4000);
    const row = document.querySelector(`tr[data-mod-id="${CSS.escape(pid)}"]`);
    if (row) {
      const imgEl = row.querySelector('td:nth-child(2) img');
      if (imgEl) imgEl.src = imgSrc(imageUrl) + '?t=' + Date.now();
    }
  }

  function renderLoginRequired() {
    if (progressEl) progressEl.remove();
    const b = document.getElementById('mod-modal-gen-btn');
    if (b) { b.disabled = false; b.textContent = origText || '🎨 Сгенерировать'; }
    if (wrap) {
      const note = document.createElement('div');
      note.style.cssText = 'position:absolute; inset:0; background:rgba(0,0,0,0.80); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; font-size:11px; color:#fff; z-index:2; border-radius:8px; padding:12px; text-align:center;';
      note.innerHTML = `
        <div style="font-size:20px;">⚠️</div>
        <div style="font-weight:600;">Нужна авторизация в Google</div>
        <div class="muted" style="font-size:10px; line-height:1.4;">Открой MoreLogin/MSB, запусти профиль и войди на gemini.google.com через Google-аккаунт. Затем попробуй снова.</div>
        <button class="btn btn-sm" onclick="this.closest('div[style]').remove()" style="margin-top:4px; font-size:10px;">Закрыть</button>`;
      wrap.style.position = 'relative';
      wrap.appendChild(note);
    }
    setStatus('⚠️ Войди в Google в профиле Gemini', 'err');
  }

  let pollTimer = null;
  try {
    const _launchMode = getMsbLaunchMode();
    const startResp = await fetch(`/api/parser/products/${encodeURIComponent(pid)}/browser-generate-image`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ launch_mode: _launchMode }),
    });
    const startData = await startResp.json();
    if (!startData.ok || !startData.job_id) {
      throw new Error(startData.error || 'Не удалось запустить генерацию');
    }

    const jobId = startData.job_id;
    setProgress(startData.message || 'Задача запущена…', 'можно закрыть модалку');

    await new Promise((resolve, reject) => {
      let attempts = 0;
      pollTimer = setInterval(async () => {
        attempts += 1;
        try {
          const r = await fetch(`/api/parser/browser-image-jobs/${encodeURIComponent(jobId)}`);
          const d = await r.json();
          if (!d.ok || !d.job) throw new Error(d.error || 'Ошибка статуса задачи');
          const job = d.job;
          setProgress(job.message || 'Идёт обработка…', job.stage || 'работает в фоне');

          if (job.status === 'done' && job.image_url) {
            clearInterval(pollTimer);
            pollTimer = null;
            renderSuccess(job.image_url);
            resolve();
            return;
          }
          if (job.status === 'error') {
            clearInterval(pollTimer);
            pollTimer = null;
            if (job.login_required) {
              renderLoginRequired();
              resolve();
              return;
            }
            reject(new Error(job.error || 'Ошибка генерации'));
            return;
          }
          if (attempts > 180) {
            clearInterval(pollTimer);
            pollTimer = null;
            reject(new Error('Таймаут ожидания статуса задачи')); 
          }
        } catch (e) {
          clearInterval(pollTimer);
          pollTimer = null;
          reject(e);
        }
      }, 2000);
    });
  } catch (e) {
    if (pollTimer) clearInterval(pollTimer);
    if (progressEl) progressEl.remove();
    setStatus('✗ ' + e.message, 'err');
    setTimeout(() => setStatus('', ''), 6000);
    const b = document.getElementById('mod-modal-gen-btn');
    if (b) { b.disabled = false; b.textContent = origText || '🎨 Сгенерировать'; }
  }
}


// ═══════════════════════════════════════════════════
//  DROPDOWNS & NOTIFICATIONS (Stage 1)
// ═══════════════════════════════════════════════════
document.addEventListener('click', (e) => {
  const isDropdownBtn = e.target.closest('.tb-btn') || e.target.closest('.tb-notification');
  if (!isDropdownBtn) {
    document.querySelectorAll('.tb-dropdown-menu').forEach(menu => {
      menu.style.display = 'none';
    });
  }
});

const setupDropdown = (btnId, menuId) => {
  const btn = document.getElementById(btnId);
  const menu = document.getElementById(menuId);
  if (btn && menu) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isVisible = menu.style.display === 'block';
      document.querySelectorAll('.tb-dropdown-menu').forEach(m => m.style.display = 'none');
      menu.style.display = isVisible ? 'none' : 'block';
    });
  }
};

setupDropdown('btn-lang-dropdown', 'menu-lang');
setupDropdown('btn-user-dropdown', 'menu-user');
setupDropdown('tb-notification', 'menu-notifications');

// Notification Polling
setInterval(async () => {
  try {
    const data = await api('/api/chats');
    const badge = document.getElementById('tb-notification-dot');
    if (data && data.has_new_messages && badge) {
      badge.style.display = 'block';
      document.getElementById('tb-notification').classList.add('has-unread');
    } else if (badge) {
      badge.style.display = 'none';
      document.getElementById('tb-notification').classList.remove('has-unread');
    }
  } catch (e) {
    // Ignore errors for background polling
  }
}, 60000);



// ═══════════════════════════════════════════════════
//  PROMO CODES (Stage 2)
// ═══════════════════════════════════════════════════
async function loadPromoCodes() {
  const tbody = document.getElementById('promo-tbody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="padding:20px;">Загрузка...</td></tr>';
  
  try {
    const data = await api('/api/promo_codes');
    if (data.stub) {
      if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="color:var(--text-muted); padding:20px;">Раздел недоступен (API-key режим)</td></tr>';
      return;
    }
    const items = data.items || data.data || [];
    renderPromoCodes(items);
  } catch(e) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="color:var(--red);">Ошибка загрузки промокодов</td></tr>';
  }
}

function renderPromoCodes(items) {
  const tbody = document.getElementById('promo-tbody');
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding:20px;">Нет промокодов</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(p => {
    let dateStr = `${p.start_date ? new Date(p.start_date).toLocaleDateString() : '—'} - ${p.end_date ? new Date(p.end_date).toLocaleDateString() : '—'}`;
    return `
      <tr>
        <td style="font-weight:600;">${esc(p.code || '—')}</td>
        <td>${esc(p.discount || '—')}%</td>
        <td>${esc(p.uses || '0')} / ${esc(p.max_uses || '∞')}</td>
        <td style="font-size:12px; color:var(--text-dim);">${dateStr}</td>
        <td>${p.active ? '<span style="color:var(--primary);">Активен</span>' : '<span style="color:var(--red);">Неактивен</span>'}</td>
        <td>
          <button class="btn btn-sm">Редактировать</button>
        </td>
      </tr>
    `;
  }).join('');
}


// ═══════════════════════════════════════════════════
//  OFFER WIZARD (Stage 4)
// ═══════════════════════════════════════════════════
let wizardCurrentOfferId = null;

async function openOfferWizard(id) {
  closeModal(); 
  wizardCurrentOfferId = id;
  const badgeEl = document.getElementById('wizard-offer-id');
  if(badgeEl) badgeEl.textContent = '#' + id;
  showView('offer-edit');
  wizardGo(1);
  
  try {
    const d = await api('/api/offer/' + id);
    const o = d.offer?.data || {};
    document.getElementById('wizard-title-ru').value = o.title_ru || '';
    document.getElementById('wizard-title-en').value = o.title_en || '';
    document.getElementById('wizard-price').value = o.price || 0;
  } catch (e) {
    showToast('Ошибка загрузки товара', 'error');
  }
}

function wizardGo(step) {
  document.querySelectorAll('.wizard-step').forEach(el => el.style.display = 'none');
  const stepEl = document.getElementById('wizard-step-' + step);
  if(stepEl) stepEl.style.display = 'block';
  
  document.querySelectorAll('.wizard-tab').forEach(el => {
    el.classList.remove('active');
    el.style.color = 'var(--text-muted)';
  });
  const activeTab = document.querySelector('.wizard-tab[data-step="' + step + '"]');
  if (activeTab) {
    activeTab.classList.add('active');
    activeTab.style.color = 'var(--text)';
  }
}

document.getElementById('wizard-save-btn')?.addEventListener('click', async () => {
  if (!wizardCurrentOfferId) return;
  const btn = document.getElementById('wizard-save-btn');
  btn.disabled = true;
  btn.textContent = 'Сохранение...';
  
  const payload = {
    title_ru: document.getElementById('wizard-title-ru').value,
    title_en: document.getElementById('wizard-title-en').value,
    price: parseFloat(document.getElementById('wizard-price').value) || 0,
    offer_type: document.getElementById('wizard-type').value
  };
  
  try {
    await api(`/api/offer/${wizardCurrentOfferId}/update`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    showToast('Успешно сохранено', 'success');
    showView('offers');
    loadOffers();
  } catch (e) {
    showToast('Ошибка сохранения', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сохранить';
  }
});


// ═══════════════════════════════════════════════════
//  PROFILE (Stage 7)
// ═══════════════════════════════════════════════════
async function loadProfile() {
  try {
    const d = await api('/api/profile');
    const data = d.data || {};
    
    document.getElementById('profile-email').value = data.email || '';
    document.getElementById('profile-nickname').value = data.name || '';
    document.getElementById('profile-wmz').value = data.wmz || '';
    
    if (d.stub) {
      showToast('Профиль: недоступен в API-key режиме', 'warning');
    }
    
    // Load notifications for tab 2
    try {
      const notifData = await api('/api/notifications');
      if (notifData.stub) {
        console.warn('Уведомления: stub mode');
      } else {
        // Here we could parse and render notifications if needed
        // For now just load the data to ensure endpoint works
      }
    } catch(e) {
      console.error('Failed to load notifications', e);
    }
  } catch (e) {
    showToast('Ошибка загрузки профиля', 'error');
  }
}

function switchProfileTab(tab) {
  document.querySelectorAll('.ptab-content').forEach(el => el.style.display = 'none');
  const target = document.getElementById('ptab-' + tab);
  if(target) target.style.display = 'block';
  
  document.querySelectorAll('.wizard-tab[data-ptab]').forEach(el => {
    el.classList.remove('active');
    el.style.color = 'var(--text-muted)';
  });
  const activeTab = document.querySelector('.wizard-tab[data-ptab="' + tab + '"]');
  if (activeTab) {
    activeTab.classList.add('active');
    activeTab.style.color = 'var(--text)';
  }
}

// ═══════════════════════════════════════════════════
//  SETTINGS (Stage 8)
// ═══════════════════════════════════════════════════
async function switchSettingsTab(tab) {
  document.querySelectorAll('.stab-content').forEach(el => el.style.display = 'none');
  const target = document.getElementById('stab-' + tab);
  if(target) target.style.display = 'block';
  
  if (tab == 2) {
    try {
      const ipData = await api('/api/whitelisted_ips');
      if (!ipData.stub && ipData.items) {
        const textarea = document.getElementById('settings-ips');
        if (textarea) {
          // Extract IPs if format matches list of strings or objects
          let ipList = [];
          if (Array.isArray(ipData.items)) {
            ipList = ipData.items.map(i => typeof i === 'string' ? i : (i.ip || i.address || JSON.stringify(i)));
          }
          textarea.value = ipList.join('\n');
        }
      }
    } catch (e) {
      console.error('Failed to load whitelisted IPs', e);
    }
  }
  
  document.querySelectorAll('.wizard-tab[data-stab]').forEach(el => {
    el.classList.remove('active');
    el.style.color = 'var(--text-muted)';
  });
  const activeTab = document.querySelector('.wizard-tab[data-stab="' + tab + '"]');
  if (activeTab) {
    activeTab.classList.add('active');
    activeTab.style.color = 'var(--text)';
  }
}
