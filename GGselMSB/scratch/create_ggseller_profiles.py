"""
create_ggseller_profiles.py — создание 5 профилей в MoreLogin с прокси из MSB.

Источник прокси: %APPDATA%/MSB/profiles/<uuid>/meta.json
  - Берём 5 профилей, у которых есть реальный proxy (не null)
  - Каждый -> новый профиль в MoreLogin, имя = account name, прокси = из meta

Использование:
  cd C:\\Users\\Atreum\\Desktop\\GgsellerMoreLogin
  python scratch\\create_ggseller_profiles.py
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

MSB_PROFILES_DIR = Path(os.environ["APPDATA"]) / "MSB" / "profiles"
GROUP_NAME = "GGSeller"
GROUP_ID_ENV: Optional[int] = None  # если уже знаешь ID — подставь вручную

# 5 профилей-источников из MSB (id → account name)
# id берём из meta.json, name = account.email, proxy = .proxy
SOURCE_PROFILE_IDS = [
    "20f77a2f-c56a-46b3-b318-ee701793f459",  # ristarel1@outlook.com  → 152.232.74.90:9080
    "0363edcc-605c-48a4-8c0c-2cab78361e05",  # vernadilo1@outlook.com → 168.196.238.232:9081
    "642c4d38-f8d8-4bcf-af0d-59e9408b2c98",  # vernadilo2@outlook.com → 168.196.239.19:9129
    "6f1459ff-a0a1-458f-a790-852186b374bc",  # biranol@outlook.com    → 185.148.26.247:8000
    "a85e80bd-60c1-4ed3-9079-ae0f7ac60ac8",  # ggparser-proxy1        → 185.183.163.219:8000
]


def load_source_proxies() -> List[Dict[str, Any]]:
    """Читает meta.json и возвращает [{name, proxy, source_id}, ...]."""
    out: List[Dict[str, Any]] = []
    for sid in SOURCE_PROFILE_IDS:
        meta_path = MSB_PROFILES_DIR / sid / "meta.json"
        if not meta_path.exists():
            print(f"  [SKIP] meta.json не найден для {sid}")
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            print(f"  [SKIP] ошибка чтения {meta_path}: {e}")
            continue

        proxy = meta.get("proxy")
        if not proxy or not proxy.get("host"):
            print(f"  [SKIP] у {sid} ({meta.get('name')}) нет прокси")
            continue

        # Имя берём из account.email (там живые адреса), иначе из name
        account = meta.get("account") or {}
        name = account.get("email") or meta.get("name") or sid

        out.append({
            "name": name,
            "proxy": proxy,
            "source_id": sid,
        })
    return out


async def ensure_group(client: MoreLoginClient, name: str) -> int:
    """Создаёт группу если её нет, возвращает её ID."""
    groups = await client.get_groups()
    for g in groups:
        if g.get("name") == name:
            gid = g.get("id")
            print(f"  [OK] группа '{name}' уже есть (id={gid})")
            return int(gid)

    # Создаём через POST /api/envgroup/create
    http = await client._ensure_http()
    data = await client._post("/api/envgroup/create", body={"groupName": name})
    if not isinstance(data, dict):
        raise MoreLoginError(f"не удалось создать группу '{name}': {data!r}")
    gid = data.get("id") or data.get("groupId")
    if not gid:
        raise MoreLoginError(f"в ответе нет id группы: {data!r}")
    print(f"  [OK] создана группа '{name}' (id={gid})")
    return int(gid)


def proxy_to_payload(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """
    MoreLogin ожидает proxy в формате:
      {"proxyType": "socks5", "host": ..., "port": ..., "proxyUser": ..., "proxyPassword": ...}
    """
    protocol = (proxy.get("protocol") or "socks5").lower()
    return {
        "proxyType": protocol,
        "host": proxy["host"],
        "port": int(proxy["port"]),
        "proxyUser": proxy.get("username") or proxy.get("proxyUser") or "",
        "proxyPassword": proxy.get("password") or proxy.get("proxyPassword") or "",
    }


async def main():
    sources = load_source_proxies()
    print(f"Найдено {len(sources)} MSB-профилей с прокси:\n")
    for s in sources:
        p = s["proxy"]
        print(f"  - {s['name']:<35} {p['protocol']}://{p.get('username')}:***@{p['host']}:{p['port']}")

    if len(sources) < 5:
        print(f"\n[!] Нужно 5, а нашли только {len(sources)}. Дополни SOURCE_PROFILE_IDS.")

    print(f"\nСоздаю в MoreLogin...")

    async with MoreLoginClient() as client:
        h = await client.health()
        if not h.get("ok"):
            print(f"[!] MoreLogin недоступен: {h}")
            return
        print(f"  [OK] MoreLogin: latency={h['latency_ms']}ms")

        group_id = GROUP_ID_ENV or await ensure_group(client, GROUP_NAME)
        print(f"  group_id = {group_id}")

        results: List[Dict[str, Any]] = []
        for s in sources:
            proxy_payload = proxy_to_payload(s["proxy"])
            print(f"\n  -> {s['name']}  (proxy {proxy_payload['host']}:{proxy_payload['port']})")

            try:
                created = await client.create_profile(
                    name=s["name"],
                    browser_type_id=1,    # Chrome
                    os_id=1,              # Windows
                    group_id=group_id,
                    proxy=proxy_payload,
                    remark=f"from MSB {s['source_id']}",
                )
            except MoreLoginError as e:
                print(f"    [ERR] {e}")
                results.append({"name": s["name"], "ok": False, "error": str(e)})
                continue

            if not created:
                print("    [ERR] create_profile вернул None")
                results.append({"name": s["name"], "ok": False, "error": "create_profile returned None"})
                continue

            env_id = created.get("envId") or created.get("id")
            print(f"    [OK] создан envId={env_id}")
            results.append({
                "name": s["name"],
                "ok": True,
                "envId": env_id,
                "source_id": s["source_id"],
                "proxy": proxy_payload,
            })

        # Проверка: прочитаем обратно и убедимся что прокси подцепился
        print("\n\nПроверка: листаю профили в группе GGSeller...")
        all_in_group = await client.get_profiles(group_id=group_id)
        for r in results:
            if not r.get("ok"):
                continue
            match = next((p for p in all_in_group if p.get("envId") == r["envId"]), None)
            if not match:
                print(f"  [WARN] {r['name']} (envId={r['envId']}) — не найден в группе")
                continue
            proxy_info = match.get("proxy") or {}
            print(f"  - {r['name']:<35} envId={r['envId']}  proxy={proxy_info.get('host')}:{proxy_info.get('port')}  type={proxy_info.get('proxyType') or proxy_info.get('protocol')}")

        print("\nГотово. Сводка:")
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
