# -*- coding: utf-8 -*-
"""
GGselV7 — единая панель продавца GGSEL.
"""
import os

# httpx crashes on IPv6 NO_PROXY like "::1" in some versions
if "NO_PROXY" in os.environ and "::1" in os.environ["NO_PROXY"]:
    os.environ["NO_PROXY"] = os.environ["NO_PROXY"].replace("::1", "")
if "no_proxy" in os.environ and "::1" in os.environ["no_proxy"]:
    os.environ["no_proxy"] = os.environ["no_proxy"].replace("::1", "")

import time
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, render_template, jsonify, request, send_from_directory

try:
    from parser.db_init import get_db_path
except Exception:  # parser module not importable in some envs
    def get_db_path() -> str:
        return os.path.join("data", "db", "parser.db")

from config import (
    GGSEL_API_KEY,
    GGSEL_SELLER_ID,
    BASE_URL,
    HTTP_TIMEOUT,
    HTTP_RETRIES,
    LOCAL_PORT,
    WITHDRAWAL_FEE,
    TAX_PCT,
    FIXED_COSTS_RUB,
    ENABLED_CATEGORY_IDS,
)
_ai_select_cats = None  # pipeline_top100 removed


# ─── App & logging ───────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['BUILD_VERSION'] = str(int(time.time()))


@app.route("/api/test123", methods=["GET"])
def api_test123():
    print('[TEST123] called', flush=True)
    return jsonify({'ok': True, 'test': 123})

@app.context_processor
def inject_version():
    return dict(build_version=app.config['BUILD_VERSION'])
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ggselv7")

_ROOT_DIR = Path(__file__).resolve().parent


def _config_warnings() -> list[str]:
    warnings = []
    # Предупреждать только если WITHDRAWAL_FEE_PCT не задан в .env вообще (отсутствует ключ)
    if "WITHDRAWAL_FEE_PCT" not in os.environ:
        warnings.append(
            "WITHDRAWAL_FEE_PCT отсутствует в переменных окружения (.env) — проверьте настройки экономики"
        )
    return warnings


_startup_warnings = _config_warnings()
for _w in _startup_warnings:
    log.warning("ВНИМАНИЕ: %s", _w)


@app.context_processor
def inject_config_warnings():
    return dict(config_warnings=_config_warnings())


# ─── AUTH / LOW-LEVEL HELPERS ────────────────────────────────────────────────

def _v1_token():
    ts = str(int(time.time()))
    sign = hashlib.sha256((GGSEL_API_KEY + ts).encode()).hexdigest()
    payload = {"seller_id": GGSEL_SELLER_ID, "timestamp": ts, "sign": sign}
    try:
        r = requests.post(
            f"{BASE_URL}/api_sellers/api/apilogin",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        d = r.json()
        return d.get("token"), r.status_code, d
    except Exception as e:
        return None, 0, {"error": str(e)}


def _v1_get(path, params=None, headers=None, token=None):
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    p = {}
    if token is not False:
        if not token:
            token, _, _ = _v1_token()
        p["token"] = token
    if params:
        p.update(params)
    last_err = None
    for attempt in range(HTTP_RETRIES + 1):
        try:
            r = requests.get(f"{BASE_URL}{path}", params=p, headers=h, timeout=HTTP_TIMEOUT)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw_text": r.text[:500]}
        except requests.exceptions.Timeout as e:
            last_err = str(e)
            if attempt < HTTP_RETRIES:
                time.sleep(1)
        except Exception as e:
            return 0, {"error": str(e)}
    return 0, {"error": f"Timeout after {HTTP_RETRIES+1} attempts: {last_err}"}


def _v1_post(path, body=None, params=None, headers=None, token=None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    p = {}
    if token is not False:
        if not token:
            token, _, _ = _v1_token()
        p["token"] = token
    if params:
        p.update(params)
    try:
        r = requests.post(f"{BASE_URL}{path}", json=body or {}, params=p, headers=h, timeout=HTTP_TIMEOUT)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw_text": r.text[:500]}
    except Exception as e:
        return 0, {"error": str(e)}


def _v2_req(method, path, params=None, body=None, headers=None, ok_codes=(200, 201, 204)):
    h = {"Accept": "application/json", "Authorization": GGSEL_API_KEY}
    if method in ("POST", "PATCH", "PUT", "DELETE"):
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    try:
        r = requests.request(method, f"{BASE_URL}{path}",
                             params=params or {}, json=body,
                             headers=h, timeout=HTTP_TIMEOUT)
        try:
            d = r.json()
        except Exception:
            d = {"raw_text": r.text[:500]}
        return r.status_code, d, r.status_code in ok_codes
    except Exception as e:
        return 0, {"error": str(e)}, False


def v2_get(path, params=None, headers=None, ok_codes=(200,)):
    code, data, _ = _v2_req("GET", path, params=params, headers=headers, ok_codes=ok_codes)
    return code, data


def v2_get_with_ok(path, params=None, headers=None, ok_codes=(200,)):
    return _v2_req("GET", path, params=params, headers=headers, ok_codes=ok_codes)


def v2_post(path, body=None, headers=None, ok_codes=(200, 201, 204)):
    return _v2_req("POST", path, body=body, headers=headers, ok_codes=ok_codes)


def v2_patch(path, body=None, headers=None, ok_codes=(200,)):
    return _v2_req("PATCH", path, body=body, headers=headers, ok_codes=ok_codes)


def v2_delete(path, body=None, headers=None, ok_codes=(200,)):
    return _v2_req("DELETE", path, body=body, headers=headers, ok_codes=ok_codes)


# ─── NORMALIZERS / EXTRACTORS ────────────────────────────────────────────────

def _unwrap(d):
    if not isinstance(d, dict):
        return d
    return d.get("content") or d.get("data") or d


def extract_balance(bal):
    c = (bal or {}).get("content") or bal or {}
    return {
        "free":        c.get("amount_t_free"),
        "frozen_lock": c.get("amount_t_lock"),
        "plus":        c.get("amount_t_plus"),
        "currency":    "WMT",
    }


def extract_receipts_items(d):
    c = (d or {}).get("content") or d or {}
    return c.get("items") or []


def extract_offers(d):
    if not isinstance(d, dict):
        return [], {}
    return d.get("data") or [], d.get("pagination") or {}


def extract_categories_v2(d):
    if not isinstance(d, dict):
        return []
    return d.get("data") or []


def extract_chats(d):
    """Normalize chat list from GGSEL API (debates/v2/chats) into frontend-friendly format.
    
    Real GGSEL fields: id_i (chat id), email (buyer email), cnt_new (unread count),
    last_message (datetime string of last msg), product (product id).
    """
    if not isinstance(d, dict):
        return []
    raw_items = d.get("items") or []
    result = []
    for chat in raw_items:
        if not isinstance(chat, dict):
            continue
        # GGSEL uses id_i as chat ID (not id)
        chat_id = chat.get("id_i") or chat.get("id") or chat.get("chat_id")
        # Buyer name: GGSEL provides email
        buyer = chat.get("email") or chat.get("buyer") or chat.get("userName") or "Покупатель"
        # last_message in GGSEL is a datetime string, not message text
        last_msg_date = chat.get("last_message") or chat.get("updatedAt") or chat.get("updated_at")
        # Unread count
        unread = chat.get("cnt_new") or chat.get("unread") or chat.get("unreadCount") or 0
        result.append({
            "id":           chat_id,
            "order_id":     chat.get("order_id") or chat.get("invoice_id") or chat.get("product"),
            "buyer":        buyer,
            "last_message_date": last_msg_date,
            "last_message": "",   # text not provided in list, fetched separately
            "product_id":   chat.get("product"),
            "unread":       int(unread) if unread else 0,
            "updated_at":   last_msg_date,
        })
    # Filter out chats without a valid ID (GGSEL sometimes returns null id_i entries)
    return [c for c in result if c["id"] is not None]


def extract_reviews(d):
    if not isinstance(d, dict):
        return [], {}
    return d.get("reviews") or [], {
        "total_pages": d.get("totalPages"),
        "total_items": d.get("totalItems"),
        "total_good":  d.get("totalGood"),
        "total_bad":   d.get("totalBad"),
    }


def extract_sales(d):
    if not isinstance(d, dict):
        return []
    return d.get("sales") or d.get("data") or d.get("items") or []


def normalize_sale(s):
    """Normalize a GGSEL sale item (from seller-last-sales) to a flat dict for frontend."""
    p = s.get("product") or {}
    name = p.get("name") or "—"
    # name may be a list of {value, locale} objects
    if isinstance(name, list):
        for entry in name:
            if isinstance(entry, dict) and entry.get("locale", "").startswith("ru"):
                name = entry.get("value", "—")
                break
        if isinstance(name, list):
            name = (name[0].get("value") if name and isinstance(name[0], dict) else "—") or "—"
    return {
        "invoice_id":  s.get("invoice_id"),
        "date":        s.get("date"),
        "item_name":   str(name),
        "price_rub":   p.get("price_rub"),
        "price_usd":   p.get("price_usd"),
        "price_eur":   p.get("price_eur"),
        "price_uah":   p.get("price_uah"),
        "product_id":  p.get("id"),
        "product":     p,
        "status":      s.get("status") or "paid",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  GUI PAGES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    # Параметры экономики для отображения в шапке Парсера
    import os as _os_e
    _econ = {
        "round_to":             int(_os_e.getenv("ROUND_TO", "1")),
        "target_margin_pct":    float(_os_e.getenv("TARGET_MARGIN_PCT", "0.15")),
        "min_net_profit_rub":   float(_os_e.getenv("MIN_NET_PROFIT_RUB", "50.0")),
        "payment_fee_pct":      float(_os_e.getenv("PAYMENT_FEE_PCT", "0.027")),
        "withdrawal_fee_pct":   float(_os_e.getenv("WITHDRAWAL_FEE_PCT", "0.02")),
        "tax_pct":              float(_os_e.getenv("TAX_PCT", "0.0")),
        "risk_reserve_pct":     float(_os_e.getenv("RISK_RESERVE_PCT", "0.05")),
        "fixed_costs_rub":      float(_os_e.getenv("FIXED_COSTS_RUB", "0.0")),
    }
    _round_label = "1 ₽" if _econ["round_to"] == 1 else ("0.01 ₽" if _econ["round_to"] == 2 else f"{10**(-_econ['round_to']):g} ₽")
    return render_template(
        "index.html",
        seller_id=GGSEL_SELLER_ID,
        api_key_preview=GGSEL_API_KEY[:8] + "…" + GGSEL_API_KEY[-4:],
        api_key_full=GGSEL_API_KEY,
        base_url=BASE_URL,
        econ=_econ,
        econ_round_label=_round_label,
    )


@app.route("/static/<path:fname>")
def static_files(fname):
    return send_from_directory(app.static_folder, fname)


# ═══════════════════════════════════════════════════════════════════════════
#  API: DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════


@app.route("/api/notifications")
def api_notifications():
    try:
        data = _cookie_get("https://seller.ggsel.com/api/v1/account/notifications")
        return jsonify({"ok": True, "items": data})
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "items": []})

@app.route("/api/whitelisted_ips")
def api_whitelisted_ips():
    try:
        data = _cookie_get("https://seller.ggsel.com/api/v1/account/whitelisted_ips")
        return jsonify({"ok": True, "items": data})
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "items": []})

