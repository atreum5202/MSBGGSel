# AGENTS.md — Карта проекта GGselMSB

> **Агент: читай этот файл первым.** Доклад: `PROJECT_DOC.md`. Интеграция с MSB: `../CONNECTOR.md`. Справочник MSB API: `../MSB/AGENT.md`.

## ⚠️ ПРАВИЛО: видимые терминалы

**Долгие процессы НИКОГДА не запускать внутри Zed-терминала** — пользователь их не видит.
Вместо этого — `.vbs` файл, пользователь запускает двойным кликом.

| Процесс | Запуск |
|---------|--------|
| Скаут эндпоинтов | `GGselMSB/scout.vbs` |
| CDP монитор (запросы браузера) | `GGselMSB/cdp_monitor.vbs` |
| mitmproxy web UI | `GGselMSB/mitmweb.vbs` → http://127.0.0.1:8081 |

Шаблон нового лаунчера:
```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /k """ & "C:\путь\к\process.bat" & """", 1, False
```

Zed-терминал — только для: `node --check`, `python -m py_compile`, `curl` проверок.

## Что делает проект
Автоматизированная панель продавца на маркетплейсе ggsel.net:
парсит товары конкурентов → AI обогащает карточки → модерация → публикация в свой магазин.

## Пути проектов

| Проект | Путь |
|---|---|
| GGselMSB (этот проект) | `C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB` |
| MSB (антидетект) | `C:\Users\Atreum\Desktop\MSBWorkshop\MSB` |
| MSB API | `http://127.0.0.1:17248` |
| Flask (GGselMSB) | `http://127.0.0.1:5000` |

## Группы профилей в MSB

> Группа — это роль профиля. Каждая группа имеет свой сценарий входа и назначение.

| Группа | Роль аккаунтов | Что дают куки | Кто использует |
|---|---|---|---|
| **`GGSeller`** | Обычные покупатели на ggsel.net | `cf_clearance`, `__qrator_jsid`, `_ggsel_session` | `parser/profile_pool.py` (парсер) |
| **`SellerGGsel`** | Аккаунт продавца seller.ggsel.com | seller-сессия, auth-токен | `cookie_autorefresh.py`, `app.py` |
| **`Gmail`** | Google-аккаунты (Gemini) | Google session | `parser/gemini_browser.py` |
| **`AI Workspace`** | Сервисный профиль | — | MSB Workspace launcher |

### GGSeller — пул парсера

Профили логинятся на **ggsel.net** как обычные покупатели (не продавцы).
Используются для обхода Qrator WAF и парсинга товаров конкурентов.

```python
# profile_pool.py — загружает все профили группы
MSB_GROUP_NAME = os.getenv("MSB_GROUP_NAME", "GGSeller")  # можно задать через .env
# → GET http://127.0.0.1:17248/profiles?group=GGSeller
```

- **Anti-hijack:** профили, открытые в MSB UI, не трогаются (проверка `GET /browser/status`)
- **Rate limiter:** адаптивный, per-profile, 2-60с, стате в `data/rate_state.json`
- **Сценарий:** `ggsel-login` — Cloudflare + Qrator WAF bypass, human-like поведение

### SellerGGsel — аккаунт продавца

Профиль логина на **seller.ggsel.com** (ЛК продавца).
Куки используются для авторизованных запросов к GGSEL Seller API и отображаются в верхней панели UI.

```python
# cookie_autorefresh.py — нечёткий матч (независимоть от регистра и пробелов):
_SELLER_GROUP_NAMES = {"sellerggsel", "seller ggsel", "seller", "sellerggsel1"}
# найдёт группу "SellerGGsel" или "Seller GGsel" или любое схожее
```

- **Куки хранятся** в `data/seller_cookies.json`
- **TTL:** 7200с (2 часа), автообновляемые фоновым потоком
- **Ручное обновление:** `POST /api/cookie/auto/trigger` или кнопка «Обновить» в UI
- **Статус:** `GET /api/cookie/status` → `{fresh, age_sec, has_qrator}`

### Gmail — Gemini AI-фото

Профили с входом в Google. Используются для автоматизации gemini.google.com в браузере.
Название группы хранится в `parser_config.json` ключ `"gemini_browser_group"`.
Изменить: `POST /api/parser/gemini/browser-group` `{ "group_name": "..." }` или вручную в `parser_config.json`.

---

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
**Требования:** MSB запущен на `http://127.0.0.1:17248`, `.env` заполнен.

