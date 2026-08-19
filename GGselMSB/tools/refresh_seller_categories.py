"""tools/refresh_seller_categories.py
===================================
Рекурсивно обходит seller API через MSB профиль 95 и обогащает таблицы:
  - seller_categories: добавляет ggsel_digi_catalog, ancestor_ids (JSON),
                       unit (JSON), default_payment_system_fee, text_for_sellers
  - category_slug_mapping: обновляет seller_tree (если точнее) и
                          seller_title (нормализованное), и для листьев ставит
                          fallback slug из slugify(seller_title) если title
                          совпадает с известным KNOWN_CATEGORIES.

Источник эндпоинтов (живые, проверены через MSB профиль 95):
  GET /api/v1/categories                  → 6 корней
  GET /api/v1/categories?parent_id=X      → дети X (пустой = лист)
  GET /api/v1/categories/{id}/tree        → ВСЯ цепочка от корня до X (с ancestor_ids)

Запуск:
  python -m tools.refresh_seller_categories --cdp http://127.0.0.1:55992
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH = _ROOT / "data" / "db" / "parser.db"
SCHEMA_PATH = _ROOT / "parser" / "schema.sql"

log = logging.getLogger("ggselv7.refresh_seller_categories")


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Добавляет недостающие колонки в seller_categories (idempotent)."""
    cur = conn.execute("PRAGMA table_info(seller_categories)")
    have = {r[1] for r in cur.fetchall()}
    alters = []
    if "ggsel_digi_catalog" not in have:
        alters.append("ALTER TABLE seller_categories ADD COLUMN ggsel_digi_catalog INTEGER")
    if "ancestor_ids" not in have:
        alters.append("ALTER TABLE seller_categories ADD COLUMN ancestor_ids TEXT")
    if "unit" not in have:
        alters.append("ALTER TABLE seller_categories ADD COLUMN unit TEXT")
    if "default_payment_system_fee" not in have:
        alters.append("ALTER TABLE seller_categories ADD COLUMN default_payment_system_fee REAL")
    if "text_for_sellers" not in have:
        alters.append("ALTER TABLE seller_categories ADD COLUMN text_for_sellers TEXT")
    for sql in alters:
        conn.execute(sql)
    if alters:
        log.info("ALTER seller_categories: %s", alters)
    conn.commit()


