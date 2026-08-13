"""make_report.py — generate a friendly text report from the saved files."""
import json
from datetime import datetime
from pathlib import Path

base = Path(r"C:\Users\Atreum\Desktop\ggseller_data\20260812_195934")

profile = json.loads((base / "profile.json").read_text(encoding="utf-8"))
offers = json.loads((base / "offers.json").read_text(encoding="utf-8"))
orders = json.loads((base / "orders.json").read_text(encoding="utf-8"))
ledger = json.loads((base / "ledger.json").read_text(encoding="utf-8"))
promo = json.loads((base / "promo_codes.json").read_text(encoding="utf-8"))
chats = json.loads((base / "chats.json").read_text(encoding="utf-8"))
categories = json.loads((base / "categories.json").read_text(encoding="utf-8"))

lines = []
lines.append("=" * 64)
lines.append("  GGSeller data snapshot")
lines.append(f"  Generated: {datetime.now().isoformat(timespec='seconds')}")
lines.append("=" * 64)
lines.append("")

# User
me = profile.get("me", {}).get("data", {})
lines.append(f"USER")
lines.append(f"  Username : {me.get('username')}")
lines.append(f"  Email    : {me.get('email')}")
lines.append(f"  ID       : {me.get('id')}  (ggsel_id: {me.get('ggsel_id')})")
lines.append(f"  Status   : {me.get('status')}")
lines.append(f"  Locale   : {me.get('locale')}")
lines.append("")

bal = profile.get("balance", {}).get("data", {})
lines.append(f"BALANCE")
lines.append(f"  Available  : {bal.get('amount')}")
lines.append(f"  On hold    : {bal.get('amount_in_hold')}")
lines.append("")

# API keys
apikeys = profile.get("api_keys", {}).get("data", [])
lines.append(f"API KEYS  ({len(apikeys)} active)")
for k in apikeys:
    perms_total = sum(len(v) for v in k.get("permissions", {}).values())
    lines.append(f"  - {k.get('title')!r}  id={k.get('id')}  perms={perms_total}  created={k.get('created_at')[:10]}")
lines.append("")

# Offers
by_status = offers.get("by_status", {})
total_offers = sum(len(v) for v in by_status.values() if isinstance(v, list))
lines.append(f"OFFERS  ({total_offers} total)")
for status, items in by_status.items():
    if isinstance(items, list):
        lines.append(f"  - {status:10}  {len(items)}")
lines.append("")

# Orders (de-dup)
all_orders_seen = set()
total_orders = 0
for status, v in orders.items():
    if isinstance(v, dict) and "items" in v:
        for it in v["items"]:
            oid = it.get("id")
            if oid not in all_orders_seen:
                all_orders_seen.add(oid)
                total_orders += 1
lines.append(f"ORDERS  ({total_orders} unique)")
for it in [i for s, v in orders.items() if isinstance(v, dict) and "items" in v for i in v["items"][:3]]:
    lines.append(f"  - #{it.get('id')}  {it.get('offer_title')[:40]}  {it.get('amount')} {it.get('currency')}  status={it.get('status')}")
lines.append("")

# Ledger
ledger_items = ledger.get("items", [])
total_amount = sum(it.get("amount", 0) for it in ledger_items if isinstance(it.get("amount"), (int, float)))
lines.append(f"LEDGER  ({len(ledger_items)} entries, sum={total_amount:.4f})")
for it in ledger_items[:5]:
    lines.append(f"  - {it.get('date', it.get('created_at', '?'))[:10]}  {it.get('amount')}  {it.get('description', '')[:40]}")
lines.append("")

# Promo
promo_list = promo.get("list", [])
lines.append(f"PROMO CODES  ({len(promo_list)})")
for p in promo_list:
    lines.append(f"  - {p.get('code') or p.get('title')!r}  status={p.get('status')}  uses={p.get('uses_count')}")
lines.append("")

# Chats
convos = chats.get("conversations", [])
total_msgs = sum(len(m["messages"]) for m in chats.get("messages", []))
lines.append(f"CHATS  ({len(convos)} conversations, {total_msgs} messages total)")
for c in convos[:5]:
    lines.append(f"  - conv {c.get('id')}  buyer={c.get('buyer_username') or c.get('title', '?')[:30]}")
lines.append("")

# Categories
cat_list = categories.get("list", [])
lines.append(f"CATEGORIES  ({len(cat_list)} top-level)")
for c in cat_list[:5]:
    lines.append(f"  - [{c.get('id')}] {c.get('title')!r}")
lines.append("")

# Files
lines.append("FILES")
for p in sorted(base.glob("*.json")):
    size = p.stat().st_size
    lines.append(f"  - {p.name:24}  {size:>7} bytes")
lines.append("")
lines.append("=" * 64)

text = "\n".join(lines)
(base / "REPORT.txt").write_text(text, encoding="utf-8")
print(text)
