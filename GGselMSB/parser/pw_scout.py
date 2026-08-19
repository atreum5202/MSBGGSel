"""
parser/pw_scout.py
==================
Deep Scout на Playwright — ловит все API эндпоинты ggsel.net.

Подключается к уже запущенному браузеру MSB через connect_over_cdp.
Вешает перехват request/response ДО навигации — ловит всё.
Проходит сценарии: главная, поиск, каталог, карточка товара, продавец.
Сохраняет результат в data/deep_scout_results.json.

Запуск:
    python -m parser.pw_scout
    python -m parser.pw_scout --scenarios search product seller
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright, Browser, Page, Request, Response

# ── Константы ────────────────────────────────────────────────────────────────

PROFILE_ID   = "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB_API      = "http://127.0.0.1:17248"
OUT_FILE     = Path("data/deep_scout_results.json")
SCREENS_DIR  = Path("screenshots")
TARGET_HOSTS = {"api.ggsel.com", "ggsel.net"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pw_scout")

# ── Получить WS endpoint профиля ─────────────────────────────────────────────

async def get_ws_endpoint() -> str:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{MSB_API}/profiles/{PROFILE_ID}/status")
        data = r.json()["data"]
        ws = data.get("cdpEndpoint") or data.get("wsEndpoint")
        if not ws:
            raise RuntimeError(f"cdpEndpoint не найден: {data}")
        return ws

# ── Перехват трафика ─────────────────────────────────────────────────────────

class TrafficCapture:
    def __init__(self):
        self.requests: list[dict] = []

    def attach(self, page: Page):
        page.on("request",  self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, req: Request):
        parsed = urlparse(req.url)
        if parsed.hostname not in TARGET_HOSTS:
            return
        entry = {
            "method": req.method,
            "url":    req.url,
            "host":   parsed.hostname,
            "path":   parsed.path,
            "params": parsed.query or None,
        }
        try:
            entry["body"] = req.post_data
        except Exception:
            pass
        self.requests.append(entry)
        log.info("→ %s %s%s", req.method, parsed.hostname, parsed.path)

    def _on_response(self, resp: Response):
        parsed = urlparse(resp.url)
        if parsed.hostname not in TARGET_HOSTS:
            return
        # дописываем статус к последнему совпадающему request
        for r in reversed(self.requests):
            if r["url"] == resp.url:
                r["status"] = resp.status
                break

    def endpoints(self) -> list[dict]:
        """Сгруппированные уникальные эндпоинты."""
        seen: dict[str, dict] = {}
        for r in self.requests:
            key = f"{r['method']} {r['host']}{r['path']}"
            if key not in seen:
                seen[key] = {
                    "method":  r["method"],
                    "host":    r["host"],
                    "path":    r["path"],
                    "status":  r.get("status"),
                    "count":   0,
                    "sample_body": r.get("body"),
                }
            seen[key]["count"] += 1
        return list(seen.values())

# ── Сценарии ─────────────────────────────────────────────────────────────────

async def scenario_home(page: Page, tc: TrafficCapture):
    log.info("=== Сценарий: главная ===")
    await page.goto("https://ggsel.net/en", wait_until="networkidle", timeout=20000)
    await page.screenshot(path=str(SCREENS_DIR / "pw_home.png"))
    await page.wait_for_timeout(2000)

async def scenario_search(page: Page, tc: TrafficCapture):
    log.info("=== Сценарий: поиск ===")
    await page.goto("https://ggsel.net/en", wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(1000)

    # Клик на поиск
    search = page.locator("input[type=search], input[placeholder*='product'], input[placeholder*='search']").first
    await search.click()
    await search.fill("steam")
    await page.wait_for_timeout(1500)  # ждём XHR автодополнения
    await page.screenshot(path=str(SCREENS_DIR / "pw_search_suggest.png"))

    await search.press("Enter")
    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(2000)
    await page.screenshot(path=str(SCREENS_DIR / "pw_search_results.png"))
    log.info("URL после поиска: %s", page.url)

async def scenario_catalog(page: Page, tc: TrafficCapture):
    log.info("=== Сценарий: каталог steam ===")
    await page.goto("https://ggsel.net/en/catalog/steam", wait_until="networkidle", timeout=20000)
    await page.wait_for_timeout(2000)

    # Скроллим — подгружаем ещё товары
    await page.evaluate("window.scrollBy(0, 1500)")
    await page.wait_for_timeout(2000)
    await page.screenshot(path=str(SCREENS_DIR / "pw_catalog.png"))

async def scenario_product(page: Page, tc: TrafficCapture):
    log.info("=== Сценарий: карточка товара ===")
    await page.goto("https://ggsel.net/en/catalog/steam", wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(1000)

    # Кликаем на первый товар
    card = page.locator("a[href*='/product/'], a[href*='/goods/'], .product-card a, .goods-card a").first
    href = await card.get_attribute("href")
    if href:
        url = f"https://ggsel.net{href}" if href.startswith("/") else href
        log.info("Карточка товара: %s", url)
        await page.goto(url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SCREENS_DIR / "pw_product.png"))

        # window.__NUXT__
        nuxt = await page.evaluate("""
            (() => {
                const d = window.__NUXT__ || window.__nuxt || window.__NEXT_DATA__;
                return d ? JSON.stringify(d).slice(0, 30000) : null;
            })()
        """)
        if nuxt:
            Path("data/nuxt_sample.json").write_text(nuxt, encoding="utf-8")
            log.info("__NUXT__ сохранён → data/nuxt_sample.json")
    else:
        log.warning("Карточка товара не найдена")

async def scenario_seller(page: Page, tc: TrafficCapture):
    log.info("=== Сценарий: страница продавца ===")
    await page.goto("https://ggsel.net/en/sellers", wait_until="networkidle", timeout=20000)
    await page.wait_for_timeout(1000)

    seller = page.locator("a[href*='/seller/'], a[href*='/sellers/']").first
    href = await seller.get_attribute("href")
    if href:
        url = f"https://ggsel.net{href}" if href.startswith("/") else href
        log.info("Продавец: %s", url)
        await page.goto(url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SCREENS_DIR / "pw_seller.png"))
    else:
        log.warning("Продавец не найден, пробую /en/sellers/1")
        await page.goto("https://ggsel.net/en/sellers/1", wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SCREENS_DIR / "pw_seller.png"))

# ── Главная функция ───────────────────────────────────────────────────────────

SCENARIO_MAP = {
    "home":    scenario_home,
    "search":  scenario_search,
    "catalog": scenario_catalog,
    "product": scenario_product,
    "seller":  scenario_seller,
}

async def run(scenarios: list[str]):
    SCREENS_DIR.mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    ws = await get_ws_endpoint()
    log.info("CDP endpoint: %s", ws)

    tc = TrafficCapture()

    async with async_playwright() as p:
        browser: Browser = await p.chromium.connect_over_cdp(ws)
        log.info("Playwright подключён. Браузер: %s", browser.version)

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Вешаем перехват ПЕРЕД сценариями
        tc.attach(page)
        log.info("Перехват трафика активен")

        for name in scenarios:
            fn = SCENARIO_MAP.get(name)
            if not fn:
                log.warning("Неизвестный сценарий: %s", name)
                continue
            try:
                await fn(page, tc)
            except Exception as e:
                log.error("Сценарий %s упал: %s", name, e)

        # Итого
        endpoints = tc.endpoints()
        log.info("Перехвачено уникальных эндпоинтов: %d", len(endpoints))

        # Печатаем таблицу
        print("\n" + "="*70)
        print(f"{'METHOD':<8} {'HOST':<20} {'PATH':<35} {'ST':<5} {'N'}")
        print("="*70)
        for ep in sorted(endpoints, key=lambda e: (e["host"], e["path"])):
            print(f"{ep['method']:<8} {ep['host']:<20} {ep['path']:<35} {str(ep.get('status','?')):<5} {ep['count']}")

        # Сохраняем
        result = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "profile_id":   PROFILE_ID,
            "scenarios":    scenarios,
            "endpoints":    endpoints,
            "requests_raw": tc.requests,
        }
        OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Сохранено → %s", OUT_FILE)

        # Не закрываем браузер — он MSB-шный
        await browser.close()

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Playwright Scout — ловит API эндпоинты ggsel.net")
    parser.add_argument(
        "--scenarios", nargs="*",
        default=["home", "search", "catalog", "product", "seller"],
        help="Сценарии: home search catalog product seller",
    )
    args = parser.parse_args()
    asyncio.run(run(args.scenarios))

if __name__ == "__main__":
    main()
