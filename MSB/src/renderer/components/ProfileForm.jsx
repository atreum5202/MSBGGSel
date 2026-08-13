import React, { useState } from 'react';

const DEFAULT_VIEWPORT = { width: 1366, height: 768 };
const DEFAULT_FORM = {
  name: '',
  notes: '',
  engine: 'auto',
  humanize: true,
  aggressiveFingerprint: false,
  startUrl: 'https://bot.sannysoft.com',
  proxyRaw: '',
  fingerprint: {
    userAgent: '',
    platform: 'Win32',
    locale: 'en-US',
    timezone: 'America/New_York',
    viewport: { ...DEFAULT_VIEWPORT },
  },
  account: {
    email: '',
    password: 'Professor.2000',
    tags: '',
    loginStatus: 'unknown',
  },
};

function detectEmailType(email) {
  if (!email) return 'other';
  const lower = email.toLowerCase();
  if (lower.includes('@gmail.')) return 'gmail';
  if (lower.includes('@outlook.') || lower.includes('@hotmail.') || lower.includes('@live.') || lower.includes('@msn.')) return 'outlook';
  return 'other';
}

function fromInitial(initial) {
  if (!initial) return { ...DEFAULT_FORM, account: { ...DEFAULT_FORM.account } };
  return {
    id: initial.id,
    name: initial.name,
    notes: initial.notes || '',
    engine: initial.engine || 'auto',
    humanize: !!initial.humanize,
    aggressiveFingerprint: !!initial.aggressiveFingerprint,
    startUrl: initial.startUrl || '',
    proxyRaw: initial.proxy ? formatProxy(initial.proxy) : '',
    fingerprint: {
      userAgent: initial.fingerprint?.userAgent || '',
      platform: initial.fingerprint?.platform || 'Win32',
      locale: initial.fingerprint?.locale || 'en-US',
      timezone: initial.fingerprint?.timezone || 'America/New_York',
      viewport: initial.fingerprint?.viewport || { ...DEFAULT_VIEWPORT },
    },
    account: {
      email: initial.account?.email || '',
      password: initial.account?.password || 'Professor.2000',
      tags: (initial.account?.tags || []).join('; '),
      loginStatus: initial.account?.loginStatus || 'unknown',
    },
  };
}

function formatProxy(p) {
  const auth = p.username && p.password ? `${p.username}:${p.password}@` : '';
  return `${p.protocol}://${auth}${p.host}:${p.port}`;
}

