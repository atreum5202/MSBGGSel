import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api.js';

// ─── helpers ────────────────────────────────────────────────────────────────

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}
function fmtDur(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  return `${Math.floor(ms / 3600000)}h ${Math.floor((ms % 3600000) / 60000)}m`;
}
function StatusPill({ status }) {
  const map = {
    queued:   { cls: 'off',  label: 'в очереди' },
    running:  { cls: 'ok',   label: 'выполняется' },
    done:     { cls: 'ok',   label: 'готово' },
    error:    { cls: 'off',  label: 'ошибка' },
  };
  const s = map[status] || map.queued;
  return (
    <span className={`msb-status-pill ${s.cls}`}>
      <span className="msb-status-dot" />
      {s.label}
    </span>
  );
}

function Cap({ available, label, version, hint }) {
  const color = available ? 'var(--ok)' : 'var(--err)';
  return (
    <div className="msb-row" style={{ borderBottom: '1px solid var(--border)' }}>
      <div className="msb-row-label">
        <div className="msb-row-label-text">
          <span style={{ color, marginRight: 6 }}>{available ? '●' : '○'}</span>
          {label}
        </div>
        {hint && !available && <div className="msb-row-hint">{hint}</div>}
      </div>
      <div className="msb-row-control" style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 11, color: 'var(--text-dim)' }}>
        {version || (available ? 'готов' : 'не установлено')}
      </div>
    </div>
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

function TextInput({ value, onChange, placeholder, type = 'text', disabled, mono, rows }) {
  if (rows) {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        rows={rows}
        className="msb-input-text"
        style={{ fontFamily: mono ? 'ui-monospace, Menlo, Consolas, monospace' : 'inherit', width: '100%', resize: 'vertical' }}
      />
    );
  }
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

// ─── JobRow ─────────────────────────────────────────────────────────────────

function JobRow({ job, expanded, onToggle, onAbort, onSelect, selected }) {
  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div
        onClick={onToggle}
        style={{
          display: 'grid',
          gridTemplateColumns: '20px 90px 1fr 90px 90px 80px',
          gap: 8,
          padding: '8px 12px',
          alignItems: 'center',
          cursor: 'pointer',
          background: selected ? 'var(--bg-active)' : (expanded ? 'var(--bg-3)' : 'transparent'),
        }}
      >
        <input
          type="checkbox"
          checked={!!selected}
          onChange={(e) => { e.stopPropagation(); onSelect(e.target.checked); }}
          onClick={(e) => e.stopPropagation()}
        />
        <span style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 11 }}>{job.id.slice(0, 8)}</span>
        <span style={{ fontSize: 12 }}>
          <strong style={{ marginRight: 6 }}>{job.mode}</strong>
          <span style={{ color: 'var(--text-dim)' }}>· {job.profileId.slice(0, 8)}</span>
        </span>
        <StatusPill status={job.status} />
        <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>{fmtDur(job.durationMs)}</span>
        <span style={{ fontSize: 11, color: 'var(--text-dim)', textAlign: 'right' }}>
          {job.resultsCount != null ? `${job.resultsCount} зап.` : ''}
        </span>
      </div>
      {expanded && <JobDetails job={job} onAbort={onAbort} />}
    </div>
  );
}

