# MSB — Agent Reference

> Читай этот файл ПЕРВЫМ, прежде чем вносить любые изменения в проект.
>
> **Doc layout**:
> - `README.md` — верхнеуровневый tour (architecture, engines, quick start).
> - `AGENT.md` (этот файл) — канонический стек + data model + полный API reference.
> - `LLM_MAP.md` — операционные правила, грабли, команды запуска.
> - `TASK.md` — текущие задачи, статус, тупики.
> - `MAP.md` — визуальная карта проекта (файлы, слои, потоки).
>
> **GGselMSB** (отдельный проект): `project/GGselMSB/AGENTS.md` — его собственный reference.
> MSB и GGselMSB — независимые проекты. GGselMSB вызывает MSB через REST на порту 17248.

---

## Stack

Electron + React (Vite) + Node.js HTTP API (Fastify). Стандарт ESM (`"type": "module"`).
- **Renderer**: React 18 + Vite, dev-сервер на `http://localhost:5173`
- **Main process**: Electron + Fastify на порту `17248`
- **Auth**: Bearer-токен из `GET /ui-config` (см. `src/renderer/api.js`)

---

## Service Architecture (слои)

```
┌─────────────────────────────────────────────────────────┐
│  REST API  (Fastify :17248)                             │
│  src/main/api/routes/*.js                               │
└─────────┬───────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────┐
│  Service Layer (Tier 1+2)                               │
│                                                         │
│  ProfileManager                                         │
│    ├── ProfileStorage   ← disk IO, schema migration     │
│    └── TrashService     ← recycle bin (7-day)           │
│                                                         │
│  BrowserLauncher                                        │
│    ├── engineLoader     ← Patchright / CloakBrowser     │
│    ├── profileBootstrap ← prefs, badge, fingerprint     │
│    ├── downloads        ← auto-save downloads           │
│    └── utils            ← port, CDP, url helpers        │
│                                                         │
│  NetworkCaptureService  ← CDP ring buffer (in-memory)   │
│  TrafficCaptureService  ← mitmproxy subprocess          │
│  AutomationService      ← pipeline runner               │
│  CrawlerService         ← Crawlee + browser-use walker  │
└─────────────────────────────────────────────────────────┘
          │
┌─────────▼───────────────────────────────────────────────┐
│  Lib Layer                                              │
│  atomicFs, profileSchema, fingerprint, proxy,           │
│  profileCrypto, humanize, auditLogger, captcha,         │
│  ipCheck, rateLimiter, requestContext, eventBus, ws     │
└─────────────────────────────────────────────────────────┘
```

---

## Stability Layer (Tier 1, 2026-08-03)

| Module | Purpose |
|---|---|
| `src/main/lib/atomicFs.js` | `atomicWriteJson` / `readJsonSafe` / `atomicUpdateJson` — write-tmp + fsync + rename. Защита от corruption при crash mid-write. |
| `src/main/lib/profileSchema.js` | `PROFILE_SCHEMA_VERSION` + `migrateProfile` (v0→v1) + `validateProfileShape`. Старые профили апгрейдятся на лету при чтении. |
| `src/main/api/validate.js` | Ajv-based validators: `validateBody('profile-create')`, `validatePatch('profile-patch')`. PATCH запрещает server-managed поля (`id`, `schemaVersion`, `number`, …). |
| `src/main/lib/requestContext.js` | AsyncLocalStorage-based request-id propagation. `X-Request-Id` header → log bindings через всю цепочку. |
| `src/main/api/routes/health.js` | `/health` (readiness) + `/health/live` (liveness). Проверяет profilesDir, disk space, engines, schema versions, running browsers, memory. |

Все profile-persistence (writeMeta, writeIndex, trash manifest, restore meta) идут через `atomicFs`. PATCH/POST профилей валидируются Ajv. Каждый ответ несёт `X-Request-Id`.

---

## Service Layer (Tier 2, 2026-08-04)

profileManager больше не god-object: ответственности разбиты по классам.

