# MSB_SETUP.md — Подключение антидетекта MSB к парсеру

> **Что это:** Парсер ggsel.net в `GGselV7/` использует локальный антидетект
> `Controller/MSB` для обхода Qrator WAF. Без MSB парсер переключается на
> CffiFetcher (fallback, без Qrator-куков).
>
> **Когда читать:** перед первым запуском парсера, или при проблемах с Qrator.

---

## 📋 Что нужно

| Компонент | Где | Зачем |
|---|---|---|
| **MSB** | Обычное приложение MSB | Основной антидетект |
| **MSB API** | `http://127.0.0.1:17248` | Local REST API |
| **MSB (Fallback)** | `Controller\MSB` | Альтернативный локальный антидетект |
| **Python deps** | httpx, curl-cffi, dotenv, bs4 | Уже в `requirements.txt` |

---

## 🚀 Шаг 1: Запустить MSB (или MSB)

### Вариант A: Silent (рекомендуется, без окна)

```cmd
cd C:\Users\Atreum\Desktop\FinalProject\Controller\MSB
silent.bat
```

Это запустит:
- `npm run start` с `MSB_SILENT=1` (Electron без главного окна)
- Fastify REST API на `127.0.0.1:17248`
- Логи в `%APPDATA%\MSB\logs\msb.log`

### Вариант B: С окном (для отладки)

```cmd
start.bat
```

Откроется Electron-окно, можно смотреть профили вживую.

### Проверить что MSB жив

```cmd
curl http://127.0.0.1:17248/profiles
```

Ожидаемый ответ:
```json
{"ok":true,"data":{"status":"ok","uptime":42.123}}
```

```cmd
curl http://127.0.0.1:17248/profiles
```

Должно вернуть массив профилей (по умолчанию ~50 в `default` MSB-конфиге).

### Остановить MSB

```cmd
stop.bat
```

---

## 🚀 Шаг 2: Запустить GGselV7

```cmd
cd C:\Users\Atreum\Desktop\FinalProject\GGselV7
python app.py
```

Flask поднимется на `http://127.0.0.1:5000`.

### Проверить что парсер видит MSB

```bash
curl http://127.0.0.1:5000/api/parser/msb/status
```

Ожидаемый ответ (фрагмент):
```json
{
  "msb_running": true,
  "fetcher_used": "",
  "pool": null,
  "rate_limiter": {
    "base_delay": 4.0,
    "max_delay": 60.0,
    "min_delay": 2.0,
    "mult_401": 3.0,
    "mult_429": 2.0,
    "mult_5xx": 1.5,
    "ok_decay": 0.95,
    "profiles": {}
  },
  "telemetry": {
    "enabled": true,
    "current_file": "events.jsonl",
    "total_size_bytes": 0
  }
}
```

`pool` будет `null` пока парсер не стартовал. После первого `start` появится полная сводка.

---

## 🚀 Шаг 3: Запустить парсинг

### Через GUI

1. Открой `http://127.0.0.1:5000`
2. Раздел "Парсер" → выбери категорию + quantity
3. Нажми "Старт"

### Через REST API

```bash
POST /api/parser/start
Content-Type: application/json

{
  "category": "games",
  "quantity": 5,
  "max_pages": 1,
  "run_ai": false
}
```

Ответ:
```json
{
  "ok": true,
  "run_id": 7,
  "message": "Запущен парсинг (q='', cat='games', qty=5)"
}
```

### Проверить прогресс

```bash
GET /api/parser/status
```

```json
{
  "status": "running",
  "is_running": true,
  "products_found": 60,
  "products_saved": 0,
  "pages_scanned": 1,
  "errors_count": 0,
  "fetcher_used": "msb"
}
```

`fetcher_used: "msb"` означает что идёт через MSB. Если `"msb_unavailable"` — fallback на CffiFetcher.

---

## 🔍 Шаг 4: Посмотреть результаты

### Товары в БД

```bash
GET /api/parser/products?limit=10
```

### Telemetry (события парсинга)

```bash
GET /api/parser/telemetry/recent?limit=20
```

