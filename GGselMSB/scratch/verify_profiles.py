"""Проверка что 5 профилей созданы в GGSeller и у них правильные прокси."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient


async def main():
    async with MoreLoginClient() as c:
        h = await c.health()
        if not h.get("ok"):
            print("MoreLogin недоступен:", h)
            return

        groups = await c.get_groups()
        ggseller = next((g for g in groups if g.get("name") == "GGSeller"), None)
        if not ggseller:
            print("Группа GGSeller не найдена")
            return
        gid = ggseller.get("id")
        print(f"Группа GGSeller id={gid}")

        profiles = await c.get_profiles(group_id=gid)
        print(f"\nНайдено {len(profiles)} профилей в GGSeller:\n")
        print(f"  {'envId':<22} {'name':<30} {'proxy':<35} {'type'}")
        print("  " + "-" * 100)
        for p in profiles:
            env_id = p.get("envId") or p.get("id")
            name = p.get("name")
            proxy = p.get("proxy") or {}
            host = proxy.get("host", "")
            port = proxy.get("port", "")
            ptype = proxy.get("proxyType") or proxy.get("protocol") or ""
            proxy_str = f"{host}:{port}"
            print(f"  {env_id:<22} {name:<30} {proxy_str:<35} {ptype}")


if __name__ == "__main__":
    asyncio.run(main())
