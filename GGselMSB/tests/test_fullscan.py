"""
tests/test_fullscan.py
======================
Тесты для:
  1. get_products_by_type  — новый метод GgselApiClient
  2. load_all_accounts     — чтение одного и нескольких аккаунтов
  3. make_client           — независимый клиент для воркера
  4. full_scan_start/stop/status — state-machine полного скана
  5. _full_scan_worker     — воркер: дедупликация, обход страниц, остановка
  6. shop_* поля в _save_batch  — заполняются из extra продавца

Запуск: python -m pytest tests/test_fullscan.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import anyio
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_api_product(pid: int, name: str = "Test", cnt_sell: int = 10,
                      rating: float = 4.5, ct_id: int = 2) -> "ApiProduct":
    from parser.ggsel_api_client import ApiProduct
    return ApiProduct(
        id_goods=pid, name=name, url=str(pid),
        price_wmz=1.5, price_wmr=130.0, price_wme=1.4,
        cnt_sell=cnt_sell, is_active=True,
        content_type_id=ct_id, rating=rating,
        id_seller=999, seller_name="ShopX",
        id_section=1001, search_title="Keys",
    )


def _make_mock_client(products_per_page: List[List["ApiProduct"]]):
    """Клиент, возвращающий заданные страницы по get_products_by_type."""
    client = MagicMock()
    call_count = [0]

    def _get_by_type(content_type_id, page=1, limit=100, currency="wmz", sort="sortByRec"):
        idx = page - 1
        if idx < len(products_per_page):
            call_count[0] += 1
            return products_per_page[idx]
        return []

    client.get_products_by_type.side_effect = _get_by_type

    def _to_engine(ap, category="", detail=None):
        from parser.parser_engine import Product
        p = Product()
        p.external_id = str(ap.id_goods)
        p.name = ap.name
        p.price = ap.price_wmr
        p.seller = ap.seller_name
        p.seller_id = str(ap.id_seller)
        p.sales_count = ap.cnt_sell
        p.in_stock = ap.is_active
        p.rating = ap.rating
        p.extra = {"seller_cnt_sell": 50}
        return p

    client.to_engine_product.side_effect = _to_engine
    client._call_count = call_count
    return client


# ══════════════════════════════════════════════════════════════════════════════
# 1. get_products_by_type
# ══════════════════════════════════════════════════════════════════════════════

def test_get_products_by_type_calls_correct_endpoint():
    """Метод должен POST в /elastic/goods/categories с content_type_ids."""
    from parser.ggsel_api_client import GgselApiClient

    client = GgselApiClient.__new__(GgselApiClient)
    client._access_token = "tok"
    client._refresh_token = ""
    client._token_exp = 9999999999
    client._client = None
    client.profile_name = "test"
    client.account_email = ""
    client.ggsel_user_id = ""

    captured = {}

    def mock_post(path, body, host=None):
        captured["path"] = path
        captured["body"] = body
        return {"data": {"items": [], "total": 0}}

    client._post = mock_post

    result = client.get_products_by_type(content_type_id=2, page=1, limit=50)

    assert captured["path"] == "/elastic/goods/categories"
    assert captured["body"]["content_type_ids"] == [2]
    assert captured["body"]["limit"] == 50
    assert captured["body"]["page"] == 1
    assert result == []


def test_get_products_by_type_parses_items():
    """Метод должен вернуть список ApiProduct из items."""
    from parser.ggsel_api_client import GgselApiClient

    client = GgselApiClient.__new__(GgselApiClient)
    client._access_token = "tok"
    client._refresh_token = ""
    client._token_exp = 9999999999
    client._client = None
    client.profile_name = "test"
    client.account_email = ""
    client.ggsel_user_id = ""

    fake_items = [
        {"id_goods": 111, "name": "Game Key", "url": "game-key",
         "price_wmz": 2.5, "price_wmr": 220.0, "price_wme": 2.3,
         "cnt_sell": 500, "is_active": True, "content_type_id": 2,
         "rating": 4.8, "id_seller": 7, "seller_name": "BestSeller",
         "id_section": 5, "search_title": "Steam Keys"},
    ]

    def mock_post(path, body, host=None):
        return {"data": {"items": fake_items, "total": 1}}

    client._post = mock_post

    result = client.get_products_by_type(content_type_id=2, page=1, limit=100)

    assert len(result) == 1
    assert result[0].id_goods == 111
    assert result[0].name == "Game Key"
    assert result[0].price_wmz == 2.5
    assert result[0].content_type_id == 2


def test_get_total_by_type():
    """get_total_by_type должен вернуть поле total из ответа."""
    from parser.ggsel_api_client import GgselApiClient

    client = GgselApiClient.__new__(GgselApiClient)
    client._access_token = "tok"
    client._refresh_token = ""
    client._token_exp = 9999999999
    client._client = None
    client.profile_name = "test"
    client.account_email = ""
    client.ggsel_user_id = ""

    def mock_post(path, body, host=None):
        return {"data": {"items": [], "total": 82065}}

    client._post = mock_post

    total = client.get_total_by_type(2)
    assert total == 82065


# ══════════════════════════════════════════════════════════════════════════════
# 2. load_all_accounts
# ══════════════════════════════════════════════════════════════════════════════

def test_load_all_accounts_single(tmp_path):
    """Файл с одним токеном — возвращает один аккаунт."""
    from parser.ggsel_api_client import load_all_accounts, _TOKEN_FILE

    token_file = tmp_path / "ggsel_tokens.json"
    token_file.write_text(json.dumps({
        "access_token":  "acc_main",
        "refresh_token": "ref_main",
        "exp":           9999999999,
        "profile_name":  "my_account",
    }))

    with patch("parser.ggsel_api_client._TOKEN_FILE", token_file):
        accounts = load_all_accounts()

    assert len(accounts) == 1
    assert accounts[0]["name"] == "my_account"
    assert accounts[0]["access_token"] == "acc_main"


def test_load_all_accounts_with_extras(tmp_path):
    """Файл с основным + extra_tokens — возвращает все аккаунты."""
    from parser.ggsel_api_client import load_all_accounts

    token_file = tmp_path / "ggsel_tokens.json"
    token_file.write_text(json.dumps({
        "access_token":  "acc_main",
        "refresh_token": "ref_main",
        "exp":           9999999999,
        "profile_name":  "account1",
        "extra_tokens": [
            {"access_token": "acc_extra1", "profile_name": "account2", "exp": 9999999999},
            {"access_token": "acc_extra2", "profile_name": "account3", "exp": 9999999999},
        ],
    }))

    with patch("parser.ggsel_api_client._TOKEN_FILE", token_file):
        accounts = load_all_accounts()

    assert len(accounts) == 3
    names = [a["name"] for a in accounts]
    assert "account1" in names
    assert "account2" in names
    assert "account3" in names


def test_load_all_accounts_no_file_uses_default(tmp_path):
    """Если файла нет — возвращает один дефолтный аккаунт."""
    from parser.ggsel_api_client import load_all_accounts

    missing = tmp_path / "nonexistent.json"
    with patch("parser.ggsel_api_client._TOKEN_FILE", missing):
        accounts = load_all_accounts()

    assert len(accounts) == 1
    assert accounts[0]["name"] == "default"
    assert accounts[0]["access_token"]  # не пустой


def test_load_all_accounts_skips_empty_extra(tmp_path):
    """extra_tokens без access_token пропускаются."""
    from parser.ggsel_api_client import load_all_accounts

    token_file = tmp_path / "ggsel_tokens.json"
    token_file.write_text(json.dumps({
        "access_token": "acc_main",
        "profile_name": "main",
        "exp": 9999999999,
        "extra_tokens": [
            {"profile_name": "broken"},       # нет access_token
            {"access_token": "acc2", "profile_name": "good", "exp": 9999999999},
        ],
    }))

    with patch("parser.ggsel_api_client._TOKEN_FILE", token_file):
        accounts = load_all_accounts()

    assert len(accounts) == 2
    assert accounts[1]["name"] == "good"


# ══════════════════════════════════════════════════════════════════════════════
# 3. make_client
# ══════════════════════════════════════════════════════════════════════════════

def test_make_client_creates_independent_instance():
    """make_client возвращает не-singleton с правильными токенами."""
    from parser.ggsel_api_client import make_client, GgselApiClient

    account = {
        "name":          "worker1",
        "access_token":  "tok_worker1",
        "refresh_token": "ref_worker1",
        "exp":           9999999999,
    }
    client = make_client(account)

    assert isinstance(client, GgselApiClient)
    assert client._access_token == "tok_worker1"
    assert client._refresh_token == "ref_worker1"
    assert client.profile_name == "worker1"
    # Не singleton
    assert client is not GgselApiClient.get()


def test_make_client_multiple_independent():
    """Два make_client — два разных объекта с разными токенами."""
    from parser.ggsel_api_client import make_client

    c1 = make_client({"name": "w1", "access_token": "tok1", "exp": 0})
    c2 = make_client({"name": "w2", "access_token": "tok2", "exp": 0})

    assert c1 is not c2
    assert c1._access_token == "tok1"
    assert c2._access_token == "tok2"


# ══════════════════════════════════════════════════════════════════════════════
# 4. full_scan_start / stop / status
# ══════════════════════════════════════════════════════════════════════════════

def test_fullscan_start_returns_ok():
    """full_scan_start должен вернуть ok=True и корректные метаданные."""
    from parser.parser_engine import full_scan_start, full_scan_stop

    # load_all_accounts импортируется внутри full_scan_start через from .ggsel_api_client
    # _full_scan_master — модульная функция в parser_engine
    with patch("parser.ggsel_api_client.load_all_accounts",
               return_value=[{"name": "acc1", "access_token": "t1", "exp": 0}]), \
         patch("parser.parser_engine._full_scan_master"):

        full_scan_stop()
        time.sleep(0.05)
        result = full_scan_start(run_ai=False, ct_ids=[2, 48], workers_per_account=2)

    assert result["ok"] is True
    assert result["categories"] == 2
    assert result["accounts"] == 1
    assert result["total_workers"] == 2
    assert result["run_ai"] is False

    full_scan_stop()


def test_fullscan_start_prevents_double_start():
    """Второй вызов full_scan_start пока идёт первый — ok=False."""
    from parser.parser_engine import full_scan_start, full_scan_stop

    barrier = threading.Event()

    def slow_master(*args, **kwargs):
        barrier.wait(timeout=3)

    with patch("parser.ggsel_api_client.load_all_accounts",
               return_value=[{"name": "acc1", "access_token": "t1", "exp": 0}]), \
         patch("parser.parser_engine._full_scan_master", side_effect=slow_master):

        full_scan_stop()
        time.sleep(0.05)
        r1 = full_scan_start(run_ai=False, ct_ids=[2])
        time.sleep(0.05)
        r2 = full_scan_start(run_ai=False, ct_ids=[48])

    assert r1["ok"] is True
    assert r2["ok"] is False
    assert "уже запущен" in r2.get("error", "")

    barrier.set()
    full_scan_stop()


def test_fullscan_stop():
    """full_scan_stop устанавливает stopped=True, running=False."""
    from parser.parser_engine import full_scan_start, full_scan_stop, full_scan_status

    with patch("parser.ggsel_api_client.load_all_accounts",
               return_value=[{"name": "acc1", "access_token": "t1", "exp": 0}]), \
         patch("parser.parser_engine._full_scan_master"):
        full_scan_stop()
        time.sleep(0.05)
        full_scan_start(run_ai=False, ct_ids=[2])
        time.sleep(0.05)

    full_scan_stop()
    st = full_scan_status()
    assert st["stopped"] is True
    assert st["running"] is False


def test_fullscan_status_has_required_keys():
    """full_scan_status возвращает все нужные ключи."""
    from parser.parser_engine import full_scan_status

    st = full_scan_status()
    for key in ("running", "stopped", "workers", "total_saved", "total_found",
                "ct_done", "ct_remaining", "started_at", "last_error", "run_ai"):
        assert key in st, f"Ключ {key!r} отсутствует в full_scan_status()"


# ══════════════════════════════════════════════════════════════════════════════
# 5. _full_scan_worker — логика обхода
# ══════════════════════════════════════════════════════════════════════════════

def test_worker_processes_all_pages():
    """Воркер обходит все страницы пока get_products_by_type не вернёт пустой список."""
    from parser.parser_engine import (
        _full_scan_worker, _full_scan_state, _full_scan_lock, FULL_SCAN_CT_NAMES
    )

    products_page1 = [_make_api_product(i) for i in range(1, 6)]
    products_page2 = [_make_api_product(i) for i in range(6, 11)]

    client = _make_mock_client([products_page1, products_page2, []])

    saved_calls = []
    eng = MagicMock()

    def mock_save(batch, category, **kw):
        saved_calls.extend([{"product_id": p.external_id} for p in batch])
        return [{"product_id": p.external_id} for p in batch]

    eng._save_batch.side_effect = mock_save

    # Инициализируем состояние
    with _full_scan_lock:
        _full_scan_state.update({
            "running": True, "stopped": False,
            "total_saved": 0, "total_found": 0,
            "ct_done": [], "ct_remaining": [2],
            "workers": [{"worker_id": 0, "account": "t", "ct_id": None,
                         "ct_name": None, "page": 0, "ct_done": [], "saved": 0}],
        })

    with patch("parser.parser_engine.get_engine", return_value=eng), \
         patch("parser.dedup.is_fresh", return_value=False), \
         patch("parser.dedup.is_rejected", return_value=False), \
         patch("parser.dedup.invalidate_name_cache"), \
         patch("parser.parser_engine.time") as mock_time:
        mock_time.sleep = MagicMock()
        _full_scan_worker(0, [2], client, "test_acc", False, "sortByRec")

    # Все 10 товаров сохранены
    assert len(saved_calls) == 10
    # API: стр.1 + стр.2 + 3 пустых = 5 вызовов (воркер останавливается после 3 подряд пустых)
    assert client.get_products_by_type.call_count == 5


def test_worker_deduplicates():
    """Воркер не сохраняет одинаковые external_id дважды."""
    from parser.parser_engine import (
        _full_scan_worker, _full_scan_state, _full_scan_lock
    )

    # Две страницы с одинаковыми id
    dup = [_make_api_product(1), _make_api_product(2)]
    client = _make_mock_client([dup, dup, []])

    saved_ids = []
    eng = MagicMock()

    def mock_save(batch, category, **kw):
        saved_ids.extend([p.external_id for p in batch])
        return [{"product_id": p.external_id} for p in batch]

    eng._save_batch.side_effect = mock_save

    with _full_scan_lock:
        _full_scan_state.update({
            "running": True, "stopped": False,
            "total_saved": 0, "total_found": 0,
            "ct_done": [], "ct_remaining": [2],
            "workers": [{"worker_id": 0, "account": "t", "ct_id": None,
                         "ct_name": None, "page": 0, "ct_done": [], "saved": 0}],
        })

    with patch("parser.parser_engine.get_engine", return_value=eng), \
         patch("parser.dedup.is_fresh", return_value=False), \
         patch("parser.dedup.is_rejected", return_value=False), \
         patch("parser.dedup.invalidate_name_cache"), \
         patch("parser.parser_engine.time") as mock_time:
        mock_time.sleep = MagicMock()
        _full_scan_worker(0, [2], client, "test_acc", False, "sortByRec")

    # Несмотря на дубли — каждый id только один раз
    assert sorted(saved_ids) == sorted(set(saved_ids))
    assert len(saved_ids) == 2


def test_worker_stops_on_signal():
    """Воркер прерывается когда _full_scan_state['stopped'] = True."""
    from parser.parser_engine import (
        _full_scan_worker, _full_scan_state, _full_scan_lock
    )

    call_count = [0]

    def slow_get(content_type_id, page=1, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # После первого вызова выставляем stopped
            with _full_scan_lock:
                _full_scan_state["stopped"] = True
        return [_make_api_product(page * 100 + i) for i in range(3)]

    client = MagicMock()
    client.get_products_by_type.side_effect = slow_get

    def _to_engine(ap, category="", detail=None):
        from parser.parser_engine import Product
        p = Product()
        p.external_id = str(ap.id_goods)
        p.name = ap.name
        p.price = 100.0
        p.extra = {}
        return p

    client.to_engine_product.side_effect = _to_engine

    eng = MagicMock()
    eng._save_batch.return_value = []

    with _full_scan_lock:
        _full_scan_state.update({
            "running": True, "stopped": False,
            "total_saved": 0, "total_found": 0,
            "ct_done": [], "ct_remaining": [2, 48],
            "workers": [{"worker_id": 0, "account": "t", "ct_id": None,
                         "ct_name": None, "page": 0, "ct_done": [], "saved": 0}],
        })

    with patch("parser.parser_engine.get_engine", return_value=eng), \
         patch("parser.dedup.is_fresh", return_value=False), \
         patch("parser.dedup.is_rejected", return_value=False), \
         patch("parser.dedup.invalidate_name_cache"), \
         patch("parser.parser_engine.time") as mock_time:
        mock_time.sleep = MagicMock()
        _full_scan_worker(0, [2, 48], client, "test_acc", False, "sortByRec")

    # Остановился после первого вызова, не пошёл на ct=48
    assert call_count[0] == 1


def test_worker_skips_consecutive_empty():
    """Воркер заканчивает категорию после 3 подряд пустых страниц."""
    from parser.parser_engine import (
        _full_scan_worker, _full_scan_state, _full_scan_lock
    )

    pages = [
        [_make_api_product(1)],  # страница 1
        [],                       # пустая 1
        [],                       # пустая 2
        [],                       # пустая 3 → стоп
    ]
    client = _make_mock_client(pages)
    eng = MagicMock()
    eng._save_batch.return_value = [{"product_id": "1"}]

    with _full_scan_lock:
        _full_scan_state.update({
            "running": True, "stopped": False,
            "total_saved": 0, "total_found": 0,
            "ct_done": [], "ct_remaining": [2],
            "workers": [{"worker_id": 0, "account": "t", "ct_id": None,
                         "ct_name": None, "page": 0, "ct_done": [], "saved": 0}],
        })

    with patch("parser.parser_engine.get_engine", return_value=eng), \
         patch("parser.dedup.is_fresh", return_value=False), \
         patch("parser.dedup.is_rejected", return_value=False), \
         patch("parser.dedup.invalidate_name_cache"), \
         patch("parser.parser_engine.time") as mock_time:
        mock_time.sleep = MagicMock()
        _full_scan_worker(0, [2], client, "test_acc", False, "sortByRec")

    # 1 страница с данными + 3 пустые = 4 вызова
    assert client.get_products_by_type.call_count == 4


# ══════════════════════════════════════════════════════════════════════════════
# 6. shop_* в _save_batch
# ══════════════════════════════════════════════════════════════════════════════

# Минимальная DDL для тестов shop_* — только нужные колонки
_SHOP_TEST_DDL = """
CREATE TABLE IF NOT EXISTS parsed_products (
    product_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    original_title TEXT,
    original_desc TEXT,
    price REAL,
    my_price REAL,
    source_price REAL,
    category TEXT,
    category_id INTEGER,
    url TEXT,
    seller_name TEXT,
    seller_id TEXT,
    seller_rating REAL,
    rating REAL,
    sales_count INTEGER DEFAULT 0,
    reviews_count INTEGER DEFAULT 0,
    in_stock INTEGER DEFAULT 1,
    image_url TEXT,
    profit_score REAL,
    status TEXT DEFAULT 'pending',
    approval_status TEXT DEFAULT 'pending',
    breadcrumb TEXT DEFAULT '',
    images_json TEXT,
    properties_json TEXT,
    quantity_available INTEGER,
    seller_url TEXT,
    published_at TEXT,
    last_parsed_at TEXT,
    last_enriched_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    detail_enriched_at TEXT,
    source_profile_name TEXT,
    source_account_email TEXT,
    source_ggsel_user_id TEXT,
    reviews_good_count INTEGER,
    reviews_bad_count INTEGER,
    first_review_at TEXT,
    last_review_at TEXT,
    payment_methods TEXT,
    agency_fee REAL,
    options_count INTEGER,
    options_json TEXT,
    price_old REAL,
    price_usd REAL,
    price_eur REAL,
    from_gsellers INTEGER,
    is_noindex INTEGER,
    seller_registered_at TEXT,
    seller_attestat TEXT,
    local_image_path TEXT,
    shop_name TEXT,
    shop_rating REAL,
    shop_registered_at TEXT,
    shop_positive_reviews INTEGER,
    shop_negative_reviews INTEGER,
    shop_url TEXT,
    shop_products_count INTEGER,
    catalog_position INTEGER,
    catalog_page INTEGER
);
CREATE TABLE IF NOT EXISTS category_slugs (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE
);
"""


def _make_test_db(db_path: str) -> None:
    """Создаём тестовую БД с минимальной DDL."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(_SHOP_TEST_DDL)
    conn.commit()
    conn.close()


