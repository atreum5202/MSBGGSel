"""Count actual items in saved files, walking nested structures."""
import json
from pathlib import Path

base = Path(r"C:\Users\Atreum\Desktop\ggseller_data\20260812_195934")


def count_items(obj):
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        n = 0
        for v in obj.values():
            n += count_items(v)
        return n
    return 0


print("== Accurate counts ==")
for p in sorted(base.glob("*.json")):
    if p.name.startswith("_"):
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    n = count_items(data)
    size_kb = p.stat().st_size / 1024
    print(f"  {p.name:24}  {n:>6} item(s)  {size_kb:>7.1f} KB")
