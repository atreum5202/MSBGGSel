# GGseller — Дропшиппинг-система для ggsel.net

Автоматизированная система продавца на платформе ggsel.net: парсинг конкурентов,
AI-обогащение товаров, генерация и рестайл фото через Gemini, публикация через GGSEL API.

---

## Быстрый старт

**Требования:**
- Python 3.11+
- MSB запущен и доступен на `http://127.0.0.1:17248`
- Заполнен `.env` (см. раздел ниже)

**Установка зависимостей:**
```bash
pip install -r requirements.txt
```

**Запуск:**

Двойной клик на `start.vbs` — запускает Flask (порт 5000), Telegram-бот и watchdog,
затем автоматически открывает браузер.

---

## Структура проекта

```
GGselMSB/
├── app.py                  # Flask-приложение (порт 5000)
├── config.py               # Конфигурация (читает .env)
├── watchdog.py             # Мониторинг Flask + MSB, авторестарт
├── start.vbs               # Точка запуска всей системы
│
├── parser/
│   ├── routes.py               # Flask Blueprint /api/parser/*
│   ├── parser_engine.py        # Оркестратор парсинга
│   ├── msb_client.py           # REST-клиент к MSB API
│   ├── msb_cookies.py          # Получение Qrator-куков через MSB CDP
│   ├── msb_fetcher.py          # HTTP-фетчер через MSB профили
│   ├── morelogin_gemini.py     # Автоматизация Gemini через CDP (через MSB)
│   ├── cdp_cookies.py          # Низкоуровневый CDP-клиент (универсальный)
│   ├── ggsel_publisher.py      # Публикация товаров в GGSEL
│   ├── content_gen.py          # AI-обогащение через Gemini API ключи
│   ├── economics.py            # Расчёт маржи и прибыли
│   └── ...                     # Парсер HTML, планировщик, логирование
│
├── bot/                    # Telegram-бот (уведомления)
├── static/                 # Фронтенд (JS/CSS)
├── templates/              # HTML-шаблоны Flask
└── parser.db               # SQLite — хранение спарсенных товаров
```

---

## Переменные окружения (.env)

| Переменная | Описание |
|---|---|
| `GGSEL_API_KEY` | Ключ GGSEL API |
| `GGSEL_SELLER_ID` | ID продавца на ggsel.net |
| `MSB_API_BASE` | URL MSB (по умолчанию `http://127.0.0.1:17248`) |
| `MSB_API_TOKEN` | Токен авторизации MSB |
| `GEMINI_API_KEY` | Ключ Gemini API (для генерации текстов) |
| `MSB_GEMINI_GROUP` | Название группы профилей MSB для Gemini |

---

## API эндпоинты (Flask /api/parser/*)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/parser/status` | Статус парсера и MSB-профилей |
| GET | `/api/parser/config` | Текущие настройки |
| POST | `/api/parser/run` | Запустить парсинг |
| GET | `/api/parser/results` | Последние результаты |
| POST | `/api/parser/msb/reset-errors` | Сбросить счётчики ошибок профилей |
| POST | `/api/parser/msb/rate/reset` | Сбросить rate-limit |

---

## Архитектура

Система состоит из пяти слабосвязанных слоёв.

**Flask (app.py)** принимает запросы из браузерного UI и делегирует их парсеру и
паблишеру через Blueprint-роуты в `parser/routes.py`.

**MSB** — антидетект-менеджер, запускает CloakBrowser-профили с изолированными
отпечатками и прокси. GGseller общается с ним через REST API (`msb_client.py`) и
низкоуровневый CDP (`cdp_cookies.py`, `msb_cookies.py`). Вся логика браузера
остаётся внутри MSB.

**Gemini web** работает через браузерный профиль MSB: `morelogin_gemini.py` управляет
вкладкой Gemini через CDP (без официального API) — генерирует и рестайлит фото.
Для генерации текстовых описаний используется Gemini API напрямую (`content_gen.py`).

**GGSEL API** принимает готовые товары от `ggsel_publisher.py` и публикует их
на маркетплейсе. Маржа и цены рассчитываются заранее в `economics.py`.

**SQLite (parser.db)** хранит спарсенные товары. Telegram-бот (`bot/`) отправляет
уведомления о ключевых событиях. Watchdog (`watchdog.py`) следит за Flask и MSB,
перезапускает их при падении.
