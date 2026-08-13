"""
fix_4_proxies_v3.py — финальный рабочий фикс:
  /api/proxyInfo/add возвращает data как строку (proxyId), не dict.
  Прокси уже в пуле — просто находим их и привязываем к профилям.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.morelogin_client import MoreLoginClient  # noqa: E402

PROFILES: List[Dict[str, Any]] = [
    {"envId": "2086579066752274432", "host": "185.148.24.128", "port": 8000, "username": "hwXLQL", "password": "82Kv91"},
    {"envId": "2086579071110156288", "host": "185.148.27.78",  "port": 8000, "username": "hwXLQL", "password": "82Kv91"},
    {"envId": "2086579075962966016", "host": "147.45.59.102", "port": 8000, "username": "qYesko", "password": "zMnYyh"},
    {"envId": "2086579080215990272", "host": "147.45.57.59",  "port": 8000, "username": "qYesko", "password": "zMnYyh"},
]


def make_name(p: Dict[str, Any]) -> str:
    return f"px-{p['host'].replace('.', '-')}-{p['username']}"


def make_remark(p: Dict[str, Any]) -> str:
    return f"socks5 {p['host']}:{p['port']} {p['username']}"


async def add_proxy(client: MoreLoginClient, p: Dict[str, Any]) -> Optional[int]:
    """POST /api/proxyInfo/add → data is a string with proxyId."""
    body = {
        "proxyProvider": 2,
        "proxyType": 2,
        "proxyIp": p["host"],
        "proxyPort": int(p["port"]),
        "proxyName": make_name(p),
        "username": p["username"],
        "password": p["password"],
    }
    data = await client._post("/api/proxyInfo/add", body=body)
    if data is None:
        return None
    # response can be: str (proxyId), int, or dict {id|proxyId}
    if isinstance(data, (str, int)):
        try:
            return int(data)
        except (TypeError, ValueError):
            return None
    if isinstance(data, dict):
        pid = data.get("id") or data.get("proxyId")
        return int(pid) if pid is not None else None
    return None


async def find_proxy_in_pool(client: MoreLoginClient, p: Dict[str, Any]) -> Optional[int]:
    """Ищем уже зарегистрированный прокси с тем же IP:port:user."""
    data = await client._post(
        "/api/proxyInfo/page",
        body={"pageNo": 1, "pageSize": 100, "proxyIp": p["host"]},
        timeout=10,
    )
    if not isinstance(data, dict):
        return None
    items = data.get("list") or data.get("dataList") or data.get("data") or []
    if isinstance(data.get("data"), dict):
        items = data["data"].get("dataList") or data["data"].get("list") or items
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
    async with MoreLoginClient() as client:
        h = await client.health()
        if not h.get("ok"):
            print(f"[!] MoreLogin недоступен: {h}")
            return
        print(f"MoreLogin ok, latency={h['latency_ms']}ms\n")

        results: List[Dict[str, Any]] = []
        for p in PROFILES:
            name = make_name(p)
            print(f"-> {p['envId']}  {name}")

            # 1) ensure proxy is in pool
            pid = await find_proxy_in_pool(client, p)
            if pid is None:
                try:
                    pid = await add_proxy(client, p)
                except Exception as e:
                    print(f"   [ERR proxyInfo/add] {e}")
                    results.append({"envId": p["envId"], "ok": False, "stage": "proxy_add", "error": str(e)})
                    continue
                if pid is None:
                    print("   [ERR] add вернул None")
                    results.append({"envId": p["envId"], "ok": False, "stage": "proxy_add", "error": "no proxyId"})
                    continue
                print(f"   [OK] прокси добавлен, proxyId={pid}")
            else:
                print(f"   [OK] прокси уже в пуле, proxyId={pid}")

            # 2) attach to profile + rename + remark
            try:
                upd = await attach_proxy(client, p["envId"], pid, name, make_remark(p))
                print(f"   [OK] env/update → {json.dumps(upd, ensure_ascii=False)[:200]}")
            except Exception as e:
                print(f"   [ERR env/update] {e}")
                results.append({"envId": p["envId"], "ok": False, "stage": "env_update", "error": str(e), "proxyId": pid})
                continue

            results.append({
                "envId": p["envId"],
                "ok": True,
                "name": name,
                "remark": make_remark(p),
                "proxyId": pid,
                "host": p["host"],
                "port": p["port"],
                "username": p["username"],
            })

        # ── Верификация ────────────────────────────────────────────────
        print("\n\nВерификация через /api/env/detail:")
        for p in PROFILES:
            detail = await client.get_profile(p["envId"])
            if not detail:
                print(f"  {p['envId']}: <не найден>")
                continue
            raw = detail.get("_raw", {})
            print(
                f"  {detail.get('name'):<32} "
                f"proxyId={raw.get('proxyId')}  "
                f"proxy={json.dumps(detail.get('proxy') or {}, ensure_ascii=False)}  "
                f"remark={detail.get('remark')}"
            )

        ok = sum(1 for r in results if r.get("ok"))
        print(f"\nГотово. Успешно: {ok}/{len(PROFILES)}")
        print("\nСводка:")
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
