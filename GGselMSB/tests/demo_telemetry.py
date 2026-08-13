"""Демо telemetry — пишет события и показывает первые строки JSONL."""
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

from parser.telemetry import Telemetry  # noqa: E402

t = Telemetry(storage_dir="data/telemetry", enabled=True)

print("Telemetry запущена, эмитим 5 событий...")

# Типичный сценарий одного запуска парсера
t.emit("parser.start", query="games", category="games", quantity=5, profile_count=49)
t.emit("parser.page_fetched", profile_id="abc-123", status=200, latency_ms=234, rate_delay=4.0, is_challenge=False)
t.emit("parser.cookies_refreshed", profile_id="abc-123", source="snapshot", took_ms=15, cookies_count=12)
t.emit("parser.product_saved", category="games", ai_enriched=True, took_ms=320)
t.emit("parser.run_complete", total_products=5, total_pages=1, duration_sec=12.4, status="done")

# Демо санитизации cookies (не должны попасть в файл)
t.emit("parser.cookies_refreshed",
       profile_id="secret-profile",
       cookies={"__qrator_jsid": "VERY_SECRET_VALUE_123", "session": "ANOTHER_SECRET_456"},
       source="msb")

print()
print("Первые 5 строк events.jsonl:")
print("=" * 60)
events_file = Path("data/telemetry/events.jsonl")
if events_file.exists():
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    for line in lines[:5]:
        print(line)
print("=" * 60)
print()
print("Проверка: значения cookies НЕ попали в файл?")
content = events_file.read_text(encoding="utf-8")
print(f"  'VERY_SECRET_VALUE_123' in file: {'VERY_SECRET_VALUE_123' in content}  (должно быть False)")
print(f"  'ANOTHER_SECRET_456'   in file: {'ANOTHER_SECRET_456' in content}  (должно быть False)")
print(f"  'cookies_count'        in file: {'cookies_count' in content}  (должно быть True)")
print(f"  'cookies_names'        in file: {'cookies_names' in content}  (должно быть True)")
