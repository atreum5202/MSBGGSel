import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api.js';

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function fmtAgo(ts) {
  if (!ts) return '—';
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 60_000) return 'только что';
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)} мин назад`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)} ч назад`;
  return `${Math.floor(ms / 86_400_000)} д назад`;
}

function StatusDot({ state }) {
  const map = {
    idle:    { color: 'var(--text-dim)',  label: 'простаивает' },
    running: { color: 'var(--ok)',        label: 'выполняется' },
    error:   { color: 'var(--err)',       label: 'ошибка' },
    success: { color: 'var(--ok)',        label: 'успех' },
  };
  const s = map[state] || map.idle;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: s.color }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: s.color, display: 'inline-block' }} />
      {s.label}
    </span>
  );
}

// ─── Card ───────────────────────────────────────────────────────────────────

function ScraperCard({ entry, onOpen, onRefresh }) {
  const { id, path: dir, manifest } = entry;
  const [state, setState] = useState('idle');
  const [errorMsg, setErrorMsg] = useState(null);
  const [lastResults, setLastResults] = useState(null);
  const [logFile, setLogFile] = useState(null);
  const [logContent, setLogContent] = useState('');
  const [logOffset, setLogOffset] = useState(0);
  const [logOpen, setLogOpen] = useState(false);
  const [readmeOpen, setReadmeOpen] = useState(false);
  const [readmeContent, setReadmeContent] = useState('');
  const logPollRef = useRef(null);

  const refresh = useCallback(async () => {
    if (manifest?.resultsFile) {
      try {
        const r = await api.scrapers.readJsonl(id, manifest.resultsFile, 5);
        if (r?.entries) setLastResults(r);
      } catch (e) { /* ignore */ }
    }
  }, [id, manifest]);

  useEffect(() => { refresh(); }, [refresh]);

  // Poll log if open
  useEffect(() => {
    if (!logOpen || !logFile) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.scrapers.readOutput(logFile, logOffset);
        if (cancelled) return;
        if (r?.content) {
          setLogContent((prev) => prev + r.content);
          setLogOffset(r.offset);
          if (r.eof && r.size > 0) {
            // process likely done — clear state
            setState((s) => (s === 'running' ? 'success' : s));
          }
        }
      } catch (e) { /* ignore */ }
    };
    tick();
    logPollRef.current = setInterval(tick, 1000);
    return () => { cancelled = true; clearInterval(logPollRef.current); };
  }, [logOpen, logFile, logOffset]);

  const run = async (command) => {
    setErrorMsg(null);
    setLogContent('');
    setLogOffset(0);
    setState('running');
    try {
      const r = await api.scrapers.run(id, command);
      if (r?.error) {
        setState('error');
        setErrorMsg(r.error);
        return;
      }
      setLogFile(r.logFile);
      setLogOpen(true);
      refresh();
    } catch (e) {
      setState('error');
      setErrorMsg(e?.message || String(e));
    }
  };

  const kill = async () => {
    try {
      await api.scrapers.kill(id);
    } catch (e) { /* ignore */ }
  };

  const openReadme = async () => {
    if (!manifest?.readme) return;
    try {
      const r = await api.scrapers.readText(`${id}/${manifest.readme}`);
      if (r?.error) {
        setErrorMsg(r.error);
        return;
      }
      setReadmeContent(r.content || '');
      setReadmeOpen(true);
    } catch (e) {
      setErrorMsg(e?.message || String(e));
    }
  };

  const openFolder = async () => { try { await api.scrapers.openPath(`${id}/`); } catch (e) { setErrorMsg(e?.message); } };

  const commands = manifest?.altCommands && manifest.altCommands.length
    ? manifest.altCommands
    : [{ label: 'Запустить', command: manifest?.command || '' }];

  return (
    <div className="msb-scraper-card">
      <div className="msb-scraper-card-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="msb-scraper-title">
            {manifest?.title || id}
            <span className="msb-scraper-id">/{id}</span>
          </div>
          <div className="msb-scraper-desc">{manifest?.description || '—'}</div>
        </div>
        <StatusDot state={state} />
      </div>

      <div className="msb-scraper-path" title={dir}>
        <code>{dir}</code>
        <button className="msb-btn xs" onClick={openFolder} title="Открыть в проводнике">📂</button>
      </div>

      {manifest?.tags && manifest.tags.length > 0 && (
        <div className="msb-scraper-tags">
          {manifest.tags.map((t) => <span key={t} className="msb-scraper-tag">{t}</span>)}
        </div>
      )}

      <div className="msb-scraper-actions">
        {commands.map((c) => (
          <button
            key={c.command}
            className="msb-btn primary"
            onClick={() => run(c.command)}
            disabled={state === 'running' || !c.command}
            title={c.command}
          >
            {state === 'running' ? '…' : '▶'} {c.label}
          </button>
        ))}
        {state === 'running' && (
          <button className="msb-btn danger" onClick={kill}>⏹ Стоп</button>
        )}
        {manifest?.readme && (
          <button className="msb-btn" onClick={openReadme}>📖 README</button>
        )}
        {manifest?.resultsFile && (
          <button className="msb-btn" onClick={refresh} title="Обновить результаты">↻ Результаты</button>
        )}
      </div>

      {errorMsg && (
        <div className="msb-flash err" style={{ marginTop: 8 }}>
          <span>{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)}>×</button>
        </div>
      )}

      {lastResults && lastResults.entries && lastResults.entries.length > 0 && (
        <div className="msb-scraper-results">
          <div className="msb-scraper-results-head">
            📊 Последние записи ({lastResults.entries.length} из {lastResults.totalLines}):
          </div>
          <pre className="msb-scraper-results-pre">
{lastResults.entries.slice(-5).map((e, i) => (
  <div key={i} className={`msb-scraper-result msb-result-${e.status || 'unknown'}`}>
    <span className="msb-mono msb-small">{fmtTime(e.registeredAt || e.timestamp || e.time)}</span>
    {' '}
    <span className={`msb-result-status msb-result-${e.status || 'unknown'}`}>{e.status || '—'}</span>
    {' '}
    {e.email || e.profileId || ''}
    {e.error ? ` — ${e.error}` : ''}
  </div>
))}
          </pre>
        </div>
      )}

      {logOpen && logFile && (
        <div className="msb-scraper-log">
          <div className="msb-scraper-log-head">
            📜 Лог запуска <code className="msb-mono msb-small">{logFile}</code>
            <button className="msb-btn xs" onClick={() => setLogOpen(false)}>×</button>
          </div>
          <pre className="msb-scraper-log-pre">{logContent || '(пусто)'}</pre>
        </div>
      )}

      {readmeOpen && (
        <div className="msb-scraper-readme">
          <div className="msb-scraper-log-head">
            📖 README — {manifest?.readme}
            <button className="msb-btn xs" onClick={() => setReadmeOpen(false)}>×</button>
          </div>
          <pre className="msb-scraper-log-pre">{readmeContent}</pre>
        </div>
      )}
    </div>
  );
}

