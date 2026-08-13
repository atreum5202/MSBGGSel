import React, { useState, useMemo, useRef } from 'react';

const ENGINE_ICON = {
  patchright: { className: 'chrome', glyph: 'C' },
  cloakbrowser: { className: 'edge', glyph: 'E' },
  auto: { className: 'chrome', glyph: 'A' },
};

function EngineIcon({ engine }) {
  const e = ENGINE_ICON[engine] || ENGINE_ICON.auto;
  return <span className={`msb-engine-icon ${e.className}`} title={engine}>{e.glyph}</span>;
}

function ProxyChip({ proxy, onClick }) {
  if (!proxy) {
    return <span onClick={onClick} style={{ color: 'var(--text-faint)', fontStyle: 'italic', cursor: onClick ? 'pointer' : 'default' }} title="Click to add proxy">—</span>;
  }
  const label = `${proxy.protocol}://${proxy.host}:${proxy.port}`;
  return (
    <span onClick={onClick} title={label + " (Click to edit)"} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: onClick ? 'pointer' : 'default' }}>
      <span className={`msb-proxy-chip ${proxy.protocol}`}>{proxy.protocol}</span>
      <span style={{ color: 'var(--text-dim)', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
        {proxy.host}:{proxy.port}
      </span>
    </span>
  );
}

function AccountCell({ profile }) {
  const acc = profile.account || {};
  if (!acc.email) return <span style={{ color: 'var(--text-faint)' }}>—</span>;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
        {acc.email}
      </span>
      {acc.password && <span title="Есть пароль" style={{ fontSize: 10, color: 'var(--ok)' }}>🔑</span>}
    </div>
  );
}

function TagsCell({ tags }) {
  if (!tags || !tags.length) return <span style={{ color: 'var(--text-faint)' }}>—</span>;
  return (
    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
      {tags.slice(0, 3).map((t) => (
        <span key={t} className={`msb-tag ${String(t).toLowerCase()}`}>{t}</span>
      ))}
      {tags.length > 3 && <span className="msb-tag">+{tags.length - 3}</span>}
    </div>
  );
}