**Порядок запуска:**
1. `MSBWorkshop\MSB\start.vbs` — сначала антидетект
2. `MSBWorkshop\GGselMSB\start.vbs` — потом панель

Или через MSB Workspace (one-click):
```bash
POST http://127.0.0.1:17248/workspace/launch
# запускает Flask + открывает http://127.0.0.1:5000
```

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

| `competitor_scanner.py` | Сканирование товаров конкурентов |
| `my_shop_scraper.py` | Скрапинг своего магазина |
| `category_catalog.py` | Каталог категорий GGSEL |
| `order_processor.py` | Обработка заказов |
| `captcha_handler.py` | Обработка капч через MSB |
| `tg_bot.py` | Устаревший, зарезервирован для будущих Telegram-уведомлений |

### Интерфейс (static/ + templates/)
| Файл | Назначение |
|---|---|
| `templates/index.html` | Единственный HTML (SPA), все view-секции здесь |
| `static/app.js` | Весь frontend JS (~4500 строк, оглавление в начале файла) |
| `static/style.css` | Стили панели |

### Остальное
| Файл | Назначение |
|---|---|
| `data/db/parser.db` | SQLite база (единственный канонический путь) |
| `logs/` | Логи: app.log, watchdog.log |
| `tests/` | Тесты и smoke-скрипты |
| `tools/` | Диагностические инструменты (CDP монитор, снимок экрана) |

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
|---|—|
| `logs/app.log` | Flask + парсер |
| `logs/watchdog.log` | Watchdog мониторинг |

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
- **`gemini_browser.py` работает только если MSB запущен`** (http://127.0.0.1:17248)
- **`content_gen.py`** работает через Gemini API ключи (не через браузер)
- **Все фото** сохраняются в `static/products/{safe_pid}/ai.jpg`
- **`morelogin_gemini.py`** — старое имя, файл существует для обратной совместимости; новый код использует `gemini_browser.py`
- **Работа с Cookies:** Куки личного кабинета для работы API подтягиваются автоматически через профиль MSB `atreum.5202@gmail.com`. Индикатор статуса отображается в верхней панели UI. Заглушки отсутствюют при валидных куках.

---

## Разработка и доработка с использованием MSB

> Этот раздел — для агента, который **дорабатывает** GGselMSB: пишет скрипты, улучшает парсер, изучает API ggsel.
> MSB (порт 17248) — ваш инструмент: антидетект + перехват трафика + автоматизация браузера.

### Что может MSB (для разработки)

| Задача | MSB API | Модуль GGselMSB |
|---|---|---|
| Открыть ggsel.net / seller.ggsel.com | `POST /profiles/{id}/start` | `msb_client.start_profile()` |
| Перехватить весь трафик (mitmproxy) | `POST /profiles/{id}/traffic/start` | напрямую через API |
| Посмотреть CDP-запросы в реальном времени | `GET /profiles/{id}/network/requests` | напрямую через API |
| Анализ API через браузер | `GET /profiles/{id}/network/endpoints` | напрямую |
| Выполнить JS в браузере | `eval_via_cdp(debug_port, code)` | `cdp_cookies.py` |
| Автоматизировать сценарий на сайте | `POST /profiles/{id}/runScenario` | `msb_client.run_scenario()` |
| Многошаговый скрипт без кода | `POST /automation/pipeline/run` | напрямую |
| Проверить прокси / IP | `POST /profiles/{id}/check-proxy` | `msb_client.check_proxy()` |
| Скриншот страницы | pipeline step `screenshot` | через pipeline |
| Запустить Flask GGselMSB | `POST /workspace/launch` | напрямую |

---

### Как изучать API ggsel.net и seller.ggsel.com

**Цель:** узнать точные эндпоинты, заголовки, формат запросов/ответов, куки, структуру авторизации.

**Способ 1: CDP Ring Buffer ()без mitmproxy, быстро)**

```python
from parser.msb_client import MsbClient
import httpx

async def discover_ggsel_api(profile_id: str):
    async with MsbClient() as cl:
        # 1. Запустить профиль
        info = await cl.start_profile(profile_id, launchMode="visible")
        print(f"debugPort: {info['debugPort']}")

        # 2. Сделай нужные действия в браузере: seller.ggsel.com
        # ... пауза 20 секунд ...

        # 3. Читать все перехваченные запросы
        r = httpx.get(f"http://127.0.0.1:17248/profiles/{profile_id}/network/endpoints")
        endpoints = r.json()["data"]
        for ep in endpoints:
            print(f"{ep['method']} {ep['urlPattern']} ({ep['count']} раз)")

        # 4. Экспорт в HAR — открыть в DevTools
        har = httpx.get(f"http://127.0.0.1:17248/profiles/{profile_id}/network/har")
        open("ggsel_api.har", "w").write(har.text)

        await cl.stop_profile(profile_id)
