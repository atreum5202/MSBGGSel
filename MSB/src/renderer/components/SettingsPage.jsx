import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../api.js';

// ─── helpers ────────────────────────────────────────────────────────────────

function Section({ title, children, style }) {
  return (
    <div className="section" style={{ marginBottom: 20, ...style }}>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, hint, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ flex: '0 0 220px' }}>
        <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 500 }}>{label}</div>
        {hint && <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 2 }}>{hint}</div>}
      </div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 44,
        height: 24,
        borderRadius: 12,
        border: 'none',
        background: checked ? 'var(--accent)' : 'var(--bg-active)',
        cursor: 'pointer',
        position: 'relative',
        transition: 'background 0.2s',
        flexShrink: 0,
        padding: 0,
      }}
    >
      <span style={{
        position: 'absolute',
        top: 3,
        left: checked ? 23 : 3,
        width: 18,
        height: 18,
        borderRadius: '50%',
        background: '#fff',
        transition: 'left 0.2s',
        boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
      }} />
    </button>
  );
}

function Input({ value, onChange, placeholder, type = 'text', mono }) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%',
        background: 'var(--bg)',
        color: 'var(--text)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '6px 10px',
        fontSize: 12,
        fontFamily: mono ? 'ui-monospace, "SF Mono", Menlo, Consolas, monospace' : 'inherit',
      }}
    />
  );
}

function Select({ value, onChange, options }) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{
        background: 'var(--bg)',
        color: 'var(--text)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '6px 10px',
        fontSize: 12,
        width: '100%',
      }}
    >
      {options.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function Badge({ color, children }) {
  const colors = {
    green: { bg: 'rgba(21,128,61,0.1)', text: 'var(--ok)', border: 'rgba(21,128,61,0.25)' },
    red:   { bg: 'rgba(196,43,28,0.1)', text: 'var(--err)', border: 'rgba(196,43,28,0.25)' },
    blue:  { bg: 'rgba(47,111,237,0.1)', text: 'var(--accent)', border: 'rgba(47,111,237,0.25)' },
    gray:  { bg: 'var(--bg-3)', text: 'var(--text-dim)', border: 'var(--border)' },
  };
  const c = colors[color] || colors.gray;
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 10,
      fontSize: 11,
      fontWeight: 600,
      background: c.bg,
      color: c.text,
      border: `1px solid ${c.border}`,
    }}>
      {children}
    </span>
  );
}

// ─── Tab buttons ─────────────────────────────────────────────────────────────

const TABS = [
  { id: 'antidetect', label: '🛡 Антидетект' },
  { id: 'api',        label: '🔌 API & Роутинг' },
  { id: 'proxy',      label: '🌐 Прокси' },
  { id: 'fingerprint',label: '🖐 Fingerprint' },
  { id: 'advanced',   label: '⚙️ Дополнительно' },
];

