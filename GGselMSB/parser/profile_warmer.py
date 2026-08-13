"""
parser/profile_warmer.py
========================
Мигрировано из GGSeller/services/parser/profile_warmer.py (2026-07-27).

«Нагул» MSB-профиля перед парсингом ggsel.net через Claude Vision.

Стратегия:
  1. Запускает браузер MSB с нужным профилем.
  2. Для каждого сайта из WARM_UP_SITES:
     - переходит на страницу
     - делает скриншот
     - отправляет в Claude Vision (что кликнуть / куда скроллить)
     - выполняет команды через MSB execute API
     - случайные паузы
  3. Сохраняет флаг warmed=true в shared/warmed_profiles.json.

Дополняет V7/parser/warm_profiles.py (который использует MSB Warmer API) —
этот модуль для тех, кто использует MSB + Claude Vision для «умного» нагула.

Standalone запуск:
    python -m parser.profile_warmer --profile-id XXX [--force]

ENV (в .env в корне V7):
  MSB_API_BASE, MSB_API_TOKEN, MSB_PROFILE_ID, MSB_HEADLESS
  ANTHROPIC_API_KEY
  WARMER_REWARM_DAYS
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    raise ImportError("Установи httpx: pip install httpx")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda *a, **k: None  # noqa

# V7 структура: parser/ находится в V7/, .env — в V7/ корень (parent.parent)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

log = logging.getLogger("ggselv7.profile_warmer")

# ── Конфиг ────────────────────────────────────────────────────────
MSB_API_BASE   = os.getenv("MSB_API_BASE", "http://127.0.0.1:17248")
MSB_API_TOKEN  = os.getenv("MSB_API_TOKEN", "")
MSB_PROFILE_ID = os.getenv("MSB_PROFILE_ID", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MSB_HEADLESS = os.getenv("MSB_HEADLESS", "false").lower() == "true"

# V7: warmed_profiles.json в shared/ рядом с parser/
_ROOT = Path(__file__).parent.parent
WARMED_FILE = _ROOT / "shared" / "warmed_profiles.json"

# Статус нагула в памяти
_warm_status: dict = {"running": False, "current_site": "", "sites_done": [], "error": ""}

# ── Сайты для нагула ──────────────────────────────────────────────
WARM_UP_POOL = [
    ("https://www.google.com/search?q=steam+games+buy+cheap", 8, 15),
    ("https://www.youtube.com",                                10, 18),
    ("https://en.wikipedia.org/wiki/Video_game",               6,  12),
    ("https://www.reddit.com/r/gamedeals",                     8,  14),
    ("https://store.steampowered.com",                         10, 16),
]
WARM_UP_GGSEL = ("https://ggsel.net", 15, 25)

REWARM_DAYS = int(os.getenv("WARMER_REWARM_DAYS", "7"))


# ── Утилиты ───────────────────────────────────────────────────────

def _msb_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if MSB_API_TOKEN:
        h["Authorization"] = f"Bearer {MSB_API_TOKEN}"
    return h


def _load_warmed() -> dict:
    try:
        data = json.loads(WARMED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    now = datetime.now()
    for profile_id, info in data.items():
        if not info.get("warmed"):
            continue
        ts_str = info.get("timestamp", "")
        if not ts_str:
            continue
        try:
            warmed_at = datetime.fromisoformat(ts_str)
            age_days = (now - warmed_at).days
            if age_days >= REWARM_DAYS:
                log.info(
                    "[Warmer] Профиль %s нагрет %d дней назад (лимит %d) — требует ре-варма",
                    profile_id, age_days, REWARM_DAYS,
                )
                data[profile_id] = {}
        except Exception:
            pass
    return data


def _save_warmed(data: dict) -> None:
    WARMED_FILE.parent.mkdir(parents=True, exist_ok=True)
    WARMED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rand(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


# ── MSB helpers ───────────────────────────────────────────────────

async def _msb_get(client: httpx.AsyncClient, path: str) -> Optional[dict]:
    try:
        r = await client.get(path, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("[Warmer] MSB GET %s ошибка: %s", path, e)
        return None


async def _msb_post(client: httpx.AsyncClient, path: str, body: dict) -> Optional[dict]:
    try:
        r = await client.post(path, json=body, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("[Warmer] MSB POST %s ошибка: %s", path, e)
        return None


async def _is_msb_available(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get("/health", timeout=3)
        return r.status_code < 500
    except Exception:
        return False


async def _ensure_running(client: httpx.AsyncClient, profile_id: str) -> bool:
    status = await _msb_get(client, f"/profiles/{profile_id}/status")
    if status and status.get("id") == profile_id:
        log.info("[Warmer] Профиль %s уже запущен", profile_id)
        return True
    log.info("[Warmer] Запускаю профиль %s...", profile_id)
    result = await _msb_post(client, f"/profiles/{profile_id}/start", {"headless": MSB_HEADLESS})
    if result is None:
        return False
    await asyncio.sleep(3)
    return True


async def _goto(client: httpx.AsyncClient, profile_id: str, url: str) -> bool:
    result = await _msb_post(client, f"/profiles/{profile_id}/goto", {"url": url})
    return result is not None


async def _screenshot_b64(client: httpx.AsyncClient, profile_id: str) -> Optional[str]:
    try:
        r = await client.get(
            f"/profiles/{profile_id}/screenshot",
            params={"type": "jpeg"},
            timeout=15,
        )
        r.raise_for_status()
        return base64.b64encode(r.content).decode()
    except Exception as e:
        log.warning("[Warmer] Скриншот ошибка: %s", e)
        return None


async def _execute(client: httpx.AsyncClient, profile_id: str, commands: list) -> bool:
    result = await _msb_post(
        client,
        f"/profiles/{profile_id}/execute",
        {"commands": commands},
    )
    return result is not None


# ── Claude Vision ─────────────────────────────────────────────────

_ALLOWED_CMD_TYPES = {"scroll", "click", "wait", "goto", "fill", "waitFor"}


def _validate_commands(raw) -> list:
    if not isinstance(raw, list):
        raise ValueError(f"Ожидался list, получен {type(raw).__name__}")
    if len(raw) == 0:
        raise ValueError("Пустой список команд")

    valid = []
    for i, cmd in enumerate(raw):
        if not isinstance(cmd, dict):
            log.debug("[Warmer] Команда #%d не dict — пропускаем: %r", i, cmd)
            continue
        cmd_type = cmd.get("type", "")
        if cmd_type not in _ALLOWED_CMD_TYPES:
            log.debug("[Warmer] Неизвестный тип команды #%d %r — пропускаем", i, cmd_type)
            continue
        if cmd_type == "scroll":
            cmd = {
                "type": "scroll",
                "x":      max(0, min(int(cmd.get("x", 760)),    3840)),
                "y":      max(0, min(int(cmd.get("y", 400)),     2160)),
                "deltaX": max(-3000, min(int(cmd.get("deltaX", 0)),    3000)),
                "deltaY": max(-3000, min(int(cmd.get("deltaY", 300)),  3000)),
            }
        elif cmd_type == "wait":
            cmd = {"type": "wait", "ms": max(100, min(int(cmd.get("ms", 1000)), 15_000))}
        elif cmd_type == "click":
            if not isinstance(cmd.get("selector"), str) or not cmd["selector"].strip():
                log.debug("[Warmer] click без selector — пропускаем")
                continue
            cmd = {"type": "click", "selector": cmd["selector"].strip()[:200]}
        valid.append(cmd)

    if not valid:
        raise ValueError("Все команды отфильтрованы как невалидные")
    return valid


async def _ask_claude_what_to_do(screenshot_b64: str, url: str) -> list:
    if not ANTHROPIC_API_KEY:
        log.warning("[Warmer] ANTHROPIC_API_KEY не задан — используем дефолтное поведение")
        return _default_commands()

    system = """Ты управляешь браузером для имитации реального пользователя.
