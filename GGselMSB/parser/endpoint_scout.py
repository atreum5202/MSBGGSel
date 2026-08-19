"""
parser/endpoint_scout.py — Разведка API ggsel.net
Навигирует по каталогу, кликает в карточки товаров,
взаимодействует с элементами страницы и перехватывает все API вызовы.
"""
from __future__ import annotations
import asyncio, json, logging, sys, time, re, random
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
import httpx

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    _PW_OK = True
except ImportError:
    _PW_OK = False

from .msb_agent_panel import AgentPanel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("endpoint_scout")

PROFILE_ID = "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB_API    = "http://127.0.0.1:17248"
BASE_URL   = "https://ggsel.net"
OUT_FILE   = Path(__file__).parent.parent / "data" / "ggsel_endpoints.json"

# Страницы каталога для старта
CATALOG_PAGES = [
    "/en/catalog",
    "/en/catalog?sort=popular",
    "/en/catalog/steam",
    "/en/catalog/gift-cards",
    "/en/catalog?page=2",
]

JSON_CTS   = ("application/json", "text/json")
IGNORE     = {"fonts.googleapis.com","fonts.gstatic.com","mc.yandex.ru",
              "yastatic.net","static.yandex.net","pixel.facebook.com","cdn.jsdelivr.net"}

# ── Хранилище ────────────────────────────────────────────────────────────────

