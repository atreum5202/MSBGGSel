"""Verified GGSEL category catalogue used by the parser.

Reads leaf categories directly from the parser SQLite DB (data/db/parser.db).
Falls back to categories_cache.json if DB is unavailable.
"""
from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _ROOT / "categories_cache.json"
_DB_PATH = _ROOT / "data" / "db" / "parser.db"


def _from_db(limit: int = 200) -> list[dict[str, Any]]:
    """Load leaf categories from SQLite DB."""
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title, parent_id, depth, full_path, content_type, fee, has_children
            FROM categories
            WHERE has_children = 0 AND fee IS NOT NULL
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (max(1, min(int(limit), 5000)),),
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for row in rows:
            item = {
                "id": int(row["id"]),
                "title": row["title"] or "",
                "full_path": row["full_path"] or row["title"] or "",
                "content_type": row["content_type"] or "",
                "fee": float(row["fee"]) if row["fee"] is not None else 0.15,
                "is_leaf": True,
                "url": f"/catalog/{row['id']}",
            }
            result.append(item)
        return result
    except Exception:
        return []


def _from_cache() -> list[dict[str, Any]]:
    """Fallback: read from categories_cache.json."""
    if not _CACHE_PATH.exists():
        return []
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    # Handle both list and {"categories": [...]} formats
    if isinstance(raw, dict):
        raw = raw.get("categories", [])
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            cat = {
                "id": int(item["id"]),
                "title": item.get("title", ""),
                "full_path": item.get("full_path", item.get("title", "")),
                "content_type": item.get("content_type", ""),
                "fee": float(item["fee"]) if item.get("fee") is not None else 0.15,
                "is_leaf": not item.get("has_children", False),
                "url": item.get("url", f"/catalog/{item['id']}"),
            }
            result.append(cat)
        except (KeyError, TypeError, ValueError):
            continue
    return result


def leaf_categories() -> tuple[dict[str, Any], ...]:
    """Return all leaf categories (with fee) from DB or cache."""
    items = _from_db(limit=5000)
    if not items:
        items = [x for x in _from_cache() if x.get("is_leaf")]
    return tuple(items)


def get_leaf_category(category_id: int | str | None) -> dict[str, Any] | None:
    try:
        wanted = int(category_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    for item in leaf_categories():
        if item["id"] == wanted:
            return dict(item)
    return None


def search_leaf_categories(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Return up to `limit` leaf categories, optionally filtered by query."""
    # Always fresh from DB for autopilot (no lru_cache here)
    items = _from_db(limit=max(limit * 10, 500))
    if not items:
        items = [x for x in _from_cache() if x.get("is_leaf")]

    query = (query or "").strip().casefold()
    if query:
        items = [
            item for item in items
            if query in str(item.get("title", "")).casefold()
            or query in str(item.get("full_path", "")).casefold()
        ]

    random.shuffle(items)
    return items[: max(1, min(int(limit), 200))]
