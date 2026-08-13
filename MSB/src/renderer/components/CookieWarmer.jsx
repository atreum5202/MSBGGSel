import React, { useState, useRef, useEffect } from 'react';
import { api } from '../api.js';

// Qrator-куки которые нам нужны
const QRATOR_KEYS = new Set(['cf_clearance', '__ddg1_', '__ddg2_', 'qrator_jsid', 'qrator_ssid', '_ym_uid']);
const TARGET_URL = 'https://ggsel.net';
const WARM_DELAY_MS = 4000;   // пауза между профилями
const NAVIGATE_WAIT_MS = 8000; // сколько ждём после навигации

// Проверка куков профиля
async function checkCookies(id) {
  try {
    const raw = await fetch(`/profiles/${encodeURIComponent(id)}/cookies?format=json`).then(r => r.json());
    const inner = raw?.data ?? raw;
    const all = Array.isArray(inner?.data) ? inner.data : Array.isArray(inner) ? inner : [];
    const ggsel = all.filter(c => c.domain?.includes('ggsel'));
    const keys = ggsel.map(c => c.name).filter(Boolean);
    const hasQrator = keys.some(k => QRATOR_KEYS.has(k));
    return { total: all.length, ggsel: ggsel.length, keys, hasQrator };
  } catch {
    return { total: 0, ggsel: 0, keys: [], hasQrator: false };
  }
}

