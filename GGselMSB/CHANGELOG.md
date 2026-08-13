# CHANGELOG — GgsellerMoreLogin

> Параллельная ветка основного проекта GGSeller, в которой антидетект
> переключен с собственного MSB на коммерческий **MoreLogin**.
> Цель — продолжить работу над парсером, пока для MSB готовится новый движок.
> Когда MSB будет готов — возврат одной строкой в `.env`.

---

## [2026-08-11] — AI-фото: фоновая генерация (job + polling) + MSB launchMode="background"

### Проблема
Кнопка "Перегенерировать" в разделе Модерация блокировала UI на время всей браузерной
генерации (до 3 мин) без промежуточных статусов, а сам браузер Gemini запускался
только в обычном видимом режиме (без поддержки фона/headless).

### Решение: background job + polling в UI

`browser_generate_image` теперь сразу возвращает `job_id` и запускает генерацию в
фоновом треде. Фронтенд обрашивает новый эндпоинт статуса каждые 2с и рисует
текущий этап (скачивание фото, выбор профиля, запуск браузера, ввод промпта, ожидание
результата, сохранение).

```
POST /api/parser/products/<id>/browser-generate-image
  → { ok, job_id, status: "queued" }

GET  /api/parser/browser-image-jobs/<job_id>   (polling каждые 2с)
  → { ok, job: { status, stage, message, image_url?, error?, login_required? } }
```

### Решение: MSB launchMode="background" вместо headless

`MSB` (личный антидетект) добавил в `POST /profiles/:id/start` и `POST /api/env/start` параметр
`launchMode: "visible" | "minimized" | "background" | "headless"`. Старые флаги `headless` /
`isHeadless` сохранены для совместимости и автоматически мапятся на `launchMode: "headless"`.

Для Gemini/Google-сценариев в `GgsellerMoreLogin` теперь исходно используется не `headless`,
а `launchMode: "background"` — он сохраняет обычный headed rendering pipeline
(меньше риска детекта со стороны Google, чем настоящий headless) и делает best-effort
сворацивание/сокрытие окна (`--start-minimized` + off-screen + CDP-minimize).

Также `MsbClient.start_profile()` теперь логирует новые поля ответа MSB:
`launchMode`, `headlessApplied`, `backgroundApplied`, `focusSuppressed`, `pid`.

### Ограничения
- `background` — **best-effort**, не абсолютная гарантия. Окно может кратко появиться перед тем
  как свёрнется/уйдёт за экран.
- Настоящий `headless` доступен, но не рекомендуется для Gemini/Google — выше риск детекта.

### Изменённые файлы

| Файл | Изменение |
|---|---|
| `static/app.js` | `doGenImageModal` — теперь стартует job, опрашивает статус каждые 2с, показывает текущий шаг вместо статичного "до 3 минут" |
| `parser/routes.py` | добавлен in-memory job store (`_BROWSER_IMAGE_JOBS`), `_run_browser_generate_image_job()` в отдельном треде, новый эндпоинт `GET /browser-image-jobs/<job_id>` |
| `parser/morelogin_gemini.py` | `restyle_image_via_browser`/`restyle_image_sync` принимают `status_callback` и `launch_mode`; каждый шаг пайплайна шлёт через `_status()` |
| `parser/msb_client.py` | `start_profile()` прокидывает `launchMode`, логирует `launchMode`/`backgroundApplied`/`focusSuppressed` из ответа MSB |

### Проблема (предыдущего раунда — CDP Screenshot)
Кнопка "Перегенерировать" в разделе Модерация запускала MoreLogin-браузер,
Gemini генерировал картинку, но в карточке товара отображалось серое поле
или неправильное фото (иконка-спаркл Gemini, либо исходное загруженное фото).

### Root cause (3 слоя)
1. **Иконка-спаркл** (`gstatic.com/lamda/images/gemini_sparkle_aurora...svg`, 150×150px) —
   появлялась во время генерации и имела HTTPS-URL, JS выбирал её вместо blob.
2. **Blob revoked** — Gemini отзывает blob URL сразу после показа;
   `fetch(blob:...)` бросает «Failed to fetch», canvas не получает пиксели.
3. **Неверный blob** — при двух blob-картинках (uploaded preview + generated)
   JS брал первую в DOM (загруженное исходное фото), а не вторую (сгенерированную).

### Решение: CDP Page.captureScreenshot

Вместо извлечения blob через JS — делаем **CDP-скриншот** области,
где Gemini отображает сгенерированную картинку.