def _make_engine_stub():
    """ParserEngine без __init__ с минимальным stub-состоянием."""
    import threading as _th
    from parser.parser_engine import ParserEngine, _StopEvent
    eng = ParserEngine.__new__(ParserEngine)
    eng._lock = _th.Lock()
    eng._stop_event = _StopEvent()
    eng._current_run_id = None
    eng._stats = {}
    eng._is_running = False
    return eng


def test_worker_sets_catalog_position():
    """Воркер должен проставлять catalog_position и catalog_page по формуле (page-1)*100 + idx + 1."""
    from parser.parser_engine import (
        _full_scan_worker, _full_scan_state, _full_scan_lock
    )

    # Две страницы по 3 товара
    page1 = [_make_api_product(i) for i in range(1, 4)]
    page2 = [_make_api_product(i) for i in range(4, 7)]
    client = _make_mock_client([page1, page2, []])

    captured: list = []
    eng = MagicMock()

    def mock_save(batch, category, **kw):
        captured.extend([
            (p.external_id, p.catalog_page, p.catalog_position)
            for p in batch
        ])
        return [{"product_id": p.external_id} for p in batch]

    eng._save_batch.side_effect = mock_save

    with _full_scan_lock:
        _full_scan_state.update({
            "running": True, "stopped": False,
            "total_saved": 0, "total_found": 0,
            "ct_done": [], "ct_remaining": [2],
            "workers": [{"worker_id": 0, "account": "t", "ct_id": None,
                         "ct_name": None, "page": 0, "ct_done": [], "saved": 0}],
        })

    with patch("parser.parser_engine.get_engine", return_value=eng), \
         patch("parser.dedup.is_fresh", return_value=False), \
         patch("parser.dedup.is_rejected", return_value=False), \
         patch("parser.dedup.invalidate_name_cache"), \
         patch("parser.parser_engine.time") as mock_time:
        mock_time.sleep = MagicMock()
        _full_scan_worker(0, [2], client, "test_acc", False, "sortByRec")

    # Страница 1, limit=100: позиции 1, 2, 3
    assert captured[0] == ("1", 1, 1)
    assert captured[1] == ("2", 1, 2)
    assert captured[2] == ("3", 1, 3)
    # Страница 2, limit=100: позиции 101, 102, 103
    assert captured[3] == ("4", 2, 101)
    assert captured[4] == ("5", 2, 102)
    assert captured[5] == ("6", 2, 103)


