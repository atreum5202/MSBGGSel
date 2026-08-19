# GGsel Parser — Документация

> Последнее обновление: 2026-08-14  
> Состояние БД на момент аудита: **149 товаров** (реально в файле), WAL-буфер: **4.0 MB**

---

## Архитектура

```
ggsel.net API (api.ggsel.com)
        │
        │  POST /elastic/goods/rec-goods
        │  Bearer <token>
        ▼
┌─────────────────────────────────────────────┐
│              parse_all.py                   │
│  Запускает 5 subprocess'ов bulk_parse.py    │
│  Фоновый монитор БД каждые 30 сек          │
└──────┬──────┬──────┬──────┬─────────────────┘
       │      │      │      │
  W1(tok=0) W2(tok=1) W3(tok=2) W4(tok=3) W5(tok=4)
       │
┌──────▼──────────────────────────────────────┐
│          bulk_parse.py (каждый процесс)     │
│  4–3 asyncio-воркера внутри одного процесса │
│  Читает категории из seller_categories.json │
│  Пишет в SQLite WAL-режиме                  │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
           data/db/parser.db  (WAL)
           static/products/   (фото товаров)
```

### Что парсится за один проход

| Шаг | Действие |
|-----|----------|
| 1 | Загружает список leaf-категорий из `seller_categories.json` (3 572 штуки) |
| 2 | Для каждой категории делает POST-запросы постранично (до 10 страниц × 50 товаров = 500 товаров/кат) |
| 3 | Каждый товар сохраняет в `parsed_products` через `INSERT … ON CONFLICT DO UPDATE` |
| 4 | Параллельно скачивает изображения в `static/products/` |
| 5 | Каждые 30 сек пишет прогресс в `data/bulk_parse_progress_wN.json` |

---

## Файлы и их роли

| Файл | Роль | Важность |
|------|------|----------|
| `bulk_parse.py` | Главный скрипт парсинга, asyncio | 🔴 Критический |
| `parse_all.py` | Запускает 5 процессов bulk_parse параллельно | 🔴 Критический |
| `parser/db_init.py` | Инициализация схемы БД, миграции | 🔴 Критический |
| `parser/schema.sql` | SQL-схема всех таблиц | 🔴 Критический |
| `parser/ggsel_api_client.py` | Синхронный API-клиент (singleton) | 🟡 Важный |
| `data/ggsel_tokens.json` | JWT-токены всех 5 аккаунтов | 🔴 Критический |
| `data/seller_categories.json` | Список всех 3572 leaf-категорий с `fee` | 🔴 Критический |
| `data/db/parser.db` | База данных SQLite (WAL-режим) | 🔴 Критический |
| `data/bulk_parse_progress_wN.json` | Прогресс N-го воркера (0–4) | 🟡 Важный |
| `static/products/` | Локальные копии фото товаров | 🟢 Вторичный |
| `parser_config.json` | Конфигурация всего пайплайна | 🟡 Важный |

---

## Токены (`data/ggsel_tokens.json`)

### Структура файла

```json
{
  "access_token":  "<JWT воркера 0, аккаунт ggsel_parser_1>",
  "refresh_token": "<hex-строка>",
  "profile_id":    "<UUID профиля>",
  "profile_name":  "ggsel_parser_1",
  "saved_at":      "2026-08-14",
  "expires_at":    "2026-09-13",

  "extra_tokens": [
    {
      "access_token":  "<JWT воркера 1>",
      "refresh_token": "<hex>",
      "profile_id":    "<UUID>",
      "profile_name":  "ggsel_parser_2",
      "expires_at":    "2026-09-13"
    }
    // ... ещё 3 объекта (индексы 2, 3, 4)
  ]
}
```

### Соответствие индексов и аккаунтов

| `--token-index` | Откуда берётся токен | Аккаунт |
|:-:|---|---|
| `0` | `data["access_token"]` | artur.doil1891 |
| `1` | `data["extra_tokens"][0]` | atreum.5202 |
| `2` | `data["extra_tokens"][1]` | abdurashidov.private |
| `3` | `data["extra_tokens"][2]` | abdurashidov.business |
| `4` | `data["extra_tokens"][3]` | boris.liron |

