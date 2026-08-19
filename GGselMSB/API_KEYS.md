# API_KEYS.md — документация по GGSEL API ключам (V1 + V2)

> **Назначение:** Этот файл — единственный источник правды о том, как работают ключи
> V1 и V2 в GGSEL Seller API. **НЕ УДАЛЯТЬ И НЕ ПЕРЕИМЕНОВЫВАТЬ** — панель GGselMSB
> ссылается на него из раздела «Настройки» (кнопка «📄 Открыть API_KEYS.md»).

## Краткая суть

В GGSEL **один и тот же API Key** используется для **обоих** API:

| API | Способ авторизации | Где берётся ключ |
|-----|--------------------|------------------|
| **V1** (legacy, JSON-RPC) | SHA-256 подпись `sha256(API_KEY + timestamp)` → JWT токен через `/api_sellers/api/apilogin` → токен передаётся в `?token=` | `GGSEL_API_KEY` в `config.py` |
| **V2** (REST) | HTTP-заголовок `Authorization: <API_KEY>` | Тот же `GGSEL_API_KEY` в `config.py` |

> ⚠️ Это **не два разных ключа**. У продавца один `GGSEL_API_KEY` (и `GGSEL_SELLER_ID`).
> Разница — только в протоколе авторизации.

## Текущие значения

```ini
# config.py
GGSEL_API_KEY   = "afbb737281b23621707ff00be1ee31dee1e6667820ebb3d11b289f4fc707b9fc"
GGSEL_SELLER_ID = 114509777
BASE_URL        = "https://seller.ggsel.com"
```

Seller: **Atreum** (id 114509777), email `atreuM.5202@gmail.com`.

## Как работает V1

```
1. Клиент формирует timestamp = str(int(time.time()))
2. sign = sha256( GGSEL_API_KEY + timestamp ).hexdigest()
3. POST /api_sellers/api/apilogin
       body: { seller_id, timestamp, sign }
   → Сервер возвращает JWT-токен (валиден ~60 дней)
4. Любой V1-запрос: GET|POST {base}{path}?token=<jwt>&...
```

V1 ручки в проекте:
- `GET  /api_sellers/api/sellers/account/balance/info` — баланс
- `GET  /api_sellers/api/sellers/account/receipts` — история операций
- `GET  /api_sellers/api/categories` — дерево категорий
- `GET  /api_sellers/api/products/list` — все товары
- `POST /api_sellers/api/seller-goods` — товары продавца
- `GET  /api_sellers/api/seller-last-sales` — последние продажи
- `GET  /api_sellers/api/purchase/info/{invoice_id}` — инфо о заказе
- `GET  /api_sellers/api/reviews` — отзывы
- `GET  /api_sellers/api/debates/v2/chats` — чаты
- `GET  /api_sellers/api/debates/v2?id_i={id}&count=N` — сообщения чата
- `POST /api_sellers/api/debates/v2?id_i={id}` (body: `{message}`) — отправить сообщение
- `POST /api_sellers/xml/shop_search.asp` — XML поиск (другой Content-Type)

## Как работает V2

```
Любой запрос:
  {base}{path}
  Headers:
    Authorization: {GGSEL_API_KEY}     ← просто ключ, без "Bearer"
    Accept: application/json
    Content-Type: application/json     ← для POST/PATCH
```

V2 ручки в проекте:
- `GET  /api_sellers/v2/categories` — плоский список категорий
- `GET  /api_sellers/v2/categories/search?q=...` — поиск категорий
- `GET  /api_sellers/v2/offers` — список офферов
- `GET  /api_sellers/v2/offers/{id}` — оффер
- `GET  /api_sellers/v2/offers/{id}/options` — опции оффера
- `GET  /api_sellers/v2/offers/{id}/products` — товары оффера
- `GET  /api_sellers/v2/offers/{id}/variants/{vid}/splitted_products` — товары варианта
- `POST /api_sellers/v2/offers` — создать оффер
- `PATCH /api_sellers/v2/offers/{id}` — обновить (price, title, description, quantity)
- `POST /api_sellers/v2/offers/batch_activate` — массовая активация
- `POST /api_sellers/v2/offers/batch_pause` — массовая пауза
- `POST /api_sellers/v2/offers/batch_delete` — массовое удаление
- `POST /api_sellers/v2/offers/{id}/options` — создать опцию
- `DELETE /api_sellers/v2/offers/{id}/options` — архивировать опции
- `POST /api_sellers/v2/offers/{id}/options/{oid}/variants` — создать вариант
- `DELETE /api_sellers/v2/offers/{id}/options/{oid}/variants` — архивировать варианты
- `POST /api_sellers/v2/offers/{id}/products` — добавить товары (ключи)
- `DELETE /api_sellers/v2/offers/{id}/products` — архивировать товары
- `POST /api_sellers/v2/offers/{id}/variants/{vid}/splitted_products` — добавить splitted-ключи
- `DELETE /api_sellers/v2/offers/{id}/variants/{vid}/splitted_products` — архивировать splitted
- `GET  /api_sellers/v2/async_job_results/{job_id}` — статус async задачи

