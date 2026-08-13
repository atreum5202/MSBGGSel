"""verify_4_proxies_detail.py — проверка прокси через /api/env/detail для 4 профилей."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient

ENV_IDS = [
    "2086579066752274432",
    "2086579071110156288",
    "2086579075962966016",
    "2086579080215990272",
]


async def main():
    async with MoreLoginClient() as client:
        h = await client.health()
        print(f"MoreLogin ok={h['ok']} latency={h['latency_ms']}ms")
        print()
        for eid in ENV_IDS:
            detail = await client.get_profile(eid)
            if not detail:
                print(f"{eid}: <not found>")
                continue
            proxy = detail.get("proxy") or {}
            print(f"{eid}")
            print(f"  name      = {detail.get('name')}")
            print(f"  groupId   = {detail.get('groupId')}")
            print(f"  groupName = {detail.get('groupName')}")
            print(f"  proxy     = {json.dumps(proxy, ensure_ascii=False)}")
            print(f"  remark    = {detail.get('remark')}")
            raw = detail.get("_raw", {})
            # Печатаем только ключи, относящиеся к прокси, чтобы понять, что реально в API
            proxy_keys = {k: v for k, v in raw.items() if "proxy" in k.lower() or k in ("host", "port")}
            print(f"  raw.proxy* = {json.dumps(proxy_keys, ensure_ascii=False)}")
            print()


asyncio.run(main())
