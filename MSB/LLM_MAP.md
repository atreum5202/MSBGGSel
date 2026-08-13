# LLM Agent Map — MSB

> **Вниманию ИИ-агента**: `AGENT.md` — канонический референс. Этот файл — **только** дополнение: операционные правила, типичные грабли и команды запуска, которые **не** описаны в AGENT.md. Если в чём-то сомневаешься — читай сначала `AGENT.md`.

---

## Операционные правила

### Документация
- Вноси изменения в `AGENT.md` при добавлении/изменении эндпоинтов, data model, движков.
- При обновлении доки проверяй, что в `MORELOGIN_COMPAT_CHANGELOG.md` отражены новые совместимые эндпоинты.
- Документация — single source of truth: `AGENT.md` для API/data model, `MORELOGIN_COMPAT_CHANGELOG.md` для новых фич и ML-parity, `README.md` для верхнеуровневого tour.

### Чистота
- **Удаляй старые хвосты** при изменениях: неиспользуемый код, временные заглушки, TODO-комментарии с истёкшим смыслом.
- Никаких «экспериментальных» файлов в корне проекта: прогресс-логи, scratch-данные, старые патчи — в личную wiki, **не** в репо.
- Всё runtime-состояние (логи, pid, build) — в `.gitignore`.

### Тесты
- Запускай `node --test tests/profileStorage.test.js` после любых изменений в ProfileStorage, TrashService, atomicFs, profileSchema.
- Запускай `npm run build:renderer` после изменений в renderer.
- `node --check src/main/...` после изменений в main process.

### Ошибки
- `try/catch` вокруг всего, что может упасть.
- Логируй с контекстом (profileId, envId, action). Никаких тихих падений.

---

## Жёсткие инварианты

- **Порт 17248** — на нём завязаны `GGSeller` и `Combined_Widget`. Не меняй.
- **Структура `index.json` + `<id>/meta.json`** — скрипты GGSeller читают напрямую. Не переноси в SQLite без миграционного слоя.
- **`account.type` / `account.tags`** — легаси-поля, только для чтения. Источник правды — `profile.group` / `profile.tags`.
- **`profile.notes`** — только человеческий текст. **Не** использовать для логики.
- **Вся запись JSON идёт через `atomicFs`** — никаких `fs.writeFile(path, ...)` напрямую для meta.json / index.json / trash.json / group-meta.json.
- **`profile.group` ≠ `profile.provider`** — никогда не копируй provider в group и наоборот.

---

## Запуск

```powershell
# Правильно — UI должен быть виден:
Start-Process "C:\Users\Atreum\Desktop\MSB\start.vbs"

# Или прямо:
Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd "C:\Users\Atreum\Desktop\MSB"; npm start' -WindowStyle Normal

# Сборка renderer:
npm run build:renderer

# ❌ Неправильно (UI не появится):
Start-Process "electron.exe" -WindowStyle Hidden
```

Тест API:
```sh
curl http://127.0.0.1:17248/health
curl http://127.0.0.1:17248/profiles -H "Authorization: Bearer TOKEN"
```

---

## Архитектура service layer

```
ProfileManager
  └── ProfileStorage    ← все disk IO (atomic, migration)
  └── TrashService      ← recycle bin (использует ProfileStorage)

BrowserLauncher
  ├── engineLoader.js   ← lazy Patchright / CloakBrowser init
  ├── profileBootstrap.js ← prefs, autofill, badge, seed
  ├── downloads.js      ← download handler
  └── utils.js          ← ports, CDP, url

NetworkCaptureService   ← CDP ring buffer (2000 entries) + NDJSON flush
TrafficCaptureService   ← mitmdump subprocess (ports 18100-18200)
AutomationService       ← job queue, pipeline runner
CrawlerService          ← Crawlee (JS) + browser-use (Python LLM)
```

---

## Грабли (обновляй при нахождении новых)

