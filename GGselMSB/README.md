# GGselMSB — Автоматизированная панель дропшиппинга ggsel.net

Парсит товары конкурентов → AI обогащает карточки → модерация → публикация в свой магазин.

---

## Быстрый старт

```
1. MSBWorkshop\MSB\start.vbs          ← сначала антидетект
2. MSBWorkshop\GGselMSB\start.vbs     ← потом панель
3. http://127.0.0.1:5000              ← открывается автоматически
```

**Требования перед первым запуском:**
- Заполнить `.env` (скопировать `.env.example`, вписать ключи)
- MSB запущен на `http://127.0.0.1:17248`
- Проверка готовности: `python _check_launch.py`

---

## Структура проекта

```
GGselMSB/
│
├── app.py                      ← Flask приложение (точка входа)
├── config.py                   ← все настройки из .env
├── watchdog.py                 ← авто-перезапуск при падении
├── cookie_autorefresh.py       ← фоновое обновление куков продавца
├── cookie_status_routes.py     ← Blueprint /api/cookie/*
├── bulk_parse.py               ← параллельный парсер (воркер)
├── parse_all.py                ← запуск 5 воркеров bulk_parse.py
│
├── parser/                     ← основной движок
│   ├── routes.py               ← все /api/parser/* эндпоинты
│   ├── parser_engine.py        ← оркестратор, парсинг, сохранение в БД
│   ├── ggsel_api_client.py     ← клиент api.ggsel.com (токены, запросы)
│   ├── competitor_scanner.py   ← сканер категорий/продавцов
│   ├── ggsel_publisher.py      ← публикация через seller API
│   ├── category_resolver.py    ← slug → seller_api_id (для маппинга)
│   ├── economics.py            ← расчёт экономики
│   ├── pricing.py              ← calculate_my_price (обёртка над economics)
│   ├── content_gen.py          ← AI генерация текстов (Gemini API)
│   ├── gemini_browser.py       ← AI рестайл фото через браузер MSB
│   ├── msb_client.py           ← REST клиент MSB API
│   ├── msb_fetcher.py          ← HTTP через MSB профили + rate limiter
│   ├── profile_pool.py         ← пул MSB профилей группы GGSeller
│   ├── dedup.py                ← дедупликация товаров
│   ├── db_init.py              ← инициализация БД, миграции
│   └── schema.sql              ← DDL схема
│
├── tools/                      ← инструменты обслуживания
│   ├── refresh_cat_fees.py     ← обновить cat_fees.json из БД
│   ├── refresh_seller_categories.py  ← синхронизация с seller API (нужен MSB)
│   ├── refresh_categories_api.py     ← обновить category_stats
│   ├── match_slugs_html.py     ← расширить slug mapping через HTML каталог
│   ├── enrich_products.py      ← пакетное AI обогащение товаров
│   ├── cdp_monitor.py/.vbs     ← мониторинг CDP трафика
│   ├── network_monitor.py/.vbs ← мониторинг сетевых запросов
│   └── take_shot.py            ← скриншот через CDP
│
├── static/                     ← фронтенд (CSS, JS, фото товаров)
│   ├── app.js                  ← весь frontend JS (~4500 строк)
│   ├── style.css               ← стили
│   └── products/               ← локально скачанные фото товаров
│
├── templates/
│   └── index.html              ← единственный HTML (SPA)
│
├── data/
│   ├── db/parser.db            ← SQLite БД (единственный канонический путь)
│   ├── ggsel_tokens.json       ← токены api.ggsel.com (buyer side)
│   └── seller_cookies.json     ← куки seller.ggsel.com (обновляет cookie_autorefresh)
│
├── logs/
│   ├── app.log                 ← Flask + парсер
│   └── watchdog.log            ← watchdog мониторинг
│
├── cat_fees.json               ← кэш комиссий {seller_api_id: fee}
├── categories_cache.json       ← кэш категорий для UI
├── parser_config.json          ← конфиг парсера (категории, Gemini группа)
│
│── Утилиты (запускать напрямую):
├── _check_launch.py            ← проверка готовности системы
├── _final_check.py             ← сквозной тест парсинг → fee → my_price
├── _sync_missing_cats.py       ← синхронизировать новые категории из buyer API
├── _refresh_token.py           ← обновить токен ggsel (раз в 30 дней)
│
│── Документация:
├── README.md                   ← этот файл
├── CATEGORY_MAPPING.md         ← ⭐ архитектура категорий и комиссий
├── GGSEL_API.md                ← перехваченные эндпоинты API
├── AGENTS.md                   ← карта проекта для AI агентов
├── API.md                      ← внутренние API эндпоинты Flask
├── ARCHITECTURE.md             ← архитектурные решения
└── MSB_SETUP.md                ← настройка MSB профилей
```

---

## Ключевые ENV переменные (.env)

| Переменная | Обязательна | Описание |
|---|---|---|
| `GGSEL_API_KEY` | ✅ | Ключ продавца — без него `create_offer` → 401 |
| `GEMINI_API_KEYS` | ✅ | Ключи Gemini API через запятую |
| `MSB_API_BASE` | — | default: `http://127.0.0.1:17248` |
| `MSB_API_TOKEN` | — | Bearer токен MSB (пусто = без авторизации) |
| `TARGET_MARGIN_PCT` | — | default: 0.15 (15%) |
| `MIN_NET_PROFIT_RUB` | — | default: 50₽ |
| `PAYMENT_FEE_PCT` | — | default: 0.027 (2.7%) |

---

## Поток данных

```
ggsel.net (buyer API)
    ↓  api.ggsel.com/elastic/goods/categories
    ↓  GET /goods/{id} — детали, category.url цепочка, описание
parser_engine._save_batch()
    → category.url leaf slug → category_slug_mapping → seller_api_id
    → seller_categories → ggsel_digi_catalog (для create_offer)
    → cat_fees.json → fee% → calculate_my_price
    → INSERT parsed_products (со всеми полями)
    ↓
Модерация в UI (approve / reject)
    ↓
ggsel_publisher.create_offer()
    → POST seller.ggsel.com/api_sellers/v2/offers
    → category_id = ggsel_digi_catalog
    ↓
Клиент покупает → ручной/авто выкуп у конкурента → передача → профит
```

---

## Обслуживание (расписание)

| Задача | Частота | Команда |
|---|---|---|
| Обновить токен ggsel | раз в 30 дней | `python _refresh_token.py` |
| Синхронизировать новые категории | раз в неделю | `python _sync_missing_cats.py` |
| Обновить комиссии | после синхронизации | `python -m tools.refresh_cat_fees` |
| Обновить кол-во товаров в категориях | раз в неделю | через UI или `tools/refresh_categories_api.py` |
| Полная синхронизация seller_categories | раз в месяц | `python -m tools.refresh_seller_categories --cdp <url>` |
| Расширить slug mapping | когда нужно | `python -m tools.match_slugs_html --max-depth 4` |

---

## Документы для чтения

- **`CATEGORY_MAPPING.md`** — как работают категории, комиссии, ID пространства ⭐
- **`GGSEL_API.md`** — все перехваченные API эндпоинты ggsel.net
- **`AGENTS.md`** — для AI агентов: полная карта модулей
- **`API.md`** — внутренние Flask эндпоинты
