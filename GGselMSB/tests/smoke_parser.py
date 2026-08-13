"""Smoke test for parser package — verifies all imports + DB init."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

print("=== Smoke test parser package ===")
from parser import init_db, get_db_path, KNOWN_CATEGORIES, MAX_QUANTITY_HARD_CAP
db = init_db()
print("OK init_db ->", db)
print("OK categories:", KNOWN_CATEGORIES)
print("OK hard cap:", MAX_QUANTITY_HARD_CAP)
print()

print("=== Smoke test parser_engine ===")
from parser.parser_engine import ParserEngine, get_engine, CffiFetcher
eng = get_engine()
print("OK engine created, is_running:", eng.is_running())
print("OK status:", eng.status())
print()

print("=== Smoke test dedup ===")
from parser.dedup import is_fresh, is_rejected, is_duplicate_name
print("OK is_fresh('abc') =", is_fresh("abc"))
print("OK is_rejected('abc') =", is_rejected("abc"))
print()

print("=== Smoke test pricing ===")
from parser.pricing import calculate_my_price, calculate_margin
print("OK price 100 games:", calculate_my_price(100, "games"))
print("OK price 1000 keys:", calculate_my_price(1000, "keys"))
m = calculate_margin(150, 100, "games")
print("OK margin 100->150:", m)
print()

print("=== Smoke test content_gen (no key) ===")
from parser.content_gen import enrich_product
result = enrich_product({"product_id": "test", "title": "Test", "category": "games", "price": 100})
print("OK enrich (no key):", {k: v for k, v in result.items() if k != "ai_error"})
print()

print("=== Smoke test routes blueprint ===")
from parser.routes import parser_bp
print("OK blueprint:", parser_bp.name, parser_bp.url_prefix)
print()

print("=== Smoke test DB content ===")
import sqlite3
conn = sqlite3.connect(get_db_path())
for tbl in ["parsed_products", "rejected_products", "parser_runs", "parser_log"]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
    print(f"OK table {tbl}: {len(cols)} cols")
conn.close()
print()

print("All smoke tests passed")
