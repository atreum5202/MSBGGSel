# GGselParser — Архитектура и внутреннее устройство

Документ описывает как парсер работает изнутри: какие модули за что отвечают,
как данные проходят от запроса до БД, и что делать когда что-то ломается.

---

## Содержание

1. [Источник данных](#1-источник-данных)
2. [Поток выполнения — от start() до БД](#2-поток-выполнения--от-start-до-бд)
3. [Full Scan — полный прогон](#3-full-scan--полный-прогон)
4. [Авто-пилот](#4-авто-пилот)
5. [AI-обогащение — Gemini и Groq](#5-ai-обогащение--gemini-и-groq)
6. [Дедупликация](#6-дедупликация)
7. [Токен ggsel](#7-токен-ggsel)
8. [База данных](#8-база-данных)
9. [Телеметрия](#9-телеметрия)
10. [Переменные окружения](#10-переменные-окружения)
11. [Схема модулей](#11-схема-модулей)
12. [Диагностика](#12-диагностика)

---

## 1. Источник данных

Единственный источник — `api.ggsel.com` через Bearer-токен.  
Браузеров, MSB, curl-cffi и HTML-скрапинга нет.

```
ParserEngine / _full_scan_worker
        │
        └── GgselApiClient
              ├── POST /elastic/goods/rec-goods      — листинг по slug категории
              └── POST /elastic/goods/categories     — листинг по content_type_id (рекомендуется)
```

**Почему `content_type_id` лучше slug-а:**  
API эндпоинт `/elastic/goods/categories` принимает числовой ID верхней категории и возвращает больше метаданных (`id_section`, `search_title`, `cnt_sell`). Слаги (`/elastic/goods/rec-goods`) менее стабильны и дают меньше данных.

---

## 2. Поток выполнения — от start() до БД

```
POST /api/parser/start
  {content_type_id, quantity, max_pages, run_ai}
       │
       ▼
 ParserEngine.start()          ← singleton, один на весь процесс
       │
       ├── проверка is_running → 400 если уже работает
       ├── hard cap: quantity ≤ 100, max_pages ≤ 10
       ├── INSERT parser_runs (status=running)
       └── Thread("parser-engine")._run_safe()
                 │
    ┌────────────┴─────────────────────────────────┐
    │              _run_safe()  (фоновый поток)     │
    │                                              │
    │  check_token()                               │
    │    ├── is_token_valid → False → _try_refresh │
    │    └── get_account() → 200 → токен рабочий  │
    │                                              │
    │  asyncio.run(_run_async_api())               │
    │    │                                         │
    │    │  for page in 1..max_pages:              │
    │    │    client.get_products_by_type(ct_id)   │
    │    │    ─ или ─                              │
    │    │    client.get_products(category_slug)   │
    │    │                                         │
    │    │    for item in api_products:            │
    │    │      is_fresh(eid)?   → пропустить      │
    │    │      is_rejected(eid)?→ пропустить      │
    │    │      is_duplicate_name(name)?→ пропустить│
    │    │      to_engine_product(item) → Product  │
    │    │      _calc_raw_score() → profit_score   │
    │    │                                         │
    │    │    _save_batch(batch)                   │
    │    │      → INSERT/UPDATE parsed_products    │
    │    │                                         │
    │    │    _ai_enrich_batch(saved)  если run_ai │
    │    │      → enrich_product() via Gemini/Groq │
    │    │      → UPDATE parsed_products           │
    │    │                                         │
    │    │    sleep(1.0–2.5 сек)                   │
    │    └────────────────────────────────────────┘
    │                                              │
    │  UPDATE parser_runs (status=done)            │
    └──────────────────────────────────────────────┘
```

### Модель `Product`

Внутренняя модель, создаётся через `GgselApiClient.to_engine_product()`:

```python
Product(
    external_id,      # str — id_goods из API
    name,             # str — название товара
    price,            # float — цена в RUB (price_wmr)
    price_usd,        # float — цена в USD (price_wmz)
    price_eur,        # float — цена в EUR (price_wme)
    url,              # str — полный URL карточки на ggsel.net
    seller,           # str — имя продавца
    id_seller,        # int — числовой ID продавца
    rating,           # float | None
    sales_count,      # int | None
    category,         # str — slug или ct_name
    content_type_id,  # int | None
    id_section,       # int | None — ID подкатегории
    search_title,     # str — название подкатегории
    image_url,        # str
    in_stock,         # bool
    profit_score,     # float 0..100 — рассчитывается из sales/rating
)
```

### `_calc_raw_score` — формула profit_score

```python
raw = sales_count * 0.5 + seller_rating * 20.0 + reviews_count * 0.3
if in_stock:
    raw *= 1.2
profit_score = clamp(raw, 0.0, 100.0)
```

---

## 3. Full Scan — полный прогон

Full Scan идёт по всем `content_type_id` из `FULL_SCAN_CONTENT_TYPES` (19 категорий) страница за страницей — без ограничения по `quantity`. Останавливается на категории после 3 пустых страниц подряд.

```
full_scan_start(run_ai, ct_ids)
  └── Thread("full-scan")._full_scan_worker()
        │
        │  for ct_id in ct_ids:        ← Keys, Gifts, DLC, ...
        │    for page in 1..:
        │      client.get_products_by_type(ct_id, page, limit=50)
        │      → если 3 пустых страницы подряд — следующий ct_id
        │
        │      for item in items:
        │        is_fresh / is_rejected → пропустить
        │        to_engine_product() → Product
        │
        │      eng._save_batch(batch)
        │      eng._ai_enrich_batch(saved)  если run_ai
        │
        │      sleep(0.2)   ← мягкий rate limit
        │
        └── st["ct_done"].append(ct_id)
```

**Порядок категорий (19 штук):**

| content_type_id | Название | Примерный объём |
|----------------|----------|-----------------|
| 2 | Keys | ~85k |
| 48 | Gifts | ~83k |
| 19 | DLC | ~56k |
| 54 | Purchasing for account | ~35k |
| 1 | Accounts | ~28k |
| 10 | Item | ~12k |
| 25 | Rent | ~10k |
| 33 | Activation | ~10k |
| 9 | Currency | ~9k |
| 8 | Payment cards | ~8k |
| 11 | Services | ~4k |
| 18 | Subscription services | ~3.5k |
| 6 | Bonus codes | ~1.5k |
| 52 | Gift card | ~1.6k |
| 26 | Promo codes | ~600 |
| 62 | QR code | ~600 |
| 42 | Sale | ~700 |
| 57 | Purchase subscription | ~270 |
| 55 | Hosting | ~105 |

---

## 4. Авто-пилот

Авто-пилот — более лёгкая альтернатива full scan. Перебирает категории из `KNOWN_CATEGORIES` (28 slug-ов) в случайном порядке, запуская стандартный `ParserEngine.start()` для каждой.

```
_ap_worker(limit, mode)
  while not stopped:
    shuffle(KNOWN_CATEGORIES)
    for category_slug in batch_of_3:
      engine.start(category=slug, quantity=limit, max_pages=2)
      wait until engine finishes (max 300 сек)
    cycles += 1
    if mode == "test": break
    sleep(30)
```

**Режимы:**

| mode | limit | поведение |
|------|-------|-----------|
| `test` | 10 | один цикл по всем категориям, затем стоп |
| `turbo` | 100 | непрерывно, пока не остановят вручную |

---

## 5. AI-обогащение — Gemini и Groq

**Модуль:** `parser/content_gen.py`

### Ротация ключей

```
GeminiKeyPool
  ├── читает GEMINI_API_KEYS (запятая) + GEMINI_API_KEY + GEMINI_API_KEY_1/2/...
  ├── get_next_key() → возвращает первый ключ со статусом "ok"
  ├── mark_success(ks) → status = "ok"
  └── mark_fail(ks, error, exhausted)
        ├── exhausted=True  → status = "exhausted" (429, лимит исчерпан)
        └── exhausted=False → status = "error"

GroqKeyPool — аналогичная структура, fallback когда все Gemini исчерпаны
```

### Промт и ответ

`enrich_product(product_dict)` формирует JSON-промт и ожидает от модели:

```json
{
  "generated_title": "краткий русский заголовок",
  "generated_desc":  "описание 2–3 предложения",
  "generated_tags":  "тег1, тег2, тег3",
  "profit_score":    72.5,
  "risk_level":      "low | medium | high",
  "risk_reason":     "..."
}
```

Если ответ не парсится как JSON — возвращается fallback (оригинальные данные без AI-полей).

### Модели

| ENV | По умолчанию |
|-----|-------------|
| `GEMINI_MODEL` | `gemini-2.0-flash` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |

---

## 6. Дедупликация

**Модуль:** `parser/dedup.py`

Перед сохранением каждый товар проверяется тремя фильтрами:

| Функция | Описание |
|---------|----------|
| `is_fresh(external_id)` | Товар уже есть в `parsed_products` — пропустить |
| `is_rejected(external_id)` | Товар в `rejected_products` — пропустить |
| `is_duplicate_name(name)` | Название уже встречалось в этом запуске — пропустить |

`is_duplicate_name` использует in-memory кеш, который сбрасывается через `invalidate_name_cache()` после каждой страницы.

---

## 7. Токен ggsel

**Модуль:** `parser/ggsel_api_client.py`

### Загрузка

```
GgselApiClient._load_tokens()
  ├── data/ggsel_tokens.json существует? → загрузить
  └── нет → использовать дефолтные токены из кода (expire 2026-09-12)
```

### Проверка перед запуском

```
check_token()
  ├── is_token_valid (exp - 5 мин > now) → False → _try_refresh()
  └── True → GET api.ggsel.com/accounts  → 200 = рабочий
                                         → ошибка = False
```

### Обновление

```
_try_refresh()
  └── POST ggsel.net/api/auth/refresh
        {"refresh_token": "..."}
        → {"access_token": "...", "refresh_token": "...", "expires_in": 2678400}
  └── _save_tokens() → data/ggsel_tokens.json
```

### Обновить токен вручную

1. Войти на `ggsel.net` в браузере
2. DevTools → Application → Cookies → скопировать `access_token`
3. Положить в `data/ggsel_tokens.json`:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "6c57...",
  "exp": 1789336235
}
```

---

## 8. База данных

**Файл:** `data/db/parser.db` (SQLite, WAL mode)  
**Инициализация:** `init_db()` запускается при старте Flask (`app.py`) и перед каждым запросом (`before_request`). Идемпотентна — безопасно вызывать многократно.

### Таблица `parsed_products`

| Колонка | Тип | Описание |
|---------|-----|----------|
| `product_id` | TEXT PK | `external_id` из API (id_goods) |
| `title` | TEXT | Оригинальное название |
| `price` | REAL | Цена в RUB |
| `price_usd` | REAL | Цена в USD (wmz) |
| `price_eur` | REAL | Цена в EUR (wme) |
| `content_type_id` | INTEGER | Верхняя категория (2=Keys, 48=Gifts...) |
| `content_type_name` | TEXT | Название верхней категории |
| `id_section` | INTEGER | ID подкатегории |
| `search_title` | TEXT | Название подкатегории |
| `url` | TEXT | URL карточки на ggsel.net |
| `seller_name` | TEXT | Имя продавца |
| `id_seller` | INTEGER | Числовой ID продавца |
| `sales_count` | INTEGER | Количество продаж |
| `in_stock` | INTEGER | 1 = в наличии |
| `profit_score` | REAL | Скор прибыльности 0..100 |
| `status` | TEXT | `parsed` / `ai_recommended` / `approved_by_owner` / `draft_created` / `published` |
| `approval_status` | TEXT | `pending` / `approved` / `rejected` |
| `generated_title` | TEXT | AI-заголовок (Gemini/Groq) |
| `generated_desc` | TEXT | AI-описание |
| `generated_tags` | TEXT | AI-теги |
| `risk_level` | TEXT | `low` / `medium` / `high` |
| `risk_reason` | TEXT | Пояснение к риску |
| `my_price` | REAL | Рекомендованная цена для продажи |
| `economy_complete` | INTEGER | 1 = экономика рассчитана |
| `created_at` | TEXT | ISO timestamp вставки |
| `last_enriched_at` | TEXT | ISO timestamp последнего AI |

### Таблица `parser_runs`

| Колонка | Тип | Описание |
|---------|-----|----------|
| `run_id` | INT PK | Auto-increment |
| `started_at` | TEXT | ISO timestamp |
| `finished_at` | TEXT | ISO timestamp |
| `status` | TEXT | `running` / `done` / `error` |
| `category` | TEXT | Переданный slug или content_type_id |
| `products_saved` | INT | Фактически сохранено |
| `products_ai_enriched` | INT | Обогащено через AI |
| `errors` | TEXT | Текст последней ошибки |

### Другие таблицы

| Таблица | Назначение |
|---------|-----------|
| `parser_log` | Лог событий каждого запуска (run_id, level, message) |
| `categories` | Справочник категорий из `all_cats.json` |
| `rejected_products` | Отклонённые товары (cooldown 7 дней) |
| `resale_deals` | Сделки перепродажи |
| `order_links` | Связка заказов покупателя и поставщика |
| `event_log` | Единый журнал событий жизненного цикла |
| `task_queue` | Очередь фоновых задач |

### Миграции

`_apply_migrations(conn)` добавляет колонки через `ALTER TABLE` при каждом старте. SQLite не поддерживает `IF NOT EXISTS` в `ALTER TABLE`, поэтому сначала проверяется `PRAGMA table_info`.

---

## 9. Телеметрия

**Модуль:** `parser/telemetry.py`

Append-only JSONL файл в `data/telemetry/events.jsonl`.  
При смене даты или превышении 50 МБ — ротация в `events-YYYY-MM-DD.jsonl`.  
Потокобезопасно (lock на append).

**Типы событий:**

| Событие | Когда |
|---------|-------|
| `parser.start` | Запуск сессии |
| `parser.page_fetched` | Страница получена |
| `parser.product_saved` | Товар сохранён |
| `parser.error` | Ошибка |
| `parser.run_complete` | Сессия завершена |
| `parser.fallback` | Переключение источника |

---

## 10. Переменные окружения

Читаются из `.env` в корне проекта через `python-dotenv`.

### Flask

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `LOCAL_PORT` | `5100` | Порт сервера |

### Парсер

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `PARSER_MAX_QUANTITY` | `100` | Hard cap товаров за запуск |
| `PARSER_MAX_PAGES` | `3` | Hard cap страниц |
| `PARSER_DB_PATH` | `data/db/parser.db` | Путь к SQLite |

### Gemini AI

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `GEMINI_API_KEYS` | `""` | Ключи через запятую |
| `GEMINI_API_KEY` | `""` | Одиночный ключ (альтернатива) |
| `GEMINI_API_KEY_1` | `""` | Дополнительный ключ (нумерованные) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Модель |
| `GEMINI_MAX_RETRY` | `3` | Попыток на ключ |
| `GEMINI_TIMEOUT` | `30` | Таймаут запроса (сек) |

### Groq (fallback)

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `GROQ_API_KEYS` | `""` | Ключи через запятую |
| `GROQ_API_KEY` | `""` | Одиночный ключ |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Модель |
| `GROQ_TIMEOUT` | `30` | Таймаут (сек) |

### Телеметрия

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `TELEMETRY_ENABLED` | `true` | Вести телеметрию |
| `TELEMETRY_DIR` | `data/telemetry` | Директория JSONL |
| `TELEMETRY_MAX_FILE_MB` | `50.0` | Порог ротации файла |

### Telegram

| ENV | Дефолт | Описание |
|-----|--------|----------|
| `TELEGRAM_BOT_TOKEN` | `""` | Токен бота (опционально) |
| `TELEGRAM_CHAT_ID` | `""` | ID чата |

---

## 11. Схема модулей

```
app.py
  ├── init_db()                        ← создаёт БД при старте
  └── parser/routes.py                 ← Flask blueprint /api/parser/*
        │
        ├── get_engine()               → ParserEngine (singleton)
        │     ├── start() / stop() / status()
        │     ├── _run_safe()          → _run_async_api()
        │     ├── _save_batch()        → SQLite INSERT/UPDATE
        │     └── _ai_enrich_batch()   → content_gen.enrich_product()
        │
        ├── full_scan_start/stop/status
        │     └── _full_scan_worker()  → GgselApiClient + _save_batch
        │
        ├── parser/ggsel_api_client.py
        │     ├── GgselApiClient       — Bearer-токен, api.ggsel.com
        │     ├── get_products()       → /elastic/goods/rec-goods
        │     ├── get_products_by_type()→ /elastic/goods/categories
        │     ├── to_engine_product()  → Product
        │     └── check_token()        → GET /accounts
        │
        ├── parser/content_gen.py
        │     ├── GeminiKeyPool        — ротация Gemini ключей
        │     ├── GroqKeyPool          — ротация Groq ключей (fallback)
        │     └── enrich_product()     — промт → JSON-ответ
        │
        ├── parser/dedup.py
        │     ├── is_fresh()           — проверка в parsed_products
        │     ├── is_rejected()        — проверка в rejected_products
        │     └── is_duplicate_name()  — in-memory кеш имён
        │
        ├── parser/db_init.py
        │     ├── init_db()            — CREATE TABLE IF NOT EXISTS + миграции
        │     └── get_db_path()        — путь к parser.db
        │
        ├── parser/telemetry.py        — JSONL append-only телеметрия
        ├── parser/event_logger.py     — журнал в таблицу event_log
        ├── parser/category_catalog.py — поиск по справочнику categories
        ├── parser/categories.py       — утилиты категорий
        └── parser/tg_bot.py           — Telegram-уведомления
```

---

## 12. Диагностика

### Парсер запустился, товаров 0

**1. Проверить статус токена:**
```bash
GET /api/parser/config
# token.valid = false → обновить data/ggsel_tokens.json
```

**2. Проверить что API отвечает:**
```bash
GET /api/parser/runs/<run_id>/log
# искать строки с "ошибка" или "пусто"
```

**3. Все страницы пустые — возможно категория пустая:**
```bash
# Попробовать другой content_type_id
POST /api/parser/start
{"content_type_id": 2, "quantity": 10, "max_pages": 1, "run_ai": false}
```

---

### Токен просрочен

```
# Лог: "Токен недействителен — обнови data/ggsel_tokens.json"
# Статус токена:
GET /api/parser/config → token.valid = false
```

Действия:
1. Войти на `ggsel.net` в браузере
2. DevTools → Application → Cookies → `access_token`
3. Обновить `data/ggsel_tokens.json`
4. Перезапустить парсер или дождаться следующего запуска — токен подтянется автоматически

---

### Gemini не работает

```bash
# Проверить состояние ключей:
GET /api/parser/gemini/status
# exhausted = все ключи лимитированы
# error = ключ невалиден

# Сбросить статус (квота обновляется раз в сутки):
POST /api/parser/gemini/reset

# Тест:
POST /api/parser/gemini/test
```

Если Gemini полностью недоступен — парсинг работает без AI (`run_ai=false`), товары сохраняются со статусом `parsed`.

---

### Логи

| Файл | Содержит |
|------|----------|
| `data/logs/parser.log` | Основной лог: запросы, страницы, товары, ошибки |
| `data/telemetry/events.jsonl` | Структурированные события (JSONL) |

```bash
# Лог конкретного запуска:
GET /api/parser/runs/<run_id>/log

# Последние события телеметрии:
GET /api/parser/telemetry/recent?limit=50
```
