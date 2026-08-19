# GGSEL API — Перехваченные эндпоинты

Реальные API-запросы, перехваченные через CDP (Playwright + MSB антидетект-браузер)
при навигации по `ggsel.net` и `seller.ggsel.com`.

Источник: `data/ggsel_endpoints.json` (захват 2026-08-14, `endpoint_scout.py`)

---

## Хосты

| Хост | Назначение | Авторизация |
|------|-----------|-------------|
| `api.ggsel.com` | Основной JSON API для покупателей | `Authorization: Bearer <access_token>` |
| `back-office.ggsel.com` | Заказы, профиль покупателя | `Authorization: Bearer <access_token>` |
| `ggsel.net` | HTML-страницы (SSR, Next.js) | Cookie: `access_token` |
| `payment.ggsel.com` | Страница оплаты заказа | Cookie / auth_token в URL |
| `technoscore.ggsel.net` | Трекинг / WAF Qrator fingerprint | — |

Токен получается из cookie `access_token` после авторизации на `ggsel.net`.
Живёт **31 день**, обновляется через `refresh_token`.

---

## api.ggsel.com

### Товары и каталог

---

#### `POST /elastic/goods/rec-goods` — список товаров
*Перехвачен: 23 раза*

Основной эндпоинт парсинга. Возвращает листинг по категории или поисковому запросу.

**Тело запроса:**
```json
{
  "limit": 12,
  "lang": "en",
  "is_russian_ip": 0,
  "currency": "wmz",
  "sort": "popular",
  "page": 1,
  "category": "spotify-premium",
  "search": ""
}
```
Поля `category` (slug) и `search` — опциональны, могут не передаваться.

**Пример ответа:**
```json
{
  "data": {
    "items": [
      {
        "id_goods": 102276701,
        "is_active": true,
        "url": "ssa-apple-id-vozrast-6-mesiacev-...-102276701",
        "name": "[US] USA Apple ID • 6 Months Old • Solo Access",
        "image": "https://img.ggsel.net/102276701/original/250x250/....webp",
        "price_wmr": "130",
        "price_wmz": "...",
        "price_brl": "...",
        "id_seller": 279760,
        "seller_name": "GalaxyLink",
        "sales_count": 42
      }
    ]
  }
}
```

**Параметры `sort`:** `popular`, `sortByRec`, `sortByNew`, `sortByPrice`

---

#### `POST /elastic/goods/categories` — поиск в каталоге (расширенный)
*Перехвачен: 1 раз*

Более детальный поиск с фильтрами.

**Тело запроса:**
```json
{
  "limit": 60,
  "content_type_ids": [],
  "search_after": [],
  "with_filters": true,
  "is_preorders": false,
  "ab_test_without_emoji": true,
  "sort": "sortByRec",
  "query_string": "",
  "with_forbidden": false,
  "min_price": "",
  "max_price": ""
}
```

**Пример ответа:**
```json
{
  "data": {
    "items": [
      {
        "id_goods": 102240902,
        "url": "24-7-avto-roblox-kod-...-102240902",
        "is_active": true,
        "id_section": 28145,
        "id_seller": 279760,
        "seller_name": "GalaxyLink",
        "name": "24/7 | AUTO | Roblox Gift Card 225-10000 Robux - Any Region",
        "image": "https://img.ggsel.net/..."
      }
    ]
  }
}
```

---

#### `POST /elastic/goods/bestsellers` — бестселлеры
*(Из `ggsel_api_client.py`, не перехвачен напрямую, но реализован в клиенте)*

**Тело запроса:**
```json
{
  "lang": "en",
  "currency": "wmz",
  "limit": 50,
  "category": "games-steam"
}
```

---

#### `GET /goods/{id}/price` — цена товара
*Перехвачен: 19 раз*

Актуальная цена и количество в наличии.

**Query params:** `currency=USD` (или `wmz`, `wmr`, `wme`), `unit_count=1`, `options[484269]=1251647`

**Пример:** `GET /goods/3796108/price?currency=USD`

**Ответ:**
```json
{
  "data": {
    "count": 50,
    "amount": 0.65,
    "cashback": null
  },
  "success": true
}
```
> `count` — количество товаров в наличии, `amount` — цена за единицу в указанной валюте.

---

#### `GET /redirects` — проверка редиректа по URL
*Перехвачен: 1 раз (404)*

Проверяет, есть ли редирект для указанного пути.

**Query params:** `from_url=/en/catalog/product/slug`

**Ответ (если нет редиректа):**
```json
{ "success": true, "data": [] }
```

---

### Категории и навигация

---

#### `GET /main/content-types` — список категорий
*Перехвачен: 9 раз*

Все категории сайта с их slug-ами.

**Query params:** `lang=en`

**Ответ:**
```json
{
  "data": [
    { "id": 2,  "url": "keys",     "title": "Keys",               "goods_name": "Key" },
    { "id": 54, "url": "Purchase-to-your-account", "title": "Purchasing for your account" },
    { "id": 48, "url": "gifts",    "title": "Gifts",              "goods_name": "Gift" },
    { "id": 42, "url": "Actia",    "title": "Sale",               "goods_name": "Sale" },
    { "id": 12, "url": "games",    "title": "Games" }
  ]
}
```

