"""probe_proxy_add.py — посмотреть сырой ответ /api/proxyInfo/add."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient


async def main():
    async with MoreLoginClient() as client:
        # bypass the code-check by calling httpx directly
        http = await client._ensure_http()
        body = {
            "proxyProvider": 2,
            "proxyType": 2,
            "proxyIp": "185.148.24.128",
            "proxyPort": 8000,
            "proxyName": "probe-px-1",
            "username": "hwXLQL",
            "password": "82Kv91",
        }
        print("REQUEST:", json.dumps(body, ensure_ascii=False))
        r = await http.post("/api/proxyInfo/add", json=body)
        print("STATUS:", r.status_code)
        print("RAW RESPONSE:", r.text[:1000])
        print()
        # also try /api/proxyInfo/page
        r2 = await http.post("/api/proxyInfo/page", json={"pageNo": 1, "pageSize": 5})
        print("PAGE STATUS:", r2.status_code)
        print("PAGE RAW RESPONSE:", r2.text[:2000])


asyncio.run(main())