Каждое событие:
```json
{
  "ts": "2026-07-24T22:08:29Z",
  "event": "parser.page_fetched",
  "profile_id": "2a9c546e-...",
  "status": 200,
  "latency_ms": 2436,
  "rate_delay": 4.0,
  "is_challenge": false
}
```

### Rate limiter (per-profile delay)

```bash
GET /api/parser/msb/rate
```

```json
{
  "base_delay": 4.0,
  "profiles": {
    "2a9c546e-...": {
      "delay": 3.8,
      "consecutive_ok": 1,
      "consecutive_errors": 0,
      "is_problematic": false,
      "last_status": 200,
      "total_requests": 1
    }
  }
}
```

`delay=3.8` = `4.0 * 0.95` после 1 успешного запроса (adaptive decay).

---

## ⚙️ Конфигурация

Все настройки в `config.py`. Можно переопределить через ENV:

| ENV | Default | Что делает |
|---|---|---|
| `MSB_API_BASE` | `http://127.0.0.1:17248` | URL MSB API |
| `MSB_API_TOKEN` | `""` | Bearer-токен (если MSB запущен с auth) |
| `MSB_PROFILE_ID` | `""` | Конкретный профиль (пусто = весь пул) |
| `MSB_COOKIE_TTL` | `3600` | Сколько секунд считать snapshot свежим |
| `POOL_MAX_HITS` | `80` | После скольки запросов профиль на отдых |
| `POOL_REST_SEC` | `300` | Сколько секунд отдыха (5 мин) |
| `RATE_BASE_DELAY` | `4.0` | Стартовая пауза между запросами |
| `RATE_MIN_DELAY` | `2.0` | Минимальная пауза |
| `RATE_MAX_DELAY` | `60.0` | Максимальная пауза (cap) |
| `RATE_OK_DECAY` | `0.95` | На 200 OK: delay *= 0.95 |
| `RATE_429_MULT` | `2.0` | На 429: delay *= 2 |
| `RATE_401_MULT` | `3.0` | На 401/403: delay *= 3 |
| `RATE_5XX_MULT` | `1.5` | На 5xx: delay *= 1.5 |
| `TELEMETRY_ENABLED` | `true` | Включить локальную телеметрию |
| `TELEMETRY_DIR` | `data/telemetry` | Где хранить JSONL |
| `CAPTCHA_ENABLED` | `true` | Включить captcha-handler |
| `PARSER_USE_MSB` | `true` | Использовать MSB (если false — только CffiFetcher) |

---

## 🛠️ Troubleshooting

### MSB не отвечает / `msb_running: false` (или MSB недоступен)

**Причина:** MSB не запущен или не на том порту.
**Решение:**
1. `cd C:\Users\Atreum\Desktop\FinalProject\Controller\MSB && silent.bat`
2. Проверь `curl http://127.0.0.1:17248/profiles`
3. Проверь порт: `netstat -ano | findstr 17248` (должен быть LISTENING)
4. Лог MSB: `%APPDATA%\MSB\logs\msb.log`

### `pool: null` после первого запуска

**Причина:** `get_pool()` ещё не вызывался. Он lazy-инициализируется при первом fetch.
**Решение:** запусти парсинг хотя бы раз, потом проверь `/api/parser/msb/status`.

### `fetcher_used: "msb_unavailable"` + `msb_unavailable_reason: "Connection refused"`

**Причина:** MSB API упал во время парсинга. Парсер сделал fallback на CffiFetcher.
**Решение:** поднять MSB, перезапустить парсинг. На CffiFetcher парсер тоже работает, но без Qrator-куков (qqq.ggsel.net выкинет challenge и парсер остановится).

### `Fetch fail: no_cookies` + `errors_count: 1`

**Причина:** все доступные профили на отдыхе (5 мин после 80 хитов), или в `/profiles/running` (юзер открыл в MSB UI).
**Решение:**
- Подожди 5 мин (отдых закончится)
- Или: `POST /api/parser/msb/reset-errors` — снимает всех с отдыха
- Или: проверь `/api/parser/msb/status` → `pool.resting` покажет сколько на отдыхе