### Срок жизни токенов

- JWT-токены живут **~31 день** (выдаются на ggsel.net)
- Текущие токены истекают **2026-09-12 / 2026-09-13**
- При истечении: `bulk_parse.py` начнёт получать HTTP 401 и останавливаться с воркерами

### Как добавить новый токен

Смотри раздел **«Как добавить новый аккаунт/токен»** ниже.

---

## Запуск парсера

### Быстрый старт (все 5 воркеров параллельно)

```bash
python parse_all.py
```

Запустит 5 процессов `bulk_parse.py`, каждый с `--workers 3` (итого 15 asyncio-воркеров).
В терминале будут цветные логи всех воркеров + строка монитора каждые 30 секунд:

```
[MONITOR] БД: 234,500 товаров | WAL: 4.2MB | Процессов: 5 живых
         Прогресс воркеров: W1=45,200 | W2=43,100 | ... | Итого saved=186,400
```

### Ручной запуск одного воркера

```bash
# Аккаунт 0 (artur.doil1891), категории с 0 по 456
python bulk_parse.py --workers 4 --token-index 0 --start-from 0 --limit 456

# Аккаунт 1 (atreum.5202), следующие 456 категорий
python bulk_parse.py --workers 4 --token-index 1 --start-from 456 --limit 456
```

