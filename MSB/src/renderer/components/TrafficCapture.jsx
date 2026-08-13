import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api.js';

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  return `${Math.floor(ms / 3600000)}h ${Math.floor((ms % 3600000) / 60000)}m`;
}

function StatusBadge({ active, label }) {
  return (
    <span className={`msb-status-pill ${active ? 'ok' : 'off'}`}>
      <span className="msb-status-dot" />
      {label || (active ? 'ACTIVE' : 'OFF')}
    </span>
  );
}

function Section({ title, hint, children, right }) {
  return (
    <div className="msb-section">
      <div className="msb-section-head">
        <div>
          <h3 className="msb-section-title">{title}</h3>
          {hint && <div className="msb-section-hint">{hint}</div>}
        </div>
        {right}
      </div>
      <div className="msb-section-body">{children}</div>
    </div>
  );
}

function Row({ label, hint, children, mono }) {
  return (
    <div className="msb-row">
      <div className="msb-row-label">
        <div className="msb-row-label-text" style={mono ? { fontFamily: 'ui-monospace, Menlo, Consolas, monospace' } : undefined}>{label}</div>
        {hint && <div className="msb-row-hint">{hint}</div>}
      </div>
      <div className="msb-row-control">{children}</div>
    </div>
  );
}

function Input({ value, onChange, placeholder, type = 'text', mono, disabled }) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="msb-input-text"
      style={mono ? { fontFamily: 'ui-monospace, Menlo, Consolas, monospace' } : undefined}
    />
  );
}

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      className={`msb-toggle ${checked ? 'on' : 'off'}`}
      aria-pressed={checked}
    >
      <span className="msb-toggle-knob" />
    </button>
  );
}

function Select({ value, onChange, options, disabled }) {
  return (
    <select
      className="msb-input-text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function MethodBadge({ method }) {
  const m = (method || '').toUpperCase();
  const cls = `msb-method msb-method-${m.toLowerCase()}`;
  return <span className={cls}>{m || '?'}</span>;
}

function StatusCodeBadge({ status }) {
  if (status == null) return <span className="msb-statuscode pending">…</span>;
  const s = String(status);
  let cls = 'msb-statuscode';
  if (status >= 200 && status < 300) cls += ' ok';
  else if (status >= 300 && status < 400) cls += ' redir';
  else if (status >= 400 && status < 500) cls += ' client';
  else if (status >= 500) cls += ' server';
  else cls += ' info';
  return <span className={cls}>{s}</span>;
}

// ─── Logs WS connector (no-op until /ws/logs is live) ────────────────────

function useLogStream(enabled) {
  const [lines, setLines] = useState([]);
  const bufRef = useRef([]);

  useEffect(() => {
    if (!enabled) return undefined;
    let ws = null;
    let closed = false;
    let retryMs = 1000;

    const flushTimer = setInterval(() => {
      if (bufRef.current.length === 0) return;
      setLines((prev) => {
        const merged = prev.concat(bufRef.current);
        bufRef.current = [];
        // Keep last 500 lines
        return merged.length > 500 ? merged.slice(merged.length - 500) : merged;
      });
    }, 250);

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      try {
        ws = new WebSocket(`${proto}//${location.host}/ws/logs`);
      } catch (e) {
        setTimeout(connect, retryMs);
        return;
      }
      ws.addEventListener('open', () => { retryMs = 1000; });
      ws.addEventListener('message', (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg && msg.msg) {
            bufRef.current.push({
              ts: msg.time || new Date().toISOString(),
              level: msg.level || 'info',
              mod: msg.mod || '',
              text: msg.msg,
            });
          }
        } catch { /* keepalive */ }
      });
      ws.addEventListener('close', () => {
        if (closed) return;
        setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 1.5, 10000);
      });
      ws.addEventListener('error', () => { try { ws.close(); } catch { /* */ } });
    };
    connect();

    return () => {
      closed = true;
      clearInterval(flushTimer);
      try { ws && ws.close(); } catch { /* */ }
    };
  }, [enabled]);

  return lines;
}

