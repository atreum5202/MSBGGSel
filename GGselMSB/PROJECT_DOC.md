# PROJECT_DOC — GGselMSB Техническая документация

> Последнее обновление: 2026-08-03
> Антидетект: **MSB** (Multi-Session Browser) — `MSBWorkshop/MSB/`
> Порт MSB API: `http://127.0.0.1:17248`

---

## Общая архитектура (AI Dropshipping Пайплайн)

```
GGselMSB (полностью автоматизированная система дропшиппинга)
    │
    │  HTTP REST API (:17248)
    ▼
MSB (персональный антидетект)
    ├── Chromium-профили с отпечатками
    ├── Прокси на уровне профиля
    └── AI-агенты (claude/chatgpt/gemini/minimax)
```

**Суть бизнеса (Флоу)**:
1. **Сбор топов**: Парсер через MSB отбирает Топ-100 ликвидных товаров у конкурентов.
2. **Экономический фильтр**: Жесткий расчет (Цена конкурента + Комиссия площадки + Вывод + Наша маржа). Если не выгодно — в мусор.
3. **AI-Оптимизация**: Нейросеть (Gemini) берет лучшее, переписывает описание под наш стиль, делает SEO.
4. **Авто-выгрузка**: Товар отправляется на нашу витрину (API V1/V2).
5. **Цикл продажи**: Клиент покупает -> Мы видим чаты -> Авто/ручной выкуп оригинала -> Передача ключа клиенту -> Фиксация профита.

GGselMSB **не содержит** собственной логики работы с браузером.
Всё что связано с антидетектом, прокси, отпечатками — это MSB.
GGselMSB работает исключительно как "Мозг" торговых операций.

---

## Модули и их назначение

### Корень проекта

| Файл | Описание |
|------|----------|
| `app.py` | Flask-приложение (порт 5000), точка входа |
| `config.py` | Вся конфигурация из `.env` |
| `watchdog.py` | Перезапуск app.py при падении |
| `cookie_autorefresh.py` | Фоновый демон авто-обновления куков продавца |
| `cookie_status_routes.py` | Flask Blueprint `/api/cookie/*` — статус и обновление куков |
| `bulk_parse.py` | Параллельный загрузчик товаров конкурентов с ggsel.net |
| `parse_all.py` | Оркестратор: запускает 5 воркеров bulk_parse.py |
| `start.vbs` | Запуск стека (Flask + watchdog), открывает браузер |
| `restart_flask.bat` | Перезапуск Flask без остановки всего стека |

### parser/ — Парсер ggsel.net

| Файл | Описание |
|------|----------|
| `routes.py` | Flask Blueprint `/api/parser/*` — все эндпоинты парсера |
| `parser_engine.py` | Оркестратор: запуск, очередь задач, статус |
| `msb_fetcher.py` | HTTP-запросы через куки из MSB (основной транспорт) |
| `msb_client.py` | Низкоуровневый REST-клиент MSB API |
| `msb_cookies.py` | Кэш Qrator-куков ggsel.net, получение из MSB |
| `profile_pool.py` | Пул профилей MSB с ротацией и anti-hijack защитой |
| `ggsel_parser.py` | HTML→структура: категории, товары, предложения конкурентов |
| `ggsel_publisher.py` | Публикация и редактирование товаров через ggsel API |
| `economics.py` | Расчёт маржи, прибыли, фильтры воронки |
| `pricing.py` | Стратегии ценообразования |
| `adaptive_rate_limiter.py` | Адаптивный rate-limit (реагирует на 429/401/5xx) |
| `captcha_handler.py` | Решение капчи через MSB сценарий `botSannysoft` |
| `category_catalog.py` | Кэш категорий ggsel.net |
| `competitor_scanner.py` | Сканирование конкурентов по категориям |
| `content_gen.py` | AI-генерация описаний и AI-фото товаров (через Gemini Imagen API) |
| `gemini_browser.py` | Браузерная автоматизация gemini.google.com через CDP + MSB. AI-фото через Gemini Imagen. |
| `dedup.py` | Дедупликация предложений |
| `event_logger.py` | Логирование событий парсера |
| `my_shop_scraper.py` | Скрапинг собственного магазина |
| `scheduler.py` | Планировщик задач парсера |
| `telemetry.py` | Локальная телеметрия |
| `tg_bot.py` | Уведомления в Telegram |

