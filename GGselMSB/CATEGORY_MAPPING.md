# Категории и комиссии GGselMSB

> Последнее обновление: 2026-08-19  
> Статус маппинга KNOWN_CATEGORIES: **60/60** slug замаплены

---

## Архитектура: три разных пространства ID

Самое важное — понять что в системе существуют **три разных ID** для одной и той же категории:

| Пространство | Что это | Где встречается | Пример (Spotify Premium) |
|---|---|---|---|
| **`id_section`** | Buyer-side ID подраздела | `api.ggsel.com/elastic/goods/categories` поле `id_section` | `28831` (но это Steam Wallet, не Spotify!) |
| **`seller_api_id`** | Внутренний id кабинета продавца | `seller.ggsel.com/api/v1/categories` | `29` |
| **`ggsel_digi_catalog`** | ID витрины ggsel.net, то что шлётся в `create_offer` | `POST /api_sellers/v2/offers` поле `category_id` | `33760` |

**⚠️ Критично:** `id_section` из buyer API НЕ равен `seller_api_id`. Это разные системы нумерации.

---

## Правильный путь: от спарсенного товара до публикации

```
┌─────────────────────────────────────────────────────┐
│ Buyer API возвращает для товара:                     │
│   id_section = 28831  (buyer-side, не используем    │
│                         для публикации напрямую)     │
│   category.url цепочка:                             │
│     podpisochnye-servisy                            │
│     > steam-wallet-popolnenie                       │
│     > steam-wallet-direct-top-up                    │
│     > steam-wallet    ← LEAF SLUG (самый точный)    │
└─────────────────┬───────────────────────────────────┘
                  │  leaf slug = "steam-wallet"
                  ▼
┌─────────────────────────────────────────────────────┐
│ category_slug_mapping (БД)                          │
│   slug="steam-wallet" → seller_id=23                │
│   (seller_id здесь = seller_api_id)                 │
└─────────────────┬───────────────────────────────────┘
                  │  seller_api_id = 23
                  ▼
┌─────────────────────────────────────────────────────┐
│ seller_categories (БД) WHERE id=23                  │
│   ggsel_digi_catalog = 33729                        │
│   fee = 0.027 (2.7%)                               │
└─────────────────┬───────────────────────────────────┘
                  │
         ┌────────┴─────────┐
         ▼                  ▼
  create_offer           calculate_my_price
  category_id=33729      fee=2.7% → my_price
```

---

## Что сейчас хранится в БД

### `seller_categories` (9288 строк)

Основная таблица категорий продавца:

| Поле | Что это |
|---|---|
| `id` | `seller_api_id` — id в кабинете продавца |
| `title` | Название категории |
| `tree` | Полный путь (Игры > Roblox > Ключи) |
| `fee` | Комиссия площадки (от 0.001 до 0.69) |
| `has_children` | 0 = лист, 1 = родитель |
| `ggsel_digi_catalog` | ID для `create_offer` (заполнен у ~4500 из 9288) |
| `ancestor_ids` | JSON массив id родителей |

### `category_slug_mapping` (6036 строк)

Маппинг slug из buyer API → seller_api_id:

| Поле | Что это |
|---|---|
| `seller_id` | `seller_api_id` (PK — один id = одна запись) |
| `slug` | URL-slug из ggsel.net/catalog/\<slug\> |
| `match_score` | Качество матча: 1.0=точный, 0.95=хороший, 0.70+=приемлемый, 0.50=fallback |
| `seller_tree` | Путь в кабинете продавца |

### `category_stats` (1790 строк)

Количество товаров конкурентов в каждой категории:

| Поле | Что это |
|---|---|
| `slug` | Slug категории |
| `total` | Количество товаров у конкурентов |
| `parent_slug` | Родительская категория (иерархия) |
| `scanned_at` | Когда обновлялось |

### `cat_fees.json` (9288 записей)

Быстрый кэш комиссий: `{"seller_api_id": fee_float}`.  
Загружается в память при старте. Горячая перезагрузка при изменении файла.

---

## KNOWN_CATEGORIES — статус маппинга

60/60 slug замаплены. Разбивка по качеству:

### ✅ Точные матчи (score ≥ 0.90) — 38 категорий

| Slug | seller_api_id | fee | Товаров у конкурентов |
|---|---|---|---|
| `igry-po-nazvaniyu` | 34 | 2.7% | 354 315 |
| `podpisochnye-servisy` | 27 | 2.7% | 29 395 |
| `robux` / `roblox` | 28145 / 28144 | 6% / 15% | 6 292 |
| `programs-new` | 2 | 2.7% | 6 006 |
| `mobile-games` | 57673 | 14% | 5 169 |
| `valorant-9149` | 139479 | 7.5% | 928 |
| `dead-by-daylight-9610` | 153 | 2.7% | 1 338 |
| `fortnite` | 24128 | 15% | 897 |
| `spotify-premium` | 26480 | 2.7% | 238 |
| `xbox-game-pass-1` | 156749 | 0.1% | 389 |
| `games-steam` | 24 | 2.7% | 1 329 |
| `adobe-creative-cloud` | 74346 | 2.7% | 44 |
| `programming-software` | 140287 | 8% | 48 |
| ... и ещё 25 | | | |

### ⚠️ Fallback 33833 "Другое" (score = 0.50) — 21 категория

