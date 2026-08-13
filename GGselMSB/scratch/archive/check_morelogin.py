"""Quick health check before creating profiles."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient


async def main():
    async with MoreLoginClient() as c:
        h = await c.health()
        print("Health:", json.dumps(h, indent=2, ensure_ascii=False))
        if not h.get("ok"):
            print("MoreLogin недоступен. Проверь что приложение запущено и API включён.")
            return

        groups = await c.get_groups()
        print("\nGroups:")
        for g in groups:
            gid = g.get("id")
            gname = g.get("name")
            print(f"  - id={gid}, name={gname}")


if __name__ == "__main__":
    asyncio.run(main())