### tools/ — Инструменты диагностики

| Файл | Описание |
|------|----------|
| `cdp_monitor.py` | Мониторинг CDP трафика профиля в реальном времени |
| `network_monitor.py` | Мониторинг сетевых запросов через MSB NetworkCapture |
| `take_shot.py` | Снимок экрана активного профиля через CDP |

---

## AI-фото: пайплайн генерации

### Как работает кнопка "Перегенерировать" в Moderation UI

С 08-2026 генерация запускается **в фоне как background job**, а фронтенд опрашивает статус поллингом вместо одного длинного запроса:

```
1. POST /api/parser/products/{id}/browser-generate-image
   → сразу возвращает { ok, job_id, status: "queued" }
   → в отдельном треде стартует _run_browser_generate_image_job()

2. GET /api/parser/browser-image-jobs/<job_id> (polling каждые 2с из UI)
   → возвращает { ok, job: { status, stage, message, image_url?, error? } }

3. _run_browser_generate_image_job() (parser/routes.py):
   → скачивает исходное фото товара (image_url из БД)
   → сохраняет во временный файл static/generated/{pid}_bgsrc.jpg
   → выбирает профиль MoreLogin/MSB из gemini_browser_group (parser_config.json)
   → вызывает restyle_image_sync(..., launch_mode="background") → restyle_image_via_browser()
   → на каждом этапе обновляет статус job'а (видит UI через polling)

4. restyle_image_via_browser() (модуль morelogin_gemini.py):
   → запускает профиль через MsbClient.start_profile(profile_id, launchMode="background")
     (MSB принимает launchMode: visible|minimized|background|headless,
      background — headed rendering + best-effort сворацивание/off-screen,
      менее рискованно для детекта Google, чем настоящий headless)
   → CDP: навигация на gemini.google.com/app
   → загрузка исходного фото + промпта в чат Gemini
   → поллинг каждые 4с: ищем img[alt~="AI generated"] в DOM
   → нашли: scrollIntoView() + CDP Page.captureScreenshot { clip }
   → base64 → JPEG → static/products/{safe_pid}/ai.jpg

5. _run_browser_generate_image_job(): UPDATE DB generated_image_url = '/static/products/{safe_pid}/ai.jpg'
   → проставляет в job status="done", image_url=...
6. Фронтенд (последний polling-ответ): <img src="/static/products/.../ai.jpg?t=timestamp"> — отображается в карточке
```

### Хранение AI-фото

```
static/
  products/
    {safe_pid}/
      ai.jpg          ← сгенерированное фото (browser-generate)
  generated/
    {safe_pid}_bgsrc.jpg  ← временный файл исходного фото (удаляется после)
```

`safe_pid` = `product_id` с заменой не-alnum на `_`. Фото всегда `ai.jpg` — легко найти по ID.

### Почему CDP Screenshot, а не JS blob extraction

| Подход | Проблема |
|---|---|
| `fetch(blob:url)` | Gemini отзывает blob URL сразу после отображения — бросает «Failed to fetch» |
| `canvas.drawImage()` | Работает, но naturalWidth=0 если blob revoked |
| `Page.captureScreenshot` | Берёт пиксели прямо с экрана — без зависимости от blob ✅ |

### Настройка (parser_config.json)

```json
{
  "gemini_browser_group": "Gmail",  // группа Google-аккаунтов в MSB для Gemini AI-фото
  "server": { "local_port": 5000 }
}
```

Если `gemini_browser_group` не задан — берётся первый профиль из `pool.profile_ids`.

---

## API эндпоинты GGselMSB

Базовый URL: `http://127.0.0.1:5000`

### Парсер

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/parser/status` | Статус парсера (running/idle, прогресс) |
| POST | `/api/parser/start` | Запустить парсинг `{ categories?: [...], limit?: N }` |
| POST | `/api/parser/stop` | Остановить парсинг |
| GET | `/api/parser/results` | Результаты последнего парсинга |
| GET | `/api/parser/logs` | Логи парсера (последние N строк) |
| GET | `/api/parser/profiles` | Статус профилей в пуле (MSB) |

### Куки

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/cookie/status` | Статус куков продавца |
| POST | `/api/cookie/open-browser` | Открыть браузер для ручного обновления куков |
| POST | `/api/cookie/refresh` | Авто-обновление куков через CDP |