## Особенности реальных ответов (что важно знать)

> Эти наблюдения сделаны на **реальных ответах API для seller_id=114509777**
> (см. `tests/probe_result.json`).

### Валюта
- **Баланс / receipts** — всегда **WMT** (внутренняя валюта GGSEL).
- **Офферы** — у этого продавца **RUB** (но другие продавцы могут иметь USD, EUR, UAH).
- **Заказы** — `currency_type` (RUB у нашего).

### Баланс
```json
{ "retval": 0, "content": { "amount_t_free": 1.02, "amount_t_lock": null, "amount_t_plus": null } }
```
- `amount_t_free` — доступно к выводу.
- `amount_t_lock` — заморожено (может быть `null`).
- `amount_t_plus` — бонусы (может быть `null`).

### Офферы
- `data[]` — массив офферов.
- `pagination.total_count` — сколько всего.
- У одного оффера **29+ полей** (см. `app.py:api_offer_get`).
- Категория — объект: `category: { id, title, tree, fee, content_type, has_children }`.
- `has_options / has_products / has_splitted_products` — флаги наличия вложенных структур.

### Продажи
- `sales[].product.price_rub` — основная цена (не `price`).
- `sales[].product.price_usd / price_eur` — для конвертации.
- `sales[].invoice_id` — ID заказа (int).

### Отзывы
- `reviews[].type` = `"good"` или `"bad"`.
- `reviews[].info` — текст отзыва покупателя.
- `reviews[].comment` — ответ продавца (может быть пусто).
- `reviews[].good` — ID оффера, на который оставлен отзыв.
- `totalGood / totalBad / totalItems` — агрегаты в корне.

### Чаты
- ⚠️ `items[].id_i` **может быть `null`** — такие чаты нельзя открыть через V1 API
  (продавец не имеет к ним доступа в Seller API). В GUI они показаны как «нет id».
- У нормальных чатов: `id_i, email, product (id оффера), last_message (ISO datetime)`.

### Категории V1 vs V2
- **V1** (`/api_sellers/api/categories`): вложенное дерево `{ category: [{ id, name, sub: [...] }] }`.
- **V2** (`/api_sellers/v2/categories`): плоский список `{ data: [{ id, title, fee, tree, ... }] }`.
- В V2 есть поиск `/api_sellers/v2/categories/search?q=...` — в V1 поиска нет.

## Что НЕ работает / требует осторожности

| Эндпоинт | Статус | Комментарий |
|----------|--------|-------------|
| `POST /api_sellers/v2/offers` (создание) | 422 | Сервер требует много полей, не все очевидны |
| `POST /api_sellers/v2/offers/{id}/products` (без вариантов) | 422 | Если у оффера есть `has_products: false` — может ругаться |
| `POST /api_sellers/api/debates/v2` (чат) | работает только с валидным `id_i` | Нельзя отправить в чат с `id_i: null` |
| `DELETE` вариантов | может вернуть 404 | Если вариант уже удалён / не существует |

## Тестирование в GUI

1. Запусти `python app.py` (или `start.bat`).
2. Открой http://127.0.0.1:5000
3. Нажми **🧪 API** в правом верхнем углу.
4. Откроется overlay с прогоном **всех 13+ эндпоинтов** (V1 + V2), каждая карточка
   раскрывается и показывает **реальные значения**, которые вернул сервер.

Тот же прогон можно запустить напрямую: `python tests/probe_all_endpoints.py`
(результат сохраняется в `tests/probe_result.json`).

## Где править ключи

Все настройки — в **`config.py`**. Поменяй `GGSEL_API_KEY` и `GGSEL_SELLER_ID`,
перезапусти сервер.

Также ключи можно поменять через GUI: **Настройки → 💾 Сохранить** (POST `/api/save_config`).
Скрипт перезапишет `config.py` и перезагрузит модуль в памяти.

## Безопасность

- Файл `config.py` содержит **реальные ключи доступа к магазину**.
- **Не коммить его в публичный git** без `git rm --cached config.py` + `.gitignore`.
- Не шарь скриншоты GUI, где видно полное значение `GGSEL_API_KEY`.
- В GUI ключ показан только в превью-формате: первые 8 + последние 4 символа.