### `404`/`401` от ggsel.net

**Причина:** Qrator-куки протухли, MSB их не обновил.
**Решение:** `POST /api/parser/msb/refresh/<profile_id>` — принудительный re-fetch.

### Captcha page в HTML (g-recaptcha / hCaptcha)

**Причина:** Qrator выкинул капчу.
**Решение:**
- CaptchaHandler попробует solve_via_msb автоматически (POST `/profiles/:id/runScenario {solveCaptcha: true}`)
- На момент написания этого документа MSB scenario `ggsel-login` **НЕ ЧИТАЕТ `params.solveCaptcha`** — флаг игнорируется
- Workaround: вручную `POST /api/parser/msb/refresh/<id>` — scenario перезапустится с большим таймаутом, Qrator re-rolls challenge
- TODO: добавить чтение `params.solveCaptcha` в `Controller\MSB\src\main\lib\scenarios\ggselLogin.js` (если есть 2Captcha API ключ)

### `rate_state.json` не обновляется

**Причина:** `save_debounce_sec` = 1.0 сек (см. `adaptive_rate_limiter.py`). Подожди секунду после запроса.
**Решение:** это by design — save происходит не чаще 1 раза в секунду.

### Профили "утекли" в `/profiles/running`

**Причина:** MSB сценарий не был остановлен корректно. Может быть после kill -9 или краша Electron.
**Решение:**
- Открыть MSB UI → закрыть висящие окна профилей
- Или: подождать — pool фильтрует их через `get_running_profile_ids()`

### Как полностью пересоздать пул

```bash
# Сбросить все счётчики и rate
POST /api/parser/msb/reset-errors
POST /api/parser/msb/rate/reset

# Или вручную:
# Удалить data/rate_state.json
# Перезапустить GGselV7 (pool переинициализируется)
```

---

## 📊 Мониторинг в проде

### Команды для проверки

```bash
# Общий статус
curl http://127.0.0.1:5000/api/parser/msb/status

# Что делает прямо сейчас
curl http://127.0.0.1:5000/api/parser/status

# Последние 50 событий
curl "http://127.0.0.1:5000/api/parser/telemetry/recent?limit=50"

# Размер telemetry-файла
curl http://127.0.0.1:5000/api/parser/telemetry/stats

# Лог MSB
tail -f $APPDATA\MSB\logs\msb.log

# Лог парсера
tail -f C:\Users\Atreum\Desktop\FinalProject\GGselV7\data\logs\parser.log

# Лог пула профилей
tail -f C:\Users\Atreum\Desktop\FinalProject\GGselV7\data\logs\profiles.log
```

### Что смотреть

| Метрика | Где | Норма |
|---|---|---|
| `pool.active / pool.total` | msb/status | > 80% активных |
| `rate_limiter.profiles.*.delay` | msb/rate | 2-6 сек |
| `rate_limiter.profiles.*.is_problematic` | msb/rate | false (если true — профиль часто ловит 401) |
| `telemetry.total_size_bytes` | telemetry/stats | растёт, ротация по 50MB |
| `profiles.log` (если "ошибка") | logs/ | должно быть 0 (если > 10% — Qrator банит) |

---

## 🔒 Безопасность

- **Snapshot cookies** хранятся в `%APPDATA%\MSB\profiles\<uuid>\cookies-snapshot.json`. НЕ логируются и НЕ отправляются.
- **Telemetry** пишет только метаданные (имена cookies, count, профиль_id, latency). **Значения cookies никогда не пишутся** в JSONL.
- **MSB scenario** использует stealth-браузер (Patchright/CloakBrowser) с humanize (движения мыши, скроллы).
- **No external calls** — кроме ggsel.net и MSB API на localhost.

### Launch mode для Gemini/Google-сценариев (авто-генерация фото)

MSB (начиная с 08-2026) принимает явный `launchMode` в `POST /profiles/:id/start` и `POST /api/env/start`:

```json
{ "launchMode": "visible" | "minimized" | "background" | "headless" }
```