Это реально новые/нишевые категории которых **нет в кабинете продавца**. При публикации товар попадёт в "Цифровые товары > Другое" (fee 2.7%):

`arc-raiders`, `helldivers-2`, `ea-sports-fc-26-fifa-26`, `arena-breakout-infinite`, `europa-universalis-v`, `dispatch`, `games-anno-117-pax-romana`, `standoff-2`, `zenless-zone-zero`, `world-of-tanks-blitz`, `albion-online`, `microsoft-office-365`, `antivirus-eset`, `unlocktool`, `voicemod-pro`, `exitlag`, `autodesk`, `software-for-gamers-and-streaming`, `seo-software`, `subscriptions-for-all-occasions`, `other-games-currency`

**Как исправить:** добавить категорию в кабинете продавца → обновить маппинг через `_sync_missing_cats.py`.

---

## Комиссии по категориям

Реальные комиссии из `cat_fees.json` (выборка ключевых):

| Категория | fee | Итоговая my_price при cost=100₽ |
|---|---|---|
| Steam Wallet Россия (28831) | **4.5%** | ~70₽ |
| Roblox Подарочные карты (28145) | **6.0%** | ~154₽ |
| Spotify Premium покупка на аккаунт (108961) | **12%** | ~141₽ |
| Valorant Points (139479) | **7.5%** | ~136₽ |
| Red Dead Redemption (100318927) | **4%** | ~131₽ |
| Аккаунты (аренда) | **20-49%** | — |
| Цифровые товары > Другое (33833) | **15%** | ~144₽ |
| Большинство игр | **2.7%** | ~130₽ |

Формула: `my_price = (cost × (1 + fee + 0.027 + 0.05)) / (1 - 0.15)`  
Где: fee = комиссия категории, 0.027 = платёжная, 0.05 = резерв, 0.15 = целевая маржа.

---

## Инструменты обслуживания

### Обновить комиссии (раз в неделю или после изменений в кабинете)

```bash
# Обновить cat_fees.json из seller_categories в БД
python -m tools.refresh_cat_fees

# Полная синхронизация seller_categories с live seller API (нужен MSB + открытый браузер)
# Передать URL CDP открытого профиля SellerGGsel
python -m tools.refresh_seller_categories --cdp http://127.0.0.1:55992

# Подтянуть новые категории которые появились в buyer API но нет в seller_categories
python _sync_missing_cats.py
```

### Обновить количество товаров в категориях

```bash
# Через Flask API (нужен запущенный Flask)
curl -X POST http://127.0.0.1:5000/api/parser/fullscan/category-stats/scan

# Или напрямую через tools
python -m tools.refresh_categories_api
```

### Расширить slug mapping (когда ggsel добавит новые категории)

```bash
# BFS по HTML каталогу ggsel.net — нужен MSB + живой ggsel.net
python -m tools.match_slugs_html --max-depth 4
```

### Проверка готовности системы

```bash
python _check_launch.py     # полная проверка всех компонентов
python _final_check.py      # сквозной тест парсинг → fee → my_price
python _sync_missing_cats.py # обновить недостающие категории
```

### Обновить токен ggsel (раз в 30 дней)

```bash
python _refresh_token.py
```

---

## Текущие ограничения и TODO

### ⚠️ Не исправлено: `id_section` используется как `category_id` в БД

**Проблема:** `_save_batch` в `parser_engine.py` сохраняет `id_section` из buyer API как `category_id` в `parsed_products`. Но `id_section` — это buyer-side ID, а `create_offer` ждёт `ggsel_digi_catalog`.

**Влияние на fee:** НЕ влияет — fee берётся из `cat_fees.json` по `id_section`, а `cat_fees.json` заполнен из `seller_categories` где `id` = `seller_api_id`. Путаница с пространствами ID делает это "случайно правильным" только если buyer id_section совпадает с seller_api_id для той же категории.

**Влияние на публикацию:** При `create_offer` нужно явно получать `ggsel_digi_catalog` через:
```python
sc = conn.execute('SELECT ggsel_digi_catalog FROM seller_categories WHERE id=?', (category_id,)).fetchone()
publish_cat_id = sc['ggsel_digi_catalog'] if sc else 33833
```

**Правильный fix (TODO):** В `_save_batch` после резолва `id_section` → найти `ggsel_digi_catalog` и сохранить его отдельным полем `publish_category_id` в `parsed_products`.

### ⚠️ ggsel_digi_catalog заполнен только у 4412/9288 категорий

Для листьев добавленных в последнюю синхронизацию `ggsel_digi_catalog` = NULL. Запускать `refresh_seller_categories.py` периодически.

---

## Как добавить новую категорию в маппинг

1. Найти `seller_api_id` в кабинете продавца:
   ```bash
   # Поиск через seller API
   curl -b seller_cookies https://seller.ggsel.com/api/v1/categories?parent_id=5&limit=200
   ```

2. Получить `ggsel_digi_catalog` через tree:
   ```bash
   curl -b seller_cookies https://seller.ggsel.com/api/v1/categories/{id}/tree
   ```

3. Вставить в БД:
   ```sql
   INSERT INTO category_slug_mapping (seller_id, slug, seller_title, seller_tree, match_score)
   VALUES (seller_api_id, 'new-slug', 'Title', 'Path > Path', 0.95);
   ```

4. Обновить `cat_fees.json`:
   ```bash
   python -m tools.refresh_cat_fees
   ```
