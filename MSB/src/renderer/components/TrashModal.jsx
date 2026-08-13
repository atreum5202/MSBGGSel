import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../api.js';

/**
 * Recycle bin modal. Shows profiles soft-deleted in the last 7 days with
 * restore / purge actions, plus a manual "purge expired" sweep button.
 *
 * MoreLogin parity: same 7-day retention, same per-row actions.
 */
export default function TrashModal({ open, onClose, onRestored }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.trash.list();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  if (!open) return null;

  const handleRestore = async (id) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.trash.restore(id);
      await load();
      onRestored?.();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const handlePurge = async (id) => {
    if (busy) return;
    if (!confirm('Удалить профиль навсегда? Это действие необратимо.')) return;
    setBusy(true);
    setError(null);
    try {
      await api.trash.purge(id);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleSweep = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.trash.sweep();
      await load();
      if (r && r.purged > 0) {
        // Surface as transient info; nothing more to do.
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          width: 'min(720px, 92vw)',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
        }}
      >
        <header
          style={{
            padding: '14px 18px',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span style={{ fontSize: 18 }}>🗑️</span>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>Корзина</h2>
          <span
            style={{
              fontSize: 11,
              color: 'var(--text-dim)',
              background: 'var(--bg-2)',
              padding: '1px 7px',
              borderRadius: 10,
            }}
          >
            {items.length}
          </span>
          <span
            style={{
              marginLeft: 8,
              fontSize: 11,
              color: 'var(--text-faint)',
            }}
          >
            хранится 7 дней
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            <button
              onClick={handleSweep}
              disabled={busy || items.length === 0}
              style={{
                padding: '5px 10px',
                fontSize: 11,
                background: 'var(--bg-3)',
                color: 'var(--text-dim)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                cursor: busy ? 'default' : 'pointer',
                opacity: busy || items.length === 0 ? 0.5 : 1,
              }}
              title="Удалить из корзины всё, что старше 7 дней"
            >
              🧹 Sweep expired
            </button>
            <button
              onClick={load}
              disabled={busy}
              style={{
                padding: '5px 10px',
                fontSize: 11,
                background: 'var(--bg-3)',
                color: 'var(--text-dim)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                cursor: busy ? 'default' : 'pointer',
              }}
              title="Обновить"
            >
              ↻
            </button>
            <button
              onClick={onClose}
              style={{
                padding: '5px 10px',
                fontSize: 13,
                background: 'transparent',
                color: 'var(--text-dim)',
                border: 'none',
                cursor: 'pointer',
              }}
              title="Закрыть"
            >
              ✕
            </button>
          </div>
        </header>

        {error && (
          <div
            style={{
              padding: '8px 16px',
              background: 'rgba(239,68,68,0.12)',
              color: '#fca5a5',
              fontSize: 12,
              borderBottom: '1px solid var(--border)',
            }}
          >
            ⚠ {error}
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {items.length === 0 && !error && (
            <div
              style={{
                padding: 36,
                textAlign: 'center',
                color: 'var(--text-dim)',
                fontSize: 12,
              }}
            >
              Корзина пуста. Удалите профиль — он попадёт сюда и его можно будет восстановить в течение 7 дней.
            </div>
          )}

          {items.map((it) => {
            const deletedAgo = it.deletedAt ? humanizeAgo(Date.now() - it.deletedAt) : '—';
            const daysLeft = it.daysLeft;
            const urgency = daysLeft != null && daysLeft <= 2;
            return (
              <div
                key={it.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 18px',
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: 'var(--text)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={it.name || it.id}
                  >
                    {it.name || <em style={{ color: 'var(--text-faint)' }}>без имени</em>}
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: 'var(--text-faint)',
                      marginTop: 2,
                      display: 'flex',
                      gap: 8,
                    }}
                  >
                    <span>#{it.number ?? '?'}</span>
                    <span>·</span>
                    <span>{it.id}</span>
                    <span>·</span>
                    <span>удалён {deletedAgo} назад</span>
                    {daysLeft != null && (
                      <>
                        <span>·</span>
                        <span style={{ color: urgency ? '#f87171' : 'var(--text-faint)', fontWeight: urgency ? 600 : 400 }}>
                          {daysLeft === 0 ? 'истекает сегодня' : `осталось ${daysLeft} дн.`}
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleRestore(it.id)}
                  disabled={busy}
                  style={{
                    padding: '4px 10px',
                    fontSize: 11,
                    background: 'var(--accent)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 4,
                    cursor: busy ? 'default' : 'pointer',
                    opacity: busy ? 0.5 : 1,
                  }}
                >
                  ↩ Восстановить
                </button>
                <button
                  onClick={() => handlePurge(it.id)}
                  disabled={busy}
                  style={{
                    padding: '4px 10px',
                    fontSize: 11,
                    background: 'transparent',
                    color: '#f87171',
                    border: '1px solid #7f1d1d',
                    borderRadius: 4,
                    cursor: busy ? 'default' : 'pointer',
                    opacity: busy ? 0.5 : 1,
                  }}
                  title="Удалить навсегда (необратимо)"
                >
                  Удалить
                </button>
              </div>
            );
          })}
        </div>

        <footer
          style={{
            padding: '10px 18px',
            borderTop: '1px solid var(--border)',
            fontSize: 11,
            color: 'var(--text-faint)',
            display: 'flex',
            justifyContent: 'space-between',
          }}
        >
          <span>Хранилище: <code>profiles/.trash/</code></span>
          <span>Автоочистка каждые 6 часов</span>
        </footer>
      </div>
    </div>
  );
}

function humanizeAgo(ms) {
  if (ms < 60_000) return 'меньше минуты';
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} дн`;
  const weeks = Math.floor(days / 7);
  return `${weeks} нед`;
}
