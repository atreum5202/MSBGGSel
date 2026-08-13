# AGENTS.md — Карта проекта GGselMSB

## Что делает проект
Автоматизированная панель продавца на маркетплейсе ggsel.net:
парсит товары конкурентов → AI обогащает карточки → модерация → публикация в свой магазин.

## Стек
- Flask 3.x (app.py) — веб-панель, порт 5000
- MSB (собственный антидетект) — CloakBrowser профили, порт 17248
- Gemini web (через браузер MSB) — генерация и рестайл фото
- Gemini API (ключи) — генерация текстов/описаний
- GGSEL Seller API — публикация товаров
- SQLite — хранилище (data/db/parser.db)
- Telegram бот — уведомления

## Быстрый старт
Двойной клик `start.vbs` — запускает Flask + бот + watchdog, открывает браузер.
Требования: MSB запущен на http://127.0.0.1:17248, .env заполнен.

## Карта файлов

### Точки входа
| Файл | Назначение |
|---|---|
| `app.py` | Flask-приложение, регистрирует blueprint'ы |
| `config.py` | Все настройки и ENV переменные |
| `start.vbs` | Запуск всего (Flask + бот + watchdog) |
| `watchdog.py` | Мониторинг Flask и MSB, авторестарт |
| `parser_config.json` | Конфиг парсера (категории, лимиты, Gemini группа) |

