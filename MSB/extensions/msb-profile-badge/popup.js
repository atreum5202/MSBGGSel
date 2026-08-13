// MSB Profile Badge — popup
// Показывает полную инфу о профиле, который привязан к этому окну браузера.

const $ = (id) => document.getElementById(id);

async function load() {
  try {
    const res = await chrome.runtime.sendMessage({ type: 'msb:getContext' });
    const ctx = res?.ctx || {};
    paint(ctx);
  } catch (e) {
    paint({});
  }
}

function paint(ctx) {
  const num = ctx.number || ctx.id || '?';
  const email = ctx.email || '—';
  const name = ctx.name && ctx.name !== ctx.email ? ctx.name : '';

  $('badge').textContent = String(num);
  $('title').textContent = name ? `${name}` : `Profile #${num}`;
  $('sub').textContent = email;

  $('kv-id').textContent = ctx.id || '—';
  $('kv-email').textContent = ctx.email || '—';
  $('kv-group').textContent = ctx.group || '—';
  $('kv-country').textContent = ctx.country || '—';
  $('kv-started').textContent = formatTime(ctx.startedAt);
}

function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(Number(ts));
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString();
  } catch {
    return '—';
  }
}

$('refresh').addEventListener('click', async () => {
  try {
    const res = await chrome.runtime.sendMessage({ type: 'msb:reloadContext' });
    paint(res?.ctx || {});
  } catch {}
});

load();