// ─── Main page ─────────────────────────────────────────────────────────────

export default function TrafficCapture({ profiles, running, onClose }) {
  // ── Profile selection ──
  const [profileId, setProfileId] = useState(() => {
    if (profiles && profiles.length) return profiles[0].id;
    return '';
  });

  // Keep profileId valid as profiles list refreshes
  useEffect(() => {
    if (!profileId && profiles && profiles.length) {
      setProfileId(profiles[0].id);
    } else if (profileId && profiles && profiles.length && !profiles.find((p) => p.id === profileId)) {
      setProfileId(profiles[0].id);
    }
  }, [profiles, profileId]);

  const selectedProfile = profiles?.find((p) => p.id === profileId) || null;
  const isRunning = running?.some((r) => r.id === profileId) || false;

  // ── Capture settings (persisted to localStorage) ──
  const SETTINGS_KEY = 'msb.traffic.settings';
  const [settings, setSettings] = useState(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
      return {
        saveFlow: stored.saveFlow !== false,
        saveHar:  !!stored.saveHar,
        filterHost: stored.filterHost || '',
        autoStart: !!stored.autoStart,
        ringSize:  Number(stored.ringSize) || 5000,
        logStream: stored.logStream !== false,
      };
    } catch {
      return { saveFlow: true, saveHar: false, filterHost: '', autoStart: false, ringSize: 5000, logStream: true };
    }
  });

  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* */ }
  }, [settings]);

  // ── Live status ──
  const [trafficStatus, setTrafficStatus] = useState(null);   // { active, port, pid, captureDir, ... }
  const [networkStatus, setNetworkStatus] = useState(null);   // { active, count, oldestAt, newestAt, pages }
  const [pastCaptures, setPastCaptures] = useState([]);
  const [busy, setBusy] = useState(false);
  const [terminalBusy, setTerminalBusy] = useState(false);
  const [error, setError] = useState(null);
  const [successMsg, setSuccessMsg] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');   // overview | endpoints | requests | log

  // ── Endpoints + Requests data (lazy, on demand) ──
  const [endpoints, setEndpoints] = useState([]);
  const [requests, setRequests] = useState([]);
  const [endpointLimit, setEndpointLimit] = useState(50);
  const [requestFilters, setRequestFilters] = useState({ method: '', host: '', status: '', limit: '100' });
  const [endpointsLoading, setEndpointsLoading] = useState(false);
  const [requestsLoading, setRequestsLoading] = useState(false);

  // ── Log stream ──
  const logLines = useLogStream(settings.logStream && activeTab === 'log');

  // ── Helpers ──
  const flashError = (e) => { setError(e?.message || String(e)); setTimeout(() => setError(null), 6000); };
  const flashOk    = (m) => { setSuccessMsg(m); setTimeout(() => setSuccessMsg(null), 4000); };

  const refreshAll = useCallback(async () => {
    if (!profileId) return;
    try {
      const [ts, ns, pc] = await Promise.all([
        api.traffic.status(profileId).catch(() => null),
        api.network.status(profileId).catch(() => null),
        api.traffic.listCaptures(profileId).catch(() => []),
      ]);
      setTrafficStatus(ts);
      setNetworkStatus(ns);
      setPastCaptures(Array.isArray(pc) ? pc : []);
    } catch (e) { flashError(e); }
  }, [profileId]);

  useEffect(() => {
    refreshAll();
    const t = setInterval(refreshAll, 5000);
    return () => clearInterval(t);
  }, [refreshAll]);

  const loadEndpoints = useCallback(async () => {
    if (!profileId) return;
    setEndpointsLoading(true);
    try {
      const list = await api.network.endpoints(profileId, { limit: endpointLimit });
      setEndpoints(Array.isArray(list) ? list : []);
    } catch (e) { flashError(e); }
    finally { setEndpointsLoading(false); }
  }, [profileId, endpointLimit]);

  const loadRequests = useCallback(async () => {
    if (!profileId) return;
    setRequestsLoading(true);
    try {
      const q = {
        method: requestFilters.method || undefined,
        host:   requestFilters.host   || undefined,
        status: requestFilters.status || undefined,
        limit:  Number(requestFilters.limit) || 100,
      };
      const list = await api.network.requests(profileId, q);
      setRequests(Array.isArray(list) ? list : []);
    } catch (e) { flashError(e); }
    finally { setRequestsLoading(false); }
  }, [profileId, requestFilters]);

  useEffect(() => { if (activeTab === 'endpoints') loadEndpoints(); }, [activeTab, loadEndpoints]);
  useEffect(() => { if (activeTab === 'requests') loadRequests(); }, [activeTab, loadRequests]);

  // ── Actions ──
  const startCapture = async () => {
    if (!profileId) { flashError('Выберите профиль'); return; }
    setBusy(true);
    try {
      const info = await api.traffic.start(profileId, {
        saveFlow: settings.saveFlow,
        saveHar:  settings.saveHar,
        filterHost: settings.filterHost || undefined,
      });
      flashOk(`Захват запущен: порт ${info?.port}, pid ${info?.pid}`);
      await refreshAll();
    } catch (e) { flashError(e); }
    finally { setBusy(false); }
  };

  const stopCapture = async () => {
    if (!profileId) return;
    setBusy(true);
    try {
      const r = await api.traffic.stop(profileId);
      flashOk(`Захват остановлен: ${r?.captureDir || ''} (${fmtDuration(r?.duration)})`);
      await refreshAll();
    } catch (e) { flashError(e); }
    finally { setBusy(false); }
  };

  // ── Открыть mitmproxy в терминале ──
  const openInTerminal = async () => {
    setTerminalBusy(true);
    try {
      if (window.msb?.traffic?.openTerminal) {
        // Electron: через IPC
        await window.msb.traffic.openTerminal({ bin: 'mitmproxy' });
      } else {
        // Web-fallback: попытка через API (если есть соответствующий endpoint)
        await api.traffic.openTerminal?.({ bin: 'mitmproxy' });
      }
      flashOk('Терминал с mitmproxy открыт');
    } catch (e) {
      flashError(e);
    } finally {
      setTerminalBusy(false);
    }
  };

  const clearNetworkBuf = async () => {
    if (!profileId) return;
    if (!confirm('Очистить in-memory буфер сетевого лога для этого профиля?')) return;
    setBusy(true);
    try {
      const r = await api.network.clear(profileId);
      flashOk(`Очищено записей: ${r?.cleared ?? 0}`);
      await refreshAll();
    } catch (e) { flashError(e); }
    finally { setBusy(false); }
  };

  const exportHar = async () => {
    if (!profileId) return;
    try {
      const har = await api.network.har(profileId, { host: settings.filterHost || undefined });
      const blob = new Blob([JSON.stringify(har, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${(selectedProfile?.name || profileId).replace(/[^\w.-]/g, '_')}.har`;
      a.click();
      URL.revokeObjectURL(a.href);
      flashOk('HAR экспортирован');
    } catch (e) { flashError(e); }
  };

  // ── Render ──
  return (
    <div className="msb-traffic-page">
      {/* Top: profile selector + global status */}
      <div className="msb-traffic-header">
        <div className="msb-traffic-header-left">
          <select
            className="msb-input-text msb-profile-select"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            {profiles?.map((p) => (
              <option key={p.id} value={p.id}>
                #{p.number} {p.name}{p.account?.email ? ` (${p.account.email})` : ''}
              </option>
            ))}
          </select>
          {selectedProfile && (
            <div className="msb-profile-status">
              {isRunning ? <StatusBadge active={true} label="BROWSER UP" /> : <StatusBadge active={false} label="BROWSER DOWN" />}
              <span className="msb-profile-status-meta">id: <code>{selectedProfile.id.slice(0, 8)}…</code></span>
            </div>
          )}
        </div>
        <div className="msb-traffic-header-right">
          {trafficStatus?.active
            ? <StatusBadge active={true} label="MITM UP" />
            : <StatusBadge active={false} label="MITM OFF" />}
          {networkStatus?.active
            ? <StatusBadge active={true} label="CDP LOG" />
            : <StatusBadge active={false} label="CDP LOG OFF" />}
        </div>
      </div>

      {(error || successMsg) && (
        <div className={`msb-flash ${error ? 'err' : 'ok'}`}>
          <span>{error || successMsg}</span>
          <button onClick={() => { setError(null); setSuccessMsg(null); }}>×</button>
        </div>
      )}

      {/* Tabs */}
      <div className="msb-tabs">
        {[
          { id: 'overview',  label: '⚙️ Обзор' },
          { id: 'endpoints', label: `📡 Эндпоинты${endpoints.length ? ` (${endpoints.length})` : ''}` },
          { id: 'requests',  label: `📋 Запросы${requests.length ? ` (${requests.length})` : ''}` },
          { id: 'log',       label: '📜 Лог' },
        ].map((t) => (
          <button
            key={t.id}
            className={`msb-tab ${activeTab === t.id ? 'active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >{t.label}</button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div className="msb-traffic-overview">
          <Section
            title="Захват трафика (mitmproxy)"
            hint="Поднимает mitmdump на свободном порту, патчит proxy профиля, пишет .mitm в captures/"
            right={
              <div className="msb-section-actions">
                {/* Кнопка: открыть mitmproxy в терминале */}
                <button
                  className="msb-btn"
                  onClick={openInTerminal}
                  disabled={terminalBusy}
                  title="Открыть mitmproxy в отдельном окне терминала"
                >
                  {terminalBusy ? '…' : '🖥 Терминал'}
                </button>

                {trafficStatus?.active ? (
                  <button className="msb-btn danger" onClick={stopCapture} disabled={busy}>⏹ Остановить</button>
                ) : (
                  <button className="msb-btn primary" onClick={startCapture} disabled={busy || !profileId}>
                    {busy ? '…' : '▶ Запустить'}
                  </button>
                )}
              </div>
            }
          >
            <Row label="saveFlow" hint="Писать .mitm flow файл" mono>
              <Toggle checked={settings.saveFlow} onChange={(v) => setSettings((s) => ({ ...s, saveFlow: v }))} disabled={trafficStatus?.active} />
            </Row>
            <Row label="saveHar" hint="Дополнительно .har (человекочитаемый)" mono>
              <Toggle checked={settings.saveHar} onChange={(v) => setSettings((s) => ({ ...s, saveHar: v }))} disabled={trafficStatus?.active} />
            </Row>
            <Row label="filterHost" hint="Только этот хост (например seller.ggsel.com). Пусто — всё." mono>
              <Input
                value={settings.filterHost}
                onChange={(v) => setSettings((s) => ({ ...s, filterHost: v }))}
                placeholder="например seller.ggsel.com"
                mono
                disabled={trafficStatus?.active}
              />
            </Row>
            <Row label="autoStart" hint="Запускать захват автоматически при старте профиля (через startProfile в скриптах)" mono>
              <Toggle checked={settings.autoStart} onChange={(v) => setSettings((s) => ({ ...s, autoStart: v }))} />
            </Row>

            {trafficStatus?.active && (
              <div className="msb-status-grid">
                <Field label="Порт"  value={trafficStatus.port} mono />
                <Field label="PID"   value={trafficStatus.pid}  mono />
                <Field label="Фильтр" value={trafficStatus.filterHost || '—'} mono />
                <Field label="Аптайм" value={trafficStatus.startedAt ? `${Math.round((Date.now() - trafficStatus.startedAt) / 1000)}s` : '—'} />
                <Field label="Записано" value={fmtBytes(trafficStatus.byteCount)} />
                <Field label="Capture dir" value={trafficStatus.captureDir || '—'} mono wide />
              </div>
            )}
          </Section>

          <Section
            title="Сетевой лог (CDP, ring buffer)"
            hint="Без прокси. MSB слушает Network.* события через CDP. Дёшево, без тел ответов по умолчанию."
            right={
              <div className="msb-section-actions">
                <button className="msb-btn" onClick={exportHar} disabled={!profileId}>⬇ Экспорт HAR</button>
                <button className="msb-btn danger" onClick={clearNetworkBuf} disabled={busy || !profileId}>🗑 Очистить</button>
              </div>
            }
          >
            <Row label="ringSize" hint="Размер кольца (записей в памяти). Default 5000." mono>
              <Input
                type="number"
                value={settings.ringSize}
                onChange={(v) => setSettings((s) => ({ ...s, ringSize: Number(v) || 5000 }))}
                mono
              />
            </Row>
            <Row label="Лог-стрим в UI" hint="Подписаться на /ws/logs и показывать события mitmdump в реальном времени" mono>
              <Toggle checked={settings.logStream} onChange={(v) => setSettings((s) => ({ ...s, logStream: v }))} />
            </Row>

            {networkStatus?.active && (
              <div className="msb-status-grid">
                <Field label="Записей"  value={networkStatus.count ?? 0} />
                <Field label="Oldest"   value={networkStatus.oldestAt || '—'} mono />
                <Field label="Newest"   value={networkStatus.newestAt || '—'} mono />
                <Field label="Pages"    value={networkStatus.pages ?? 0} />
              </div>
            )}
          </Section>

          <Section
            title="Прошлые сессии захвата"
            hint="Список папок в <MSB>/captures/<profileId>/. Каждая сессия = одна папка с capture.mitm."
            right={
              <button className="msb-btn" onClick={refreshAll}>↻ Обновить</button>
            }
          >
            {pastCaptures.length === 0 ? (
              <div className="msb-empty">Нет сессий. Нажмите «Запустить» выше.</div>
            ) : (
              <table className="msb-table">
                <thead>
                  <tr>
                    <th>Сессия</th>
                    <th>Путь</th>
                    <th>Файлов</th>
                    <th>Размер</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {pastCaptures.map((s) => (
                    <tr key={s.session}>
                      <td><code>{s.session}</code></td>
                      <td className="msb-mono msb-truncate" title={s.path}>{s.path}</td>
                      <td>{s.files}</td>
                      <td>{fmtBytes(s.bytes)}</td>
                      <td>
                        <button
                          className="msb-btn xs"
                          onClick={() => {
                            const link = `file:///${s.path.replace(/\\/g, '/')}`;
                            window.open(link, '_blank');
                          }}
                        >📂 Открыть</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>
        </div>
      )}

      {activeTab === 'endpoints' && (
        <Section
          title="Уникальные эндпоинты"
          hint="Группировка по шаблонному пути. /api/v1/offers/123 и /api/v1/offers/456 → /api/v1/offers/{id}"
          right={<button className="msb-btn" onClick={loadEndpoints} disabled={endpointsLoading}>↻ Обновить</button>}
        >
          <div className="msb-row" style={{ borderBottom: 'none', paddingBottom: 0 }}>
            <div className="msb-row-label">Лимит</div>
            <div className="msb-row-control">
              <Input type="number" value={endpointLimit} onChange={(v) => setEndpointLimit(Number(v) || 50)} mono />
            </div>
          </div>
          {endpointsLoading ? (
            <div className="msb-empty">Загружаю…</div>
          ) : endpoints.length === 0 ? (
            <div className="msb-empty">Нет данных. Сходите в браузер — лог пополнится.</div>
          ) : (
            <table className="msb-table">
              <thead>
                <tr><th>Метод</th><th>Путь (templated)</th><th>Кол-во</th><th>Статусы</th><th>Первое</th><th>Последнее</th></tr>
              </thead>
              <tbody>
                {endpoints.map((e, i) => (
                  <tr key={`${e.method}-${e.path}-${i}`}>
                    <td><MethodBadge method={e.method} /></td>
                    <td className="msb-mono">{e.path}</td>
                    <td><strong>{e.count}</strong></td>
                    <td>{(e.statuses || []).map((s) => <StatusCodeBadge key={s} status={s} />)}</td>
                    <td className="msb-mono msb-small">{e.firstAt?.slice(11, 19)}</td>
                    <td className="msb-mono msb-small">{e.lastAt?.slice(11, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {activeTab === 'requests' && (
        <Section
          title="Список запросов"
          hint="Сырые записи из ring buffer. Лимит 100–500."
          right={<button className="msb-btn" onClick={loadRequests} disabled={requestsLoading}>↻ Обновить</button>}
        >
          <div className="msb-filters">
            <Input value={requestFilters.method} onChange={(v) => setRequestFilters((f) => ({ ...f, method: v }))} placeholder="GET / POST" mono />
            <Input value={requestFilters.host}   onChange={(v) => setRequestFilters((f) => ({ ...f, host: v })) } placeholder="host" mono />
            <Input value={requestFilters.status} onChange={(v) => setRequestFilters((f) => ({ ...f, status: v }))} placeholder="статус" mono />
            <Input type="number" value={requestFilters.limit} onChange={(v) => setRequestFilters((f) => ({ ...f, limit: v }))} placeholder="limit" mono />
          </div>
          {requestsLoading ? (
            <div className="msb-empty">Загружаю…</div>
          ) : requests.length === 0 ? (
            <div className="msb-empty">Нет данных.</div>
          ) : (
            <table className="msb-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Время</th>
                  <th>Метод</th>
                  <th>URL</th>
                  <th>Статус</th>
                  <th>Длит.</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.n}>
                    <td className="msb-mono">{r.n}</td>
                    <td className="msb-mono msb-small">{r.capturedAt?.slice(11, 19)}</td>
                    <td><MethodBadge method={r.method} /></td>
                    <td className="msb-mono msb-truncate" title={r.url}>{r.method} {r.host}{r.path}</td>
                    <td>{r.failed ? <StatusCodeBadge status={null} /> : <StatusCodeBadge status={r.status} />}</td>
                    <td className="msb-mono">{r.durationMs != null ? `${r.durationMs}ms` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {activeTab === 'log' && (
        <Section
          title="Лог-стрим"
          hint="События из /ws/logs (mitmdump, MSB events). Последние 500 строк."
          right={
            <Toggle
              checked={settings.logStream}
              onChange={(v) => setSettings((s) => ({ ...s, logStream: v }))}
            />
          }
        >
          <pre className="msb-log-stream">
            {logLines.length === 0 ? (
              <div className="msb-empty">{settings.logStream ? 'Ожидание событий…' : 'Лог-стрим отключён в настройках выше.'}</div>
            ) : logLines.map((l, i) => (
              <div key={i} className={`msb-log-line msb-log-${l.level}`}>
                <span className="msb-log-ts">{l.ts?.slice(11, 23)}</span>
                <span className="msb-log-lvl">{l.level.toUpperCase()}</span>
                {l.mod && <span className="msb-log-mod">[{l.mod}]</span>}
                <span className="msb-log-msg">{l.text}</span>
              </div>
            ))}
          </pre>
        </Section>
      )}
    </div>
  );
}

function Field({ label, value, mono, wide }) {
  return (
    <div className={`msb-field ${wide ? 'wide' : ''}`}>
      <div className="msb-field-label">{label}</div>
      <div className="msb-field-value" style={mono ? { fontFamily: 'ui-monospace, Menlo, Consolas, monospace' } : undefined}>{value}</div>
    </div>
  );
}