def test_save_batch_fills_shop_fields():
    """_save_batch должен записывать shop_* из extra продавца."""
    import os, sqlite3
    from parser.parser_engine import Product

    p = Product()
    p.external_id = "shop_test_001"
    p.name = "Test Product"
    p.price = 200.0
    p.seller = "TopSeller"
    p.seller_id = "777"
    p.seller_rating = 4.9
    p.sales_count = 300
    p.in_stock = True
    p.rating = 4.7
    p.extra = {
        "seller_registered_at": "2022-05-10",
        "seller_attestat":      "verified",
        "seller_cnt_sell":      1500,
        "reviews_good_count":   100,
        "reviews_bad_count":    5,
        "price_usd":            2.5,
        "price_eur":            2.3,
    }

    db_path = str(ROOT / "data" / "db" / "_test_shop_fields.db")
    try:
        _make_test_db(db_path)
        with patch("parser.db_init.get_db_path", return_value=db_path), \
             patch("parser.pricing.calculate_my_price", return_value=240.0), \
             patch("parser.parser_engine._DOWNLOAD_IMAGES_ENABLED", False):
            eng = _make_engine_stub()
            eng._save_batch([p], category="Keys")

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """SELECT shop_name, shop_rating, shop_registered_at,
                      shop_positive_reviews, shop_negative_reviews,
                      shop_url, shop_products_count
               FROM parsed_products WHERE product_id = ?""",
            ("shop_test_001",)
        ).fetchone()
        conn.close()
    finally:
        try: Path(db_path).unlink(missing_ok=True)
        except Exception: pass

    assert row is not None, "Товар не сохранён в БД"
    shop_name, shop_rating, shop_reg, shop_pos, shop_neg, shop_url, shop_cnt = row
    assert shop_name == "TopSeller",                       f"shop_name={shop_name!r}"
    assert shop_rating == 4.9,                             f"shop_rating={shop_rating}"
    assert shop_reg == "2022-05-10",                       f"shop_registered_at={shop_reg!r}"
    assert shop_pos == 100,                                f"shop_positive_reviews={shop_pos}"
    assert shop_neg == 5,                                  f"shop_negative_reviews={shop_neg}"
    assert shop_url == "https://ggsel.net/en/seller/777",  f"shop_url={shop_url!r}"
    assert shop_cnt == 1500,                               f"shop_products_count={shop_cnt}"


def test_save_batch_shop_url_empty_when_no_seller_id():
    """Если seller_id пустой — shop_url должен быть None."""
    import os, sqlite3
    from parser.parser_engine import Product

    p = Product()
    p.external_id = "shop_test_no_seller"
    p.name = "No Seller Product"
    p.price = 100.0
    p.seller = "Unknown"
    p.seller_id = ""
    p.extra = {}

    db_path = str(ROOT / "data" / "db" / "_test_shop_noseller.db")
    try:
        _make_test_db(db_path)
        with patch("parser.db_init.get_db_path", return_value=db_path), \
             patch("parser.pricing.calculate_my_price", return_value=120.0), \
             patch("parser.parser_engine._DOWNLOAD_IMAGES_ENABLED", False):
            eng = _make_engine_stub()
            eng._save_batch([p], category="Keys")

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT shop_url FROM parsed_products WHERE product_id = ?",
            ("shop_test_no_seller",)
        ).fetchone()
        conn.close()
    finally:
        try: Path(db_path).unlink(missing_ok=True)
        except Exception: pass

    assert row is not None
    assert row[0] is None, f"shop_url должен быть None, получили {row[0]!r}"
