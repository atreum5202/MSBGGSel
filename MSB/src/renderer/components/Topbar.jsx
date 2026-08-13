import React from 'react';

const VERSION = '1.2.0';

// workspaceState — это объект { phase, error?, profile?, flask? }.
//   phase: 'idle' | 'launching' | 'running' | 'error'
// Компонент сам выводит лейбл/цвет, App.jsx держит state.
function workspaceButtonLabel(state) {
  if (!state) return { text: '🚀 ИИ Воркспейс', short: '🚀' };
  switch (state.phase) {
    case 'launching':
      return { text: '⏳ Запускаю…', short: '⏳' };
    case 'running':
      return { text: '🟢 Воркспейс', short: '🟢' };
    case 'error':
      return { text: '⚠ Ошибка', short: '⚠' };
    case 'idle':
    default:
      return { text: '🚀 ИИ Воркспейс', short: '🚀' };
  }
}

function workspaceTooltip(state) {
  if (!state) return 'Открыть профиль AI Workspace с локальной Flask-панелью (127.0.0.1:5000)';
  switch (state.phase) {
    case 'launching':
      return 'Поднимаю Flask и браузер профиля…';
    case 'running':
      return [
        'AI Workspace запущен.',
        state.profile?.startUrl ? `Старт: ${state.profile.startUrl}` : '',
        state.flask?.running === false ? 'Flask не отвечает — кликни ещё раз для ретрая' : '',
      ].filter(Boolean).join('\n');
    case 'error':
      return `Ошибка: ${state.error || 'unknown'}`;
    default:
      return 'Открыть профиль AI Workspace с локальной Flask-панелью (127.0.0.1:5000)';
  }
}

export default function Topbar({
  theme, onToggleTheme, encryptionEnabled, onEncryptionClick, user,
  workspaceState, onLaunchWorkspace,
}) {
  const wLabel = workspaceButtonLabel(workspaceState);
  const wTip = workspaceTooltip(workspaceState);
  const wBusy = workspaceState?.phase === 'launching';
  return (
    <header className="msb-topbar">
      <div className="msb-topbar-version">MSB v{VERSION}</div>

      <button
        className={`msb-encryption-badge${encryptionEnabled ? '' : ' disabled'}`}
        onClick={onEncryptionClick}
        title={encryptionEnabled
          ? 'Сквозное шифрование включено — кликни чтобы настроить'
          : 'Шифрование не настроено — кликни чтобы включить'}
      >
        🔐 {encryptionEnabled ? 'Сквозное шифрование' : 'Шифрование выкл'}
      </button>

      <button
        className={`msb-workspace-btn phase-${workspaceState?.phase || 'idle'}`}
        onClick={onLaunchWorkspace}
        disabled={wBusy}
        title={wTip}
        data-workspace-phase={workspaceState?.phase || 'idle'}
      >
        <span className="msb-workspace-btn-glyph">{wLabel.short}</span>
        <span className="msb-workspace-btn-label">{wLabel.text}</span>
      </button>

      <div className="msb-topbar-spacer" />

      <button
        className="msb-icon-btn"
        onClick={onToggleTheme}
        title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
      >
        {theme === 'dark' ? '☀' : '☾'}
      </button>

      <button className="msb-icon-btn" title="Уведомления">🔔</button>

      {user ? (
        <div className="msb-user-chip" title={user.name}>
          <div className="msb-user-avatar">{user.avatar || user.name?.[0]?.toUpperCase() || 'U'}</div>
          <span>{user.name}</span>
        </div>
      ) : null}
    </header>
  );
}