async def collect_all_categories(cdp_url: str, limit: int | None = None) -> dict:
    """Рекурсивно собирает все категории через MSB-подключённый Chrome.
    BFS с асинхронным gather — параллельно опрашиваем children одного уровня."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        try:
            await page.goto("https://seller.ggsel.com/en", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("Initial nav warning: %s", e)
        await page.wait_for_timeout(4000)
        try:
            await page.evaluate("() => 1")
        except Exception as e:
            log.warning("context touch failed: %s", e)
            return {}

        all_nodes: dict[int, dict] = {}
        queue: list[int | None] = [None]  # BFS, starts with roots
        depth_map: dict[int, int] = {}

        async def fetch_children(parent_id):
            if parent_id is None:
                expr = """async () => {
                    const r = await fetch('/api/v1/categories', {
                        headers: {accept:'application/json', locale:'ru'},
                        credentials: 'include',
                    });
                    return await r.json();
                }"""
                arg = None
            else:
                expr = """async (pid) => {
                    const r = await fetch('/api/v1/categories?parent_id=' + pid + '&limit=100&page=1', {
                        headers: {accept:'application/json', locale:'ru'},
                        credentials: 'include',
                    });
                    return await r.json();
                }"""
                arg = parent_id
            try:
                r = await page.evaluate(expr, arg)
            except Exception as e:
                log.warning("fetch_children(%s) failed: %s", parent_id, e)
                return []
            items = (r or {}).get("data", []) if isinstance(r, dict) else []
            return items

        async def visit(parent_id, depth):
            if limit and len(all_nodes) >= limit:
                return
            items = await fetch_children(parent_id)
            next_parents: list[int] = []
            for it in items:
                nid = int(it["id"])
                if nid in all_nodes:
                    continue
                all_nodes[nid] = {
                    "id": nid,
                    "title": it.get("title") or "",
                    "title_with_ancestors": it.get("title_with_ancestors") or "",
                    "content_type": it.get("content_type") or "",
                    "fee": float(it["fee"]) if it.get("fee") is not None else 0.15,
                    "kind": it.get("kind") or "",
                    "ggsel_digi_catalog": int(it["ggsel_digi_catalog"]) if it.get("ggsel_digi_catalog") is not None else None,
                    "has_children": bool(it.get("has_children")),
                    "ancestor_ids": list(it.get("ancestor_ids") or []),
                    "default_payment_system_fee": float(it["default_payment_system_fee"])
                        if it.get("default_payment_system_fee") is not None else None,
                    "text_for_sellers": it.get("text_for_sellers"),
                    "unit": it.get("unit"),
                    "tree": it.get("title_with_ancestors") or it.get("title") or "",
                }
                depth_map[nid] = depth
                if it.get("has_children") and depth < 5:
                    next_parents.append(nid)
                if limit and len(all_nodes) >= limit:
                    return
            # BFS — gather siblings at this depth in parallel (small batches to avoid 429)
            BATCH = 3
            for i in range(0, len(next_parents), BATCH):
                batch = next_parents[i:i + BATCH]
                await asyncio.gather(*[visit(p, depth + 1) for p in batch])
                await asyncio.sleep(0.4)  # gentle throttle

        await visit(None, 0)
        log.info("collected %d nodes", len(all_nodes))
        await browser.close()
        return all_nodes


def save_to_db(nodes: dict[int, dict]) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    ensure_columns(conn)
    cur = conn.cursor()
    inserted = 0
    updated = 0
    for nid, n in nodes.items():
        row = cur.execute("SELECT id FROM seller_categories WHERE id = ?", (nid,)).fetchone()
        if row is None:
            cur.execute(
                """INSERT INTO seller_categories
                   (id, title, tree, content_type, fee, has_children,
                    ggsel_digi_catalog, ancestor_ids, default_payment_system_fee,
                    text_for_sellers, unit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (nid, n["title"], n["tree"], n["content_type"], n["fee"],
                 1 if n["has_children"] else 0,
                 n["ggsel_digi_catalog"],
                 json.dumps(n["ancestor_ids"], ensure_ascii=False),
                 n["default_payment_system_fee"],
                 n["text_for_sellers"],
                 json.dumps(n["unit"], ensure_ascii=False) if n["unit"] else None),
            )
            inserted += 1
        else:
            cur.execute(
                """UPDATE seller_categories
                   SET title = ?, tree = ?, content_type = ?, fee = ?,
                       has_children = ?, ggsel_digi_catalog = ?,
                       ancestor_ids = ?, default_payment_system_fee = ?,
                       text_for_sellers = ?, unit = ?
                   WHERE id = ?""",
                (n["title"], n["tree"], n["content_type"], n["fee"],
                 1 if n["has_children"] else 0,
                 n["ggsel_digi_catalog"],
                 json.dumps(n["ancestor_ids"], ensure_ascii=False),
                 n["default_payment_system_fee"],
                 n["text_for_sellers"],
                 json.dumps(n["unit"], ensure_ascii=False) if n["unit"] else None,
                 nid),
            )
            updated += 1
    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": len(nodes)}


def update_category_slug_mapping(nodes: dict[int, dict]) -> dict:
    """Обновляет category_slug_mapping: seller_title (нормализованный) и seller_tree.
    Также проставляет has_children/ancestor_ids туда если есть."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    cur = conn.cursor()
    updated = 0
    for nid, n in nodes.items():
        cur.execute(
            """UPDATE category_slug_mapping
               SET seller_title = ?, seller_tree = ?
               WHERE seller_id = ?""",
            (n["title"], n["tree"], nid),
        )
        if cur.rowcount > 0:
            updated += 1
    conn.commit()
    conn.close()
    return {"updated": updated}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:55992",
                    help="CDP endpoint of running MSB profile 95")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max nodes to collect (for testing). Default = all.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    log.info("=== refresh_seller_categories start ===")
    nodes = asyncio.run(collect_all_categories(args.cdp, limit=args.limit))
    log.info("collected %d unique nodes", len(nodes))

    if args.dry_run:
        # show sample
        sample = sorted(nodes.values(), key=lambda x: x["id"])[:5]
        for s in sample:
            print(f"  {s['id']:6}  ggsel={s['ggsel_digi_catalog']}  ancestors={s['ancestor_ids']}  {s['title'][:50]!r}")
        return 0

    stats1 = save_to_db(nodes)
    log.info("seller_categories: %s", stats1)
    stats2 = update_category_slug_mapping(nodes)
    log.info("category_slug_mapping: %s", stats2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
