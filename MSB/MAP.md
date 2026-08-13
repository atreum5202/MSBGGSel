# MAP.md — Визуальная карта MSBWorkshop

> Обзор всего: структура файлов, слои, потоки данных, связи между проектами.
> Детали API → `AGENT.md`. Операционные правила → `LLM_MAP.md`. Задачи → `TASK.md`.

---

## Структура корневой папки

```
C:\Users\Atreum\Desktop\MSBWorkshop\
  ├── MSB\           ← антидетект-браузер (Electron + Fastify + React)
  └── GGselMSB\      ← Flask-панель продавца (Python, отдельный проект)
```

Проекты **независимые**. GGselMSB вызывает MSB через REST API на `http://127.0.0.1:17248`.

---

## MSB — Карта файлов

```
MSB\
├── src\
│   ├── main\                        ← Electron main process
│   │   ├── index.js                 ← точка входа, запускает Fastify + Electron window
│   │   ├── api\
│   │   │   ├── server.js            ← Fastify, регистрирует все routes
│   │   │   ├── auth.js              ← Bearer-токен авторизация
│   │   │   ├── validate.js          ← Ajv schemas (profile-create, profile-patch)
│   │   │   ├── envelope.js          ← MoreLogin dual-envelope opt-in
│   │   │   └── routes\
│   │   │       ├── profiles.js      ← CRUD + bulk + recycle bin
│   │   │       ├── groups.js        ← агрегация групп + group-meta.json
│   │   │       ├── browser.js       ← start/stop/status + /api/env/* aliases
│   │   │       ├── health.js        ← /health + /health/live
│   │   │       ├── traffic.js       ← mitmproxy capture (TrafficCaptureService)
│   │   │       ├── networkTraffic.js← CDP ring buffer (NetworkCaptureService)
│   │   │       ├── network.js       ← WebRTC/DNS/headers/TLS settings
│   │   │       ├── automation.js    ← pipeline runner (AutomationService)
│   │   │       ├── crawler.js       ← Crawlee + browser-use (CrawlerService)
│   │   │       ├── agents.js        ← AI agents (chatgpt/claude/gemini/minimax)
│   │   │       ├── anthropicAdapter.js ← POST /v1/messages → Clewd proxy
│   │   │       ├── workspace.js     ← /workspace/launch + /workspace/status
│   │   │       ├── storage.js       ← localStorage/sessionStorage/IDB/history
│   │   │       ├── events.js        ← WebSocket /ws/events
│   │   │       ├── cookies.js       ← cookie import/export
│   │   │       ├── extensions.js    ← common extensions manager
│   │   │       ├── proxies.js       ← proxy list
│   │   │       ├── warmer.js        ← Cookie Warmer (Qrator)
│   │   │       ├── audit.js         ← audit log
│   │   │       ├── monitoring.js    ← monitoring endpoints
│   │   │       ├── stats.js         ← statistics
│   │   │       ├── logs.js          ← log streaming
│   │   │       ├── shutdown.js      ← graceful shutdown
│   │   │       └── status.js        ← server status
│   │   ├── services\
│   │   │   ├── profileManager.js    ← CRUD + кэш + number assignment
│   │   │   ├── profile\
│   │   │   │   ├── ProfileStorage.js← stateless disk IO (atomic, migration)
│   │   │   │   └── TrashService.js  ← recycle bin (7-day, sweep 6h)
│   │   │   ├── browserLauncher\
│   │   │   │   ├── index.js         ← BrowserLauncher (start/stop/CDP/proxy/fingerprint)
│   │   │   │   ├── engineLoader.js  ← lazy Patchright / CloakBrowser
│   │   │   │   ├── profileBootstrap.js ← prefs, autofill, badge, seed
│   │   │   │   ├── downloads.js     ← download handler
│   │   │   │   ├── utils.js         ← getFreePort, waitForCdpJson, safeUrl
│   │   │   │   └── constants.js     ← COMMON_ARGS, CLOAK_COMMON_ARGS
│   │   │   ├── networkCapture.js    ← CDP ring buffer (2000 req/profile, NDJSON flush)
│   │   │   ├── trafficCapture.js    ← mitmdump subprocess (18100–18200)
│   │   │   ├── automationService.js ← pipeline jobs (in-memory)
│   │   │   ├── crawlerService.js    ← Crawlee (JS) + browser-use (Python)
│   │   │   ├── cookieStore.js       ← cookie snapshots
│   │   │   ├── loginDataManager.js  ← login data
│   │   │   ├── consoleLogBuffer.js  ← console log ring buffer
│   │   │   ├── commonExtensionsManager.js
│   │   │   ├── supervisor.js        ← crash detection + auto-restart
│   │   │   ├── geoip.js, statistics.js, proxyStore.js, ...
│   │   │   └── legacyProfileImport.js
│   │   ├── lib\
│   │   │   ├── atomicFs.js          ← write-tmp + fsync + rename (ТОЛЬКО через него!)
│   │   │   ├── profileSchema.js     ← PROFILE_SCHEMA_VERSION + migrateProfile v0→v1
│   │   │   ├── requestContext.js    ← AsyncLocalStorage (X-Request-Id)
│   │   │   ├── fingerprint.js       ← fingerprint generation + injection
│   │   │   ├── proxy.js             ← normalization + switchProxy()
│   │   │   ├── profileCrypto.js     ← E2E encryption (account.password/notes/tokens)
│   │   │   ├── humanize.js          ← human-like delays/mouse
│   │   │   ├── rateLimiter.js       ← 60 req/min per (IP, token)
│   │   │   ├── auditLogger.js       ← audit log
│   │   │   ├── captcha.js           ← captcha handling
│   │   │   ├── ipCheck.js           ← IP leak detection
│   │   │   ├── eventBus.js          ← shared EventEmitter
│   │   │   ├── ws.js                ← WebSocket helpers (trySend)
│   │   │   └── scenarios\
│   │   │       ├── index.js         ← registry + runScenario() + listScenarios()
│   │   │       ├── googleLogin.js
│   │   │       ├── ggselLogin.js
│   │   │       ├── chatgptLogin.js
│   │   │       ├── botSannysoft.js
│   │   │       └── aiProviderSend.js← makeAiProviderScenario(provider)
│   │   ├── core\
│   │   │   ├── paths.js             ← все пути (profileDir, userDataDir, ...)
│   │   │   ├── constants.js         ← DEFAULTS, ENGINES, IPC
│   │   │   └── logger.js            ← Pino
│   │   ├── ipc\                     ← Electron IPC handlers
│   │   └── ui\                      ← UI helpers
│   ├── renderer\                    ← React 18 + Vite
│   │   ├── App.jsx                  ← root, навигация, состояние
│   │   ├── api.js                   ← HTTP-клиент (api.profiles, api.groups, ...)
│   │   ├── styles.css               ← CSS variables, dark theme
│   │   └── components\
│   │       ├── Sidebar.jsx          ← навигация (collapsible, persisted)
│   │       ├── Topbar.jsx           ← theme toggle, workspace button
│   │       ├── ProfileTable.jsx     ← data-dense таблица (sortable, checkboxes)
│   │       ├── ProfileDetail.jsx    ← right drawer
│   │       ├── ProfileForm.jsx      ← create/edit форма
│   │       ├── TrashModal.jsx       ← recycle bin UI
│   │       ├── CookieWarmer.jsx     ← Cookie Warmer UI
│   │       ├── CommonExtensions.jsx ← extensions manager
│   │       └── ProxyList.jsx        ← proxy list view
│   └── preload\                     ← Electron preload scripts
├── profiles\     ← index.json + <id>/meta.json + <id>/userData/ (APPDATA по умолчанию)
├── captures\     ← CDP NDJSON + mitmproxy .mitm flow files
├── logs\         ← pino logs
├── extensions\
│   └── msb-profile-badge\   ← Chrome extension (overlay в браузере)
├── tests\
│   ├── profileStorage.test.js  ← 17 тестов (ProfileStorage/TrashService/atomicFs/schema)
│   ├── profileNumber.test.js
│   ├── proxyStore.test.js
│   └── badgeContext.test.js
├── AGENT.md      ← ГЛАВНАЯ ДОКА (API reference, data model, стек)
├── LLM_MAP.md    ← операционные правила, грабли, команды
├── TASK.md       ← задачи, статус, тупики
├── MAP.md        ← этот файл
├── README.md     ← верхнеуровневый tour
├── start.vbs     ← запуск (relative paths, не требует изменений при переезде)
├── stop.bat      ← остановка
├── package.json
└── vite.config.js
```