// ─── Main page ──────────────────────────────────────────────────────────────

// Group icons for known groups
const GROUP_ICONS = {
  'Разведка': '🛰️',
  'Интеграция': '🔌',
  'GGSeller': '🎯',
};

function groupEntries(entries) {
  // { groupName -> { label, icon, items: [] } }
  // Flat (null group) goes to '__flat__' (rendered at top, без заголовка)
  const groups = new Map();
  for (const e of entries) {
    const key = e.group || '__flat__';
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        label: e.group || 'Без группы',
        icon: e.group ? (GROUP_ICONS[e.group] || '📁') : '📦',
        items: [],
      });
    }
    groups.get(key).items.push(e);
  }
  // Sort: known groups first in icon-map order, then alphabetical, flat last
  const order = ['Разведка', 'Интеграция'];
  const sorted = Array.from(groups.values()).sort((a, b) => {
    if (a.key === '__flat__') return 1;
    if (b.key === '__flat__') return -1;
    const ai = order.indexOf(a.key);
    const bi = order.indexOf(b.key);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.label.localeCompare(b.label);
  });
  return sorted;
}

export default function Scrapers() {
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const arr = await api.scrapers.list();
      setList(arr || []);
    } catch (e) {
      setError(e?.message || String(e));
      setList([]);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openScrapersDir = async () => {
    try {
      await api.scrapers.openPath('/');
    } catch (e) {
      setError(e?.message || String(e));
    }
  };

  const groups = list ? groupEntries(list) : null;
  const groupCount = groups ? groups.filter(g => g.key !== '__flat__').length : 0;

  return (
    <div className="msb-scrapers-page">
      <div className="msb-scrapers-header">
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>🤖 Скраперы</h2>
          <div style={{ color: 'var(--text-dim)', fontSize: 12, marginTop: 4 }}>
            Пользовательские проекты в <code>%APPDATA%\MSB\scrapers\</code>.
            Поддерживаются группы: <code>&lt;group&gt;/&lt;scraper&gt;/manifest.json</code>.
            {groupCount > 0 && (
              <> Сейчас групп: <b>{groupCount}</b>.</>
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="msb-btn" onClick={openScrapersDir}>📂 Папка scrapers</button>
          <button className="msb-btn" onClick={refresh}>↻ Обновить</button>
        </div>
      </div>

      {error && (
        <div className="msb-flash err">
          <span>Ошибка: {error}</span>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      {info && (
        <div className="msb-flash ok">
          <span>{info}</span>
          <button onClick={() => setInfo(null)}>×</button>
        </div>
      )}

      {list === null ? (
        <div className="msb-empty">Загружаю…</div>
      ) : list.length === 0 ? (
        <div className="msb-empty">
          Папка scrapers пуста. Положи туда проект с <code>manifest.json</code>:
          <pre className="msb-scraper-log-pre" style={{ marginTop: 12, textAlign: 'left' }}>{`$APPDATA\\MSB\\scrapers\\
  my-scraper/
    manifest.json   <- {"{ id, title, description, command, group, ... }"}
    index.js
    package.json
    README.md

  # или с группой:
  <group>/
    my-scraper/
      manifest.json   <- { "group": "<group>", ... }`}</pre>
        </div>
      ) : (
        <div>
          {groups.map((g) => (
            <div key={g.key} className="msb-scraper-group">
              {g.key !== '__flat__' && (
                <div className="msb-scraper-group-head">
                  <span className="msb-scraper-group-icon">{g.icon}</span>
                  <span className="msb-scraper-group-title">{g.label}</span>
                  <span className="msb-scraper-group-count">{g.items.length}</span>
                </div>
              )}
              <div className="msb-scraper-grid">
                {g.items.map((entry) => (
                  <ScraperCard key={entry.id} entry={entry} onRefresh={refresh} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