```
Gemini генерирует
        ↓
JS: ищем img[alt contains "AI generated"] → нашли → scrollIntoView()
        ↓
Python: ждём (polling 4с) пока нашли такой img
        ↓
CDP: Page.captureScreenshot { clip: {x,y,w,h,scale:1}, format:jpeg, quality:92 }
        ↓
Base64 → bytes → сохраняем в static/products/{safe_pid}/ai.jpg
        ↓
DB: UPDATE parsed_products SET generated_image_url = '/static/products/{safe_pid}/ai.jpg'
        ↓
Flask: отдаёт /static/products/... → браузер отображает в карточке
```

### Архитектура хранения AI-фото (новая)

| Старый путь | Новый путь |
|---|---|
| `static/generated/{safe_pid}_browser.jpg` | `static/products/{safe_pid}/ai.jpg` |
| `static/generated/{safe_pid}.jpg` | `static/products/{safe_pid}/ai.jpg` |

Каждый товар имеет **собственную папку** `static/products/{safe_pid}/`.
`safe_pid` = `product_id` с заменой не-alnum на `_`.
Фото всегда называется `ai.jpg` — легко найти по ID товара.

### Изменённые файлы

| Файл | Изменение |
|---|---|
| `parser/morelogin_gemini.py` | Новые функции `_wait_for_screenshot`, `_FIND_AND_SCROLL_JS`, `_GET_RECT_JS`; в `restyle_image_via_browser` — screenshot как основной путь, JS-blob как fallback |
| `parser/routes.py` | `browser_generate_image` и `generate_product_image` — сохраняют в `static/products/{safe_pid}/ai.jpg` |
| `parser/content_gen.py` | `generate_image` — сохраняет в `static/products/{safe_pid}/ai.jpg`, возвращает web-URL `/static/products/...` (было: абсолютный путь к файлу) |
| `static/app.js` | `imgSrc()` — обрабатывает старые абсолютные пути из БД через `/parser/image?path=...` |

### Smoke test результат (08-11, 04:36)
```
screenshot #1-3 — AI-картинка ещё не появилась (Gemini генерирует)
screenshot #4   — clip 708×386 at (196,53) nw=1024  ✓
сохранили 47051 байт → static/products/.../ai.jpg
GET /static/products/.../ai.jpg  HTTP 200 ✓
```

---

## [2026-08-03] — Initial port: GGSeller → MoreLogin

### Что сделано

Создана полная параллельная копия проекта `C:\Users\Atreum\Desktop\GgsellerMoreLogin\`
из `C:\Users\Atreum\Desktop\MySoft\GGSeller\`. Вся логика парсера, экономики,
публикации, тёплки профилей — сохранена. Изменено только:

#### Новые модули (parser/)

| Файл | Назначение |
|---|---|
| `cdp_cookies.py` | Тонкий клиент Chrome DevTools Protocol (WebSocket). Браузер-агностик — работает с MoreLogin, MSB (новый движок), AdsPower, Dolphin{Anty}, GoLogin. **Планируется портировать в оригинальный GGSeller.** |
| `morelogin_client.py` | Drop-in замена `msb_client.py` с тем же интерфейсом (`get_profiles`, `start_profile`, `get_cookies`, ...), но через MoreLogin Local API (порт 40000, заголовки `X-Api-Id` / `X-Api-Key`). |
| `morelogin_cookies.py` | `QratorCookieMiddleware` — обёртка над `cdp_cookies` для получения Qrator-куков ggsel.net. Кеш на диске: `data/cookies_cache.json`. |
| `morelogin_fetcher.py` | Главный fetcher (класс `MsbFetcher` — имя класса для совместимости с `parser_engine.py`). Использует `MoreLoginClient` + curl-cffi + `QratorCookieMiddleware` + CDP. |

#### Изменённые модули

| Файл | Что изменено |
|---|---|
| `config.py` | Добавлены `MORELOGIN_API_BASE/ID/KEY`, `MORELOGIN_GROUP_NAME`, `MORELOGIN_COOKIE_TTL`, `MORELOGIN_HEADLESS/CDP_EVASION/OPEN_TIMEOUT/CDP_TIMEOUT`. Добавлен `ANTIDETECT_BACKEND` — автоопределение `"morelogin"` / `"msb"` / `"none"` по заполненным ENV. |
| `.env` | Секция `# === MoreLogin ===` с пустыми ключами (заполнишь сам). Старая секция `# === MSB ===` сохранена. |
| `parser/profile_pool.py` | `_build_client()` теперь создаёт `MoreLoginClient` (а не `MsbClient`). MSB-вариант закомментирован для возврата. |
| `parser/parser_engine.py` | CascadeFetcher создаёт `MsbFetcher` из `morelogin_fetcher` с новыми параметрами (`morelogin_api_*`). |
| `parser/routes.py` | Добавлены `/api/parser/morelogin/*` эндпоинты: `status`, `refresh/<id>`, `groups`, `profile/<id>`, `start/<id>`, `stop/<id>`. Старые `/api/parser/msb/*` оставлены. |

