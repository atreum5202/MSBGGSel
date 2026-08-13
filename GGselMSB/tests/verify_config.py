"""Проверка config.py."""
import sys
import io
import json
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

print("=" * 60)
print("config.py — все настройки")
print("=" * 60)
print(json.dumps(config.dump(), indent=2, ensure_ascii=False))
print()
print("=" * 60)
print("Проверка критичных значений:")
print("=" * 60)
checks = [
    ("MSB_API_BASE", config.MSB_API_BASE, "http://127.0.0.1:17248"),
    ("MSB_API_TOKEN", bool(config.MSB_API_TOKEN), False),
    ("MSB_PROFILES_DIR", str(config.MSB_PROFILES_DIR).endswith("MSB\\profiles"), True),
    ("POOL_MAX_HITS", config.POOL_MAX_HITS, 80),
    ("POOL_REST_SEC", config.POOL_REST_SEC, 300),
    ("RATE_BASE_DELAY", config.RATE_BASE_DELAY, 4.0),
    ("RATE_MIN_DELAY", config.RATE_MIN_DELAY, 2.0),
    ("RATE_MAX_DELAY", config.RATE_MAX_DELAY, 60.0),
    ("RATE_OK_DECAY", config.RATE_OK_DECAY, 0.95),
    ("TELEMETRY_ENABLED", config.TELEMETRY_ENABLED, True),
    ("CAPTCHA_ENABLED", config.CAPTCHA_ENABLED, True),
    ("PARSER_USE_MSB", config.PARSER_USE_MSB, True),
]
all_ok = True
for name, actual, expected in checks:
    ok = actual == expected
    all_ok = all_ok and ok
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name:<20} = {actual!r:<40} (ожидалось {expected!r})")

print()
print("=" * 60)
print("OK" if all_ok else "FAIL")
