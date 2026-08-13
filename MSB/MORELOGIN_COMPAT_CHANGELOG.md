# MoreLogin Compatibility — MSB patch

> **Канонический API-референс** — в `AGENT.md`. Этот файл — только описание **что изменилось** и **как пользоваться новыми эндпоинтами**, плюс обоснование архитектурных решений (что НЕ скопировали и почему).

## Что нового (2026-08-03)

### 1. Dual response envelope — обратно совместимо

По умолчанию MSB отдаёт `{ok, data}` / `{ok: false, error}` — не трогаем существующих клиентов.

Клиент может **opt-in** в MoreLogin-формат (`{code, msg, data, requestId}`) одним из способов:

- `?format=morelogin` в query
- `X-MSB-Format: morelogin` header
- `Accept: application/vnd.msb+morelogin`

Коды ошибок маппятся на MoreLogin-стиль: 400→99001, 401→99002, 403→99003, 404→99004, 409→99005, 429→99006, 5xx→99999.

### 2. Anti-detection — cdpEvasion

`POST /profiles/:id/start` принимает `cdpEvasion: true`. В init-script добавляется расширенный набор anti-detect маркеров: `Permissions.query` patching, `chrome.runtime` shim, hardware-concurrency rounding, languages array. Полезно при подключении через Playwright/Puppeteer/Selenium по CDP.

### 3. Pre-flight IP check

`POST /profiles/:id/start` принимает:
- `closeCheckIPpage: true` — запустить IP-проверку через прокси перед стартом
- `checkIPErrorHandle: 1` — abort start при ошибке (default)
- `checkIPErrorHandle: 2` — продолжить, прикрепить warning к ответу

### 4. Recycle bin (soft delete, 7-day retention)

Заменил hard-delete UX: теперь "удалить" = отправить в корзину. Hard delete остался только для явного purge из корзины.

| Endpoint | Что делает |
|---|---|
| `POST /profiles/:id/trash` | soft-delete (recoverable 7 days) |
| `GET /profiles/trash` | список с days-left |
| `POST /profiles/trash/:id/restore` | восстановить |
| `DELETE /profiles/trash/:id` | hard-delete (purge) |
| `POST /profiles/trash/purge-expired` | manual sweep; auto-sweep каждые 6ч |

В UI: кнопка 🗑 в сайдбаре + TrashModal со списком и кнопками восстановления.

### 5. Rate limiter

`60 req/min per (IP, token)` — MoreLogin parity. Заголовки `X-RateLimit-Limit/Remaining/Reset`. 429 с `Retry-After`. Override: `MSB_RATE_LIMIT=0` для отключения.

### 6. E2E encryption (`encryptKey`)

Базовый helper для шифрования sensitive полей в meta.json (`account.password`, `account.notes`, `account.tokens`):

| Endpoint | Что делает |
|---|---|
| `POST /api/env/encrypt-key` `{action: "enable", envId, encryptKey}` | записать marker + зашифровать поля |
| `POST /api/env/encrypt-key` `{action: "verify", envId, encryptKey}` | проверить, что ключ совпадает с marker |
| `POST /api/env/encrypt-key` `{action: "disable", envId}` | удалить marker (поля остаются зашифрованными) |

`POST /api/env/start` с `encryptKey` в payload: если на диске есть marker, ключ валидируется (fingerprint match), иначе 403.

**Честное ограничение**: MSB не имеет кастомной Chromium-сборки, поэтому **весь** userDataDir не шифруется. Шифруются только JS-visible sensitive поля в meta.json. User-data (cookies, history) защищается на уровне ОС. Документация ML про E2E описывает kernel-level encryption, недостижимое без форка Chromium.

### 7. MoreLogin API aliases (`/api/env/*`)

Для переносимости клиентов, написанных под MoreLogin docs:

| Endpoint | Equivalent |
|---|---|
| `POST /api/env/start` | `POST /profiles/:id/start` |
| `POST /api/env/close` | `POST /profiles/:id/stop` |
| `POST /api/env/closeAll` | закрыть все running |
| `POST /api/env/status` | `GET /browser/status` (фильтр по envId) |
| `POST /api/env/getAllProcessIds` | list `[{envId, pid}]` |
| `POST /api/env/getAllDebugInfo` | list `[{envId, debugPort, cdpEndpoint, pid}]` |
| `POST /api/env/arrangeWindows` | auto-tile открытых окон через Electron `BrowserWindow` |
| `POST /api/env/getAllScreen` | `screen.getAllDisplays()` |
| `POST /api/env/removeLocalCache` | `context.clearCookies()` |
| `POST /api/env/fingerprint/refresh` | `POST /profiles/:id/fingerprint/refresh` |

Ответы `start` содержат ML-алиасы: `envId` (= `id`), `debugPort` (= `cdpPort` как string), `type` (= `engine`), `webdriver` (= `cdpEndpoint`).

## Что НЕ скопировано (и почему)

- **MITM, декомпиляция, кряки** — отказано; использовались только публичные источники: `guide.morelogin.com` (включая `.md` зеркала), `github.com/MoreLoginBrowser/MoreLogin-API-Demos` (MIT), public Bugrap bug-bounty отчёты, community reviews.
- **Полная kernel-level E2E** — требует кастомной Chromium-сборки. У нас Patchright/Cloak, не свой форк. Сделали JS-уровневую защиту sensitive полей — задокументировано явно.
- **Mobile / cloud phone** — не наш use case.
- **MCP server** — отдельный большой проект, не в скоупе.
- **ML Canvas fingerprint "noise"** — у MoreLogin это ML-generated подмена hash; у нас в `lib/fingerprint.js` уже есть свой механизм через `installFingerprintInitScripts`. Не дублировал.
- **Team management / RBAC** — у нас single-user, нет смысла.

