# GGselParser — API Reference

Все эндпоинты доступны по базовому URL `http://localhost:5100`.  
Blueprint prefix: `/api/parser` — добавляется ко всем путям ниже.

---

## Содержание

1. [Управление парсером](#1-управление-парсером)
2. [Товары — CRUD и воркфлоу](#2-товары--crud-и-воркфлоу)
3. [Gemini — AI-обогащение](#3-gemini--ai-обогащение)
4. [Телеметрия](#4-телеметрия)
5. [Авто-пилот](#5-авто-пилот)
6. [Full Scan — полный прогон](#6-full-scan--полный-прогон)

---

## 1. Управление парсером

### `POST /api/parser/start`

Запускает сессию парсинга в фоновом потоке. Одновременно может работать только один запуск.

**Тело запроса (JSON):**

| Поле | Тип | По умолчанию | Описание |
|------|-----|-------------|----------|
| `content_type_id` | int | — | **Рекомендуется.** ID верхней категории (см. таблицу ниже) |
| `category` | string | `""` | Slug категории, например `"spotify-premium"` (устаревший способ) |
| `query` | string | `""` | Поисковый запрос (устаревший, ненадёжный) |
| `quantity` | int | `20` | Максимум товаров (hard cap — 100) |
| `max_pages` | int | `3` | Максимум страниц (hard cap — 10) |
| `run_ai` | bool | `true` | Запускать Gemini/Groq AI-обогащение |

**Таблица `content_type_id`:**

| ID | Название |
|----|----------|
| `2` | Keys |
| `48` | Gifts |
| `19` | DLC |
| `54` | Purchasing for account |
| `1` | Accounts |
| `10` | Item |
| `25` | Rent |
| `33` | Activation |
| `9` | Currency |
| `8` | Payment cards |
| `11` | Services |
| `18` | Subscription services |
| `6` | Bonus codes |
| `52` | Gift card |
| `26` | Promo codes |

**Ответ:**

```json
{ "ok": true, "run_id": 42, "message": "Запущен (q='', cat='', qty=50)", "hard_cap_quantity": 100 }
```

**Ошибки:**
- `400` — не указан ни `content_type_id`, ни `category`, ни `query`
- `400` — парсер не смог запуститься (например, токен недействителен)

---

### `POST /api/parser/stop`

Останавливает текущий запуск парсера (посылает сигнал фоновому потоку).

**Ответ:**

```json
{ "ok": true, "message": "Stop signal sent" }
```

---

### `GET /api/parser/status`

Текущее состояние парсера и статистика последнего запуска.

**Ответ:**

```json
{
  "is_running": true,
  "run_id": 42,
  "status": "running",
  "products_found": 80,
  "products_saved": 35,
  "products_ai_enriched": 30,
  "pages_scanned": 2,
  "errors_count": 0,
  "last_query": "",
  "last_category": "2",
  "fetcher_used": "api_client",
  "last_started_at": "2026-08-14T10:00:00",
  "last_finished_at": null,
  "hard_cap_quantity": 100,
  "leaf_categories_count": 214
}
```

---

### `GET /api/parser/config`

Текущая конфигурация: настройки Flask, парсера, статус токена ggsel, статус Gemini пула.

**Ответ:**

```json
{
  "server":    { "local_port": 5100 },
  "parser":    { "max_quantity": 100, "max_pages": 3 },
  "telemetry": { "enabled": true, "dir": "data/telemetry" },
  "hard_cap_quantity": 100,
  "content_types": { "2": "Keys", "48": "Gifts" },
  "token": { "valid": true, "exp_unix": 1789336235 },
  "gemini": { "total": 2, "ok": 2, "exhausted": 0, "available": true }
}
```

---

### `GET /api/parser/stats`

Агрегированная статистика по базе данных.

**Ответ:**

```json
{
  "total_products": 583,
  "by_status": [
    { "status": "parsed", "n": 410 },
    { "status": "ai_recommended", "n": 120 },
    { "status": "approved_by_owner", "n": 53 }
  ],
  "last_run": {
    "run_id": 42,
    "started_at": "2026-08-14T10:00:00",
    "finished_at": "2026-08-14T10:05:00",
    "status": "done",
    "products_saved": 35,
    "products_ai_enriched": 30
  }
}
```

---

### `GET /api/parser/runs`

История запусков парсера (пагинация).

**Query params:** `page` (int, default `1`), `limit` (int 1–100, default `20`)

**Ответ:**

```json
{
  "page": 1, "limit": 20, "total": 7,
  "items": [
    {
      "run_id": 42, "started_at": "...", "finished_at": "...",
      "status": "done", "query": "", "category": "2",
      "quantity": 50, "max_pages": 3,
      "products_saved": 35, "products_ai_enriched": 30, "errors": ""
    }
  ]
}
```

---

### `GET /api/parser/runs/<run_id>/log`

Лог событий конкретного запуска.

**Query params:** `limit` (int 1–1000, default `200`)

**Ответ:**

```json
{
  "run_id": 42,
  "items": [
    { "log_id": 1, "level": "info", "message": "АПИ старт: ct=2 qty=50", "created_at": "..." }
  ]
}
```

---

## 2. Товары — CRUD и воркфлоу

### Жизненный цикл товара

```
parsed → ai_recommended → approved (approval_status)
                       ↓
                   rejected (approval_status)
```

Поле `status` описывает этап обработки:  
`parsed` → `ai_recommended` (после Gemini) → `approved_by_owner` → `draft_created` → `published`

Поле `approval_status` — решение оператора:  
`pending` → `approved` / `rejected`

---

### `GET /api/parser/products`

Пагинированный список товаров с фильтрацией.

**Query params:**

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|-------------|----------|
| `page` | int | `1` | Номер страницы |
| `limit` | int | `50` | Записей на страницу (1–200) |
| `status` | string | `"parsed"` | Фильтр по `status` (parsed/ai_recommended/...) или по `approval_status` (approved/rejected/published) |
| `q` | string | — | Поиск по названию товара |

**Ответ:**

```json
{
  "page": 1, "limit": 50, "total": 410,
  "items": [
    {
      "product_id": "102276701",
      "title": "[US] Apple ID • 6 Months",
      "price": 130.0,
      "price_usd": 1.45,
      "content_type_id": 1,
      "sales_count": 42,
      "status": "parsed",
      "approval_status": "pending"
    }
  ]
}
```

---

### `GET /api/parser/products/<product_id>`

Один товар по ID.

**Ответ:** `{ "ok": true, "product": { ... } }` или `404`.

---

### `DELETE /api/parser/products/<product_id>`

Удаляет товар из базы. Действие необратимо.

**Ответ:** `{ "ok": true, "deleted": 1 }` или `404`.

---

### `PATCH /api/parser/products/<product_id>`

Обновляет редактируемые поля товара.

**Тело запроса (JSON) — любой subset допустимых полей:**

| Поле | Тип | Описание |
|------|-----|----------|
| `generated_title` | string | AI-заголовок |
| `generated_desc` | string | AI-описание |
| `generated_tags` | string | AI-теги |
| `my_price` | float | Итоговая цена товара |

**Ответ:**

```json
{ "ok": true, "updated": ["generated_title", "my_price"] }
```

`400` если ни одно допустимое поле не передано.

---

### `POST /api/parser/products/<product_id>/approve`

Устанавливает `approval_status = "approved"`.

**Ответ:**

```json
{ "ok": true, "product_id": "102276701", "approval_status": "approved" }
```

---

### `POST /api/parser/products/<product_id>/reject`

Устанавливает `approval_status = "rejected"`.

**Ответ:**

```json
{ "ok": true, "product_id": "102276701", "approval_status": "rejected" }
```

---

### `POST /api/parser/products/batch_approve`

Массовое одобрение списка товаров.

**Тело запроса:**

```json
{ "ids": ["102276701", "102240902", "103001234"] }
```

**Ответ:** `{ "ok": true, "count": 3 }`  
`400` если `ids` не передан.

---

### `POST /api/parser/products/batch_reject`

Массовый отказ списка товаров.

**Тело запроса:**

```json
{ "ids": ["102276701", "102240902"] }
```

**Ответ:** `{ "ok": true, "count": 2 }`

---

### `POST /api/parser/products/<product_id>/rewrite`

Перегенерирует AI-поля товара через Gemini/Groq.  
Обновляет: `generated_title`, `generated_desc`, `generated_tags`, `profit_score`, `risk_level`, `risk_reason`, `status = "ai_recommended"`, `last_enriched_at`.

**Ответ:**

```json
{ "ok": true, "product": { "generated_title": "...", "generated_desc": "...", "..." } }
```

`404` если товар не найден. `500` при ошибке Gemini.

---

## 3. Gemini — AI-обогащение

### `GET /api/parser/gemini/status`

Статус всех Gemini API ключей (ротация).

**Ответ:**

```json
{
  "total": 2,
  "ok": 1,
  "exhausted": 1,
  "error": 0,
  "available": true,
  "keys": [
    {
      "index": 0,
      "masked": "AIzaSy...xxxx",
      "status": "ok",
      "last_error": null,
      "last_used_at": "2026-08-14T11:00:00",
      "fail_count": 0,
      "success_count": 42
    },
    {
      "index": 1,
      "masked": "AIzaSy...yyyy",
      "status": "exhausted",
      "last_error": "429 Resource exhausted",
      "fail_count": 3,
      "success_count": 100
    }
  ]
}
```

---

### `POST /api/parser/gemini/reset`

Сбрасывает статус ключей обратно в `"ok"`.

**Тело запроса:**

```json
{ "index": 1 }
```

Без `index` — сбрасывает все ключи.

**Ответ:** `{ "ok": true, "reset": "key #1" }` или `{ "ok": true, "reset": "all" }`

---

### `POST /api/parser/gemini/test` (также `GET`)

Тест активного Gemini ключа на реальном запросе без запуска парсера.

**Тело запроса (все поля опциональны):**

| Поле | По умолчанию |
|------|-------------|
| `title` | `"Windows 11 Pro Key"` |
| `category` | `"software"` |
| `price` | `800.0` |
| `sales_count` | `42` |
| `seller_rating` | `4.8` |
| `reviews_count` | `15` |

**Ответ:**

```json
{
  "ok": true,
  "result": {
    "generated_title": "...",
    "generated_desc": "...",
    "generated_tags": "...",
    "profit_score": 74.5,
    "risk_level": "low"
  },
  "gemini_pool": { "total": 2, "ok": 2 }
}
```

`500` при ошибке с полем `"trace"`.

---

## 4. Телеметрия

Локальная append-only телеметрия, пишется в `data/telemetry/events.jsonl`.  
Ничего не отправляется наружу.

### `GET /api/parser/telemetry/recent`

Последние N событий из текущего JSONL файла.

**Query params:** `limit` (int 1–1000, default `50`)

**Ответ:**

```json
{
  "limit": 50,
  "count": 12,
  "items": [
    { "ts": "2026-08-14T10:01:00Z", "event": "parser.product_saved", "category": "Keys" }
  ]
}
```

---

### `GET /api/parser/telemetry/stats`

Сводка по файлам телеметрии на диске.

**Ответ:**

```json
{
  "enabled": true,
  "storage_dir": "data/telemetry",
  "current_file": "events.jsonl",
  "max_file_size_mb": 50.0,
  "total_files": 4,
  "total_size_bytes": 1048576,
  "current_size_bytes": 204800
}
```

---

## 5. Авто-пилот

Авто-пилот — фоновый поток, который последовательно перебирает категории из `KNOWN_CATEGORIES` и запускает парсер для каждой.

### `POST /api/parser/auto/start`

**Тело запроса:**

| Поле | Тип | По умолчанию | Описание |
|------|-----|-------------|----------|
| `mode` | string | `"test"` | `"test"` — один цикл (limit=10 товаров), `"turbo"` — непрерывно (limit=100) |

**Ответ:**

```json
{ "ok": true, "mode": "turbo", "limit": 100 }
```

`409` если авто-пилот уже запущен.

---

### `POST /api/parser/auto/stop`

Останавливает авто-пилот и текущий запуск парсера.

**Ответ:** `{ "ok": true, "message": "Авто-пилот остановлен" }`

---

### `GET /api/parser/auto/status`

**Ответ:**

```json
{
  "running": true,
  "stopped": false,
  "mode": "turbo",
  "limit": 100,
  "current_category": "spotify-premium",
  "next_categories": ["games-steam", "robux"],
  "cycles": 3,
  "last_error": null,
  "started_at": "2026-08-14T10:00:00"
}
```

---

## 6. Full Scan — полный прогон

Full Scan обходит все `content_type_id` из фиксированного списка (19 категорий) страница за страницей через `api.ggsel.com`. Не ограничен `quantity`, идёт до конца каждой категории.

**Порядок категорий:** Keys → Gifts → DLC → Purchasing for account → Accounts → Item → Rent → Activation → Currency → Payment cards → Services → Subscription services → Bonus codes → Gift card → Promo codes → QR code → Sale → Purchase subscription → Hosting

### `POST /api/parser/full-scan/start`

**Тело запроса:**

| Поле | Тип | По умолчанию | Описание |
|------|-----|-------------|----------|
| `run_ai` | bool | `false` | Запускать AI-обогащение для каждого товара |
| `ct_ids` | list[int] | все 19 | Ограничить прогон конкретными `content_type_id` |

**Ответ:**

```json
{ "ok": true, "categories": 19, "run_ai": false }
```

`409` если full scan уже запущен.

---

### `POST /api/parser/full-scan/stop`

**Ответ:** `{ "ok": true, "message": "Фулл-скан остановлен" }`

---

### `GET /api/parser/full-scan/status`

**Ответ:**

```json
{
  "running": true,
  "stopped": false,
  "current_ct_id": 48,
  "current_ct_name": "Gifts",
  "current_page": 7,
  "total_saved": 1240,
  "total_found": 1800,
  "ct_done": [2],
  "ct_remaining": [19, 54, 1, 10],
  "started_at": "2026-08-14T08:00:00",
  "last_error": null,
  "run_ai": false,
  "progress_pct": 5.3,
  "available_content_types": { "2": "Keys", "48": "Gifts" }
}
```

---

## Health-check

### `GET /health`

**Ответ:** `{ "ok": true, "service": "GGselParser" }`

---

## Шпаргалка

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/parser/start` | Запустить парсинг |
| `POST` | `/api/parser/stop` | Остановить парсер |
| `GET` | `/api/parser/status` | Статус парсера |
| `GET` | `/api/parser/config` | Конфигурация |
| `GET` | `/api/parser/stats` | Статистика БД |
| `GET` | `/api/parser/runs` | История запусков |
| `GET` | `/api/parser/runs/<id>/log` | Лог запуска |
| `GET` | `/api/parser/products` | Список товаров |
| `GET` | `/api/parser/products/<id>` | Один товар |
| `DELETE` | `/api/parser/products/<id>` | Удалить товар |
| `PATCH` | `/api/parser/products/<id>` | Обновить поля |
| `POST` | `/api/parser/products/<id>/approve` | Одобрить |
| `POST` | `/api/parser/products/<id>/reject` | Отклонить |
| `POST` | `/api/parser/products/batch_approve` | Массовое одобрение |
| `POST` | `/api/parser/products/batch_reject` | Массовый отказ |
| `POST` | `/api/parser/products/<id>/rewrite` | Перегенерировать тексты |
| `GET` | `/api/parser/gemini/status` | Статус Gemini ключей |
| `POST` | `/api/parser/gemini/reset` | Сбросить ключи |
| `POST` | `/api/parser/gemini/test` | Тест Gemini |
| `GET` | `/api/parser/telemetry/recent` | Последние события |
| `GET` | `/api/parser/telemetry/stats` | Статистика телеметрии |
| `POST` | `/api/parser/auto/start` | Запустить авто-пилот |
| `POST` | `/api/parser/auto/stop` | Остановить авто-пилот |
| `GET` | `/api/parser/auto/status` | Статус авто-пилота |
| `POST` | `/api/parser/full-scan/start` | Запустить full scan |
| `POST` | `/api/parser/full-scan/stop` | Остановить full scan |
| `GET` | `/api/parser/full-scan/status` | Статус full scan |
| `GET` | `/health` | Health-check |