#### Новая документация

| Файл | Назначение |
|---|---|
| `MORELOGIN_SETUP.md` | Полная инструкция по подключению MoreLogin, настройке, прогреву, troubleshooting. |
| `CHANGELOG.md` | Этот файл. |
| `.env.example` | Шаблон `.env` без секретов (можно коммитить). |

### Что НЕ менялось (намеренно)

- `parser/msb_client.py`, `parser/msb_cookies.py`, `parser/msb_fetcher.py` — **оставлены как есть**. Это резерв для возврата на MSB.
- `parser/captcha_handler.py` — оставлен (использует httpx к MSB на 17248, вернёт None на этой ветке, что не критично).
- `parser/profile_warmer.py` — оставлен (использует httpx к MSB, не критично для парсинга).
- `parser/economics.py`, `parser/content_gen.py`, `parser/ggsel_parser.py`, `parser/ggsel_publisher.py` — не трогали.
- `bot/`, `templates/`, `static/` — не трогали.
- `tests/` — добавили smoke_morelogin.py, остальные тесты работают как раньше.

### Что планируется портировать в оригинальный GGSeller (когда будет готов новый движок MSB)

| # | Что | Где | Зачем |
|---|---|---|---|
| 1 | `parser/cdp_cookies.py` | новый | Заменит `/profiles/:id/cookies` на CDP `Network.getAllCookies`. Работает с любым антидетектом, у которого есть CDP. |
| 2 | `parser/morelogin_client.py` | новый | Эталон реализации drop-in клиента. Для MSB v2 — копия с заменой endpoint'ов. |
| 3 | Паттерн `ANTIDETECT_BACKEND` в `config.py` | уже там | Добавить в GGSeller — чтобы можно было переключаться без правок кода. |
| 4 | CDP-based scenario execution (`eval_via_cdp`, `navigate`, `wait_for_url`) | `cdp_cookies.py` | Замена "вызвать scenario в MSB" → "выполнить JS в открытой вкладке". Сценарии прогрева/капчи живут в нашем коде. |
| 5 | Унифицированный формат профиля (`_normalize_profile`) | `morelogin_client.py` | `{envId, id, name, groupId, ...}` — общий для обоих бэкендов. |
| 6 | `headless` + `cdpEvasion` флаги на старт | MoreLogin API | В MSB v2 — поддержать оба параметра. |

### Состояние

- ✅ Копия создана
- ✅ Конфиг обновлён
- ✅ Клиент MoreLogin реализован
- ✅ CDP-клиент для куков реализован
- ✅ Cookie middleware реализован
- ✅ Fetcher реализован
- ✅ Routes обновлены
- ✅ Документация написана
- ⏳ Smoke-тест
- ⏳ `start_morelogin.vbs`
- ⏳ Заполнение `.env` (зависит от пользователя)
- ⏳ Реальный запуск + проверка парсинга

### Зависимости

`requirements.txt` уже содержит нужное:

```
httpx>=0.27
curl-cffi>=0.7
websockets>=12.0        ← для cdp_cookies.py
python-dotenv>=1.0
```

Ничего дополнительно ставить не нужно.

### Как запустить

```cmd
cd C:\Users\Atreum\Desktop\GgsellerMoreLogin
:: 1. Заполни .env (MORELOGIN_API_ID, MORELOGIN_API_KEY)
notepad .env

:: 2. Проверь что MoreLogin жив
python -m parser.morelogin_client health

:: 3. Запусти Flask
start_morelogin.vbs
:: или: python app.py

:: 4. Открой UI
start http://127.0.0.1:5000
```

Подробности — в `MORELOGIN_SETUP.md`.

## 2026-08-12 � ���� 1: Ҹ���� ���� + Layout
- ��������� ����� ����: �������� ����� ������� ����� � style.css �� ����������.
- �������� ������� (������� ��������� � '�������', ��������� �������, ���������, ������� �������, ������).
- �������� ������: ��������� ���������� ������ ����, �����������, ������������.
- ��������� ������ �������� ���������� ������� ��� ����� ��� � ������� ����������� ��� � ������.

## 2026-08-12 � ���� 2: ���������
- pp.py: ��������� ������� _cookie_get ��� ������ ������ �� cookies.json.
- pp.py: ��������� ��������� /api/promo_codes � /api/promo_codes/filters.
- index.html: ��������� ������� �������� ��������� (�������, �������).
- pp.js: ��������� ������� loadPromoCodes � enderPromoCodes.

