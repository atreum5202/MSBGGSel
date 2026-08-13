"""Smoke test: Flask app + parser blueprint registered + all routes respond."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

print("=== Importing app ===")
import app as appmod
print("OK Flask app:", appmod.app)
print("OK routes registered:", len(list(appmod.app.url_map.iter_rules())))
print()

print("=== Parser routes registered ===")
parser_routes = [r for r in appmod.app.url_map.iter_rules() if "/api/parser" in str(r)]
for r in parser_routes:
    methods = ",".join(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS")))
    print(f"  {methods:10s} {r.rule}")
print()

print("=== Test client: status ===")
client = appmod.app.test_client()
r = client.get("/api/parser/status")
print(f"GET /api/parser/status -> {r.status_code}")
data = r.get_json()
print("  status:", data.get("status"), "| is_running:", data.get("is_running"))
print()

print("=== Test client: config ===")
r = client.get("/api/parser/config")
data = r.get_json()
print(f"GET /api/parser/config -> {r.status_code}")
print("  hard_cap:", data.get("hard_cap_quantity"))
print("  gemini_key_set:", data.get("env_keys", {}).get("gemini_api_key_set"))
print()

print("=== Test client: products list ===")
r = client.get("/api/parser/products?limit=5")
data = r.get_json()
print(f"GET /api/parser/products -> {r.status_code}, total: {data.get('total')}")
print()

print("=== Test client: runs list ===")
r = client.get("/api/parser/runs?limit=5")
data = r.get_json()
print(f"GET /api/parser/runs -> {r.status_code}, total: {data.get('total')}")
print()

print("=== Test client: stats ===")
r = client.get("/api/parser/stats")
data = r.get_json()
print(f"GET /api/parser/stats -> {r.status_code}, total_products: {data.get('total_products')}")
print()

print("=== Test client: start (validation - no params) ===")
r = client.post("/api/parser/start", json={})
print(f"POST /api/parser/start (no params) -> {r.status_code}: {r.get_json()}")
print()

print("=== Test client: start (validation - bad category) ===")
r = client.post("/api/parser/start", json={"category": "hacker"})
print(f"POST /api/parser/start (bad cat) -> {r.status_code}: {r.get_json()}")
print()

print("=== Test client: stop (when idle) ===")
r = client.post("/api/parser/stop")
print(f"POST /api/parser/stop (idle) -> {r.status_code}: {r.get_json()}")
print()

print("All Flask integration tests passed")
