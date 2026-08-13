"""
parser/db_init.py
=================
РРЅРёС†РёР°Р»РёР·Р°С†РёСЏ Рё РјРёРіСЂР°С†РёРё Р‘Р” РїР°СЂСЃРµСЂР°.

Р“Р»Р°РІРЅР°СЏ С‚РѕС‡РєР° РІС…РѕРґР° вЂ” init_db(). Р‘РµР·РѕРїР°СЃРЅРѕ РІС‹Р·С‹РІР°РµС‚СЃСЏ РјРЅРѕРіРѕРєСЂР°С‚РЅРѕ:
  - РµСЃР»Рё С„Р°Р№Р»Р° РЅРµС‚ вЂ” СЃРѕР·РґР°С‘С‚
  - РµСЃР»Рё С‚Р°Р±Р»РёС†С‹ РµСЃС‚СЊ вЂ” РїСЂРѕРїСѓСЃРєР°РµС‚
  - РµСЃР»Рё РґРѕР±Р°РІРёР»РёСЃСЊ РЅРѕРІС‹Рµ РєРѕР»РѕРЅРєРё вЂ” ALTER TABLE РёРґРµРјРїРѕС‚РµРЅС‚РЅРѕ
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

# РџР°РїРєР° РґР»СЏ РІСЃРµС… РґР°РЅРЅС‹С… РїР°СЂСЃРµСЂР° вЂ” РІРЅСѓС‚СЂРё GGselV7, РЅРµ Р·Р°РІРёСЃРёС‚ РѕС‚ GGSeller
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "db" / "parser.db"
DEFAULT_ALL_CATS_PATH = _PROJECT_ROOT / "all_cats.json"
log = logging.getLogger("ggselv7.db_init")


def _resolve_db_path() -> Path:
    """Р‘Р” Р»РµР¶РёС‚ РІ GGselV7/data/db/parser.db. РњРѕР¶РЅРѕ РїРµСЂРµРѕРїСЂРµРґРµР»РёС‚СЊ С‡РµСЂРµР· PARSER_DB_PATH."""
    env = os.getenv("PARSER_DB_PATH", "").strip()
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def get_db_path() -> str:
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ Р°Р±СЃРѕР»СЋС‚РЅС‹Р№ РїСѓС‚СЊ Рє Р‘Р” РІ РІРёРґРµ СЃС‚СЂРѕРєРё."""
    return str(_resolve_db_path())


def init_db() -> str:
    """РЎРѕР·РґР°С‘С‚ Р‘Р” Рё РїСЂРёРјРµРЅСЏРµС‚ РјРёРіСЂР°С†РёРё. Р’РѕР·РІСЂР°С‰Р°РµС‚ РїСѓС‚СЊ Рє С„Р°Р№Р»Сѓ Р‘Р”."""
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema_sql)
        conn.commit()
        _apply_migrations(conn)
        if DEFAULT_ALL_CATS_PATH.exists():
            try:
                imported = _import_categories_from_json_conn(conn, DEFAULT_ALL_CATS_PATH)
                log.info("Categories imported into parser DB: %d", imported)
            except Exception as e:
                log.warning("Categories import failed: %s", e)
    finally:
        conn.close()
    return str(db_path)


def import_categories_from_json(json_path: str | Path | None = None, db_path: str | Path | None = None) -> int:
    """
    РРјРїРѕСЂС‚РёСЂСѓРµС‚ РєР°С‚РµРіРѕСЂРёРё РёР· all_cats.json РІ С‚Р°Р±Р»РёС†Сѓ categories.
    РџРѕР»РЅРѕСЃС‚СЊСЋ РїРµСЂРµСЃРѕР±РёСЂР°РµС‚ СЃРїСЂР°РІРѕС‡РЅРёРє, С‡С‚РѕР±С‹ РѕРЅ РІСЃРµРіРґР° СЃРѕРѕС‚РІРµС‚СЃС‚РІРѕРІР°Р» JSON.
    """
    source_path = Path(json_path) if json_path else DEFAULT_ALL_CATS_PATH
    target_db_path = Path(db_path) if db_path else _resolve_db_path()
    target_db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_db_path))
    try:
        schema_path = Path(__file__).parent / "schema.sql"
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()
        _apply_migrations(conn)
        imported = _import_categories_from_json_conn(conn, source_path)
        conn.commit()
        return imported
    finally:
        conn.close()


