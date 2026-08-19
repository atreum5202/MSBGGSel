"""tools/refresh_cat_fees.py
==========================
Перезаписывает `cat_fees.json` реальными fee из таблицы `seller_categories` БД parser.db.

Ключ = str(id) (это ggsel_digi_catalog для ggsel.net витрины — что и использовалось в исходном
cat_fees.json). Значение = fee (float).

ВНИМАНИЕ про коллизию id-пространств:
- В БД `seller_categories.id` = ggsel_digi_catalog (витрина)
- В live API `/api/v1/categories/{id}` (seller API) — другое число
- Поле `seller_id` в `category_slug_mapping` — на самом деле ggsel_digi_catalog (название вводит
  в заблуждение, см. parser/category_resolver.py)

Запуск:
  python -m tools.refresh_cat_fees
  python -m tools.refresh_cat_fees --dry-run
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH = _ROOT / "data" / "db" / "parser.db"
OUT = _ROOT / "cat_fees.json"


def build_fees() -> dict[str, float]:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    fees: dict[str, float] = {}
    for sid, fee in conn.execute("SELECT id, fee FROM seller_categories WHERE fee IS NOT NULL"):
        fees[str(int(sid))] = round(float(fee), 4)
    conn.close()
    return fees


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    fees = build_fees()
    n = len(fees)
    print(f"loaded {n} fee records from seller_categories")

    # Sample fee distribution
    by_fee: dict[float, int] = {}
    for v in fees.values():
        by_fee[v] = by_fee.get(v, 0) + 1
    print("top fee values:")
    for f, c in sorted(by_fee.items(), key=lambda x: -x[1])[:8]:
        print(f"  {f}: {c} categories")

    if dry:
        print("\n--- sample 10 records ---")
        for k in sorted(fees, key=lambda x: int(x))[:10]:
            print(f"  {k}: {fees[k]}")
        return 0

    OUT.write_text(
        json.dumps(fees, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"saved -> {OUT}  ({n} keys, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