### Параметры `bulk_parse.py`

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|:---:|---------|
| `--workers` | int | `4` | Кол-во asyncio-воркеров **внутри** этого процесса |
| `--start-from` | int | `0` | Индекс первой категории (в отсортированном списке leaf'ов) |
| `--limit` | int | `0` | Кол-во категорий (0 = все оставшиеся) |
| `--token-index` | int | `0` | Индекс токена из `ggsel_tokens.json` (0–4) |

---

## База данных (`data/db/parser.db`)

SQLite в WAL-режиме. Размер основного файла ~2 MB, WAL-буфер может вырасти до 10–20 MB во время парсинга.

### Таблица `parsed_products` — ключевые колонки

| Колонка | Тип | Смысл |
|---------|-----|-------|
| `product_id` | TEXT PK | ID товара на ggsel.net (`id_goods`) |
| `title` | TEXT | Название товара |
| `price` | REAL | Цена продавца в рублях (WMR) |
| `source_price` | REAL | Цена в WMZ (доллары) — для расчёта экономики |
| `my_price` | REAL | Рекомендуемая цена перепродажи (price × (1 + fee)) |
| `category` | TEXT | Путь категории (обрезан до 80 символов) |
| `seller_name` | TEXT | Логин продавца на ggsel |
| `sales_count` | INTEGER | Кол-во продаж |
| `image_url` | TEXT | Ссылка на фото (CDN ggsel) |
| `local_image_path` | TEXT | Локальный путь `/static/products/ID.webp` |
| `profit_score` | REAL | Скор 0–100 (продажи / 10 × активность) |
| `status` | TEXT | Жизненный цикл: `parsed → economics_checked → approved_by_owner → published` |
| `approval_status` | TEXT | `pending / approved / rejected` — решение владельца |
| `last_parsed_at` | TEXT | ISO timestamp последнего обновления |

### Все таблицы

| Таблица | Назначение |
|---------|-----------|
| `parsed_products` | Основная таблица товаров |
| `categories` | Справочник категорий (4785 записей) |
| `rejected_products` | Отклонённые товары (cooldown 7 дней) |
| `parser_runs` | История запусков парсера (19 запусков) |
| `parser_log` | Лог событий текущего запуска |
| `task_queue` | Очередь фоновых задач |
| `resale_deals` | Сделки перепродажи (ШАГ 11) |
| `order_links` | Связка заказа покупателя с источником |
| `event_log` | Единый журнал событий с индексами |

### Как проверить результат

```python
import sqlite3
conn = sqlite3.connect("data/db/parser.db")

# Реальное кол-во товаров (сбрасывает WAL-буфер)
conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
total = conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0]
print(f"Товаров в БД: {total:,}")

# Топ-10 по продажам
for row in conn.execute("""
    SELECT product_id, title, price, sales_count, category
    FROM parsed_products
    ORDER BY sales_count DESC LIMIT 10
"""):
    print(row)

conn.close()
```

```bash
# Или через sqlite3 CLI:
sqlite3 data/db/parser.db "SELECT COUNT(*) FROM parsed_products;"
sqlite3 data/db/parser.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## Известные проблемы и ограничения

### 🔴 WAL-буфер скрывает реальное состояние БД

**Проблема:** Прогресс-файлы (`bulk_parse_progress_wN.json`) показывают счётчик `saved` из памяти процесса. Но SQLite в WAL-режиме не сбрасывает страницы на диск пока процессы живы. В реальном файле `parser.db` может быть значительно меньше записей.

**Пример:** Прогресс показывает `saved=184,500`, в БД реально `149` записей (WAL-буфер 4 MB).

**Как проверить реальное состояние:**
```python
conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
n = conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0]
```

**Решение:** WAL автоматически сбрасывается при завершении всех процессов. Можно принудительно сбросить: `PRAGMA wal_checkpoint(TRUNCATE)`.

---

### 🟡 5 процессов пишут в одну SQLite через WAL

**Проблема:** SQLite WAL допускает несколько reader'ов + один writer одновременно. При 5 параллельных процессах-писателях возникают `SQLITE_BUSY` (timeout 30 сек задан). В экстремальной нагрузке возможны задержки.

**Текущий митигейшн:** `conn = sqlite3.connect(DB_PATH, timeout=30)` — каждый вызов `_db_save` открывает новое соединение и закрывает его после `commit()`.

**Лучшее решение (будущее):** Выделить один процесс-писатель с очередью или переключиться на PostgreSQL.

---

### 🟡 Хардкод диапазонов категорий в `parse_all.py`

**Проблема:** `WORKERS` в `parse_all.py` содержит фиксированные `start` и `limit`. При следующем запуске (докачка с другой позиции) нужно вручную редактировать файл.

```python
WORKERS = [
    {"token": 0, "start": 1291, "limit": 456, ...},
    ...
]
```

**Обходной путь:** Запускать `bulk_parse.py` напрямую с нужными `--start-from` и `--limit` (см. раздел «Как докачать»).

---

### 🟡 Прогресс-файл раньше писался только от W1

**Статус: ИСПРАВЛЕНО** в текущей версии.

Теперь каждый процесс пишет свой файл `data/bulk_parse_progress_wN.json` по `--token-index = N`. Монитор в `parse_all.py` читает все файлы и показывает сводку.

---

### 🟢 `--limit 0` для последнего воркера

**Поведение:** `boris.liron` (W5) запускается с `--limit 0`, что означает «все оставшиеся категории от `start=3115` до конца». Это корректно, но при добавлении новых категорий он заберёт их автоматически.

---

### 🟢 MAX_PAGES = 10 (500 товаров/категорию)

В `bulk_parse.py` задан лимит `MAX_PAGES = 10` → максимум 500 товаров на категорию. Категории с более чем 500 товарами будут недопарсены (API отдаёт постранично по 50).

---

## Как докачать, если парсер не собрал всё

### 1. Проверьте что уже есть в БД

```python
import sqlite3
conn = sqlite3.connect("data/db/parser.db")
conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
print(conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0])
# Посмотреть какие категории уже есть:
for row in conn.execute("SELECT DISTINCT category FROM parsed_products LIMIT 20"):
    print(row[0])