def _import_categories_from_json_conn(conn: sqlite3.Connection, json_path: Path) -> int:
    if not json_path.exists():
        raise FileNotFoundError(f"Categories JSON not found: {json_path}")

    items = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"Expected list in {json_path}, got {type(items).__name__}")

    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cat_id = item.get("id")
        title = (item.get("title") or "").strip()
        if cat_id is None or not title:
            continue
        rows.append(
            (
                int(cat_id),
                title,
                item.get("parent_id"),
                int(item.get("depth") or 0),
                item.get("full_path"),
                item.get("content_type"),
                item.get("fee"),
                1 if item.get("has_children") else 0,
            )
        )

    if not rows:
        raise ValueError(f"No categories found in {json_path}")

    conn.execute("DELETE FROM categories")
    conn.executemany(
        """
        INSERT INTO categories (
            id, title, parent_id, depth, full_path, content_type, fee, has_children, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        rows,
    )
    return len(rows)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    РРґРµРјРїРѕС‚РµРЅС‚РЅС‹Рµ РјРёРіСЂР°С†РёРё. SQLite РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚ IF NOT EXISTS РІ ALTER TABLE вЂ”
    РїСЂРѕРІРµСЂСЏРµРј РїРѕ PRAGMA table_info.
    """
    new_columns = [
        ("parsed_products", "original_title",   "TEXT"),
        ("parsed_products", "original_desc",    "TEXT"),
        ("parsed_products", "seller_id",        "TEXT"),
        ("parsed_products", "seller_rating",    "REAL"),
        ("parsed_products", "reviews_count",    "INTEGER DEFAULT 0"),
        ("parsed_products", "is_top",           "INTEGER DEFAULT 0"),
        ("parsed_products", "tags",             "TEXT DEFAULT ''"),
        ("parsed_products", "source_price",     "REAL"),
        ("parsed_products", "last_parsed_at",   "TEXT"),
        ("parsed_products", "last_enriched_at", "TEXT"),
        ("parsed_products", "offer_id",         "TEXT"),
        ("parsed_products", "profit_score",      "REAL"),
        ("parsed_products", "status",            "TEXT DEFAULT 'parsed'"),
        ("parsed_products", "status_reason",     "TEXT DEFAULT ''"),
        # в”Ђв”Ђ Phase 2: РґРµС‚Р°Р»СЊРЅР°СЏ СЃС‚СЂР°РЅРёС†Р° + AI profit score в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        ("parsed_products", "images_json",            "TEXT"),
        ("parsed_products", "properties_json",        "TEXT"),
        ("parsed_products", "quantity_available",     "INTEGER"),
        ("parsed_products", "seller_url",             "TEXT"),
        ("parsed_products", "published_at",           "TEXT"),
        ("parsed_products", "recommended_margin_pct", "REAL"),
        ("parsed_products", "risk_level",             "TEXT"),
        ("parsed_products", "risk_reason",            "TEXT"),
        # в”Ђв”Ђ Phase 3: Р­РєРѕРЅРѕРјРёРєР° (РЁРђР“ 4) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        ("parsed_products", "category_id",            "INTEGER"),
        ("parsed_products", "delivery_type",          "TEXT DEFAULT 'unknown'"),
        ("parsed_products", "sell_price",             "REAL"),
        ("parsed_products", "ggsel_fee_pct",          "REAL"),
        ("parsed_products", "ggsel_fee_source",       "TEXT"),
        ("parsed_products", "payment_fee_pct",        "REAL"),
        ("parsed_products", "withdrawal_fee_pct",     "REAL"),
        ("parsed_products", "tax_pct",                "REAL"),
        ("parsed_products", "fixed_costs_rub",        "REAL"),
        ("parsed_products", "risk_reserve_pct",       "REAL"),
        ("parsed_products", "total_costs_rub",        "REAL"),
        ("parsed_products", "expected_profit_rub",    "REAL"),
        ("parsed_products", "expected_net_margin_pct","REAL"),
        ("parsed_products", "calculated_at",          "TEXT"),
        ("parsed_products", "economy_complete",       "INTEGER DEFAULT 0"),
        ("parsed_products", "target_margin_pct",      "REAL DEFAULT 0.15"),
        ("parsed_products", "min_net_profit_rub",     "REAL DEFAULT 50.0"),
        # в”Ђв”Ђ rejected_products РЁРђР“ 6 в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        ("rejected_products", "reject_code",   "TEXT"),
        ("rejected_products", "reject_reason", "TEXT"),
        ("rejected_products", "source_price",  "REAL"),
        ("rejected_products", "snapshot_json", "TEXT"),
        ("rejected_products", "seller_name",   "TEXT"),
        ("rejected_products", "category_id",   "INTEGER"),
        # в”Ђв”Ђ parser_runs РЁРђР“ 3: errors СѓР¶Рµ РІ СЃС…РµРјРµ, РЅРѕ РЅР° РІСЃСЏРєРёР№ СЃР»СѓС‡Р°Р№ в”Ђв”Ђв”Ђв”Ђ
        ("parser_runs", "errors", "TEXT DEFAULT ''"),
        # в”Ђв”Ђ resale_deals РЁРђР“ 11 в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        # Вторая фаза: AI и т.д.
        ("parsed_products", "ai_score",          "REAL"),
        ("parsed_products", "ai_reason",         "TEXT"),
        ("parsed_products", "ai_moderated_at",   "TEXT"),
        ("resale_deals", "actual_net_margin_pct", "REAL"),
        # Shop data columns
        ("parsed_products", "shop_name",             "TEXT"),
        ("parsed_products", "shop_rating",           "REAL"),
        ("parsed_products", "shop_products_count",   "INTEGER"),
        ("parsed_products", "shop_registered_at",    "TEXT"),
        ("parsed_products", "shop_positive_reviews", "INTEGER"),
        ("parsed_products", "shop_negative_reviews", "INTEGER"),
        ("parsed_products", "shop_url",              "TEXT"),
        ("parsed_products", "target_margin_pct",     "REAL DEFAULT 0.15"),
        ("parsed_products", "min_net_profit_rub",     "REAL DEFAULT 50.0"),
        # Промт 4: approval_status
        ("parsed_products", "approval_status",       "TEXT DEFAULT 'pending'"),
        # Локально скачанное фото товара (обход хотлинк-защиты CDN при показе в админке)
        ("parsed_products", "local_image_path",      "TEXT"),
    ]
    
    # Промт 5: Создать таблицу order_links в БД
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_links (
                order_id          TEXT PRIMARY KEY,
                my_offer_id       TEXT,
                source_offer_id   TEXT,
                source_seller_id  TEXT,
                source_price      REAL,
                my_price          REAL,
                profit_rub        REAL,
                status            TEXT DEFAULT 'new',
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    except Exception as e:
        log.warning("Migration for order_links failed: %s", e)
    
    # Миграция для создания таблицы event_log если её нет
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                time              TEXT NOT NULL,
                entity            TEXT NOT NULL,
                entity_id         TEXT NOT NULL,
                stage             TEXT NOT NULL,
                level             TEXT DEFAULT 'info',
                reason_code       TEXT,
                message           TEXT,
                technical_detail  TEXT,
                action            TEXT
            )
        """)
        
        # Создаём индексы если их нет
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_entity 
            ON event_log(entity, entity_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_time 
            ON event_log(time DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_stage 
            ON event_log(stage)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_level 
            ON event_log(level)
        """)
        
        conn.commit()
    except Exception as e:
        log.warning("Migration for event_log failed: %s", e)
    
    # Миграция статусов на новую систему
    try:
        # Обновляем старые статусы на новые
        conn.execute("""
            UPDATE parsed_products 
            SET status = CASE 
                WHEN status = 'pending' THEN 'parsed'
                WHEN status = 'approved' THEN 'approved_by_owner'
                WHEN status = 'rejected' THEN 'parsed'
                WHEN status = 'listed' THEN 'published'
                ELSE 'parsed'
            END,
            status_reason = CASE 
                WHEN status = 'rejected' THEN 'Миграция с rejected'
                ELSE ''
            END
            WHERE status IN ('pending', 'approved', 'rejected', 'listed')
        """)
        conn.commit()
    except Exception as e:
        log.warning("Migration of statuses failed: %s", e)
    for table, col_name, col_def in new_columns:
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if col_name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass
    conn.commit()