Старые флаги всё ещё работают для совместимости (`headless: true` / `isHeadless: true` автоматически трактуется как `launchMode: "headless"`).

**Важно:** для браузерной генерации через Gemini (кнопка «Перегенерировать» в Модерации) парсер использует не `headless`, а `launchMode: "background"`:

| Режим | Поведение | Риск детекта Google |
|---|---|---|
| `headless` | настоящий headless Chromium | **выше** — иначе rendering pipeline, часто палится |
| `background` | обычный headed рендер, `--start-minimized` + off-screen позиция + best-effort CDP-minimize | ниже — рекомендуется для Gemini |
| `minimized` | headed, окно свёрнуто через CDP после старта | ниже |
| `visible` | обычный видимый запуск | базовый |

`background` — **best-effort**, не абсолютная гарантия ОС-уровня. Окно может кратко появиться на экране перед тем как свёрнется/уйдёт за границы экрана.

Ответ `start` в MSB теперь возвращает дополнительные поля: `launchMode`, `headlessApplied`, `backgroundApplied`, `focusSuppressed`, `pid`. `GGselMSB` логирует их при каждом запуске через `parser/morelogin_gemini.py`.

Согласованность профиля для Gemini/Google остаётся важнее самого launch mode — смотри чтобы у профиля совпадали:
- `timezone`
- `locale`
- proxy geo
- viewport/screen
- fonts
- GPU/WebGL

---

## 🧪 Запуск тестов

```cmd
cd C:\Users\Atreum\Desktop\FinalProject\GGselV7
python -m pytest tests/ -v
```

**59 тестов** (4 файла):
- `test_rate_limiter.py` (11) — adaptive delay, persist, threadsafe
- `test_captcha_handler.py` (21) — детект + solve strategies
- `test_telemetry.py` (12) — append-only, sanitization, rotation
- `test_msb_fetcher.py` (15) — pre-checks, refresh flow, errors

Все тесты работают **без реального MSB** (моки).

### E2E проверка против реального MSB

```cmd
python tests\verify_e2e.py
```

Показывает: БД товаров, rate_state, telemetry, parser log, profile log, чек-лист из 11 критериев приёмки.

---

## 📁 Структура файлов

```
C:\Users\Atreum\Desktop\FinalProject\GGselV7\
├── config.py                       ← все настройки (MSB, rate, telemetry, captcha)
├── app.py                          ← Flask + parser_bp
├── MSB_SETUP.md                    ← этот файл
├── parser/
│   ├── parser_engine.py            ← оркестратор, async через asyncio.run
│   ├── msb_fetcher.py              ← главный fetcher (curl-cffi + MSB)
│   ├── profile_pool.py             ← ротация, /profiles/running
│   ├── msb_cookies.py              ← 4-стратегический клиент к MSB
│   ├── adaptive_rate_limiter.py    ← per-profile delay + persist
│   ├── captcha_handler.py          ← детект + solve_via_msb
│   ├── telemetry.py                ← append-only JSONL
│   └── routes.py                   ← /api/parser/* (8 новых)
├── data/
│   ├── db/parser.db                ← SQLite
│   ├── logs/
│   │   ├── parser.log              ← главный лог
│   │   └── profiles.log            ← лог пула
│   ├── rate_state.json             ← state rate limiter
│   └── telemetry/
│       └── events.jsonl            ← телеметрия (ротация по дате/размеру)
└── tests/
    ├── test_rate_limiter.py        ← 11 unit-тестов
    ├── test_captcha_handler.py     ← 21 unit-тестов
    ├── test_telemetry.py           ← 12 unit-тестов
    ├── test_msb_fetcher.py         ← 15 unit-тестов
    ├── smoke_msb_pool.py           ← живой smoke против MSB
    ├── verify_routes.py            ← список эндпоинтов
    ├── verify_config.py            ← config sanity-check
    ├── verify_imports.py           ← все импорты
    ├── verify_e2e.py               ← сводка после e2e запуска
    ├── debug_ggsel_html.py         ← дебаг что реально от ggsel
    └── demo_*.py                   ← демо rate_limiter и telemetry
```
