# -*- coding: utf-8 -*-
"""
Прогон всех эндпоинтов V1 и V2 в обоих проектах для понимания реальной структуры данных.
Запускает запросы напрямую к API GGSEL, сохраняет результаты в JSON.
"""
import sys, os, json, time, hashlib
from pathlib import Path

# Принудительно UTF-8 для вывода в Windows-консоли
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Корень проекта GGselMSB
BASE_DIR = Path(__file__).resolve().parents[1]  # MSBWorkshop/GGselMSB/
sys.path.insert(0, str(BASE_DIR))

import requests
from config import GGSEL_API_KEY, GGSEL_SELLER_ID, BASE_URL

TIMEOUT = 30


def v1_token():
    ts = str(int(time.time()))
    sign = hashlib.sha256((GGSEL_API_KEY + ts).encode()).hexdigest()
    payload = {"seller_id": GGSEL_SELLER_ID, "timestamp": ts, "sign": sign}
    r = requests.post(
        f"{BASE_URL}/api_sellers/api/apilogin",
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    return r.json().get("token"), r.status_code


def call(label, method, path, *, headers=None, params=None, body=None, v2=False, token=None):
    h = {"Accept": "application/json"}
    if v2:
        h["Authorization"] = GGSEL_API_KEY
    else:
        if token:
            params = dict(params or {})
            params["token"] = token
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    try:
        r = requests.request(method, f"{BASE_URL}{path}", headers=h, params=params or {}, json=body, timeout=TIMEOUT)
        try:
            d = r.json()
        except Exception:
            d = {"raw_text": r.text[:600]}
        return {"label": label, "method": method, "path": path, "v2": v2, "status_code": r.status_code, "ok": r.status_code in (200, 201, 204, 400, 404, 422), "raw": d}
    except Exception as e:
        return {"label": label, "method": method, "path": path, "v2": v2, "status_code": 0, "ok": False, "raw": {"error": str(e)}}


def shape(label, d, prefix=""):
    """Печатает скелет структуры ответа: ключи, типы, длины массивов."""
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                print(f"{prefix}{k}: dict({len(v)} keys)")
            elif isinstance(v, list):
                print(f"{prefix}{k}: list[{len(v)}]")
                if v and isinstance(v[0], dict):
                    print(f"{prefix}  └─ first item keys: {list(v[0].keys())[:8]}")
            else:
                vstr = str(v)[:40]
                print(f"{prefix}{k}: {vstr}")
    elif isinstance(d, list):
        print(f"list[{len(d)}]")
        if d and isinstance(d[0], dict):
            print(f"  └─ first item keys: {list(d[0].keys())[:10]}")
    else:
        print(f"  value: {d}")


def main():
    print("=" * 60)
    print(f"GGSEL API PROBE  seller={GGSEL_SELLER_ID}")
    print("=" * 60)

    token, tcode = v1_token()
    print(f"Token: {bool(token)} ({tcode})")

    if not token:
        print("FAIL: токен не получен")
        return

    # ─── V1 ───
    v1_targets = [
        ("V1_balance", "GET", "/api_sellers/api/sellers/account/balance/info", None, None, None, False),
        ("V1_receipts", "GET", "/api_sellers/api/sellers/account/receipts", {"page": 1, "count": 5}, None, None, False),
        ("V1_categories", "GET", "/api_sellers/api/categories", {"page": 1, "count": 5}, {"lang": "ru-RU"}, None, False),
        ("V1_products_list", "GET", "/api_sellers/api/products/list", {"page": 1, "count": 5}, {"lang": "ru-RU"}, None, False),
        ("V1_seller_goods", "POST", "/api_sellers/api/seller-goods", None, None, {"id_seller": GGSEL_SELLER_ID, "order_col": "cntsell", "order_dir": "desc", "rows": 5, "page": 1, "currency": "RUB", "lang": "ru-RU", "show_hidden": 0}, False),
        ("V1_last_sales", "GET", "/api_sellers/api/seller-last-sales", {"seller_id": GGSEL_SELLER_ID, "top": 5}, {"locale": "ru"}, None, False),
        ("V1_reviews", "GET", "/api_sellers/api/reviews", {"page": 1, "count": 5, "type": "all"}, {"locale": "ru-RU"}, None, False),
        ("V1_chats", "GET", "/api_sellers/api/debates/v2/chats", {"page": 1, "pagesize": 5}, None, None, False),
    ]
    # ─── V2 ───
    v2_targets = [
        ("V2_categories", "GET", "/api_sellers/v2/categories", {"page": 1, "limit": 5}, {"locale": "ru"}, None, True),
        ("V2_offers", "GET", "/api_sellers/v2/offers", {"page": 1, "limit": 5}, {"locale": "ru"}, None, True),
    ]

    out = {}
    for label, method, path, params, headers, body, v2 in v1_targets + v2_targets:
        r = call(label, method, path, params=params, headers=headers, body=body, v2=v2, token=token)
        out[label] = r
        print(f"\n── {label} [{r['status_code']}] ──")
        shape(label, r["raw"])

    # Дополнительно: заказ, чат, оффер, опции — берём из первых элементов
    if out["V1_last_sales"].get("raw", {}).get("sales"):
        invoice_id = out["V1_last_sales"]["raw"]["sales"][0].get("invoice_id")
        if invoice_id:
            r = call("V1_order_info", "GET", f"/api_sellers/api/purchase/info/{invoice_id}", headers={"locale": "ru"}, token=token)
            out["V1_order_info"] = r
            print(f"\n── V1_order_info [{r['status_code']}] (invoice={invoice_id}) ──")
            shape("V1_order_info", r["raw"])

    if out["V1_chats"].get("raw", {}).get("items"):
        chat_id = out["V1_chats"]["raw"]["items"][0].get("id_i")
        if chat_id:
            r = call("V1_chat_messages", "GET", "/api_sellers/api/debates/v2", params={"id_i": chat_id, "count": 5}, token=token)
            out["V1_chat_messages"] = r
            print(f"\n── V1_chat_messages [{r['status_code']}] (chat={chat_id}) ──")
            shape("V1_chat_messages", r["raw"])

    if out["V2_offers"].get("raw", {}).get("data"):
        offer_id = out["V2_offers"]["raw"]["data"][0].get("id")
        if offer_id:
            r = call("V2_offer_get", "GET", f"/api_sellers/v2/offers/{offer_id}", headers={"locale": "ru"}, v2=True)
            out["V2_offer_get"] = r
            print(f"\n── V2_offer_get [{r['status_code']}] (offer={offer_id}) ──")
            shape("V2_offer_get", r["raw"])

            r = call("V2_offer_options", "GET", f"/api_sellers/v2/offers/{offer_id}/options", headers={"locale": "ru"}, v2=True)
            out["V2_offer_options"] = r
            print(f"\n── V2_offer_options [{r['status_code']}] ──")
            shape("V2_offer_options", r["raw"])

            r = call("V2_offer_products", "GET", f"/api_sellers/v2/offers/{offer_id}/products", params={"status": "in_stock"}, headers={"locale": "ru"}, v2=True)
            out["V2_offer_products"] = r
            print(f"\n── V2_offer_products [{r['status_code']}] ──")
            shape("V2_offer_products", r["raw"])

    # Сохраняем полный результат
    out_path = Path(__file__).resolve().parents[1] / "tests" / "probe_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\nРезультат сохранён: {out_path}")


if __name__ == "__main__":
    main()
