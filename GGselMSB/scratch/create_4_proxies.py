"""
create_4_proxies.py — создание 4 профилей в MoreLogin с 4 SOCKS5-прокси.

Источник прокси: хардкод в PROXIES (proxy6.net, IPv4, порт 8000).
Каждый -> новый профиль в группе GGSeller.

Использование:
  cd C:\\Users\\Atreum\\Desktop\\MySoft\\GgsellerMoreLogin
  python scratch\\create_4_proxies.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient, MoreLoginError  # noqa: E402

# ── Конфиг ──────────────────────────────────────────────────────────────

GROUP_NAME = "GGSeller"

# 4 прокси proxy6.net (socks5, IPv4, порт 8000)
# Поля: host, port, username, password, protocol
PROXIES: List[Dict[str, Any]] = [
    {
        "host": "185.148.24.128",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        "protocol": "socks5",
    },
    {
        "host": "185.148.27.78",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        "protocol": "socks5",
    },
    {
        "host": "147.45.59.102",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "protocol": "socks5",
    },
    {
        "host": "147.45.57.59",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "protocol": "socks5",
    },
]


def proxy_to_payload(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """
    MoreLogin ожидает proxy в формате:
      {"proxyType": "socks5"|"http"|"https",
       "host": ..., "port": int,
       "proxyUser": "...", "proxyPassword": "..."}
    """
    return {
        "proxyType": proxy.get("protocol", "socks5").lower(),
        "host": proxy["host"],
        "port": int(proxy["port"]),
        "proxyUser": proxy.get("username", ""),
        "proxyPassword": proxy.get("password", ""),
    }


def make_profile_name(proxy: Dict[str, Any]) -> str:
    """Имя профиля: px-<короткий хост>-<логин> (уникально + читаемо)."""
    host_short = proxy["host"].replace(".", "-")
    return f"px-{host_short}-{proxy['username']}"


async def ensure_group(client: MoreLoginClient, name: str) -> int:
    """Создаёт группу если её нет, возвращает её ID."""
    groups = await client.get_groups()
    for g in groups:
        if g.get("name") == name:
            gid = g.get("id")
            print(f"  [OK] группа '{name}' уже есть (id={gid})")
            return int(gid)

    data = await client._post("/api/envgroup/create", body={"groupName": name})
    if not isinstance(data, dict):
        raise MoreLoginError(f"не удалось создать группу '{name}': {data!r}")
    gid = data.get("id") or data.get("groupId")
    if not gid:
        raise MoreLoginError(f"в ответе нет id группы: {data!r}")
    print(f"  [OK] создана группа '{name}' (id={gid})")
    return int(gid)


async def find_existing_for_proxy(
    client: MoreLoginClient,
    group_id: int,
    proxy: Dict[str, Any],
) -> Optional[dict]:
    """Ищем профиль в группе с тем же host/port/user — чтобы не плодить дубли."""
    try:
        profiles = await client.get_profiles(group_id=group_id)
    except Exception:
        return None
    for p in profiles:
        pinfo = p.get("proxy") or {}
        if (
            pinfo.get("host") == proxy["host"]
            and int(pinfo.get("port") or 0) == int(proxy["port"])
            and (pinfo.get("proxyUser") or pinfo.get("username")) == proxy["username"]
        ):
            return p
    return None


async def main():
    print(f"Создаю {len(PROXIES)} профилей в MoreLogin (группа {GROUP_NAME!r}):\n")
    for p in PROXIES:
        print(f"  - {make_profile_name(p):<32} {p['protocol']}://{p['username']}:***@{p['host']}:{p['port']}")

    async with MoreLoginClient() as client:
        h = await client.health()
        if not h.get("ok"):
            print(f"\n[!] MoreLogin недоступен: {h}")
            return
        print(f"\n  [OK] MoreLogin: latency={h['latency_ms']}ms, base={h['base_url']}")

        group_id = await ensure_group(client, GROUP_NAME)
        print(f"  group_id = {group_id}")

        results: List[Dict[str, Any]] = []
        for p in PROXIES:
            name = make_profile_name(p)
            proxy_payload = proxy_to_payload(p)
            print(f"\n  -> {name}  (proxy {proxy_payload['host']}:{proxy_payload['port']})")

            # Проверка на дубликат
            existing = await find_existing_for_proxy(client, group_id, p)
            if existing:
                eid = existing.get("envId") or existing.get("id")
                print(f"    [SKIP] уже есть профиль с этим прокси: envId={eid} name={existing.get('name')}")
                results.append({
                    "name": name,
                    "ok": True,
                    "skipped": True,
                    "envId": eid,
                    "proxy": proxy_payload,
                })
                continue

            try:
                created = await client.create_profile(
                    name=name,
                    browser_type_id=1,    # Chrome
                    os_id=1,              # Windows
                    group_id=group_id,
                    proxy=proxy_payload,
                    remark=f"socks5 {p['host']}:{p['port']} {p['username']}",
                )
            except MoreLoginError as e:
                print(f"    [ERR] {e}")
                results.append({"name": name, "ok": False, "error": str(e), "proxy": proxy_payload})
                continue

            if not created:
                print("    [ERR] create_profile вернул None")
                results.append({"name": name, "ok": False, "error": "create_profile returned None", "proxy": proxy_payload})
                continue

            env_id = created.get("envId") or created.get("id")
            print(f"    [OK] создан envId={env_id}")
            results.append({
                "name": name,
                "ok": True,
                "envId": env_id,
                "proxy": proxy_payload,
            })

        # Финальная сверка
        print("\n\nПроверка: профили в группе GGSeller (фильтр по нашим прокси):")
        all_in_group = await client.get_profiles(group_id=group_id)
        for r in results:
            if not r.get("ok"):
                continue
            match = next((p for p in all_in_group if p.get("envId") == r["envId"]), None)
            if not match:
                print(f"  [WARN] {r['name']} (envId={r['envId']}) — не найден в группе")
                continue
            proxy_info = match.get("proxy") or {}
            print(
                f"  - {r['name']:<32} envId={r['envId']}  "
                f"proxy={proxy_info.get('host')}:{proxy_info.get('port')}  "
                f"type={proxy_info.get('proxyType') or proxy_info.get('protocol')}"
            )

        # Сводка
        ok_count = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
        skip_count = sum(1 for r in results if r.get("skipped"))
        err_count = sum(1 for r in results if not r.get("ok"))
        print(f"\nГотово. создано={ok_count}, уже было={skip_count}, ошибок={err_count}")
        print("\nСводка (JSON):")
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
