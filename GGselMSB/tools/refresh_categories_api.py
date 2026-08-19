"""tools/refresh_categories_api.py
===================================
Рекурсивно обходит GGSEL Seller API и обновляет seller_categories в БД.
Использует GGSEL_API_KEY напрямую — CDP и MSB не нужны.

GET /api_sellers/v2/categories              → корневые категории
GET /api_sellers/v2/categories?parent_id=X  → дети категории X

Запуск:
  python tools/refresh_categories_api.py
  python tools/refresh_categories_api.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from config import GGSEL_API_KEY
from parser.db_init import get_db_path

log = logging.getLogger("refresh_categories_api")

BASE = "https://seller.ggsel.com"
HEADERS = {"Authorization": GGSEL_API_KEY, "locale": "ru", "Accept": "application/json"}


def fetch_children(parent_id=None, retries=3) -> list[dict]:
    """GET /api_sellers/v2/categories[?parent_id=X] — все дочерние категории."""
    params = {"limit": 200, "page": 1}
    if parent_id is not None:
        params["parent_id"] = parent_id
    for attempt in range(retries):
        try:
            r = requests.get(
                f"{BASE}/api_sellers/v2/categories",
                headers=HEADERS,
                params=params,
                timeout=20,
            )
            if r.status_code == 429:
                wait = 10 + attempt * 10
                log.warning("429 parent_id=%s — wait %ds", parent_id, wait)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log.warning("HTTP %d parent_id=%s", r.status_code, parent_id)
                return []
            data = r.json()
            items = data.get("data", [])
            return items if isinstance(items, list) else []
        except Exception as e:
            log.warning("err parent_id=%s attempt=%d: %s", parent_id, attempt, e)
            time.sleep(2 + attempt * 2)
    return []


PARALLEL_WORKERS = 20   # параллельных запросов одновременно


def _node_from_item(it: dict) -> dict:
    digi = it.get("ggsel_digi_catalog")
    return {
        "id":                         int(it["id"]),
        "title":                      (it.get("title") or "").strip(),
        "tree":                       it.get("title_with_ancestors") or it.get("title") or "",
        "content_type":               it.get("content_type") or "",
        "fee":                        float(it["fee"]) if it.get("fee") is not None else 0.15,
        "has_children":               1 if it.get("has_children") else 0,
        "ggsel_digi_catalog":         int(digi) if digi is not None else None,
        "ancestor_ids":               json.dumps(list(it.get("ancestor_ids") or []), ensure_ascii=False),
        "default_payment_system_fee": float(it["default_payment_system_fee"])
                                      if it.get("default_payment_system_fee") is not None else None,
        "text_for_sellers":           it.get("text_for_sellers"),
        "unit":                       json.dumps(it.get("unit"), ensure_ascii=False) if it.get("unit") else None,
        "depth":                      int(it.get("depth") or 0),
        "path":                       it.get("title_with_ancestors") or "",
    }


def collect_all() -> dict[int, dict]:
    """
    BFS с параллельными запросами (до 20 одновременно).
    Каждый уровень BFS обрабатывается батчами parent_id.
    """
    all_nodes: dict[int, dict] = {}
    visited: set = set()

    # Начинаем с корня (None)
    current_level: list = [None]

    while current_level:
        next_level: list = []
        # Фильтруем уже посещённые
        to_fetch = [p for p in current_level if p not in visited]
        for p in to_fetch:
            visited.add(p)

        if not to_fetch:
            break

        # Параллельно получаем детей всех parent_id текущего уровня
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            futures = {ex.submit(fetch_children, pid): pid for pid in to_fetch}
            for fut in as_completed(futures):
                pid = futures[fut]
                items = fut.result()
                new_children = 0
                for it in items:
                    nid = int(it["id"])
                    if nid in all_nodes:
                        continue
                    all_nodes[nid] = _node_from_item(it)
                    new_children += 1
                    if it.get("has_children"):
                        next_level.append(nid)
                log.info("parent_id=%-10s  дочерних=%-4d  новых=%-4d  всего=%d",
                         pid, len(items), new_children, len(all_nodes))

        log.info("--- уровень завершён: обработано %d parent_id, на следующем %d ---",
                 len(to_fetch), len(next_level))
        current_level = next_level

    return all_nodes


def save_to_db(nodes: dict[int, dict], dry_run: bool = False) -> dict:
    conn = sqlite3.connect(get_db_path(), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")

    # Убедимся что все нужные колонки есть
    have = {r[1] for r in conn.execute("PRAGMA table_info(seller_categories)").fetchall()}
    for col, typ in [
        ("ggsel_digi_catalog", "INTEGER"),
        ("ancestor_ids",       "TEXT"),
        ("unit",               "TEXT"),
        ("default_payment_system_fee", "REAL"),
        ("text_for_sellers",   "TEXT"),
        ("depth",              "INTEGER"),
        ("path",               "TEXT"),
    ]:
        if col not in have:
            conn.execute(f"ALTER TABLE seller_categories ADD COLUMN {col} {typ}")
            log.info("ALTER TABLE seller_categories ADD COLUMN %s %s", col, typ)
    conn.commit()

    inserted = updated = 0
    for nid, n in nodes.items():
        row = conn.execute("SELECT id FROM seller_categories WHERE id=?", (nid,)).fetchone()
        if dry_run:
            if not row:
                inserted += 1
            else:
                updated += 1
            continue

        if not row:
            conn.execute("""
                INSERT INTO seller_categories
                  (id, title, tree, content_type, fee, has_children,
                   ggsel_digi_catalog, ancestor_ids, default_payment_system_fee,
                   text_for_sellers, unit, depth, path)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                nid, n["title"], n["tree"], n["content_type"], n["fee"],
                n["has_children"], n["ggsel_digi_catalog"], n["ancestor_ids"],
                n["default_payment_system_fee"], n["text_for_sellers"],
                n["unit"], n["depth"], n["path"],
            ))
            inserted += 1
        else:
            conn.execute("""
                UPDATE seller_categories
                SET title=?, tree=?, content_type=?, fee=?, has_children=?,
                    ggsel_digi_catalog=?, ancestor_ids=?, default_payment_system_fee=?,
                    text_for_sellers=?, unit=?, depth=?, path=?
                WHERE id=?
            """, (
                n["title"], n["tree"], n["content_type"], n["fee"],
                n["has_children"], n["ggsel_digi_catalog"], n["ancestor_ids"],
                n["default_payment_system_fee"], n["text_for_sellers"],
                n["unit"], n["depth"], n["path"], nid,
            ))
            updated += 1

        if (inserted + updated) % 200 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": len(nodes)}


def main():
    ap = argparse.ArgumentParser(description="Обновление seller_categories через GGSEL API")
    ap.add_argument("--dry-run", action="store_true", help="Не писать в БД")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(_ROOT / "logs" / "refresh_categories_api.log",
                                encoding="utf-8", mode="a"),
        ],
    )

    log.info("=== refresh_categories_api start (dry_run=%s) ===", args.dry_run)
    log.info("API key: %s...%s", GGSEL_API_KEY[:8], GGSEL_API_KEY[-4:])

    nodes = collect_all()
    log.info("Собрано категорий: %d", len(nodes))

    stats = save_to_db(nodes, dry_run=args.dry_run)
    log.info("БД: inserted=%d  updated=%d  total=%d", stats["inserted"], stats["updated"], stats["total"])
    log.info("=== done ===")


if __name__ == "__main__":
    main()