// Навигация через MSB API (navigate endpoint если есть, иначе runScenario)
async function navigateProfile(id) {
  // Пробуем navigate
  try {
    const r = await fetch(`/profiles/${encodeURIComponent(id)}/navigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: TARGET_URL }),
    });
    if (r.ok) return { ok: true, method: 'navigate' };
  } catch {}

  // Пробуем runScenario ggsel-warmup
  try {
    const r = await fetch(`/profiles/${encodeURIComponent(id)}/runScenario`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: 'ggsel-warmup', params: { timeoutMs: 30000 } }),
    });
    if (r.ok) return { ok: true, method: 'scenario' };
  } catch {}

  // Fallback: просто ждём — профиль открыт, пользователь видит startUrl
  return { ok: true, method: 'manual' };
}

function StatusBadge({ status }) {
  const map = {
    waiting:  { bg: '#374151', text: '#9ca3af', label: 'Ожидает' },
    running:  { bg: '#1e3a5f', text: '#60a5fa', label: 'Открываем...' },
    navigate: { bg: '#1a3347', text: '#38bdf8', label: 'Загружаем...' },
    checking: { bg: '#1a3347', text: '#a78bfa', label: 'Проверяем...' },
    ok:       { bg: '#14532d', text: '#4ade80', label: '✓ Прогрет' },
    partial:  { bg: '#422006', text: '#fb923c', label: '~ Частично' },
    empty:    { bg: '#450a0a', text: '#f87171', label: '✗ Нет куков' },
    error:    { bg: '#3f1010', text: '#f87171', label: '✗ Ошибка' },
    skipped:  { bg: '#292524', text: '#78716c', label: '— Пропущен' },
  };
  const s = map[status] || map.waiting;
  return (
    <span style={{
      display: 'inline-block',
      background: s.bg,
      color: s.text,
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 7px',
      minWidth: 80,
      textAlign: 'center',
    }}>{s.label}</span>
  );
}

export default function CookieWarmer({ profiles, running, onClose, onRunningChange, stopWarmerRef }) {
  const [rows, setRows] = useState(() =>
    profiles.map(p => ({
      id: p.id,
      name: p.name,
      status: 'waiting',
      ggsel: 0,
      keys: [],
      hasQrator: false,
      error: '',
      started: false, // открыт нами
    }))
  );
  const [isRunning, setIsRunning] = useState(false);
  const [current, setCurrent] = useState(-1);
  const [done, setDone] = useState(false);
  const [skipRunning, setSkipRunning] = useState(true);
  const stopRef = useRef(false);
  const logRef = useRef(null);

  const runIds = new Set(running.map(r => r.id));

  const updateRow = (id, patch) => {
    setRows(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r));
  };

  const warmOne = async (row, idx) => {
    if (stopRef.current) {
      updateRow(row.id, { status: 'skipped' });
      return;
    }

    const alreadyRunning = runIds.has(row.id);

    // Пропускаем профили открытые в UI если опция включена
    if (skipRunning && alreadyRunning) {
      updateRow(row.id, { status: 'skipped', error: 'открыт в UI' });
      return;
    }

    setCurrent(idx);

    // 1. Проверяем текущие куки — может уже прогрет
    const before = await checkCookies(row.id);
    if (before.hasQrator) {
      updateRow(row.id, { status: 'ok', ...before });
      return;
    }

    // 2. Запускаем профиль если не запущен
    updateRow(row.id, { status: 'running' });
    let startedByUs = false;
    if (!alreadyRunning) {
      try {
        await api.browser.start(row.id, { headless: false });
        startedByUs = true;
        await new Promise(r => setTimeout(r, 2500)); // ждём загрузку браузера
      } catch (e) {
        updateRow(row.id, { status: 'error', error: `start: ${e.message}` });
        return;
      }
    }

    // 3. Навигация на ggsel.net
    updateRow(row.id, { status: 'navigate' });
    const navResult = await navigateProfile(row.id);
    const waitMs = navResult.method === 'manual' ? NAVIGATE_WAIT_MS * 1.5 : NAVIGATE_WAIT_MS;
    await new Promise(r => setTimeout(r, waitMs));

    // 4. Проверяем куки после навигации
    updateRow(row.id, { status: 'checking' });
    const after = await checkCookies(row.id);

    // 5. Если нет Qrator-куков — ждём ещё немного (Qrator JS может быть медленным)
    let final = after;
    if (!after.hasQrator && after.ggsel > 0) {
      await new Promise(r => setTimeout(r, 4000));
      final = await checkCookies(row.id);
    }

    // 6. Закрываем браузер если мы его открыли
    if (startedByUs) {
      try { await api.browser.stop(row.id); } catch {}
    }

    // 7. Результат
    if (final.hasQrator) {
      updateRow(row.id, { status: 'ok', ...final });
    } else if (final.ggsel > 0) {
      updateRow(row.id, { status: 'partial', ...final });
    } else {
      updateRow(row.id, { status: 'empty', ...final });
    }
  };

  const startWarming = async () => {
    stopRef.current = false;
    setIsRunning(true);
    onRunningChange?.(true);
    setDone(false);

    for (let i = 0; i < rows.length; i++) {
      if (stopRef.current) break;
      await warmOne(rows[i], i);
      if (i < rows.length - 1 && !stopRef.current) {
        await new Promise(r => setTimeout(r, WARM_DELAY_MS));
      }
    }

    setCurrent(-1);
    setIsRunning(false);
    onRunningChange?.(false);
    setDone(true);
  };

  const stopWarming = () => {
    stopRef.current = true;
  };
  // Регистрируем функцию остановки в ref родителя
  React.useEffect(() => {
    if (stopWarmerRef) stopWarmerRef.current = stopWarming;
    return () => { if (stopWarmerRef) stopWarmerRef.current = null; };
  });


  // Прокрутка к текущему профилю
  useEffect(() => {
    if (current >= 0 && logRef.current) {
      const el = logRef.current.children[current];
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [current]);

  const summary = {
    ok:      rows.filter(r => r.status === 'ok').length,
    partial: rows.filter(r => r.status === 'partial').length,
    empty:   rows.filter(r => r.status === 'empty').length,
    error:   rows.filter(r => r.status === 'error').length,
    skipped: rows.filter(r => r.status === 'skipped').length,
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 10000,
      background: 'rgba(0,0,0,0.75)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: 'var(--bg, #1a1d24)',
        border: '1px solid var(--border, #333)',
        borderRadius: 10,
        width: 620,
        maxWidth: '95vw',
        maxHeight: '90vh',
        display: 'flex',
        flexDirection: 'column',
        boxShadow: '0 20px 60px rgba(0,0,0,0.7)',
        overflow: 'hidden',
      }}>
        {/* Заголовок */}
        <div style={{
          padding: '14px 18px',
          borderBottom: '1px solid var(--border, #333)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 20 }}>🍪</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Cookie Warmer</div>
            <div style={{ fontSize: 11, color: 'var(--text-dim, #888)', marginTop: 2 }}>
              Открывает каждый профиль на ggsel.net и получает Qrator-куки
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isRunning}
            style={{
              background: 'none', border: 'none', cursor: isRunning ? 'default' : 'pointer',
              fontSize: 18, color: 'var(--text-dim, #888)', opacity: isRunning ? 0.3 : 1,
              padding: '2px 6px',
            }}
          >✕</button>
        </div>

        {/* Настройки */}
        {!isRunning && !done && (
          <div style={{
            padding: '12px 18px',
            borderBottom: '1px solid var(--border, #333)',
            background: 'var(--bg2, #1e2128)',
            display: 'flex', alignItems: 'center', gap: 12, fontSize: 12,
          }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={skipRunning}
                onChange={e => setSkipRunning(e.target.checked)}
              />
              Пропускать профили открытые в UI
            </label>
            <span style={{ color: 'var(--text-dim, #888)', marginLeft: 'auto' }}>
              Профилей: {rows.length}
            </span>
          </div>
        )}

        {/* Сводка (после завершения) */}
        {done && (
          <div style={{
            padding: '10px 18px',
            borderBottom: '1px solid var(--border, #333)',
            background: 'var(--bg2, #1e2128)',
            display: 'flex', gap: 14, fontSize: 12, flexWrap: 'wrap',
          }}>
            <span style={{ color: '#4ade80' }}>✓ Прогрето: {summary.ok}</span>
            <span style={{ color: '#fb923c' }}>~ Частично: {summary.partial}</span>
            <span style={{ color: '#f87171' }}>✗ Без куков: {summary.empty + summary.error}</span>
            <span style={{ color: '#78716c' }}>— Пропущено: {summary.skipped}</span>
          </div>
        )}

        {/* Список профилей */}
        <div
          ref={logRef}
          style={{
            flex: 1, overflowY: 'auto',
            padding: '8px 0',
          }}
        >
          {rows.map((row, idx) => (
            <div
              key={row.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '7px 18px',
                background: idx === current ? 'var(--bg2, #1e2128)' : 'transparent',
                borderLeft: idx === current ? '2px solid #60a5fa' : '2px solid transparent',
                transition: 'background 0.15s',
              }}
            >
              <span style={{
                fontSize: 10, color: 'var(--text-dim, #666)',
                minWidth: 24, textAlign: 'right', flexShrink: 0,
              }}>#{idx + 1}</span>
              <span style={{
                flex: 1, fontSize: 12, overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{row.name}</span>
              {row.ggsel > 0 && (
                <span style={{ fontSize: 10, color: 'var(--text-dim, #666)', flexShrink: 0 }}>
                  {row.ggsel}🍪
                </span>
              )}
              {row.error && (
                <span style={{ fontSize: 10, color: '#f87171', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {row.error}
                </span>
              )}
              <StatusBadge status={row.status} />
            </div>
          ))}
        </div>

        {/* Кнопки */}
        <div style={{
          padding: '12px 18px',
          borderTop: '1px solid var(--border, #333)',
          display: 'flex', gap: 10,
        }}>
          {!isRunning && !done && (
            <button
              className="primary"
              style={{ flex: 1, padding: '9px 0', fontSize: 13, fontWeight: 700 }}
              onClick={startWarming}
            >
              🚀 Начать прогрев ({rows.length} профилей)
            </button>
          )}
          {isRunning && (
            <>
              <div style={{
                flex: 1, display: 'flex', alignItems: 'center', gap: 8,
                fontSize: 12, color: 'var(--text-dim, #888)',
              }}>
                <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>⏳</span>
                Прогреваем профиль {current + 1} из {rows.length}...
              </div>
              <button
                onClick={stopWarming}
                style={{ padding: '9px 18px', fontSize: 12 }}
              >
                ⏹ Стоп
              </button>
            </>
          )}
          {done && (
            <>
              <button
                className="primary"
                style={{ flex: 1, padding: '9px 0', fontSize: 13, fontWeight: 700 }}
                onClick={() => {
                  setRows(prev => prev.map(r => ({ ...r, status: 'waiting', ggsel: 0, keys: [], hasQrator: false, error: '' })));
                  setDone(false);
                  setCurrent(-1);
                }}
              >
                🔄 Повторить
              </button>
              <button
                onClick={onClose}
                style={{ padding: '9px 18px', fontSize: 13 }}
              >
                Закрыть
              </button>
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
