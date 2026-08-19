"""tools/match_slugs_html.py — v2: full BFS of ggsel.net/catalog/* through MSB profile 95.
Собирает slug+title с каждого /catalog/<slug>, рекурсивно обходя детей.
Потом матчит с seller_categories.title и обновляет category_slug_mapping.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH = _ROOT / "data" / "db" / "parser.db"
log = logging.getLogger("ggselv7.match_slugs_html")

GG_L1_SEEDS = [
    "https://ggsel.net/catalog",
    "https://ggsel.net/catalog/igry-po-nazvaniyu",
    "https://ggsel.net/catalog/game-currency",
    "https://ggsel.net/catalog/mobile-games",
    "https://ggsel.net/catalog/podpisochnye-servisy",
    "https://ggsel.net/catalog/programs-new",
]


async def collect_html_slugs_full(cdp_url: str, max_depth: int = 4) -> dict[str, dict]:
    """BFS: открывает каждую /catalog/<slug>, собирает ссылки на дочерние /catalog/<slug>."""
    from playwright.async_api import async_playwright, Error as PlaywrightError

    seen: dict[str, dict] = {}  # slug -> {title, source_url, depth}
    queue: list[tuple[str, int]] = []  # (url, depth)
    skipped = 0

    for u in GG_L1_SEEDS:
        queue.append((u, 0))

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]
        page = await ctx.new_page()

        # Warm up — одна страница на весь BFS, просто открываем ggsel.net
        try:
            await page.goto("https://ggsel.net/", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("warmup warn: %s", e)
        await page.wait_for_timeout(3000)

        while queue:
            url, depth = queue.pop(0)
            if depth > max_depth:
                continue

            # Сброс страницы на about:blank перед каждым переходом
            # — это «успокаивает» браузер после chrome-error:// или прерванной навигации
            try:
                await page.goto("about:blank", wait_until="commit", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(300)

            ok = False
            for attempt in range(3):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    ok = True
                    break
                except PlaywrightError as e:
                    err = str(e)
                    log.warning("goto attempt %d/%d %s: %s", attempt + 1, 3, url.split("/")[-1], err[:100])
                    # Сбрасываем на about:blank и ждём перед retry
                    try:
                        await page.goto("about:blank", wait_until="commit", timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(2000 * (attempt + 1))
                except Exception as e:
                    log.warning("goto skip %s: %s", url.split("/")[-1], str(e)[:100])
                    break

            if not ok:
                skipped += 1
                log.warning("skipped (%d total): %s", skipped, url)
                continue

            # Ждём появления ссылок каталога (Next.js RSC догружает их асинхронно)
            try:
                await page.wait_for_selector('a[href*="/catalog/"]', timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            data = await page.evaluate("""
                () => {
                    const out = [];
                    const seenLocal = new Set();
                    for (const a of document.querySelectorAll('a[href*="/catalog/"]')) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/^\\/catalog\\/([a-z0-9-]+)\\/?$/);
                        if (!m) continue;
                        const slug = m[1];
                        if (slug === 'product' || seenLocal.has(slug)) continue;
                        seenLocal.add(slug);
                        const title = (a.innerText || '').trim();
                        if (!title || title.length > 80) continue;
                        out.push({slug, title});
                    }
                    return out;
                }
            """)
            new_in_page = 0
            for d in data:
                if d["slug"] in seen:
                    continue
                seen[d["slug"]] = {
                    "slug": d["slug"],
                    "title": d["title"],
                    "source_url": url,
                    "depth": depth,
                }
                new_in_page += 1
                # enqueue child for BFS
                if depth < max_depth:
                    child_url = f"https://ggsel.net/catalog/{d['slug']}"
                    queue.append((child_url, depth + 1))
            log.info("depth=%d %s → +%d (total %d, queue=%d)",
                     depth, url.split("/catalog/")[-1] or "root", new_in_page, len(seen), len(queue))
        await browser.close()
    log.info("BFS finished: total %d unique slugs", len(seen))
    return seen


def match_to_db(html_slugs: dict[str, dict]) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    cur = conn.cursor()
    matched = 0
    updated = 0

    title_index: dict[str, list[int]] = {}
    for sid, title in cur.execute("SELECT id, title FROM seller_categories WHERE title IS NOT NULL"):
        key = (title or "").strip().casefold()
        if key:
            title_index.setdefault(key, []).append(int(sid))

    contains_index = list(cur.execute(
        "SELECT title, id FROM seller_categories WHERE title IS NOT NULL AND LENGTH(title) > 3"
    ))

    for slug, info in html_slugs.items():
        title = (info.get("title") or "").strip()
        if not title:
            continue
        seller_ids = title_index.get(title.casefold())
        if not seller_ids:
            t_low = title.casefold()
            for s_title, s_id in contains_index:
                sl = s_title.casefold()
                if t_low in sl or sl in t_low:
                    seller_ids = [s_id]
                    break
        if not seller_ids:
            continue
        for sid in seller_ids:
            row = cur.execute(
                "SELECT match_score FROM category_slug_mapping WHERE seller_id = ? AND slug = ?",
                (sid, slug),
            ).fetchone()
            if row is not None:
                continue
            existing = cur.execute(
                "SELECT match_score FROM category_slug_mapping WHERE seller_id = ?",
                (sid,),
            ).fetchone()
            if existing is not None:
                if existing[0] < 0.7:
                    cur.execute(
                        "UPDATE category_slug_mapping SET slug = ?, match_score = 0.95 "
                        "WHERE seller_id = ? AND match_score < 0.7",
                        (slug, sid),
                    )
                    updated += 1
            else:
                cur.execute(
                    """INSERT INTO category_slug_mapping
                       (seller_id, slug, seller_title, seller_tree, match_score)
                       VALUES (?, ?, ?, ?, 0.95)""",
                    (sid, slug, title, title),
                )
                matched += 1
    conn.commit()
    conn.close()
    return {"matched": matched, "updated": updated, "total_slugs": len(html_slugs)}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:55992")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    log.info("=== match_slugs_html v2 BFS start ===")
    slugs = asyncio.run(collect_html_slugs_full(args.cdp, max_depth=args.max_depth))
    log.info("collected %d html slugs", len(slugs))
    if args.dry_run:
        for s, d in list(slugs.items())[:10]:
            print(f"  d={d['depth']}  {s:30} {d['title']!r}")
        return 0

    stats = match_to_db(slugs)
    log.info("match result: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