---

#### `GET /main/category-types` — типы категорий (верхний уровень)
*Перехвачен: 3 раза*

**Query params:** `lang=en`

**Ответ:**
```json
{
  "data": [
    {
      "name": "All Games",
      "url": "games",
      "icon_alias": "SportsEsports",
      "seo_title": "Game catalog for PC, PS, Xbox",
      "category_url": "igry-po-nazvaniyu"
    },
    {
      "name": "In-game items & currency",
      "url": "in-game"
    }
  ]
}
```

---

#### `GET /main/category-types/compilation` — тематические подборки
*Перехвачен: 4 раза*

**Query params:** `lang=en`, `category_type_url=subscription-services` (опционально)

**Ответ:**
```json
{
  "data": [
    {
      "title": "Services",
      "url": "podpisochnye-servisy",
      "type": "banner",
      "items": [
        { "name": "Cursor",          "url": "cursor",          "banner": "https://img.ggsel.net/..." },
        { "name": "YouTube Premium", "url": "youtube-premium", "banner": "https://img.ggsel.net/..." }
      ]
    }
  ]
}
```

---

#### `GET /main/menu` — меню сайта
*Перехвачен: 3 раза*

**Query params:** `type=head_main_mob`, `lang=en`

**Ответ:**
```json
{
  "success": true,
  "data": [
    {
      "slug": "/catalog/games-1",
      "title": "Games",
      "image_path": "https://img.ggsel.net/production/uploads/menu_items/..."
    }
  ]
}
```

---

### Аккаунт покупателя

---

#### `GET /accounts` — данные аккаунта
*Перехвачен: 42 раза (самый частый)*

**Query params:** `lang=en`

**Ответ:**
```json
{
  "data": {
    "id": 5612821,
    "name": "Atreum",
    "nickname": "Atreum",
    "email": "atreum.5202@gmail.com",
    "birth_date": "1999-01-02",
    "gender": "male",
    "newsletter_consent": false,
    "first_transaction_date": "2026-01-04T19:02:36.000000Z",
    "image_url": "",
    "is_new_account": false,
    "favorite_count": 0
  },
  "success": true
}
```

---

#### `GET /accounts/favorites` — список избранного
*Перехвачен: 5 раз*

**Query params:** `lang=en`, `search_term=`, `limit=10`, `sort=sortByNew`, `is_russian_ip=0`

**Ответ:**
```json
{
  "data": {
    "items": [],
    "total_search": 0,
    "total": 0
  },
  "success": true
}
```

---

#### `POST /accounts/favorites` — добавить в избранное
*Перехвачен: 8 раз*

**Тело запроса:**
```json
{
  "offer_id": 102240902,
  "offer_type": "goods",
  "is_russian_ip": 0
}
```

**Ответ:** `201 Created` → `{ "success": true }`

---

#### `DELETE /accounts/favorites` — удалить из избранного
*Перехвачен: 8 раз*

**Query params:** `offer_id=102240902`, `offer_type=goods`, `is_russian_ip=0`

**Ответ:** `204 No Content`

---

#### `GET /accounts/current-draw` — текущий розыгрыш
*Перехвачен: 15 раз*

**Ответ:** `{ "success": true, "data": [] }` (если нет активного розыгрыша)

---

### Прочее

---

#### `GET /currencies` — курсы валют
*Перехвачен: 14 раз*

**Query params:** `lang=en`

**Ответ:**
```json
{
  "data": [
    { "name": "wmr", "rate": "1" },
    { "name": "wmz", "rate": "83.8058" },
    { "name": "wme", "rate": "96.7538" }
  ]
}
```
> `wmr` = рубль (базовая), `wmz` = USD, `wme` = EUR

---

#### `GET /lotteries/active` — активные лотереи
*Перехвачен: 15 раз*

**Ответ:**
```json
{
  "data": {
    "id": 17,
    "name": "Розыгрыш Июнь-Июль 2025",
    "image": "https://img.ggsel.net/production/uploads/lotteries/big/....png",
    "event_start": "2025-06-26T07:00:00.000000Z",
    "event_date":  "2025-07-20T20:59:00.000000Z",
    "event_end":   "2025-07-23T20:59:00.000000Z"
  }
}
```

---

#### `POST /ecom/buy` — купить товар
*Перехвачен: 2 раза*

> ⚠️ Реальная покупка. Не использовать в парсере.

**Тело запроса:** `{ "payload": "<base64>" }` — payload = JSON с `email`, `id_goods`, `name`, `options`.

**Ответ:** `201 Created`

---

## back-office.ggsel.com

### Заказы и профиль

---

#### `POST /api_customer/v1/orders` — создать заказ
*Перехвачен: 2 раза*

> ⚠️ Реальная покупка. Не использовать в парсере.

**Тело запроса:**
```json
{
  "lang": "en",
  "currency_code": "USD",
  "ggsel_id_goods": 102214136,
  "unit_count": 1,
  "ip": "5.77.198.59",
  "options": [{ "id": 3091161, "value": { "id": 18552341 } }],
  "source": "https://ggsel.net/en/catalog/product/..."
}
```