## Verification

Все новые файлы прошли `node --check` + end-to-end smoke-тесты: envelope roundtrip, rateLimiter блокировка, ipCheck, profileCrypto encrypt/decrypt/marker IO, vite build renderer (40 модулей, OK).

## Historical — Iteration 1

Initial reverse-engineering pass: API surface map, comparison with MSB, selection of 10 features to port. See git log for the full diff.

---

## Iteration 3 (2026-08-03, late) — Tier 1 stability + GUI rewrite

Closed the structural gaps. Goal: bring MSB from 6.5/10 to 8.5/10 of "commercial-grade self-hosted antidetect".

### Stability layer (Tier 1, all atomicFs + Ajv + AsyncLocalStorage-based)

| New module | What it does |
|---|---|
| `src/main/lib/atomicFs.js` | `atomicWriteJson` / `readJsonSafe` / `atomicUpdateJson`. write-tmp + fsync + rename + parent-dir fsync. Survives crash mid-write. |
| `src/main/lib/profileSchema.js` | `PROFILE_SCHEMA_VERSION = 1` + `migrateProfile(raw)` (v0→v1: schemaVersion, group, account, fingerprint, tags, proxyEnabled) + `validateProfileShape`. Migration registry is append-only. |
| `src/main/api/validate.js` | Ajv-based. `validateBody('profile-create')` + `validatePatch('profile-patch')`. PATCH rejects server-managed fields (`id`, `schemaVersion`, `number`, `createdAt`, `updatedAt`, `_forwardCompat`). Built-in schemas: `profile-create`, `profile-patch`, `browser-start`, `trash-restore`. |
| `src/main/lib/requestContext.js` | AsyncLocalStorage-based request context. `resolveRequestId(req)` honours `X-Request-Id` / `X-Mlb-Request-Id` / `X-Msb-Request-Id` / Fastify `req.id`. `getRequestIdOrAnon()` works anywhere in the call stack. |

### Integrations

- `profileManager` — `_readMeta` migrates on read; `_writeMeta` / `_writeIndex` / `trash.manifest` / `restore.meta` all use `atomicWriteJson`. Old `_atomicWriteFile` removed.
- `server.js` — `onRequest` hook resolves request-id, `onSend` echoes `X-Request-Id`, `preHandler` runs handler inside `runWithRequestContext`.
- `routes/profiles.js` — `POST /profiles` uses `validateBody('profile-create')`; `PATCH /profiles/:id` uses `validatePatch('profile-patch')`.
- `routes/health.js` — `GET /health` now does real checks: profilesDir writable (CRITICAL), disk space (DOWN if <50MB / DEGRADED if <500MB), engines availability, schema version, running browsers, memory. Returns 503 when `down`. `GET /health/live` is the cheap liveness probe.

### GUI rewrite

Inspired by MoreLogin V2.62.0 layout — but original implementation, no copyrighted assets copied.

| New component | Replaces |
|---|---|
| `src/renderer/components/Sidebar.jsx` | old inline `Navigation` in `App.jsx`. Vertical icon nav, collapsible, 10 items. |
| `src/renderer/components/Topbar.jsx` | new. Version, encryption indicator, theme toggle, user chip. |
| `src/renderer/components/ProfileTable.jsx` | `ProfileList.jsx` (deleted). Data-dense table view: ✓ / No. / Профили / Аккаунт / Прокси / Примечание / Метка / actions. Sortable columns, row context menu, status pulse. |
| `src/renderer/styles.css` | rewritten with CSS variables + `[data-theme="dark"]` selector. New component styles (`.msb-*` namespace). |

### App shell

`App.jsx` now has:
- Theme toggle (`light` / `dark`) persisted to `localStorage` (`msb.theme`)
- Sidebar collapse state persisted (`msb.sidebar`)
- Profiles page: tabs (Browser / Cloud mobile — but Cloud mobile removed as "not in scope"), search, filter, mass-import, create
- Group chips: Все группы / GGSeller / Без группы (dynamic from API)
- Sortable table + pagination (100 per page, 1/N)
- Footer: trash / select-all / page nav
- Right drawer for `ProfileDetail` (overlays instead of pushing the table)

### Pages that exist
- Профили (table)
- Прокси
- Группы
- Корзина
- Расширения
- Cookie Warmer
- Автоматизация (placeholder)
- Логи / Аудит (placeholder)
- Мониторинг (placeholder)
- Настройки

### Pages deliberately **not** added (MoreLogin has them, we don't)
- Cloud mobile / cloud phone
- AI Hub
- Облачное хранилище
- Команда / RBAC
- Биллинг / пакеты / расходы
- Синхронизатор (no remote sync — single user only)
- Разработчик ИИ-агентов

### Verification
- `node --check` — 11/11 OK
- Vite build — 42 modules, 222 kB JS, 12.58 kB CSS
- atomicFs selftest: round-trip OK
- profileSchema: v0→v1 migration OK, forward-compat flag set, idempotent
- Ajv: rejects bad engine, bad port, bad email; accepts valid payload; PATCH rejects `id` and `schemaVersion`
- requestContext: AsyncLocalStorage carries context through await correctly

### Estimated score: 6.5/10 → 8.5/10

What's left for 9.5/10 (Tier 2):
- Break `profileManager` into ProfileService + ProfileStorage + TrashService + GroupService
- Break `browserLauncher` into EngineAdapter (Cloak/Patchright) + Supervisor + FingerprintInjector
- Render routes thin, business logic in services
- JSDoc with runtime check or migrate to TypeScript
