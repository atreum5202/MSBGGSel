"""
parser/deep_scout_v2.py
=======================
Разведчик пропущенных эндпоинтов ggsel.net — Deep Scout V2.

Использует только нативный стек проекта:
  - parser.cdp_cookies  (CDP через websockets)
  - parser.msb_network_capture (MSB NetworkCapture API)
  - parser.msb_agent_panel (панель агента в браузере)
  - httpx (HTTP к MSB API)

НЕ использует Playwright.

Исследуемые сценарии:
  search           — поиск "steam" через input, XHR
  category_filters — фильтры в каталоге /en/catalog/steam
  product_full     — window.__NEXT_DATA__ на карточке товара
  seller_page      — страница продавца
  perf_entries     — performance.getEntries() на каждой странице
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from parser.cdp_cookies import (
    _CDPSession,
    eval_via_cdp,
    find_page_ws_url,
    navigate,
)
from parser.msb_agent_panel import AgentPanel
from parser.msb_network_capture import MsbNetworkCapture

# ── Константы ────────────────────────────────────────────────────────────────

PROFILE_ID    = "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB_API       = "http://127.0.0.1:17248"
OUT_FILE      = Path("data/deep_scout_results.json")
SCREENSHOTS_DIR = Path("data/screenshots")
OLD_ENDPOINTS = Path("data/ggsel_endpoints.json")

SCENARIOS = [
    "search",
    "category_filters",
    "product_full",
    "seller_page",
    "perf_entries",
]

# ── Логирование ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("deep_scout_v2")

# ── Вспомогательные функции ──────────────────────────────────────────────────


async def start_profile(profile_id: str) -> Optional[int]:
    """
    POST /profiles/:id/start → вернуть debugPort.
    Возвращает None при ошибке.
    """
    url = f"{MSB_API}/profiles/{profile_id}/start"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json={})
            data = r.json()
            logger.info("start_profile → %s", data)
            # MSB обычно: { data: { debugPort: N } } или { debugPort: N }
            port = (
                data.get("data", {}).get("cdpPort")
                or data.get("data", {}).get("debugPort")
                or data.get("cdpPort")
                or data.get("debugPort")
                or data.get("data", {}).get("debug_port")
                or data.get("debug_port")
            )
            if port:
                logger.info("Профиль запущен, debugPort=%s", port)
                return int(port)
            logger.warning("debugPort не найден в ответе: %s", data)
            return None
    except Exception as e:
        logger.error("start_profile: %s", e)
        return None


async def take_screenshot(debug_port: int, name: str) -> Optional[Path]:
    """
    CDP Page.captureScreenshot → сохранить PNG в SCREENSHOTS_DIR.
    Возвращает путь или None при ошибке.
    """
    try:
        ws_url = find_page_ws_url(debug_port)
        if not ws_url:
            logger.warning("take_screenshot: нет ws_url для порта %s", debug_port)
            return None
        async with _CDPSession(ws_url, timeout=15.0) as s:
            result = await s.send(
                "Page.captureScreenshot",
                {"format": "png", "quality": 80},
            )
        raw = result.get("data", "")
        if not raw:
            logger.warning("take_screenshot: пустые данные")
            return None
        data = base64.b64decode(raw)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / f"{name}_{int(time.time())}.png"
        path.write_bytes(data)
        logger.info("Скриншот сохранён: %s", path)
        return path
    except Exception as e:
        logger.error("take_screenshot(%s, %r): %s", debug_port, name, e)
        return None


async def type_text(debug_port: int, selector: str, text: str) -> None:
    """
    Ввести текст в элемент через CDP Input.insertText.
    Сначала фокусирует элемент через JS.
    """
    try:
        # фокус на элемент
        await eval_via_cdp(
            debug_port,
            f"document.querySelector({json.dumps(selector)})?.focus()",
        )
        await asyncio.sleep(0.3)
        # ввод текста через CDP
        ws_url = find_page_ws_url(debug_port)
        if not ws_url:
            logger.warning("type_text: нет ws_url")
            return
        async with _CDPSession(ws_url, timeout=10.0) as s:
            await s.send("Input.insertText", {"text": text})
        logger.info("type_text: введено %r в %r", text, selector)
    except Exception as e:
        logger.error("type_text(%r, %r): %s", selector, text, e)


# ── Сценарии разведки ────────────────────────────────────────────────────────


async def scenario_search(debug_port: int, panel: AgentPanel) -> dict:
    """Поиск 'steam' — перехватываем XHR запросы к search API."""
    result: dict = {"scenario": "search", "urls": [], "title": None, "error": None}
    try:
        panel.navigate("https://ggsel.net/en")
        await navigate(debug_port, "https://ggsel.net/en", timeout=20.0)
        await asyncio.sleep(2)

        # найти поисковый input
        selector = (
            "input[type='search'], "
            "input[placeholder*='search' i], "
            "input[placeholder*='поиск' i], "
            "input[name='search'], "
            "input[name='q']"
        )
        panel.search("steam")
        panel.type("search input", "steam")

        # кликнуть и ввести текст
        await eval_via_cdp(
            debug_port,
            f"""
            (() => {{
                const inputs = document.querySelectorAll({json.dumps(selector)});
                if (inputs.length) inputs[0].click();
            }})()
            """,
        )
        await asyncio.sleep(0.5)

        # попробуем найти более конкретно
        input_found = await eval_via_cdp(
            debug_port,
            f"""
            (() => {{
                const sel = {json.dumps(selector)};
                const el = document.querySelector(sel);
                return el ? el.tagName + '#' + el.id + '.' + el.className : null;
            }})()
            """,
        )
        logger.info("search input: %s", input_found)

        if input_found:
            await type_text(debug_port, selector.split(",")[0].strip(), "steam")
            await asyncio.sleep(3)  # ждём XHR

        # собираем URL и title
        title = await eval_via_cdp(debug_port, "document.title")
        cur_url = await eval_via_cdp(debug_port, "location.href")
        result["title"] = title
        result["urls"] = [cur_url] if cur_url else []

        panel.success(f"Поиск выполнен: {title}")
    except Exception as e:
        logger.error("scenario_search: %s", e)
        result["error"] = str(e)
        panel.error(f"Ошибка поиска: {e}")

    await take_screenshot(debug_port, "search")
    return result


async def scenario_category_filters(debug_port: int, panel: AgentPanel) -> dict:
    """Открыть /en/catalog/steam, кликнуть фильтры в sidebar."""
    result: dict = {"scenario": "category_filters", "filters": [], "error": None}
    try:
        panel.navigate("https://ggsel.net/en/catalog/steam")
        await navigate(debug_port, "https://ggsel.net/en/catalog/steam", timeout=20.0)
        await asyncio.sleep(3)

        panel.read("Читаю фильтры в sidebar")

        # собрать все фильтры/чекбоксы в sidebar
        filters_js = """
        (() => {
            const sidebar = document.querySelector(
                'aside, [class*="sidebar"], [class*="filter"], [class*="Sidebar"], [class*="Filter"]'
            );
            if (!sidebar) return JSON.stringify([]);
            const labels = [...sidebar.querySelectorAll('label, [class*="filter-item"], [class*="FilterItem"]')];
            return JSON.stringify(labels.slice(0, 30).map(l => l.innerText?.trim()).filter(Boolean));
        })()
        """
        filters_raw = await eval_via_cdp(debug_port, filters_js)
        try:
            filters = json.loads(filters_raw) if filters_raw else []
        except Exception:
            filters = []
        logger.info("Найдено фильтров: %d", len(filters))
        result["filters"] = filters

        # кликнуть первый доступный фильтр-чекбокс
        if filters:
            panel.click("первый фильтр")
            await eval_via_cdp(
                debug_port,
                """
                (() => {
                    const sidebar = document.querySelector(
                        'aside, [class*="sidebar"], [class*="filter"]'
                    );
                    if (!sidebar) return;
                    const cb = sidebar.querySelector('input[type="checkbox"]');
                    if (cb) { cb.click(); }
                })()
                """,
            )
            await asyncio.sleep(2)

        panel.success(f"Фильтры: {len(filters)} найдено")
    except Exception as e:
        logger.error("scenario_category_filters: %s", e)
        result["error"] = str(e)
        panel.error(f"Ошибка фильтров: {e}")

    await take_screenshot(debug_port, "category_filters")
    return result


async def scenario_product_full(debug_port: int, panel: AgentPanel) -> dict:
    """Открыть карточку товара, извлечь window.__NEXT_DATA__."""
    result: dict = {"scenario": "product_full", "next_data_keys": [], "product_url": None, "error": None}
    try:
        # сначала открыть каталог и найти ссылку на товар
        panel.navigate("https://ggsel.net/en/catalog/steam")
        await navigate(debug_port, "https://ggsel.net/en/catalog/steam", timeout=20.0)
        await asyncio.sleep(2)

        panel.read("Ищу ссылку на карточку товара")
        product_url = await eval_via_cdp(
            debug_port,
            """
            (() => {
                const links = [...document.querySelectorAll('a[href]')];
                const productLink = links.find(a =>
                    /\\/products?\\/|\/item\/|\/p\//.test(a.href) ||
                    (a.href.includes('ggsel') && /\\/[a-z]{2}\/[\\w-]+-\\d+/.test(a.href))
                );
                return productLink ? productLink.href : null;
            })()
            """,
        )

        if not product_url:
            # fallback: поискать карточки по классам
            product_url = await eval_via_cdp(
                debug_port,
                """
                (() => {
                    const card = document.querySelector('[class*="product"] a, [class*="card"] a, [class*="item"] a');
                    return card ? card.href : null;
                })()
                """,
            )

        if product_url:
            result["product_url"] = product_url
            panel.navigate(product_url)
            await navigate(debug_port, product_url, timeout=20.0)
            await asyncio.sleep(3)

        panel.extract("Извлекаю SSR данные страницы")
        next_data_raw = await eval_via_cdp(
            debug_port,
            """
            (() => {
                // Nuxt.js (ggsel использует Nuxt) — window.__NUXT__ или window.__nuxt
                const nd =
                    window.__NUXT__
                    || window.__nuxt
                    || window.__NEXT_DATA__      // Next.js fallback
                    || window.__INITIAL_STATE__  // generic SSR fallback
                    || null;
                if (!nd) return null;
                try { return JSON.stringify(nd).slice(0, 50000); } catch { return null; }
            })()
            """,
        )

        if next_data_raw:
            try:
                next_data = json.loads(next_data_raw)
                result["next_data_keys"] = list(next_data.keys()) if isinstance(next_data, dict) else []
                logger.info("__NEXT_DATA__ ключи: %s", result["next_data_keys"])
                # сохранить рядом для анализа
                nd_path = Path("data") / "next_data_sample.json"
                nd_path.parent.mkdir(exist_ok=True)
                nd_path.write_text(next_data_raw, encoding="utf-8")
                logger.info("__NEXT_DATA__ сохранён в %s", nd_path)
            except Exception as parse_err:
                logger.warning("__NEXT_DATA__ parse error: %s", parse_err)
        else:
            logger.warning("__NEXT_DATA__ не найден")

        panel.success(f"__NEXT_DATA__: {len(result['next_data_keys'])} ключей")
    except Exception as e:
        logger.error("scenario_product_full: %s", e)
        result["error"] = str(e)
        panel.error(f"Ошибка карточки: {e}")

    await take_screenshot(debug_port, "product_full")
    return result


async def scenario_seller_page(debug_port: int, panel: AgentPanel) -> dict:
    """Найти ссылку на продавца и открыть его страницу."""
    result: dict = {"scenario": "seller_page", "seller_urls": [], "visited": None, "error": None}
    try:
        panel.read("Ищу ссылки на продавцов")
        seller_links = await eval_via_cdp(
            debug_port,
            """
            (() => JSON.stringify(
                [...document.querySelectorAll('a[href*="/seller"]')]
                    .map(a => a.href)
                    .filter((h, i, arr) => arr.indexOf(h) === i)
                    .slice(0, 5)
            ))()
            """,
        )
        try:
            links = json.loads(seller_links) if seller_links else []
        except Exception:
            links = []

        result["seller_urls"] = links
        logger.info("Ссылки на продавцов: %s", links)

        if links:
            panel.navigate(links[0])
            await navigate(debug_port, links[0], timeout=20.0)
            await asyncio.sleep(3)
            result["visited"] = links[0]
            panel.success(f"Страница продавца открыта: {links[0]}")
        else:
            # попробовать найти через catalog
            panel.navigate("https://ggsel.net/en/catalog/steam")
            await navigate(debug_port, "https://ggsel.net/en/catalog/steam", timeout=20.0)
            await asyncio.sleep(2)
            seller_links2 = await eval_via_cdp(
                debug_port,
                """
                (() => JSON.stringify(
                    [...document.querySelectorAll('a[href*="/seller"]')]
                        .map(a => a.href)
                        .filter((h, i, arr) => arr.indexOf(h) === i)
                        .slice(0, 5)
                ))()
                """,
            )
            try:
                links2 = json.loads(seller_links2) if seller_links2 else []
            except Exception:
                links2 = []
            result["seller_urls"].extend(links2)
            if links2:
                panel.navigate(links2[0])
                await navigate(debug_port, links2[0], timeout=20.0)
                await asyncio.sleep(3)
                result["visited"] = links2[0]
                panel.success(f"Страница продавца: {links2[0]}")
            else:
                panel.info("Ссылки на продавцов не найдены")
    except Exception as e:
        logger.error("scenario_seller_page: %s", e)
        result["error"] = str(e)
        panel.error(f"Ошибка продавца: {e}")

    await take_screenshot(debug_port, "seller_page")
    return result


async def scenario_perf_entries(debug_port: int, panel: AgentPanel) -> dict:
    """Собрать performance.getEntries() — все запросы браузера."""
    result: dict = {"scenario": "perf_entries", "entries": [], "error": None}
    try:
        panel.read("Читаю performance.getEntries()")
        perf_raw = await eval_via_cdp(
            debug_port,
            """
            (() => JSON.stringify(
                performance.getEntries()
                    .filter(e =>
                        e.name.includes('ggsel') ||
                        e.name.includes('api.ggsel') ||
                        e.name.includes('ggsel.net')
                    )
                    .map(e => ({
                        name: e.name,
                        type: e.initiatorType,
                        duration: Math.round(e.duration)
                    }))
            ))()
            """,
        )
        try:
            entries = json.loads(perf_raw) if perf_raw else []
        except Exception:
            entries = []

        result["entries"] = entries
        logger.info("perf_entries: найдено %d записей ggsel", len(entries))
        panel.success(f"performance.getEntries(): {len(entries)} ggsel-запросов")
    except Exception as e:
        logger.error("scenario_perf_entries: %s", e)
        result["error"] = str(e)
        panel.error(f"Ошибка perf_entries: {e}")

    return result


# ── Объединение эндпоинтов ───────────────────────────────────────────────────


def merge_endpoints(old_file: Path, new_endpoints: list) -> list:
    """
    Загрузить старый ggsel_endpoints.json, добавить новые,
    дедуплицировать по (method, host, path_template).
    """
    old: list = []
    if old_file.exists():
        try:
            raw = old_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                old = data
            elif isinstance(data, dict):
                # возможный формат: { endpoints: [...] }
                for key in ("endpoints", "data", "items"):
                    if isinstance(data.get(key), list):
                        old = data[key]
                        break
            logger.info("Загружено %d старых эндпоинтов из %s", len(old), old_file)
        except Exception as e:
            logger.warning("merge_endpoints: не удалось загрузить %s — %s", old_file, e)

    combined = {_ep_key(ep): ep for ep in old if isinstance(ep, dict)}
    added = 0
    for ep in new_endpoints:
        if not isinstance(ep, dict):
            continue
        k = _ep_key(ep)
        if k not in combined:
            combined[k] = ep
            added += 1

    result = list(combined.values())
    logger.info(
        "merge_endpoints: было %d, добавлено %d новых, итого %d",
        len(old), added, len(result),
    )
    return result


def _ep_key(ep: dict) -> tuple:
    """Ключ дедупликации: (method, host, path_template или path или url)."""
    return (
        str(ep.get("method", "")).upper(),
        str(ep.get("host", ep.get("domain", ""))),
        str(ep.get("path_template", ep.get("path", ep.get("pattern", ep.get("url", ""))))),
    )


# ── Итоговая таблица ─────────────────────────────────────────────────────────


def print_endpoints_table(endpoints: list) -> None:
    """Вывести итоговую таблицу эндпоинтов в консоль."""
    print("\n" + "=" * 80)
    print(f"  ИТОГО ЭНДПОИНТОВ: {len(endpoints)}")
    print("=" * 80)
    print(f"  {'МЕТОД':<8} {'ХОСТ':<30} {'ПУТЬ'}")
    print("-" * 80)
    for ep in sorted(endpoints, key=lambda e: (_ep_key(e)[1], _ep_key(e)[2])):
        method = str(ep.get("method", "?")).upper()[:7]
        host   = str(ep.get("host", ep.get("domain", "?")))[:29]
        path   = str(ep.get("path_template", ep.get("path", ep.get("pattern", ep.get("url", "?")))))[:80]
        print(f"  {method:<8} {host:<30} {path}")
    print("=" * 80 + "\n")


# ── Основная функция ─────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    profile_id = args.profile_id
    scenarios  = args.scenarios

    logger.info("=== Deep Scout V2 запущен ===")
    logger.info("Профиль: %s", profile_id)
    logger.info("Сценарии: %s", scenarios)

    nc = MsbNetworkCapture(MSB_API)
    panel = AgentPanel(
        profile_id=profile_id,
        msb_url=MSB_API,
        agent_name="Deep Scout V2",
        model="claude-sonnet-4-6",
        task="Разведка эндпоинтов ggsel.net",
    )

    # 1. Запустить профиль (если нужно)
    debug_port: Optional[int] = None
    if not args.no_start:
        panel.info("Запускаю профиль...")
        debug_port = await start_profile(profile_id)
        if debug_port is None:
            logger.error("Не удалось получить debugPort. Выход.")
            return
        await asyncio.sleep(3)  # дать браузеру подняться
    else:
        logger.info("--no-start: используем уже запущенный профиль")
        # попробуем получить порт из статуса MSB
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{MSB_API}/profiles/{profile_id}/status")
                data = r.json()
                debug_port = (
                    data.get("data", {}).get("cdpPort")
                    or data.get("data", {}).get("debugPort")
                    or data.get("cdpPort")
                    or data.get("debugPort")
                    or data.get("data", {}).get("debug_port")
                )
                if debug_port:
                    debug_port = int(debug_port)
                    logger.info("debugPort из статуса: %s", debug_port)
        except Exception as e:
            logger.warning("Не удалось получить debugPort из статуса: %s", e)

    if debug_port is None:
        logger.error("debugPort неизвестен. Добавьте --profile-id и убедитесь, что профиль запущен.")
        return

    # 2. Сбросить буфер захвата
    panel.info("Сбрасываю буфер NetworkCapture")
    nc.clear(profile_id)
    await asyncio.sleep(0.5)

    # 3. Запустить сессию панели
    panel.start()

    # 4. Выполнить сценарии
    scenario_results: list[dict] = []
    SCENARIO_MAP = {
        "search":           scenario_search,
        "category_filters": scenario_category_filters,
        "product_full":     scenario_product_full,
        "seller_page":      scenario_seller_page,
        "perf_entries":     scenario_perf_entries,
    }

    for sc_name in scenarios:
        if sc_name not in SCENARIO_MAP:
            logger.warning("Неизвестный сценарий: %r", sc_name)
            continue

        logger.info("--- Сценарий: %s ---", sc_name)
        panel.info(f"Сценарий: {sc_name}")
        try:
            sc_func = SCENARIO_MAP[sc_name]
            sc_result = await sc_func(debug_port, panel)
            scenario_results.append(sc_result)
        except Exception as e:
            logger.error("Сценарий %s упал: %s", sc_name, e)
            scenario_results.append({"scenario": sc_name, "error": str(e)})

        await asyncio.sleep(1)

    # 5. Прочитать захваченный трафик
    panel.read("Читаю захваченные эндпоинты из NetworkCapture")
    logger.info("Читаю NetworkCapture...")
    await asyncio.sleep(2)

    new_endpoints = nc.endpoints(profile_id, limit=500)
    logger.info("NetworkCapture вернул %d эндпоинтов", len(new_endpoints))

    # дополнительно — requests для полноты картины
    all_requests = nc.requests(profile_id, host="ggsel.net", limit=500)
    logger.info("NetworkCapture requests (ggsel.net): %d", len(all_requests))

    # извлечь эндпоинты из requests если они богаче
    if all_requests and not new_endpoints:
        new_endpoints = _extract_endpoints_from_requests(all_requests)

    # 6. Объединить с прошлыми данными
    panel.think("Объединяю с прошлыми данными...")
    merged = merge_endpoints(OLD_ENDPOINTS, new_endpoints)

    # 7. Сохранить результат
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "profile_id": profile_id,
        "scenarios_run": scenarios,
        "scenario_results": scenario_results,
        "endpoints_count": len(merged),
        "endpoints": merged,
    }
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Результат сохранён: %s", OUT_FILE)

    # 8. Вывести таблицу
    print_endpoints_table(merged)
    panel.success(f"Разведка завершена. Эндпоинтов: {len(merged)}")
    panel.stop()

    nc.close()
    logger.info("=== Deep Scout V2 завершён ===")


def _extract_endpoints_from_requests(requests: list) -> list:
    """
    Из raw-запросов (NetworkCapture /requests) извлечь уникальные эндпоинты.
    """
    from urllib.parse import urlparse
    seen: set = set()
    result = []
    for req in requests:
        if not isinstance(req, dict):
            continue
        url = req.get("url", "")
        method = str(req.get("method", "GET")).upper()
        if not url:
            continue
        try:
            parsed = urlparse(url)
            key = (method, parsed.netloc, parsed.path)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "method": method,
                "host": parsed.netloc,
                "path": parsed.path,
                "path_template": parsed.path,
                "url": url,
                "status": req.get("status"),
            })
        except Exception:
            continue
    return result


# ── Точка входа ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Deep Scout V2 — разведка эндпоинтов ggsel.net"
    )
    ap.add_argument(
        "--profile-id",
        default=PROFILE_ID,
        help="ID профиля MSB (default: %(default)s)",
    )
    ap.add_argument(
        "--scenarios",
        nargs="+",
        default=SCENARIOS,
        choices=list({*SCENARIOS}),
        metavar="SCENARIO",
        help=f"Сценарии для запуска. Доступно: {', '.join(SCENARIOS)}",
    )
    ap.add_argument(
        "--no-start",
        action="store_true",
        help="Не запускать профиль — он уже запущен",
    )
    args = ap.parse_args()
    asyncio.run(run(args))
