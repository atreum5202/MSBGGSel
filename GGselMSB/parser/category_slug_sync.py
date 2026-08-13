"""
parser/category_slug_sync.py
=============================
Синхронизация slug категорий ggsel.net с их числовыми ID и комиссиями.

Алгоритм:
  1. Парсим HTML /catalog через curl_cffi + Qrator куки → все slug + title
  2. Получаем все категории из Seller API V2 → id + title + fee
  3. Матчим по title (нормализованному) → slug ↔ id ↔ fee
  4. Сохраняем в таблицу category_slugs в parser.db
  5. При повторном запуске — обновляем только изменившееся

Запуск:
  python -m parser.category_slug_sync
  или из кода: from parser.category_slug_sync import sync_category_slugs; sync_category_slugs()
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .db_init import get_db_path

log = logging.getLogger("ggselv7.category_slug_sync")

BASE_PUBLIC = "https://ggsel.net"
BASE_SELLER = "https://seller.ggsel.com"


# ─── DB ───────────────────────────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS category_slugs (
            id          INTEGER PRIMARY KEY,
            slug        TEXT NOT NULL,
            title       TEXT,
            fee         REAL,
            full_path   TEXT,
            has_children INTEGER DEFAULT 0,
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_slugs_slug ON category_slugs(slug)")
    conn.commit()


def get_slug_by_id(category_id: int) -> Optional[str]:
    """Быстрый lookup slug по числовому ID категории."""
    try:
        conn = sqlite3.connect(get_db_path(), timeout=5)
        row = conn.execute(
            "SELECT slug FROM category_slugs WHERE id = ?", (category_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def get_id_by_slug(slug: str) -> Optional[int]:
    """Быстрый lookup ID по slug."""
    try:
        conn = sqlite3.connect(get_db_path(), timeout=5)
        row = conn.execute(
            "SELECT id FROM category_slugs WHERE slug = ?", (slug,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def get_fee_by_slug(slug: str) -> Optional[float]:
    """Получить комиссию категории по slug."""
    try:
        conn = sqlite3.connect(get_db_path(), timeout=5)
        row = conn.execute(
            "SELECT fee FROM category_slugs WHERE slug = ?", (slug,)
        ).fetchone()
        conn.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def get_all_slugs() -> list[dict]:
    """Все записи из category_slugs для автопилота."""
    try:
        conn = sqlite3.connect(get_db_path(), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, slug, title, fee, full_path FROM category_slugs ORDER BY slug"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ─── Step 1: Парсинг HTML каталога → slug + title ─────────────────────────────

def _fetch_catalog_slugs_from_html() -> list[dict]:
    """
    Открывает ggsel.net/catalog через curl_cffi + Qrator куки,
    извлекает все ссылки вида /catalog/<slug>.
    Возвращает [{"slug": ..., "title": ...}, ...]
    """
    import asyncio
    from curl_cffi import requests as cffi

    async def _get_cookies():
        from .msb_cookies import QratorCookieMiddleware
        mw = QratorCookieMiddleware()
        return await mw.cookies()

    try:
        cookies = asyncio.run(_get_cookies())
    except Exception as e:
        log.warning("Не удалось получить Qrator куки: %s — пробуем без куков", e)
        cookies = {}

    session = cffi.Session(impersonate="chrome131")
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="ggsel.net")

    # Парсим несколько страниц чтобы собрать больше slug
    pages_to_check = [
        f"{BASE_PUBLIC}/catalog",
        f"{BASE_PUBLIC}/catalog/igry-po-nazvaniyu",
        f"{BASE_PUBLIC}/catalog/programs-new",
        f"{BASE_PUBLIC}/catalog/podpisochnye-servisy",
        f"{BASE_PUBLIC}/catalog/mobile-games",
        f"{BASE_PUBLIC}/catalog/game-currency",
    ]

    seen_slugs: set[str] = set()
    result: list[dict] = []

    for page_url in pages_to_check:
        try:
            r = session.get(page_url, timeout=15)
            if r.status_code != 200:
                log.warning("HTML fetch %s → %d", page_url, r.status_code)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                if "/catalog/" not in href:
                    continue
                # Берём только прямые подкатегории /catalog/<slug>
                m = re.match(r"^/catalog/([^/?#]+)/?$", href)
                if not m:
                    continue
                slug = m.group(1).strip()
                if not slug or slug in seen_slugs:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) > 80:
                    continue
                seen_slugs.add(slug)
                result.append({"slug": slug, "title": title})

            log.info("HTML %s → +%d slug (итого %d)", page_url, len(result) - len(seen_slugs) + len(result), len(result))
            time.sleep(2)
        except Exception as e:
            log.warning("HTML fetch error %s: %s", page_url, e)

    log.info("Всего slug из HTML: %d", len(result))
    return result


# ─── Step 2: Seller API → id + title + fee ────────────────────────────────────

def _fetch_api_categories() -> list[dict]:
    """
    Получает все категории из Seller API V2.
    Возвращает [{"id": ..., "title": ..., "fee": ..., "full_path": ..., "has_children": ...}]
    """
    from config import GGSEL_API_KEY
    headers = {"Authorization": GGSEL_API_KEY, "locale": "ru"}

    all_cats: list[dict] = []
    page = 1
    while True:
        try:
            r = requests.get(
                f"{BASE_SELLER}/api_sellers/v2/categories",
                headers=headers,
                params={"page": page, "limit": 100},
                timeout=15,
            )
            data = r.json()
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                all_cats.append({
                    "id":          int(item["id"]),
                    "title":       (item.get("title") or "").strip(),
                    "fee":         float(item["fee"]) if item.get("fee") is not None else 0.15,
                    "full_path":   item.get("tree") or item.get("title") or "",
                    "has_children": 1 if item.get("has_children") else 0,
                })
            log.info("API категории стр.%d: +%d (итого %d)", page, len(items), len(all_cats))
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            log.error("API categories page %d error: %s", page, e)
            break

    log.info("Всего категорий из API: %d", len(all_cats))
    return all_cats


# ─── Step 3: Матчинг slug ↔ id по title ───────────────────────────────────────

def _normalize(text: str) -> str:
    """Нормализация для нечёткого матчинга: нижний регистр, только буквы/цифры."""
    return re.sub(r"[^a-zа-яё0-9]", "", text.lower())


def _match_slugs_to_api(
    html_slugs: list[dict],
    api_cats: list[dict],
) -> list[dict]:
    """
    Матчит slug из HTML с категориями из API по нормализованному title.
    Возвращает список записей готовых для сохранения в БД.
    """
    # Индекс API по нормализованному title
    api_index: dict[str, dict] = {}
    for cat in api_cats:
        key = _normalize(cat["title"])
        if key:
            api_index[key] = cat

    matched: list[dict] = []
    unmatched: list[str] = []

    for item in html_slugs:
        slug = item["slug"]
        html_title = item["title"]
        key = _normalize(html_title)
        api_cat = api_index.get(key)
        if api_cat:
            matched.append({
                "id":          api_cat["id"],
                "slug":        slug,
                "title":       html_title,
                "fee":         api_cat["fee"],
                "full_path":   api_cat["full_path"],
                "has_children": api_cat["has_children"],
            })
        else:
            # Нет точного матча — сохраняем slug без id (id=NULL не подходит для PK)
            # Используем отрицательный hash как временный id
            tmp_id = -(abs(hash(slug)) % 10_000_000)
            matched.append({
                "id":          tmp_id,
                "slug":        slug,
                "title":       html_title,
                "fee":         None,
                "full_path":   "",
                "has_children": 0,
            })
            unmatched.append(f"{slug!r} ({html_title!r})")

    if unmatched:
        log.warning("Без матча с API (%d): %s", len(unmatched), ", ".join(unmatched[:10]))

    # Добавляем категории из API у которых нет slug в HTML
    # (они есть в API но не показываются в навигации)
    matched_ids = {r["id"] for r in matched if r["id"] > 0}
    for cat in api_cats:
        if cat["id"] not in matched_ids:
            matched.append({
                "id":          cat["id"],
                "slug":        "",   # slug неизвестен
                "title":       cat["title"],
                "fee":         cat["fee"],
                "full_path":   cat["full_path"],
                "has_children": cat["has_children"],
            })

    log.info("Итого записей для БД: %d (из них без slug: %d)",
             len(matched), sum(1 for r in matched if not r["slug"]))
    return matched


# ─── Step 4: Сохранение в БД ──────────────────────────────────────────────────

def _save_to_db(records: list[dict]) -> dict:
    """Upsert записей в category_slugs. Возвращает статистику."""
    conn = sqlite3.connect(get_db_path(), timeout=15)
    _ensure_table(conn)

    inserted = 0
    updated = 0
    for r in records:
        existing = conn.execute(
            "SELECT slug, fee FROM category_slugs WHERE id = ?", (r["id"],)
        ).fetchone()
        if existing is None:
            conn.execute("""
                INSERT INTO category_slugs (id, slug, title, fee, full_path, has_children, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (r["id"], r["slug"], r["title"], r["fee"], r["full_path"], r["has_children"]))
            inserted += 1
        else:
            # Обновляем если что-то изменилось
            if existing[0] != r["slug"] or existing[1] != r["fee"]:
                conn.execute("""
                    UPDATE category_slugs
                    SET slug=?, title=?, fee=?, full_path=?, has_children=?, updated_at=datetime('now')
                    WHERE id=?
                """, (r["slug"], r["title"], r["fee"], r["full_path"], r["has_children"], r["id"]))
                updated += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": len(records)}


