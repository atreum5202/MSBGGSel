# Отчёт: исправление категоризации товаров

**Дата:** 2026-08-19  
**Файлы изменены:** `parser/parser_engine.py`, `parser/competitor_scanner.py`

---

## Анализ проблем

### Проблема 1: `category` = slug запроса, а не реальная категория товара

**Где:** `parser/parser_engine.py`, функция `_enrich_one` (L~2003–2005)

**Причина:**  
В `to_engine_product` (`ggsel_api_client.py` L739) устанавливается `p.category = category`,
где `category` — аргумент запроса (slug страницы парсинга, например `spotify-premium`).

В `_enrich_one` была проверка:
```python
if not p_obj.category:
    p_obj.category = leaf_slug
```
Но `p_obj.category` уже непустой (там slug запроса), поэтому `leaf_slug` из реальной
цепочки API (`/goods/{id}` → `category.parent`) **никогда не записывался**.

В `_save_batch` (`parser_engine.py` L2314):
```python
cat_slug_val = p.category or category or ""  # брал slug запроса
```

**Итог:** в поле `category` записывался `spotify-premium` вместо, например, `spotify-premium-ru`.

---

### Проблема 2: `category_id` иногда NULL или неверный

**Где:** `parser/parser_engine.py` `_save_batch` (L~2305–2308), `parser/competitor_scanner.py` `_save_products_batch` (L~371–399)

**Причина в `parser_engine.py`:**  
```python
cat_id = (
    extra.get("category_id")
    or _get_category_id(conn, category or p.category)  # slug запроса → не найдёт
)
```
Поле `id_section` из API-листинга всегда есть в `extra` (заполняется в `to_engine_product`),
но в `_save_batch` оно не использовалось как источник `category_id`.

**Причина в `competitor_scanner.py`:**  
```python
seller_cat_id = self._resolve_category(p.category)  # p.category = slug запроса
```
Нет попытки взять `category_id` или `id_section` из `p.extra`.

---

### Проблема 3: breadcrumb не сохранялся в `competitor_scanner.py`

**Где:** `parser/competitor_scanner.py` `_save_products_batch`

**Причина:**  
В исходном INSERT не было поля `breadcrumb`. Даже если `extra["breadcrumb"]` был заполнен
через `_enrich_one`, в БД он не попадал через `competitor_scanner`.

---

## Внесённые изменения

### `parser/parser_engine.py` — `_enrich_one` (L~2004–2007 + L~2020–2025)

**До:**
```python
if not p_obj.category:
    p_obj.category = leaf_slug
```

**После:**
```python
# Всегда ставим реальный leaf_slug из цепочки API
# (не slug запроса, который был в p_obj.category)
p_obj.category = leaf_slug
p_obj.extra["category_slug"] = leaf_slug
```

Добавлен также fallback для breadcrumb когда `/goods/{id}` не вернул `category` object:
```python
elif not p_obj.extra.get("breadcrumb"):
    search_title = p_obj.extra.get("search_title") or ""
    if search_title:
        p_obj.extra["breadcrumb"] = search_title
```

---

### `parser/parser_engine.py` — `_save_batch` (L~2311–2326)

**До:**
```python
cat_id = (
    extra.get("category_id")
    or _get_category_id(conn, category or p.category)
)
cat_slug_val = p.category or category or ""
```

**После:**
```python
cat_id = (
    extra.get("category_id")
    or (int(extra["id_section"]) if extra.get("id_section") else None)
    or _get_category_id(conn, extra.get("category_slug") or category or p.category)
)
cat_slug_val = extra.get("category_slug") or p.category or category or ""
```

**Цепочка приоритетов `category_id`:**
1. `extra["category_id"]` — результат `_resolve_category_id` из `_enrich_one` (лучший вариант)
2. `extra["id_section"]` — прямой ID из API-листинга (всегда присутствует в extra)
3. `_get_category_id(slug)` — поиск по leaf_slug в таблице `category_slugs`

**Цепочка приоритетов `category` slug:**
1. `extra["category_slug"]` — реальный leaf slug из цепочки `/goods/{id}`
2. `p.category` — может быть обновлён в `_enrich_one` (тот же leaf_slug после фикса)
3. `category` аргумент — slug запроса (fallback)

---

### `parser/competitor_scanner.py` — `_save_products_batch` (L~370–419)

**Изменения:**

1. **`category` → реальный slug товара:**
   ```python
   real_cat_slug = extra.get("category_slug") or p.category or ""
   # в INSERT: real_cat_slug вместо p.category
   ```

2. **`category_id` → приоритет из extra:**
   ```python
   cat_id_from_extra = (
       extra.get("category_id")
       or (int(extra["id_section"]) if extra.get("id_section") else None)
   )
   if cat_id_from_extra:
       seller_cat_id = int(cat_id_from_extra)
   else:
       seller_cat_id = self._resolve_category(real_cat_slug or p.category)
   ```

3. **breadcrumb теперь сохраняется:**
   - Добавлено поле `breadcrumb` в INSERT
   - В ON CONFLICT: `breadcrumb = COALESCE(NULLIF(excluded.breadcrumb,''), parsed_products.breadcrumb)`
   - В ON CONFLICT добавлено `category = excluded.category` (обновляем при репарсинге)

---

## Что НЕ изменялось

- Схема БД (`parser/schema.sql`) — не трогалась
- Таблица `categories`, `category_slugs`, `cat_fees.json` — не трогались
- `_resolve_category_id` — логика не изменилась
- `to_engine_product` — не изменялась (источник данных)
- Все остальные файлы проекта

---

## Проверка синтаксиса

```
python -m py_compile parser/competitor_scanner.py  → OK
python -m py_compile parser/parser_engine.py       → OK
```

---

## Оставшиеся риски

- Если `/goods/{id}` не возвращает поле `category` (например, товар удалён или приватный),
  `category` останется slug запроса, `category_slug` в extra не появится.
  В этом случае `id_section` из листинга будет использован как `category_id` (второй приоритет).

- `search_title` как fallback breadcrumb — это название подкатегории в одно слово
  (например `"Steam Wallet"`), не полный путь. Это лучше чем пусто, но менее информативно
  чем полный `breadcrumb` из цепочки `category.parent`.