export default function ProfileTable({
  profiles, running, groupFilter, search, sortBy, sortDir,
  onToggleSort, onSelectAll, onSelectOne, checkedIds, allChecked,
  onStart, onStop, onMenu, onEditProxy, busyIds, onOpenDetail, pageSize = 100, page = 1,
}) {
  const [contextMenu, setContextMenu] = useState(null);
  const menuRef = useRef(null);

  React.useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    document.addEventListener('click', close);
    document.addEventListener('contextmenu', close);
    return () => {
      document.removeEventListener('click', close);
      document.removeEventListener('contextmenu', close);
    };
  }, [contextMenu]);

  const runIds = useMemo(() => new Set(running.map((r) => r.id)), [running]);

  if (!profiles || profiles.length === 0) {
    return (
      <div className="msb-empty">
        <h3>Нет профилей</h3>
        <p>Создай первый профиль кнопкой «+ Создать профиль» выше</p>
      </div>
    );
  }

  const SortHeader = ({ id, children, align = 'left' }) => {
    const isSorted = sortBy === id;
    return (
      <th
        className={`sortable${isSorted ? ` sorted-${sortDir}` : ''}`}
        onClick={() => onToggleSort(id)}
        style={{ textAlign: align }}
      >
        {children}
        <span className="sort-arrow">{isSorted ? (sortDir === 'asc' ? '▲' : '▼') : '⇅'}</span>
      </th>
    );
  };

  return (
    <div className="msb-table-wrap" onClick={() => setContextMenu(null)}>
      <table className="msb-table">
        <thead>
          <tr>
            <th style={{ width: 32 }}>
              <input
                type="checkbox"
                className="msb-row-check"
                checked={allChecked}
                onChange={(e) => onSelectAll(e.target.checked)}
              />
            </th>
            <SortHeader id="number">No.</SortHeader>
            <SortHeader id="name">Профили</SortHeader>
            <SortHeader id="account">Аккаунт</SortHeader>
            <SortHeader id="proxy">Прокси</SortHeader>
            <SortHeader id="notes">Примечание</SortHeader>
            <SortHeader id="tags">Метка</SortHeader>
            <th style={{ width: 110 }} />
          </tr>
        </thead>
        <tbody>
          {profiles.map((p) => {
            const isRunning = runIds.has(p.id);
            const isBusy = busyIds.has(p.id);
            const isChecked = checkedIds.has(p.id);
            return (
              <tr
                key={p.id}
                className={[
                  p.flagged ? 'flagged' : '',
                  isRunning ? 'running' : '',
                ].filter(Boolean).join(' ')}
                onDoubleClick={() => onOpenDetail?.(p.id)}
              >
                <td>
                  <input
                    type="checkbox"
                    className="msb-row-check"
                    checked={isChecked}
                    onChange={(e) => onSelectOne(p.id, e.target.checked)}
                    onClick={(e) => e.stopPropagation()}
                  />
                </td>
                <td><span className="msb-row-number">{p.number ?? '?'}</span></td>
                <td>
                  <div className="msb-row-name">
                    <EngineIcon engine={p.engine} />
                    <span
                      style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220, cursor: 'pointer' }}
                      onClick={() => onOpenDetail?.(p.id)}
                      title={p.name}
                    >
                      {p.name}
                    </span>
                    {isRunning && <span className="msb-status-dot running" title="Запущен" />}
                    {p.flagged && <span title="Помечен" style={{ color: 'var(--warn)', fontSize: 12 }}>🚩</span>}
                  </div>
                </td>
                <td><AccountCell profile={p} /></td>
                <td><ProxyChip proxy={p.proxy} onClick={(e) => { e.stopPropagation(); onEditProxy?.(p.id, p.proxy); }} /></td>
                <td className="muted" style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.notes || '—'}
                </td>
                <td><TagsCell tags={p.tags} /></td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>
                    <button
                      className={`msb-start-btn${isRunning ? ' stop' : ''}`}
                      disabled={isBusy}
                      onClick={() => isRunning ? onStop(p.id) : onStart(p.id)}
                    >
                      {isRunning ? <>■ Стоп</> : <>▶ Запуск</>}
                    </button>
                    <button
                      className="msb-row-menu"
                      onClick={(e) => {
                        e.stopPropagation();
                        setContextMenu({ x: e.clientX, y: e.clientY, profile: p });
                      }}
                      title="Меню"
                    >
                      ⋮
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {contextMenu && (
        <div
          ref={menuRef}
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 9999,
            background: 'var(--bg-2)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            boxShadow: 'var(--shadow)',
            minWidth: 200,
            padding: '4px 0',
            fontSize: 13,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <MenuItem onClick={() => { onOpenDetail?.(contextMenu.profile.id); setContextMenu(null); }}>📂 Открыть</MenuItem>
          <MenuItem onClick={() => { onMenu?.('rename', contextMenu.profile.id); setContextMenu(null); }}>✎ Переименовать</MenuItem>
          <MenuItem onClick={() => { onMenu?.('flag', contextMenu.profile.id); setContextMenu(null); }}>🚩 Пометить</MenuItem>
          <MenuItem onClick={() => { onMenu?.('moveToEnd', contextMenu.profile.id); setContextMenu(null); }}>→ В конец</MenuItem>
          <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
          <MenuItem danger onClick={() => { onMenu?.('trash', contextMenu.profile.id); setContextMenu(null); }}>🗑 В корзину</MenuItem>
        </div>
      )}
    </div>
  );
}

function MenuItem({ children, onClick, danger }) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: '7px 14px',
        cursor: 'pointer',
        color: danger ? 'var(--danger)' : 'var(--text)',
        transition: 'background 0.1s',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-3)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = ''; }}
    >
      {children}
    </div>
  );
}
