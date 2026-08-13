"""Проверка что все классы и конфиги из msb_cookies + profile_pool доступны."""
import sys
import io
import dataclasses
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.msb_cookies import (
    QratorCookieMiddleware,
    QRATOR_COOKIE_KEYS, validate_qrator_cookies,
    GGSEL_PROFILES_DIR, MSB_API_BASE, MSB_PROFILE_ID,
    SCENARIO_NAME, COOKIE_TTL_SECONDS,
)
from parser.msb_client import MsbClient as MsbCookieClient
MSB_API_TOKEN = ""
from parser.profile_pool import (
    ProfilePool, PoolProfile, get_pool, get_pool_sync,
    MAX_HITS_PER_PROFILE, POOL_REST_SEC,
    COOKIE_TTL_SECONDS as POOL_TTL,
    MSB_API_BASE as POOL_BASE,
)

print("=== msb_cookies ===")
print(f"  MSB_API_BASE:        {MSB_API_BASE}")
print(f"  MSB_API_TOKEN set:   {bool(MSB_API_TOKEN)}")
print(f"  MSB_PROFILE_ID:      {MSB_PROFILE_ID!r}")
print(f"  SCENARIO_NAME:       {SCENARIO_NAME}")
print(f"  COOKIE_TTL_SECONDS:  {COOKIE_TTL_SECONDS}")
print(f"  GGSEL_PROFILES_DIR:  {GGSEL_PROFILES_DIR}")
print(f"  Qrator keys:         {sorted(QRATOR_COOKIE_KEYS)}")
print()
print(f"  validate_qrator_cookies({{}}): {validate_qrator_cookies({})}")
print(f"  validate_qrator_cookies({{'__qrator_jsid': 'abc'}}): {validate_qrator_cookies({'__qrator_jsid': 'abc'})}")
print()
print("=== profile_pool ===")
print(f"  MSB_API_BASE:        {POOL_BASE}")
print(f"  MAX_HITS_PER_PROFILE:{MAX_HITS_PER_PROFILE}")
print(f"  POOL_REST_SEC:       {POOL_REST_SEC}")
print(f"  COOKIE_TTL_SECONDS:  {POOL_TTL}")
print()
print("  PoolProfile dataclass fields:")
for f in dataclasses.fields(PoolProfile):
    print(f"    {f.name}: {f.type}")
print()
print("OK - все импорты работают")