// ─── Main component ───────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [tab, setTab] = useState('antidetect');
  const [saved, setSaved] = useState(false);
  const [apiInfo, setApiInfo] = useState(null);

  // ── Antidetect settings ──
  const [antidetect, setAntidetect] = useState({
    defaultEngine: 'auto',
    humanizeByDefault: true,
    aggressiveFingerprintByDefault: false,
    canvasSpoofing: true,
    webglSpoofing: true,
    audioContextSpoofing: true,
    webrtcPolicy: 'disable',
    timezoneFromProxy: true,
    localeFromProxy: true,
    randomizeViewport: false,
    defaultViewportW: 1366,
    defaultViewportH: 768,
    defaultPlatform: 'Win32',
    defaultLocale: 'en-US',
    defaultTimezone: 'America/New_York',
    cookieWarmEnabled: true,
    supervisorRestarts: true,
    maxRestarts: 3,
  });

  // ── API settings ──
  const [apiSettings, setApiSettings] = useState({
    port: 3717,
    host: '127.0.0.1',
    token: '',
    corsEnabled: true,
    corsOrigins: '*',
    rateLimitEnabled: true,
    rateLimitWindow: 60,
    rateLimitMax: 100,
    swaggerEnabled: true,
    logRequests: false,
  });

  // ── Proxy defaults ──
  const [proxySettings, setProxySettings] = useState({
    defaultProtocol: 'http',
    rotationMode: 'none',
    rotationInterval: 30,
    testUrl: 'https://api.ipify.org?format=json',
    testOnStart: true,
    failOnBadProxy: false,
    geoipEnabled: true,
  });

  // ── Fingerprint defaults ──
  const [fpSettings, setFpSettings] = useState({
    uaStrategy: 'auto',
    customUA: '',
    screenNoise: true,
    fontListSpoofing: true,
    pluginListSpoofing: true,
    hardwareConcurrencyMin: 2,
    hardwareConcurrencyMax: 8,
    deviceMemoryMin: 4,
    deviceMemoryMax: 16,
    languageNoise: false,
  });

  // ── Advanced ──
  const [advanced, setAdvanced] = useState({
    refreshInterval: 8000,
    logLevel: 'info',
    logRetainDays: 7,
    trayOnClose: true,
    silentMode: false,
    updateChannel: 'stable',
    splashScreen: true,
    devTools: false,
  });

  // Fetch current API info on mount
  useEffect(() => {
    fetch('/ui-config')
      .then(r => r.json())
      .then(d => setApiInfo(d))
      .catch(() => {});
  }, []);

  const setA = (patch) => setAntidetect(p => ({ ...p, ...patch }));
  const setApi = (patch) => setApiSettings(p => ({ ...p, ...patch }));
  const setProxy = (patch) => setProxySettings(p => ({ ...p, ...patch }));
  const setFp = (patch) => setFpSettings(p => ({ ...p, ...patch }));
  const setAdv = (patch) => setAdvanced(p => ({ ...p, ...patch }));

  const handleSave = () => {
    // Сохраняем в localStorage как временное хранилище
    // В реальности — отправлять на /api/settings через POST
    try {
      localStorage.setItem('msb_settings', JSON.stringify({
        antidetect, api: apiSettings, proxy: proxySettings,
        fingerprint: fpSettings, advanced,
      }));
    } catch {}
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  // ── Antidetect tab ────────────────────────────────────────────────────────
  const renderAntidetect = () => (
    <>
      <Section title="Движок по умолчанию">
        <Row label="Браузерный движок" hint="Выбирается при создании нового профиля">
          <Select
            value={antidetect.defaultEngine}
            onChange={v => setA({ defaultEngine: v })}
            options={[
              { value: 'auto', label: 'Auto (CloakBrowser → Patchright)' },
              { value: 'cloakbrowser', label: 'CloakBrowser' },
              { value: 'patchright', label: 'Patchright' },
            ]}
          />
        </Row>
        <Row label="Гуманизация по умолчанию" hint="Эмуляция человеческого поведения (движения мыши, задержки)">
          <Toggle checked={antidetect.humanizeByDefault} onChange={v => setA({ humanizeByDefault: v })} />
        </Row>
        <Row label="Агрессивный fingerprint" hint="Canvas / WebGL / AudioContext шум — сильнее, но медленнее">
          <Toggle checked={antidetect.aggressiveFingerprintByDefault} onChange={v => setA({ aggressiveFingerprintByDefault: v })} />
        </Row>
      </Section>

      <Section title="JavaScript Spoofing">
        <Row label="Canvas fingerprint" hint="Добавляет субпиксельный шум в canvas readback">
          <Toggle checked={antidetect.canvasSpoofing} onChange={v => setA({ canvasSpoofing: v })} />
        </Row>
        <Row label="WebGL fingerprint" hint="Патчит UNMASKED_VENDOR_WEBGL и UNMASKED_RENDERER_WEBGL">
          <Toggle checked={antidetect.webglSpoofing} onChange={v => setA({ webglSpoofing: v })} />
        </Row>
        <Row label="AudioContext fingerprint" hint="Добавляет шум в AnalyserNode / OscillatorNode">
          <Toggle checked={antidetect.audioContextSpoofing} onChange={v => setA({ audioContextSpoofing: v })} />
        </Row>
        <Row label="WebRTC политика" hint="Контроль утечки реального IP через WebRTC">
          <Select
            value={antidetect.webrtcPolicy}
            onChange={v => setA({ webrtcPolicy: v })}
            options={[
              { value: 'disable', label: 'Отключить WebRTC полностью' },
              { value: 'fake', label: 'Подставить фейковый IP' },
              { value: 'proxy', label: 'Направить через прокси' },
              { value: 'allow', label: 'Разрешить (не рекомендуется)' },
            ]}
          />
        </Row>
      </Section>

      <Section title="Геолокация и локаль">
        <Row label="Timezone из прокси" hint="Автоматически определяет таймзону по IP прокси при запуске">
          <Toggle checked={antidetect.timezoneFromProxy} onChange={v => setA({ timezoneFromProxy: v })} />
        </Row>
        <Row label="Locale из прокси" hint="Язык и регион из IP прокси (accept-language, navigator.language)">
          <Toggle checked={antidetect.localeFromProxy} onChange={v => setA({ localeFromProxy: v })} />
        </Row>
        <Row label="Таймзона по умолчанию" hint="Используется если прокси не настроен">
          <Input value={antidetect.defaultTimezone} onChange={v => setA({ defaultTimezone: v })} placeholder="America/New_York" />
        </Row>
        <Row label="Локаль по умолчанию">
          <Input value={antidetect.defaultLocale} onChange={v => setA({ defaultLocale: v })} placeholder="en-US" />
        </Row>
      </Section>

      <Section title="Viewport">
        <Row label="Случайный viewport" hint="Небольшое отклонение от стандартных размеров для каждого профиля">
          <Toggle checked={antidetect.randomizeViewport} onChange={v => setA({ randomizeViewport: v })} />
        </Row>
        <Row label="Размер viewport по умолчанию" hint="Ширина × Высота (px)">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input value={antidetect.defaultViewportW} onChange={v => setA({ defaultViewportW: Number(v) })} type="number" />
            <span style={{ color: 'var(--text-dim)' }}>×</span>
            <Input value={antidetect.defaultViewportH} onChange={v => setA({ defaultViewportH: Number(v) })} type="number" />
          </div>
        </Row>
        <Row label="Платформа по умолчанию">
          <Select
            value={antidetect.defaultPlatform}
            onChange={v => setA({ defaultPlatform: v })}
            options={[
              { value: 'Win32', label: 'Win32 (Windows)' },
              { value: 'MacIntel', label: 'MacIntel (macOS)' },
              { value: 'Linux x86_64', label: 'Linux x86_64' },
            ]}
          />
        </Row>
      </Section>

      <Section title="Supervisor">
        <Row label="Автоперезапуск браузеров" hint="Supervisor автоматически перезапускает упавшие профили">
          <Toggle checked={antidetect.supervisorRestarts} onChange={v => setA({ supervisorRestarts: v })} />
        </Row>
        <Row label="Максимум перезапусков" hint="После N падений профиль помечается как сбойный">
          <Input value={antidetect.maxRestarts} onChange={v => setA({ maxRestarts: Number(v) })} type="number" />
        </Row>
      </Section>
    </>
  );

  // ── API tab ───────────────────────────────────────────────────────────────
  const renderApi = () => (
    <>
      <Section title="API сервер">
        <div style={{
          display: 'flex',
          gap: 10,
          padding: '10px 14px',
          background: 'var(--accent-soft)',
          border: '1px solid rgba(47,111,237,0.25)',
          borderRadius: 6,
          marginBottom: 14,
          alignItems: 'center',
          fontSize: 12,
        }}>
          <span style={{ fontSize: 16 }}>ℹ️</span>
          <div>
            Изменение порта и хоста требует перезапуска MSB.
            Swagger UI доступен по адресу{' '}
            <a
              href={`http://${apiSettings.host}:${apiSettings.port}/docs`}
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent)' }}
            >
              http://{apiSettings.host}:{apiSettings.port}/docs
            </a>
          </div>
        </div>

        <Row label="Хост" hint="По умолчанию 127.0.0.1 — доступен только локально">
          <Input value={apiSettings.host} onChange={v => setApi({ host: v })} mono placeholder="127.0.0.1" />
        </Row>
        <Row label="Порт" hint="Порт REST API (MSB_API_PORT)">
          <Input value={apiSettings.port} onChange={v => setApi({ port: Number(v) })} type="number" />
        </Row>
        <Row label="API Token" hint="Bearer-токен для авторизации. Пусто — без авторизации">
          <div style={{ display: 'flex', gap: 8 }}>
            <Input value={apiSettings.token} onChange={v => setApi({ token: v })} mono placeholder="Оставьте пустым для отключения" type="password" />
            <button
              onClick={() => {
                const t = Array.from({length: 32}, () => Math.random().toString(36)[2]).join('');
                setApi({ token: t });
              }}
              style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
            >
              🔑 Генерировать
            </button>
          </div>
          {apiInfo?.token && (
            <div style={{ marginTop: 6 }}>
              <Badge color="green">Токен активен</Badge>
            </div>
          )}
          {!apiInfo?.token && (
            <div style={{ marginTop: 6 }}>
              <Badge color="gray">Без авторизации</Badge>
            </div>
          )}
        </Row>
        <Row label="Swagger UI" hint="Документация и тестирование API прямо в браузере">
          <Toggle checked={apiSettings.swaggerEnabled} onChange={v => setApi({ swaggerEnabled: v })} />
        </Row>
        <Row label="Логировать запросы" hint="DEBUG лог каждого HTTP запроса к API">
          <Toggle checked={apiSettings.logRequests} onChange={v => setApi({ logRequests: v })} />
        </Row>
      </Section>

      <Section title="CORS">
        <Row label="Включить CORS" hint="Разрешить кросс-доменные запросы к API (нужно для веб-интеграций)">
          <Toggle checked={apiSettings.corsEnabled} onChange={v => setApi({ corsEnabled: v })} />
        </Row>
        <Row label="Разрешённые origins" hint="* — любые. Или перечислите через запятую: https://myapp.com,http://localhost:3000">
          <Input
            value={apiSettings.corsOrigins}
            onChange={v => setApi({ corsOrigins: v })}
            placeholder="* или https://myapp.com"
            mono
          />
        </Row>
      </Section>

      <Section title="Rate Limiting">
        <Row label="Включить rate limit" hint="Защита от злоупотреблений (особенно если API открыт в сеть)">
          <Toggle checked={apiSettings.rateLimitEnabled} onChange={v => setApi({ rateLimitEnabled: v })} />
        </Row>
        <Row label="Окно (сек)" hint="Период для подсчёта запросов">
          <Input value={apiSettings.rateLimitWindow} onChange={v => setApi({ rateLimitWindow: Number(v) })} type="number" />
        </Row>
        <Row label="Макс. запросов в окне">
          <Input value={apiSettings.rateLimitMax} onChange={v => setApi({ rateLimitMax: Number(v) })} type="number" />
        </Row>
      </Section>

      <Section title="Доступные роуты">
        <div style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 11, lineHeight: 1.8 }}>
          {[
            ['GET',    '/api/profiles',              'Список профилей'],
            ['POST',   '/api/profiles',              'Создать профиль'],
            ['GET',    '/api/profiles/:id',          'Получить профиль'],
            ['PATCH',  '/api/profiles/:id',          'Обновить профиль'],
            ['DELETE', '/api/profiles/:id',          'Удалить профиль'],
            ['POST',   '/api/profiles/bulk-delete',  'Удалить несколько'],
            ['POST',   '/api/browser/start',         'Запустить браузер'],
            ['POST',   '/api/browser/stop',          'Остановить браузер'],
            ['GET',    '/api/browser/status',        'Статус браузеров'],
            ['GET',    '/api/stats',                 'Статистика профилей'],
            ['GET',    '/api/cookies/:id',           'Куки профиля'],
            ['POST',   '/api/cookies/:id',           'Сохранить куки'],
            ['GET',    '/api/logs',                  'История логов'],
            ['WS',     '/ws/logs',                   'Стрим логов'],
            ['WS',     '/ws/status',                 'Стрим статусов'],
            ['GET',    '/api/agents',                'Список агентов'],
            ['POST',   '/api/agents/:id/prompt',     'Отправить промпт агенту'],
            ['GET',    '/api/network',               'Сетевой статус'],
            ['GET',    '/api/groups',                'Группы профилей'],
            ['POST',   '/api/automation/run',        'Запустить автоматизацию'],
            ['GET',    '/api/monitoring',            'Данные мониторинга'],
            ['GET',    '/api/audit',                 'Лог аудита'],
            ['POST',   '/api/shutdown',              'Завершить MSB'],
            ['GET',    '/docs',                      'Swagger UI'],
          ].map(([method, route, desc]) => {
            const methodColors = {
              GET: '#4ade80', POST: '#3b82f6', PATCH: '#f59e0b',
              DELETE: '#ef4444', WS: '#8b5cf6',
            };
            return (
              <div key={route} style={{
                display: 'grid',
                gridTemplateColumns: '60px 1fr 1fr',
                gap: 8,
                padding: '3px 0',
                borderBottom: '1px solid var(--border)',
              }}>
                <span style={{ color: methodColors[method] || 'var(--text-dim)', fontWeight: 700 }}>{method}</span>
                <span style={{ color: 'var(--accent)' }}>{route}</span>
                <span style={{ color: 'var(--text-dim)' }}>{desc}</span>
              </div>
            );
          })}
        </div>
      </Section>
    </>
  );

  // ── Proxy tab ─────────────────────────────────────────────────────────────
  const renderProxy = () => (
    <>
      <Section title="Прокси по умолчанию">
        <Row label="Протокол по умолчанию">
          <Select
            value={proxySettings.defaultProtocol}
            onChange={v => setProxy({ defaultProtocol: v })}
            options={[
              { value: 'http',   label: 'HTTP' },
              { value: 'https',  label: 'HTTPS' },
              { value: 'socks4', label: 'SOCKS4' },
              { value: 'socks5', label: 'SOCKS5' },
            ]}
          />
        </Row>
        <Row label="URL для проверки прокси" hint="Запрос к этому URL при старте профиля для проверки прокси">
          <Input value={proxySettings.testUrl} onChange={v => setProxy({ testUrl: v })} mono placeholder="https://api.ipify.org?format=json" />
        </Row>
        <Row label="Проверять прокси при запуске" hint="Если прокси недоступен — предупреждение в логе">
          <Toggle checked={proxySettings.testOnStart} onChange={v => setProxy({ testOnStart: v })} />
        </Row>
        <Row label="Стоп при плохом прокси" hint="Не запускать браузер если прокси не работает">
          <Toggle checked={proxySettings.failOnBadProxy} onChange={v => setProxy({ failOnBadProxy: v })} />
        </Row>
        <Row label="GeoIP определение" hint="Определять страну/таймзону по IP прокси (через ipapi.co)">
          <Toggle checked={proxySettings.geoipEnabled} onChange={v => setProxy({ geoipEnabled: v })} />
        </Row>
      </Section>

      <Section title="Ротация прокси">
        <Row label="Режим ротации" hint="Автоматическая смена прокси для профилей без назначенного прокси">
          <Select
            value={proxySettings.rotationMode}
            onChange={v => setProxy({ rotationMode: v })}
            options={[
              { value: 'none',     label: 'Отключена' },
              { value: 'round',    label: 'Round-Robin (по кругу)' },
              { value: 'random',   label: 'Случайная' },
              { value: 'interval', label: 'По интервалу (минуты)' },
            ]}
          />
        </Row>
        {proxySettings.rotationMode === 'interval' && (
          <Row label="Интервал ротации (мин)">
            <Input value={proxySettings.rotationInterval} onChange={v => setProxy({ rotationInterval: Number(v) })} type="number" />
          </Row>
        )}
      </Section>

      <Section title="Формат импорта прокси">
        <div style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.7 }}>
          <div>Поддерживаемые форматы в TXT / CSV:</div>
          <div style={{ fontFamily: 'monospace', background: 'var(--bg-3)', padding: '8px 12px', borderRadius: 4, marginTop: 8, fontSize: 11 }}>
            <div style={{ color: 'var(--text-dim)' }}># Полный URL с авторизацией</div>
            <div>http://user:pass@host:port</div>
            <div>socks5://user:pass@host:port</div>
            <br/>
            <div style={{ color: 'var(--text-dim)' }}># Без авторизации</div>
            <div>http://host:port</div>
            <br/>
            <div style={{ color: 'var(--text-dim)' }}># CSV (host, port, user, pass)</div>
            <div>host,port,user,pass</div>
            <div>host:port:user:pass</div>
          </div>
        </div>
      </Section>
    </>
  );

  // ── Fingerprint tab ───────────────────────────────────────────────────────
  const renderFingerprint = () => (
    <>
      <Section title="User-Agent">
        <Row label="Стратегия UA" hint="Как генерируется User-Agent для новых профилей">
          <Select
            value={fpSettings.uaStrategy}
            onChange={v => setFp({ uaStrategy: v })}
            options={[
              { value: 'auto',   label: 'Авто — последний Chrome под платформу' },
              { value: 'custom', label: 'Фиксированный (задать ниже)' },
              { value: 'random', label: 'Случайный из пула реальных UA' },
            ]}
          />
        </Row>
        {fpSettings.uaStrategy === 'custom' && (
          <Row label="User-Agent строка">
            <Input value={fpSettings.customUA} onChange={v => setFp({ customUA: v })} mono placeholder="Mozilla/5.0 ..." />
          </Row>
        )}
      </Section>

      <Section title="Аппаратные характеристики">
        <Row label="navigator.hardwareConcurrency" hint="Число логических ядер CPU (рандом в диапазоне)">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input value={fpSettings.hardwareConcurrencyMin} onChange={v => setFp({ hardwareConcurrencyMin: Number(v) })} type="number" />
            <span style={{ color: 'var(--text-dim)' }}>–</span>
            <Input value={fpSettings.hardwareConcurrencyMax} onChange={v => setFp({ hardwareConcurrencyMax: Number(v) })} type="number" />
          </div>
        </Row>
        <Row label="navigator.deviceMemory (GB)" hint="Объём RAM (рандом в диапазоне)">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Input value={fpSettings.deviceMemoryMin} onChange={v => setFp({ deviceMemoryMin: Number(v) })} type="number" />
            <span style={{ color: 'var(--text-dim)' }}>–</span>
            <Input value={fpSettings.deviceMemoryMax} onChange={v => setFp({ deviceMemoryMax: Number(v) })} type="number" />
          </div>
        </Row>
      </Section>

      <Section title="Браузерные API">
        <Row label="Screen noise" hint="Незначительные отклонения в screen.colorDepth, pixelDepth">
          <Toggle checked={fpSettings.screenNoise} onChange={v => setFp({ screenNoise: v })} />
        </Row>
        <Row label="Font list spoofing" hint="Ограничивает список шрифтов для уникального отпечатка">
          <Toggle checked={fpSettings.fontListSpoofing} onChange={v => setFp({ fontListSpoofing: v })} />
        </Row>
        <Row label="Plugin list spoofing" hint="Подставляет стандартный список плагинов Chrome">
          <Toggle checked={fpSettings.pluginListSpoofing} onChange={v => setFp({ pluginListSpoofing: v })} />
        </Row>
        <Row label="Language noise" hint="Небольшие вариации в navigator.languages">
          <Toggle checked={fpSettings.languageNoise} onChange={v => setFp({ languageNoise: v })} />
        </Row>
      </Section>
    </>
  );

  // ── Advanced tab ──────────────────────────────────────────────────────────
  const renderAdvanced = () => (
    <>
      <Section title="Приложение">
        <Row label="Интервал обновления статуса" hint="Как часто UI опрашивает API (мс)">
          <Select
            value={advanced.refreshInterval}
            onChange={v => setAdv({ refreshInterval: Number(v) })}
            options={[
              { value: 3000,  label: '3 сек (агрессивно)' },
              { value: 5000,  label: '5 сек' },
              { value: 8000,  label: '8 сек (по умолчанию)' },
              { value: 15000, label: '15 сек' },
              { value: 30000, label: '30 сек' },
            ]}
          />
        </Row>
        <Row label="Свернуть в трей при закрытии" hint="Крестик прячет окно, а не убивает процесс">
          <Toggle checked={advanced.trayOnClose} onChange={v => setAdv({ trayOnClose: v })} />
        </Row>
        <Row label="Тихий старт (Silent mode)" hint="Запуск без показа окна — только трей">
          <Toggle checked={advanced.silentMode} onChange={v => setAdv({ silentMode: v })} />
        </Row>
        <Row label="Splash screen" hint="Показывать загрузочный экран при старте">
          <Toggle checked={advanced.splashScreen} onChange={v => setAdv({ splashScreen: v })} />
        </Row>
        <Row label="DevTools в профилях" hint="Открывать DevTools при старте каждого профиля (для отладки)">
          <Toggle checked={advanced.devTools} onChange={v => setAdv({ devTools: v })} />
        </Row>
      </Section>

      <Section title="Логи">
        <Row label="Уровень логирования">
          <Select
            value={advanced.logLevel}
            onChange={v => setAdv({ logLevel: v })}
            options={[
              { value: 'debug', label: 'DEBUG (всё подряд)' },
              { value: 'info',  label: 'INFO (по умолчанию)' },
              { value: 'warn',  label: 'WARN (только предупреждения+)' },
              { value: 'error', label: 'ERROR (только ошибки)' },
            ]}
          />
        </Row>
        <Row label="Хранить логи (дней)">
          <Input value={advanced.logRetainDays} onChange={v => setAdv({ logRetainDays: Number(v) })} type="number" />
        </Row>
      </Section>

      <Section title="Сброс">
        <Row label="Сбросить все настройки" hint="Вернуть все параметры к значениям по умолчанию">
          <button
            className="danger"
            onClick={() => {
              if (confirm('Сбросить все настройки MSB до значений по умолчанию?')) {
                try { localStorage.removeItem('msb_settings'); } catch {}
                window.location.reload();
              }
            }}
          >
            🔄 Сбросить настройки
          </button>
        </Row>
      </Section>
    </>
  );

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        background: 'var(--bg-2)',
        flexShrink: 0,
      }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>Settings</h2>
          <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-dim)' }}>
            Антидетект, API, прокси, fingerprint
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {saved && (
            <span style={{ fontSize: 12, color: 'var(--ok)', fontWeight: 600 }}>
              ✓ Сохранено
            </span>
          )}
          <button className="primary" onClick={handleSave}>
            💾 Сохранить
          </button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Sidebar tabs */}
        <div style={{
          width: 200,
          flexShrink: 0,
          borderRight: '1px solid var(--border)',
          background: 'var(--bg-2)',
          padding: '8px',
          overflowY: 'auto',
        }}>
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={{
                width: '100%',
                padding: '10px 14px',
                marginBottom: 3,
                border: 'none',
                borderRadius: 6,
                cursor: 'pointer',
                background: tab === t.id ? 'var(--accent-soft)' : 'transparent',
                color: tab === t.id ? 'var(--accent)' : 'var(--text-dim)',
                fontSize: 13,
                fontWeight: tab === t.id ? 600 : 400,
                textAlign: 'left',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { if (tab !== t.id) e.currentTarget.style.background = 'var(--bg-3)'; }}
              onMouseLeave={e => { if (tab !== t.id) e.currentTarget.style.background = 'transparent'; }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 28px' }}>
          {tab === 'antidetect'  && renderAntidetect()}
          {tab === 'api'         && renderApi()}
          {tab === 'proxy'       && renderProxy()}
          {tab === 'fingerprint' && renderFingerprint()}
          {tab === 'advanced'    && renderAdvanced()}
        </div>
      </div>
    </div>
  );
}
