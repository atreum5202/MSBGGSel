"""verify_4_proxies_v3.py — финальная проверка: envId → proxyId → proxyInfo в пуле."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient

PROFILES = [
    ("2086579066752274432", "px-185-148-24-128-hwXLQL"),
    ("2086579071110156288", "px-185-148-27-78-hwXLQL"),
    ("2086579075962966016", "px-147-45-59-102-qYesko"),
    ("2086579080215990272", "px-147-45-57-59-qYesko"),
]


async def main():
    async with MoreLoginClient() as client:
        print(f"{'name':<32} {'envId':<22} {'proxyId':<22} {'proxy info'}")
        print("-" * 100)
        for env_id, name in PROFILES:
            detail = await client.get_profile(env_id)
            if not detail:
                print(f"{name:<32} {env_id:<22} <not found>")
                continue
            raw = detail.get("_raw", {})
            proxy_id = raw.get("proxyId")
            if not proxy_id or proxy_id == "0":
                print(f"{name:<32} {env_id:<22} proxyId=<none>")
                continue

            # достаём прокси из пула
            data = await client._post("/api/proxyInfo/page", body={"pageNo": 1, "pageSize": 100})
            items = []
            if isinstance(data, dict):
                d = data.get("data")
                if isinstance(d, dict):
                    items = d.get("dataList") or d.get("list") or []
                else:
                    items = data.get("list") or data.get("dataList") or []
            match = next((x for x in items if str(x.get("id") or x.get("proxyId")) == str(proxy_id)), None)
            if match:
                proto = {0: "http", 1: "https", 2: "socks5"}.get(match.get("proxyType"), str(match.get("proxyType")))
                info = f"{proto}://{match.get('username')}:***@{match.get('proxyIp')}:{match.get('proxyPort')}"
                print(f"{name:<32} {env_id:<22} {str(proxy_id):<22} {info}")
            else:
                print(f"{name:<32} {env_id:<22} {str(proxy_id):<22} <not in pool>")


asyncio.run(main())
