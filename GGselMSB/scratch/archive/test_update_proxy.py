import asyncio
import sys
sys.path.insert(0, ".")
from parser.morelogin_client import get_default_client

async def main():
    async with get_default_client() as ml:
        proxy_data = {
            "proxyType": "socks5",
            "host": "185.148.24.128",
            "port": 8000,
            "proxyUser": "hwXLQL",
            "proxyPassword": "82Kv91"
        }
        print("Sending update_profile...")
        res = await ml.update_profile("2084336484026486784", proxyInfo=proxy_data)
        print("Update result:", res)
        
        print("Fetching profile...")
        p = await ml.get_profile("2084336484026486784")
        proxy = p.get('proxy', {})
        print(f"Proxy now: {proxy.get('proxyIp')}:{proxy.get('proxyPort', '8000')}")

if __name__ == "__main__":
    asyncio.run(main())
