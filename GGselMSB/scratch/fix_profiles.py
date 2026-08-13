"""
fix_profiles.py — навести порядок в GGSeller:
  1. Удалить мусорные профили, которые MoreLogin создал с именами P-130..P-135
  2. У 5 моих профилей (P-136..P-140) обновить имя и прицепить прокси
  3. Удалить тестовый профиль P-135 (test from script)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient, MoreLoginError  # noqa: E402

# envId → (account name, proxy dict)
MY_PROFILES = {
    "2084336471078670336": {
        "name": "ristarel1@outlook.com",
        "proxy": {"protocol": "socks5", "host": "152.232.74.90", "port": 9080,
                  "username": "Ugfnp7", "password": "E895Eg"},
    },
    "2084336475029704704": {
        "name": "vernadilo1@outlook.com",
        "proxy": {"protocol": "socks5", "host": "168.196.238.232", "port": 9081,
                  "username": "Ugfnp7", "password": "E895Eg"},
    },
    "2084336479580524544": {
        "name": "vernadilo2@outlook.com",
        "proxy": {"protocol": "socks5", "host": "168.196.239.19", "port": 9129,
                  "username": "Ugfnp7", "password": "E895Eg"},
    },
    "2084336484026486784": {
        "name": "biranol@outlook.com",
        "proxy": {"protocol": "socks5", "host": "185.148.26.247", "port": 8000,
                  "username": "LEjyAb", "password": "MJ7f34"},
    },
    "2084336489676214272": {
        "name": "ggparser-proxy1",
        "proxy": {"protocol": "socks5", "host": "185.183.163.219", "port": 8000,
                  "username": "phdA8V", "password": "6jpA38"},
    },
}

# Профили, которые надо удалить (созданы MoreLogin-ом и/или моим тестом)
PROFILES_TO_DELETE = [
    "2084336353835290624",  # P-135 (мой тестовый "test from script")
    "2084336270968426496",  # P-134
    "2084336268460232704",  # P-133
    "2084336265754906624",  # P-132
    "2084336262940528640",  # P-131
    "2084336259497005056",  # P-130
]


async def main():
    async with MoreLoginClient() as c:
        h = await c.health()
        if not h.get("ok"):
            print("MoreLogin недоступен:", h)
            return

        # 1) Удаляем мусорные
        print("Удаляю мусорные профили...")
        await c.bulk_delete_profiles(PROFILES_TO_DELETE)
        print(f"  -> удалено {len(PROFILES_TO_DELETE)} шт.")

        # 2) Обновляем имена + ставим прокси
        http = await c._ensure_http()
        for env_id, info in MY_PROFILES.items():
            # 2a) Имя
            try:
                await http.post(
                    "/api/env/update",
                    json={"envId": env_id, "envName": info["name"]},
                )
                print(f"  [OK] {env_id} -> name={info['name']}")
            except Exception as e:
                print(f"  [ERR] rename {env_id}: {e}")

            # 2b) Прокси через /api/env/setProxy/batch
            p = info["proxy"]
            proxy_body = {
                "envIds": [int(env_id) if env_id.isdigit() else env_id],
                "proxy": {
                    "proxyType": p["protocol"].replace("socks5", "socks5").replace("http", "http"),
                    "proxyIp": p["host"],
                    "proxyPort": int(p["port"]),
                    "proxyUser": p.get("username", ""),
                    "proxyPassword": p.get("password", ""),
                },
            }
            try:
                resp = await http.post("/api/env/setProxy/batch", json=proxy_body)
                body = resp.text[:500]
                print(f"        setProxy: {resp.status_code} {body}")
            except Exception as e:
                print(f"  [ERR] setProxy {env_id}: {e}")

        # 3) Финальная сверка
        print("\n\nФинальная проверка:")
        groups = await c.get_groups()
        gid = next(g["id"] for g in groups if g["name"] == "GGSeller")
        profiles = await c.get_profiles(group_id=gid)
        print(f"  В GGSeller осталось {len(profiles)} профилей:\n")
        print(f"  {'envId':<22} {'name':<30} {'proxy':<35} {'type'}")
        print("  " + "-" * 100)
        for p in profiles:
            eid = p.get("envId")
            name = p.get("name")
            prx = p.get("proxy") or {}
            host = prx.get("host", "")
            port = prx.get("port", "")
            ptype = prx.get("proxyType") or prx.get("protocol") or ""
            print(f"  {eid:<22} {name:<30} {host}:{port:<25} {ptype}")


if __name__ == "__main__":
    asyncio.run(main())
