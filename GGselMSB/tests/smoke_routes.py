"""Smoke-тест новых эндпоинтов через Flask test client."""
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

from flask import Flask
from parser.routes import parser_bp

app = Flask(__name__)
app.register_blueprint(parser_bp)
app.config["TESTING"] = True
client = app.test_client()


def get(path, label):
    r = client.get(path)
    print(f"  {r.status_code}  GET  {path:<40} {label}")
    if r.status_code >= 400:
        print(f"      body: {r.data[:200].decode('utf-8', errors='replace')}")
    return r


def post(path, body=None, label=""):
    r = client.post(path, json=body or {})
    print(f"  {r.status_code}  POST {path:<40} {label}")
    if r.status_code >= 400:
        print(f"      body: {r.data[:200].decode('utf-8', errors='replace')}")
    return r


print("=" * 60)
print("Smoke-тест эндпоинтов MSB + telemetry")
print("=" * 60)

print()
print("─── MSB endpoints ───")
r = get("/api/parser/msb/status", "статус пула + rate limiter + telemetry")
if r.status_code == 200:
    j = r.get_json()
    print(f"      msb_running:           {j.get('msb_running')}")
    print(f"      fetcher_used:          {j.get('fetcher_used')}")
    pool = j.get("pool")
    if pool and isinstance(pool, dict) and "total" in pool:
        print(f"      pool.total:            {pool['total']}")
        print(f"      pool.active:           {pool['active']}")
        print(f"      pool.resting:          {pool['resting']}")
        print(f"      pool.max_hits:         {pool['max_hits']}")
    rl = j.get("rate_limiter")
    if rl and isinstance(rl, dict) and "base_delay" in rl:
        print(f"      rate.base_delay:       {rl['base_delay']}")
        print(f"      rate.profiles:         {len(rl.get('profiles', {}))}")
    tel = j.get("telemetry")
    if tel and isinstance(tel, dict) and "enabled" in tel:
        print(f"      telemetry.enabled:     {tel.get('enabled')}")
        print(f"      telemetry.current:     {tel.get('current_file')}")

r = get("/api/parser/msb/rate", "snapshot rate limiter")
if r.status_code == 200:
    j = r.get_json()
    print(f"      base_delay:            {j.get('base_delay')}")
    print(f"      profiles:              {len(j.get('profiles', {}))}")

print()
print("─── Telemetry endpoints ───")
r = get("/api/parser/telemetry/recent?limit=10", "последние 10 событий")
if r.status_code == 200:
    j = r.get_json()
    print(f"      count:                 {j.get('count')}")
    print(f"      items (last 3):")
    for item in (j.get("items") or [])[-3:]:
        print(f"        {item.get('ts','?')[:19]}  {item.get('event','?')}")

r = get("/api/parser/telemetry/stats", "статистика файла")
if r.status_code == 200:
    j = r.get_json()
    print(f"      enabled:               {j.get('enabled')}")
    print(f"      current_file:          {j.get('current_file')}")
    print(f"      total_size_bytes:      {j.get('total_size_bytes')}")

print()
print("─── Telemetry toggle ───")
r = post("/api/parser/telemetry/enabled", {"enabled": False}, "выключить")
r = post("/api/parser/telemetry/enabled", {"enabled": True}, "включить обратно")

print()
print("─── Rate reset ───")
r = post("/api/parser/msb/rate/reset", {}, "сбросить всех")
r = post("/api/parser/msb/reset-errors", {}, "сбросить ошибки пула")

print()
print("─── Existing endpoints (smoke) ───")
r = get("/api/parser/status", "engine status")
r = get("/api/parser/config", "config")
r = get("/api/parser/products?limit=5", "products (limit 5)")

print()
print("=" * 60)
print("OK — все эндпоинты отвечают")
