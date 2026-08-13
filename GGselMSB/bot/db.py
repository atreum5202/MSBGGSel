"""
bot/db.py — работа с БД для бота GGselMSB.
Таблицы: bot_shop_links, bot_connect_codes.
Использует тот же parser.db из data/db/.
"""
import os
import sqlite3
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=False)

_CODE_TTL = 600  # 10 минут


def _get_db_path() -> str:
    env = os.getenv("BOT_DB_PATH", "")
    if env:
        return env
    base = Path(__file__).resolve().parent.parent
    return str(base / "data" / "db" / "parser.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_get_db_path())
    c.row_factory = sqlite3.Row
    return c


def _ensure_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bot_shop_links (
            shop_id    TEXT PRIMARY KEY,
            chat_id    INTEGER,
            linked_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bot_connect_codes (
            code       TEXT PRIMARY KEY,
            shop_id    TEXT NOT NULL,
            expires_at REAL NOT NULL
        );
    """)
    conn.commit()


# ── Connect codes ─────────────────────────────────────────────────────────────

def generate_connect_code(shop_id: str = "default") -> str:
    """Создать одноразовый код привязки (10 мин)."""
    code = secrets.token_urlsafe(8).upper()
    with _conn() as conn:
        _ensure_tables(conn)
        conn.execute("DELETE FROM bot_connect_codes WHERE shop_id = ?", (shop_id,))
        conn.execute(
            "INSERT INTO bot_connect_codes (code, shop_id, expires_at) VALUES (?, ?, ?)",
            (code, shop_id, time.time() + _CODE_TTL),
        )
        conn.commit()
    return code


def consume_connect_code(code: str) -> str | None:
    """Проверить код → вернуть shop_id или None."""
    with _conn() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT shop_id, expires_at FROM bot_connect_codes WHERE code = ?",
            (code.strip().upper(),),
        ).fetchone()
        if not row:
            return None
        if time.time() > row["expires_at"]:
            conn.execute("DELETE FROM bot_connect_codes WHERE code = ?", (code,))
            conn.commit()
            return None
        shop_id = row["shop_id"]
        conn.execute("DELETE FROM bot_connect_codes WHERE code = ?", (code,))
        conn.commit()
        return shop_id


# ── Shop ↔ Chat links ─────────────────────────────────────────────────────────

def save_chat_id(shop_id: str, chat_id: int):
    with _conn() as conn:
        _ensure_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO bot_shop_links (shop_id, chat_id) VALUES (?, ?)",
            (shop_id, chat_id),
        )
        conn.commit()


def get_shop_by_chat_id(chat_id: int) -> dict | None:
    with _conn() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT shop_id FROM bot_shop_links WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return dict(row) if row else None


def get_chat_id_by_shop(shop_id: str = "default") -> int | None:
    with _conn() as conn:
        _ensure_tables(conn)
        # Ищем конкретный shop или первый доступный
        row = conn.execute(
            "SELECT chat_id FROM bot_shop_links WHERE shop_id = ? LIMIT 1",
            (shop_id,),
        ).fetchone()
        if row:
            return row["chat_id"]
        # Fallback: первый привязанный
        row = conn.execute(
            "SELECT chat_id FROM bot_shop_links LIMIT 1"
        ).fetchone()
        return row["chat_id"] if row else None


def get_first_chat_id() -> int | None:
    """Вернуть первый зарегистрированный chat_id (для admin-уведомлений)."""
    with _conn() as conn:
        _ensure_tables(conn)
        row = conn.execute("SELECT chat_id FROM bot_shop_links LIMIT 1").fetchone()
        return row["chat_id"] if row else None


def disconnect_shop(shop_id: str):
    with _conn() as conn:
        _ensure_tables(conn)
        conn.execute("DELETE FROM bot_shop_links WHERE shop_id = ?", (shop_id,))
        conn.commit()


def is_connected(chat_id: int) -> bool:
    return get_shop_by_chat_id(chat_id) is not None


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_queue_count() -> int:
    """Кол-во товаров в очереди."""
    with _conn() as conn:
        _ensure_tables(conn)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM parsed_products WHERE status='pending'"
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0


def get_approved_count() -> int:
    """Кол-во одобренных товаров."""
    with _conn() as conn:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM parsed_products WHERE status='approved'"
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0


def get_last_parser_run() -> dict | None:
    """Последний запуск парсера."""
    with _conn() as conn:
        try:
            row = conn.execute(
                "SELECT * FROM parser_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None


def get_pending_products(limit: int = 10) -> list:
    """Товары со статусом pending."""
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM parsed_products WHERE status='pending' ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


def set_product_status(product_id: str, status: str):
    """Изменить статус товара."""
    with _conn() as conn:
        conn.execute(
            "UPDATE parsed_products SET status=?, updated_at=datetime('now') WHERE product_id=?",
            (status, product_id)
        )
        conn.commit()
