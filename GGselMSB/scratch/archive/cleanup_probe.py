"""cleanup_probe.py — удалить отладочный прокси probe-px-1, оставшийся от диагностики."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient


async def main():
    async with MoreLoginClient() as client:
        data = await client._post("/api/proxyInfo/page", body={"pageNo": 1, "pageSize": 100})
        d = data.get("data") if isinstance(data, dict) else None
        items = d.get("dataList", []) if isinstance(d, dict) else []
        for it in items:
            if it.get("proxyName") == "probe-px-1":
                pid = it.get("id")
                print(f"deleting probe-px-1 (id={pid})")
                resp = await client._post("/api/proxyInfo/delete", body={"ids": [int(pid)]})
                print("resp:", json.dumps(resp, ensure_ascii=False))
                return
        print("probe-px-1 not found, nothing to do")


asyncio.run(main())