@app.route("/api/dashboard")
def api_dashboard():
    code, balance = _v1_get("/api_sellers/api/sellers/account/balance/info")
    code, sales   = _v1_get("/api_sellers/api/seller-last-sales",
                            params={"seller_id": GGSEL_SELLER_ID, "top": 20},
                            headers={"locale": "ru"})
    code, offers  = v2_get("/api_sellers/v2/offers",
                            params={"page": 1, "limit": 20},
                            headers={"locale": "ru"})
    code, reviews = _v1_get("/api_sellers/api/reviews",
                            params={"page": 1, "count": 10, "type": "all"},
                            headers={"locale": "ru-RU"})
    code, chats   = _v1_get("/api_sellers/api/debates/v2/chats",
                            params={"page": 1, "pagesize": 10})
    code, receipts = _v1_get("/api_sellers/api/sellers/account/receipts",
                             params={"page": 1, "count": 10})
    code, categories = v2_get("/api_sellers/v2/categories",
                               params={"page": 1, "limit": 10},
                               headers={"locale": "ru"})

    offers_list, pagination = extract_offers(offers)
    reviews_list, review_stats = extract_reviews(reviews)

    return jsonify({
        "balance":        extract_balance(balance),
        "balance_raw":    balance,
        "sales":          [normalize_sale(s) for s in extract_sales(sales)],
        "sales_raw":      sales,
        "offers":         offers_list,
        "offers_pagination": pagination,
        "offers_raw":     offers,
        "reviews":        reviews_list,
        "reviews_stats":  review_stats,
        "reviews_raw":    reviews,
        "chats":          extract_chats(chats),
        "chats_raw":      chats,
        "receipts":       extract_receipts_items(receipts),
        "receipts_raw":   receipts,
        "categories":     extract_categories_v2(categories),
        "categories_raw": categories,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  API: COOKIES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/cookie/status")
def api_cookie_status():
    """Cтатус куков продавца (Seller MSB сессия)."""
    try:
        from cookie_status_routes import _cookie_info
        info = _cookie_info()
        cookies_dict = {}
        try:
            import json as _j
            from cookie_status_routes import _get_cookies_path
            p = _get_cookies_path()
            if p.exists():
                raw = _j.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cookies_dict = raw
                elif isinstance(raw, list):
                    cookies_dict = {c["name"]: c.get("value", "") for c in raw if isinstance(c, dict) and "name" in c}
        except Exception:
            pass
        return jsonify({
            "exists":     info.get("exists", False),
            "has_qrator": info.get("has_qrator", False),
            "fresh":      info.get("fresh", False),
            "age_seconds": info.get("age_sec"),
            "count":      info.get("count", 0),
            "cookies":    cookies_dict,
        })
    except Exception as e:
        return jsonify({
            "exists": False, "has_qrator": False, "fresh": False,
            "age_seconds": None, "count": 0, "cookies": {}, "error": str(e),
        })


# Путь для куков личного кабинета продавца (отдельно от парсера)
_SELLER_COOKIES_PATH = Path(__file__).parent / "data" / "seller_cookies.json"


@app.route("/api/cookie/open-browser", methods=["POST"])
def api_cookie_open_browser():
    """
    Открывает браузер MSB из группы SellerGGsel (видимый режим, launchMode=visible).
    Пользователь вручную навигирует в seller.ggsel.com, куки снимаются через CDP и сохраняются.
    launchMode visible — намеренно: человек должен видеть браузер и может взаимодействовать. Qrator-безопасно.
    """
    import threading as _threading
    result = {}

    def _run():
        import asyncio as _aio

        async def _do():
            from parser.msb_client import MsbClient

            async with MsbClient() as cl:
                # 1. Найти профиль из группы SellerGGsel
                _SELLER_GROUP_NAMES = {"sellerggsel", "seller ggsel", "seller", "sellerggsel1"}
                groups = await cl.get_groups()
                seller_profile_ids = []
                for g in groups:
                    if (g.get("name") or "").lower().replace(" ", "") in {
                        n.replace(" ", "") for n in _SELLER_GROUP_NAMES
                    }:
                        seller_profile_ids = [str(x) for x in (g.get("profileIds") or [])]
                        break

                if not seller_profile_ids:
                    return False, "Группа SellerGGsel не найдена в MSB", False

                profile_id = seller_profile_ids[0]

                # 2. Запустить браузер в видимом режиме (окно открывается на экране — пользователь должен видеть)
                await cl.start_profile(profile_id, launchMode="visible")
                await _aio.sleep(3)  # дать браузеру открыться

                def _filter_cookies(raw: dict) -> dict:
                    """Оставляем только ggsel/Qrator куки."""
                    return {
                        k: v for k, v in raw.items()
                        if any(x in k.lower() for x in
                               ("ggsel", "qrator", "session", "auth", "token"))
                        or any(x in (v or "").lower() for x in ("ggsel",))
                    }

                # 3a. Главная страница ggsel.net — профиль авторизован, Qrator выдаёт qrator_ssid
                await cl.goto(profile_id, "https://ggsel.net")
                await _aio.sleep(8)
                # Переходим на личную страницу продавца — Qrator подтверждает авторизованную сессию
                await cl.goto(profile_id, f"https://ggsel.net/seller/{GGSEL_SELLER_ID}")
                await _aio.sleep(12)  # ждём Qrator JS-задачу + сессию на ggsel.net

                # Снимаем куки ggsel.net ДО перехода на seller (иначе могут перезаписаться)
                cookies_ggsel = _filter_cookies(await cl.get_cookies(profile_id))

                # 3b. Второй Qrator-челлендж: личный кабинет продавца
                await cl.goto(profile_id, "https://seller.ggsel.com")
                await _aio.sleep(15)  # ждём загрузки + Qrator/сессия куки

                # 4. Снять куки seller.ggsel.com
                cookies_seller = _filter_cookies(await cl.get_cookies(profile_id))

                if not cookies_seller and not cookies_ggsel:
                    return False, "Куки не получены — страница не загрузилась?", False

                # Мерджим: базис — seller (там ACCESS_TOKEN и qrator_ssid2),
                # поверх — ggsel.net куки (там qrator_ssid), чтобы не перезаписать
                cookies = {**cookies_seller, **cookies_ggsel}

                if not cookies:
                    return False, "Куки ggsel/Qrator не найдены в профиле", False

                # 5. Сохраняем в файл продавца (отдельно от парсера)
                _SELLER_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = _SELLER_COOKIES_PATH.with_suffix(".tmp")
                tmp.write_text(
                    __import__("json").dumps(cookies, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(_SELLER_COOKIES_PATH)

                # Qrator check
                qrator_keys = {"qrator_msid2", "qrator_jsid", "qrator_ssid", "qrator_ssid2", "qrator_jsr", "__qrator_jsid"}
                has_qrator = bool(qrator_keys & set(cookies.keys()))
                return True, len(cookies), has_qrator

        try:
            ok, count, valid = _aio.run(_do())
            result["ok"] = ok
            result["count"] = count
            result["valid"] = valid
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)[:300]

    t = _threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)

    if "ok" not in result:
        return jsonify({"ok": False, "error": "timeout_90s — браузер не ответил"}), 504
    if not result["ok"]:
        return jsonify({"ok": False, "error": result.get("error") or result.get("count", "unknown")}), 500

    count = result["count"]
    valid = result["valid"]
    return jsonify({
        "ok": True,
        "count": count,
        "has_qrator": valid,
        "msg": f"Сохранено {count} куков, Qrator: {'OK' if valid else 'не найден'}",
    })


@app.route("/api/cookie/refresh", methods=["POST"])
def api_cookie_refresh():
    import asyncio
    from parser.msb_cookies import QratorCookieMiddleware, validate_qrator_cookies
    try:
        async def refresh():
            async with QratorCookieMiddleware() as mw:
                return await mw.cookies(force_refresh=True)
        cookies = asyncio.run(refresh())
        if cookies and validate_qrator_cookies(cookies):
            return jsonify({"ok": True, "cookies": cookies})
        else:
            return jsonify({"ok": False, "error": "Куки не получены или не валидны"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════
#  API: CATEGORIES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/categories/v2")
def api_categories_v2():
    page  = 1
    limit = 100
    all_categories = []
    
    # Загружаем все страницы в цикле пока есть данные
    while True:
        params = {"page": page, "limit": limit}
        code, data = v2_get("/api_sellers/v2/categories", params=params, headers={"locale": "ru"})
        if code != 200:
            break
        
        # GGSEL V2 API возвращает список в поле "data" или прямо в корне
        items = data.get("data") if isinstance(data, dict) else None
        if not items and isinstance(data, list):
            items = data
        
        if not items:
            break
            
        all_categories.extend(items)
        if len(items) < limit:
            break
        page += 1

    # Сохраняем результат в categories_cache.json с временной меткой
    cache_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "categories": all_categories
    }
    
    from config import CATEGORIES_CACHE_PATH
    try:
        with open(CATEGORIES_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("Не удалось записать categories_cache.json: %s", e)

    return jsonify({"status_code": 200, "count": len(all_categories), "data": all_categories})



@app.route("/api/categories/v2/tree")
def api_categories_v2_tree():
    """
    Возвращает дерево категорий из локальной БД (таблица categories).
    Кешируется в памяти на 10 минут.
    """
    import time as _time
    from parser.db_init import get_db_path
    import sqlite3

    cache    = getattr(api_categories_v2_tree, '_cache', None)
    cache_ts = getattr(api_categories_v2_tree, '_cache_ts', 0)
    if cache is not None and (_time.time() - cache_ts) < 600:
        return jsonify({"ok": True, "items": cache, "cached": True})

    try:
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, parent_id, depth, full_path, content_type, fee, has_children
            FROM categories
            ORDER BY depth, title
        """)
        items = []
        for row in cur.fetchall():
            items.append({
                "id": row[0],
                "title": row[1],
                "parent_id": row[2],
                "depth": row[3],
                "full_path": row[4],
                "content_type": row[5],
                "fee": row[6],
                "has_children": bool(row[7]),
            })
        conn.close()
        
        # Фоллбэк: если БД пуста, читать из categories_cache.json
        if not items:
            from config import CATEGORIES_CACHE_PATH
            import os
            if os.path.exists(CATEGORIES_CACHE_PATH):
                try:
                    with open(CATEGORIES_CACHE_PATH, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
                    cached_list = cache_data.get("categories", [])
                    for c in cached_list:
                        items.append({
                            "id": c.get("id"),
                            "title": c.get("title"),
                            "parent_id": c.get("parent_id"),
                            "depth": c.get("depth", 0),
                            "full_path": c.get("full_path") or c.get("title"),
                            "content_type": c.get("content_type"),
                            "fee": c.get("fee"),
                            "has_children": bool(c.get("has_children")),
                        })
                except Exception as ex:
                    log.warning("Не удалось прочитать categories_cache.json во время фоллбэка: %s", ex)
        
        api_categories_v2_tree._cache    = items
        api_categories_v2_tree._cache_ts = _time.time()
        return jsonify({"ok": True, "items": items, "cached": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/categories/reload_fees", methods=["POST"])
def api_reload_fees():
    try:
        from parser.pricing import reload_fees

        count = reload_fees()
        return jsonify({"ok": True, "loaded": count})
    except Exception as e:
        log.exception("Fee reload failed")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/categories/selected", methods=["GET", "POST"])
def api_categories_selected():
    path = os.path.join("data", "selected_categories.json")
    os.makedirs("data", exist_ok=True)
    if request.method == "POST":
        try:
            body = request.get_json(silent=True) or []
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            return jsonify({"ok": True, "selected": body})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    else:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return jsonify({"ok": True, "selected": data})
            except Exception:
                pass
        return jsonify({"ok": True, "selected": [47488, 34063, 34066, 34072, 35526]})

@app.route("/api/categories/v1")
def api_categories_v1():
    page  = request.args.get("page",  1, type=int)
    count = request.args.get("count", 100, type=int)
    code, data = _v1_get("/api_sellers/api/categories",
                          params={"page": page, "count": count},
                          headers={"lang": "ru-RU"})
    items = (data or {}).get("category", []) if isinstance(data, dict) else []
    return jsonify({"status_code": code, "raw": data, "items": items})


# ═══════════════════════════════════════════════════════════════════════════
#  API: OFFERS / GOODS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/offers")
def api_offers():
    page   = request.args.get("page",  1,  type=int)
    limit  = request.args.get("limit", 50, type=int)
    status = request.args.get("status", "")
    params = {"page": page, "limit": limit}
    if status:
        params["status"] = status
    code, data = v2_get("/api_sellers/v2/offers", params=params, headers={"locale": "ru"})
    items, pagination = extract_offers(data)
    return jsonify({"status_code": code, "data": data, "items": items, "pagination": pagination})


@app.route("/api/offer/<int:offer_id>")
def api_offer_get(offer_id):
    code, offer = v2_get(f"/api_sellers/v2/offers/{offer_id}", headers={"locale": "ru"})
    code, opts  = v2_get(f"/api_sellers/v2/offers/{offer_id}/options", headers={"locale": "ru"})
    code, prods = v2_get(f"/api_sellers/v2/offers/{offer_id}/products",
                          params={"status": "in_stock", "limit": 100},
                          headers={"locale": "ru"})
    splitted = {}
    opts_list = (opts.get("data") or []) if isinstance(opts, dict) else []
    for opt in opts_list:
        if opt.get("has_splitted_products"):
            for v in (opt.get("variants") or []):
                vid = v.get("id")
                if vid:
                    code, sp = v2_get(
                        f"/api_sellers/v2/offers/{offer_id}/variants/{vid}/splitted_products",
                        params={"status": "in_stock", "limit": 100},
                        headers={"locale": "ru"},
                    )
                    splitted[str(vid)] = sp
    return jsonify({
        "offer":              offer,
        "options":            opts,
        "products":           prods,
        "splitted_products":  splitted,
    })


@app.route("/api/offer/<int:offer_id>/update", methods=["PATCH"])
def api_offer_update(offer_id):
    body = request.get_json() or {}
    allowed = {k: v for k, v in body.items()
               if k in ("price", "description_ru", "description_en",
                        "title_ru", "title_en", "quantity")}
    code, data, ok = v2_patch(f"/api_sellers/v2/offers/{offer_id}",
                               body=allowed, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/offers/batch_activate", methods=["POST"])
def api_offers_batch_activate():
    ids = (request.get_json() or {}).get("offer_ids", [])
    code, data, ok = v2_post("/api_sellers/v2/offers/batch_activate",
                              body={"offer_ids": ids}, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/offers/batch_pause", methods=["POST"])
def api_offers_batch_pause():
    ids = (request.get_json() or {}).get("offer_ids", [])
    code, data, ok = v2_post("/api_sellers/v2/offers/batch_pause",
                              body={"offer_ids": ids}, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/offers/batch_delete", methods=["POST"])
def api_offers_batch_delete():
    ids = (request.get_json() or {}).get("offer_ids", [])
    code, data, ok = v2_post("/api_sellers/v2/offers/batch_delete",
                              body={"offer_ids": ids}, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/offer/<int:offer_id>/variant/<int:variant_id>/products", methods=["POST"])
def api_variant_add_products(offer_id, variant_id):
    body  = request.get_json() or {}
    values = body.get("values", [])
    products = [{"value": v.strip()} for v in values if v and v.strip()]
    if not products:
        return jsonify({"status_code": 0, "ok": False, "raw": {"error": "Пустой список ключей"}})
    code, data, ok = v2_post(
        f"/api_sellers/v2/offers/{offer_id}/variants/{variant_id}/splitted_products",
        body={"products": products}, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/offer/<int:offer_id>/products", methods=["POST"])
def api_offer_add_products(offer_id):
    body  = request.get_json() or {}
    values = body.get("values", [])
    products = [{"value": v.strip()} for v in values if v and v.strip()]
    if not products:
        return jsonify({"status_code": 0, "ok": False, "raw": {"error": "Пустой список ключей"}})
    code, data, ok = v2_post(f"/api_sellers/v2/offers/{offer_id}/products",
                              body={"products": products}, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/seller_goods")
def api_seller_goods():
    page = request.args.get("page", 1, type=int)
    code, data = _v1_post("/api_sellers/api/seller-goods",
                           body={"id_seller": GGSEL_SELLER_ID, "order_col": "cntsell",
                                 "order_dir": "desc", "rows": 30, "page": page,
                                 "currency": "RUB", "lang": "ru-RU", "show_hidden": 0})
    return jsonify({"status_code": code, "raw": data})


@app.route("/api/products_v1")
def api_products_v1():
    page = request.args.get("page", 1, type=int)
    code, data = _v1_get("/api_sellers/api/products/list",
                          params={"page": page, "count": 30},
                          headers={"lang": "ru-RU"})
    return jsonify({"status_code": code, "raw": data})


# ═══════════════════════════════════════════════════════════════════════════
#  API: SALES / ORDERS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/sales")
def api_sales():
    top = request.args.get("top", 50, type=int)
    code, data = _v1_get("/api_sellers/api/seller-last-sales",
                          params={"seller_id": GGSEL_SELLER_ID, "top": top},
                          headers={"locale": "ru"})
    raw_sales = extract_sales(data)
    normalized = [normalize_sale(s) for s in raw_sales]

    try:
        from parser.order_processor import process_sales
        process_sales(raw_sales)
    except Exception as e:
        log.warning(f"Error matching sales: {e}")

    return jsonify({"status_code": code, "raw": data, "items": normalized})


@app.route("/api/order/<int:invoice_id>")
def api_order(invoice_id):
    code, data = _v1_get(f"/api_sellers/api/purchase/info/{invoice_id}",
                          headers={"locale": "ru"})
    return jsonify({"status_code": code, "raw": data})


@app.route("/api/orders/linked")
def api_orders_linked():
    import sqlite3
    from parser.db_init import get_db_path
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT o.*, p.title, p.url as original_url
            FROM order_links o
            LEFT JOIN parsed_products p ON o.source_offer_id = p.product_id
            ORDER BY o.created_at DESC
            """
        ).fetchall()
        orders = []
        for r in rows:
            orders.append({
                "order_id": r["order_id"],
                "my_offer_id": r["my_offer_id"],
                "source_offer_id": r["source_offer_id"],
                "source_seller_id": r["source_seller_id"],
                "source_price": r["source_price"],
                "my_price": r["my_price"],
                "profit_rub": r["profit_rub"],
                "status": r["status"],
                "created_at": r["created_at"],
                "title": r["title"] or "Неизвестный товар",
                "original_url": r["original_url"] or f"https://ggsel.net/goods/{r['source_offer_id']}"
            })
        return jsonify({"ok": True, "orders": orders})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/orders/<order_id>/link")
def api_order_link(order_id):
    import sqlite3
    from parser.db_init import get_db_path
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            """
            SELECT o.*, p.title, p.url as original_url
            FROM order_links o
            LEFT JOIN parsed_products p ON o.source_offer_id = p.product_id
            WHERE o.order_id = ?
            """,
            (order_id,)
        ).fetchone()
        if not r:
            return jsonify({"ok": False, "error": "Link not found"}), 404
        return jsonify({
            "ok": True,
            "link": {
                "order_id": r["order_id"],
                "my_offer_id": r["my_offer_id"],
                "source_offer_id": r["source_offer_id"],
                "source_seller_id": r["source_seller_id"],
                "source_price": r["source_price"],
                "my_price": r["my_price"],
                "profit_rub": r["profit_rub"],
                "status": r["status"],
                "created_at": r["created_at"],
                "title": r["title"] or "Неизвестный товар",
                "original_url": r["original_url"] or f"https://ggsel.net/goods/{r['source_offer_id']}"
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/orders/<order_id>/mark_buying", methods=["POST"])
def api_order_mark_buying(order_id):
    import sqlite3
    from parser.db_init import get_db_path
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        conn.execute(
            "UPDATE order_links SET status = 'buying', updated_at = datetime('now') WHERE order_id = ?",
            (order_id,)
        )
        conn.commit()
        return jsonify({"ok": True, "order_id": order_id, "status": "buying"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/orders/<order_id>/mark_done", methods=["POST"])
def api_order_mark_done(order_id):
    import sqlite3
    from parser.db_init import get_db_path
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        conn.execute(
            "UPDATE order_links SET status = 'done', updated_at = datetime('now') WHERE order_id = ?",
            (order_id,)
        )
        conn.commit()
        return jsonify({"ok": True, "order_id": order_id, "status": "done"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  API: REVIEWS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/reviews")
def api_reviews():
    page  = request.args.get("page", 1,  type=int)
    count = request.args.get("count", 30, type=int)
    rtype = request.args.get("type", "all")
    code, data = _v1_get("/api_sellers/api/reviews",
                          params={"page": page, "count": count, "type": rtype},
                          headers={"locale": "ru-RU"})
    items, stats = extract_reviews(data)
    return jsonify({"status_code": code, "raw": data, "items": items, "stats": stats})


# ═══════════════════════════════════════════════════════════════════════════
#  API: CHATS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/chats")
def api_chats():
    page     = request.args.get("page", 1,  type=int)
    pagesize = request.args.get("pagesize", 30, type=int)
    code, data = _v1_get("/api_sellers/api/debates/v2/chats",
                          params={"page": page, "pagesize": pagesize})
    items = extract_chats(data)
    has_new = any((i.get("unread") or 0) > 0 for i in items)
    return jsonify({"status_code": code, "raw": data, "items": items, "has_new_messages": has_new})


@app.route("/api/chat/<int:chat_id>/messages")
def api_chat_messages(chat_id):
    count = request.args.get("count", 50, type=int)
    code, data = _v1_get("/api_sellers/api/debates/v2",
                          params={"id_i": chat_id, "count": count})
    # Normalize messages to a consistent format for the frontend
    raw_messages = []
    if isinstance(data, dict):
        raw_messages = (
            data.get("messages") or
            data.get("items") or
            data.get("data") or
            []
        )
    elif isinstance(data, list):
        raw_messages = data
    normalized = []
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        # GGSEL message fields: seller=1/0, buyer=1/0, date_written, message
        is_seller = bool(
            m.get("seller") == 1 or
            m.get("is_seller") or
            m.get("isSeller") or
            m.get("sender") == "seller" or
            m.get("from") == "seller"
        )
        normalized.append({
            "id":        m.get("id") or m.get("message_id"),
            "text":      m.get("message") or m.get("text") or m.get("body") or "",
            "is_seller": is_seller,
            "date":      m.get("date_written") or m.get("date") or m.get("created_at") or m.get("createdAt"),
        })
    return jsonify({"status_code": code, "items": normalized, "raw": data})


@app.route("/api/chat/<int:chat_id>/send", methods=["POST"])
def api_chat_send(chat_id):
    message = ((request.get_json() or {}).get("message") or "").strip()
    if not message:
        return jsonify({"status_code": 0, "ok": False, "raw": {"error": "Пустое сообщение"}})
    code, data = _v1_post("/api_sellers/api/debates/v2",
                           body={"message": message},
                           params={"id_i": chat_id})
    return jsonify({"status_code": code, "ok": code == 200, "raw": data})


@app.route("/api/chats/my")
def api_chats_my():
    return api_chats()


@app.route("/api/chats/my/<int:chat_id>")
def api_chats_my_detail(chat_id):
    return api_chat_messages(chat_id)


@app.route("/api/chats/my/<int:chat_id>/send", methods=["POST"])
def api_chats_my_send(chat_id):
    return api_chat_send(chat_id)


@app.route("/api/chats/my/order/<order_id>")
def api_chats_my_order(order_id):
    """Find chat by order_id: look up the product_id from order, then find chat by product."""
    # First get order info to find product id
    code, order_data = _v1_get(f"/api_sellers/api/purchase/info/{order_id}",
                                headers={"locale": "ru"})
    product_id = None
    if code == 200 and isinstance(order_data, dict):
        content = order_data.get("content") or {}
        if isinstance(content, dict):
            product_id = content.get("unit_goods") or content.get("content_id")

    # Fetch chats and find by product_id or just return all chats for this order
    code, data = _v1_get("/api_sellers/api/debates/v2/chats", params={"page": 1, "pagesize": 100})
    if code != 200:
        return jsonify({"ok": False, "error": "Failed to fetch chats"}), code

    chats = (data.get("items") or []) if isinstance(data, dict) else []
    target_chat = None
    for chat in chats:
        chat_product = chat.get("product")
        if product_id and chat_product and str(chat_product) == str(product_id):
            target_chat = chat
            break

    if not target_chat:
        return jsonify({"ok": False, "error": "No chat found for this order", "order_id": order_id, "product_id": product_id}), 404

    chat_id = target_chat.get("id_i")
    if not chat_id:
        return jsonify({"ok": False, "error": "Chat has no id_i"}), 404

    msg_code, msg_data = _v1_get("/api_sellers/api/debates/v2", params={"id_i": chat_id, "count": 50})
    raw_messages = []
    if isinstance(msg_data, dict):
        raw_messages = msg_data.get("messages") or msg_data.get("items") or msg_data.get("data") or []
    elif isinstance(msg_data, list):
        raw_messages = msg_data

    normalized_msgs = []
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        is_seller = bool(m.get("seller") == 1 or m.get("is_seller") or m.get("isSeller"))
        normalized_msgs.append({
            "id":        m.get("id") or m.get("message_id"),
            "text":      m.get("message") or m.get("text") or m.get("body") or "",
            "is_seller": is_seller,
            "date":      m.get("date_written") or m.get("date") or m.get("created_at") or m.get("createdAt"),
        })

    return jsonify({
        "ok": True,
        "chat_id": chat_id,
        "chat": target_chat,
        "messages": normalized_msgs,
        "_raw_messages": msg_data,
    })


@app.route("/api/chats/debug")
def api_chats_debug():
    """Debug endpoint: show raw GGSEL API response for chats and first chat messages."""
    code, data = _v1_get("/api_sellers/api/debates/v2/chats", params={"page": 1, "pagesize": 5})
    first_chat_data = None
    chats = (data.get("items") or []) if isinstance(data, dict) else []
    if chats:
        first_id = chats[0].get("id_i")
        if first_id:
            _, first_chat_data = _v1_get("/api_sellers/api/debates/v2", params={"id_i": first_id, "count": 5})
    return jsonify({
        "chats_response": data,
        "first_chat_messages_response": first_chat_data,
        "extracted_chats": extract_chats(data),
    })


@app.route("/api/chats/source/<order_id>")
def api_chats_source(order_id):
    import sqlite3
    from parser.db_init import get_db_path
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        row = conn.execute(
            "SELECT source_offer_id, source_seller_id, my_offer_id FROM order_links WHERE order_id = ?",
            (order_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Order link not found in database"}), 404
        source_offer_id, source_seller_id, my_offer_id = row
        title = ""
        p_row = conn.execute("SELECT title FROM parsed_products WHERE product_id = ?", (source_offer_id,)).fetchone()
        if p_row:
            title = p_row[0]
        
        seller_url = f"https://ggsel.net/sellers/{source_seller_id}" if source_seller_id else ""
        product_url = f"https://ggsel.net/goods/{source_offer_id}" if source_offer_id else ""
        
        return jsonify({
            "ok": True,
            "order_id": order_id,
            "source_offer_id": source_offer_id,
            "source_seller_id": source_seller_id,
            "seller_url": seller_url,
            "product_url": product_url,
            "title": title,
            "message_template": f"Хочу купить {title or '[название товара]'}, вариант: по умолчанию, количество: 1"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  API: FINANCE
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/balance")
def api_balance():
    code, data = _v1_get("/api_sellers/api/sellers/account/balance/info")
    return jsonify({"status_code": code, "raw": data, "extracted": extract_balance(data)})


@app.route("/api/receipts")
def api_receipts():
    page  = request.args.get("page", 1,  type=int)
    count = request.args.get("count", 30, type=int)
    code, data = _v1_get("/api_sellers/api/sellers/account/receipts",
                          params={"page": page, "count": count})
    return jsonify({"status_code": code, "raw": data, "items": extract_receipts_items(data)})


# ═══════════════════════════════════════════════════════════════════════════
#  API: SETTINGS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/save_config", methods=["POST"])
def save_config():
    import importlib
    data = request.get_json() or {}
    new_key = (data.get("api_key") or "").strip()
    new_sid = (data.get("seller_id") or "").strip()
    if not new_key or not new_sid:
        return jsonify({"ok": False, "error": "Заполни оба поля"})
    if not new_sid.isdigit():
        return jsonify({"ok": False, "error": "Seller ID должен быть числом"})

    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    content = (
        "# ===== GGSEL API Настройки =====\n"
        f'GGSEL_API_KEY   = "{new_key}"\n'
        f'GGSEL_SELLER_ID = {new_sid}\n\n'
        'BASE_URL = "https://seller.ggsel.com"\n'
        f'\nHTTP_TIMEOUT = {HTTP_TIMEOUT}\n'
        f'HTTP_RETRIES = {HTTP_RETRIES}\n'
        f'LOCAL_PORT   = {LOCAL_PORT}\n'
    )
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    import config as cfg_module
    importlib.reload(cfg_module)
    global GGSEL_API_KEY, GGSEL_SELLER_ID
    GGSEL_API_KEY = cfg_module.GGSEL_API_KEY
    GGSEL_SELLER_ID = cfg_module.GGSEL_SELLER_ID
    return jsonify({"ok": True,
                    "api_key_preview": new_key[:8] + "…" + new_key[-4:],
                    "seller_id": new_sid})


@app.route("/api/pipeline/log")
def api_pipeline_log():
    """Отдаёт последние 50 строк из data/pipeline.log для мониторинга прогресса."""
    log_path = os.path.join(os.path.dirname(__file__), "logs", "pipeline.log")
    lines = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Берём последние 50 строк
        last_lines = lines[-50:] if len(lines) > 50 else lines
        return jsonify({"ok": True, "lines": last_lines, "total": len(lines)})
    except FileNotFoundError:
        return jsonify({"ok": True, "lines": [], "total": 0, "message": "Лог файл не найден"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/parsed-products")
def api_parsed_products():
    """Отдаёт спарсенные товары из БД parsed_products."""
    import sqlite3
    from pathlib import Path

    status_filter = request.args.get("status")
    is_top_filter = request.args.get("is_top")

    db_path = Path(__file__).resolve().parent / "data" / "db" / "parser.db"
    if not db_path.exists():
        return jsonify({"ok": True, "items": [], "total": 0})

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
            SELECT p.product_id, p.title, p.source_price, p.sell_price,
                   p.my_price, p.expected_profit_rub, p.expected_net_margin_pct,
                   p.category_id, p.category, p.breadcrumb,
                   p.status, p.is_top, p.image_url, p.url,
                   p.seller_name, p.sales_count, p.rating,
                   p.ggsel_fee_pct, p.payment_fee_pct,
                   p.expected_profit_rub, p.risk_level,
                   p.approval_status, p.offer_id,
                   p.created_at, p.updated_at,
                   sc.title as category_title,
                   sc.path as category_path
            FROM parsed_products p
            LEFT JOIN seller_categories sc ON sc.id = p.category_id
            WHERE 1=1
        """
        params = []

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        if is_top_filter is not None:
            query += " AND is_top = ?"
            params.append(1 if is_top_filter in ("1", "true", "True") else 0)

        query += " ORDER BY created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append({
                "product_id":             row["product_id"],
                "title":                  row["title"],
                "source_price":           row["source_price"],
                "sell_price":             row["sell_price"],
                "my_price":               row["my_price"],
                "expected_profit_rub":    row["expected_profit_rub"],
                "expected_net_margin_pct":row["expected_net_margin_pct"],
                "category_id":            row["category_id"],
                "category":               row["category"],
                "breadcrumb":             row["breadcrumb"],
                "category_title":         row["category_title"],
                "category_path":          row["category_path"],
                "status":                 row["status"],
                "approval_status":        row["approval_status"],
                "is_top":                 bool(row["is_top"]),
                "image_url":              row["image_url"],
                "url":                    row["url"],
                "seller_name":            row["seller_name"],
                "sales_count":            row["sales_count"],
                "rating":                 row["rating"],
                "ggsel_fee_pct":          row["ggsel_fee_pct"],
                "payment_fee_pct":        row["payment_fee_pct"],
                "risk_level":             row["risk_level"],
                "offer_id":               row["offer_id"],
                "created_at":             row["created_at"],
                "updated_at":             row["updated_at"],
            })

        conn.close()
        return jsonify({"ok": True, "items": items, "total": len(items)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500





# ═══════════════════════════════════════════════════════════════════════════
#  API: V1 — расширенные эндпоинты
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/v1/product/<int:product_id>/data")
def api_v1_product_data(product_id):
    code, data = _v1_get(f"/api_sellers/api/products/{product_id}/data",
                          headers={"lang": "ru-RU"})
    return jsonify({"status_code": code, "raw": data})


@app.route("/api/v1/update_prices", methods=["POST"])
def api_v1_update_prices():
    body = request.get_json()
    code, data = _v1_post("/api_sellers/api/product/edit/prices", body=body or [])
    return jsonify({"status_code": code, "ok": code in (200, 400), "raw": data})


@app.route("/api/v1/task_status")
def api_v1_task_status():
    task_id = request.args.get("taskId", "test-task-id")
    code, data = _v1_get("/api_sellers/api/product/edit/UpdateProductsTaskStatus",
                          params={"taskId": task_id})
    return jsonify({"status_code": code, "ok": code in (200, 400, 404), "raw": data})


@app.route("/api/v1/unique_code")
def api_v1_unique_code():
    code, sales = _v1_get("/api_sellers/api/seller-last-sales",
                           params={"seller_id": GGSEL_SELLER_ID, "top": 5},
                           headers={"locale": "ru"})
    invoice_id = None
    if isinstance(sales, dict):
        s = sales.get("sales", [])
        if s:
            invoice_id = s[0].get("invoice_id")
    if not invoice_id:
        return jsonify({"status_code": 0, "raw": {"info": "Нет продаж для проверки unique_code"}})
    code, order = _v1_get(f"/api_sellers/api/purchase/info/{invoice_id}",
                           headers={"locale": "ru"})
    uc = (order.get("content") or {}).get("unique_code") if isinstance(order, dict) else None
    if not uc:
        return jsonify({"status_code": 0, "invoice_id": invoice_id,
                        "raw": {"info": "unique_code отсутствует в заказе"}})
    code, data = _v1_get(f"/api_sellers/api/purchases/unique-code/{uc}")
    return jsonify({"status_code": code, "invoice_id": invoice_id, "unique_code": uc, "raw": data})


# ═══════════════════════════════════════════════════════════════════════════
#  API: V2 — Options / Variants / Splitted / Products / Create offer
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/v2/offer/<int:offer_id>/option/<int:option_id>")
def api_v2_option_view(offer_id, option_id):
    code, data, ok = v2_get_with_ok(f"/api_sellers/v2/offers/{offer_id}/options/{option_id}",
                                      headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/options", methods=["POST"])
def api_v2_option_create(offer_id):
    body = request.get_json() or {}
    code, data, ok = v2_post(f"/api_sellers/v2/offers/{offer_id}/options",
                              body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/options", methods=["DELETE"])
def api_v2_option_archive(offer_id):
    body = request.get_json() or {}
    code, data, ok = v2_delete(f"/api_sellers/v2/offers/{offer_id}/options",
                                body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/option/<int:option_id>/variants", methods=["POST"])
def api_v2_variant_create(offer_id, option_id):
    body = request.get_json() or {}
    code, data, ok = v2_post(f"/api_sellers/v2/offers/{offer_id}/options/{option_id}/variants",
                              body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/option/<int:option_id>/variants", methods=["DELETE"])
def api_v2_variant_archive(offer_id, option_id):
    body = request.get_json() or {}
    code, data, ok = v2_delete(f"/api_sellers/v2/offers/{offer_id}/options/{option_id}/variants",
                                body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/products", methods=["DELETE"])
def api_v2_products_archive(offer_id):
    body = request.get_json() or {}
    code, data, ok = v2_delete(f"/api_sellers/v2/offers/{offer_id}/products",
                                body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offer/<int:offer_id>/variant/<int:variant_id>/splitted_products", methods=["DELETE"])
def api_v2_splitted_archive(offer_id, variant_id):
    body = request.get_json() or {}
    code, data, ok = v2_delete(
        f"/api_sellers/v2/offers/{offer_id}/variants/{variant_id}/splitted_products",
        body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


@app.route("/api/v2/offers", methods=["POST"])
def api_v2_offer_create():
    body = request.get_json() or {}
    code, data, ok = v2_post("/api_sellers/v2/offers", body=body, headers={"locale": "ru"})
    return jsonify({"status_code": code, "ok": ok, "raw": data})


# ═══════════════════════════════════════════════════════════════════════════
#  API: FULL TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/test_all")
def api_test_all():
    out = {}

    def add(key, label, method, path, code, data, ok, v2=False, params=None):
        if isinstance(data, dict):
            sample = {}
            for k, v in list(data.items())[:8]:
                if isinstance(v, list) and len(v) > 2:
                    sample[k] = f"[list: {len(v)} items, first: {json.dumps(v[0], ensure_ascii=False)[:120]}…]"
                else:
                    sample[k] = v
        elif isinstance(data, list):
            sample = f"[list: {len(data)} items, first: {json.dumps(data[0], ensure_ascii=False)[:200] if data else '—'}…]" if data else "[]"
        else:
            sample = data
        out[key] = {
            "label": label, "method": method, "path": path, "v2": v2,
            "status_code": code, "ok": bool(ok), "sample": sample,
            "params": params or {},
        }

    def idle(key, label, method, path, v2=False, info="Не вызывался автоматически"):
        out[key] = {
            "label": label, "method": method, "path": path, "v2": v2,
            "status_code": 0, "ok": True, "idle": True, "sample": {"info": info},
            "params": {},
        }

    token, tcode, tdata = _v1_token()
    if not token:
        out["v1_01_token"] = {
            "label": "V1 ApiLogin — получение токена",
            "method": "POST", "path": "/api_sellers/api/apilogin",
            "v2": False, "status_code": tcode, "ok": False,
            "sample": tdata,
            "error_hint": "Неверный API ключ или Seller ID. Проверь config.py",
        }
        return jsonify(out)

    add("v1_01_token", "V1 ApiLogin", "POST", "/api_sellers/api/apilogin", tcode, tdata, True,
        params={"seller_id": GGSEL_SELLER_ID})

    c, d = _v1_get("/api_sellers/api/sellers/account/balance/info")
    add("v1_02_balance", "V1 Баланс", "GET", "/api_sellers/api/sellers/account/balance/info", c, d, c == 200)

    c, d = _v1_get("/api_sellers/api/sellers/account/receipts", params={"page": 1, "count": 5})
    add("v1_03_receipts", "V1 История операций", "GET", "/api_sellers/api/sellers/account/receipts", c, d, c == 200)

    c, d = _v1_get("/api_sellers/api/categories", params={"page": 1, "count": 5}, headers={"lang": "ru-RU"})
    add("v1_04_categories", "V1 Категории", "GET", "/api_sellers/api/categories", c, d, c == 200)

    c, d = _v1_get("/api_sellers/api/products/list", params={"page": 1, "count": 5}, headers={"lang": "ru-RU"})
    add("v1_05_products_list", "V1 Список товаров", "GET", "/api_sellers/api/products/list", c, d, c == 200)

    c, d = _v1_post("/api_sellers/api/seller-goods",
                     body={"id_seller": GGSEL_SELLER_ID, "order_col": "cntsell",
                           "order_dir": "desc", "rows": 5, "page": 1,
                           "currency": "RUB", "lang": "ru-RU", "show_hidden": 0})
    add("v1_06_seller_goods", "V1 Товары продавца", "POST", "/api_sellers/api/seller-goods", c, d, c == 200)

    c, d = _v1_get("/api_sellers/api/seller-last-sales",
                    params={"seller_id": GGSEL_SELLER_ID, "top": 5}, headers={"locale": "ru"})
    sales = extract_sales(d)
    add("v1_09_last_sales", "V1 Последние продажи", "GET", "/api_sellers/api/seller-last-sales", c, d, c == 200)

    c, d = _v1_get("/api_sellers/api/reviews",
                    params={"page": 1, "count": 5, "type": "all"}, headers={"locale": "ru-RU"})
    add("v1_11_reviews", "V1 Отзывы", "GET", "/api_sellers/api/reviews", c, d, c == 200)

    c_chats, d_chats = _v1_get("/api_sellers/api/debates/v2/chats", params={"page": 1, "pagesize": 10})
    chats_items = extract_chats(d_chats)
    add("v1_12_chats", "V1 Чаты", "GET", "/api_sellers/api/debates/v2/chats", c_chats, d_chats, c_chats == 200)

    c, d = v2_get("/api_sellers/v2/categories", params={"page": 1, "limit": 5}, headers={"locale": "ru"})
    cats = extract_categories_v2(d)
    add("v2_01_categories", "V2 Категории", "GET", "/api_sellers/v2/categories", c, d, c == 200)

    c, d = v2_get("/api_sellers/v2/offers", params={"page": 1, "limit": 5}, headers={"locale": "ru"})
    offers_list, pagination = extract_offers(d)
    add("v2_03_offers", "V2 Офферы", "GET", "/api_sellers/v2/offers", c, d, c == 200)

    # ── Дополнительные V1 эндпоинты ──
    invoice_id = None
    if sales:
        invoice_id = sales[0].get("invoice_id")
    if invoice_id:
        c, d = _v1_get(f"/api_sellers/api/purchase/info/{invoice_id}", headers={"locale": "ru"})
        add("v1_07_order_info", f"V1 Информация о заказе #{invoice_id}", "GET", f"/api_sellers/api/purchase/info/{invoice_id}", c, d, c == 200)
    else:
        idle("v1_07_order_info", "V1 Информация о заказе", "GET", "/api_sellers/api/purchase/info/{invoice_id}", info="Нет доступных заказов для теста")

    chat_id = None
    if chats_items:
        chat_id = chats_items[0].get("id_i") or chats_items[0].get("id")
    if chat_id:
        c, d = _v1_get("/api_sellers/api/debates/v2", params={"id_i": chat_id, "count": 5})
        add("v1_08_chat_messages", f"V1 Сообщения чата #{chat_id}", "GET", "/api_sellers/api/debates/v2", c, d, c == 200)
    else:
        idle("v1_08_chat_messages", "V1 Сообщения чата", "GET", "/api_sellers/api/debates/v2", info="Нет доступных чатов для теста")

    idle("v1_10_reviews_answer", "V1 Ответ на отзыв", "POST", "/api_sellers/api/reviews/answer", info="Вызывается по запросу при ответе на отзыв")
    idle("v1_13_chats_send", "V1 Отправить сообщение", "POST", "/api_sellers/api/debates/v2", info="Вызывается по запросу при отправке сообщения в чат")
    idle("v1_14_promo_codes", "V1 Список промокодов", "GET", "/api_sellers/api/promocodes/list", info="Вызывается по запросу при просмотре промокодов")
    idle("v1_15_promo_code_create", "V1 Создать промокод", "POST", "/api_sellers/api/promocodes/create", info="Вызывается по запросу при создании промокода")

    # ── Дополнительные V2 эндпоинты ──
    cat_id = None
    if cats:
        cat_id = cats[0].get("id")
    # GET /api_sellers/v2/categories/{id} не поддерживается V2 API (404)
    idle("v2_02_category_info", "V2 Категория по ID (не поддерживается)", "GET",
         "/api_sellers/v2/categories/{id}", v2=True,
         info="V2 API не поддерживает получение одной категории по ID — используй /api_sellers/v2/categories (список)")

    off_id = None
    if offers_list:
        off_id = offers_list[0].get("id")
    if off_id:
        c, d = v2_get(f"/api_sellers/v2/offers/{off_id}", headers={"locale": "ru"})
        add("v2_05_offer_get", f"V2 Информация об оффере #{off_id}", "GET", f"/api_sellers/v2/offers/{off_id}", c, d, c == 200, v2=True)
        
        c, d = v2_get(f"/api_sellers/v2/offers/{off_id}/options", headers={"locale": "ru"})
        add("v2_08_offer_options", f"V2 Опции оффера #{off_id}", "GET", f"/api_sellers/v2/offers/{off_id}/options", c, d, c == 200, v2=True)
    else:
        idle("v2_05_offer_get", "V2 Информация об оффере", "GET", "/api_sellers/v2/offers/{id}", v2=True, info="Нет доступных офферов V2")
        idle("v2_08_offer_options", "V2 Опции оффера", "GET", "/api_sellers/v2/offers/{id}/options", v2=True, info="Нет доступных офферов V2")

    idle("v2_04_offer_create", "V2 Создать оффер", "POST", "/api_sellers/v2/offers", v2=True, info="Вызывается по запросу при создании нового оффера")
    idle("v2_06_offer_update", "V2 Обновить оффер", "PATCH", "/api_sellers/v2/offers/{id}", v2=True, info="Вызывается по запросу при обновлении настроек оффера")
    idle("v2_07_offer_delete", "V2 Удалить оффер", "DELETE", "/api_sellers/v2/offers/{id}", v2=True, info="Вызывается по запросу при удалении оффера")
    idle("v2_09_offer_options_create", "V2 Создать опцию", "POST", "/api_sellers/v2/offers/{id}/options", v2=True, info="Вызывается по запросу при добавлении новой опции")
    idle("v2_10_offer_options_delete", "V2 Удалить опцию", "DELETE", "/api_sellers/v2/offers/{id}/options", v2=True, info="Вызывается по запросу при удалении опции")
    idle("v2_11_offer_option_variants_create", "V2 Создать вариант опции", "POST", "/api_sellers/v2/offers/{id}/option/{option_id}/variants", v2=True, info="Вызывается по запросу при добавлении варианта опции")
    idle("v2_12_offer_option_variants_delete", "V2 Удалить вариант опции", "DELETE", "/api_sellers/v2/offers/{id}/option/{option_id}/variants", v2=True, info="Вызывается по запросу при удалении варианта опции")
    idle("v2_13_offer_products_create", "V2 Добавить товары", "POST", "/api_sellers/v2/offers/{id}/products", v2=True, info="Вызывается по запросу при импорте кодов/ключей")
    idle("v2_14_offer_products_delete", "V2 Удалить товары", "DELETE", "/api_sellers/v2/offers/{id}/products", v2=True, info="Вызывается по запросу при очистке списка товаров")
    idle("v2_15_offer_variant_products_create", "V2 Загрузить файлы/коды варианта", "POST", "/api_sellers/v2/offers/{id}/variant/{variant_id}/products", v2=True, info="Вызывается по запросу при загрузке содержимого варианта")
    idle("v2_16_offer_variant_products_delete", "V2 Удалить файлы/коды варианта", "DELETE", "/api_sellers/v2/offers/{id}/variant/{variant_id}/splitted_products", v2=True, info="Вызывается по запросу при удалении содержимого варианта")

    # Финализируем статистику GGSEL (все ключи до MSB)
    ggsel_keys   = [k for k in out if not k.startswith("msb_") and not k.startswith("__")]
    ggsel_idle   = sum(1 for k in ggsel_keys if out[k].get("idle"))
    ggsel_ok     = sum(1 for k in ggsel_keys if out[k].get("ok") and not out[k].get("idle"))
    ggsel_fail   = len(ggsel_keys) - ggsel_ok - ggsel_idle
    out["__summary_ggsel__"] = {
        "title": "GGsel API (V1 + V2)",
        "total": len(ggsel_keys), "ok": ggsel_ok, "idle": ggsel_idle, "fail": ggsel_fail,
    }

    # ═══════════════════════════════════════════════════════════════════════
    #  MSB (MyStealthBrowser) API — порт 17248
    # ═══════════════════════════════════════════════════════════════════════
    from config import MSB_API_BASE, MSB_API_TOKEN as _msb_token
    import requests as _req

    def _msb_get(path, params=None):
        headers = {}
        if _msb_token:
            headers["Authorization"] = f"Bearer {_msb_token}"
        try:
            r = _req.get(f"{MSB_API_BASE}{path}", headers=headers, params=params or {}, timeout=5)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text[:300]}
        except Exception as e:
            return 0, {"error": str(e)}

    def _msb_add(key, label, path, code, data):
        ok = code == 200 and (isinstance(data, dict) and data.get("ok", True))
        add(key, f"MSB {label}", "GET", path, code, data.get("data", data) if isinstance(data, dict) else data, ok)

    # 1. Healthcheck
    c, d = _msb_get("/health")
    _msb_add("msb_01_health", "Health", "/health", c, d)

    # 2. Список профилей
    c, d = _msb_get("/profiles")
    profiles_data = d.get("data", []) if isinstance(d, dict) else []
    _msb_add("msb_02_profiles", "Список профилей", "/profiles", c, d)

    # 3. Запущенные браузеры
    c, d = _msb_get("/profiles/running")
    _msb_add("msb_03_running", "Запущенные профили", "/profiles/running", c, d)

    # 4. Статус браузеров
    c, d = _msb_get("/browser/status")
    _msb_add("msb_04_browser_status", "Статус браузеров", "/browser/status", c, d)

    # 5. Статистика
    c, d = _msb_get("/stats")
    _msb_add("msb_05_stats", "Статистика сессий", "/stats", c, d)

    # 6-8. Детали первого профиля (если есть)
    if profiles_data:
        pid = profiles_data[0].get("id")
        if pid:
            c, d = _msb_get(f"/profiles/{pid}")
            _msb_add("msb_06_profile_detail", f"Детали профиля {profiles_data[0].get('name','')[:20]}", f"/profiles/{pid}", c, d)

            c, d = _msb_get(f"/profiles/{pid}/status")
            _msb_add("msb_07_profile_status", "Статус профиля", f"/profiles/{pid}/status", c, d)

            c, d = _msb_get(f"/profiles/{pid}/cookies", params={"format": "json"})
            _msb_add("msb_08_profile_cookies", "Куки профиля", f"/profiles/{pid}/cookies", c, d)
    else:
        idle("msb_06_profile_detail", "MSB Детали профиля", "GET", "/profiles/{id}", info="Нет профилей")
        idle("msb_07_profile_status", "MSB Статус профиля", "GET", "/profiles/{id}/status", info="Нет профилей")
        idle("msb_08_profile_cookies", "MSB Куки профиля", "GET", "/profiles/{id}/cookies", info="Нет профилей")

    # Mutation-эндпоинты — не вызываем автоматически
    idle("msb_09_profile_start",  "MSB Запустить браузер",          "POST",   "/profiles/{id}/start",              info="Вызывается по запросу при запуске профиля")
    idle("msb_10_profile_stop",   "MSB Остановить браузер",         "POST",   "/profiles/{id}/stop",               info="Вызывается по запросу при остановке профиля")
    idle("msb_11_profile_goto",   "MSB Навигация в браузере",       "POST",   "/profiles/{id}/goto",               info="Вызывается по запросу для перехода по URL")
    idle("msb_12_run_scenario",   "MSB Запустить сценарий",         "POST",   "/profiles/{id}/runScenario",        info="Вызывается парсером для логина через куки")
    idle("msb_13_check_proxy",    "MSB Проверить прокси",           "POST",   "/profiles/{id}/check-proxy",        info="Вызывается по запросу для проверки прокси")
    idle("msb_14_switch_proxy",   "MSB Сменить прокси",             "POST",   "/profiles/{id}/switchProxy",        info="Вызывается по запросу для смены прокси на лету")
    idle("msb_15_refresh_fp",     "MSB Обновить Fingerprint",       "POST",   "/profiles/{id}/refreshFingerprint", info="Вызывается по запросу для генерации нового отпечатка")
    idle("msb_16_import_cookies", "MSB Импортировать куки",         "POST",   "/profiles/{id}/cookies",            info="Вызывается при сохранении сессии из браузера")
    idle("msb_17_clear_cookies",  "MSB Очистить куки",              "DELETE", "/profiles/{id}/cookies",            info="Вызывается по запросу для сброса сессии")
    idle("msb_18_create_profile", "MSB Создать профиль",            "POST",   "/profiles",                         info="Вызывается по запросу при создании нового профиля")
    idle("msb_19_update_profile", "MSB Обновить профиль",           "PATCH",  "/profiles/{id}",                    info="Вызывается по запросу при редактировании профиля")
    idle("msb_20_delete_profile", "MSB Удалить профиль",            "DELETE", "/profiles/{id}",                    info="Вызывается по запросу при удалении профиля")

    # Финализируем статистику MSB
    msb_keys  = [k for k in out if k.startswith("msb_")]
    msb_idle  = sum(1 for k in msb_keys if out[k].get("idle"))
    msb_ok    = sum(1 for k in msb_keys if out[k].get("ok") and not out[k].get("idle"))
    msb_fail  = len(msb_keys) - msb_ok - msb_idle
    out["__summary_msb__"] = {
        "title": "MSB API (порт 17248)",
        "total": len(msb_keys), "ok": msb_ok, "idle": msb_idle, "fail": msb_fail,
    }

    # Общий итог
    total    = len([k for k in out if not k.startswith("__")])
    idle_cnt = ggsel_idle + msb_idle
    ok_cnt   = ggsel_ok   + msb_ok
    fail_cnt = ggsel_fail  + msb_fail
    out["__summary__"] = {"total": total, "ok": ok_cnt, "idle": idle_cnt, "fail": fail_cnt}
    return jsonify(out)


@app.route("/api/test_msb")
def api_test_msb():
    """MSB API diagnostics - MSB endpoints only (port 17248)."""
    from config import MSB_API_BASE, MSB_API_TOKEN as _msb_token
    import requests as _req

    out = {}

    def add(key, label, method, path, code, data, ok, info=None, v2=False):
        out[key] = {
            "label": label, "method": method, "path": path,
            "status_code": code,
            "sample": data if isinstance(data, (dict, list)) else {},
            "ok": ok, "idle": False, "v2": v2,
            "error_hint": info if not ok and info else None,
        }

    def idle(key, label, method, path, info="", v2=False):
        out[key] = {
            "label": label, "method": method, "path": path,
            "status_code": 0, "sample": {}, "ok": False, "idle": True, "v2": v2,
            "error_hint": info,
        }

    def _get(path, params=None):
        headers = {}
        if _msb_token:
            headers["Authorization"] = f"Bearer {_msb_token}"
        try:
            r = _req.get(f"{MSB_API_BASE}{path}", headers=headers,
                         params=params or {}, timeout=5)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text[:300]}
        except Exception as e:
            return 0, {"error": str(e)}

    def _add(key, label, path, code, data):
        ok = code == 200 and (isinstance(data, dict) and data.get("ok", True))
        add(key, label, "GET", path, code,
            data.get("data", data) if isinstance(data, dict) else data, ok)

    # 1. Health
    c, d = _get("/health")
    _add("msb_01_health", "Health", "/health", c, d)

    # 2. Профили
    c, d = _get("/profiles")
    profiles_data = d.get("data", []) if isinstance(d, dict) else []
    _add("msb_02_profiles", "Список профилей", "/profiles", c, d)

    # 3. Запущенные
    c, d = _get("/profiles/running")
    _add("msb_03_running", "Запущенные профили", "/profiles/running", c, d)

    # 4. Browser status
    c, d = _get("/browser/status")
    _add("msb_04_browser_status", "Статус браузеров", "/browser/status", c, d)

    # 5. Статистика
    c, d = _get("/stats")
    _add("msb_05_stats", "Статистика сессий", "/stats", c, d)

    # 6. Группы
    c, d = _get("/groups")
    _add("msb_06_groups", "Группы профилей", "/groups", c, d)

    # 7-9. Первый профиль
    if profiles_data:
        pid = profiles_data[0].get("id")
        if pid:
            pname = str(profiles_data[0].get("name", pid))[:24]
            c, d = _get(f"/profiles/{pid}")
            _add("msb_07_profile_detail", f"Профиль: {pname}", f"/profiles/{pid}", c, d)

            c, d = _get(f"/profiles/{pid}/status")
            _add("msb_08_profile_status", "Статус профиля", f"/profiles/{pid}/status", c, d)

            c, d = _get(f"/profiles/{pid}/cookies", params={"format": "json"})
            _add("msb_09_profile_cookies", "Куки профиля", f"/profiles/{pid}/cookies", c, d)
    else:
        idle("msb_07_profile_detail", "Профиль (детали)", "GET", "/profiles/{id}", info="Нет профилей в MSB")
        idle("msb_08_profile_status", "Статус профиля", "GET", "/profiles/{id}/status", info="Нет профилей в MSB")
        idle("msb_09_profile_cookies", "Куки профиля", "GET", "/profiles/{id}/cookies", info="Нет профилей в MSB")

    # Mutation-эндпоинты (не вызываем автоматически)
    idle("msb_10_start",          "Запустить браузер",      "POST",   "/profiles/{id}/start",              info="Запускает профиль, возвращает debugPort для CDP")
    idle("msb_11_stop",           "Остановить браузер",     "POST",   "/profiles/{id}/stop",               info="Останавливает запущенный профиль")
    idle("msb_12_goto",           "Навигация",              "POST",   "/profiles/{id}/goto",               info="Переходит по URL в открытом браузере")
    idle("msb_13_scenario",       "Сценарий",               "POST",   "/profiles/{id}/runScenario",        info="Запускает автоматизированный сценарий (логин и т.д.)")
    idle("msb_14_proxy_check",    "Проверить прокси",       "POST",   "/profiles/{id}/check-proxy",        info="Проверяет прокси профиля")
    idle("msb_15_proxy_switch",   "Сменить прокси",         "POST",   "/profiles/{id}/switchProxy",        info="Меняет прокси на лету без перезапуска")
    idle("msb_16_fp_refresh",     "Обновить Fingerprint",   "POST",   "/profiles/{id}/refreshFingerprint", info="Генерирует новый fingerprint профиля")
    idle("msb_17_cookies_import", "Импорт куков",           "POST",   "/profiles/{id}/cookies",            info="Импортирует куки в профиль")
    idle("msb_18_cookies_clear",  "Очистить куки",          "DELETE", "/profiles/{id}/cookies",            info="Очищает все куки профиля")
    idle("msb_19_create",         "Создать профиль",        "POST",   "/profiles",                         info="Создаёт новый профиль")
    idle("msb_20_update",         "Обновить профиль",       "PATCH",  "/profiles/{id}",                    info="Обновляет настройки профиля")
    idle("msb_21_delete",         "Удалить профиль",        "DELETE", "/profiles/{id}",                    info="Удаляет профиль безвозвратно")

    # Summary
    total    = len(out)
    idle_cnt = sum(1 for v in out.values() if v.get("idle"))
    ok_cnt   = sum(1 for v in out.values() if v.get("ok") and not v.get("idle"))
    fail_cnt = total - ok_cnt - idle_cnt
    out["__summary__"] = {
        "title": "MSB API (MyStealthBrowser · порт 17248)",
        "total": total, "ok": ok_cnt, "idle": idle_cnt, "fail": fail_cnt,
    }
    return jsonify(out)


@app.route("/api/test_backend")
def api_test_backend():
    """Диагностика собственных Flask-эндпоинтов панели."""
    import requests as _req

    _base = f"http://127.0.0.1:{LOCAL_PORT}"
    out = {}

    def _get(path, params=None, timeout=6):
        try:
            r = _req.get(f"{_base}{path}", params=params or {}, timeout=timeout)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text[:300]}
        except Exception as e:
            return 0, {"error": str(e)[:200]}

    def add(key, label, method, path, code, data, ok, info=None):
        sample = data if isinstance(data, (dict, list)) else {}
        # trim heavy payloads
        if isinstance(sample, dict):
            sample = {k: v for i, (k, v) in enumerate(sample.items()) if i < 12}
        out[key] = {
            "label": label, "method": method, "path": path,
            "status_code": code, "sample": sample,
            "ok": ok, "idle": False,
            "error_hint": info if not ok and info else None,
        }

    def idle(key, label, method, path, info=""):
        out[key] = {
            "label": label, "method": method, "path": path,
            "status_code": 0, "sample": {}, "ok": False, "idle": True,
            "error_hint": info,
        }

    # ── GET-эндпоинты (проверяем реально) ──────────────────────────────
    c, d = _get("/api/cookie/status")
    add("be_01_cookie",     "Cookie статус",         "GET", "/api/cookie/status",   c, d, c == 200)

    c, d = _get("/api/balance")
    add("be_02_balance",    "Баланс продавца",        "GET", "/api/balance",         c, d, c == 200)

    c, d = _get("/api/offers", {"page": 1, "limit": 5})
    add("be_03_offers",     "Офферы",                 "GET", "/api/offers",          c, d, c == 200)

    c, d = _get("/api/sales", {"top": 5})
    add("be_04_sales",      "Продажи",                "GET", "/api/sales",           c, d, c == 200)

    c, d = _get("/api/reviews", {"page": 1, "count": 5})
    add("be_05_reviews",    "Отзывы",                 "GET", "/api/reviews",         c, d, c == 200)

    c, d = _get("/api/chats", {"page": 1, "pagesize": 5})
    add("be_06_chats",      "Чаты",                   "GET", "/api/chats",           c, d, c == 200)

    c, d = _get("/api/categories/v2")
    add("be_07_cats",       "Категории V2",           "GET", "/api/categories/v2",   c, d, c == 200)

    c, d = _get("/api/notifications")
    add("be_08_notifs",     "Уведомления",            "GET", "/api/notifications",   c, d, c == 200)

    c, d = _get("/api/orders/linked")
    add("be_09_orders",     "Связанные заказы",       "GET", "/api/orders/linked",   c, d, c == 200)

    try:
        from parser.routes import parser_bp
        parser_ok = True
    except Exception:
        parser_ok = False
    c, d = _get("/api/parser/status") if parser_ok else (0, {"error": "blueprint not loaded"})
    add("be_10_parser",     "Парсер статус",          "GET", "/api/parser/status",   c, d, c == 200)

    c, d = _get("/api/parser/msb/status") if parser_ok else (0, {"error": "blueprint not loaded"})
    add("be_11_parser_msb", "Парсер MSB статус",      "GET", "/api/parser/msb/status", c, d, c == 200)

    # ── Внутренние эндпоинты (GET, лёгкие) ─────────────────────────────
    c, d = _get("/api/categories/v2/tree")
    add("be_12_cat_tree",   "Дерево категорий (кеш)", "GET", "/api/categories/v2/tree", c, d, c == 200)

    # ── Mutation-эндпоинты (только документируем) ───────────────────────
    idle("be_m01_cookie_refresh",  "Обновить куки (MSB CDP)",   "POST",   "/api/cookie/refresh",
         info="Запускает QratorCookieMiddleware, забирает куки через CDP профиля парсера")
    idle("be_m02_cookie_browser",  "Открыть MSB Seller",        "POST",   "/api/cookie/open-browser",
         info="Открывает группу SellerGGsel в MSB, переходит на seller.ggsel.com, снимает куки")
    idle("be_m03_offer_update",    "Обновить оффер",            "PATCH",  "/api/offer/{id}/update",
         info="Обновляет цену/описание/название оффера через V2 API")
    idle("be_m04_offers_activate", "Batch активация офферов",   "POST",   "/api/offers/batch_activate",
         info="Массово активирует офферы по списку ID")
    idle("be_m05_offers_pause",    "Batch пауза офферов",       "POST",   "/api/offers/batch_pause",
         info="Массово приостанавливает офферы")
    idle("be_m06_offers_delete",   "Batch удаление офферов",    "POST",   "/api/offers/batch_delete",
         info="Массово удаляет офферы")
    idle("be_m07_add_products",    "Добавить товары в оффер",   "POST",   "/api/offer/{id}/products",
         info="Загружает коды/ключи в оффер через V2 API")
    idle("be_m08_chat_send",       "Отправить сообщение в чат", "POST",   "/api/chat/{id}/send",
         info="Отправляет сообщение продавца в чат с покупателем")
    idle("be_m09_cats_selected",   "Сохранить категории",       "POST",   "/api/categories/selected",
         info="Сохраняет список выбранных категорий для парсера")
    idle("be_m10_order_link",      "Связать заказ",             "POST",   "/api/orders/{id}/link",
         info="Связывает заказ с товаром из парсера для отслеживания")
    idle("be_m11_parser_run",      "Запустить парсер",          "POST",   "/api/parser/run",
         info="Запускает парсинг по выбранным категориям")

    # ── Summary ─────────────────────────────────────────────────────────
    total    = len(out)
    idle_cnt = sum(1 for v in out.values() if v.get("idle"))
    ok_cnt   = sum(1 for v in out.values() if v.get("ok") and not v.get("idle"))
    fail_cnt = total - ok_cnt - idle_cnt
    out["__summary__"] = {
        "title": f"Backend Flask (:{LOCAL_PORT})",
        "total": total, "ok": ok_cnt, "idle": idle_cnt, "fail": fail_cnt,
    }
    return jsonify(out)


# ═══════════════════════════════════════════════════════════════════════════
#  AI Workspace — запуск профиля MSB
# ═══════════════════════════════════════════════════════════════════════════

AI_WORKSPACE_PROFILE_ID = "1873432d-b054-48a6-a031-b2bacc0fe77d"


@app.route("/api/msb/ai-workspace/launch", methods=["POST"])
def api_ai_workspace_launch():
    """POST /api/msb/ai-workspace/launch - launch AI Workspace profile in MSB."""
    import asyncio as _aio
    from parser.msb_client import MsbClient

    async def _launch():
        async with MsbClient() as ml:
            return await ml.start_profile(
                AI_WORKSPACE_PROFILE_ID,
                launchMode="visible",
            )

    try:
        result = _aio.run(_launch())
        if not result:
            return jsonify({"ok": False, "error": "MSB не вернул ответ — профиль уже запущен или MSB недоступен"}), 502
        return jsonify({"ok": True, "profile_id": AI_WORKSPACE_PROFILE_ID, "data": result})
    except Exception as e:
        log.warning("ai-workspace launch error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500




# ═══════════════════════════════════════════════════════════════════════
#  Cookie Auto-Refresh (background scheduler)
# ═══════════════════════════════════════════════════════════════════════
try:
    from cookie_autorefresh import auto_bp as _auto_bp, start_scheduler as _start_autorefresh
    app.register_blueprint(_auto_bp)
    _start_autorefresh()
    log.info("Cookie autorefresh scheduler started")
except Exception as e:
    log.warning("Cookie autorefresh failed to start: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
#  Parser blueprint
# ═══════════════════════════════════════════════════════════════════════════
try:
    from curl_cffi import requests as _cffi_check  # noqa: F401
    _PARSER_DEPS_OK = True
except Exception:
    _PARSER_DEPS_OK = False

if _PARSER_DEPS_OK:
    try:
        from parser.routes import parser_bp
        try:
            from parser import init_db as _parser_init_db
            _parser_init_db()
            log.info("Parser DB initialized")

            # ── ШАГ 3: Очистка невалидных записей ─────────────────────────
            try:
                import sqlite3 as _s3
                from parser.db_init import get_db_path as _gdp
                _conn = _s3.connect(_gdp(), timeout=10)
                try:
                    _cur = _conn.cursor()
                    deleted_parsed = _cur.execute(
                        "DELETE FROM parsed_products "
                        "WHERE category_id IS NULL AND (sell_price IS NULL OR sell_price = 0) "
                        "AND title IS NULL"
                    ).rowcount
                    deleted_rejected = _cur.execute(
                        "DELETE FROM rejected_products "
                        "WHERE reject_code IS NULL OR TRIM(reject_code) = ''"
                    ).rowcount
                    _conn.commit()
                    if deleted_parsed or deleted_rejected:
                        log.info(
                            "DB cleanup: parsed_products=%d, rejected_products=%d",
                            deleted_parsed, deleted_rejected,
                        )
                finally:
                    _conn.close()
            except Exception as e:
                log.warning("DB cleanup failed: %s", e)

            # ── ШАГ 3: Завершаем зависшие parser_runs ─────────────────────
            try:
                import sqlite3 as _s3
                from datetime import datetime as _dt
                from parser.db_init import get_db_path as _gdp
                _conn = _s3.connect(_gdp(), timeout=10)
                try:
                    _cur = _conn.cursor()
                    try:
                        _cols = [r[1] for r in _cur.execute("PRAGMA table_info(parser_runs)")]
                        if "errors" not in _cols:
                            _cur.execute("ALTER TABLE parser_runs ADD COLUMN errors TEXT DEFAULT ''")
                    except Exception:
                        pass
                    _now_iso = _dt.utcnow().isoformat()
                    reset = _cur.execute(
                        "UPDATE parser_runs "
                        "SET status='crashed', "
                        "    finished_at=COALESCE(finished_at,?), "
                        "    errors=TRIM(COALESCE(errors,'') || '; app_restarted',';') "
                        "WHERE status='running'", (_now_iso,)
                    ).rowcount
                    _conn.commit()
                    if reset:
                        log.info("parser_runs: переведено crashed: %d (app_restarted)", reset)
                finally:
                    _conn.close()
            except Exception as e:
                log.warning("parser_runs crash-reset failed: %s", e)
            # ──────────────────────────────────────────────────────────────
        except Exception as e:
            log.warning("Parser DB init failed: %s", e)
        app.register_blueprint(parser_bp)
        log.info("Parser blueprint registered (curl-cffi OK)")

        from pathlib import Path as _P
        _GEN_DIR = _P(__file__).resolve().parent / "data" / "images" / "generated"

        @app.route("/parser/image")
        def _parser_image():
            from flask import abort, send_file, request as _req
            rel = _req.args.get("path", "")
            if not rel:
                abort(400)
            try:
                p = _P(rel).resolve()
                gen_resolved = _GEN_DIR.resolve()
                p.relative_to(gen_resolved)
            except (ValueError, Exception):
                abort(403)
            if not p.is_file():
                abort(404)
            return send_file(str(p))
    except Exception as e:
        log.warning("Parser blueprint failed to register: %s", e)
else:
    log.warning(
        "Parser blueprint NOT registered: curl-cffi не установлен. "
        "Установи: pip install curl-cffi beautifulsoup4 lxml"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════


# =============================================================================
#  ШАГ 10: force-recheck — сброс last_parsed_at для ручного перепарсивания
# =============================================================================
@app.route('/api/products/<product_id>/force-recheck', methods=['POST'])
def api_product_force_recheck(product_id):
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    try:
        import sqlite3 as _s3
        from parser.db_init import get_db_path as _gdp
        _conn = _s3.connect(_gdp(), timeout=15)
        try:
            _c = _conn.cursor()
            # 1) сбросить last_parsed_at в parsed_products
            _c.execute("UPDATE parsed_products SET last_parsed_at=NULL, updated_at=datetime('now') "
                       "WHERE product_id=?", (product_id,))
            upd = _c.rowcount
            # 2) удалить из rejected_products (TTL больше не применяется)
            _c.execute("DELETE FROM rejected_products WHERE product_id=?", (product_id,))
            rem = _c.rowcount
            _conn.commit()
            if upd or rem:
                return jsonify({'ok': True, 'product_id': product_id,
                                'parser_reset': upd, 'rejected_removed': rem})
            return jsonify({'ok': False, 'error': 'not_found', 'product_id': product_id}), 404
        finally:
            _conn.close()
    except Exception as e:
        log.exception("force-recheck failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


# =============================================================================
#  ШАГ 11: Сделки перепродажи resale_deals
# =============================================================================
_DEAL_STATUSES_FLOW = [
    'new', 'source_recheck', 'buy_from_source',
    'source_chat', 'deliver_to_buyer', 'completed',
]
_DEAL_STATUSES_EXTRA = ['cancelled', 'refund', 'dispute', 'loss']
_DEAL_STATUSES = set(_DEAL_STATUSES_FLOW + _DEAL_STATUSES_EXTRA)


def _deals_db():
    from parser.db_init import get_db_path
    import sqlite3
    conn = sqlite3.connect(get_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _append_changelog(current: str | None, status: str, who: str, note: str) -> str:
    import json as _j
    from datetime import datetime
    try:
        arr = _j.loads(current or '[]')
    except Exception:
        arr = []
    arr.append({'ts': datetime.utcnow().isoformat(),
                'status': status, 'who': who or 'web',
                'note': note or ''})
    return _j.dumps(arr, ensure_ascii=False)


@app.route('/deals', methods=['GET'])
def deals_list():
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    status = request.args.get('status')
    limit = int(request.args.get('limit', 100))
    try:
        conn = _deals_db()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM resale_deals WHERE status=? "
                    "ORDER BY updated_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM resale_deals ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return jsonify({'ok': True, 'items': [dict(r) for r in rows],
                            'count': len(rows), 'valid_statuses': sorted(list(_DEAL_STATUSES))})
        finally:
            conn.close()
    except Exception as e:
        log.exception("/deals list failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@app.route('/deals/<int:deal_id>', methods=['GET'])
def deals_detail(deal_id):
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    try:
        conn = _deals_db()
        try:
            r = conn.execute("SELECT * FROM resale_deals WHERE deal_id=?", (deal_id,)).fetchone()
            if not r:
                return jsonify({'ok': False, 'error': 'not_found'}), 404
            return jsonify({'ok': True, 'deal': dict(r)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@app.route('/deals', methods=['POST'])
def deals_create():
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    payload = request.get_json(silent=True) or {}
    product_id = (payload.get('product_id') or '').strip()
    if not product_id:
        return jsonify({'ok': False, 'error': 'product_id required'}), 400
    try:
        import sqlite3 as _s3
        from parser.db_init import get_db_path as _gdp
        from datetime import datetime
        conn = _s3.connect(_gdp(), timeout=15)
        try:
            row = conn.execute(
                "SELECT product_id, url, seller_name, source_price, sell_price, "
                "expected_profit_rub, offer_id "
                "FROM parsed_products WHERE product_id=? AND status='approved'",
                (product_id,)
            ).fetchone()
            if not row:
                return jsonify({'ok': False, 'error': 'approved_product_not_found',
                                'product_id': product_id}), 404
            (pid, url, seller, src_price, sell_p, exp_profit, offid) = row
            now = datetime.utcnow().isoformat()
            changelog = _append_changelog(None, 'new', payload.get('who'), 'created from approved product')
            cur = conn.execute(
                """INSERT INTO resale_deals
                   (buyer_order_id, offer_id, product_id, product_url, seller_name,
                    source_price_at_decision, sell_price, expected_profit_rub,
                    buyer_chat_ref, seller_chat_ref, status, changelog_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (payload.get('buyer_order_id'), offid or payload.get('offer_id'), pid, url,
                 seller or payload.get('seller_name'), src_price,
                 payload.get('sell_price') or sell_p,
                 payload.get('expected_profit_rub') or exp_profit,
                 payload.get('buyer_chat_ref'), payload.get('seller_chat_ref'),
                 'new', changelog, now, now)
            )
            conn.commit()
            return jsonify({'ok': True, 'deal_id': cur.lastrowid, 'status': 'new'})
        finally:
            conn.close()
    except Exception as e:
        log.exception("/deals create failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@app.route('/deals/<int:deal_id>/status', methods=['POST'])
def deals_status_change(deal_id):
    """Ручная смена статуса. ПЕРЕХОД В buy_from_source ЗАПРЕЩАЕТСЯ без проверок."""
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    payload = request.get_json(silent=True) or {}
    new_status = (payload.get('status') or '').strip()
    if new_status not in _DEAL_STATUSES:
        return jsonify({'ok': False, 'error': 'invalid_status',
                        'allowed': sorted(list(_DEAL_STATUSES))}), 400
    try:
        import sqlite3 as _s3
        from parser.db_init import get_db_path as _gdp
        import config as _CFG
        from datetime import datetime
        conn = _s3.connect(_gdp(), timeout=15)
        try:
            row = conn.execute(
                "SELECT * FROM resale_deals WHERE deal_id=?", (deal_id,)).fetchone()
            if not row:
                return jsonify({'ok': False, 'error': 'not_found'}), 404
            (did, bord, ofid, pid, purl, sname, src_dec, sellp,
             expp, actp, actm, bchat, schat, curst, clog, c_at, u_at) = row

            # ── Ограничение ШАГ 11: buy_from_source только после явного подтверждения ──
            if new_status == 'buy_from_source':
                # 1) актуальная цена источника = decision_price (владелец подтверждает руками)
                actual_source_at_req = payload.get('actual_source_price')
                if actual_source_at_req is None or abs(float(actual_source_at_req) - float(src_dec or 0)) > 0.01:
                    return jsonify({
                        'ok': False,
                        'error': 'source_price_not_confirmed',
                        'detail': ('Передайте actual_source_price равным '
                                   f'source_price_at_decision ({src_dec}). '
                                   'Это ручное подтверждение владельцем.')
                    }), 412
                # 2) пересчитаем фактическую экономику на текущую дату
                act_sell = float(sellp or 0)
                act_cost = float(actual_source_at_req or 0)
                actual_profit = payload.get('actual_profit_rub')
                actual_margin = payload.get('actual_net_margin_pct')
                if actual_profit is None or actual_margin is None:
                    # Считаем прибыль и маржу через единую формулу EconomicsCalculator
                    from parser.economics import get_calculator
                    calc = get_calculator()
                    pf = conn.execute(
                        "SELECT ggsel_fee_pct, payment_fee_pct, withdrawal_fee_pct,"
                        "       tax_pct, fixed_costs_rub, risk_reserve_pct "
                        "FROM parsed_products WHERE product_id=?", (pid,)).fetchone()
                    gf_val = float(pf[0]) if (pf and pf[0] is not None) else 0.0
                    res = calc.calculate(
                        source_price=act_cost,
                        category_fee_pct=gf_val,
                        payment_fee_pct=float(pf[1]) if (pf and pf[1] is not None) else None,
                        withdrawal_fee_pct=float(pf[2]) if (pf and pf[2] is not None) else None,
                        tax_pct=float(pf[3]) if (pf and pf[3] is not None) else None,
                        fixed_costs_rub=float(pf[4]) if (pf and pf[4] is not None) else None,
                        risk_reserve_pct=float(pf[5]) if (pf and pf[5] is not None) else None,
                    )
                    actual_profit = round(act_sell - res.total_costs_rub, 2)
                    actual_margin = round((actual_profit / act_sell) if act_sell else 0.0, 6)
                else:
                    actual_profit = float(actual_profit)
                    actual_margin = float(actual_margin)

                if actual_profit < float(_CFG.MIN_EXPECTED_PROFIT_RUB):
                    return jsonify({'ok': False, 'error': 'profit_below_minimum',
                                    'actual_profit_rub': actual_profit,
                                    'required_min': float(_CFG.MIN_EXPECTED_PROFIT_RUB)}), 412
                if actual_margin < float(_CFG.TARGET_NET_MARGIN):
                    return jsonify({'ok': False, 'error': 'margin_below_minimum',
                                    'actual_net_margin_pct': actual_margin,
                                    'required_min': float(_CFG.TARGET_NET_MARGIN)}), 412
                # окей — записываем пересчитанные actual_*
                conn.execute(
                    "UPDATE resale_deals SET actual_profit_rub=?, actual_net_margin_pct=? "
                    "WHERE deal_id=?", (actual_profit, actual_margin, did))

            now = datetime.utcnow().isoformat()
            clog2 = _append_changelog(clog, new_status,
                                      payload.get('who'), payload.get('note') or '')
            conn.execute(
                "UPDATE resale_deals SET status=?, changelog_json=?, updated_at=? WHERE deal_id=?",
                (new_status, clog2, now, did))
            conn.commit()
            return jsonify({'ok': True, 'deal_id': did,
                            'old_status': curst, 'new_status': new_status})
        finally:
            conn.close()
    except Exception as e:
        log.exception("/deals/<id>/status failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


# =============================================================================
#  ШАГ 12: Напоминания в интерфейсе (ТЫ ДУМАЙ АВТО, НИЧЕГО НЕ ОТПРАВЛЯЕТ)
# =============================================================================
@app.route('/api/reminders', methods=['GET'])
def api_reminders():
    if not _PARSER_DEPS_OK:
        return jsonify({'ok': False, 'error': 'parser_deps_missing'}), 503
    try:
        import sqlite3 as _s3
        from parser.db_init import get_db_path as _gdp
        conn = _s3.connect(_gdp(), timeout=15)
        conn.row_factory = _s3.Row
        reminders = []
        try:
            # 1. Approved parsed_products с source_price, не обновлённой > 12ч
            rows = conn.execute(
                """SELECT product_id, title, source_price, updated_at, status, seller_name
                     FROM parsed_products
                    WHERE status='approved'
                      AND (source_price IS NOT NULL AND source_price > 0)
                      AND julianday('now') - julianday(COALESCE(updated_at,created_at)) > 0.5
                """
            ).fetchall()
            for r in rows:
                reminders.append({
                    'type': 'approved_price_stale_over_12h',
                    'severity': 'warning',
                    'title': f"Товар одобрен, цена не обновлялась >12ч: {r['title'][:80]}",
                    'ref_type': 'product', 'ref_id': r['product_id'],
                    'product_id': r['product_id'],
                    'meta': {'source_price': r['source_price'], 'updated_at': r['updated_at']}
                })
            # 2-4. Сделки с "зависшими" статусами
            rules = [
                ('source_recheck',   1/24,  'deal_waiting_source_recheck_over_1h',  'warning'),
                ('buy_from_source',  2/24,  'deal_waiting_buy_over_2h',             'high'),
                ('deliver_to_buyer', 1/24,  'deal_waiting_delivery_over_1h',        'high'),
            ]
            for st, days_d, kind, sev in rules:
                rs = conn.execute(
                    f"""SELECT deal_id, status, product_id, buyer_order_id, seller_name,
                               updated_at, created_at
                          FROM resale_deals
                         WHERE status=?
                           AND julianday('now') - julianday(COALESCE(updated_at,created_at)) > ?
                    """, (st, days_d)).fetchall()
                for r in rs:
                    reminders.append({
                        'type': kind, 'severity': sev,
                        'title': f"Сделка #{r['deal_id']} в статусе '{st}' давно",
                        'ref_type': 'deal', 'ref_id': r['deal_id'],
                        'deal_id': r['deal_id'],
                        'meta': {'status': st, 'updated_at': r['updated_at'],
                                 'product_id': r['product_id'],
                                 'buyer_order_id': r['buyer_order_id']}
                    })
            # 5. Оплаченный заказ без сделки — заглушка (покупательские заказы вне БД -> нет)
            #    Эмулируем: если есть buyer_order_id в resale_deals NULL в какой-то внешней
            #    интеграции — тут оставляем место. Пока только заглушка по parsed_approved
            #    без deal:
            orphans = conn.execute(
                """SELECT p.product_id, p.title, p.updated_at
                     FROM parsed_products p
                    WHERE p.status='approved'
                      AND p.offer_id IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM resale_deals d
                                       WHERE d.product_id=p.product_id
                                         AND d.status NOT IN ('cancelled','refund','loss','completed'))
                """
            ).fetchall()
            for r in orphans[:50]:
                reminders.append({
                    'type': 'approved_product_without_active_deal',
                    'severity': 'info',
                    'title': f"Товар approved, нет активной сделки: {r['title'][:80]}",
                    'ref_type': 'product', 'ref_id': r['product_id'],
                    'product_id': r['product_id'],
                    'meta': {'updated_at': r['updated_at']}
                })
            return jsonify({'ok': True, 'count': len(reminders), 'items': reminders})
        finally:
            conn.close()
    except Exception as e:
        log.exception("/api/reminders failed")
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500





@app.route("/api/pipeline/status/<int:run_id>", methods=["GET"])
def api_pipeline_status(run_id: int):
    if not _PARSER_DEPS_OK:
        return jsonify({"ok": False, "error": "parser_deps_missing"}), 503
    import sqlite3 as _s3
    from parser.db_init import get_db_path as _gdp
    conn = _s3.connect(_gdp(), timeout=10)
    conn.row_factory = _s3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parser_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"ok": False, "error": "not_found"}), 404
    item = dict(row)
    return jsonify({"ok": True, "run_id": run_id, **item})




# ═══════════════════════════════════════════════════════════════════════════
#  API: AI MODERATION — ИИ отбирает лучшие товары из parsed_products
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/ai-moderate", methods=["POST"])
def api_ai_moderate():
    """
    Берёт спаршенные товары, отправляет батчем в Claude,
    получает AI-оценку (score 1-10, reason, recommendation) и сохраняет в БД.
    """
    import sqlite3 as _s3
    import json as _json
    import requests as _req
    from parser.db_init import get_db_path as _gdp
    from parser.db_init import _apply_migrations
    from datetime import datetime as _dt

    body = request.get_json(silent=True) or {}
    status_filter = body.get("status", "pending")   # "pending" | "all"
    limit = min(int(body.get("limit", 50)), 100)
    threshold = float(body.get("threshold", 6.0))   # автоодобрять >= этого

    db_path = _gdp()
    conn = _s3.connect(db_path, timeout=15)
    conn.row_factory = _s3.Row

    try:
        _apply_migrations(conn)
    except Exception:
        pass

    try:
        if status_filter == "all":
            rows = conn.execute(
                "SELECT product_id, title, original_desc, source_price, sell_price, "
                "expected_profit_rub, expected_net_margin_pct, category_id, rating, "
                "sales_count, seller_name, seller_rating, status "
                "FROM parsed_products ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT product_id, title, original_desc, source_price, sell_price, "
                "expected_profit_rub, expected_net_margin_pct, category_id, rating, "
                "sales_count, seller_name, seller_rating, status "
                "FROM parsed_products WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status_filter, limit)
            ).fetchall()
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 500

    if not rows:
        conn.close()
        return jsonify({"ok": True, "results": [], "total": 0, "message": "Нет товаров для оценки"})

    products_list = []
    for r in rows:
        products_list.append({
            "id": r["product_id"],
            "title": r["title"] or "",
            "desc": (r["original_desc"] or "")[:300],
            "source_price": r["source_price"],
            "sell_price": r["sell_price"],
            "profit_rub": r["expected_profit_rub"],
            "margin_pct": r["expected_net_margin_pct"],
            "rating": r["rating"],
            "sales_count": r["sales_count"],
            "seller_rating": r["seller_rating"],
        })

    products_json = _json.dumps(products_list, ensure_ascii=False)

    system_prompt = (
        "Ты — эксперт по перепродаже цифровых товаров на маркетплейсе ggsel.net. "
        "Твоя задача: оценить каждый товар и решить, стоит ли его публиковать для перепродажи. "
        "Критерии хорошего товара:\n"
        "- Популярный/узнаваемый продукт (Adobe, Microsoft, Figma, Steam, игры и т.п.)\n"
        "- Адекватная цена закупки и хорошая маржа (margin_pct > 25%)\n"
        "- Высокий рейтинг продавца (seller_rating > 4.0) или много продаж\n"
        "- Нет признаков мусорного/серого товара\n"
        "Для каждого товара верни JSON-массив объектов:\n"
        '[{"id": "<product_id>", "score": <1-10>, "reason": "<1-2 предложения по-русски>", "recommend": "approve"|"reject"}]\n'
        "ВАЖНО: верни ТОЛЬКО JSON-массив, без пояснений, без markdown, без ```."
    )

    user_prompt = "Оцени следующие {} товаров:\n{}".format(len(products_list), products_json)

    api_key_env = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key_env:
        conn.close()
        return jsonify({"ok": False, "error": "ANTHROPIC_API_KEY не задан в .env"}), 503

    try:
        claude_resp = _req.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key_env,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=60,
        )
        claude_data = claude_resp.json()
        if claude_resp.status_code != 200:
            conn.close()
            return jsonify({"ok": False, "error": "Claude API error {}".format(claude_resp.status_code), "detail": claude_data}), 502

        raw_text = ""
        for block in claude_data.get("content", []):
            if block.get("type") == "text":
                raw_text += block["text"]

        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        ai_results = _json.loads(raw_text)

    except _json.JSONDecodeError as e:
        conn.close()
        return jsonify({"ok": False, "error": "Claude вернул невалидный JSON: {}".format(e), "raw": raw_text[:500]}), 502
    except Exception as e:
        conn.close()
        return jsonify({"ok": False, "error": str(e)}), 502

    now = _dt.utcnow().isoformat()
    updated = 0
    approved_auto = 0
    rejected_auto = 0
    results_out = []

    for item in ai_results:
        pid = item.get("id")
        score = float(item.get("score", 0))
        reason = item.get("reason", "")
        recommend = item.get("recommend", "")

        new_status = "approved" if (recommend == "approve" or score >= threshold) else "rejected"
        if new_status == "approved":
            approved_auto += 1
        else:
            rejected_auto += 1

        try:
            conn.execute(
                "UPDATE parsed_products SET ai_score=?, ai_reason=?, ai_moderated_at=?, status=?, updated_at=? "
                "WHERE product_id=?",
                (score, reason, now, new_status, now, pid)
            )
            updated += 1
        except Exception as upd_err:
            log.warning("ai_moderate update failed for %s: %s", pid, upd_err)

        results_out.append({
            "product_id": pid,
            "score": score,
            "reason": reason,
            "recommend": recommend,
            "new_status": new_status,
        })

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "total": len(rows),
        "updated": updated,
        "approved_auto": approved_auto,
        "rejected_auto": rejected_auto,
        "threshold": threshold,
        "results": results_out,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  Категории: синхронизация с API
# ═══════════════════════════════════════════════════════════════════════════

def sync_categories():
    """
    Загружает категории из ggsel API, сравнивает с локальной БД,
    обновляет только изменившиеся записи.
    Возвращает dict с статистикой: {"added": N, "updated": N, "deleted": N}
    """
    from parser.db_init import get_db_path
    import sqlite3

    def fetch_all_from_api():
        """Загружает все категории рекурсивно из API."""
        def fetch_level(parent_id=None):
            params = {"limit": 200}
            if parent_id is not None:
                params["parent_id"] = parent_id
            code, data = v2_get("/api_sellers/v2/categories", params=params, headers={"locale": "ru"})
            if code != 200 or not data:
                return []
            return (data.get("data") or []) if isinstance(data, dict) else []

        def recurse(parent_id, depth, parent_title):
            items = fetch_level(parent_id)
            result = []
            for c in items:
                full_path = f"{parent_title} → {c['title']}" if parent_title else c["title"]
                result.append({
                    "id": c["id"],
                    "title": c["title"],
                    "full_path": full_path,
                    "depth": depth,
                    "parent_id": parent_id,
                    "content_type": c.get("content_type"),
                    "fee": c.get("fee"),
                    "has_children": c.get("has_children", False),
                })
                if c.get("has_children"):
                    result.extend(recurse(c["id"], depth + 1, full_path))
            return result

        try:
            return recurse(None, 0, "")
        except Exception as e:
            print(f"[sync_categories] API fetch error: {e}")
            return []

    try:
        # Загружаем из API
        api_items = fetch_all_from_api()
        if not api_items:
            print("[sync_categories] Пустой ответ от API")
            return {"added": 0, "updated": 0, "deleted": 0}

        # Загружаем из БД
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        cur = conn.cursor()
        cur.execute("SELECT id, title, parent_id, depth, full_path, content_type, fee, has_children FROM categories")
        db_items = {}
        for row in cur.fetchall():
            db_items[row[0]] = {
                "id": row[0],
                "title": row[1],
                "parent_id": row[2],
                "depth": row[3],
                "full_path": row[4],
                "content_type": row[5],
                "fee": row[6],
                "has_children": bool(row[7]),
            }

        # Если БД пуста - полная загрузка
        if not db_items:
            print("[sync_categories] БД пуста, полная загрузка...")
            conn.execute("DELETE FROM categories")
            for item in api_items:
                conn.execute("""
                    INSERT INTO categories (id, title, parent_id, depth, full_path, content_type, fee, has_children, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    item["id"], item["title"], item["parent_id"], item["depth"],
                    item["full_path"], item["content_type"], item["fee"],
                    1 if item["has_children"] else 0
                ))
            conn.commit()
            conn.close()
            print(f"[sync_categories] Загружено {len(api_items)} категорий")
            return {"added": len(api_items), "updated": 0, "deleted": 0}

        # Diff: находим новые, изменившиеся, удалённые
        api_dict = {item["id"]: item for item in api_items}
        db_ids = set(db_items.keys())
        api_ids = set(api_dict.keys())

        new_ids = api_ids - db_ids
        deleted_ids = db_ids - api_ids
        common_ids = db_ids & api_ids

        updated = 0
        for cid in common_ids:
            db = db_items[cid]
            api = api_dict[cid]
            # Проверяем изменения в важных полях
            if (db["title"] != api["title"] or
                db["parent_id"] != api["parent_id"] or
                db["depth"] != api["depth"] or
                db["full_path"] != api["full_path"] or
                db["content_type"] != api["content_type"] or
                db["fee"] != api["fee"] or
                db["has_children"] != api["has_children"]):
                conn.execute("""
                    UPDATE categories SET
                        title = ?, parent_id = ?, depth = ?, full_path = ?,
                        content_type = ?, fee = ?, has_children = ?, updated_at = datetime('now')
                    WHERE id = ?
                """, (
                    api["title"], api["parent_id"], api["depth"], api["full_path"],
                    api["content_type"], api["fee"], 1 if api["has_children"] else 0, cid
                ))
                updated += 1

        # Добавляем новые
        added = 0
        for cid in new_ids:
            item = api_dict[cid]
            conn.execute("""
                INSERT INTO categories (id, title, parent_id, depth, full_path, content_type, fee, has_children, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                item["id"], item["title"], item["parent_id"], item["depth"],
                item["full_path"], item["content_type"], item["fee"],
                1 if item["has_children"] else 0
            ))
            added += 1

        # Удаляем исчезнувшие
        deleted = 0
        for cid in deleted_ids:
            conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
            deleted += 1

        conn.commit()
        conn.close()

        # Инвалидация кэша
        if hasattr(api_categories_v2_tree, '_cache'):
            api_categories_v2_tree._cache = None
            api_categories_v2_tree._cache_ts = 0

        print(f"[sync_categories] +{added} новых, ~{updated} обновлено, -{deleted} удалено")
        return {"added": added, "updated": updated, "deleted": deleted}

    except Exception as e:
        print(f"[sync_categories] Ошибка: {e}")
        return {"added": 0, "updated": 0, "deleted": 0, "error": str(e)}


@app.route("/api/categories/sync", methods=["POST"])
def api_categories_sync():
    """
    Ручной запуск синхронизации категорий с API.
    Возвращает статистику изменений.
    """
    result = sync_categories()
    if "error" in result:
        return jsonify({"ok": False, "error": result["error"]}), 500
    return jsonify({"ok": True, **result})


def sync_fees():
    """
    Синхронизирует комиссии категорий (fee/commission) и записывает в cat_fees.json.
    """
    import json
    from datetime import datetime
    page = 1
    limit = 100
    fees_dict = {}
    
    while True:
        params = {"page": page, "limit": limit}
        code, data = v2_get("/api_sellers/v2/categories", params=params, headers={"locale": "ru"})
        if code != 200:
            break
        items = data.get("data") if isinstance(data, dict) else None
        if not items and isinstance(data, list):
            items = data
        if not items:
            break
            
        for cat in items:
            cat_id = cat.get("id")
            fee = cat.get("fee") or cat.get("commission")
            if cat_id is not None and fee is not None:
                try:
                    fee_val = float(fee)
                    if fee_val > 1.0:
                        fee_val = fee_val / 100.0
                    fees_dict[str(cat_id)] = fee_val
                except Exception:
                    pass
                    
        if len(items) < limit:
            break
        page += 1
        
    if fees_dict:
        fees_path = os.path.join(os.path.dirname(__file__), "cat_fees.json")
        try:
            with open(fees_path, "w", encoding="utf-8") as f:
                json.dump(fees_dict, f, ensure_ascii=False, indent=2)
            log.info(f"Синхронизировано {len(fees_dict)} комиссий в cat_fees.json")
        except Exception as e:
            log.warning(f"Ошибка сохранения cat_fees.json: {e}")
            
    return len(fees_dict), datetime.utcnow().isoformat()


@app.route("/api/categories/sync_fees", methods=["POST"])
def api_categories_sync_fees():
    count, ts = sync_fees()
    return jsonify({"ok": True, "count": count, "timestamp": ts})


def check_and_sync_fees_on_startup():
    import time
    fees_path = os.path.join(os.path.dirname(__file__), "cat_fees.json")
    should_sync = False
    if not os.path.exists(fees_path):
        should_sync = True
    else:
        mtime = os.path.getmtime(fees_path)
        if time.time() - mtime > 86400: # 24 часа
            should_sync = True
            
    if should_sync:
        log.info("cat_fees.json отсутствует или старше 24 часов, запускаем фоновую синхронизацию комиссий...")
        import threading
        threading.Thread(target=sync_fees, daemon=True, name="StartupFeesSync").start()


def start_categories_sync_thread():
    """
    Запускает синхронизацию категорий в фоновом треде при старте.
    Не блокирует запуск сервера.
    """
    import threading
    def worker():
        import time
        time.sleep(2)  # даём серверу запуститься
        print("[categories] Фоновая синхронизация категорий...")
        sync_categories()
    t = threading.Thread(target=worker, daemon=True)
    t.start()




# ==========================================
# STAGE 2: PROMO CODES (Cookie-based Auth)
# ==========================================
import json as _json

def _get_cookie_header():
    try:
        from cookie_status_routes import _get_cookies_path
        cookie_path = _get_cookies_path()
        if not cookie_path.exists():
            return ""
        with open(cookie_path, "r", encoding="utf-8") as f:
            cookies = _json.load(f)
        if isinstance(cookies, list):
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c])
        elif isinstance(cookies, dict):
            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        else:
            cookie_str = ""
        return cookie_str
    except Exception as e:
        app.logger.error(f"Error reading cookies: {e}")
        return ""

def _cookie_get(url, params=None):
    headers = {
        "Cookie": _get_cookie_header(),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 500, {"error": str(e)}

@app.route("/api/promo_codes", methods=["GET"])
def get_promo_codes():
    url = "https://seller.ggsel.com/api/v1/promo_codes"
    try:
        status, data = _cookie_get(url, params=request.args)
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes", params=request.args)
    except:
        status, data = _v1_get("/api/v1/promo_codes", params=request.args)
    return jsonify(data), status

@app.route("/api/promo_codes/filters/statuses", methods=["GET"])
def get_promo_codes_filters_statuses():
    try:
        status, data = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/statuses")
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes/filters/statuses")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500

@app.route("/api/promo_codes/filters/offers", methods=["GET"])
def get_promo_codes_filters_offers():
    try:
        status, data = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/offers")
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes/filters/offers")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500


# ==========================================
# STAGE 3: WHOLESALE (Cookie-based Auth)
# ==========================================
@app.route("/api/wholesale", methods=["GET"])
def get_wholesale():
    url = "https://seller.ggsel.com/api_sellers/api/wholesale"
    status, data = _cookie_get(url, params=request.args)
    return jsonify(data), status

@app.route("/api/wholesale/filters", methods=["GET"])
def get_wholesale_filters():
    url = "https://seller.ggsel.com/api_sellers/api/wholesale/filters"
    status, data = _cookie_get(url)
    return jsonify(data), status


# ==========================================
# STAGE 6: FINANCE (Ledger)
# ==========================================
@app.route("/api/ledger", methods=["GET"])
def get_ledger():
    url = "https://seller.ggsel.com/api/v1/ledger_items"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    
    # Fallback to receipts
    try:
        # We need to call the api_receipts logic, but it's another route. 
        # Let's just do v2_get("/api/v1/receipts") as receipts does
        c, d = v2_get("/api/v1/receipts", params=request.args)
        if c == 200:
            return jsonify(d), 200
    except:
        pass
        
    return jsonify({"ok": False, "stub": True, "items": []}), 401


# ==========================================
# STAGE 7: PROFILE
# ==========================================
@app.route("/api/profile", methods=["GET"])
def get_profile():
    url = "https://seller.ggsel.com/api_sellers/api/profile"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    # Stub fallback if cookie auth fails
    return jsonify({
        "ok": False,
        "stub": True,
        "data": {
            "email": "user@example.com (Stub)",
            "name": "GGsel Seller (Stub)",
            "wmz": "Z123456789012"
        },
        "error": "cookie_auth_required"
    }), 200

@app.route("/api/parser/logs")
def api_parser_logs():
    try:
        since_id = int(request.args.get("since_id", 0) or 0)
    except (TypeError, ValueError):
        since_id = 0
    try:
        limit = int(request.args.get("limit", 100) or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 1000))
    conn = None
    try:
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, level, message FROM parser_log WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
        logs = [{"id": r[0], "ts": r[1], "level": r[2], "message": r[3]} for r in rows]
        last_id = logs[-1]["id"] if logs else since_id
        return jsonify({"ok": True, "logs": logs, "last_id": last_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "logs": [], "last_id": since_id})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@app.route("/api/offer/<int:offer_id>/public_info", methods=["GET"])
def api_offer_public(offer_id):
    print(f"[DEBUG] api_offer_public called with offer_id={offer_id}", flush=True)
    try:
        status, data = _cookie_get(f"https://seller.ggsel.com/api/v1/offers/{offer_id}")
        print(f"[DEBUG] _cookie_get returned status={status}", flush=True)
        if status in [401, 404]:
            status, data = _v1_get(f"/api/v1/offers/{offer_id}")
            print(f"[DEBUG] _v1_get fallback returned status={status}", flush=True)
        return jsonify(data), status
    except Exception as e:
        print(f"[DEBUG] exception: {e}", flush=True)
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500

try:
    from flask_sock import Sock
    sock = Sock(app)

    @sock.route('/ws/chats')
    def ws_chats(ws):
        import websocket
        import threading
        import re

        try:
            client_msg = ws.receive()
        except:
            client_msg = ""

        token = client_msg if len(client_msg) > 20 else ""
        if not token:
            h = _get_cookie_header()
            m = re.search(r'ACCESS_TOKEN=([^;]+)', h)
            if m: token = m.group(1)

        try:
            upstream = websocket.create_connection(
                f"wss://wss.ggsel.com/cable?access_token={token}",
                timeout=10
            )
            def relay():
                try:
                    while True:
                        ws.send(upstream.recv())
                except: pass
            threading.Thread(target=relay, daemon=True).start()
            while True:
                upstream.send(ws.receive())
        except:
            pass
except Exception as _sock_err:
    log.warning(f"flask_sock не инициализирован: {_sock_err}")


if __name__ == "__main__":
    print("=" * 60)
    print(f"  GGselV7 — Seller Cabinet (unified)")
    print(f"  http://127.0.0.1:{LOCAL_PORT}")
    print(f"  Seller: {GGSEL_SELLER_ID}  Key: {GGSEL_API_KEY[:8]}…{GGSEL_API_KEY[-4:]}")
    print(f"  Base:   {BASE_URL}")
    print("=" * 60)

    start_categories_sync_thread()
    check_and_sync_fees_on_startup()

    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, host="127.0.0.1", port=LOCAL_PORT, use_reloader=debug_mode)
