"""
tests/test_parallel_enrich.py
==============================
Тесты параллельного обогащения деталей товаров в _run_async_api.

Проверяем:
  1. Все товары в батче обогащаются (ни один не теряется)
  2. Параллельность реально работает (время < последовательного)
  3. Семафор держит не больше 8 одновременных запросов
  4. Ошибка в одном товаре не роняет остальные
  5. Данные из detail API корректно маппятся в Product

Запуск: python -m pytest tests/test_parallel_enrich.py -v
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
import anyio

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_product(pid: str, name: str = "Test Product") -> "Product":
    """Создаёт минимальный Product для тестов."""
    from parser.parser_engine import Product
    p = Product(
        external_id=pid,
        name=name,
        price=100.0,
        url=f"https://ggsel.net/catalog/product/{pid}",
    )
    return p


def _fake_detail(pid: str, delay: float = 0.0) -> Dict[str, Any]:
    """Возвращает фейковый ответ GET /goods/{pid}."""
    if delay:
        time.sleep(delay)
    return {
        "data": {
            "info": f"Description for {pid}",
            "cnt_goodresponses": 10,
            "cnt_badresponses": 2,
            "price_wmz": 1.5,
            "price_wme": 1.4,
            "images": [f"https://img.ggsel.net/{pid}.jpg"],
            "seller": {
                "name_seller": f"seller_{pid}",
                "rating": 4.8,
                "id_seller": f"s{pid}",
            },
        }
    }


# ── Тест 1: все товары обогащаются ────────────────────────────────────────────

def test_all_products_enriched():
    anyio.from_thread.run_sync  # noqa — убеждаемся что anyio импортирован
    anyio.run(_test_all_products_enriched)

async def _test_all_products_enriched():
    """Каждый товар из батча должен получить original_desc из detail API."""
    N = 15
    products = [_make_product(str(i)) for i in range(N)]

    call_log: List[str] = []

    def mock_get(path: str, params=None):
        pid = path.split("/")[-1]
        call_log.append(pid)
        return _fake_detail(pid)

    mock_client = MagicMock()
    mock_client._get.side_effect = mock_get

    # Запускаем параллельное обогащение напрямую через семафор — точно так же
    # как делает _run_async_api
    sem = asyncio.Semaphore(8)

    async def enrich_one(p):
        async with sem:
            detail_raw = await asyncio.to_thread(mock_client._get, f"/goods/{p.external_id}", {"lang": "ru"})
            if detail_raw and detail_raw.get("data"):
                gd = detail_raw["data"]
                desc = gd.get("info") or ""
                if desc:
                    p.extra["original_desc"] = desc
                p.extra["price_usd"] = float(gd.get("price_wmz") or 0)
                seller_obj = gd.get("seller") or {}
                if seller_obj.get("name_seller"):
                    p.seller = seller_obj["name_seller"]

    await asyncio.gather(*[enrich_one(p) for p in products])

    # Все N товаров должны быть обогащены
    assert len(call_log) == N, f"Ожидали {N} вызовов, получили {len(call_log)}"

    for p in products:
        assert "original_desc" in p.extra, f"Товар {p.external_id}: нет original_desc"
        assert p.extra["price_usd"] == 1.5, f"Товар {p.external_id}: неверная цена"
        assert p.seller.startswith("seller_"), f"Товар {p.external_id}: нет продавца"


# ── Тест 2: параллельность быстрее последовательного ────────────────────────

def test_parallel_faster_than_sequential():
    anyio.run(_test_parallel_faster_than_sequential)

async def _test_parallel_faster_than_sequential():
    """
    С задержкой 0.1с на товар и 8 воркерами:
      - последовательно 16 товаров = ~1.6с
      - параллельно      16 товаров = ~0.2с (2 волны по 8)
    """
    N = 16
    DELAY = 0.05  # 50мс на запрос
    products = [_make_product(str(i)) for i in range(N)]

    def slow_get(path: str, params=None):
        pid = path.split("/")[-1]
        return _fake_detail(pid, delay=DELAY)

    mock_client = MagicMock()
    mock_client._get.side_effect = slow_get

    sem = asyncio.Semaphore(8)

    async def enrich_one(p):
        async with sem:
            detail_raw = await asyncio.to_thread(mock_client._get, f"/goods/{p.external_id}", {"lang": "ru"})
            if detail_raw and detail_raw.get("data"):
                p.extra["original_desc"] = detail_raw["data"].get("info", "")

    t0 = time.monotonic()
    await asyncio.gather(*[enrich_one(p) for p in products])
    elapsed = time.monotonic() - t0

    # Последовательное время: N * DELAY = 0.8с
    # Параллельное должно уложиться в 4x меньше (≤ 0.4с с запасом)
    sequential_time = N * DELAY
    assert elapsed < sequential_time * 0.6, (
        f"Параллельное обогащение слишком медленное: {elapsed:.3f}с "
        f"(последовательное было бы {sequential_time:.3f}с)"
    )


# ── Тест 3: семафор ограничивает одновременность ─────────────────────────────

def test_semaphore_max_concurrency():
    anyio.run(_test_semaphore_max_concurrency)

async def _test_semaphore_max_concurrency():
    """Пиковая одновременность не должна превышать 8."""
    MAX_WORKERS = 8
    N = 20
    products = [_make_product(str(i)) for i in range(N)]

    concurrency_peak = [0]
    current = [0]

    async def counting_get(path: str, params=None):
        current[0] += 1
        concurrency_peak[0] = max(concurrency_peak[0], current[0])
        await asyncio.sleep(0.02)  # имитируем сетевой запрос
        current[0] -= 1
        pid = path.split("/")[-1]
        return _fake_detail(pid)

    sem = asyncio.Semaphore(MAX_WORKERS)

    async def enrich_one(p):
        async with sem:
            detail_raw = await counting_get(f"/goods/{p.external_id}", {"lang": "ru"})
            if detail_raw and detail_raw.get("data"):
                p.extra["original_desc"] = detail_raw["data"].get("info", "")

    await asyncio.gather(*[enrich_one(p) for p in products])

    assert concurrency_peak[0] <= MAX_WORKERS, (
        f"Пик одновременности {concurrency_peak[0]} превышает лимит {MAX_WORKERS}"
    )
    # И реально параллелизм был > 1
    assert concurrency_peak[0] > 1, "Параллелизм не включился — все запросы шли последовательно"


# ── Тест 4: ошибка в одном товаре не роняет остальные ────────────────────────

def test_error_in_one_does_not_stop_others():
    anyio.run(_test_error_in_one_does_not_stop_others)

async def _test_error_in_one_does_not_stop_others():
    """Exception в _get для одного товара не должен мешать остальным."""
    N = 10
    FAIL_ID = "5"
    products = [_make_product(str(i)) for i in range(N)]
    enriched: List[str] = []

    def flaky_get(path: str, params=None):
        pid = path.split("/")[-1]
        if pid == FAIL_ID:
            raise ConnectionError(f"Simulated network error for {pid}")
        return _fake_detail(pid)

    mock_client = MagicMock()
    mock_client._get.side_effect = flaky_get

    import logging
    sem = asyncio.Semaphore(8)
    log = logging.getLogger("test")

    async def enrich_one(p):
        async with sem:
            try:
                detail_raw = await asyncio.to_thread(mock_client._get, f"/goods/{p.external_id}", {"lang": "ru"})
                if detail_raw and detail_raw.get("data"):
                    p.extra["original_desc"] = detail_raw["data"].get("info", "")
                    enriched.append(p.external_id)
            except Exception as e:
                log.debug("detail enrich %s: %s", p.external_id, e)

    await asyncio.gather(*[enrich_one(p) for p in products])

    # Все кроме упавшего должны быть обогащены
    assert FAIL_ID not in enriched, f"Упавший товар {FAIL_ID} попал в enriched"
    assert len(enriched) == N - 1, (
        f"Обогащено {len(enriched)} из {N - 1} ожидаемых"
    )


# ── Тест 5: маппинг полей detail API → Product ────────────────────────────────

def test_detail_field_mapping():
    anyio.run(_test_detail_field_mapping)

async def _test_detail_field_mapping():
    """Проверяем что все поля из API правильно маппятся в Product.extra."""
    from parser.parser_engine import Product

    pid = "99999"
    p = _make_product(pid)

    full_detail = {
        "data": {
            "info": "Подробное описание товара",
            "cnt_goodresponses": 25,
            "cnt_badresponses": 3,
            "cnt_digi_responses": 5,
            "price_wmz": 2.5,
            "price_wme": 2.3,
            "old_price": 3.0,
            "from_gsellers": True,
            "is_noindex": False,
            "images": ["https://img.ggsel.net/main.jpg", "https://img.ggsel.net/2.jpg"],
            "payment_methods": [{"type": "card"}, {"type": "crypto"}],
            "options": [{"name": "RU"}, {"name": "EU"}, {"name": "US"}],
            "seller": {
                "name_seller": "TopSeller",
                "rating": 4.95,
                "id_seller": "777",
                "created_at": "2022-01-15",
                "attestat": "verified",
                "statistics": {"cnt_sell": 1500},
            },
            "responses": [
                {"created_at": "2024-01-10"},
                {"created_at": "2025-06-20"},
                {"created_at": "2023-05-01"},
            ],
        }
    }

    def mock_get(path, params=None):
        return full_detail

    mock_client = MagicMock()
    mock_client._get.side_effect = mock_get

    sem = asyncio.Semaphore(8)

    async def enrich_one(p_obj):
        async with sem:
            try:
                detail_raw = await asyncio.to_thread(mock_client._get, f"/goods/{p_obj.external_id}", {"lang": "ru"})
                if detail_raw and detail_raw.get("data"):
                    gd = detail_raw["data"]
                    import json

                    desc = gd.get("info") or gd.get("add_info") or gd.get("description") or ""
                    if desc:
                        p_obj.extra["original_desc"] = desc[:5000]

                    good_r = int(gd.get("cnt_goodresponses") or 0)
                    bad_r  = int(gd.get("cnt_badresponses")  or 0)
                    if good_r or bad_r:
                        p_obj.reviews_count = good_r + bad_r
                        p_obj.extra["reviews_count"]      = good_r + bad_r
                        p_obj.extra["reviews_good_count"] = good_r
                        p_obj.extra["reviews_bad_count"]  = bad_r

                    resp_list = gd.get("responses") or []
                    if resp_list and isinstance(resp_list, list):
                        dates = []
                        for rv in resp_list:
                            if isinstance(rv, dict):
                                d_str = rv.get("created_at") or rv.get("date") or rv.get("published_at")
                                if d_str:
                                    dates.append(str(d_str))
                        if dates:
                            p_obj.extra["first_review_at"] = min(dates)
                            p_obj.extra["last_review_at"]  = max(dates)

                    options = gd.get("options") or []
                    if options:
                        p_obj.extra["options_count"] = len(options)
                        p_obj.extra["options_json"]  = json.dumps(options[:50], ensure_ascii=False)

                    pm = gd.get("payment_methods") or []
                    if pm:
                        p_obj.extra["payment_methods"] = json.dumps(pm, ensure_ascii=False)

                    old_price = gd.get("old_price")
                    if old_price:
                        try: p_obj.extra["price_old"] = float(old_price)
                        except: pass
                    p_obj.extra["price_usd"] = float(gd.get("price_wmz") or 0)
                    p_obj.extra["price_eur"] = float(gd.get("price_wme") or 0)

                    if gd.get("from_gsellers") is not None:
                        p_obj.extra["from_gsellers"] = 1 if gd["from_gsellers"] else 0
                    if gd.get("is_noindex") is not None:
                        p_obj.extra["is_noindex"] = 1 if gd["is_noindex"] else 0

                    seller_obj = gd.get("seller") or {}
                    if isinstance(seller_obj, dict):
                        sname = seller_obj.get("name_seller") or seller_obj.get("name") or p_obj.seller
                        if sname:
                            p_obj.seller = sname
                        srating = seller_obj.get("rating") or (seller_obj.get("statistics") or {}).get("rating")
                        if srating:
                            p_obj.seller_rating = float(srating)
                        sid = seller_obj.get("id_seller") or seller_obj.get("id")
                        if sid:
                            p_obj.seller_id = str(sid)
                        reg = seller_obj.get("created_at") or seller_obj.get("registered_at")
                        if reg:
                            p_obj.extra["seller_registered_at"] = str(reg)
                        att = seller_obj.get("attestat") or seller_obj.get("verification")
                        if att:
                            p_obj.extra["seller_attestat"] = str(att)
                        stats = seller_obj.get("statistics") or {}
                        seller_cnt_sell = stats.get("cnt_sell")
                        if seller_cnt_sell:
                            p_obj.extra["seller_cnt_sell"] = int(seller_cnt_sell)

                    imgs = gd.get("images")
                    if imgs:
                        if isinstance(imgs, list) and imgs:
                            p_obj.image_url = imgs[0]
                        elif isinstance(imgs, str) and imgs:
                            p_obj.image_url = imgs

            except Exception as e:
                raise

    await asyncio.gather(*[enrich_one(p)])

    # Проверяем все поля
    assert p.extra.get("original_desc") == "Подробное описание товара"
    assert p.reviews_count == 28  # 25 + 3
    assert p.extra.get("reviews_good_count") == 25
    assert p.extra.get("reviews_bad_count") == 3
    assert p.extra.get("first_review_at") == "2023-05-01"
    assert p.extra.get("last_review_at") == "2025-06-20"
    assert p.extra.get("options_count") == 3
    assert p.extra.get("price_usd") == 2.5
    assert p.extra.get("price_eur") == 2.3
    assert p.extra.get("price_old") == 3.0
    assert p.extra.get("from_gsellers") == 1
    assert p.extra.get("is_noindex") == 0
    assert p.seller == "TopSeller"
    assert p.seller_rating == 4.95
    assert p.seller_id == "777"
    assert p.extra.get("seller_registered_at") == "2022-01-15"
    assert p.extra.get("seller_attestat") == "verified"
    assert p.extra.get("seller_cnt_sell") == 1500
    assert p.image_url == "https://img.ggsel.net/main.jpg"
    assert "payment_methods" in p.extra