Тебе дают скриншот веб-страницы. Верни JSON массив команд для браузера.

Доступные команды:
- {"type": "scroll", "x": 760, "y": 400, "deltaY": 300} — скролл вниз
- {"type": "click", "selector": "a[href*='/games']"} — клик по CSS-селектору
- {"type": "wait", "ms": 1200} — пауза

Правила:
1. Веди себя как живой пользователь — скролл, чтение, иногда клик на интересную ссылку
2. Кликай только на ссылки того же домена (не уходи на другой сайт)
3. 2-4 команды скролла, 0-1 клик, паузы между ними
4. Отвечай ТОЛЬКО валидным JSON массивом, без пояснений и markdown

Пример ответа:
[{"type":"scroll","x":760,"y":400,"deltaY":350},{"type":"wait","ms":900},{"type":"scroll","x":760,"y":700,"deltaY":400}]"""

    prompt = f"Страница: {url}\nЧто сделать чтобы выглядеть как реальный пользователь?"

    MAX_ATTEMPTS = 3
    for attempt in range(1, MAX_ATTEMPTS + 1):
        await asyncio.sleep(2.0 + random.uniform(0, 1.0))
        try:
            async with httpx.AsyncClient(timeout=30) as ac:
                r = await ac.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 512,
                        "system": system,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/jpeg",
                                            "data": screenshot_b64,
                                        },
                                    },
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                    },
                )
                if r.status_code == 529:
                    log.warning(
                        "[Warmer] Claude API HTTP 529 (overload), попытка %d/%d — ждём 30 сек",
                        attempt, MAX_ATTEMPTS,
                    )
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(30)
                        continue
                    break
                r.raise_for_status()
                text = r.json()["content"][0]["text"].strip()
                if text.startswith("```"):
                    parts = text.split("```")
                    body = parts[1] if len(parts) > 1 else ""
                    if body.startswith("json"):
                        body = body[4:]
                    text = body.strip()
                raw = json.loads(text)
                commands = _validate_commands(raw)
                log.info("[Warmer] Claude вернул %d валидных команд для %s (попытка %d)",
                         len(commands), url, attempt)
                return commands
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("[Warmer] Невалидный ответ Claude (попытка %d/%d): %s",
                        attempt, MAX_ATTEMPTS, e)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(1.0)
                continue
        except Exception as e:
            log.warning("[Warmer] Claude Vision ошибка (попытка %d/%d): %s",
                        attempt, MAX_ATTEMPTS, e)
            break

    log.warning("[Warmer] Claude не вернул валидные команды — используем дефолтные")
    return _default_commands()


def _default_commands() -> list:
    cmds = []
    for _ in range(random.randint(2, 4)):
        cmds.append({"type": "scroll", "x": 760, "y": random.randint(300, 600),
                     "deltaY": random.randint(200, 500)})
        cmds.append({"type": "wait", "ms": int(_rand(600, 1800))})
    return cmds


# ── Прокси + Timezone ─────────────────────────────────────────────

async def _apply_proxy(client: httpx.AsyncClient, profile_id: str, proxy: Optional[str]) -> bool:
    result = await _msb_post(
        client,
        f"/profiles/{profile_id}/switchProxy",
        {"proxy": proxy},
    )
    if result is not None:
        log.info("[Warmer] Прокси %s применён к профилю %s",
                 proxy or "снят", profile_id)
        return True
    return False


async def _detect_timezone_from_proxy(proxy: str) -> str:
    default_tz = "Europe/Moscow"
    try:
        async with httpx.AsyncClient(proxies={"http://": proxy, "https://": proxy}, timeout=5) as ac:
            r = await ac.get("http://ip-api.com/json")
            if r.status_code == 200:
                data = r.json()
                tz = data.get("timezone", "").strip()
                if tz:
                    log.info("[Warmer] Timezone по IP прокси: %s", tz)
                    return tz
    except Exception as e:
        log.warning("[Warmer] _detect_timezone_from_proxy ошибка: %s — используем %s", e, default_tz)
    return default_tz


async def configure_profile_network(
    profile_id: str,
    proxy: Optional[str] = None,
    timezone: str = "Europe/Moscow",
    language: str = "ru-RU",
) -> dict:
    msb_base = MSB_API_BASE.rstrip("/")
    applied = []
    if proxy and timezone == "Europe/Moscow":
        detected_tz = await _detect_timezone_from_proxy(proxy)
        if detected_tz != timezone:
            timezone = detected_tz
            log.info("[Warmer] Timezone автоопределён по прокси: %s", timezone)

    async with httpx.AsyncClient(base_url=msb_base, headers=_msb_headers()) as client:
        if not await _is_msb_available(client):
            return {"success": False, "reason": "msb_unavailable"}
        if proxy:
            ok = await _apply_proxy(client, profile_id, proxy)
            if ok:
                applied.append(f"proxy:{proxy[:30]}...")
        patch = {}
        if timezone:
            patch["timezone"] = timezone
        if language:
            patch["language"] = language
            patch["acceptLanguage"] = f"{language},{language.split('-')[0]};q=0.9,en;q=0.8"
        if patch:
            try:
                r = await client.patch(f"/profiles/{profile_id}", json=patch, timeout=10)
                if r.status_code < 300:
                    applied.extend(patch.keys())
                    log.info("[Warmer] Профиль %s обновлён: %s", profile_id, patch)
            except Exception as e:
                log.warning("[Warmer] Patch профиля ошибка: %s", e)
    return {"success": True, "applied": applied}


# ── Главная функция нагула ───────────────────────────────────────

async def warm_profile(profile_id: str, force: bool = False, proxy: Optional[str] = None) -> dict:
    global _warm_status

    if not profile_id:
        log.warning("[Warmer] profile_id не задан")
        return {"success": False, "reason": "no_profile_id"}

    warmed_data = _load_warmed()
    profile_info = warmed_data.get(profile_id, {})
    if profile_info.get("warmed") and not force:
        log.info("[Warmer] Профиль %s уже нагрет (%s), пропускаем",
                 profile_id, profile_info.get("timestamp", "?"))
        return {
            "success": True,
            "skipped": True,
            "warmed_at": profile_info.get("timestamp"),
            "cookies_count": profile_info.get("cookies_count", 0),
        }

    _warm_status = {"running": True, "current_site": "", "sites_done": [], "error": ""}
    t_start = time.monotonic()
    sites_visited = []
    msb_base = MSB_API_BASE.rstrip("/")

    async with httpx.AsyncClient(base_url=msb_base, headers=_msb_headers()) as client:
        if not await _is_msb_available(client):
            log.warning("[Warmer] MSB недоступен")
            _warm_status["running"] = False
            _warm_status["error"] = "msb_unavailable"
            return {"success": False, "reason": "msb_unavailable"}

        if proxy:
            await _msb_post(client, f"/profiles/{profile_id}/stop", {})
            await asyncio.sleep(1.5)
            await _apply_proxy(client, profile_id, proxy)
            await asyncio.sleep(0.5)
            log.info("[Warmer] Прокси применён, перезапускаем профиль %s (GeoIP пересчёт)...", profile_id)

        if not await _ensure_running(client, profile_id):
            _warm_status["running"] = False
            _warm_status["error"] = "profile_start_failed"
            return {"success": False, "reason": "profile_start_failed"}

        n_sites = random.randint(3, 4)
        selected_sites = random.sample(WARM_UP_POOL, min(n_sites, len(WARM_UP_POOL)))
        sites_to_visit = selected_sites + [WARM_UP_GGSEL]
        log.info("[Warmer] Выбрано %d сайтов для нагула: %s",
                 len(sites_to_visit), [s[0] for s in sites_to_visit])

        for url, min_sec, max_sec in sites_to_visit:
            _warm_status["current_site"] = url
            log.info("[Warmer] → %s", url)
            ok = await _goto(client, profile_id, url)
            if not ok:
                log.warning("[Warmer] Не удалось перейти на %s", url)
                continue
            await asyncio.sleep(_rand(2.5, 4.5))
            screenshot = await _screenshot_b64(client, profile_id)
            commands = await _ask_claude_what_to_do(screenshot, url) if screenshot else _default_commands()
            for cmd in commands:
                cmd_type = cmd.get("type", "")
                if cmd_type == "wait":
                    await asyncio.sleep(cmd.get("ms", 1000) / 1000)
                else:
                    await _execute(client, profile_id, [cmd])
                    await asyncio.sleep(_rand(0.4, 1.2))
            elapsed_so_far = 2.5 + len(commands) * 0.8
            remaining = _rand(min_sec, max_sec) - elapsed_so_far
            if remaining > 0:
                await asyncio.sleep(remaining)
            sites_visited.append(url)
            _warm_status["sites_done"].append(url)
            log.info("[Warmer] ✓ %s", url)

        # bot-sannysoft check
        sannysoft_passed = sannysoft_failed = sannysoft_total = 0
        try:
            san_resp = await _msb_post(
                client, f"/profiles/{profile_id}/runScenario",
                {"scenario": "bot-sannysoft", "params": {}},
            )
            if san_resp:
                san_data = san_resp.get("data", san_resp) if isinstance(san_resp, dict) else san_resp
                sannysoft_passed = san_data.get("passed", 0)
                sannysoft_failed = san_data.get("failed", 0)
                sannysoft_total  = san_data.get("total",  0)
                if sannysoft_failed > 3:
                    log.warning("[Warmer] ⚠️  bot-sannysoft: %s провалил %d/%d — высокий риск Qrator",
                                profile_id, sannysoft_failed, sannysoft_total)
                else:
                    log.info("[Warmer] ✅ bot-sannysoft: passed=%d failed=%d", sannysoft_passed, sannysoft_failed)
        except Exception as e:
            log.warning("[Warmer] bot-sannysoft ошибка (не критично): %s", e)

        cookies_count = 0
        try:
            r = await client.get(f"/profiles/{profile_id}/cookies", params={"format": "json"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                inner = data.get("data", data) if isinstance(data, dict) else data
                all_c = (inner.get("data", []) if isinstance(inner, dict)
                         else (inner if isinstance(inner, list) else []))
                cookies_count = len(all_c)
        except Exception:
            pass

    duration = round(time.monotonic() - t_start, 1)
    warmed_data[profile_id] = {
        "warmed": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_sec": duration,
        "sites_visited": len(sites_visited),
        "cookies_count": cookies_count,
        "proxy_used": bool(proxy),
    }
    _save_warmed(warmed_data)
    _warm_status["running"] = False
    _warm_status["current_site"] = ""

    result = {
        "success": True,
        "cookies_count": cookies_count,
        "duration_sec": duration,
        "sites_visited": len(sites_visited),
        "sites": sites_visited,
        "sannysoft": {
            "passed": sannysoft_passed,
            "failed": sannysoft_failed,
            "total":  sannysoft_total,
            "clean":  sannysoft_failed <= 3,
        },
    }
    log.info("[Warmer] Готово: %s", result)
    return result


def get_warm_status() -> dict:
    warmed_data = _load_warmed()
    profile_id = MSB_PROFILE_ID
    info = warmed_data.get(profile_id, {})
    return {
        **_warm_status,
        "warmed": info.get("warmed", False),
        "warmed_at": info.get("timestamp"),
        "cookies_count": info.get("cookies_count", 0),
        "proxy_used": info.get("proxy_used", False),
    }


# ── Standalone ────────────────────────────────────────────────────

async def _main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Нагул MSB профиля")
    parser.add_argument("--profile-id", default=MSB_PROFILE_ID)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    args = parser.parse_args()

    if not args.profile_id:
        print("Укажи --profile-id или задай MSB_PROFILE_ID в .env")
        return
    result = await warm_profile(args.profile_id, force=args.force, proxy=args.proxy)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
