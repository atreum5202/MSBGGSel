"""
fix_4_proxies_v2.py — финальный фикс:
  1) /api/proxyInfo/add  — зарегистрировать 4 SOCKS5 прокси в пуле, получить proxyId
  2) /api/env/update     — переименовать профиль, проставить envRemark, привязать proxyId
  3) /api/env/detail     — верифицировать
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient  # noqa: E402

# envId из create_4_proxies.py + прокси
PROFILES: List[Dict[str, Any]] = [
    {
        "envId": "2086579066752274432",
        "host": "185.148.24.128",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        # 0=http 1=https 2=socks5 3=ssh
        "proxyProvider": 2,
        "proxyType": 2,
    },
    {
        "envId": "2086579071110156288",
        "host": "185.148.27.78",
        "port": 8000,
        "username": "hwXLQL",
        "password": "82Kv91",
        "proxyProvider": 2,
        "proxyType": 2,
    },
    {
        "envId": "2086579075962966016",
        "host": "147.45.59.102",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "proxyProvider": 2,
        "proxyType": 2,
    },
    {
        "envId": "2086579080215990272",
        "host": "147.45.57.59",
        "port": 8000,
        "username": "qYesko",
        "password": "zMnYyh",
        "proxyProvider": 2,
        "proxyType": 2,
    },
]


def make_name(p: Dict[str, Any]) -> str:
    return f"px-{p['host'].replace('.', '-')}-{p['username']}"


def make_remark(p: Dict[str, Any]) -> str:
    return f"socks5 {p['host']}:{p['port']} {p['username']}"


async def add_proxy(client: MoreLoginClient, p: Dict[str, Any]) -> Optional[int]:
    """POST /api/proxyInfo/add — зарегистрировать прокси в пуле. Возвращает proxyId."""
    body = {
        "proxyProvider": p["proxyProvider"],
        "proxyType": p["proxyType"],
        "proxyIp": p["host"],
        "proxyPort": int(p["port"]),
        "proxyName": make_name(p),
        "username": p["username"],
        "password": p["password"],
    }
    data = await client._post("/api/proxyInfo/add", body=body)
    if not isinstance(data, dict):
        return None
    pid = data.get("id") or data.get("proxyId")
    if pid is None:
        # Sometimes the response is {code, msg, data: {id: ...}}
        inner = data.get("data") if "data" in data else None
        if isinstance(inner, dict):
            pid = inner.get("id") or inner.get("proxyId")
    if pid is None:
        print(f"   [WARN] не получили proxyId из ответа: {json.dumps(data, ensure_ascii=False)[:200]}")
        return None
    return int(pid)


async def find_existing_proxy(client: MoreLoginClient, p: Dict[str, Any]) -> Optional[int]:
    """Проверить, есть ли уже в пуле прокси с таким IP:port:user — и вернуть его id, чтобы не дублировать."""
    try:
        data = await client._post(
            "/api/proxyInfo/page",
            body={"pageNo": 1, "pageSize": 100, "proxyIp": p["host"]},
            timeout=10,
        )
    except Exception:
        return None
    items = []
    if isinstance(data, dict):
        items = data.get("list") or data.get("dataList") or data.get("data") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        if (
            it.get("proxyIp") == p["host"]
            and int(it.get("proxyPort") or 0) == int(p["port"])
            and (it.get("username") or "") == p["username"]
        ):
            pid = it.get("id") or it.get("proxyId")
            if pid is not None:
                return int(pid)
    return None


async def attach_proxy(client: MoreLoginClient, env_id: str, proxy_id: int, name: str, remark: str) -> dict:
    """POST /api/env/update — обновить имя/remark/привязать proxyId."""
    return await client._post(
        "/api/env/update",
        body={
            "envId": env_id,
            "envName": name,
            "envRemark": remark,
            "proxyId": proxy_id,
        },
    )


async def main():
    print(f"Шаг 1. Регистрирую {len(PROFILES)} прокси в пуле MoreLogin...\n")

    async with MoreLoginClient() as client:
        h = await client.health()
        if not h.get("ok"):
            print(f"[!] MoreLogin недоступен: {h}")
            return
        print(f"  [OK] MoreLogin: latency={h['latency_ms']}ms\n")

        results: List[Dict[str, Any]] = []
        for p in PROFILES:
            name = make_name(p)
            print(f"-> {p['envId']}  {name}")

            existing_pid = await find_existing_proxy(client, p)
            if existing_pid:
                print(f"   [OK] прокси уже в пуле, proxyId={existing_pid}")
                proxy_id = existing_pid
            else:
                try:
                    proxy_id = await add_proxy(client, p)
                except Exception as e:
                    print(f"   [ERR proxyInfo/add] {e}")
                    results.append({"envId": p["envId"], "ok": False, "stage": "proxy_add", "error": str(e)})
                    continue
                if proxy_id is None:
                    print("   [ERR] не получили proxyId")
                    results.append({"envId": p["envId"], "ok": False, "stage": "proxy_add", "error": "no proxyId"})
                    continue
                print(f"   [OK] прокси добавлен, proxyId={proxy_id}")

            try:
                upd = await attach_proxy(client, p["envId"], proxy_id, name, make_remark(p))
                print(f"   [OK] env/update -> {json.dumps(upd, ensure_ascii=False)[:200]}")
            except Exception as e:
                print(f"   [ERR env/update] {e}")
                results.append({"envId": p["envId"], "ok": False, "stage": "env_update", "error": str(e), "proxyId": proxy_id})
                continue

            results.append({
                "envId": p["envId"],
                "ok": True,
                "name": name,
                "remark": make_remark(p),
                "proxyId": proxy_id,
                "host": p["host"],
                "port": p["port"],
                "username": p["username"],
            })

        # ── Верификация ────────────────────────────────────────────────────
        print("\n\nВерификация через /api/env/detail:")
        for p in PROFILES:
            detail = await client.get_profile(p["envId"])
            if not detail:
                print(f"  {p['envId']}: <не найден>")
                continue
            print(
                f"  {detail.get('name'):<32} "
                f"proxyId={detail.get('_raw', {}).get('proxyId')}  "
                f"proxy={json.dumps(detail.get('proxy') or {}, ensure_ascii=False)}"
            )

        ok = sum(1 for r in results if r.get("ok"))
        print(f"\nГотово. Успешно: {ok}/{len(PROFILES)}")
        print("\nСводка:")
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