# ─── Главная функция ──────────────────────────────────────────────────────────

def sync_category_slugs(force: bool = False) -> dict:
    """
    Полная синхронизация slug ↔ id ↔ fee.
    force=True — игнорировать кеш, пересинхронизировать полностью.
    """
    log.info("=== Синхронизация категорий: старт ===")

    # Проверяем нужна ли синхронизация
    if not force:
        conn = sqlite3.connect(get_db_path(), timeout=5)
        _ensure_table(conn)
        count = conn.execute("SELECT COUNT(*) FROM category_slugs WHERE slug != ''").fetchone()[0]
        conn.close()
        if count > 50:
            log.info("category_slugs уже содержит %d записей со slug — пропускаем (force=False)", count)
            return {"skipped": True, "count": count}

    # Шаг 1: HTML → slugs
    log.info("Шаг 1: парсинг HTML каталога...")
    html_slugs = _fetch_catalog_slugs_from_html()

    # Шаг 2: API → categories
    log.info("Шаг 2: загрузка категорий из Seller API...")
    api_cats = _fetch_api_categories()

    # Шаг 3: матчинг
    log.info("Шаг 3: матчинг slug ↔ id...")
    records = _match_slugs_to_api(html_slugs, api_cats)

    # Шаг 4: сохранение
    log.info("Шаг 4: сохранение в БД...")
    stats = _save_to_db(records)

    log.info("=== Готово: inserted=%d updated=%d total=%d ===",
             stats["inserted"], stats["updated"], stats["total"])
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import sys
    force = "--force" in sys.argv
    result = sync_category_slugs(force=force)
    print("Результат:", json.dumps(result, ensure_ascii=False, indent=2))
