"""
fix_4_proxies.py — починить 4 только что созданных профиля:
  1) переименовать P-2..P-5 в человеко-читаемые px-<ip>-<user>
  2) привязать socks5 прокси через /api/proxyInfo/update
  3) проставить remark
  4) верифицировать через /api/env/detail
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient  # noqa: E402

# envId из create_4_proxies.py
BROKEN_PROFILES = [
    {
        "envId": "2086579066752274432",
        "host": "185.148.24.128",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        "protocol": "socks5",
    },
    {
        "envId": "2086579071110156288",
        "host": "185.148.27.78",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        "protocol": "socks5",
    },
    {
        "envId": "2086579075962966016",
        "host": "147.45.59.102",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "protocol": "socks5",
    },
    {
        "envId": "2086579080215990272",
        "host": "147.45.57.59",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "protocol": "socks5",
    },
]


def make_name(p: Dict[str, Any]) -> str:
    return f"px-{p['host'].replace('.', '-')}-{p['username']}"


def make_remark(p: Dict[str, Any]) -> str:
    return f"socks5 {p['host']}:{p['port']} {p['username']}"


async def set_proxy(client: MoreLoginClient, p: Dict[str, Any]) -> dict:
    """POST /api/proxyInfo/update — прямой способ задать/сменить прокси профиля."""
    body = {
        "id": p["envId"],
        "proxyType": p["protocol"],
        "proxyIp": p["host"],
        "proxyPort": int(p["port"]),
        "username": p["username"],
        "password": p["password"],
    }
    return await client._post("/api/proxyInfo/update", body=body)


async def rename_and_remark(client: MoreLoginClient, env_id: str, name: str, remark: str) -> dict:
    """POST /api/env/update — переименовать + проставить envRemark."""
    return await client._post(
        "/api/env/update",
        body={"envId": env_id, "envName": name, "envRemark": remark},
    )


async def main():
    print(f"Чиню {len(BROKEN_PROFILES)} профилей...\n")

    async with MoreLoginClient() as client:
        h = await client.health()
        if not h.get("ok"):
            print(f"[!] MoreLogin недоступен: {h}")
            return
        print(f"  [OK] MoreLogin: latency={h['latency_ms']}ms\n")

        results: List[Dict[str, Any]] = []
        for p in BROKEN_PROFILES:
            name = make_name(p)
            remark = make_remark(p)
            print(f"-> {p['envId']}  target: {name}")

            try:
                proxy_resp = await set_proxy(client, p)
                print(f"   proxyInfo/update -> {json.dumps(proxy_resp, ensure_ascii=False)[:200]}")
            except Exception as e:
                print(f"   [ERR proxyInfo/update] {e}")
                results.append({"envId": p["envId"], "ok": False, "stage": "proxy", "error": str(e)})
                continue

            try:
                rename_resp = await rename_and_remark(client, p["envId"], name, remark)
                print(f"   env/update       -> {json.dumps(rename_resp, ensure_ascii=False)[:200]}")
            except Exception as e:
                print(f"   [ERR env/update] {e}")
                results.append({"envId": p["envId"], "ok": False, "stage": "rename", "error": str(e), "proxy_resp": proxy_resp})
                continue

            results.append({
                "envId": p["envId"],
                "ok": True,
                "name": name,
                "remark": remark,
                "proxy": {
                    "proxyType": p["protocol"],
                    "host": p["host"],
                    "port": p["port"],
                    "proxyUser": p["username"],
                },
                "proxy_resp": proxy_resp,
                "rename_resp": rename_resp,
            })

        # Верификация — читаем обратно
        print("\n\nВерификация через /api/env/detail:")
        for p in BROKEN_PROFILES:
            detail = await client.get_profile(p["envId"])
            if not detail:
                print(f"  {p['envId']}: <не найден>")
                continue
            print(
                f"  {detail.get('name'):<32} groupName={detail.get('groupName')}  "
                f"proxy={json.dumps(detail.get('proxy') or {}, ensure_ascii=False)}  "
                f"remark={detail.get('remark')}"
            )

        # Доп. проверка: что /api/proxyInfo/list показывает наш прокси (если эндпоинт есть)
        print("\n\nПрямая проверка через /api/proxyInfo/list (если поддерживается):")
        try:
            data = await client._post(
                "/api/proxyInfo/list",
                body={"pageNo": 1, "pageSize": 50},
                timeout=10,
            )
            items = []
            if isinstance(data, dict):
                items = data.get("list") or data.get("dataList") or data.get("data") or []
            for p in BROKEN_PROFILES:
                match = next(
                    (x for x in items if str(x.get("id") or x.get("envId") or "") == p["envId"]),
                    None,
                )
                if match:
                    print(f"  {p['envId']}: {json.dumps(match, ensure_ascii=False)[:200]}")
                else:
                    print(f"  {p['envId']}: <нет в списке>")
        except Exception as e:
            print(f"  (эндпоинт /api/proxyInfo/list недоступен: {e})")

        # Финал
        ok = sum(1 for r in results if r.get("ok"))
        print(f"\nГотово. Успешно починено: {ok}/{len(BROKEN_PROFILES)}")
        print("\nСводка:")
        print(json.dumps(
            [{k: v for k, v in r.items() if k not in ("proxy_resp", "rename_resp")} for r in results],
            indent=2, ensure_ascii=False,
        ))


if __name__ == "__main__":
    asyncio.run(main())
