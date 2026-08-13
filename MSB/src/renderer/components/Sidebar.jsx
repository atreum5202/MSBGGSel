import React from 'react';

const NAV_ITEMS = [
  { key: 'profiles',     icon: '🛡', label: 'Профили' },
  { key: 'proxies',      icon: '🌐', label: 'Прокси' },
  { key: 'traffic',      icon: '🕵', label: 'Перехват трафика' },
  { key: 'scrapers',     icon: '🤖', label: 'Скраперы' },
  { key: 'groups',       icon: '📁', label: 'Группы' },
  { key: 'trash',        icon: '🗑', label: 'Корзина' },
  { key: 'extensions',   icon: '🧩', label: 'Расширения' },
  { key: 'warmer',       icon: '🍪', label: 'Cookie Warmer' },
  { key: 'automation',   icon: '⚡', label: 'Автоматизация' },
  { key: 'audit',        icon: '📜', label: 'Логи / Аудит' },
  { key: 'monitoring',   icon: '📈', label: 'Мониторинг' },
  { key: 'settings',     icon: '⚙️', label: 'Настройки' },
];

export default function Sidebar({ currentPage, onNavigate, collapsed, onToggle, profileCount, runningCount, trashCount }) {
  return (
    <aside className={`msb-sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="msb-sidebar-header">
        <div className="msb-sidebar-logo" title="MSB — MyStealthBrowser">M</div>
        <div className="msb-sidebar-title">MSB</div>
        <button
          className="msb-sidebar-collapse"
          onClick={onToggle}
          title={collapsed ? 'Развернуть' : 'Свернуть'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>

      <nav className="msb-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            className={`msb-nav-item${currentPage === item.key ? ' active' : ''}`}
            onClick={() => onNavigate(item.key)}
            title={collapsed ? item.label : undefined}
          >
            <span className="msb-nav-icon">{item.icon}</span>
            <span className="msb-nav-label">{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="msb-sidebar-footer">
        <div className="msb-sidebar-footer-stat">
          <span>Профили:</span>
          <span className="msb-sidebar-footer-stat-val">{profileCount}</span>
        </div>
        <div className="msb-sidebar-footer-stat">
          <span>Запущено:</span>
          <span className="msb-sidebar-footer-stat-val" style={{ color: 'var(--ok)' }}>
            {runningCount}
          </span>
        </div>
        {trashCount > 0 && (
          <div className="msb-sidebar-footer-stat">
            <span>В корзине:</span>
            <span className="msb-sidebar-footer-stat-val" style={{ color: 'var(--warn)' }}>
              {trashCount}
            </span>
          </div>
        )}
      </div>
    </aside>
  );
}