---

## GGselMSB — Карта файлов

```
GGselMSB\
├── app.py               ← Flask точка входа, регистрирует blueprints
├── config.py            ← все настройки (MSB_API_BASE, GGSEL_API_KEY, ...)
├── start.vbs            ← запуск Flask + бот + watchdog (relative paths)
├── restart_flask.bat    ← перезапуск Flask
├── watchdog.py          ← мониторинг Flask + MSB, авторестарт
├── cookie_autorefresh.py
├── cookie_status_routes.py
├── warmer_routes.py
├── parser_config.json   ← конфиг парсера (категории, лимиты, Gemini группа)
├── parser\
│   ├── routes.py        ← все /api/parser/* (~1500 строк)
│   ├── parser_engine.py ← оркестратор, CascadeFetcher (MSB → cffi fallback)
│   ├── db_init.py       ← SQLite init, get_db_path() → data/db/parser.db
│   ├── schema.sql       ← DDL схема БД
│   ├── economics.py     ← расчёт маржи и цен
│   ├── content_gen.py   ← AI генерация текстов (Gemini API, ротация ключей)
│   ├── gemini_browser.py← автоматизация gemini.google.com через MSB CDP
│   ├── ggsel_publisher.py← публикация в GGSEL Seller API
│   ├── msb_client.py    ← REST-клиент к MSB API
│   ├── msb_cookies.py   ← Qrator-куки через MSB CDP
│   ├── msb_fetcher.py   ← HTTP через MSB профили + rate limiter
│   ├── profile_pool.py  ← пул MSB профилей
│   ├── adaptive_rate_limiter.py
│   ├── dedup.py, event_logger.py, telemetry.py, scheduler.py
│   ├── competitor_scanner.py, my_shop_scraper.py
│   ├── category_catalog.py, order_processor.py
│   ├── captcha_handler.py, tg_bot.py
│   └── ...
├── bot\                 ← Telegram бот (aiogram 3.x)
├── static\
│   ├── app.js           ← весь frontend JS (~4500 строк)
│   └── style.css
├── templates\
│   └── index.html       ← единственный HTML (SPA)
├── data\
│   └── db\parser.db     ← SQLite БД (единственный канонический путь)
├── logs\                ← app.log, bot.log, watchdog.log
└── tests\
```