function JobDetails({ job, onAbort }) {
  const [tab, setTab] = useState('log'); // log | results | spec
  const [log, setLog] = useState({ lines: [], lastTs: 0 });
  const [results, setResults] = useState(null);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        if (tab === 'log') {
          const r = await api.automation.crawlerJobLog(job.id, { sinceTs: log.lastTs, limit: 200 });
          if (cancelled || !r) return;
          if (r.lines?.length) {
            setLog((prev) => ({ lines: [...prev.lines, ...r.lines], lastTs: r.lastTs }));
          }
        } else if (tab === 'results') {
          const r = await api.automation.crawlerJobResults(job.id, { limit: 200 });
          if (cancelled) return;
          setResults(r);
        }
      } catch (e) { setErr(e?.message || String(e)); }
    };
    tick();
    if (job.status === 'running' || job.status === 'queued') {
      pollRef.current = setInterval(tick, 1500);
    }
    return () => { cancelled = true; clearInterval(pollRef.current); };
  }, [tab, job.id, job.status, log.lastTs]);

  return (
    <div style={{ padding: '0 12px 12px', background: 'var(--bg-2)' }}>
      <div style={{ display: 'flex', gap: 8, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
        <button className={`msb-btn xs ${tab === 'log' ? 'primary' : ''}`} onClick={() => setTab('log')}>Лог</button>
        <button className={`msb-btn xs ${tab === 'results' ? 'primary' : ''}`} onClick={() => setTab('results')}>Результаты</button>
        <button className={`msb-btn xs ${tab === 'spec' ? 'primary' : ''}`} onClick={() => setTab('spec')}>Спека</button>
        <div style={{ flex: 1 }} />
        {(job.status === 'running' || job.status === 'queued') && (
          <button className="msb-btn xs danger" onClick={() => onAbort(job.id)}>⏹ Остановить</button>
        )}
      </div>
      {err && <div className="msb-flash err" style={{ marginTop: 8 }}><span>{err}</span></div>}
      {tab === 'log' && (
        <pre className="msb-scraper-log-pre" style={{ maxHeight: 280 }}>
          {log.lines.length === 0 ? '(пусто — лог ещё не пришёл)' : log.lines.map((l, i) => (
            <div key={i}>
              <span className="msb-mono msb-small">{new Date(l.ts).toLocaleTimeString()}</span>{' '}
              {l.msg}
            </div>
          ))}
        </pre>
      )}
      {tab === 'results' && (
        <pre className="msb-scraper-log-pre" style={{ maxHeight: 280 }}>
          {!results ? 'Загружаю…' : results.results.length === 0 ? '(пусто — walker ещё не собрал данные)' : JSON.stringify(results.results, null, 2)}
        </pre>
      )}
      {tab === 'spec' && (
        <pre className="msb-scraper-log-pre" style={{ maxHeight: 280 }}>{JSON.stringify(job, null, 2)}</pre>
      )}
    </div>
  );
}

// ─── Main panel ─────────────────────────────────────────────────────────────

export default function WalkerPanel({ profiles, running, onRefresh }) {
  const [caps, setCaps] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);

  // Form
  const [profileId, setProfileId] = useState('');
  const [mode, setMode] = useState('crawlee');
  const [urls, setUrls] = useState('https://example.com');
  const [task, setTask] = useState('Open example.com and find the contact email');
  const [maxPages, setMaxPages] = useState(25);
  const [maxDepth, setMaxDepth] = useState(2);
  const [linkPattern, setLinkPattern] = useState('');
  const [extractSelector, setExtractSelector] = useState('');
  const [extractType, setExtractType] = useState('text');
  const [extractAttr, setExtractAttr] = useState('');
  const [extractName, setExtractName] = useState('data');
  const [maxSteps, setMaxSteps] = useState(30);
  const [model, setModel] = useState('');
  const pollRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const [c, js] = await Promise.all([
        api.automation.crawlerCapabilities(),
        api.automation.crawlerJobs(),
      ]);
      setCaps(c || null);
      setJobs(Array.isArray(js) ? js : []);
    } catch (e) { setError(e?.message || String(e)); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    pollRef.current = setInterval(refresh, 3000);
    return () => clearInterval(pollRef.current);
  }, [refresh]);

  // Auto-pick first running profile
  useEffect(() => {
    if (!profileId && running?.length) setProfileId(running[0].id);
  }, [running, profileId]);

  const onStart = async () => {
    setError(null); setInfo(null);
    if (!profileId) { setError('Сначала запусти профиль (вкладка "Профили") и выбери его здесь'); return; }
    if (!running?.find((r) => r.id === profileId)) {
      setError('Профиль не запущен. Нажми ▶ в таблице профилей, потом приходи сюда.'); return;
    }
    if (mode === 'crawlee' && caps && !caps.crawlee.available) {
      setError('crawlee не установлен. Выполни в корне MSB: npm install'); return;
    }
    if (mode === 'llm' && caps && !caps.browserUse.available) {
      setError('browser-use не установлен (pip install browser-use) или python не найден'); return;
    }

    setBusy(true);
    try {
      const spec = { profileId, mode };
      if (mode === 'crawlee') {
        spec.urls = urls.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        if (!spec.urls.length) { setError('Укажи хотя бы один URL'); setBusy(false); return; }
        spec.maxPages = Number(maxPages) || 25;
        spec.maxDepth = Number(maxDepth) || 2;
        if (linkPattern.trim()) spec.linkPattern = linkPattern.trim();
        if (extractSelector.trim()) {
          spec.extract = {
            name: extractName.trim() || 'data',
            selector: extractSelector.trim(),
            type: extractType,
          };
          if (extractType === 'attr' && extractAttr.trim()) spec.extract.attr = extractAttr.trim();
        }
      } else {
        spec.task = task.trim();
        if (!spec.task) { setError('Укажи задание для LLM-агента'); setBusy(false); return; }
        spec.maxSteps = Number(maxSteps) || 30;
        if (model.trim()) spec.model = model.trim();
      }
      const r = await api.automation.crawlerStart(spec);
      if (r?.jobId) {
        setInfo(`Walker запущен: ${r.jobId}`);
        setExpanded(r.jobId);
        await refresh();
      }
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const onAbort = async (jobId) => {
    try {
      await api.automation.crawlerAbort(jobId);
      setInfo(`Остановка отправлена: ${jobId.slice(0, 8)}`);
      await refresh();
    } catch (e) { setError(e?.message || String(e)); }
  };

  const runningJobs = jobs.filter((j) => j.status === 'running' || j.status === 'queued');

  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: 24, maxWidth: 1100 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>🚶 Walker (Crawlee + LLM)</h2>
          <div style={{ color: 'var(--text-dim)', fontSize: 12, marginTop: 4 }}>
            Автономный обход сайтов через запущенный профиль. Crawlee ходит по ссылкам и собирает данные; LLM-агент (browser-use) сам кликает и заполняет по текстовому заданию.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {runningJobs.length > 0 && (
            <span className="msb-status-pill ok">
              <span className="msb-status-dot" />
              активных: {runningJobs.length}
            </span>
          )}
          <button className="msb-btn" onClick={refresh}>↻ Обновить</button>
        </div>
      </div>

      {error && <div className="msb-flash err"><span>{error}</span><button onClick={() => setError(null)}>×</button></div>}
      {info && <div className="msb-flash ok"><span>{info}</span><button onClick={() => setInfo(null)}>×</button></div>}

      <Section title="Возможности" hint="Что доступно прямо сейчас. Если что-то не готово — установи и нажми ↻.">
        {caps ? (
          <>
            <Cap available={caps.crawlee.available} label="Crawlee (Node.js)" version={caps.crawlee.version} hint={caps.crawlee.hint} />
            <Cap available={caps.playwright.available} label="playwright-core" version={caps.playwright.version} hint="Должен быть в dependencies MSB" />
            <Cap available={caps.python.available} label="Python" version={caps.python.version} hint={caps.python.hint} />
            <Cap available={caps.browserUse.available} label="browser-use (pip)" version={caps.browserUse.version} hint={caps.browserUse.hint} />
          </>
        ) : (
          <div style={{ padding: 12, color: 'var(--text-dim)' }}>Определяю…</div>
        )}
      </Section>

      <Section title="Запустить walker" hint="Профиль должен быть уже запущен (▶ в таблице профилей). Walker использует его CDP — куки/отпечаток/прокси сохраняются.">
        <Row label="Профиль" hint="Запущенный профиль с CDP">
          <Select
            value={profileId}
            onChange={setProfileId}
            options={[
              { value: '', label: '— выбери профиль —' },
              ...(running || []).map((r) => ({ value: r.id, label: `${r.id.slice(0, 8)} · ${r.engine || '—'}` })),
            ]}
          />
        </Row>
        <Row label="Режим" hint="Crawlee = правила + ссылки, LLM = агент с заданием">
          <Select
            value={mode}
            onChange={(v) => { setMode(v); setError(null); }}
            options={[
              { value: 'crawlee', label: 'Crawlee — обход по ссылкам' },
              { value: 'llm',     label: 'LLM-агент (browser-use)' },
            ]}
          />
        </Row>

        {mode === 'crawlee' && (
          <>
            <Row label="Start URLs" hint="По одному URL на строку">
              <TextInput value={urls} onChange={setUrls} placeholder="https://example.com" rows={3} mono />
            </Row>
            <Row label="Макс. страниц">
              <TextInput value={String(maxPages)} onChange={(v) => setMaxPages(v)} type="number" />
            </Row>
            <Row label="Макс. глубина перехода по ссылкам">
              <TextInput value={String(maxDepth)} onChange={(v) => setMaxDepth(v)} type="number" />
            </Row>
            <Row label="Link pattern" hint="RegExp. Пусто = все ссылки.">
              <TextInput value={linkPattern} onChange={setLinkPattern} placeholder="^https://example\\.com/.*" mono />
            </Row>
            <Row label="Extract selector" hint="CSS-селектор. Пусто = сохранять только title + canonical.">
              <TextInput value={extractSelector} onChange={setExtractSelector} placeholder="h1, .price, a[href]" mono />
            </Row>
            <Row label="Extract type">
              <Select
                value={extractType}
                onChange={setExtractType}
                options={[
                  { value: 'text', label: 'text (innerText)' },
                  { value: 'html', label: 'html (innerHTML)' },
                  { value: 'attr', label: 'attr (getAttribute)' },
                ]}
              />
            </Row>
            {extractType === 'attr' && (
              <Row label="Attribute name">
                <TextInput value={extractAttr} onChange={setExtractAttr} placeholder="href" mono />
              </Row>
            )}
            <Row label="Extract name" hint="Метка поля в результате">
              <TextInput value={extractName} onChange={setExtractName} placeholder="data" />
            </Row>
          </>
        )}

        {mode === 'llm' && (
          <>
            <Row label="Задание для агента" hint="Что должен сделать LLM-агент на сайте">
              <TextInput value={task} onChange={setTask} placeholder="Find contact email and submit a form" rows={3} />
            </Row>
            <Row label="Max steps" hint="Сколько шагов агент может сделать">
              <TextInput value={String(maxSteps)} onChange={(v) => setMaxSteps(v)} type="number" />
            </Row>
            <Row label="LLM model" hint="browser-use по умолчанию. Примеры: gpt-4o, claude-3-5-sonnet-latest">
              <TextInput value={model} onChange={setModel} placeholder="(default)" />
            </Row>
          </>
        )}

        <div style={{ display: 'flex', gap: 8, padding: '12px 0' }}>
          <button className="msb-btn primary" disabled={busy} onClick={onStart}>
            {busy ? '…' : '▶'} Запустить walker
          </button>
          <button className="msb-btn" onClick={() => { setUrls('https://example.com'); setTask('Open example.com and find the contact email'); }}>
            ↺ Сброс
          </button>
        </div>
      </Section>

      <Section
        title="Задачи"
        hint={`Всего: ${jobs.length}, активных: ${runningJobs.length}`}
        right={
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="msb-btn xs" onClick={refresh}>↻</button>
          </div>
        }
      >
        {jobs.length === 0 ? (
          <div style={{ padding: 16, color: 'var(--text-dim)', textAlign: 'center' }}>
            Пока нет задач. Запусти walker выше.
          </div>
        ) : (
          <div style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '20px 90px 1fr 90px 90px 80px',
                gap: 8,
                padding: '8px 12px',
                fontSize: 11,
                color: 'var(--text-dim)',
                background: 'var(--bg-2)',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: 0.5,
              }}
            >
              <span />
              <span>job</span>
              <span>mode · profile</span>
              <span>status</span>
              <span>длит.</span>
              <span style={{ textAlign: 'right' }}>записей</span>
            </div>
            {jobs.map((j) => (
              <JobRow
                key={j.id}
                job={j}
                expanded={expanded === j.id}
                onToggle={() => setExpanded(expanded === j.id ? null : j.id)}
                onAbort={onAbort}
                onSelect={(v) => {
                  setSelected((prev) => {
                    const n = new Set(prev);
                    if (v) n.add(j.id); else n.delete(j.id);
                    return n;
                  });
                }}
                selected={selected.has(j.id)}
              />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