| Class / File | Ответственность |
|---|---|
| `src/main/services/profile/ProfileStorage.js` | Низкоуровневый IO: read/write meta.json, index.json, scan orphans, trash dir primitives. Stateless, без кэша и бизнес-правил. |
| `src/main/services/profile/TrashService.js` | Recycle bin: soft-delete (trash), restore, purge, list с retention countdown, purgeExpired (7-day sweep). Делегирует IO в ProfileStorage. |
| `src/main/services/profileManager.js` | Главный сервис: CRUD с кэшем, migration на init(), number assignment, group/tags/provider aggregation. Использует ProfileStorage и TrashService. |
| `src/main/services/browserLauncher/index.js` | Запуск/остановка браузеров, CDP, cookie snapshot, proxy switching, fingerprint injection, humanize. |
| `src/main/services/browserLauncher/engineLoader.js` | Lazy load Patchright / CloakBrowser engines. |
| `src/main/services/browserLauncher/profileBootstrap.js` | Prefs, autofill scripts, badge context, fingerprintSeed. |
| `src/main/services/browserLauncher/downloads.js` | Download handler (автосохранение). |
| `src/main/services/browserLauncher/utils.js` | getFreePort, waitForCdpJson, pickEngine, safeUrl, normalizeNavigationInput. |

---

## Network & Traffic Layer (Tier 3, новый)

| Class / File | Ответственность |
|---|---|
| `src/main/services/networkCapture.js` | `NetworkCaptureService` — in-memory ring buffer CDP-запросов профиля. Attach/detach к CDP-сессии. Методы: `attach(profileId, page)`, `detach(profileId)`, `list(id, opts)`, `get(id, n)`, `endpoints(id, opts)`, `toHar(id, opts)`, `clear(id)`, `status(id)`, `sessions()`. Кольцевой буфер 2000 записей. Сброс на диск при `FLUSH_AT` — NDJSON в `captures/<id>/`. |
| `src/main/services/trafficCapture.js` | `TrafficCaptureService` — mitmproxy-based перехват. Запускает `mitmdump` как дочерний процесс. Поддерживает upstream-прокси (цепочка: браузер→mitmdump→upstream). Сохраняет `.mitm` flow file и опционально `.har`. |
| `src/main/services/automationService.js` | `AutomationService` — pipeline runner для скрейперов. Типы шагов: `create`, `start`, `stop`, `wait`, `http`, `eval`, `screenshot`, `traffic`, `network`, `crawl`, `llm`. |
| `src/main/services/crawlerService.js` | `CrawlerService` — Crawlee walker (JS) + browser-use walker (Python/LLM). Методы: `capabilities()`, `startWalker(spec)`, `jobStatus(id)`, `jobLog(id, opts)`, `jobResults(id, opts)`, `abort(id)`, `listJobs()`. |

---

## Automation & AI Layer (Tier 4, новый)

