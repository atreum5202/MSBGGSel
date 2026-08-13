"""Test different payloads for /api/env/setProxy/batch."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient


async def main():
    async with MoreLoginClient() as c:
        http = await c._ensure_http()
        eid = "2084336471078670336"

        # Try the original create/quick with proxyInfo instead of setProxy/batch
        print("=== Trying /api/env/update with proxyInfo ===")
        body = {
            "envId": eid,
            "envName": "ristarel1@outlook.com",
            "proxyInfo": {
                "proxyType": "socks5",
                "host": "152.232.74.90",
                "port": 9080,
                "proxyUser": "Ugfnp7",
                "proxyPassword": "E895Eg",
            },
        }
        resp = await http.post("/api/env/update", json=body)
        d = json.loads(resp.text)
        print(f"  code={d.get('code')} msg={d.get('msg')}")
        # check
        resp2 = await http.post("/api/env/detail", json={"envId": eid})
        d2 = json.loads(resp2.text)["data"]
        print(f"  proxyId={d2.get('proxyId')}, proxy={d2.get('proxy')}")

        print("\n=== Trying /api/env/update with proxy field (no proxyId) ===")
        body = {
            "envId": eid,
            "proxy": {
                "proxyType": "socks5",
                "proxyIp": "152.232.74.90",
                "proxyPort": 9080,
                "proxyUser": "Ugfnp7",
                "proxyPassword": "E895Eg",
            },
        }
        resp = await http.post("/api/env/update", json=body)
        d = json.loads(resp.text)
        print(f"  code={d.get('code')} msg={d.get('msg')}")
        resp2 = await http.post("/api/env/detail", json={"envId": eid})
        d2 = json.loads(resp2.text)["data"]
        print(f"  proxyId={d2.get('proxyId')}, proxy={d2.get('proxy')}")

        # Maybe the field is "proxyCustom" with create+update combined?
        print("\n=== Listing existing proxies in MoreLogin ===")
        for ep in ["/api/proxy/page", "/api/proxy/list", "/api/proxies/list"]:
            resp = await http.post(ep, json={"pageNo": 1, "pageSize": 50})
            if resp.status_code == 200:
                d = json.loads(resp.text)
                if d.get("code") == 0:
                    print(f"  {ep}: {d.get('data')}")


if __name__ == "__main__":
    asyncio.run(main())