```

**Способ 2: mitmproxy (HAR + фильтр по хосту)**

```python
async def capture_seller_traffic(profile_id: str):
    async with MsbClient() as cl:
        await cl.start_profile(profile_id, launchMode="visible")

        # Включить mitmproxy, фильтр: только seller.ggsel.com
        r = httpx.post(
            f"http://127.0.0.1:17248/profiles/{profile_id}/traffic/start",
            json={"saveHar": True, "filterHost": "seller.ggsel.com"}
        )
        capture_dir = r.json()["data"]["captureDir"]
        print(f"Перехват запущен, директория: {capture_dir}")

        # ... работа в браузере ...

        await cl.stop_profile(profile_id)
        # .har файл → импортировать в Postman/Insomnia/DevTools
```

---

### Как добавить новый сценарий в MSB

Сценарий — это функция Node.js, которая запускается внутри запущенного профиля.
Файлы: `MSBWorkshop/MSB/src/main/lib/scenarios/`.

**Шаг 1** — создать `MSBWorkshop/MSB/src/main/lib/scenarios/myScenario.js`:

```js
// Получаешь: { page, profile, params, logger }
// page    — Playwright/Patchright Page объект
// profile — полный объект профиля MSB (id, name, account, proxy, ...)
// params  — что передало GGselMSB в { params: {...} }
// logger  — Pino logger с profileId-контекстом
export default async function myScenario({ page, profile, params, logger }) {
  const timeout = params?.timeoutMs ?? 30_000;
  logger.info({ profileId: profile.id }, 'myScenario: start');

  // Навигация:
  await page.goto('https://seller.ggsel.com/', { waitUntil: 'domcontentloaded', timeout });

  // Джавать элемент:
  await page.waitForSelector('.target-element', { timeout });

  // Выполнить JS:
  const result = await page.evaluate(() => document.title);

  // Human-like задержка (lib/humanize.js внутри MSB):
  // await humanDelay(800, 1600);

  logger.info({ profileId: profile.id, result }, 'myScenario: done');
  return { success: true, data: result };
}
```

**Шаг 2** — зарегистрировать в `MSBWorkshop/MSB/src/main/lib/scenarios/index.js`:

```js
import myScenario from './myScenario.js';

const registry = {
  'google-login': googleLogin,
  'ggsel-login': ggselLogin,
  // ... существующие ...
  'my-scenario': myScenario,  // <-- добавить сюда
};
```

**Шаг 3** — вызвать из GGselMSB (Python):

```python
# parser/msb_client.py
result = await msb_client.run_scenario(
    profile_id,
    'my-scenario',
    params={'timeoutMs': 60000, 'customParam': 'value'}
)
```

Доступные сценарии в MSB:

| Имя | Файл | Назначение |
|---|---|---|
| `ggsel-login` | `ggselLogin.js` | Логин на ggsel.net + Qrator WAF bypass |
| `google-login` | `googleLogin.js` | Логин в Google (для Gemini-профилей) |
| `bot-sannysoft` | `botSannysoft.js` | Проверка fingerprint |
| `chatgpt-login` | `chatgptLogin.js` | Логин в ChatGPT |
| `chatgpt-send` | `aiProviderSend.js` | Отправка промпта в ChatGPT |
| `claude-send` | `aiProviderSend.js` | Отправка промпта в Claude.ai |
| `gemini-send` | `aiProviderSend.js` | Отправка промпта в Gemini |
| `minimax-send` | `aiProviderSend.js` | Отправка промпта в MiniMax |

---

### CDP напрямую (без сценариев)

Модуль `parser/cdp_cookies.py` — универсальный CDP клиент. Использует только `websockets`, не знает о MSB.

```python
from parser.cdp_cookies import (
    get_cookies_via_cdp,
    eval_via_cdp,
    navigate,
    wait_for_url,
    find_page_ws_url,
)
from parser.msb_client import MsbClient

