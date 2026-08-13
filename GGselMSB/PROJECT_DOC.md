# PROJECT_DOC — GGselV7 Техническая документация

> Последнее обновление: 2026-08-03
> Антидетект: **MSB** (Multi-Session Browser) — `FinalProject/Controller/MSB/`
> Порт MSB API: `http://127.0.0.1:17248`

---

## Общая архитектура (AI Dropshipping Пайплайн)

```
GGselV7 (полностью автоматизированная система дропшиппинга)
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

GGselV7 **не содержит** собственной логики работы с браузером.
Всё что связано с антидетектом, прокси, отпечатками — это MSB.
GGselV7 работает исключительно как "Мозг" торговых операций.

---

## Модули и их назначение

### Корень проекта

| Файл | Описание |
|------|----------|
| `app.py` | Flask-приложение (порт 5000), точка входа |
| `config.py` | Вся конфигурация из `.env` |
| `warm_profiles.py` | CLI-прогрев куков ggsel.net через MSB Warmer API |
| `warmer_routes.py` | Flask Blueprint `/api/warmer/*` — управление прогревом из UI |
| `watchdog.py` | Перезапуск app.py при падении |
| `pipeline_top100.py` | CLI: воронка отбора топ-100 товаров |
| `start.vbs` | Запуск стека (MSB + GGselV7) |
| `stop.bat` | Остановка |

### parser/ — Парсер ggsel.net

| Файл | Описание |
|------|----------|
| `routes.py` | Flask Blueprint `/api/parser/*` — все эндпоинты парсера |
| `parser_engine.py` | Оркестратор: запуск, очередь задач, статус |
| `msb_fetcher.py` | HTTP-запросы через куки из MSB (основной транспорт) |
| `msb_client.py` | Низкоуровневый REST-клиент MSB API |
| `msb_cookies.py` | Кэш Qrator-куков ggsel.net, получение из MSB |
| `profile_pool.py` | Пул профилей MSB с ротацией и anti-hijack защитой |
| `profile_warmer.py` | «Умный» нагул профиля через Claude Vision + MSB |
| `ggsel_parser.py` | HTML→структура: категории, товары, предложения конкурентов |
| `ggsel_publisher.py` | Публикация и редактирование товаров через ggsel API |
| `economics.py` | Расчёт маржи, прибыли, фильтры воронки |
| `pricing.py` | Стратегии ценообразования |
| `adaptive_rate_limiter.py` | Адаптивный rate-limit (реагирует на 429/401/5xx) |
| `captcha_handler.py` | Решение капчи через MSB сценарий `botSannysoft` |
| `category_catalog.py` | Кэш категорий ggsel.net |
| `competitor_scanner.py` | Сканирование конкурентов по категориям |
| `content_gen.py` | AI-генерация описаний и AI-фото товаров (через Gemini Imagen API) |
| `morelogin_gemini.py` | Браузерная автоматизация gemini.google.com через CDP + MoreLogin. Сердце пиплайна AI-фото. |
| `dedup.py` | Дедупликация предложений |
| `event_logger.py` | Логирование событий парсера |
| `my_shop_scraper.py` | Скрапинг собственного магазина |
| `scheduler.py` | Планировщик задач парсера |
| `telemetry.py` | Локальная телеметрия |
| `tg_bot.py` | Уведомления в Telegram |

### bot/ — Telegram-бот

Управление GGselV7 через Telegram: запуск парсера, статус, топ товары.

### scratch/ — Скрипты автоматизации и интеграции MSB

**Официальная подсистема (не удалять!)**, отвечающая за:
- Автоматическую регистрацию аккаунтов на ggsel.net (`ggsel_register.js`).
- Интеграцию с MSB по протоколу CDP (папка `lib/`).
- Авторизацию Outlook аккаунтов (`outlook_login.js`).
- Выполнение массовых задач по всем профилям (`run_all_profiles.js`).
- Рабочую зону (scratch) для временных скриптов, диагностики прокси и отладки парсера.

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
  "gemini_browser_group": "GeminiGroup",  // название группы профилей MoreLogin
  "server": { "local_port": 5000 }
}
```

Если `gemini_browser_group` не задан — берётся первый профиль из `pool.profile_ids`.

---

## API эндпоинты GGselV7

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

### Прогрев куков

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/warmer/start` | Запустить прогрев через MSB |
| GET | `/api/warmer/status` | Статус прогрева |
| POST | `/api/warmer/stop` | Остановить прогрев |

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
1. warm_profiles.py       → MSB /api/warmer/start (Прогрев куки)

2. parser_engine.py       → Запуск пула (Топ-100 конкурентов)
   └── msb_fetcher.py     → Сбор данных через антидетект MSB

3. economics.py           → Фильтрация по маржинальности
                            (Конкурент + Комиссии = Розничная Цена)

4. content_gen.py         → Передача в Gemini (API/MCP)
                            Уникализация описания, адаптация SEO

5. ggsel_publisher.py     → POST /deals через API V1/V2
                            Авто-выгрузка на витрину

6. order_processor.py     → (План) Цикл обработки: Покупка -> Перепродажа -> Выдача.
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

# ── Прогрев ───────────────────────────────────────────────────
WARM_POLL_INTERVAL=5           # Интервал опроса статуса прогрева (сек)
WARM_TIMEOUT=600               # Таймаут прогрева (сек)

# ── Telegram бот ──────────────────────────────────────────────
TG_BOT_TOKEN=...
TG_CHAT_ID=...

# ── Разное ────────────────────────────────────────────────────
FLASK_DEBUG=false
LOG_LEVEL=INFO
```

---

## Зависимости

GGselV7 зависит от MSB. MSB должен быть запущен перед стартом GGselV7.

```
MSB (антидетект)          → FinalProject/Controller/MSB/
GGselV7 (дропшиппинг)    → GGsellerFinal/GGselV7/
```

MSB не зависит от GGselV7 — антидетект самостоятельный.
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
FinalProject\Controller\MSB\start.vbs

# 2. Греем профили
cd GGsellerFinal\GGselV7
python warm_profiles.py

# 3. Запускаем GGselV7
start.vbs
```

Или всё через `FinalProject/start.bat` если он настроен на оба проекта.
