"""Quick inspector to peek at any section's contents."""
import json
import sys
from pathlib import Path

target = sys.argv[1] if len(sys.argv) > 1 else "orders"
path = Path(__file__).parent / f"{target}.json"
data = json.loads(path.read_text(encoding="utf-8"))

print(f"== {target} ==")
print(f"Top-level keys: {list(data.keys())}")
print()

if target == "orders":
    for k, v in data.items():
        if isinstance(v, dict) and "items" in v:
            print(f"orders[{k!r}].items = {len(v['items'])}")
            for pg in v.get("pages", []):
                if "error" in pg:
                    print(f"  page {pg['page']} ERROR: {pg['error']}")
                else:
                    print(f"  page {pg['page']}: {pg.get('count')} items, pagination={pg.get('pagination')}")
            if v["items"]:
                print(f"  first item: {v['items'][0].get('id')} - {v['items'][0].get('offer_title')}")
        elif isinstance(v, dict):
            print(f"orders[{k!r}] keys: {list(v.keys())[:5]}")
        else:
            print(f"orders[{k!r}] = {str(v)[:80]}")
elif target == "offers":
    for k, v in data.get("by_status", {}).items():
        if isinstance(v, list):
            print(f"offers[by_status][{k!r}] = {len(v)} items")
        else:
            print(f"offers[by_status][{k!r}] = {v}")
    print(f"offers[filter_list] = {len(data.get('filter_list', []))} items")
elif target == "chats":
    print(f"chats[conversations] = {len(data.get('conversations', []))}")
    for m in data.get("messages", []):
        print(f"  conv {m['conversation_id']}: {len(m['messages'])} message(s)")
elif target == "ledger":
    print(f"ledger[items] = {len(data.get('items', []))}")
    for it in data.get("items", [])[:3]:
        print(f"  - id={it.get('id')} amount={it.get('amount')} date={it.get('date') or it.get('created_at')}")
else:
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