---

## Слои и зависимости

```
┌─────────────────────────────────────────────────────────────────────┐
│  GGselMSB (Flask :5000)                                             │
│  parser/msb_client.py  ─────────────────────────────────────────┐  │
│  parser/gemini_browser.py  ──────────────────────────────────┐  │  │
│  parser/msb_fetcher.py  ─────────────────────────────────┐   │  │  │
└──────────────────────────────────────────────────────────│───│──│──┘
                                                           │   │  │
                              REST API http://127.0.0.1:17248  │  │
                                                           │   │  │
┌──────────────────────────────────────────────────────────▼───▼──▼──┐
│  MSB (Fastify :17248)                                               │
│                                                                     │
│  ProfileManager ─── ProfileStorage (disk IO)                        │
│                  └─ TrashService (recycle bin)                      │
│                                                                     │
│  BrowserLauncher ── engineLoader (Patchright / CloakBrowser)        │
│                  ├─ profileBootstrap (prefs, fingerprint, badge)    │
│                  ├─ downloads                                        │
│                  └─ utils (CDP, port, url)                          │
│                                                                     │
│  NetworkCaptureService ← CDP ring buffer (2000 req, NDJSON)         │
│  TrafficCaptureService ← mitmdump subprocess (ports 18100–18200)    │
│  AutomationService     ← pipeline runner                            │
│  CrawlerService        ← Crawlee + browser-use                      │
│                                                                     │
│  Lib: atomicFs, fingerprint, proxy, profileCrypto, humanize,        │
│       captcha, ipCheck, eventBus, rateLimiter, requestContext        │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼ CDP (WebSocket)
┌─────────────────────────────┐
│  Chromium Browsers           │
│  (Patchright / CloakBrowser) │
└─────────────────────────────┘
```

