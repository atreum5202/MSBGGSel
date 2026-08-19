"""
parser/price_scan.py
====================
Price Scan — адаптивное бинарное разбиение каталога ggsel.net по ценовым диапазонам.

Стратегия:
  1. PRICE SCAN  — режет каталог на ценовые диапазоны < 9000 товаров (бинарное деление),
                   пагинирует каждый диапазон полностью → все ~384k товаров без ограничения 10k.
                   Пишет: базовые поля + catalog_page + catalog_position.

  2. DETAIL SCAN — параллельно обогащает каждый новый товар через GET /goods/{id}:
                   описание, варианты, способы оплаты, данные продавца, цена со скидкой.

  3. REVIEW SCAN — параллельно получает GET /goods/{id}/reviews:
                   кол-во отзывов+/-, дату первого и последнего отзыва.

Это единственный способ получить весь каталог: стандартный Full Scan ограничен ~10k/категорию
из-за пагинации API (max offset). Price Scan обходит это через фильтр min_price/max_price.

Использование (из routes.py / Flask):
    from .price_scan import price_scan_start, price_scan_stop, price_scan_status

Использование (CLI):
    python -m parser.price_scan
    python -m parser.price_scan --workers 4 --no-detail
    python -m parser.price_scan --ct 2 48 19
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import httpx

log = logging.getLogger("ggselv7.price_scan")

# ── Константы ─────────────────────────────────────────────────────────────────
MAX_PER_RANGE = 9000    # порог разбиения диапазона
MAX_DEPTH     = 16      # защита от бесконечной рекурсии
MAX_PRICE     = 9999.0  # максимальная цена в каталоге (USD)
PAGE_LIMIT    = 50      # товаров на страницу
MAX_EMPTY     = 3       # страниц подряд пустых → выход


# ── Глобальное состояние ──────────────────────────────────────────────────────
_price_scan_state: dict = {
    "running":        False,
    "stopped":        True,
    "thread":         None,
    "started_at":     None,
    "finished_at":    None,
    "last_error":     None,
    # price phase
    "found":          0,
    "saved":          0,
    "ranges_done":    0,
    "ranges_split":   0,
    # detail phase
    "detail_done":    0,
    "detail_errors":  0,
    "detail_queue_size": 0,
    # config
    "workers":        0,
    "enrich":         True,
    "ct_ids":         [],
}
_price_scan_lock = threading.Lock()
_price_queue:  queue.Queue = queue.Queue()
_detail_queue: queue.Queue = queue.Queue()
_stopped = threading.Event()


# ── Публичный API ─────────────────────────────────────────────────────────────

def price_scan_start(
    workers_per_account: int = 4,
    enrich: bool = True,
    ct_ids: Optional[List[int]] = None,
) -> dict:
    """
    Запускает Price Scan в фоновом потоке.

    workers_per_account — потоков на аккаунт (default 4)
    enrich              — запускать detail+review обогащение (default True)
    ct_ids              — фильтр content_type_id (default: все из FULL_SCAN_CONTENT_TYPES)
    """
    from .parser_engine import FULL_SCAN_CONTENT_TYPES
    from .ggsel_api_client import load_all_accounts

    st = _price_scan_state
    with _price_scan_lock:
        if st["running"] and not st["stopped"]:
            return {"ok": False, "error": "Price Scan уже запущен"}

    _stopped.clear()
    # Очищаем очереди от предыдущего прогона
    while not _price_queue.empty():
        try: _price_queue.get_nowait()
        except queue.Empty: break
    while not _detail_queue.empty():
        try: _detail_queue.get_nowait()
        except queue.Empty: break

    ids      = list(ct_ids or FULL_SCAN_CONTENT_TYPES)
    accounts = load_all_accounts()
    n_workers = len(accounts) * workers_per_account

    with _price_scan_lock:
        st.update({
            "running":        True,
            "stopped":        False,
            "started_at":     datetime.utcnow().isoformat(),
            "finished_at":    None,
            "last_error":     None,
            "found":          0,
            "saved":          0,
            "ranges_done":    0,
            "ranges_split":   0,
            "detail_done":    0,
            "detail_errors":  0,
            "detail_queue_size": 0,
            "workers":        n_workers,
            "enrich":         enrich,
            "ct_ids":         ids,
        })

    t = threading.Thread(
        target=_price_scan_master,
        args=(ids, accounts, workers_per_account, enrich),
        daemon=True,
        name="price-scan-master",
    )
    with _price_scan_lock:
        st["thread"] = t
    t.start()

    return {
        "ok":      True,
        "ct_ids":  ids,
        "accounts": len(accounts),
        "workers": n_workers,
        "enrich":  enrich,
    }


def price_scan_stop() -> dict:
    _stopped.set()
    with _price_scan_lock:
        _price_scan_state["stopped"] = True
        _price_scan_state["running"] = False
    return {"ok": True, "message": "Price Scan остановлен"}


def price_scan_status() -> dict:
    with _price_scan_lock:
        st = {k: v for k, v in _price_scan_state.items() if k != "thread"}
    st["detail_queue_size"] = _detail_queue.qsize()
    st["price_queue_size"]  = _price_queue.qsize()
    return st


# ── Мастер-поток ──────────────────────────────────────────────────────────────

def _price_scan_master(
    ct_ids: List[int],
    accounts: list,
    workers_per_account: int,
    enrich: bool,
) -> None:
    n_workers = len(accounts) * workers_per_account

    # Начальные задачи: каждый content_type_id → весь ценовой диапазон [0, MAX_PRICE]
    for ct_id in ct_ids:
        _price_queue.put((ct_id, 0.0, MAX_PRICE, 0))

    log.info("[PriceScan] Старт: %d категорий, %d воркеров, enrich=%s",
             len(ct_ids), n_workers, enrich)

    price_threads = []
    for wid in range(n_workers):
        acc = accounts[wid % len(accounts)]
        t = threading.Thread(
            target=_price_worker,
            args=(wid, acc, enrich),
            daemon=True,
            name=f"price-w{wid}",
        )
        price_threads.append(t)
        t.start()

    detail_threads = []
    if enrich:
        for wid in range(n_workers):
            acc = accounts[wid % len(accounts)]
            t = threading.Thread(
                target=_detail_worker,
                args=(wid, acc),
                daemon=True,
                name=f"detail-w{wid}",
            )
            detail_threads.append(t)
            t.start()

    for t in price_threads:
        t.join()

    # Дожидаемся опустошения очереди деталей
    if enrich and not _stopped.is_set():
        _detail_queue.join()

    _stopped.set()
    for t in detail_threads:
        t.join(timeout=5)

    with _price_scan_lock:
        _price_scan_state["running"]     = False
        _price_scan_state["stopped"]     = True
        _price_scan_state["finished_at"] = datetime.utcnow().isoformat()

    log.info("[PriceScan] Готово. Сохранено: %d | Обогащено: %d",
             _price_scan_state["saved"], _price_scan_state["detail_done"])


# ── Низкоуровневые запросы к API ──────────────────────────────────────────────

def _headers(client) -> dict:
    return {
        "Authorization": f"Bearer {client._access_token}",
        "Content-Type":  "application/json",
        "lang":          "en",
    }


def _get_total(client, ct_id: int, min_p: float, max_p: float) -> int:
    body = {
        "lang": "en", "currency": "wmz", "limit": 1, "page": 1,
        "sort": "sortByRec", "query_string": "", "search_after": [],
        "content_type_ids": [ct_id], "with_filters": False,
        "is_preorders": False, "with_forbidden": False,
        "min_price": str(min_p) if min_p > 0 else "",
        "max_price": str(max_p) if max_p < MAX_PRICE else "",
    }
    try:
        r = httpx.post("https://api.ggsel.com/elastic/goods/categories",
                       json=body, headers=_headers(client), timeout=15)
        return int(r.json().get("data", {}).get("total", 0))
    except Exception:
        return 0


def _fetch_page(client, ct_id: int, min_p: float, max_p: float, page: int) -> Optional[list]:
    body = {
        "lang": "en", "currency": "wmz", "limit": PAGE_LIMIT, "page": page,
        "sort": "sortByRec", "query_string": "", "search_after": [],
        "content_type_ids": [ct_id], "with_filters": False,
        "is_preorders": False, "with_forbidden": False,
        "min_price": str(min_p) if min_p > 0 else "",
        "max_price": str(max_p) if max_p < MAX_PRICE else "",
    }
    try:
        r = httpx.post("https://api.ggsel.com/elastic/goods/categories",
                       json=body, headers=_headers(client), timeout=15)
        return r.json().get("data", {}).get("items") or []
    except Exception as e:
        log.warning("[PriceScan] fetch_page error: %s", e)
        return None


def _fetch_detail(client, id_goods: int) -> Optional[dict]:
    """GET /goods/{id} — полные данные товара."""
    try:
        r = httpx.get(f"https://api.ggsel.com/goods/{id_goods}",
                      headers=_headers(client), timeout=15)
        if r.status_code == 200:
            return r.json().get("data")
    except Exception as e:
        log.debug("[PriceScan] detail error %d: %s", id_goods, e)
    return None


def _fetch_reviews(client, id_goods: int) -> Optional[list]:
    """GET /goods/{id}/reviews — отзывы товара."""
    try:
        r = httpx.get(f"https://api.ggsel.com/goods/{id_goods}/reviews",
                      headers=_headers(client), timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", [])
            return data if isinstance(data, list) else []
    except Exception as e:
        log.debug("[PriceScan] reviews error %d: %s", id_goods, e)
    return None


# ── Конвертация листинга → Product ───────────────────────────────────────────

def _item_to_product(item: dict, ct_id: int, ct_name: str,
                     page: int, idx: int):
    from .parser_engine import Product, _calc_raw_score

    id_goods = item.get("id_goods")
    if not id_goods:
        return None

    slug        = item.get("url") or ""
    sales_count = int(item.get("cnt_sell") or 0)
    rating      = float(item["rating"]) if item.get("rating") else None
    in_stock    = bool(item.get("is_active", True))

    p = Product()
    p.external_id      = str(id_goods)
    p.name             = (item.get("name") or "").strip()
    p.price            = float(item.get("price_wmr") or item.get("price_wmr_for_one") or 0)
    p.currency         = "RUB"
    p.url              = f"https://ggsel.net/en/catalog/product/{slug}" if slug else ""
    p.image_url        = item.get("image") or ""
    p.sales_count      = sales_count
    p.in_stock         = in_stock
    p.rating           = rating
    p.seller           = item.get("seller_name") or ""
    p.catalog_page     = page
    p.catalog_position = (page - 1) * PAGE_LIMIT + idx + 1
    p.profit_score     = _calc_raw_score(sales_count, rating or 0.0, 0, in_stock)

    # Расширенные поля — в extra (так читает _save_batch)
    p.extra = {
        "price_usd":      float(item.get("price_wmz") or item.get("price_wmz_for_one") or 0),
        "price_eur":      float(item.get("price_wme") or item.get("price_wme_for_one") or 0),
        "price_old":      float(item.get("category_discount") or 0) or None,
        "id_section":     item.get("id_section"),
        "content_type_id": ct_id,
        "search_title":   item.get("search_title") or "",
        "seller_id":      str(item.get("id_seller") or ""),
        "from_gsellers":  1 if item.get("from_gsellers") else 0,
        "is_noindex":     1 if item.get("hidden_from_search") else 0,
    }
    return p


# ── Применение деталей и отзывов → dict ─────────────────────────────────────

def _extract_detail(detail: dict) -> dict:
    """Извлекает поля из /goods/{id} в плоский dict для UPDATE."""
    pm   = detail.get("payment_methods")
    opts = detail.get("options") or []
    cat    = detail.get("category") or {}
    seller = detail.get("seller") or {}
    return {
        "product_description":  detail.get("info") or None,
        "agency_fee":           detail.get("agency_fee"),
        "from_gsellers":        1 if detail.get("from_gsellers") else 0,
        "is_noindex":           1 if detail.get("is_noindex") else 0,
        "payment_methods":      json.dumps(pm, ensure_ascii=False) if pm else None,
        "price_old":            float(detail.get("old_price") or 0) or None,
        "options_count":        len(opts) if opts else None,
        "category_url":         cat.get("url") if isinstance(cat, dict) else None,
        "category_title":       cat.get("title") if isinstance(cat, dict) else None,
        "seller_registered_at": seller.get("date_registration") if isinstance(seller, dict) else None,
        "seller_attestat":      str(seller.get("attestat") or "") or None if isinstance(seller, dict) else None,
        "reviews_good_count":   detail.get("cnt_goodresponses"),
        "reviews_bad_count":    detail.get("cnt_badresponses"),
    }


def _extract_reviews(reviews: list) -> dict:
    """Извлекает даты первого/последнего отзыва из /goods/{id}/reviews."""
    if not reviews:
        return {}
    dates = [r.get("date_response") for r in reviews if r.get("date_response")]
    if not dates:
        return {}
    return {"last_review_at": dates[0], "first_review_at": dates[-1]}


# ── Воркер PRICE SCAN ─────────────────────────────────────────────────────────

def _price_worker(wid: int, account: dict, enrich: bool) -> None:
    from .ggsel_api_client import make_client
    from .parser_engine import FULL_SCAN_CT_NAMES, get_engine
    from .dedup import is_fresh, is_rejected, invalidate_name_cache

    client   = make_client(account)
    eng      = get_engine()
    acc_name = account["name"]

    while not _stopped.is_set():
        try:
            task = _price_queue.get(timeout=5)
        except queue.Empty:
            break

        ct_id, min_p, max_p, depth = task
        try:
            ct_name = FULL_SCAN_CT_NAMES.get(ct_id, str(ct_id))
            total   = _get_total(client, ct_id, min_p, max_p)

            if total == 0:
                with _price_scan_lock:
                    _price_scan_state["ranges_done"] += 1
                _price_queue.task_done()
                continue

            # Бинарное разбиение диапазона
            if total > MAX_PER_RANGE and depth < MAX_DEPTH:
                mid = round((min_p + max_p) / 2, 4)
                _price_queue.put((ct_id, min_p, mid, depth + 1))
                _price_queue.put((ct_id, mid,   max_p, depth + 1))
                with _price_scan_lock:
                    _price_scan_state["ranges_split"] += 1
                _price_queue.task_done()
                continue

            log.info("[PW%d/%s] ▶ %s [$%.2f–$%.2f] ~%d товаров",
                     wid, acc_name, ct_name, min_p, max_p, total)

            seen: set = set()
            page = 0
            empty = 0

            while not _stopped.is_set():
                page += 1
                raw  = _fetch_page(client, ct_id, min_p, max_p, page)

                if raw is None:
                    time.sleep(1)
                    continue
                if not raw:
                    empty += 1
                    if empty >= MAX_EMPTY:
                        break
                    continue
                empty = 0

                with _price_scan_lock:
                    _price_scan_state["found"] += len(raw)

                batch = []
                for idx, item in enumerate(raw):
                    eid = str(item.get("id_goods") or "")
                    if not eid or eid in seen:
                        continue
                    if is_fresh(eid):
                        continue
                    if is_rejected(eid):
                        continue
                    seen.add(eid)
                    p = _item_to_product(item, ct_id, ct_name, page, idx)
                    if p:
                        batch.append(p)

                if batch:
                    saved = eng._save_batch(batch, ct_name)
                    with _price_scan_lock:
                        _price_scan_state["saved"] += len(saved)
                        total_now = _price_scan_state["saved"]
                    invalidate_name_cache()

                    for s in saved:
                        log.info(
                            "[PW%d/%s] ✓ %-15s стр.%d  %s | $%.2f | ⭐%.1f | продаж:%d | итого:%d",
                            wid, acc_name, ct_name, page,
                            (s.get("title") or "")[:45],
                            s.get("price_usd") or 0,
                            s.get("rating") or 0,
                            s.get("sales_count") or 0,
                            total_now,
                        )

                    if enrich:
                        for p in batch:
                            _detail_queue.put(p.external_id)

                time.sleep(0.1)

            with _price_scan_lock:
                _price_scan_state["ranges_done"] += 1

        except Exception as e:
            log.exception("[PW%d/%s] ошибка: %s", wid, acc_name, e)
            with _price_scan_lock:
                _price_scan_state["last_error"] = str(e)
        finally:
            _price_queue.task_done()

    log.info("[PW%d/%s] price-worker завершён.", wid, acc_name)


# ── Воркер DETAIL + REVIEW SCAN ──────────────────────────────────────────────

def _detail_worker(wid: int, account: dict) -> None:
    """Обогащает товары деталями и отзывами через /goods/{id}."""
    from .ggsel_api_client import make_client
    from .db_init import get_db_path

    client   = make_client(account)
    acc_name = account["name"]

    while not _stopped.is_set():
        try:
            product_id = _detail_queue.get(timeout=10)
        except queue.Empty:
            if _stopped.is_set():
                break
            continue

        try:
            id_goods = int(product_id)
            detail   = _fetch_detail(client, id_goods)
            reviews  = _fetch_reviews(client, id_goods)

            if detail or reviews:
                fields: dict = {}
                if detail:
                    fields.update(_extract_detail(detail))
                if reviews:
                    fields.update(_extract_reviews(reviews))

                if fields:
                    _save_enrichment(product_id, fields, get_db_path())

                with _price_scan_lock:
                    _price_scan_state["detail_done"] += 1

        except Exception as e:
            log.debug("[DW%d] ошибка %s: %s", wid, product_id, e)
            with _price_scan_lock:
                _price_scan_state["detail_errors"] += 1
        finally:
            _detail_queue.task_done()
            time.sleep(0.05)


def _save_enrichment(product_id: str, fields: dict, db_path: str) -> None:
    """
    Обновляет только детальные поля, не трогая основные данные товара.
    fields — dict с ключами совпадающими с именами столбцов parsed_products.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE parsed_products SET
                reviews_good_count   = COALESCE(?, reviews_good_count),
                reviews_bad_count    = COALESCE(?, reviews_bad_count),
                first_review_at      = COALESCE(?, first_review_at),
                last_review_at       = COALESCE(?, last_review_at),
                product_description  = COALESCE(?, product_description),
                payment_methods      = COALESCE(?, payment_methods),
                agency_fee           = COALESCE(?, agency_fee),
                from_gsellers        = COALESCE(?, from_gsellers),
                is_noindex           = COALESCE(?, is_noindex),
                category_url         = COALESCE(?, category_url),
                category_title       = COALESCE(?, category_title),
                options_count        = COALESCE(?, options_count),
                price_old            = COALESCE(?, price_old),
                seller_registered_at = COALESCE(?, seller_registered_at),
                seller_attestat      = COALESCE(?, seller_attestat),
                detail_enriched_at   = ?,
                updated_at           = ?
            WHERE product_id = ?
        """, (
            fields.get("reviews_good_count"),
            fields.get("reviews_bad_count"),
            fields.get("first_review_at"),
            fields.get("last_review_at"),
            fields.get("product_description"),
            fields.get("payment_methods"),
            fields.get("agency_fee"),
            fields.get("from_gsellers"),
            fields.get("is_noindex"),
            fields.get("category_url"),
            fields.get("category_title"),
            fields.get("options_count"),
            fields.get("price_old"),
            fields.get("seller_registered_at"),
            fields.get("seller_attestat"),
            now, now,
            product_id,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug("[PriceScan] save_enrichment %s: %s", product_id, e)


# ── CLI точка входа ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    from parser.db_init import get_db_path, init_db
    from parser.parser_engine import FULL_SCAN_CONTENT_TYPES

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                Path(__file__).resolve().parent.parent / "data" / "logs" / "price_scan.log",
                encoding="utf-8",
            ),
        ],
    )
    for lib in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="GGsel Price Scan — полный сбор каталога")
    ap.add_argument("--workers",   type=int, default=4, help="Воркеров на аккаунт (default 4)")
    ap.add_argument("--no-detail", action="store_true",  help="Не запускать detail/review обогащение")
    ap.add_argument("--ct", nargs="*", type=int, default=None, help="Фильтр content_type_id")
    args = ap.parse_args()

    init_db()
    result = price_scan_start(
        workers_per_account=args.workers,
        enrich=not args.no_detail,
        ct_ids=args.ct,
    )
    print(result)

    try:
        while True:
            time.sleep(15)
            st = price_scan_status()
            if not st["running"]:
                break
            conn = sqlite3.connect(get_db_path())
            db_now    = conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0]
            enriched  = conn.execute(
                "SELECT COUNT(*) FROM parsed_products WHERE detail_enriched_at IS NOT NULL"
            ).fetchone()[0]
            conn.close()
            log.info(
                ">> В БД: %d | сохранено: %d | найдено: %d | "
                "диапазонов: %d сплитов / %d готово | detail: %d (err:%d) | q:%d",
                db_now, st["saved"], st["found"],
                st["ranges_split"], st["ranges_done"],
                st["detail_done"], st["detail_errors"],
                st["detail_queue_size"],
            )
    except KeyboardInterrupt:
        price_scan_stop()
        log.info("Остановлено.")

    conn = sqlite3.connect(get_db_path())
    final    = conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0]
    enriched = conn.execute(
        "SELECT COUNT(*) FROM parsed_products WHERE detail_enriched_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    log.info("ГОТОВО. В БД: %d товаров | Обогащено: %d", final, enriched)