### Основной движок (parser/)
| Файл | Назначение |
|---|---|
| `routes.py` | Все /api/parser/* эндпоинты (~1500 строк, есть оглавление вверху) |
| `parser_engine.py` | Оркестратор парсинга, CascadeFetcher (MSB → cffi fallback) |
| `db_init.py` | Инициализация SQLite, `get_db_path()` → data/db/parser.db |
| `schema.sql` | DDL схема базы данных |
| `economics.py` | Расчёт маржи, рекомендуемой цены, прибыли |
| `content_gen.py` | AI-генерация текстов через Gemini API (ротация ключей) |
| `gemini_browser.py` | Автоматизация gemini.google.com через MSB CDP (рестайл фото) |
| `ggsel_publisher.py` | Публикация офферов в GGSEL Seller API |
| `msb_client.py` | REST-клиент к MSB API (профили, старт/стоп) |
| `msb_cookies.py` | Получение Qrator-куков через MSB CDP |
| `msb_fetcher.py` | HTTP-фетчер через MSB профили + rate limiter |
| `cdp_cookies.py` | Низкоуровневый CDP клиент (универсальный) |
| `profile_pool.py` | Пул MSB профилей для парсинга |
| `adaptive_rate_limiter.py` | Адаптивный rate limiter (429/5xx backoff) |
| `economics.py` | Расчёт маржи и рекомендуемой цены |
| `dedup.py` | Дедупликация товаров |
| `event_logger.py` | Логирование событий парсера |
| `telemetry.py` | Телеметрия и статистика запросов |
| `scheduler.py` | Планировщик автопилота |
| `profile_warmer.py` | Прогрев MSB профилей (Qrator куки) |
| `competitor_scanner.py` | Сканирование товаров конкурентов |
| `my_shop_scraper.py` | Скрапинг своего магазина |
| `category_catalog.py` | Каталог категорий GGSEL |
| `order_processor.py` | Обработка заказов |
| `captcha_handler.py` | Обработка капч через MSB |
| `tg_bot.py` | Интеграция с Telegram из парсера |

### Интерфейс (static/ + templates/)
| Файл | Назначение |
|---|---|
| `templates/index.html` | Единственный HTML (SPA), все view-секции здесь |
| `static/app.js` | Весь frontend JS (~4500 строк, оглавление в начале файла) |
| `static/style.css` | Стили панели |

### Остальное
| Файл | Назначение |
|---|---|
| `bot/` | Telegram бот (aiogram 3.x) |
| `data/db/parser.db` | SQLite база (единственный канонический путь) |
| `logs/` | Логи: app.log, bot.log, watchdog.log |
| `tests/` | Тесты и smoke-скрипты |
| `scratch/` | Официальная подсистема авторегистрации (Node.js/Playwright), скрипты CDP и рабочая зона |

## Поток данных

```
ggsel.net → parser_engine.py (CascadeFetcher)
    → parsed_products (SQLite)
    → content_gen.py (AI тексты через Gemini API)
    → gemini_browser.py (AI фото через MSB + Gemini web)
    → Модерация в UI (approve/reject)
    → ggsel_publisher.py (публикация в GGSEL)
```

## Ключевые паттерны

### Добавить новый API эндпоинт
→ `parser/routes.py`, добавить в нужную секцию (см. оглавление в начале файла)

### Добавить новый UI раздел
→ `templates/index.html` — добавить `<section class="view" id="view-NAME">`
→ `static/app.js` — добавить в `VIEWS[]`, `loadView()`, написать `loadNAME()`

### Изменить расчёт цены/маржи
→ `parser/economics.py` — класс `EconomicsCalculator`

### Изменить AI-генерацию текста
→ `parser/content_gen.py` — функция `generate_product_content()`

### Изменить генерацию/рестайл фото
→ `parser/gemini_browser.py` — функция `restyle_image_via_browser()`
→ `parser/routes.py` — эндпоинт `browser_generate_image`

### Изменить публикацию товаров
→ `parser/ggsel_publisher.py` — класс `GGselPublisher`

### Изменить работу с MSB профилями
→ `parser/msb_client.py` — класс `MsbClient`

## ENV переменные (.env)

| Переменная | Обязательна | Описание |
|---|---|---|
| `GGSEL_API_KEY` | ✅ | Ключ GGSEL Seller API |
| `GGSEL_SELLER_ID` | ✅ | ID продавца |
| `MSB_API_BASE` | ✅ | URL MSB (default: http://127.0.0.1:17248) |
| `MSB_API_TOKEN` | ✅ | Bearer-токен MSB |
| `GEMINI_API_KEYS` | ✅ | Ключи Gemini API через запятую |
| `MSB_GEMINI_GROUP` | ⚠️ | Группа профилей MSB для Gemini web |
| `GEMINI_MODEL` | — | default: gemini-2.0-flash |
| `TARGET_MARGIN_PCT` | — | default: 0.15 (15%) |
| `MIN_NET_PROFIT_RUB` | — | default: 50 ₽ |
| `PAYMENT_FEE_PCT` | — | default: 0.027 (2.7%) |
| `PARSER_DB_PATH` | — | default: data/db/parser.db |

## База данных

Один канонический файл: `data/db/parser.db`
Путь получать **только** через `from parser.db_init import get_db_path`.
Схема: `parser/schema.sql`
Ключевая таблица: `parsed_products` (id, title, price, status, generated_*, ...)

## Логи

| Лог | Что пишет |
|---|---|
| `logs/app.log` | Flask + парсер |
| `logs/bot.log` | Telegram бот |
| `logs/watchdog.log` | Watchdog мониторинг |
| `data/pipeline.log` | Детальный лог пайплайна парсера |

## Тесты

```bash
set PYTHONIOENCODING=utf-8
python -m pytest tests/ -v
```

Ключевые тест-файлы:
- `tests/test_economics.py` — расчёт маржи и цен
- `tests/test_economics_full.py` — расширенные тесты экономики
- `tests/test_publisher.py` — тесты публикатора (с моками)
- `tests/test_rate_limiter.py` — rate limiting
- `tests/test_msb_fetcher.py` — MSB fetcher
- `tests/test_telemetry.py` — телеметрия

## Важные ограничения

- **Никогда не хардкодить путь к БД** — только через `get_db_path()`
- **Не импортировать `morelogin_*`** — эти модули удалены; используй `msb_*` и `gemini_browser`
- **`gemini_browser.py` работает только если MSB запущен** (http://127.0.0.1:17248)
- **`content_gen.py`** работает через Gemini API ключи (не через браузер)
- **Все фото** сохраняются в `static/products/{safe_pid}/ai.jpg`
- **`morelogin_gemini.py`** — старое имя, файл существует для обратной совместимости; новый код использует `gemini_browser.py`
- **Работа с Cookies:** Куки личного кабинета для работы API подтягиваются автоматически через профиль MSB `atreum.5202@gmail.com`. Индикатор статуса отображается в верхней панели UI. Заглушки отсутствуют при валидных куках.