---

## Поток данных — основные сценарии

### 1. Запуск профиля + перехват трафика

```
Client → POST /profiles/:id/start
  → BrowserLauncher.start()
    → engineLoader (lazy init)
    → profileBootstrap (prefs, fingerprint, badge)
    → CDP attach → NetworkCaptureService.attach()  ← автоматически
  → ответ: { wsEndpoint, page, ... }

Client → POST /profiles/:id/traffic/start
  → TrafficCaptureService.start()
    → getFreePort (18100–18200)
    → spawn mitmdump
    → switchProxy() → browser → mitmdump → upstream
  → ответ: { port, captureDir, ... }
```

### 2. GGselMSB парсит ggsel.net через MSB

```
GGselMSB → msb_client.py
  → POST /profiles/:id/start    (запустить профиль)
  → GET  /profiles/:id/cookies  (получить Qrator-куки)
  → msb_fetcher.py → HTTP через CDP профиля
  → POST /profiles/:id/stop     (вернуть в пул)
```

### 3. Gemini рестайл фото через MSB

```
GGselMSB → gemini_browser.py
  → POST /api/agents/:id/start      (запустить Gemini профиль)
  → POST /api/agents/:id/navigate   (перейти на gemini.google.com)
  → POST /api/agents/:id/prompt     (отправить промпт с фото)
  → GET  /api/agents/:id/response/latest
```

### 4. Pipeline автоматизации

```
Client → POST /automation/pipeline/run { steps: [...] }
  → AutomationService.runPipeline()
    → step: create  → ProfileManager.create()
    → step: start   → BrowserLauncher.start()
    → step: crawl   → CrawlerService.startWalker() (Crawlee)
    → step: llm     → CrawlerService.startWalker() (browser-use Python)
    → step: traffic → TrafficCaptureService.start()
    → step: network → NetworkCaptureService.list/har/endpoints
    → step: stop    → BrowserLauncher.stop()
```

---

## Ключевые константы

| Константа | Значение | Где менять |
|---|---|---|
| API Port | 17248 | Менять НЕЛЬЗЯ (GGSeller, Combined_Widget) |
| Ring buffer | 2000 req | `networkCapture.js` RING_SIZE |
| Traffic ports | 18100–18200 | `trafficCapture.js` PORT_MIN/PORT_MAX |
| Trash retention | 7 дней | `TrashService.js` |
| Rate limit | 60 req/min | `rateLimiter.js`, override `MSB_RATE_LIMIT=0` |
| Schema version | 1 | `profileSchema.js` PROFILE_SCHEMA_VERSION |
| Profiles dir | `%APPDATA%\MSB\profiles` | env `MSB_PROFILES_DIR` |
| GGselMSB path | `MSBWorkshop\GGselMSB` | `constants.js` GGSELLER_PROJECT_DIR |

---

## Тесты быстрого запуска

```powershell
# MSB тесты
cd C:\Users\Atreum\Desktop\MSBWorkshop\MSB
node --test tests/profileStorage.test.js   # 17/17
node --test tests/**/*.test.js             # все

# Проверка синтаксиса
node --check src/main/index.js

# Сборка renderer
npm run build:renderer

# GGselMSB тесты
cd C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB
python -m pytest tests/ -v
```
