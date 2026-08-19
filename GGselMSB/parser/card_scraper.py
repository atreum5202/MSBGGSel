"""
parser/card_scraper.py
======================
Парсер КАРТОЧЕК товаров ggsel.net — достаёт ВСЕ данные со страницы.

Источник: __NEXT_DATA__ (React SSR JSON, встроен в HTML) — надёжнее LD+JSON.
Содержит: цену, описание, опции, отзывы, продавца, рейтинг, изображения.

Использование:
  python -m parser.card_scraper              — обновить все товары из БД без деталей
  python -m parser.card_scraper --limit 100  — только 100 штук
  python -m parser.card_scraper --id 102298627  — один товар по ID

Можно запускать параллельно с парсером — только обновляет уже существующие строки.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ── Логирование ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("card_scraper")

# ── Константы ──────────────────────────────────────────────────────────────────

SITE_BASE   = "https://ggsel.net"
API_BASE    = "https://api.ggsel.com"

# Путь к БД
_DB_PATH = Path(__file__).parent / "ggsel_parser.db"

# Задержка между запросами (сек)
DELAY_BETWEEN = 0.8

# Таймаут HTTP (сек)
HTTP_TIMEOUT = 15

# ── HTTP клиент ────────────────────────────────────────────────────────────────

def _make_session():
    """Создать HTTP сессию — предпочитаем curl_cffi для обхода защиты."""
    try:
        from curl_cffi.requests import Session
        s = Session(impersonate="chrome120")
        s.headers.update({
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://ggsel.net/catalog",
        })
        return s, "cffi"
    except ImportError:
        pass

    try:
        import httpx
        s = httpx.Client(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
        )
        return s, "httpx"
    except ImportError:
        pass

    import urllib.request
    return None, "urllib"


# ── Парсинг HTML ───────────────────────────────────────────────────────────────

def _extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    """Извлечь __NEXT_DATA__ из HTML — основной источник данных."""
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _extract_ld_json(html: str) -> Optional[Dict[str, Any]]:
    """Извлечь LD+JSON (Product schema) — фолбэк."""
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            d = json.loads(raw.strip())
            if isinstance(d, dict) and d.get("@type") == "Product":
                return d
        except Exception:
            continue
    return None


def parse_card_from_html(html: str, product_id: str) -> Dict[str, Any]:
    """
    Основной парсер HTML карточки.
    Возвращает dict с полями для обновления в parsed_products.
    """
    result: Dict[str, Any] = {"product_id": product_id}

    # ── Попытка 1: __NEXT_DATA__ ──────────────────────────────────────────────
    next_data = _extract_next_data(html)
    if next_data:
        try:
            # Данные товара лежат в props.pageProps.product (или initialState.product)
            props = next_data.get("props", {}).get("pageProps", {})
            prod  = props.get("product") or props.get("good") or {}

            # Если не нашли в pageProps — смотрим в initialState (redux)
            if not prod:
                state = next_data.get("props", {}).get("initialReduxState", {})
                prod  = (state.get("product") or {}).get("product") or {}

            if prod:
                _fill_from_product_obj(result, prod)
        except Exception as e:
            log.debug("__NEXT_DATA__ parse error for %s: %s", product_id, e)

    # ── Попытка 2: LD+JSON ────────────────────────────────────────────────────
    ld = _extract_ld_json(html)
    if ld:
        _fill_from_ld_json(result, ld)

    # ── Попытка 3: прямые регэкспы из HTML ───────────────────────────────────
    if not result.get("title"):
        m = re.search(r'<h1[^>]*data-testid=["\']product-header-title["\'][^>]*>(.*?)</h1>',
                      html, re.DOTALL)
        if m:
            result["title"] = re.sub(r'<[^>]+>', '', m.group(1)).strip()

    if not result.get("price"):
        m = re.search(r'data-testid=["\']product-price["\'][^>]*>([0-9\s]+)\s*[₽$]',
                      html)
        if m:
            try:
                result["price"] = float(m.group(1).replace('\xa0', '').replace(' ', ''))
            except Exception:
                pass

    if not result.get("sales_count"):
        m = re.search(r'(\d+)\s*продаж', html)
        if m:
            try:
                result["sales_count"] = int(m.group(1))
            except Exception:
                pass

    if not result.get("reviews_count"):
        m = re.search(r'(\d+)\s*отзыв', html)
        if m:
            try:
                result["reviews_count"] = int(m.group(1))
            except Exception:
                pass

    # Рейтинг из aria-label="N Stars"
    if not result.get("rating"):
        m = re.search(r'aria-label=["\'](\d+(?:\.\d+)?)\s*Stars["\']', html)
        if m:
            try:
                result["rating"] = float(m.group(1))
            except Exception:
                pass

    result["detail_enriched_at"] = datetime.utcnow().isoformat()
    return result


def _fill_from_product_obj(out: Dict, prod: Dict) -> None:
    """Заполнить out из объекта product/good из __NEXT_DATA__."""

    # Название
    if prod.get("name"):
        out.setdefault("title", prod["name"].strip())

    # Цены
    for field, key in [("price", "price_wmr"), ("price_usd", "price_wmz"), ("price_eur", "price_wme")]:
        v = prod.get(key)
        if v is not None:
            try:
                out[field] = float(v)
            except Exception:
                pass

    # Если есть price (RUB) — записываем и в price и в source_price
    if out.get("price"):
        out.setdefault("source_price", out["price"])

    # Продажи
    for k in ("cnt_sell", "sales_count"):
        if prod.get(k) is not None:
            try:
                out["sales_count"] = int(prod[k])
                break
            except Exception:
                pass

    # Рейтинг товара
    if prod.get("rating") is not None:
        try:
            out["rating"] = float(prod["rating"])
        except Exception:
            pass

    # Изображение
    imgs = prod.get("images") or prod.get("image") or ""
    if isinstance(imgs, list):
        if imgs:
            out.setdefault("image_url", imgs[0] if isinstance(imgs[0], str) else "")
    elif isinstance(imgs, str) and imgs:
        out.setdefault("image_url", imgs)

    # Описание (HTML)
    desc = prod.get("info") or prod.get("add_info") or prod.get("description") or ""
    if desc:
        out["product_description"] = desc

    # Опции (варианты товара)
    options = prod.get("options") or prod.get("variations") or []
    if options:
        out["options_count"] = len(options)

    # Старая цена (скидка)
    old = prod.get("price_old") or prod.get("old_price")
    if old:
        try:
            out["price_old"] = float(old)
        except Exception:
            pass

    # In stock
    active = prod.get("is_active")
    if active is not None:
        out["in_stock"] = 1 if active else 0

    # Способы оплаты
    pm = prod.get("payment_methods") or prod.get("pay_methods")
    if pm:
        try:
            out["payment_methods"] = json.dumps(pm, ensure_ascii=False)
        except Exception:
            pass

    # Комиссия агентства
    fee = prod.get("agency_fee") or prod.get("commission")
    if fee is not None:
        try:
            out["agency_fee"] = float(fee)
        except Exception:
            pass

    # Флаги
    if prod.get("from_gsellers") is not None:
        out["from_gsellers"] = 1 if prod["from_gsellers"] else 0
    if prod.get("is_noindex") is not None:
        out["is_noindex"] = 1 if prod["is_noindex"] else 0

    # Категория
    cat = prod.get("category") or {}
    if isinstance(cat, dict):
        if cat.get("url"):
            out.setdefault("category_url", cat["url"])
        if cat.get("title") or cat.get("name"):
            out.setdefault("category_title", cat.get("title") or cat.get("name"))
    elif isinstance(cat, str) and cat:
        out.setdefault("category_url", cat)

    # Подкатегория / секция
    sec = prod.get("section") or prod.get("id_section")
    if sec is not None:
        try:
            out.setdefault("id_section", int(sec) if isinstance(sec, (int, float, str)) else None)
        except Exception:
            pass

    # Продавец
    seller = prod.get("seller") or {}
    if isinstance(seller, dict):
        if seller.get("name_seller") or seller.get("name"):
            out.setdefault("seller_name", seller.get("name_seller") or seller.get("name"))
        if seller.get("id"):
            try:
                out.setdefault("id_seller", int(seller["id"]))
            except Exception:
                pass

        # Рейтинг продавца
        stats = seller.get("statistics") or {}
        sr = stats.get("rating") or seller.get("rating")
        if sr is not None:
            try:
                out.setdefault("seller_rating", float(sr))
            except Exception:
                pass

        # Дата регистрации продавца
        reg = seller.get("created_at") or seller.get("registered_at")
        if reg:
            out.setdefault("seller_registered_at", str(reg))

        # Верификация продавца
        att = seller.get("attestat") or seller.get("verification")
        if att:
            out.setdefault("seller_attestat", str(att))

    # Отзывы
    good_r = prod.get("cnt_goodresponses") or prod.get("reviews_positive") or 0
    bad_r  = prod.get("cnt_badresponses")  or prod.get("reviews_negative") or 0
    try:
        good_r = int(good_r)
    except Exception:
        good_r = 0
    try:
        bad_r = int(bad_r)
    except Exception:
        bad_r = 0
    if good_r or bad_r:
        out["reviews_good_count"] = good_r
        out["reviews_bad_count"]  = bad_r
        out["reviews_count"]      = good_r + bad_r

    # Дата первого/последнего отзыва
    resp_list = prod.get("responses") or prod.get("reviews") or []
    if resp_list and isinstance(resp_list, list):
        dates = []
        for r in resp_list:
            if isinstance(r, dict):
                d = r.get("created_at") or r.get("date") or r.get("published_at")
                if d:
                    dates.append(str(d))
        if dates:
            out.setdefault("first_review_at", min(dates))
            out.setdefault("last_review_at",  max(dates))

    # URL товара
    slug = prod.get("url") or prod.get("slug")
    if slug and not out.get("url"):
        if slug.startswith("http"):
            out["url"] = slug
        else:
            out["url"] = f"https://ggsel.net/en/catalog/product/{slug}"


def _fill_from_ld_json(out: Dict, ld: Dict) -> None:
    """Заполнить out из LD+JSON Product schema (фолбэк)."""
    out.setdefault("title", ld.get("name", "").replace("$ $", "").strip())
    out.setdefault("product_description", ld.get("description", ""))
    out.setdefault("image_url", ld.get("image", ""))

    agg = ld.get("aggregateRating") or {}
    if agg.get("ratingValue"):
        try:
            out.setdefault("rating", float(agg["ratingValue"]))
        except Exception:
            pass
    if agg.get("reviewCount"):
        try:
            out.setdefault("reviews_count", int(agg["reviewCount"]))
        except Exception:
            pass

    offers = ld.get("offers") or {}
    # Цена из priceSpecification (берём USD/wmz и RUB/wmr)
    for spec in offers.get("priceSpecification") or []:
        if not isinstance(spec, dict):
            continue
        cur = spec.get("priceCurrency", "")
        try:
            p = float(spec.get("price") or 0)
        except Exception:
            p = 0.0
        if cur in ("USD", "wmz") and p:
            out.setdefault("price_usd", p)
        elif cur in ("RUB", "RUR", "wmr") and p:
            out.setdefault("price", p)
            out.setdefault("source_price", p)

    if not out.get("price_usd"):
        try:
            out.setdefault("price_usd", float(offers.get("price") or 0))
        except Exception:
            pass

    avail = offers.get("availability", "")
    if avail:
        out.setdefault("in_stock", 1 if "InStock" in avail else 0)

    seller_obj = offers.get("seller") or {}
    if isinstance(seller_obj, dict) and seller_obj.get("name"):
        out.setdefault("seller_name", seller_obj["name"])


# ── API детальный запрос ───────────────────────────────────────────────────────

def fetch_detail_via_api(product_id: str) -> Dict[str, Any]:
    """
    Запросить детали товара через api.ggsel.com/goods/{id}.
    Это быстрее, чем парсить HTML, но требует живого токена.
    """
    result: Dict[str, Any] = {"product_id": product_id}

    try:
        from .ggsel_api_client import get_client
        client = get_client()

        raw = client._get(f"/goods/{product_id}", params={"lang": "ru"})
        if not raw or not raw.get("data"):
            return result

        d = raw["data"]

        # Базовые поля
        for src, dst in [
            ("name", "title"),
            ("cnt_sell", "sales_count"),
            ("rating", "rating"),
            ("cnt_goodresponses", "reviews_good_count"),
            ("cnt_badresponses",  "reviews_bad_count"),
            ("is_active", "in_stock"),
            ("agency_fee", "agency_fee"),
            ("from_gsellers", "from_gsellers"),
            ("is_noindex", "is_noindex"),
            ("options_count", "options_count"),
        ]:
            if d.get(src) is not None:
                try:
                    v = d[src]
                    if dst in ("in_stock", "from_gsellers", "is_noindex"):
                        result[dst] = 1 if v else 0
                    elif dst in ("sales_count", "reviews_good_count", "reviews_bad_count", "options_count"):
                        result[dst] = int(v)
                    elif dst in ("rating", "agency_fee"):
                        result[dst] = float(v)
                    else:
                        result[dst] = str(v)
                except Exception:
                    pass

        # Цены
        for src, dst in [("price_wmr", "price"), ("price_wmz", "price_usd"), ("price_wme", "price_eur")]:
            if d.get(src) is not None:
                try:
                    result[dst] = float(d[src])
                except Exception:
                    pass
        if d.get("price_old"):
            try:
                result["price_old"] = float(d["price_old"])
            except Exception:
                pass

        # Описание
        desc = d.get("info") or d.get("add_info") or d.get("description") or ""
        if desc:
            result["product_description"] = desc

        # Изображение
        imgs = d.get("images") or ""
        if isinstance(imgs, list):
            result["image_url"] = imgs[0] if imgs else ""
        elif isinstance(imgs, str) and imgs:
            result["image_url"] = imgs

        # Способы оплаты
        pm = d.get("payment_methods") or d.get("pay_methods") or []
        if pm:
            try:
                result["payment_methods"] = json.dumps(pm, ensure_ascii=False)
            except Exception:
                pass

        # Категория
        cat = d.get("category") or d.get("section") or {}
        if isinstance(cat, dict):
            result["category_url"]   = cat.get("url", "")
            result["category_title"] = cat.get("title") or cat.get("name") or ""

        # Продавец
        seller = d.get("seller") or {}
        if isinstance(seller, dict):
            result["seller_name"] = seller.get("name_seller") or seller.get("name") or ""
            if seller.get("id"):
                try:
                    result["id_seller"] = int(seller["id"])
                except Exception:
                    pass
            stats = seller.get("statistics") or {}
            sr = stats.get("rating") or seller.get("rating")
            if sr:
                try:
                    result["seller_rating"] = float(sr)
                except Exception:
                    pass
            if seller.get("created_at"):
                result["seller_registered_at"] = str(seller["created_at"])
            if seller.get("attestat"):
                result["seller_attestat"] = str(seller["attestat"])

        # Рейтинг
        if d.get("rating"):
            try:
                result["rating"] = float(d["rating"])
            except Exception:
                pass

        # Отзывы итого
        gr = result.get("reviews_good_count", 0) or 0
        br = result.get("reviews_bad_count",  0) or 0
        if gr + br > 0:
            result["reviews_count"] = gr + br

        result["detail_enriched_at"] = datetime.utcnow().isoformat()

    except Exception as e:
        log.debug("API detail error for %s: %s", product_id, e)

    return result


def fetch_card_html(session, url: str, product_id: str) -> Dict[str, Any]:
    """Загрузить страницу и распарсить карточку."""
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT)
        if hasattr(r, "status_code"):
            sc = r.status_code
        else:
            sc = 200  # urllib

        if sc == 404:
            log.warning("404 for %s — пропускаем", product_id)
            return {}
        if sc != 200:
            log.warning("HTTP %d for %s", sc, product_id)
            return {}

        html = r.text if hasattr(r, "text") else r.read().decode("utf-8", errors="replace")
        return parse_card_from_html(html, product_id)

    except Exception as e:
        log.warning("fetch error %s: %s", product_id, e)
        return {}


# ── БД ─────────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_products_to_enrich(limit: int = 0, product_id: str = "") -> List[Tuple[str, str]]:
    """
    Вернуть список (product_id, url) товаров для обогащения деталями.
    Приоритет: без деталей, потом давно не обновлённые.
    """
    conn = get_db()
    try:
        if product_id:
            rows = conn.execute(
                "SELECT product_id, url FROM parsed_products WHERE product_id = ?",
                (product_id,),
            ).fetchall()
        else:
            q = """
                SELECT product_id, url FROM parsed_products
                WHERE (detail_enriched_at IS NULL OR detail_enriched_at = '')
                   OR product_description IS NULL OR product_description = ''
                ORDER BY
                    CASE WHEN detail_enriched_at IS NULL THEN 0 ELSE 1 END,
                    sales_count DESC
            """
            if limit:
                q += f" LIMIT {int(limit)}"
            rows = conn.execute(q).fetchall()
        return [(str(r[0]), str(r[1] or "")) for r in rows]
    finally:
        conn.close()


def update_product(data: Dict[str, Any]) -> bool:
    """Обновить поля товара в БД."""
    if not data or not data.get("product_id"):
        return False

    UPDATABLE = {
        "title", "original_title", "image_url",
        "price", "price_usd", "price_eur", "price_old", "source_price",
        "sales_count", "rating", "reviews_count",
        "reviews_good_count", "reviews_bad_count",
        "first_review_at", "last_review_at",
        "product_description", "payment_methods",
        "agency_fee", "from_gsellers", "is_noindex",
        "category_url", "category_title", "options_count",
        "seller_name", "id_seller", "seller_rating",
        "seller_registered_at", "seller_attestat",
        "in_stock", "detail_enriched_at",
    }

    fields = {k: v for k, v in data.items() if k in UPDATABLE and v is not None}
    if not fields:
        return False

    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(
        f"{k} = COALESCE(?, {k})" if k not in (
            "price", "price_usd", "price_eur", "sales_count", "rating",
            "updated_at", "detail_enriched_at", "in_stock"
        ) else f"{k} = ?"
        for k in fields
    )
    values = list(fields.values()) + [data["product_id"]]

    conn = get_db()
    try:
        conn.execute(
            f"UPDATE parsed_products SET {set_clause} WHERE product_id = ?",
            values,
        )
        conn.commit()
        return True
    except Exception as e:
        log.error("DB update error for %s: %s", data["product_id"], e)
        return False
    finally:
        conn.close()


# ── Основной процесс ───────────────────────────────────────────────────────────

def build_url(product_id: str, url: str) -> str:
    """Построить URL карточки по имеющимся данным."""
    if url and url.startswith("http"):
        # Убираем UTM и прочий мусор
        parsed = urlparse(url)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return clean
    # Fallback: числовой ID → URL не знаем, пробуем API
    return ""


def run_scraper(
    limit: int = 0,
    product_id: str = "",
    workers: int = 3,
    use_api: bool = True,
    use_html: bool = True,
    delay: float = DELAY_BETWEEN,
    _state=None,
    _lock=None,
):
    """
    Основная функция скрапинга.
    use_api  — сначала пробуем API (быстро, нужен токен)
    use_html — фолбэк через HTML страницу (медленнее, не требует токена)
    """
    products = get_products_to_enrich(limit=limit, product_id=product_id)
    if _state is not None and _lock is not None:
        with _lock:
            _state["total"] = len(products)
            _state["processed"] = 0
            _state["errors"] = 0
    log.info("Товаров для обогащения: %d", len(products))
    if not products:
        log.info("Нечего обновлять — все товары уже имеют детали")
        return

    session, session_type = _make_session()
    log.info("HTTP сессия: %s", session_type)

    success = 0
    errors  = 0

    def process_one(pid: str, url: str) -> bool:
        data: Dict[str, Any] = {}

        # Пробуем API сначала
        if use_api:
            try:
                data = fetch_detail_via_api(pid)
            except Exception as e:
                log.debug("API failed for %s: %s", pid, e)

        # Если API не дал нужных данных — парсим HTML
        if use_html and (not data.get("product_description") or not data.get("price")):
            card_url = build_url(pid, url)
            if not card_url and session:
                # Ищем по ID в поиске ggsel
                card_url = f"{SITE_BASE}/en/catalog/product/{pid}"

            if card_url and session:
                html_data = fetch_card_html(session, card_url, pid)
                # Мержим: API данные приоритетнее по ценам, HTML — по описанию
                for k, v in html_data.items():
                    if k not in data or not data[k]:
                        data[k] = v

        if not data or len(data) <= 1:
            return False

        ok = update_product(data)
        if ok:
            name_short = (data.get("title") or pid)[:50]
            price_info = f"₽{data.get('price', 0):.0f}" if data.get("price") else "цена?"
            sales_info = f"{data.get('sales_count', '?')} прод."
            rating_info = f"★{data.get('rating', '?')}"
            log.info("✓ [%s] %s | %s | %s | %s",
                     pid, name_short, price_info, sales_info, rating_info)
        return ok

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_one, pid, url): pid
                for pid, url in products
            }
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    ok = future.result()
                    if ok:
                        success += 1
                    else:
                        errors += 1
                except Exception as e:
                    log.error("Worker error %s: %s", pid, e)
                    errors += 1
                time.sleep(delay / workers)
    else:
        for pid, url in products:
            ok = process_one(pid, url)
            if ok:
                success += 1
            else:
                errors += 1
            time.sleep(delay)

    log.info("Готово: успешно=%d, ошибок=%d из %d", success, errors, len(products))


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Парсер карточек товаров ggsel.net")
    ap.add_argument("--limit",   type=int, default=0,  help="Максимум товаров (0=все)")
    ap.add_argument("--id",      type=str, default="", help="Один product_id")
    ap.add_argument("--workers", type=int, default=3,  help="Параллельных потоков")
    ap.add_argument("--no-api",  action="store_true",  help="Не использовать API")
    ap.add_argument("--no-html", action="store_true",  help="Не использовать HTML парсинг")
    ap.add_argument("--delay",   type=float, default=DELAY_BETWEEN, help="Задержка между запросами")
    ap.add_argument("--db",      type=str, default="", help="Путь к БД (если не стандартный)")
    args = ap.parse_args()

    if args.db:
        _DB_PATH = Path(args.db)

    if not _DB_PATH.exists():
        log.error("БД не найдена: %s", _DB_PATH)
        log.error("Укажите путь через --db или запустите из папки GGselParser/parser/")
        sys.exit(1)

    log.info("БД: %s", _DB_PATH)

    run_scraper(
        limit=args.limit,
        product_id=args.id,
        workers=args.workers,
        use_api=not args.no_api,
        use_html=not args.no_html,
        delay=args.delay,
    )