**Ответ:**
```json
{ "data": { "uuid": "445f0364-5f2c-46f4-9d9b-b6d7bb8619a5" } }
```

---

#### `GET /api_customer/v1/orders/{uuid}` — детали заказа
*Перехвачен: 2 раза*

**Query params:** `lang=en`

**Ответ:**
```json
{
  "data": {
    "status": "active",
    "paid": false,
    "reviewed": false,
    "id": 44217569,
    "created_at": 1786671362,
    "unit_count": 1.0,
    "payment_systems": [
      {
        "id": 1011,
        "title": "Card (EUR)",
        "code": "card",
        "currency_code": "EUR",
        "commission": 0.0,
        "full_amount": 8.05
      }
    ]
  }
}
```

---

#### `GET /api_customer/v1/users/me` — профиль через back-office
*Перехвачен: 2 раза*

**Ответ:**
```json
{
  "id": 7254888,
  "email": "atreum.5202@gmail.com",
  "has_orders": true
}
```

---

## ggsel.net — HTML страницы

SSR на Next.js. Данные о товаре содержатся в `<script type="application/ld+json">` и `window.__NEXT_DATA__`.

| URL | Назначение |
|-----|-----------|
| `GET /en/catalog` | Каталог (главная) |
| `GET /en/catalog?sort=popular&page=2` | Каталог с сортировкой и пагинацией |
| `GET /en/catalog/steam` | Категория Steam |
| `GET /en/catalog/gift-cards` | Категория Gift Cards |
| `GET /en/catalog/product/{slug}` | Карточка товара |
| `GET /en/profile?tab=favourites` | Профиль пользователя |
| `GET /en/sellers` | Список продавцов |

**Формат URL товара:** `/en/catalog/product/{name-slug}-{id_goods}`

**Пример:** `https://ggsel.net/en/catalog/product/spotify-premium-1-3-6-12-mesiacev-...-102219546`

**LD+JSON на странице товара:**
```json
{
  "@type": "Product",
  "name": "Spotify Premium",
  "offers": {
    "@type": "Offer",
    "price": "5.50",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  }
}
```

---

## payment.ggsel.com

| URL | Назначение |
|-----|-----------|
| `GET /en/order/{uuid}?auth_token=...` | Страница оплаты заказа |
| `GET /profile/history` | История платежей |

---

## technoscore.ggsel.net — WAF / Fingerprint

> Это **система защиты Qrator** — не трогать в парсере.

| Эндпоинт | Описание |
|----------|----------|
| `GET /v2/fingerprint_b?apiUserName=ggsel&tid=...&text=<encoded>` | Отправка fingerprint браузера (перехвачен: 26 раз) |
| `POST /v2/tracking_b?apiUserName=ggsel&tid=...` | Поведенческий трекинг (перехвачен: 10 раз) |

`curl-cffi` с `impersonate="chrome120"` имитирует TLS fingerprint и обходит базовую проверку без браузера. При Qrator-challenge (JS-задача) нужен настоящий браузерный профиль через MSB.

---

## Авторизация

### Bearer токен (api.ggsel.com / back-office.ggsel.com)

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
Accept: application/json
Content-Type: application/json
```

Токен = `access_token` из cookie `ggsel.net`.

### Обновление токена

```
POST https://api.ggsel.com/auth/refresh
Body: { "refresh_token": "6c57c4cf49..." }
```
*(Реализовано в `ggsel_api_client.py` → `_try_refresh()`)*

### Структура JWT (access_token)

```json
{
  "iss": "ggsel",
  "sub": 7254888,
  "iat": 1786657835,
  "exp": 1789336235,
  "jti": "613344e2-64c4-4b93-82dc-ffb8b0c7b690",
  "user": { "id": 7254888, "email": "atreum.5202@gmail.com" },
  "namespace": "api_customer"
}
```
Срок жизни: **31 день** (`exp - iat ≈ 2678400 сек`).

---

## Быстрая шпаргалка для парсинга

```python
import httpx

TOKEN = "eyJhbGciOiJIUzI1NiJ9..."
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

# 1. Товары категории
r = httpx.post("https://api.ggsel.com/elastic/goods/rec-goods",
               headers=H,
               json={"lang": "en", "currency": "wmz", "limit": 50,
                     "page": 1, "sort": "popular", "category": "spotify-premium"})
items = r.json()["data"]["items"]

# 2. Цена товара
r = httpx.get(f"https://api.ggsel.com/goods/{id_goods}/price",
              headers=H, params={"currency": "wmz"})
price = r.json()["data"]["amount"]

# 3. Курсы валют
r = httpx.get("https://api.ggsel.com/currencies", headers=H)
rates = {x["name"]: float(x["rate"]) for x in r.json()["data"]}
# rates = {"wmr": 1.0, "wmz": 83.8, "wme": 96.75}

# 4. Все категории
r = httpx.get("https://api.ggsel.com/main/content-types",
              headers=H, params={"lang": "en"})
categories = r.json()["data"]  # [{id, url (slug), title}, ...]
```
