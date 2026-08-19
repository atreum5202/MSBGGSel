"""
parser/category_stats.py
Сканирует все категории ggsel.net и сохраняет total в таблицу category_stats.

Алгоритм:
1. Собирает slug из HTML /catalog (меню + подкатегории)
2. Для каждого slug GET /catalog/SLUG, regex "total" из RSC payload
3. Сохраняет в SQLite таблицу category_stats
4. Поддерживает инкрементное обновление (upsert по slug)
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional

from parser.db_init import get_db_path

log = logging.getLogger("ggselv7.category_stats")

_BASE_URL = "https://ggsel.net"
_DELAY_SEC = 0.4
_IMPERSONATE = "chrome131"

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

_scan_state: dict = {
    "running": False,
    "progress": 0,
    "total_slugs": 0,
    "saved": 0,
    "last_error": None,
    "started_at": None,
    "finished_at": None,
    "grand_total": 0,
}
_scan_lock = threading.Lock()


# ─── DB ───────────────────────────────────────────────────────────────────────

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS category_stats (
            slug        TEXT PRIMARY KEY,
            title       TEXT,
            url         TEXT,
            total       INTEGER,
            http_status INTEGER,
            parent_slug TEXT,
            scanned_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_category_stats_total "
        "ON category_stats(total DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_category_stats_parent "
        "ON category_stats(parent_slug)"
    )
    conn.commit()


def _upsert(conn: sqlite3.Connection, slug: str, title: Optional[str],
            url: str, total: Optional[int], http_status: int,
            parent_slug: Optional[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO category_stats (slug, title, url, total, http_status, parent_slug, scanned_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title       = excluded.title,
            url         = excluded.url,
            total       = excluded.total,
            http_status = excluded.http_status,
            parent_slug = excluded.parent_slug,
            scanned_at  = excluded.scanned_at,
            updated_at  = excluded.updated_at
    """, (slug, title, url, total, http_status, parent_slug, now, now))


# ─── HTML helpers ─────────────────────────────────────────────────────────────

def _extract_total(html: str) -> Optional[int]:
    m = re.search(r'"total"\s*:\s*(\d+)\s*\}[^}]{0,50}"dataUpdateCount"', html, re.DOTALL)
    if m:
        return int(m.group(1))
    m = re.search(r'"total"\s*:\s*(\d+)', html)
    if m:
        return int(m.group(1))
    return None