| File | Ответственность |
|---|---|
| `src/main/api/routes/agents.js` | AI-агент абстракция поверх профилей. Providers: chatgpt, claude, gemini, minimax. Методы: start/stop/navigate/prompt/interrupt, `/response/latest`, `/responding`. |
| `src/main/api/routes/anthropicAdapter.js` | Адаптер Anthropic API → Clewd (http://127.0.0.1:8444). Поддерживает streaming и non-streaming режимы. Парсит `<tool_use>` XML из Clewd-ответа обратно в Anthropic tool_use блоки. Точка: `POST /v1/messages`. |
| `src/main/api/routes/workspace.js` | One-click запуск GGselMSB. Обеспечивает профиль "AI Workspace", поднимает Flask (`python app.py`) если не отвечает, открывает браузер. Точки: `POST /workspace/launch`, `GET /workspace/status`. |
| `src/main/lib/scenarios/` | Сценарии автоматизации: `google-login`, `ggsel-login`, `bot-sannysoft`, `chatgpt-login`, `chatgpt-send`, `claude-send`, `gemini-send`, `minimax-send`. Запускаются через `browserLauncher.runScenario(profileId, name, ctx)`. |

---

## Tests

```
tests/
  profileStorage.test.js   ← 17 тестов: ProfileStorage, TrashService, atomicFs, schema migration
  profileNumber.test.js    ← number uniqueness, reuse prevention
  proxyStore.test.js       ← proxy CRUD
  badgeContext.test.js     ← MSB Profile Badge context

node --test tests/profileStorage.test.js   # 17/17 ✅
node --test tests/**/*.test.js             # все тесты
```

---

## Project Layout

```
src/main/
  index.js                   ← точка входа Electron (main process)
  api/
    server.js                ← Fastify-сервер, регистрирует все роуты
    auth.js                  ← Bearer-токен авторизация
    validate.js              ← Ajv schemas (profile-create, profile-patch)
    envelope.js              ← MoreLogin dual-envelope opt-in
    routes/
      profiles.js            ← CRUD + bulk-delete + check-proxy + recycle bin
      groups.js              ← агрегация групп по profile.group + group-meta.json
      browser.js             ← start/stop/status + /api/env/* aliases
      health.js              ← /health + /health/live
      warmer.js              ← Cookie Warmer (Qrator-прогрев)
      traffic.js             ← TrafficCapture REST (mitmproxy)
      networkTraffic.js      ← NetworkCapture REST (CDP ring buffer)
      network.js             ← Network settings (WebRTC, DNS, headers, TLS)
      automation.js          ← AutomationService REST (pipeline runner)
      crawler.js             ← CrawlerService REST (Crawlee + browser-use)
      agents.js              ← AI Agent абстракция (chatgpt/claude/gemini/minimax)
      anthropicAdapter.js    ← POST /v1/messages → Clewd proxy
      workspace.js           ← /workspace/launch + /workspace/status
      storage.js             ← Browser storage access (localStorage/sessionStorage/IDB/history)
      events.js              ← WebSocket /ws/events (profile lifecycle events)
      cookies.js             ← Cookie import/export
      extensions.js          ← Common extensions manager
      proxies.js             ← Proxy list
      audit.js               ← Audit log
      monitoring.js          ← Monitoring endpoints
      stats.js               ← Statistics
      logs.js                ← Log streaming
      shutdown.js            ← Graceful shutdown
      status.js              ← Status
  services/
    profileManager.js        ← CRUD с кэшем, оркестрирует Storage + Trash
    profile/
      ProfileStorage.js      ← disk IO (atomic, schema migration)
      TrashService.js        ← recycle bin logic
    browserLauncher/
      index.js               ← BrowserLauncher class
      engineLoader.js        ← lazy engine init
      profileBootstrap.js    ← prefs, autofill, badge, fingerprintSeed
      downloads.js           ← download handler
      utils.js               ← port, CDP, url helpers
      constants.js           ← COMMON_ARGS, CLOAK_COMMON_ARGS
    networkCapture.js        ← CDP ring buffer (in-memory, per-profile)
    trafficCapture.js        ← mitmproxy subprocess manager
    automationService.js     ← pipeline runner (jobs, steps)
    crawlerService.js        ← Crawlee + browser-use walkers
    cookieStore.js           ← cookie snapshots
    loginDataManager.js      ← login data manager
    consoleLogBuffer.js      ← console log buffer
    commonExtensionsManager.js
    supervisor.js            ← crash detection + restart
    statistics.js, geoip.js, proxyStore.js, ...
  lib/
    atomicFs.js              ← write-tmp + fsync + rename, readJsonSafe
    profileSchema.js         ← PROFILE_SCHEMA_VERSION, migrateProfile
    requestContext.js        ← AsyncLocalStorage request-id
    fingerprint.js           ← fingerprint generation + injection
    proxy.js                 ← proxy normalization + switching
    profileCrypto.js         ← E2E encryption
    humanize.js              ← human-like delays/movements
    rateLimiter.js           ← 60 req/min per (IP, token)
    auditLogger.js           ← audit log
    captcha.js               ← captcha handling
    ipCheck.js               ← IP leak detection
    eventBus.js              ← EventEmitter shared bus
    ws.js                    ← WebSocket helpers (trySend)
    scenarios/               ← browser automation scenarios
      index.js               ← registry + runScenario() + listScenarios()
      googleLogin.js
      ggselLogin.js
      chatgptLogin.js
      botSannysoft.js
      aiProviderSend.js      ← makeAiProviderScenario(provider)
  core/
    paths.js                 ← все пути (profileDir, userDataDir, ...)
    constants.js             ← DEFAULTS, ENGINES
    logger.js                ← Pino
  ipc/                       ← Electron IPC handlers
  ui/                        ← UI helpers
src/renderer/
  App.jsx                    ← корневой компонент, навигация, состояние
  api.js                     ← HTTP-клиент (api.profiles, api.groups, ...)
  styles.css                 ← CSS variables, dark theme, msb-* components
  components/
    Sidebar.jsx              ← навигация (collapsible, persisted)
    Topbar.jsx               ← theme toggle, encryption status, workspace button
    ProfileTable.jsx         ← data-dense таблица (sortable, checkboxes, context menu)
    ProfileDetail.jsx        ← right drawer: просмотр/управление профилем
    ProfileForm.jsx          ← create/edit форма
    TrashModal.jsx           ← recycle bin UI
    CookieWarmer.jsx         ← Cookie Warmer UI
    CommonExtensions.jsx     ← shared extensions manager
    ProxyList.jsx            ← proxy list view
profiles/                    ← index.json + <id>/meta.json + <id>/userData/
captures/                    ← network captures (CDP NDJSON + mitmproxy flow files)
logs/                        ← pino logs
tests/                       ← интеграционные + unit тесты
extensions/
  msb-profile-badge/         ← Chrome extension: overlay в браузере
project/
  GGselMSB/                  ← ОТДЕЛЬНЫЙ проект (Flask панель продавца)
```

---

## Data Model

```
profile.schemaVersion  — версия схемы (int). Текущая: 1. Мигрируется на лету в ProfileStorage.readMeta().
profile.id             — UUID (string)
profile.number         — порядковый номер (int > 0, уникальный, не переиспользуется)
                         хранится в index.json как nextNumber
profile.name           — отображаемое имя
profile.group          — пользовательская группа (string | null)
                         НИКОГДА не совпадает с provider ("gmail"/"outlook"/"other")
profile.tags           — метки (string[]), верхний уровень
profile.provider       — автодетект из email: "gmail" | "outlook" | "other"
                         используется для иконки, НЕ для группировки
profile.engine         — "patchright" | "cloakbrowser" | "auto"
profile.proxy          — { protocol, host, port, username?, password? } | null
profile.proxyEnabled   — bool (default true)
profile.network        — { webRTC: {...}, dns: {...}, headers: {...}, tls: {...} }
profile.fingerprint    — { userAgent, platform, timezone, locale, viewport, ... }
profile.account        — { email, type, password, tags, loginStatus }
profile.notes          — только текст, не используется в логике
profile.flagged        — bool, флаг в UI
profile.startUrl       — URL для открытия при старте
profile.sortOrder      — int, для ручной сортировки
profile.createdAt      — timestamp ms
profile.updatedAt      — timestamp ms
```

**Важно:**
- `account.type` и `account.tags` — легаси поля, только для чтения (GGSeller читает их напрямую)
- `group` и `provider` — ортогональные оси, никогда не дублировать одно в другое
- `number` — НЕ переиспользуется после удаления; явный номер через POST { number: N } проверяется на уникальность
- `profile.network` — настройки сети (WebRTC, DNS, custom headers, TLS fingerprint)

---

## Groups

Группы **динамические** — создаются при выставлении `profile.group`. Метаданные (description, color) персистируются в `<profilesDir>/group-meta.json`.

```bash
PATCH /api/profiles/:id  { "group": "GGSeller" }     # задать группу
GET   /api/groups                                      # [{ name, count, profileIds, color, description, virtual }]
GET   /api/profiles?group=GGSeller                    # фильтр по группе
GET   /api/profiles?group=null                        # несгруппированные
PATCH /api/groups/:name  { "name": "NewName" }        # переименовать
PATCH /api/groups/:name/meta { "description": "...", "color": "#..." }  # метаданные (в т.ч. virtual)
DELETE /api/groups/:name                              # удалить (group=null у всех)
POST  /api/groups/:name/bulk-move  { profileIds: [] }
```

**Виртуальные группы** (virtual: true): «Без групп», «Minimax», «Claude», «GitHub» — всегда присутствуют в ответе `GET /api/groups`. Их нельзя удалить, но можно задать `description` и `color` через PATCH meta.

---

## API Reference

Base URL: `http://localhost:17248`
Auth: `Authorization: Bearer <token>` (токен из `GET /ui-config`)
Format: `{ ok: true, data: ... }` (native) или `?format=morelogin` / `X-MSB-Format: morelogin`

---

### Profiles CRUD

| Method | Path | Description |
|---|---|---|
| GET | `/profiles` | Список (`?group=Name`, `?group=null`) |
| GET | `/profiles/:id` | Полные данные |
| POST | `/profiles` | Создать |
| PATCH | `/profiles/:id` | Обновить поля (Ajv validated) |
| DELETE | `/profiles/:id` | Hard delete |
| POST | `/profiles/bulk-delete` | `{ ids: [...] }` |
| POST | `/profiles/import-legacy-bulk` | Bulk TXT import |

### Recycle Bin (7-day retention)

| Method | Path | Description |
|---|---|---|
| POST | `/profiles/:id/trash` | Soft-delete |
| GET | `/profiles/trash` | Список с `daysLeft` |
| POST | `/profiles/trash/:id/restore` | Восстановить |
| DELETE | `/profiles/trash/:id` | Hard delete (purge) |
| POST | `/profiles/trash/purge-expired` | Sweep (авто каждые 6ч) |

### Browser Control

| Method | Path | Description |
|---|---|---|
| POST | `/profiles/:id/start` | `{ launchMode?, headless?, isHeadless?, cdpEvasion?, closeCheckIPpage?, checkIPErrorHandle?, encryptKey?, extraArgs? }` |
| POST | `/profiles/:id/stop` | Остановить |
| GET | `/browser/status` | Список запущенных |
| POST | `/profiles/:id/check-proxy` | Проверка прокси |
| POST | `/profiles/:id/navigate` | `{ url }` — навигация CDP |

**MoreLogin-compatible aliases:** `/api/env/start`, `/api/env/stop`, `/api/env/list`, `/api/env/detail/:id`

### Traffic Capture (mitmproxy)

Требует: `mitmdump` в PATH или `MITMDUMP_BIN` env. Порты 18100–18200.

| Method | Path | Description |
|---|---|---|
| POST | `/profiles/:id/traffic/start` | `{ saveFlow?, saveHar?, filterHost? }` — запустить перехват |
| POST | `/profiles/:id/traffic/stop` | Остановить, восстановить исходный прокси |
| GET | `/profiles/:id/traffic/status` | `{ active, port, pid, captureDir, startedAt, byteCount }` |
| GET | `/profiles/:id/traffic/captures` | Список записанных сессий в `captures/<id>/` |
| GET | `/traffic/sessions` | Все активные сессии перехвата |

Если профиль имеет upstream-прокси — автоматически цепочка: `браузер → mitmdump → upstream`.

### Network Capture (CDP Ring Buffer)

In-memory кольцевой буфер CDP-запросов (2000 записей). Автоматически активируется при старте профиля. Файловый сброс в `captures/<id>/` (NDJSON, при переполнении буфера).

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/:id/network/requests` | `?method=&host=&path=&pattern=&status=&minStatus=&maxStatus=&since=&until=&limit=` |
| GET | `/profiles/:id/network/requests/:n` | Одна запись по sequence number |
| GET | `/profiles/:id/network/endpoints` | Группировка по шаблонному пути (`{id}`, `{uuid}`) |
| GET | `/profiles/:id/network/har` | Экспорт в HAR 1.2 с фильтрами |
| POST | `/profiles/:id/network/clear` | Очистить буфер |
| GET | `/profiles/:id/network/status` | `{ active, count, oldestAt, newestAt, pages }` |
| GET | `/network/captures` | Все профили с активным capture |

### Network Settings (профиль)

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/:id/network` | WebRTC / DNS / headers / TLS настройки |
| PATCH | `/profiles/:id/network` | Обновить настройки сети |
| POST | `/profiles/:id/network/test-dns` | Тест DNS leak |
| POST | `/profiles/:id/network/test-webrtc` | Тест WebRTC leak |
| POST | `/profiles/:id/network/headers` | `{ name, value }` — добавить custom header |
| DELETE | `/profiles/:id/network/headers/:headerName` | Удалить header |
| POST | `/profiles/:id/network/dns` | `{ servers: [...] }` — задать DNS серверы |

### Browser Storage (требует запущенного профиля)

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/:id/storage` | localStorage + sessionStorage + IndexedDB (список БД) |
| DELETE | `/profiles/:id/storage/local` | Очистить localStorage |
| DELETE | `/profiles/:id/storage/session` | Очистить sessionStorage |
| POST | `/profiles/:id/storage/local` | `{ key, value }` — записать в localStorage |
| DELETE | `/profiles/:id/storage/indexeddb/:dbName` | Удалить IndexedDB базу |
| GET | `/profiles/:id/storage/history` | `?limit=50` — история браузера |
| DELETE | `/profiles/:id/storage/history` | Очистить историю |

### Automation Pipeline

| Method | Path | Description |
|---|---|---|
| POST | `/automation/profile/create` | `{ name, group?, tags?, proxy?, engine?, ... }` |
| POST | `/automation/profile/start` | `{ profileId, headless?, traffic?, network? }` |
| POST | `/automation/profile/stop` | `{ profileId }` |
| POST | `/automation/pipeline/run` | `{ steps: [...] }` → `{ jobId }` |
| GET | `/automation/jobs` | Список всех jobs |
| GET | `/automation/jobs/:id` | `{ status, currentStep, totalSteps, results, error, durationMs }` |
| GET | `/automation/jobs/:id/log` | `?sinceTs=&limit=` |

**Pipeline step types:**

| type | Поля | Description |
|---|---|---|
| `create` | `name, group, proxy, engine, ...` | Создать профиль |
| `start` | `profileId, headless, traffic, network` | Запустить профиль |
| `stop` | `profileId` | Остановить |
| `wait` | `ms` | Пауза |
| `http` | `profileId, method, url, headers?, body?` | HTTP через CDP профиля |
| `eval` | `profileId, code` | Выполнить JS в странице |
| `screenshot` | `profileId, path` | Скриншот |
| `traffic` | `profileId, action: start/stop/status, opts?` | Управление mitmproxy |
| `network` | `profileId?, action: status/clear/endpoints/requests/har` | Ring buffer |
| `crawl` | `profileId, urls/url, maxPages?, maxDepth?, linkPattern?, extract?` | Crawlee walker |
| `llm` | `profileId, task, maxSteps?, model?` | browser-use LLM walker |

Все шаги поддерживают `continueOnError: true`.

### Crawler (Crawlee + browser-use)

| Method | Path | Description |
|---|---|---|
| GET | `/automation/crawl/capabilities` | `{ crawlee: { available, version }, python: { available, path }, browserUse: { available } }` |
| POST | `/automation/crawl/start` | `{ profileId, mode: "crawlee"|"llm", urls?, task?, maxPages?, ... }` |
| GET | `/automation/crawl/jobs` | Список walker jobs |
| GET | `/automation/crawl/jobs/:id` | Статус |
| GET | `/automation/crawl/jobs/:id/log` | Лог |
| GET | `/automation/crawl/jobs/:id/results` | Результаты `?limit=` |
| POST | `/automation/crawl/jobs/:id/abort` | Прервать |

### AI Agents

| Method | Path | Description |
|---|---|---|
| GET | `/api/agents` | Список агентов (`{ id, label, provider, status }`) |
| POST | `/api/agents/:id/start` | Запустить браузер агента |
| POST | `/api/agents/:id/stop` | Остановить |
| POST | `/api/agents/:id/navigate` | `{ url }` — перейти |
| POST | `/api/agents/:id/prompt` | `{ text, taskPackage? }` — отправить промпт провайдеру |
| GET | `/api/agents/:id/response/latest` | Последний ответ |
| GET | `/api/agents/:id/responding` | `{ responding: bool }` |
| POST | `/api/agents/:id/interrupt` | Прервать (Escape) |
| GET | `/api/providers/:name/health` | Health статус провайдера (chatgpt/claude/gemini/minimax) |

**Определение провайдера** — из `profile.notes` (тег `tag: claude;gemini`), имени, или `profile.provider`. Default: chatgpt.

### Anthropic API Adapter

Проксирует Anthropic API → Clewd (`http://127.0.0.1:8444`). Позволяет любому агенту использовать Clewd как бэкенд с нативным Anthropic SDK.

| Method | Path | Description |
|---|---|---|
| POST | `/v1/messages` | Anthropic Messages API (streaming + non-streaming, tool_use) |

### Workspace (GGselMSB launcher)

| Method | Path | Description |
|---|---|---|
| POST | `/workspace/launch` | Обеспечить профиль «AI Workspace», запустить Flask, открыть браузер |
| GET | `/workspace/status` | Статус (profile + flask + browser), без сайд-эффектов |

Профиль идентифицируется по тегу `ai-workspace`. Config: `MSB_GGSELLER_PROJECT_DIR`, `MSB_GGSELLER_URL`, `MSB_GGSELLER_PYTHON`, `MSB_GGSELLER_START_CMD`.

### Groups

| Method | Path | Description |
|---|---|---|
| GET | `/api/groups` | Все группы (реальные + виртуальные) с `{ name, count, virtual, description, color }` |
| PATCH | `/api/groups/:name` | `{ name }` — переименовать реальную |
| PATCH | `/api/groups/:name/meta` | `{ description?, color? }` — метаданные (любая группа) |
| DELETE | `/api/groups/:name` | Удалить реальную группу |
| POST | `/api/groups/:name/bulk-move` | `{ profileIds: [...] }` |

### Cookie Warmer (Qrator)

| Method | Path | Description |
|---|---|---|
| POST | `/warmer/start` | Запустить прогрев профиля |
| GET | `/warmer/status` | Статус прогрева |
| POST | `/warmer/stop` | Остановить |

### WebSocket Events

| Endpoint | Description |
|---|---|
| `ws://localhost:17248/ws/events` | Lifecycle events: `profile:created`, `profile:updated`, `profile:deleted`, `profile:trashed`, `profile:restored`, `cookies:imported`, `cookies:cleared`. Keepalive ping каждые 25 сек. |

### Misc

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Readiness check (disk, engines, profilesDir, memory) |
| GET | `/health/live` | Liveness check (дешёвый) |
| GET | `/ui-config` | Bearer-токен + конфиг для renderer |
| GET | `/api/status` | Текущий статус сервера |
| POST | `/api/shutdown` | Graceful shutdown |

---

## Launch Mode Policy

| Mode | Поведение |
|---|---|
| `visible` | Обычный запуск с окном |
| `minimized` | Best-effort сворачивание через CDP после старта |
| `background` | Окно минимизировано, без фокуса, попытка off-screen. Для Gemini/Google — предпочтительнее headless |
| `headless` | Настоящий headless. Для Google — более палевный |

Если клиент передаёт `headless=true` или `isHeadless=true` — MSB автоматически интерпретирует как `launchMode: "headless"`.

---

## Response Envelope

- **Native (default):** `{ ok: true, data: ... }` или `{ ok: false, error: "..." }`
- **MoreLogin opt-in:** `?format=morelogin` или `X-MSB-Format: morelogin`
- **Rate limit:** 60 req/min per (IP, token). Override: `MSB_RATE_LIMIT=0`. Ответ 429 с `Retry-After`.
- **X-Request-Id:** в каждом ответе, propagated через AsyncLocalStorage.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MSB_API_TOKEN` | auto | Bearer-токен API |
| `MSB_RATE_LIMIT` | 60 | req/min, 0 = off |
| `MSB_APPDATA` | `%APPDATA%/MSB` | Папка для automation jobs, etc. |
| `MITMDUMP_BIN` | `mitmdump` | Путь к mitmdump |
| `MITM_SCRIPT` | null | Путь к intercept.py |
| `MITM_CAPTURES_DIR` | `captures/` | Папка для mitmproxy flow files |
| `MSB_GGSELLER_PROJECT_DIR` | DEFAULTS | Путь к GGselMSB |
| `MSB_GGSELLER_URL` | DEFAULTS | URL Flask панели |
| `MSB_GGSELLER_PYTHON` | DEFAULTS | Python executable |
| `MSB_GGSELLER_START_CMD` | DEFAULTS | Команда старта Flask |

---

## Key Commands

```powershell
npm run dev            # dev-режим: Vite + Electron hot reload
npm run build:renderer # собрать renderer (dist/)
npm start              # production-запуск

# Правильный запуск с видимым UI:
Start-Process "C:\Users\Atreum\Desktop\MSB\start.vbs"

# Тесты:
node --test tests/profileStorage.test.js   # 17 integration tests
node --test tests/**/*.test.js             # все тесты

# Проверка синтаксиса (node --check):
node --check src/main/index.js src/main/services/profileManager.js ...

# Vite build smoke-test:
npm run build:renderer
```

---

## Common Pitfalls

1. **Никогда не пиши JSON напрямую через `fs.writeFile`** — только через `atomicWriteJson` из `atomicFs.js`. Иначе crash mid-write = corrupted meta.json.
2. **`PATCH /profiles/:id`** — Ajv запрещает `id`, `schemaVersion`, `number` в патче. При добавлении нового server-managed поля добавь его в `validate.js` blacklist.
3. **`_toListItem()`** возвращает `group`, `tags`, `provider` — если добавил поле в профиль, добавь и сюда.
4. **`profile.group` ≠ `profile.provider`** — provider ("gmail"/"outlook"/"other") никогда не попадает в group.
5. **`account.tags`** — скрипты GGSeller читают его напрямую; не трогай.
6. **Прогрев Qrator** — при смене прокси куки сбрасываются. Используй Warmer API.
7. **Порт `17248`** неизменен — на него завязаны GGSeller и Combined_Widget.
8. **`GET /groups`** агрегирует по `profile.group` в реальном времени, не кэшируется.
9. **socks5 с авторизацией** — не через `--proxy-server`, а через `switchProxy()` (undici ProxyAgent + context.route()).
10. **Gemini/Google launch policy** — предпочитай `launchMode: "background"` вместо `headless`.
11. **Fingerprint coherence для Google** — timezone, locale, proxy geo, viewport/screen, шрифты, GPU/WebGL должны совпадать. Не миксуй RU locale с US proxy.
12. **ProfileStorage** — stateless, без кэша. Кэш держит ProfileManager. Не создавай второй экземпляр ProfileStorage с тем же profilesDir.
13. **NetworkCaptureService** — ring buffer 2000 записей. `attach()` вызывается автоматически при старте профиля (CDP сессия). Не нужно вызывать вручную если используешь `browserLauncher.start()`.
14. **TrafficCaptureService** — требует `mitmdump` в PATH. При старте mitmproxy получает свободный порт из диапазона 18100–18200. Исходный прокси профиля восстанавливается при `stop()`.
15. **AutomationService jobs** — хранятся in-memory, не персистируются. После рестарта MSB jobs теряются.
16. **CrawlerService `llm` mode** — требует Python + browser-use пакет. `capabilities()` скажет что доступно.
17. **Anthropic Adapter** — требует запущенный Clewd на `127.0.0.1:8444`. Без Clewd — 502.
18. **`/workspace/launch`** — Flask запускается detached (`child.unref()`), переживает MSB. Повторный вызов идемпотентен.
19. **Виртуальные группы** — нельзя удалить через `DELETE /api/groups/:name` (400). Описание сбросить через `PATCH /api/groups/:name/meta { description: '' }`.
20. **group-meta.json** — хранится в `<profilesDir>/group-meta.json`, пишется через atomicWriteJson.
