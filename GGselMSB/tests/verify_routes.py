"""Проверка что все эндпоинты зарегистрированы."""
import sys
import io
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

print("=" * 60)
print("Зарегистрированные эндпоинты /api/parser/*:")
print("=" * 60)
parser_routes = []
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    if rule.rule.startswith("/api/parser/"):
        methods = sorted(rule.methods - {"OPTIONS", "HEAD"})
        parser_routes.append((rule.rule, methods))
        methods_str = ",".join(methods)
        print(f"  {methods_str:<10}  {rule.rule}")

print()
print(f"Всего: {len(parser_routes)} эндпоинтов")
print()

# Проверим что новые есть
required = [
    ("/api/parser/msb/status", "GET"),
    ("/api/parser/msb/refresh/<profile_id>", "POST"),
    ("/api/parser/msb/rate", "GET"),
    ("/api/parser/msb/rate/reset", "POST"),
    ("/api/parser/msb/reset-errors", "POST"),
    ("/api/parser/telemetry/recent", "GET"),
    ("/api/parser/telemetry/stats", "GET"),
    ("/api/parser/telemetry/enabled", "POST"),
]
print("Проверка новых эндпоинтов из Шага 8:")
for path, method in required:
    found = any(r[0] == path and method in r[1] for r in parser_routes)
    status = "OK" if found else "MISSING"
    print(f"  [{status}] {method:<6} {path}")