- **Группа ≠ notes**: До 2026-08 группа хранилась в `profile.notes` как маркер `Group: Gmail`. Сейчас — `profile.group`. Не повторяй.
- **`_toListItem()` в `profileManager.js`**: поля, которых нет здесь, не видны UI через `GET /api/profiles`. Добавляешь новое поле — добавь и сюда.
- **CloakBrowser peer-deps**: `playwright-core`, `puppeteer-core`, `mmdb-lib`. Без них — 500 на старте. Patchright НЕ проксирует `playwright-core`.
- **Profile `number` монотонно растёт** и не переиспользуется. При явном задании через PATCH уникальность проверяется в `_isNumberTaken()`.
- **E2E encryption** (`profileCrypto.js`): шифрует только `account.password`, `account.notes`, `account.tokens`. Не весь userDataDir.
- **Recycle bin**: retention = 7 дней, sweep каждые 6ч. Хард-удаление только через `DELETE /profiles/trash/:id`. `DELETE /profiles/:id` — hard-delete для обратной совместимости.
- **Rate limit** = 60 req/min per (IP, token). 429 с `Retry-After`. Override: `MSB_RATE_LIMIT=0`.
- **MoreLogin envelope** — opt-in: `?format=morelogin`. По умолчанию MSB-native `{ok, data}`.
- **atomicFs** — при добавлении нового JSON-файла всегда используй `atomicWriteJson` / `readJsonSafe`. Никакого прямого `fs.writeFile` для persistence-файлов.
- **Schema migration** — добавляй новые поля через `migrateProfile()` в `profileSchema.js`. Старые профили апгрейдятся при следующем чтении. Никогда не переписывай существующую migration.
- **Ajv validation**: `id`, `schemaVersion`, `number`, `createdAt`, `updatedAt` запрещены в PATCH. `engine` = `auto | patchright | cloakbrowser`. При добавлении поля — обнови `validate.js`.
- **Request-id**: `X-Request-Id` в каждом ответе. `getRequestContext().requestId` (AsyncLocalStorage) для корреляции в логах.
- **Health endpoint**: `/health` делает реальные проверки (disk, engines, profilesDir). 503 при degraded. `/health/live` — дешёвый liveness probe.
- **Dark theme**: переключатель в Topbar. `localStorage` → `msb.theme`. CSS-переменные в `[data-theme="dark"]` в `styles.css`.
- **Layout**: `ProfileList.jsx` удалён, заменён на `ProfileTable.jsx`. При добавлении page — обнови `NAV_ITEMS` в `Sidebar.jsx`.
- **socks5 с авторизацией** не проходит через `--proxy-server=socks5://user:pass@...` (Chromium не поддерживает). Используй `switchProxy()` — undici ProxyAgent + `context.route()`.
- **ProfileStorage.trashPath(id)** vs **ProfileStorage.dir(id)**: не перепутай. `dir()` — live, `trashPath()` — в `.trash/`.
- **TrashService.trash()** требует объект profile, не id. profileManager.trash(id) сам делает this.get(id) перед вызовом.
- **NetworkCapture ring buffer** — auto-flush при FLUSH_AT=1800 записях (90% RING_SIZE=2000). Файл: `captures/<id>/<ts>/network.ndjson`.
- **TrafficCapture portrange** = 18100–18200. Если все порты заняты — `Error: No free port in range`. Убедись, что старые mitmdump процессы убиты.
- **AutomationService jobs** — in-memory, не персистируются. Лог обрезается до 2000 строк per job. После рестарта MSB — потеряны.
- **CrawlerService llm mode** — Python-харнес генерируется в tmpdir и запускается как subprocess. Нужен `browser-use` пакет в Python env.
- **Clewd adapter** (`/v1/messages`) — всегда проксирует через streaming от Clewd (даже если клиент запросил non-streaming). Парсит `<tool_use>...</tool_use>` XML в tool_use блоки для Anthropic SDK.
- **group-meta.json** хранится в `<profilesDir>/group-meta.json`. Пишется через `atomicWriteJson`. Грузится при регистрации роутов (fire-and-forget `_loadMeta`).
- **Виртуальные группы** — «Без групп», «Minimax», «Claude», «GitHub». `DELETE /api/groups/:name` → 400 для virtual. Сбросить description → `PATCH /api/groups/:name/meta { description: '' }`.
- **Workspace профиль** — идентифицируется по тегу `ai-workspace` (primary) или по имени (fallback). При каждом `launch` ключевые поля синхронизируются с config.
- **Flask в workspace** — запускается через `spawn(..., { detached: true })` + `child.unref()`. Переживает MSB. Повторный вызов `/workspace/launch` идемпотентен.