export default function ProfileForm({ initial, onSave, onCancel }) {
  const [form, setForm] = useState(() => fromInitial(initial));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [showPass, setShowPass] = useState(false);

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const setFp = (patch) => setForm((f) => ({ ...f, fingerprint: { ...f.fingerprint, ...patch } }));
  const setAcc = (patch) => setForm((f) => ({ ...f, account: { ...f.account, ...patch } }));

  // Автозаполнение email из name если name выглядит как email
  const handleNameChange = (val) => {
    set({ name: val });
    if (val.includes('@') && !form.account.email) {
      setAcc({ email: val });
    }
  };

  const submit = async () => {
    setError(null);
    setSaving(true);
    try {
      const tags = form.account.tags.split(/[;,]/).map(t => t.trim()).filter(Boolean);
      const payload = {
        id: form.id,
        name: form.name || 'Untitled',
        notes: form.notes,
        engine: form.engine,
        humanize: form.humanize,
        aggressiveFingerprint: form.aggressiveFingerprint,
        startUrl: form.startUrl,
        proxy: form.proxyRaw.trim() || null,
        fingerprint: {
          userAgent: form.fingerprint.userAgent || undefined,
          platform: form.fingerprint.platform,
          locale: form.fingerprint.locale,
          timezone: form.fingerprint.timezone,
          viewport: {
            width: Number(form.fingerprint.viewport.width) || DEFAULT_VIEWPORT.width,
            height: Number(form.fingerprint.viewport.height) || DEFAULT_VIEWPORT.height,
          },
        },
        account: {
          email: form.account.email.trim(),
          type: detectEmailType(form.account.email.trim()),
          password: form.account.password,
          tags,
          loginStatus: form.account.loginStatus,
        },
      };
      await onSave(payload);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  };

  const statusOptions = [
    { value: 'unknown', label: '— неизвестно' },
    { value: 'ok', label: '✓ Залогинен' },
    { value: 'expired', label: '⚠ Сессия истекла' },
    { value: 'error', label: '✗ Ошибка входа' },
  ];

  const emailType = detectEmailType(form.account.email);

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{initial ? 'Edit profile' : 'New profile'}</h2>
        <div className="grid">

          {/* ── Account section ─────────────────────────────────── */}
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <div style={{
              background: 'var(--bg2, #23272e)',
              border: '1px solid var(--border, #444)',
              borderRadius: 6,
              padding: '10px 14px',
            }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Account
              </div>
              <div className="grid" style={{ gap: 8 }}>
                <div className="field">
                  <label>Email</label>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    {emailType !== 'other' && (
                      <span style={{
                        background: emailType === 'gmail' ? '#ea4335' : '#0078d4',
                        color: '#fff',
                        borderRadius: 3,
                        fontSize: 9,
                        fontWeight: 700,
                        padding: '2px 6px',
                        flexShrink: 0,
                      }}>
                        {emailType === 'gmail' ? 'Gmail' : 'Outlook'}
                      </span>
                    )}
                    <input
                      value={form.account.email}
                      onChange={(e) => setAcc({ email: e.target.value })}
                      placeholder="user@gmail.com"
                      style={{ flex: 1 }}
                    />
                  </div>
                </div>
                <div className="field">
                  <label>Пароль</label>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      type={showPass ? 'text' : 'password'}
                      value={form.account.password}
                      onChange={(e) => setAcc({ password: e.target.value })}
                      style={{ flex: 1 }}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPass(v => !v)}
                      style={{ padding: '4px 8px', fontSize: 12 }}
                      title={showPass ? 'Скрыть' : 'Показать'}
                    >
                      {showPass ? '🙈' : '👁'}
                    </button>
                  </div>
                </div>
                <div className="field">
                  <label>Теги (через ; или ,)</label>
                  <input
                    value={form.account.tags}
                    onChange={(e) => setAcc({ tags: e.target.value })}
                    placeholder="Claude; Cursor; Minimax"
                  />
                </div>
                <div className="field">
                  <label>Статус входа</label>
                  <select
                    value={form.account.loginStatus}
                    onChange={(e) => setAcc({ loginStatus: e.target.value })}
                  >
                    {statusOptions.map(s => (
                      <option key={s.value} value={s.value}>{s.label}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </div>

          {/* ── Основные поля ───────────────────────────────────── */}
          <div className="field">
            <label>Name</label>
            <input value={form.name} onChange={(e) => handleNameChange(e.target.value)} />
          </div>
          <div className="field">
            <label>Engine</label>
            <select value={form.engine} onChange={(e) => set({ engine: e.target.value })}>
              <option value="auto">Auto (CloakBrowser → Patchright)</option>
              <option value="cloakbrowser">CloakBrowser</option>
              <option value="patchright">Patchright</option>
            </select>
          </div>
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <label>Start URL</label>
            <input value={form.startUrl} onChange={(e) => set({ startUrl: e.target.value })} />
          </div>
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <label>Notes</label>
            <textarea value={form.notes} onChange={(e) => set({ notes: e.target.value })} />
          </div>
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <label>Proxy (http://user:pass@host:port or socks5://…)</label>
            <input
              value={form.proxyRaw}
              onChange={(e) => set({ proxyRaw: e.target.value })}
              placeholder="Leave empty for direct connection"
            />
            <div className="hint">
              Residential/mobile proxies recommended. Timezone/locale auto-detected from the proxy IP at start.
            </div>
          </div>
          <div className="field">
            <label>Platform</label>
            <select value={form.fingerprint.platform} onChange={(e) => setFp({ platform: e.target.value })}>
              <option value="Win32">Win32</option>
              <option value="MacIntel">MacIntel</option>
              <option value="Linux x86_64">Linux x86_64</option>
            </select>
          </div>
          <div className="field">
            <label>Locale</label>
            <input value={form.fingerprint.locale} onChange={(e) => setFp({ locale: e.target.value })} />
          </div>
          <div className="field">
            <label>Timezone</label>
            <input value={form.fingerprint.timezone} onChange={(e) => setFp({ timezone: e.target.value })} />
          </div>
          <div className="field">
            <label>Viewport (WxH)</label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type="number"
                value={form.fingerprint.viewport.width}
                onChange={(e) => setFp({ viewport: { ...form.fingerprint.viewport, width: e.target.value } })}
              />
              <input
                type="number"
                value={form.fingerprint.viewport.height}
                onChange={(e) => setFp({ viewport: { ...form.fingerprint.viewport, height: e.target.value } })}
              />
            </div>
          </div>
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <label>User-Agent (leave blank to auto-generate)</label>
            <input value={form.fingerprint.userAgent} onChange={(e) => setFp({ userAgent: e.target.value })} />
          </div>
          <div className="field" style={{ gridColumn: 'span 2' }}>
            <div className="checkbox-row">
              <input id="humanize" type="checkbox" checked={form.humanize} onChange={(e) => set({ humanize: e.target.checked })} />
              <label htmlFor="humanize" style={{ margin: 0 }}>Enable humanization</label>
            </div>
            <div className="checkbox-row">
              <input id="agg" type="checkbox" checked={form.aggressiveFingerprint} onChange={(e) => set({ aggressiveFingerprint: e.target.checked })} />
              <label htmlFor="agg" style={{ margin: 0 }}>Aggressive JS fingerprint spoof (Canvas/WebGL/Audio noise)</label>
            </div>
          </div>
        </div>

        {error && <div style={{ color: 'var(--danger)', marginTop: 10 }}>{error}</div>}
        <div className="footer">
          <button onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="primary" onClick={submit} disabled={saving}>
            {saving ? 'Saving…' : initial ? 'Save' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}
