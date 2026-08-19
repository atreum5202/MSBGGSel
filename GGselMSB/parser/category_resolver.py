"""parser/category_resolver.py
============================
Резолвер категорий: slug → seller_api_id.

Читает таблицу `category_slug_mapping` (parser.db), заполняется `build_category_map.py`
через рекурсивный обход seller API (/api/v1/categories?parent_id=X) + матчинг по title
с HTML-каталогом ggsel.net (slug).

Алгоритм поиска:
  1. Если на вход int/str-int → вернуть как есть (это уже seller id).
  2. Иначе: SELECT seller_id, match_score, seller_tree
     FROM category_slug_mapping
     WHERE slug = ? AND match_score >= 0.7
     ORDER BY match_score DESC, length(seller_tree) ASC
     LIMIT 1
  3. Fallback: нечёткий матч по title (casefold contains).
  4. None если ничего не нашли.

Подключение:
  from parser.category_resolver import find_seller_category_id
  cat_id = find_seller_category_id('roblox')   # → 28144
  cat_id = find_seller_category_id(39082)      # → 39082 (passthrough)

CLI:
  python -m parser.category_resolver roblox
  python -m parser.category_resolver 39082
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

log = logging.getLogger("ggselv7.category_resolver")

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "db" / "parser.db"

# Минимальный score — слабее уже шум (0.0 в маппинге = «не сматчено»)
MIN_MATCH_SCORE = 0.7

# Fallback: категории которых нет в seller_categories продавца.
# Новые/нишевые игры — маппятся в «Цифровые товары > Другое» (fee 2.7%).
# Обновляется когда продавец добавляет новую категорию в кабинете.
_SLUG_FALLBACKS: dict[str, int] = {
    # Игры без категории у продавца → Другое (33833)
    "arc-raiders":               33833,
    "helldivers-2":              33833,
    "ea-sports-fc-26-fifa-26":   33833,
    "arena-breakout-infinite":   33833,
    "europa-universalis-v":      33833,
    "dispatch":                  33833,
    "games-anno-117-pax-romana": 33833,
    "standoff-2":                33833,
    "zenless-zone-zero":         33833,
    "world-of-tanks-blitz":      33833,
    "albion-online":             33833,
    "microsoft-office-365":      33833,
    "antivirus-eset":            33833,
    "unlocktool":                33833,
    "voicemod-pro":              33833,
    "exitlag":                   33833,
    "autodesk":                  33833,
    "software-for-gamers-and-streaming": 33833,
    "seo-software":              33833,
    "subscriptions-for-all-occasions":   33833,
    "other-games-currency":      33833,
    # Нашли, но неточно (score < 0.70)
    "grand-theft-auto-5-first":  59,     # GTA > Аккаунты > Steam
    "playstation-games":         43973,  # PlayStation
}


def _connect(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else _DB_PATH
    if not p.exists():
        raise FileNotFoundError(f"parser.db not found: {p}")
    conn = sqlite3.connect(str(p), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def find_seller_category_id(
    slug_or_id: Any,
    db_path: Optional[Union[str, Path]] = None,
    min_score: float = MIN_MATCH_SCORE,
) -> Optional[int]:
    """
    Возвращает seller API id категории по:
      - числовому id (int/str-int) — passthrough;
      - строковому slug (например "roblox", "games-steam", "microsoft-office").

    Возвращает None, если не нашли (и min_score > 0).
    """
    if slug_or_id is None:
        return None
    s = str(slug_or_id).strip()
    if not s:
        return None

    # 1. Числовой id — passthrough
    try:
        return int(s)
    except (TypeError, ValueError):
        pass

    # 2. Поиск по slug
    try:
        conn = _connect(db_path)
    except FileNotFoundError as e:
        log.warning("[resolver] %s", e)
        return None

    try:
        cur = conn.cursor()

        # Точный матч по slug.
        # Приоритет: score DESC → лист (has_children=0) → длина дерева ASC.
        # LEFT JOIN seller_categories чтобы предпочесть листья над родителями.
        row = cur.execute(
            """
            SELECT csm.seller_id, csm.match_score, csm.seller_tree, csm.seller_title,
                   COALESCE(sc.has_children, 0) AS is_parent
            FROM category_slug_mapping csm
            LEFT JOIN seller_categories sc ON sc.id = csm.seller_id
            WHERE csm.slug = ? AND csm.match_score >= ?
            ORDER BY csm.match_score DESC,
                     COALESCE(sc.has_children, 0) ASC,
                     LENGTH(csm.seller_tree) ASC
            LIMIT 1
            """,
            (s, min_score),
        ).fetchone()

        if row is not None:
            if row["is_parent"]:
                log.debug(
                    "[resolver] slug=%r resolved to parent cat %s (%s) — no leaf found",
                    s, row["seller_id"], row["seller_title"],
                )
            return int(row["seller_id"])

        # 3. Нечёткий: substring по title, тоже предпочитаем листья
        row = cur.execute(
            """
            SELECT csm.seller_id, csm.match_score, csm.seller_tree, csm.seller_title,
                   COALESCE(sc.has_children, 0) AS is_parent
            FROM category_slug_mapping csm
            LEFT JOIN seller_categories sc ON sc.id = csm.seller_id
            WHERE csm.seller_title LIKE ? AND csm.match_score >= ?
            ORDER BY csm.match_score DESC,
                     COALESCE(sc.has_children, 0) ASC,
                     LENGTH(csm.seller_tree) ASC
            LIMIT 1
            """,
            (f"%{s}%", min_score),
        ).fetchone()

        if row is not None:
            log.info(
                "[resolver] fuzzy match %r → %s (%s, score=%.2f, is_parent=%s)",
                s, row["seller_id"], row["seller_title"], row["match_score"], bool(row["is_parent"]),
            )
            return int(row["seller_id"])

        # 4. Статический fallback для slug-ов которых нет в seller_categories
        if s in _SLUG_FALLBACKS:
            fb_id = _SLUG_FALLBACKS[s]
            log.info(
                "[resolver] static fallback %r → seller_id=%s",
                s, fb_id,
            )
            return fb_id

        log.debug("[resolver] no match for slug=%r", s)
        return None
    finally:
        conn.close()


def get_by_seller_id(
    seller_id: int,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    """Возвращает dict по точному seller_id (=ggsel_digi_catalog).
    Сначала ищет в category_slug_mapping, fallback на seller_categories."""
    if seller_id is None:
        return None
    try:
        conn = _connect(db_path)
    except FileNotFoundError as e:
        log.warning("[resolver] %s", e)
        return None
    try:
        # 1. category_slug_mapping
        row = conn.execute(
            "SELECT seller_id, match_score, seller_tree, seller_title, slug "
            "FROM category_slug_mapping WHERE seller_id = ? "
            "ORDER BY match_score DESC LIMIT 1",
            (int(seller_id),),
        ).fetchone()
        if row is not None:
            return {
                "seller_id": int(row["seller_id"]),
                "match_score": float(row["match_score"]),
                "seller_tree": row["seller_tree"],
                "seller_title": row["seller_title"],
                "slug": row["slug"],
            }
        # 2. fallback: seller_categories
        row = conn.execute(
            "SELECT id, title, tree, fee, content_type, has_children, "
            "       ggsel_digi_catalog, ancestor_ids "
            "FROM seller_categories WHERE id = ? LIMIT 1",
            (int(seller_id),),
        ).fetchone()
        if row is not None:
            return {
                "seller_id": int(row["id"]),
                "match_score": 0.0,
                "seller_tree": row["tree"] or row["title"] or "",
                "seller_title": row["title"] or "",
                "slug": None,
                "content_type": row["content_type"],
                "fee": row["fee"],
                "has_children": bool(row["has_children"]),
                "ggsel_digi_catalog": row["ggsel_digi_catalog"],
                "ancestor_ids": json.loads(row["ancestor_ids"]) if row["ancestor_ids"] else [],
            }
        return None
    finally:
        conn.close()


def resolve_with_tree(
    slug_or_id: Any,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[dict]:
    """
    То же что find_seller_category_id, но возвращает dict с seller_id + tree + title.
    Удобно для логирования в паблишере.
    """
    if slug_or_id is None:
        return None
    s = str(slug_or_id).strip()
    if not s:
        return None

    try:
        conn = _connect(db_path)
    except FileNotFoundError as e:
        log.warning("[resolver] %s", e)
        return None

    try:
        cur = conn.cursor()
        try:
            sid = int(s)
            row = cur.execute(
                "SELECT seller_id, match_score, seller_tree, seller_title, slug "
                "FROM category_slug_mapping WHERE seller_id = ? LIMIT 1", (sid,)
            ).fetchone()
        except ValueError:
            row = cur.execute(
                """
                SELECT csm.seller_id, csm.match_score, csm.seller_tree, csm.seller_title, csm.slug
                FROM category_slug_mapping csm
                LEFT JOIN seller_categories sc ON sc.id = csm.seller_id
                WHERE csm.slug = ? AND csm.match_score >= ?
                ORDER BY csm.match_score DESC,
                         COALESCE(sc.has_children, 0) ASC,
                         LENGTH(csm.seller_tree) ASC
                LIMIT 1
                """,
                (s, MIN_MATCH_SCORE),
            ).fetchone()
        if row is None:
            return None
        return {
            "seller_id": int(row["seller_id"]),
            "match_score": float(row["match_score"]),
            "seller_tree": row["seller_tree"],
            "seller_title": row["seller_title"],
            "slug": row["slug"],
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json as _json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python -m parser.category_resolver <slug|id>")
        sys.exit(1)
    arg = sys.argv[1]
    result = resolve_with_tree(arg)
    print(_json.dumps(result, ensure_ascii=False, indent=2))
