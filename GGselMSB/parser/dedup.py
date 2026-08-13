"""
parser/dedup.py
===============
Дедупликация спаршенных товаров.
  - is_fresh()  — уже есть в БД и обновлялся недавно
  - is_rejected() — был отклонён и cooldown не истёк
  - is_duplicate_name() — название слишком похоже на существующее
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from .db_init import get_db_path

RECHECK_HOURS = int(os.getenv("PARSER_RECHECK_HOURS", "24"))
DEDUP_CACHE_TTL = int(os.getenv("DEDUP_CACHE_TTL", "300"))

# thread-local соединение — не плодим новые на каждый вызов
_tls = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = sqlite3.connect(get_db_path(), check_same_thread=False, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _tls.conn = conn
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except Exception:
            pass
        conn = sqlite3.connect(get_db_path(), check_same_thread=False, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _tls.conn = conn
    return conn


def is_fresh(product_id: str) -> bool:
    """True если товар уже в БД и обновлялся менее RECHECK_HOURS часов назад."""
    if not product_id:
        return False
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT updated_at FROM parsed_products WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        if not row or not row[0]:
            return False
        updated = datetime.fromisoformat(str(row[0]).replace(" ", "T"))
        return datetime.utcnow() - updated < timedelta(hours=RECHECK_HOURS)
    except Exception:
        return False


def is_rejected(product_id: str) -> bool:
    """True если товар в blacklist и cooldown ещё не истёк."""
    if not product_id:
        return False
    try:
        cooldown_days = int(os.getenv("REJECTED_COOLDOWN_DAYS", "7"))
        cutoff = (datetime.utcnow() - timedelta(days=cooldown_days)).isoformat()
        conn = _get_conn()
        row = conn.execute(
            "SELECT rejected_at FROM rejected_products "
            "WHERE product_id = ? AND rejected_at > ?",
            (product_id, cutoff),
        ).fetchone()
        return row is not None
    except Exception:
        return False


# ── Дедупликация по названию ──────────────────────────────────────────────────
import difflib

_name_cache: list = []
_name_cache_ts: float = 0.0


def _load_names() -> list:
    global _name_cache, _name_cache_ts
    now = _time.monotonic()
    if _name_cache and (now - _name_cache_ts) < DEDUP_CACHE_TTL:
        return _name_cache
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT title FROM parsed_products WHERE status != 'rejected'"
        ).fetchall()
        _name_cache = [r[0] for r in rows if r[0]]
        _name_cache_ts = now
    except Exception:
        pass
    return _name_cache


def is_duplicate_name(name: str, threshold: float = 0.85) -> bool:
    """True если название слишком похоже на уже существующее (порог 0..1)."""
    if not name:
        return False
    similarity_threshold = float(
        os.getenv("DEDUP_SIMILARITY_THRESHOLD", str(threshold))
    )
    existing_names = _load_names()
    name_lower = name.lower()
    for existing in existing_names:
        ratio = difflib.SequenceMatcher(
            None, name_lower, existing.lower()
        ).ratio()
        if ratio >= similarity_threshold:
            return True
    return False


def invalidate_name_cache() -> None:
    """Сбрасывает кеш названий. Вызывай после массовых вставок."""
    global _name_cache, _name_cache_ts
    _name_cache = []
    _name_cache_ts = 0.0