def _extract_title(html: str) -> Optional[str]:
    m = re.search(r'<h1[^>]*>\s*([^<]{2,120})\s*</h1>', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'<title>\s*([^<]{2,200})\s*</title>', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _collect_slugs(session, headers: dict) -> dict[str, dict]:
    """
    Собирает все slug каталога ggsel.net.

    Шаг 1: GET /catalog → все href=/catalog/SLUG (top-level меню).
    Шаг 2: для каждого top-level GET /catalog/SLUG → все href=/catalog/ANY_SLUG
            (ggsel использует плоские slug, не иерархические /parent/child).
    Возвращает {slug: {title, parent_slug}}.
    """
    result: dict[str, dict] = {}
    # Исключаем служебные slug которые не являются категориями
    _SKIP_SLUGS = {"product", "search", "filter", "sort", "page", "tag", "all"}
    # Учитываем языковый префикс: /en/catalog/, /ru/catalog/, /catalog/
    _pat = re.compile(r'href="(?:https?://ggsel\.net)?(?:/[a-z]{2})?/catalog/([A-Za-z0-9][A-Za-z0-9_-]*)"')

    # Шаг 1: главная каталога
    try:
        resp = session.get(f"{_BASE_URL}/catalog", headers=headers, timeout=20)
        catalog_html = resp.text
    except Exception as exc:
        log.warning("_collect_slugs: /catalog fetch failed: %s", exc)
        return result

    top_slugs: list[str] = []
    for m in _pat.finditer(catalog_html):
        slug = m.group(1)
        if slug and slug not in result and slug not in _SKIP_SLUGS:
            result[slug] = {"title": None, "parent_slug": None}
            top_slugs.append(slug)

    log.info("_collect_slugs: %d top-level slugs", len(top_slugs))

    # Шаг 2: подкатегории со страниц каждой top-level категории
    for top_slug in top_slugs:
        time.sleep(_DELAY_SEC)
        try:
            resp = session.get(f"{_BASE_URL}/catalog/{top_slug}", headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            page_html = resp.text
        except Exception as exc:
            log.warning("_collect_slugs: /catalog/%s failed: %s", top_slug, exc)
            continue

        # Обновляем title top-level
        title = _extract_title(page_html)
        if title:
            result[top_slug]["title"] = title

        # Все slug со страницы — это подкатегории
        for m in _pat.finditer(page_html):
            slug = m.group(1)
            if slug and slug not in result and slug != top_slug and slug not in _SKIP_SLUGS:
                result[slug] = {"title": None, "parent_slug": top_slug}

    log.info("_collect_slugs: total %d slugs", len(result))
    return result


# ─── Main scan ────────────────────────────────────────────────────────────────

def scan_all_categories(profile_id: Optional[str] = None) -> dict:
    from curl_cffi import requests as cffi

    t0 = time.monotonic()
    headers = dict(_DEFAULT_HEADERS)

    if profile_id:
        try:
            import asyncio
            import httpx as _httpx
            from parser.cdp_cookies import get_cookies_via_cdp

            async def _get_cookies():
                # Получаем статус профиля напрямую из MSB API
                r = _httpx.get(
                    f"http://127.0.0.1:17248/profiles/{profile_id}/status",
                    timeout=5,
                )
                data = r.json().get("data", {})
                port = data.get("debugPort") or data.get("cdpPort") or data.get("port")
                if not port:
                    # Профиль не запущен — запускаем
                    from parser.msb_client import MsbClient
                    async with MsbClient() as cl:
                        info = await cl.start_profile(profile_id, launchMode="visible")
                        port = info.get("debugPort")
                if not port:
                    return {}
                return await get_cookies_via_cdp(int(port), "ggsel.net")

            cookies_dict = asyncio.run(_get_cookies())
            if cookies_dict:
                headers["Cookie"] = "; ".join(
                    f"{k}={v}" for k, v in cookies_dict.items()
                )
                log.info("scan_all_categories: loaded %d cookies via CDP port", len(cookies_dict))
        except Exception as exc:
            log.warning("scan_all_categories: CDP cookie fetch failed: %s", exc)

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    ensure_table(conn)

    with cffi.Session(impersonate=_IMPERSONATE) as session:
        slugs_meta = _collect_slugs(session, headers)

        total_slugs = len(slugs_meta)
        saved = 0
        grand_total = 0

        with _scan_lock:
            _scan_state["total_slugs"] = total_slugs
            _scan_state["progress"] = 0
            _scan_state["saved"] = 0
            _scan_state["grand_total"] = 0

        for idx, (slug, meta) in enumerate(slugs_meta.items(), start=1):
            url = f"{_BASE_URL}/catalog/{slug}"
            http_status = 0
            total = None
            title = meta.get("title")
            parent_slug = meta.get("parent_slug")

            try:
                resp = session.get(url, headers=headers, timeout=20)
                http_status = resp.status_code
                if http_status == 200:
                    html = resp.text
                    total = _extract_total(html)
                    if title is None:
                        title = _extract_title(html)
                    if total is not None:
                        grand_total += total
            except Exception as exc:
                log.warning("scan_all_categories: error fetching %s: %s", slug, exc)

            try:
                _upsert(conn, slug, title, url, total, http_status, parent_slug)
                conn.commit()
                saved += 1
            except Exception as exc:
                log.warning("scan_all_categories: upsert failed for %s: %s", slug, exc)

            with _scan_lock:
                _scan_state["progress"] = idx
                _scan_state["saved"] = saved
                _scan_state["grand_total"] = grand_total

            log.debug("scan_all_categories: %d/%d slug=%s total=%s", idx, total_slugs, slug, total)

            if idx < total_slugs:
                time.sleep(_DELAY_SEC)

    conn.close()
    elapsed = round(time.monotonic() - t0, 1)
    log.info(
        "scan_all_categories: done. scanned=%d saved=%d grand_total=%d elapsed=%.1fs",
        total_slugs, saved, grand_total, elapsed,
    )
    return {
        "ok": True,
        "scanned": total_slugs,
        "saved": saved,
        "grand_total": grand_total,
        "elapsed_sec": elapsed,
    }


# ─── Background runner ────────────────────────────────────────────────────────

def _run_background(profile_id: Optional[str]) -> None:
    with _scan_lock:
        _scan_state["running"] = True
        _scan_state["last_error"] = None
        _scan_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _scan_state["finished_at"] = None
        _scan_state["progress"] = 0
        _scan_state["saved"] = 0
        _scan_state["grand_total"] = 0

    try:
        result = scan_all_categories(profile_id=profile_id)
        with _scan_lock:
            _scan_state["grand_total"] = result.get("grand_total", 0)
    except Exception as exc:
        log.exception("scan_all_categories background error: %s", exc)
        with _scan_lock:
            _scan_state["last_error"] = str(exc)
    finally:
        with _scan_lock:
            _scan_state["running"] = False
            _scan_state["finished_at"] = datetime.now(timezone.utc).isoformat()


def start_scan_background(profile_id: Optional[str] = None) -> dict:
    with _scan_lock:
        if _scan_state["running"]:
            return {"ok": False, "message": "Скан уже запущен"}

    t = threading.Thread(target=_run_background, args=(profile_id,), daemon=True)
    t.start()
    return {"ok": True, "message": "Скан запущен в фоне"}


def get_scan_status() -> dict:
    with _scan_lock:
        return deepcopy(_scan_state)


# ─── Read helpers ─────────────────────────────────────────────────────────────

def get_stats() -> list[dict]:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    rows = conn.execute(
        "SELECT slug, title, url, total, http_status, parent_slug, scanned_at, updated_at "
        "FROM category_stats ORDER BY total DESC NULLS LAST"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary() -> dict:
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)

    row = conn.execute(
        "SELECT COUNT(*) AS cnt, SUM(total) AS grand, MAX(scanned_at) AS last_scan "
        "FROM category_stats WHERE total IS NOT NULL"
    ).fetchone()

    top_rows = conn.execute(
        "SELECT slug, title, total FROM category_stats "
        "WHERE total IS NOT NULL ORDER BY total DESC LIMIT 15"
    ).fetchall()
    conn.close()

    return {
        "grand_total": row["grand"] or 0,
        "categories_count": row["cnt"] or 0,
        "top_categories": [dict(r) for r in top_rows],
        "last_scan_at": row["last_scan"],
    }