## 2026-08-12 � ���� 3: ������� �������
- pp.py: ��������� ��������� /api/wholesale � /api/wholesale/filters.
- index.html: ��������� ������� �������� ������� �������.
- pp.js: ��������� ������� loadWholesale � enderWholesale.

## 2026-08-12 � ���� 3: ������� �������
- index.html: ��������� ������ �������� � #view-orders (����-����, ������, �����).
- pp.js: ��������� ������� loadOrders ��� ������ � ��������� � query-�����������.
- pp.js: ��������� ����������� ������� (����� �� �����, ��������� �������).

## 2026-08-12 � ���� 4: �������������� ������ (Wizard)
- index.html: ��������� ������� ������� �������������� ������ (#view-offer-edit) �� 3 �����.
- pp.js: ��������� ������� openOfferWizard ��� �������� �������.
- pp.js: ��������� ������ ������������ ������� wizardGo.
- pp.js: ����������� ���������� ������ ����� ������������ PATCH-��������.

## 2026-08-12 � ���� 5: ���� (Split-View)
- index.html: ������� #view-messages �� ������������� ��� (split-view).
- pp.js: ��������� ������ loadChats ��� �������� ������.
- pp.js: ��������� ������ openChat � etchChatMessages � ��������������� ������ 5 ���.
- pp.js: ��������� ������ �������� ���������.

## 2026-08-12 � ���� 6: �������
- index.html: ������� #view-finance, ��������� ������� ������� ���������� � time chips.
- pp.js: ��������� ������ loadFinance() � ����������� �� ������� � �������� fallback �� /api/receipts.
- pp.py: �������� �������� GET /api/ledger ��� ������������� �������� ��������.

## 2026-08-12 � ���� 7: ������� � �����������
- index.html: �������� #view-profile � 2 ��������� (������ ��������, �����������).
- style.css: �������� ����� .switch ��� ���������-�������.
- pp.js: ��������� ������ loadProfile() � ������������ �������.
- pp.py: �������� fallback-�������� GET /api/profile.

## 2026-08-12 � ���� 8: API ����� � IP
- index.html: ������� #view-settings ��� ����������� 2 ����� (API �����, IP ������) � �������� � textarea.
- pp.js: ��������� ������� switchSettingsTab ��� ���������� ������.

## 2026-08-12 � ���� 9: ������ � ������������
- index.html: �������� #view-help �� ������� �������� ������, ������������� � ����� �������.
- index.html: ��������� ������ �������� � �������.
- pp.js: �������� ������� ��� #view-help.

## 2026-08-12 � ���� 9: �������
- �������� view #view-docs �� ������� 16 ������ �� seller.ggsel.com/docs/*
- � sidebar �������� ����� "�������"

## 2026-08-12 � Bugfix: �������������� ������ app.js
- app.js:1024 � �������� ���������� �������� ������� ����� URLSearchParams
- app.js:4902/4987/5007 � querySelector ������ � �������

## 2026-08-12 � Fix: notifications + whitelisted_ips endpoints
- app.py: ��������� /api/notifications � /api/whitelisted_ips
- app.js: loadProfile � switchSettingsTab ���������� �������� endpoint'�

## 2026-08-12 � Fix: promo_codes + ledger ����
- app.py: /api/promo_codes ���������� /api/v1/promo_codes
- app.py: /api/ledger ���������� /api/v1/ledger_items
- app.py: /api/promo_codes/filters ������� �� /statuses � /offers

## 2026-08-12 � Fix: ���������� ���� API
- app.py: /api/promo_codes > /api/v1/promo_codes
- app.py: /api/promo_codes/filters ������� �� /statuses � /offers
- app.py: /api/ledger > /api/v1/ledger_items
- app.py: �������� warmer_routes.py (�������� ��� blueprint)
- app.py: �������� /api/offer/<id>/public

## 2026-08-12 � Fix: offer/public + (���) WebSocket
- app.py: /api/offer/<id>/public � �������� ����
- app.py: /ws/chats endpoint ����� flask-sock
- app.js: WebSocket client � loadChats


## 2026-08-12 - CriticalFix: перенос if __name__ в конец файла
- app.py: блок if __name__ == '__main__': с app.run() был в середине файла (строка 2587)
- Из-за этого все route-декораторы ПОСЛЕ него (api_parser_logs, api_offer_public,
  flask_sock /ws/chats) НЕ регистрировались при запуске как скрипт
- Перенесён в самый конец файла - теперь ВСЕ routes регистрируются
- debug_mode из env FLASK_DEBUG (по умолчанию off) - чтобы избежать reloader'а
- Итог: 10/15 endpoint'ов отдают 200, 5/15 отдают 401 (cookies, Flask route есть)