### Товары

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/products` | Список товаров в БД |
| GET | `/api/products/:id` | Конкретный товар |
| POST | `/api/products/:id/publish` | Опубликовать на ggsel.net |
| POST | `/api/products/:id/update-price` | Обновить цену |
| GET | `/api/top100` | Топ-100 кандидатов |

---

## Поток данных (Бизнес-логика)

```
1. cookie_autorefresh.py  → Авто-получение Qrator-куков через MSB CDP

2. bulk_parse.py (×5)     → Параллельный парсинг конкурентов через GGseller-профили
   └── msb_fetcher.py     → HTTP-запросы через куки MSB антидетекта

3. economics.py           → Фильтрация по маржинальности
                            (Цена конкурента + Комиссии = Наша розничная цена)

4. content_gen.py         → Генерация описаний и AI-фото (gemini_browser.py + CDP)

5. ggsel_publisher.py     → Публикация через API V1/V2
                            Авто-выгрузка на витрину

6. order_processor.py     → (План) Цикл: Покупка → Перепродажа → Выдача ключа.
```

---

## Конфигурация (.env)

```env
# ── GGSEL ─────────────────────────────────────────────────────
GGSEL_API_KEY=...              # Ключ продавца ggsel.net
GGSEL_SELLER_ID=...            # ID продавца

# ── MSB (антидетект) ──────────────────────────────────────────
MSB_API_BASE=http://127.0.0.1:17248
MSB_API_TOKEN=                 # пусто = без авторизации
MSB_PROFILE_IDS=               # CSV профилей (пусто = все)

# ── Парсер ────────────────────────────────────────────────────
PARSER_WORKERS=3               # Параллельных потоков парсинга
PARSER_DELAY_MIN=2             # Минимальная пауза между запросами (сек)
PARSER_DELAY_MAX=7             # Максимальная пауза

# ── Куки продавца ────────────────────────────────────────────
MSB_COOKIE_TTL=900             # Время жизни куков продавца (сек)

# ── Разное ────────────────────────────────────────────────────
FLASK_DEBUG=false
LOG_LEVEL=INFO
```

---

## Зависимости

GGselMSB зависит от MSB. MSB должен быть запущен перед стартом GGselMSB.

```
MSB (антидетект)          → MSBWorkshop/MSB/
GGselMSB (дропшиппинг) → MSBWorkshop/GGselMSB/
```

MSB не зависит от GGselMSB — антидетект самостоятельный.
Любой другой проект тоже может использовать MSB через его REST API.

---

## Логи

| Файл | Что пишет |
|------|-----------|
| `logs/app.log` | Flask приложение |
| `app_stdout.log` | stdout при запуске через start.vbs |
| `app_stderr.log` | stderr (ошибки запуска) |
| `data/parser_events.jsonl` | События парсера (структурированно) |

---

## Регистрация аккаунтов

CLI-инструмент для массовой регистрации аккаунтов на ggsel.net через профили MSB.

**Расположение:** [`scratch/`](scratch/README.md)

**Источник профилей:** MSB API — группа `"GGSeller"` (`GET /profiles`, фильтр по `group`).

```bash
cd scratch

# Зарегистрировать все профили группы GGSeller
node run_all_profiles.js --group GGSeller

# С кастомной паузой
node run_all_profiles.js --group GGSeller --delay 15
```

- Результаты пишутся в `scratch/results.jsonl` (JSONL, append)
- Уже зарегистрированные профили пропускаются автоматически
- Поддерживаются только Outlook-аккаунты; Gmail-профили игнорируются

Подробнее → [scratch/README.md](scratch/README.md)

---

## Запуск

```bat
# 1. Сначала запускаем MSB
MSBWorkshop\MSB\start.vbs

# 2. Запускаем GGselMSB
cd MSBWorkshop\GGselMSB
start.vbs  # ← тихий запуск Flask + watchdog + открывает браузер

# Или через MSB Workspace (one-click):
# POST http://127.0.0.1:17248/workspace/launch