class EndpointStore:
    def __init__(self):
        self._d: Dict[str, dict] = {}

    def _tpl(self, path):
        path = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{uuid}', path)
        path = re.sub(r'/\d+', '/{id}', path)
        return re.sub(r'[?&][^?&]*=[^?&]*', '', path)

    def add(self, method, url, status, req_body=None, res_body=None, ct="", src=""):
        p = urlparse(url)
        if not p.hostname or p.hostname in IGNORE: return
        if "ggsel" not in p.hostname: return
        key = f"{method}:{p.hostname}:{self._tpl(p.path)}"
        if key not in self._d:
            self._d[key] = {"method":method,"host":p.hostname,
                            "path_template":self._tpl(p.path),
                            "count":0,"statuses":set(),"ct":ct,"src":src,"examples":[]}
        e = self._d[key]
        e["count"] += 1
        if status: e["statuses"].add(status)
        if len(e["examples"]) < 3:
            ex = {"url":url,"status":status}
            if req_body: ex["req"] = str(req_body)[:200]
            if res_body: ex["res"] = str(res_body)[:400]
            e["examples"].append(ex)

    def summary(self):
        return sorted([{**v,"statuses":list(v["statuses"])} for v in self._d.values()],
                      key=lambda x: -x["count"])

    def save(self, path=OUT_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self.summary(), open(path,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
        logger.info("Сохранено: %d эндпоинтов → %s", len(self._d), path)


# ── CDP helpers ───────────────────────────────────────────────────────────────

async def get_cdp_ws(profile_id: str = PROFILE_ID):
    r = httpx.get(f"{MSB_API}/profiles/{profile_id}/status", timeout=5)
    ws = r.json()["data"].get("cdpEndpoint") or r.json()["data"].get("wsEndpoint")
    if not ws: raise RuntimeError("Профиль не запущен — запусти через MSB UI")
    return ws

async def safe_eval(page, js):
    try: return await page.evaluate(js)
    except: pass

async def inject_cursor(page):
    await safe_eval(page, """() => {
        if (window.__msbCursorInjected) return;
        window.__msbCursorInjected = true;
        const d = document.createElement('div');
        d.id = 'msb-cursor-dot';
        d.style.cssText = 'position:fixed;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(47,111,237,0.85);box-shadow:0 0 0 3px rgba(47,111,237,0.25);pointer-events:none;z-index:2147483644;opacity:0;transition:left .07s,top .07s,opacity .2s';
        document.documentElement.appendChild(d);
        window.msbMoveCursor = (x,y) => { d.style.left=x+'px'; d.style.top=y+'px'; d.style.opacity='1'; };
        window.msbClick = (x,y) => {
            window.msbMoveCursor(x,y);
            const r = document.createElement('div');
            r.style.cssText = `position:fixed;left:${x}px;top:${y}px;width:0;height:0;border-radius:50%;background:rgba(47,111,237,0.35);border:2px solid rgba(47,111,237,0.8);pointer-events:none;z-index:2147483643;transform:translate(-50%,-50%);animation:msbR .55s ease-out forwards`;
            if (!document.getElementById('msb-r-s')) { const s=document.createElement('style'); s.id='msb-r-s'; s.textContent='@keyframes msbR{0%{width:0;height:0;opacity:.9}100%{width:54px;height:54px;opacity:0}}'; document.head.appendChild(s); }
            document.documentElement.appendChild(r);
            r.addEventListener('animationend',()=>r.remove());
        };
    }""")

async def move(page, x, y):
    await page.mouse.move(x, y)
    await safe_eval(page, f"window.msbMoveCursor&&window.msbMoveCursor({x},{y})")

async def click(page, x, y):
    await page.mouse.click(x, y)
    await safe_eval(page, f"window.msbClick&&window.msbClick({x},{y})")

async def setup_intercept(page, store: EndpointStore):
    """Подключаем перехват всех JSON ответов."""
    async def on_response(response):
        try:
            url = response.url
            p   = urlparse(url)
            if not p.hostname or p.hostname in IGNORE: return
            if "ggsel" not in p.hostname: return
            ct   = response.headers.get("content-type","")
            is_j = any(x in ct for x in JSON_CTS)
            is_x = response.request.resource_type in ("xhr","fetch","document")
            if not (is_j or is_x): return
            body = None
            if is_j:
                try: body = await response.text()
                except: pass
            rb = None
            try:
                if response.request.method in ("POST","PUT","PATCH"):
                    rb = response.request.post_data
            except: pass
            store.add(response.request.method, url, response.status, rb, body, ct, "playwright")
            icon = "📦" if is_j else "📄"
            logger.info("  %s %-5s %s → %d", icon, response.request.method, url[:80], response.status)
        except: pass
    page.on("response", on_response)


# ── Навигация по каталогу ─────────────────────────────────────────────────────

async def scroll_page(page: Page, steps=4):
    """Медленный скролл с движением мыши."""
    vp = page.viewport_size or {"width":1280,"height":800}
    w, h = vp["width"], vp["height"]
    for i in range(steps):
        await move(page, random.randint(w//4, 3*w//4), random.randint(h//4, 3*h//4))
        await safe_eval(page, f"window.scrollBy({{top:{h*0.6},behavior:'smooth'}})")
        await asyncio.sleep(1.5)
    await safe_eval(page, "window.scrollTo({top:0})")
    await asyncio.sleep(0.8)


async def get_product_links(page: Page) -> List[str]:
    """Собираем ссылки на карточки товаров с текущей страницы."""
    links = await safe_eval(page, """() => {
        return [...new Set(
            [...document.querySelectorAll('a[href*="/catalog/product/"]')]
                .map(a => a.href)
        )].slice(0, 8);
    }""") or []
    return links


# ── Исследование карточки товара ──────────────────────────────────────────────

async def explore_product(page: Page, url: str, panel: AgentPanel, store: EndpointStore):
    """Открываем карточку, кликаем по вкладкам и элементам."""
    short = url.split("/")[-1][:50]
    panel.navigate(url)
    logger.info("  📦 Карточка: %s", short)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    except Exception as e:
        panel.error(f"Ошибка: {e}")
        return

    await asyncio.sleep(2)
    await inject_cursor(page)
    await scroll_page(page, steps=2)

    vp = page.viewport_size or {"width":1280,"height":800}
    w, h = vp["width"], vp["height"]

    # Ищем и кликаем интерактивные элементы карточки
    panel.click("Элементы карточки товара")
    elements_js = """() => {
        const targets = [];
        // Кнопки (кроме "Купить" — не нажимаем реальную покупку)
        document.querySelectorAll('button, [role=tab], [role=button]').forEach(el => {
            const t = el.textContent.trim().toLowerCase();
            // Пропускаем buy/checkout кнопки
            if (['buy','купить','add to cart','оплатить'].some(x => t.includes(x))) return;
            const r = el.getBoundingClientRect();
            if (r.width > 10 && r.height > 10 && r.top > 0 && r.top < window.innerHeight) {
                targets.push({x: r.left+r.width/2, y: r.top+r.height/2, text: t.slice(0,30)});
            }
        });
        return targets.slice(0, 8);
    }"""

    elements = await safe_eval(page, elements_js) or []
    for el in elements:
        if el.get("x") and el.get("y"):
            panel.click(f"Нажимаю: {el.get('text','?')}")
            await move(page, el["x"], el["y"])
            await asyncio.sleep(0.4)
            await click(page, el["x"], el["y"])
            await asyncio.sleep(1.5)

    # Скроллим ещё раз — загружаем lazy content
    await scroll_page(page, steps=3)

    # Извлекаем LD+JSON и __NEXT_DATA__ — могут содержать данные товара
    ld_data = await safe_eval(page, """() => {
        return [...document.querySelectorAll('script[type="application/ld+json"]')]
            .map(s => { try { return JSON.parse(s.textContent) } catch { return null } })
            .filter(Boolean);
    }""")
    if ld_data:
        panel.extract(f"LD+JSON: {len(ld_data)} блоков")
        logger.info("  📋 LD+JSON на странице: %s", str(ld_data)[:200])

    next_data = await safe_eval(page, "() => window.__NEXT_DATA__")
    if next_data:
        panel.extract("__NEXT_DATA__ найден")
        logger.info("  📋 __NEXT_DATA__ keys: %s", list(next_data.keys()) if isinstance(next_data, dict) else "?")

    await asyncio.sleep(1)


# ── Основной скаут ────────────────────────────────────────────────────────────

async def run_scout(profile_id: str = PROFILE_ID):
    if not _PW_OK:
        logger.error("playwright не установлен")
        sys.exit(1)

    panel = AgentPanel(profile_id, MSB_API, "Zed Agent", "claude-sonnet-4-6",
                       "Разведка API: каталог + карточки товаров ggsel.net")
    store = EndpointStore()

    panel.start()
    panel.action("Подключаюсь к браузеру...", "wait")

    try:
        cdp_ws = await get_cdp_ws(profile_id)
    except Exception as e:
        panel.error(str(e)); panel.stop(); sys.exit(1)

    logger.info("CDP: %s", cdp_ws)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.connect_over_cdp(cdp_ws)
        ctx: BrowserContext = browser.contexts[0]
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await safe_eval(page, "window.msbSetAgentActive&&window.msbSetAgentActive(true)")
        await inject_cursor(page)
        await setup_intercept(page, store)

        all_product_links: List[str] = []

        # ── ШАГ 1: Каталог — собираем страницы и ссылки на товары ──────────

        for i, rel in enumerate(CATALOG_PAGES):
            url = BASE_URL + rel
            panel.action(f"[{i+1}/{len(CATALOG_PAGES)}] Каталог: {rel}", "navigate")
            logger.info("→ %s", url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as e:
                panel.error(f"Ошибка: {e}"); continue

            await asyncio.sleep(2)
            await inject_cursor(page)
            await scroll_page(page, steps=4)

            # Собираем ссылки на товары
            links = await get_product_links(page)
            new = [l for l in links if l not in all_product_links]
            all_product_links.extend(new)
            panel.extract(f"Найдено ссылок на товары: {len(all_product_links)} (новых: {len(new)})")

            store.save()
            logger.info("Эндпоинтов: %d, товаров в очереди: %d", len(store._d), len(all_product_links))

        # ── ШАГ 2: Карточки товаров — кликаем и исследуем ──────────────────

        panel.action(f"Исследую {len(all_product_links)} карточек товаров", "search")
        logger.info("\n=== Исследование карточек (%d шт.) ===", len(all_product_links))

        for i, url in enumerate(all_product_links[:10]):  # максимум 10 карточек
            panel.action(f"Карточка [{i+1}/{min(len(all_product_links),10)}]", "click")
            await explore_product(page, url, panel, store)
            store.save()

        # ── ШАГ 3: Итог ─────────────────────────────────────────────────────

        await safe_eval(page, "window.msbHideCursor&&window.msbHideCursor()")
        await safe_eval(page, "window.msbSetAgentActive&&window.msbSetAgentActive(false)")

        total = len(store._d)
        panel.success(f"Готово! {total} уникальных эндпоинтов")
        store.save()

        logger.info("\n" + "="*60)
        logger.info("ИТОГО: %d эндпоинтов", total)
        logger.info("="*60)
        for ep in store.summary()[:40]:
            logger.info("  [%3dx] %-5s %-28s %s",
                        ep["count"], ep["method"], ep["host"], ep["path_template"])
        logger.info("Файл: %s", OUT_FILE.resolve())

        panel.stop()
        await browser.close()

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-id", default=PROFILE_ID)
    args = ap.parse_args()
    asyncio.run(run_scout(args.profile_id))

if __name__ == "__main__":
    main()