async def run_custom_cdp(profile_id: str):
    async with MsbClient() as cl:
        info = await cl.start_profile(profile_id, launchMode="background")
        debug_port = info["debugPort"]

        # Навигация:
        await navigate(debug_port, "https://seller.ggsel.com/dashboard")

        # Получить куки:
        cookies = await get_cookies_via_cdp(debug_port, "ggsel.net")

        # Выполнить JS:
        balance = await eval_via_cdp(debug_port, """
            document.querySelector('.balance-amount')?.textContent
        """)

        # Дождаться навигации:
        await wait_for_url(debug_port, "/dashboard")

        await cl.stop_profile(profile_id)
        return {"cookies": cookies, "balance": balance}
```

---

### Как добавить новый эндпоинт в GGselMSB

**Новый API-эндпоинт:**
→ `parser/routes.py` — добавить в нужную секцию (см. оглавление в начале файла)

```python
@parser_bp.route('/my-feature', methods=['POST'])
def api_my_feature():
    data = request.get_json()
    # логика
    return jsonify({'ok': True, 'result': ...})
```

**Новый UI-раздел:**
1. `templates/index.html` — `<section class="view" id="view-my-feature">...</section>`
2. `static/app.js` — добавить в `VIEWS[]`, `loadView()`, написать `loadMyFeature()`

**Новый Flask Blueprint:**
→ создать `my_routes.py` в корне проекта
→ зарегистрировать через `register_my_routes(app)` в `app.py`

**Новый парсер-модуль:**
→ `parser/my_module.py`
→ импортировать в `parser/routes.py`

---

### MSB Automation Pipeline из Python

Для многошаговых скриптов (pipeline runner MSB не требует Python-кода):

```python
import httpx

def run_msb_pipeline(steps: list) -> str:
    r = httpx.post(
        "http://127.0.0.1:17248/automation/pipeline/run",
        json={"steps": steps},
        timeout=10
    )
    return r.json()["data"]["jobId"]

def get_pipeline_status(job_id: str) -> dict:
    r = httpx.get(f"http://127.0.0.1:17248/automation/jobs/{job_id}")
    return r.json()["data"]

# Пример: открыть ggsel.net и собрать данные
job_id = run_msb_pipeline([
    {"type": "start", "profileId": "UUID", "launchMode": "visible", "network": True},
    {"type": "wait", "ms": 3000},
    {"type": "crawl", "profileId": "UUID", "mode": "crawlee",
     "urls": ["https://ggsel.net/catalog/games"],
     "maxPages": 3, "extract": {"title": "h1", "price": ".price"}},
    {"type": "network", "action": "endpoints", "profileId": "UUID"},
    {"type": "stop", "profileId": "UUID"}
])
```

Типы шагов: `create`, `start`, `stop`, `wait`, `http`, `eval`, `screenshot`, `traffic`, `network`, `crawl`, `llm`.

---

### Как смотреть логи MSB

```powershell
# Лог MSB (реальтайм):
tail -f $env:APPDATA\MSB\logs\msb.log

# Лог GGselMSB:
tail -f C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB\logs\app.log

# Статус MSB:
curl http://127.0.0.1:17248/health

# Статус MSB с точки зрения GGselMSB:
curl http://127.0.0.1:5000/api/parser/msb/status
```

---

### Крест-вызовы: что куда идти если...

| Задача | Куда писать |
|---|---|
| Улучшить парсинг HTML | `parser/parser_engine.py` + `parser/ggsel_parser.py` |
| Добавить новый API эндпоинт | `parser/routes.py` |
| Добавить новый сценарий в MSB | `MSBWorkshop/MSB/src/main/lib/scenarios/` + `index.js` |
| Изменить объект MSB-запроса | `parser/msb_client.py` |
| Добавить поле в Профиль MSB | `MSBWorkshop/MSB/src/main/api/validate.js` + `profileSchema.js` |
| Изменить CDP-автоматизацию | `parser/cdp_cookies.py` или `parser/gemini_browser.py` |
| Изменить расчёт маржи | `parser/economics.py` |
| Изменить AI-текст | `parser/content_gen.py` |
| Изменить AI-фото | `parser/gemini_browser.py` + `parser/routes.py` |
| Добавить поле в БД | `parser/schema.sql` + миграция через `ALTER TABLE` в `db_init.py` |
| Изменить UI | `templates/index.html` + `static/app.js` |
| Добавить Telegram-уведомления | `parser/tg_bot.py` (зарезервирован, нужен bot-модуль) |
| Изменить публикацию товаров | `parser/ggsel_publisher.py` |
| Настроить прокси / ротацию | `config.py` + `parser/profile_pool.py` |
| Настроить rate limiter | `config.py` (переменные RATE_*) + `parser/adaptive_rate_limiter.py` |
