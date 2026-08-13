"""
Smoke-тест ProfilePool против реального MSB.
Запуск: python tests/smoke_msb_pool.py
"""
import asyncio
import sys
import io
from pathlib import Path

# Принудительно UTF-8 для stdout (Windows консоль по умолчанию cp1251)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# Добавляем корень проекта в sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.profile_pool import get_pool  # noqa: E402


async def main():
    print("=" * 60)
    print("SMOKE: ProfilePool против реального MSB")
    print("=" * 60)

    pool = await get_pool()
    s = await pool.status()

    print(f"  Инициализирован:      {s['initialized']}")
    print(f"  Всего профилей:       {s['total']}")
    print(f"  Активных:             {s['active']}")
    print(f"  На отдыхе:            {s['resting']}")
    print(f"  Max hits per profile: {s['max_hits']}")
    print(f"  Rest sec:             {s['rest_sec']}")
    print()
    print("  Первые 5 профилей:")
    for p in s["profiles"][:5]:
        pid = p["profile_id"][:8]
        name = p["name"][:38]
        hit = p["hit_count"]
        err = p["error_count"]
        rest = p["is_resting"]
        has_c = p["has_cookies"]
        age = p["cookies_age"]
        rest_rem = p["rest_remaining"]
        print(f"    {pid}.. {name:<38} hit={hit:<3} err={err:<3} rest={rest} has_cookies={has_c} age={age}s rest_rem={rest_rem}s")

    print()
    print("  Тест assign_proxies (мок):")
    mapping = await pool.assign_proxies(["http://proxy1:8080", "http://proxy2:8080"])
    print(f"    Назначено прокси: {len(mapping)} профилям (пример: {dict(list(mapping.items())[:2])})")

    print()
    print("  Тест reset_errors:")
    await pool.reset_errors()
    s2 = await pool.status()
    print(f"    После reset: resting={s2['resting']}, active={s2['active']}")

    await pool.close()
    print()
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
