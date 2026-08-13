import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api.js';

const LOG_LIMIT = 300;

// ── Omnibox ───────────────────────────────────────────────────────────────────
const SEARCH_ENGINE = 'https://duckduckgo.com/?q=';

function parseOmniboxInput(raw) {
  const input = (raw || '').trim();
  if (!input) return null;
  if (/\s/.test(input)) return SEARCH_ENGINE + encodeURIComponent(input);
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(input)) return input;
  if (/^localhost(:\d+)?(\/.*)?$/i.test(input)) return `http://${input}`;
  if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?(\/.*)?$/.test(input)) return `http://${input}`;
  if (/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:\d+)?(\/.*)?$/i.test(input)) return `https://${input}`;
  return SEARCH_ENGINE + encodeURIComponent(input);
}

function useProfileLogs(profileId) {
  const [entries, setEntries] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!profileId) { setEntries([]); setConnected(false); return; }
    setEntries([]);
    setConnected(false);

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/logs?scope=${encodeURIComponent(profileId)}`;
    let alive = true;
    let retryTimer = null;
    let retryMs = 1200;

    function connect() {
      if (!alive) return;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.addEventListener('open', () => { if (!alive) { ws.close(); return; } setConnected(true); retryMs = 1200; });
      ws.addEventListener('message', (ev) => {
        try {
          const { entry, historical } = JSON.parse(ev.data);
          if (!entry) return;
          const level = entry.level >= 50 ? 'err' : entry.level >= 40 ? 'warn' : entry.level >= 30 ? 'info' : 'debug';
          const time = entry.time ? new Date(entry.time).toLocaleTimeString() : new Date().toLocaleTimeString();
          const msg = entry.msg || JSON.stringify(entry);
          setEntries(prev => [...prev, { level, time, msg, historical: !!historical }].slice(-LOG_LIMIT));
        } catch { /* ignore */ }
      });
      ws.addEventListener('close', () => {
        setConnected(false);
        if (alive) { retryTimer = setTimeout(connect, retryMs); retryMs = Math.min(retryMs * 1.5, 8000); }
      });
      ws.addEventListener('error', () => { try { ws.close(); } catch { /* ok */ } });
    }

    connect();
    return () => { alive = false; clearTimeout(retryTimer); try { wsRef.current?.close(); } catch { /* ok */ } };
  }, [profileId]);

  return { entries, connected };
}

// ── Account секция ────────────────────────────────────────────────────────────

const TAG_COLORS = { claude: '#7c5cfc', cursor: '#1a91da', minimax: '#e05b2e' };

function AccountSection({ profile, onUpdateAccount }) {
  const acc = profile.account || {};
  const [showPass, setShowPass] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    email: acc.email || '',
    password: acc.password || '',
    tags: (acc.tags || []).join('; '),
    loginStatus: acc.loginStatus || 'unknown',
  });

  const statusOptions = [
    { value: 'unknown', label: '— неизвестно', color: 'var(--text-dim)' },
    { value: 'ok',      label: '✓ Залогинен',   color: '#4ade80' },
    { value: 'expired', label: '⚠ Сессия истекла', color: '#f59e0b' },
    { value: 'error',   label: '✗ Ошибка входа', color: '#ef4444' },
  ];

  const emailType = acc.type || 'other';
  const typeLabel = emailType === 'gmail' ? 'Gmail' : emailType === 'outlook' ? 'Outlook' : 'Other';
  const typeBg = emailType === 'gmail' ? '#ea4335' : emailType === 'outlook' ? '#0078d4' : '#555';
  const currentStatus = statusOptions.find(s => s.value === (acc.loginStatus || 'unknown'));

  const handleSave = async () => {
    const tags = form.tags.split(/[;,]/).map(t => t.trim()).filter(Boolean);
    await onUpdateAccount({ email: form.email.trim(), password: form.password, tags, loginStatus: form.loginStatus });
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="section">
        <h3>Account</h3>
        <div className="kv">
          <div className="k">Email</div>
          <div className="v"><input value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} style={{ width: '100%', fontSize: 12 }} /></div>
          <div className="k">Пароль</div>
          <div className="v" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type={showPass ? 'text' : 'password'} value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} style={{ flex: 1, fontSize: 12 }} />
            <button onClick={() => setShowPass(v => !v)} style={{ padding: '2px 7px', fontSize: 11 }}>{showPass ? '🙈' : '👁'}</button>
          </div>
          <div className="k">Теги</div>
          <div className="v">
            <input value={form.tags} onChange={e => setForm(f => ({ ...f, tags: e.target.value }))} placeholder="Claude; Cursor; Minimax" style={{ width: '100%', fontSize: 12 }} />
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>через ; или ,</div>
          </div>
          <div className="k">Статус входа</div>
          <div className="v">
            <select value={form.loginStatus} onChange={e => setForm(f => ({ ...f, loginStatus: e.target.value }))} style={{ fontSize: 12 }}>
              {statusOptions.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button onClick={() => setEditing(false)}>Отмена</button>
          <button className="primary" onClick={handleSave}>Сохранить</button>
        </div>
      </div>
    );
  }

  return (
    <div className="section">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>Account</h3>
        <button onClick={() => { setForm({ email: acc.email || '', password: acc.password || '', tags: (acc.tags || []).join('; '), loginStatus: acc.loginStatus || 'unknown' }); setEditing(true); }} style={{ fontSize: 11, padding: '2px 8px' }}>✎ Изменить</button>
      </div>
      <div className="kv">
        <div className="k">Email</div>
        <div className="v" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ background: typeBg, color: '#fff', borderRadius: 3, fontSize: 9, fontWeight: 700, padding: '1px 5px', flexShrink: 0 }}>{typeLabel}</span>
          <span style={{ fontSize: 12 }}>{acc.email || '—'}</span>
        </div>
        <div className="k">Пароль</div>
        <div className="v" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontFamily: 'monospace', fontSize: 12, letterSpacing: showPass ? 0 : 2 }}>{showPass ? (acc.password || '—') : (acc.password ? '••••••••••' : '—')}</span>
          {acc.password && <button onClick={() => setShowPass(v => !v)} style={{ padding: '1px 6px', fontSize: 11 }} title={showPass ? 'Скрыть' : 'Показать'}>{showPass ? '🙈' : '👁'}</button>}
          {acc.password && <button onClick={() => navigator.clipboard.writeText(acc.password)} style={{ padding: '1px 6px', fontSize: 11 }} title="Скопировать">📋</button>}
        </div>
        <div className="k">Статус</div>
        <div className="v" style={{ color: currentStatus?.color, fontSize: 12 }}>{currentStatus?.label || '—'}</div>
        {acc.tags?.length > 0 && (
          <>
            <div className="k">Теги</div>
            <div className="v" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {acc.tags.map(t => (
                <span key={t} style={{ background: TAG_COLORS[t.toLowerCase()] || '#555', color: '#fff', borderRadius: 3, fontSize: 10, padding: '2px 7px', fontWeight: 600 }}>{t}</span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Proxy секция ──────────────────────────────────────────────────────────────

function ProxySection({ profile, onEdit, onToggleProxy }) {
  const proxy = profile.proxy;
  const enabled = profile.proxyEnabled !== false; // по умолчанию выключен если не задан
  const [showProxyPass, setShowProxyPass] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [toggling, setToggling] = useState(false);

  const protocolColors = { http: '#3b82f6', https: '#10b981', socks4: '#f59e0b', socks5: '#8b5cf6' };

  const handleCheck = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      const result = await api.profiles.checkProxy(profile.id);
      setCheckResult(result);
    } catch (err) {
      setCheckResult({ status: 'error', error: err.message });
    } finally {
      setChecking(false);
    }
  };

  const handleToggle = async () => {
    setToggling(true);
    try {
      await onToggleProxy(!enabled);
    } finally {
      setToggling(false);
    }
  };

  // Тоггл — кнопка вкл/выкл
  const toggleBtn = (
    <button
      onClick={handleToggle}
      disabled={toggling || !proxy}
      style={{
        fontSize: 11,
        padding: '2px 10px',
        borderRadius: 4,
        fontWeight: 600,
        background: enabled && proxy ? '#22c55e22' : 'var(--bg3)',
        color: enabled && proxy ? '#22c55e' : 'var(--text-dim)',
        border: `1px solid ${enabled && proxy ? '#22c55e55' : 'var(--border)'}`,
        cursor: proxy ? 'pointer' : 'not-allowed',
        transition: 'all 0.15s',
      }}
      title={!proxy ? 'Сначала настройте прокси' : enabled ? 'Выключить прокси' : 'Включить прокси'}
    >
      {toggling ? '…' : enabled && proxy ? '● Вкл' : '○ Выкл'}
    </button>
  );

  if (!proxy) {
    return (
      <div className="section">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Proxy</h3>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {toggleBtn}
            <button onClick={onEdit} style={{ fontSize: 11, padding: '2px 8px' }}>✎ Настроить</button>
          </div>
        </div>
        <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>Прокси не настроен — прямое соединение.</div>
      </div>
    );
  }

  const protoBg = protocolColors[proxy.protocol] || '#555';
  const hasAuth = !!(proxy.username && proxy.password);
  const fullUrl = hasAuth
    ? `${proxy.protocol}://${proxy.username}:${proxy.password}@${proxy.host}:${proxy.port}`
    : `${proxy.protocol}://${proxy.host}:${proxy.port}`;

  const statusIndicator = () => {
    if (checking) {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f59e0b', animation: 'pulse 1s infinite' }} />
          Проверка…
        </span>
      );
    }
    if (!checkResult) return null;
    if (checkResult.status === 'ok') {
      return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#4ade80' }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#4ade80' }} />
          {checkResult.ip}
          {checkResult.country && <span style={{ color: 'var(--text-dim)' }}>· {checkResult.city ? `${checkResult.city}, ` : ''}{checkResult.country}</span>}
          <span style={{ color: 'var(--text-dim)' }}>· {checkResult.latencyMs}ms</span>
        </span>
      );
    }
    if (checkResult.status === 'direct') {
      return <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>Прямое соединение</span>;
    }
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#ef4444' }}>
        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#ef4444' }} />
        Недоступен: {checkResult.error}
      </span>
    );
  };

  return (
    <div className="section">
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }`}</style>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <h3 style={{ margin: 0 }}>Proxy</h3>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {statusIndicator()}
          {toggleBtn}
          <button
            onClick={handleCheck}
            disabled={checking || !enabled}
            style={{ fontSize: 11, padding: '2px 8px', background: checking ? 'var(--bg3)' : undefined }}
            title={!enabled ? 'Включите прокси для проверки' : 'Проверить доступность прокси'}
          >
            {checking ? '⏳ Проверка…' : '🔍 Проверить'}
          </button>
          <button onClick={() => navigator.clipboard.writeText(fullUrl)} style={{ fontSize: 11, padding: '2px 8px' }} title="Скопировать полный URL прокси">📋</button>
          <button onClick={onEdit} style={{ fontSize: 11, padding: '2px 8px' }}>✎</button>
        </div>
      </div>

      {/* Плашка "выключен" */}
      {!enabled && (
        <div style={{
          background: '#f59e0b18',
          border: '1px solid #f59e0b44',
          borderRadius: 6,
          padding: '6px 10px',
          fontSize: 11,
          color: '#f59e0b',
          marginBottom: 10,
        }}>
          ⚠ Прокси выключен — браузер использует прямое соединение
        </div>
      )}

      <div className="kv">
        <div className="k">Протокол</div>
        <div className="v">
          <span style={{ background: protoBg, color: '#fff', borderRadius: 3, fontSize: 10, fontWeight: 700, padding: '2px 8px', textTransform: 'uppercase', letterSpacing: '0.5px', opacity: enabled ? 1 : 0.5 }}>
            {proxy.protocol}
          </span>
        </div>

        <div className="k">Хост</div>
        <div className="v" style={{ fontFamily: 'monospace', fontSize: 12, opacity: enabled ? 1 : 0.5 }}>{proxy.host}</div>

        <div className="k">Порт</div>
        <div className="v" style={{ fontFamily: 'monospace', fontSize: 12, opacity: enabled ? 1 : 0.5 }}>{proxy.port}</div>

        {hasAuth ? (
          <>
            <div className="k">Логин</div>
            <div className="v" style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: enabled ? 1 : 0.5 }}>
              <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{proxy.username}</span>
              <button onClick={() => navigator.clipboard.writeText(proxy.username)} style={{ padding: '1px 6px', fontSize: 11 }} title="Скопировать логин">📋</button>
            </div>
            <div className="k">Пароль</div>
            <div className="v" style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: enabled ? 1 : 0.5 }}>
              <span style={{ fontFamily: 'monospace', fontSize: 12, letterSpacing: showProxyPass ? 0 : 2 }}>
                {showProxyPass ? proxy.password : '••••••••••'}
              </span>
              <button onClick={() => setShowProxyPass(v => !v)} style={{ padding: '1px 6px', fontSize: 11 }} title={showProxyPass ? 'Скрыть' : 'Показать'}>{showProxyPass ? '🙈' : '👁'}</button>
              <button onClick={() => navigator.clipboard.writeText(proxy.password)} style={{ padding: '1px 6px', fontSize: 11 }} title="Скопировать пароль">📋</button>
            </div>
          </>
        ) : (
          <>
            <div className="k">Авторизация</div>
            <div className="v" style={{ color: 'var(--text-dim)', fontSize: 12 }}>Без авторизации</div>
          </>
        )}

        {proxy.bypass && (
          <>
            <div className="k">Bypass</div>
            <div className="v" style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-dim)' }}>{proxy.bypass}</div>
          </>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

export default function ProfileDetail({ profile, runningInfo, onEdit, onDelete, onStart, onStop, onOpenWindow, onRefresh }) {
  const [actionLog, setActionLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [activeTab, setActiveTab] = useState('live');
  const [urlInput, setUrlInput] = useState('');
  const logBottomRef = useRef(null);
  const isRunning = !!runningInfo;

  useEffect(() => { if (runningInfo?.url) setUrlInput(runningInfo.url); }, [runningInfo?.url]);
  useEffect(() => { setUrlInput(''); }, [profile?.id]);

  const { entries: liveEntries, connected } = useProfileLogs(profile?.id);

  useEffect(() => { logBottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [liveEntries, actionLog, activeTab]);

  const pushAction = (level, msg) =>
    setActionLog((l) => [...l, { level, msg, t: new Date().toLocaleTimeString() }].slice(-LOG_LIMIT));

  const wrap = async (label, fn) => {
    setBusy(true);
    pushAction('info', `▶ ${label}`);
    try {
      const res = await fn();
      pushAction('ok', `✓ ${label} → ${res ? JSON.stringify(res).slice(0, 200) : 'ok'}`);
      return res;
    } catch (err) {
      pushAction('err', `✗ ${label}: ${err.message || err}`);
    } finally {
      setBusy(false);
      onRefresh();
    }
  };

  const runSelfTest = async () => {
    if (!isRunning) return;
    const res = await wrap('self-test', () => window.msb.diagnostics.selfTest(profile.id));
    if (res?.probes) for (const p of res.probes) pushAction(p.ok ? 'ok' : 'err', `  · ${p.name}: ${p.detail}`);
  };

  const runGoogleLogin = async () => {
    const defaultEmail = profile.account?.email || '';
    const defaultPass = profile.account?.password || '';
    const email = prompt('Google email:', defaultEmail);
    if (!email) return;
    const password = prompt('Google password:', defaultPass);
    if (!password) return;
    await wrap('scenario: google-login', () =>
      window.msb.browser.runScenario(profile.id, 'google-login', { email, password })
    );
  };

  const goToUrl = async (e) => {
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    const target = parseOmniboxInput(urlInput);
    if (!target) return;
    await wrap(`goto ${target}`, () => window.msb.browser.goto(profile.id, target));
  };

  const togglePreview = async () => {
    if (previewing) {
      await window.msb.widget.hide();
      setPreviewing(false);
      pushAction('info', '📺 Preview closed');
    } else {
      try {
        await window.msb.widget.show(profile.id);
        setPreviewing(true);
        pushAction('ok', '📺 Stealth screencast started');
      } catch (err) {
        pushAction('err', `📺 Preview failed: ${err.message}`);
      }
    }
  };

  const exportJson = async () => {
    const json = await api.profiles.exportJson ? api.profiles.exportJson(profile.id) : window.msb.profiles.exportJson(profile.id);
    const blob = new Blob([json], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${profile.name.replace(/\s+/g, '_')}.json`;
    a.click();
  };

  const handleUpdateAccount = async (accountPatch) => {
    await api.profiles.update(profile.id, { account: accountPatch });
    onRefresh();
  };

  const handleToggleProxy = async (enabled) => {
    await api.profiles.update(profile.id, { proxyEnabled: enabled });
    onRefresh();
  };

  function levelClass(level) {
    if (level === 'err') return 'err';
    if (level === 'warn') return 'warn';
    if (level === 'debug') return 'debug';
    return 'info';
  }

  return (
    <>
      <header>
        <div className="header-top">
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{profile.name}</div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
              {profile.id}{' '}
              <span className={`pill ${isRunning ? 'running' : 'stopped'}`}>{isRunning ? 'running' : 'stopped'}</span>{' '}
              <span className={`pill ${profile.engine === 'cloakbrowser' ? 'cloak' : 'patch'}`}>{runningInfo?.engine || profile.engine}</span>
            </div>
          </div>
          <div className="toolbar">
            {!isRunning ? (
              <button className="primary" disabled={busy} onClick={() => wrap('start', onStart)}>▶ Start</button>
            ) : (
              <button disabled={busy} onClick={() => wrap('stop', onStop)}>■ Stop</button>
            )}
            <button disabled={busy} title="Открыть в окне" onClick={() => wrap('open in window', onOpenWindow)}>🗗 Открыть в окне</button>
            <button disabled={!isRunning || busy} onClick={togglePreview} style={previewing ? { background: 'var(--accent)', color: '#fff' } : {}}>
              {previewing ? '✕ Close preview' : '📺 Preview'}
            </button>
            <button disabled={!isRunning || busy} onClick={runSelfTest}>Self-test</button>
            <button disabled={!isRunning || busy} onClick={runGoogleLogin}>Google login</button>
            <button onClick={onEdit}>Edit</button>
            <button onClick={exportJson}>Export</button>
            <button className="danger" onClick={onDelete}>Delete</button>
          </div>
        </div>
        <form className="address-bar" onSubmit={goToUrl}>
          <input
            type="text"
            className="address-input"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="Enter URL or search…"
            spellCheck={false}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            disabled={!isRunning || busy}
          />
          <button type="submit" className="primary" disabled={!isRunning || busy || !urlInput.trim()} title="Перейти по URL">Go</button>
        </form>
      </header>
      <div className="body">

        {/* ── Account ── */}
        <AccountSection profile={profile} onUpdateAccount={handleUpdateAccount} />

        {/* ── Fingerprint ── */}
        <div className="section">
          <h3>Fingerprint</h3>
          <div className="kv">
            <div className="k">User-Agent</div><div className="v">{profile.fingerprint.userAgent}</div>
            <div className="k">Platform</div><div className="v">{profile.fingerprint.platform}</div>
            <div className="k">Locale</div><div className="v">{profile.fingerprint.locale}</div>
            <div className="k">Timezone</div><div className="v">{profile.fingerprint.timezone}</div>
            <div className="k">Viewport</div>
            <div className="v">{profile.fingerprint.viewport?.width}×{profile.fingerprint.viewport?.height}</div>
            <div className="k">Aggressive JS spoof</div><div className="v">{profile.aggressiveFingerprint ? 'yes' : 'no'}</div>
            <div className="k">Humanize</div><div className="v">{profile.humanize ? 'yes' : 'no'}</div>
          </div>
        </div>

        {/* ── Proxy ── */}
        <ProxySection profile={profile} onEdit={onEdit} onToggleProxy={handleToggleProxy} />

        {/* ── Runtime ── */}
        <div className="section">
          <h3>Runtime</h3>
          {isRunning ? (
            <div className="kv">
              <div className="k">Engine</div><div className="v">{runningInfo.engine}</div>
              <div className="k">Started</div><div className="v">{new Date(runningInfo.startedAt).toLocaleString()}</div>
              <div className="k">Current URL</div><div className="v">{runningInfo.url || '—'}</div>
              <div className="k">CDP</div><div className="v">{runningInfo.cdpEndpoint || '—'}</div>
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)' }}>Not running.</div>
          )}
        </div>

        {/* ── Logs ── */}
        <div className="section">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>Logs</h3>
            <div style={{ display: 'flex', gap: 4 }}>
              <button onClick={() => setActiveTab('live')} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: activeTab === 'live' ? 'var(--accent)' : 'var(--bg-3)', color: activeTab === 'live' ? '#fff' : 'var(--text-dim)', border: `1px solid ${activeTab === 'live' ? 'var(--accent)' : 'var(--border)'}` }}>
                Live {connected ? <span style={{ color: '#4ade80' }}>●</span> : <span style={{ color: 'var(--text-faint)' }}>○</span>}
              </button>
              <button onClick={() => setActiveTab('actions')} style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: activeTab === 'actions' ? 'var(--accent)' : 'var(--bg-3)', color: activeTab === 'actions' ? '#fff' : 'var(--text-dim)', border: `1px solid ${activeTab === 'actions' ? 'var(--accent)' : 'var(--border)'}` }}>
                Actions
              </button>
            </div>
            {activeTab === 'live' && (
              <span style={{ fontSize: 10, color: 'var(--text-faint)', marginLeft: 'auto' }}>
                {connected ? `${liveEntries.length} entries` : 'connecting…'}
              </span>
            )}
          </div>
          {activeTab === 'live' ? (
            <div className="log" style={{ maxHeight: 300 }}>
              {liveEntries.length === 0 && <span className="info">{connected ? 'Ожидание логов…' : 'Подключение к лог-серверу…'}</span>}
              {liveEntries.map((l, i) => (
                <div key={i} className={levelClass(l.level)} style={l.historical ? { opacity: 0.65 } : {}}>[{l.time}] {l.msg}</div>
              ))}
              <div ref={logBottomRef} />
            </div>
          ) : (
            <div className="log" style={{ maxHeight: 300 }}>
              {actionLog.length === 0 && <span className="info">No actions yet.</span>}
              {actionLog.map((l, i) => <div key={i} className={l.level}>[{l.t}] {l.msg}</div>)}
              <div ref={logBottomRef} />
            </div>
          )}
        </div>

      </div>
    </>
  );
}