conn.close()
```

### 2. Посмотрите прогресс-файлы

```bash
# Windows PowerShell:
Get-Content data\bulk_parse_progress_w*.json
```

или в Python:
```python
import json
from pathlib import Path
for f in sorted(Path("data").glob("bulk_parse_progress_w*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    print(f"{f.name}: saved={d['saved']:,}  total_cats={d['total_cats']}")
```

### 3. Найдите с какого индекса докачивать

```python
import json
from pathlib import Path

data = json.loads(Path("data/seller_categories.json").read_text(encoding="utf-8"))
leaves = [d for d in data if not d.get("has_children") and d.get("id")]
print(f"Всего leaf-категорий: {len(leaves)}")
# leaves[1291] — категория на индексе 1291
print(leaves[1291])
```

### 4. Запустите докачку вручную

```bash
# Допустим нужно докачать категории 500–1000 аккаунтом 0:
python bulk_parse.py --token-index 0 --start-from 500 --limit 500 --workers 4

# Или запустите parse_all.py с изменёнными WORKERS
# (отредактируйте parse_all.py перед запуском)
python parse_all.py
```

### 5. После завершения — сбросьте WAL

```bash
sqlite3 data/db/parser.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## Как добавить новый аккаунт/токен

### Шаг 1. Получите токен через браузер

1. Откройте DevTools → Network на сайте `ggsel.net`
2. Войдите в новый аккаунт
3. Найдите любой запрос к `api.ggsel.com` → Headers → `Authorization: Bearer <TOKEN>`
4. Скопируйте JWT-токен (строка начинается с `eyJ...`)

### Шаг 2. Добавьте в `data/ggsel_tokens.json`

```json
{
  "extra_tokens": [
    ...,
    {
      "access_token":  "eyJ...<новый токен>...",
      "refresh_token": "<hex refresh token если есть>",
      "profile_id":    "<UUID или пустая строка>",
      "profile_name":  "новый_аккаунт",
      "expires_at":    "2026-10-ХХ"
    }
  ]
}
```

> ⚠️ Новый токен получит индекс `len(extra_tokens) - 1` → `--token-index = индекс + 1`.

### Шаг 3. Добавьте воркер в `parse_all.py`

```python
WORKERS = [
    ...,  # существующие
    {"token": 5, "start": <ВАША_ПОЗИЦИЯ>, "limit": 0, "account": "новый_аккаунт"},
]
```

### Шаг 4. Проверьте токен

```python
from bulk_parse import _load_token
tok = _load_token(5)
print(tok[:30], "...")  # должен вернуть новый JWT
```

---

## Планы по улучшению

| Приоритет | Задача | Сложность |
|:---------:|--------|:---------:|
| 🔴 Высокий | Автоматическое распределение категорий по воркерам (без хардкода `start`/`limit`) | Средняя |
| 🔴 Высокий | Принудительный `wal_checkpoint` после каждых N батчей чтобы прогресс-файл отражал реальность | Низкая |
| 🟡 Средний | Отдельный процесс-писатель (один writer + очередь) чтобы убрать конкуренцию 5 процессов за SQLite | Высокая |
| 🟡 Средний | Возобновление с последней успешной категории (checkpoint per worker по `cat_id`, не по индексу) | Средняя |
| 🟡 Средний | Авто-обновление токенов через refresh_token при получении HTTP 401 (сейчас только в API-клиенте) | Средняя |
| 🟢 Низкий | Web-интерфейс для мониторинга прогресса вместо чтения прогресс-файлов | Высокая |
| 🟢 Низкий | Сохранение `category_id` в `parsed_products` (сейчас пишется только строка `category`) | Низкая |
| 🟢 Низкий | Поддержка `MAX_PAGES > 10` для категорий с тысячами товаров | Низкая |

---

## Быстрая шпаргалка

```bash
# Запустить всё
python parse_all.py

# Проверить реальный счётчик в БД
sqlite3 data/db/parser.db "PRAGMA wal_checkpoint(PASSIVE); SELECT COUNT(*) FROM parsed_products;"

# Принудительно сбросить WAL на диск
sqlite3 data/db/parser.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Посмотреть прогресс всех воркеров
python -c "
import json; from pathlib import Path
for f in sorted(Path('data').glob('bulk_parse_progress_w*.json')):
    d = json.loads(f.read_text(encoding='utf-8'))
    print(f\"{f.name}: saved={d['saved']:,} / total_cats={d['total_cats']} @ {d['updated_at']}\")
"

# Посмотреть последние 10 спаршенных товаров
sqlite3 data/db/parser.db "SELECT product_id, title, price, sales_count FROM parsed_products ORDER BY last_parsed_at DESC LIMIT 10;"
```
