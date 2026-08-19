"""
parser/agent_browser.py
=======================
Единый интерфейс агента к браузеру MSB.

Агент видит ВСЁ и управляет КАК ЧЕЛОВЕК:
  - screenshot()          → PNG файл → читается read_file → визуальный анализ
  - navigate(url)         → CDP Page.navigate
  - click(x, y)           → CDP мышь + визуальный ripple в браузере
  - click_element(sel)    → найти элемент в DOM → вычислить центр → click()
  - type(text)            → CDP Input.insertText (после click на поле)
  - key(key)              → CDP Input.dispatchKeyEvent (Enter, Tab, Escape...)
  - scroll(dy)            → JS window.scrollBy
  - eval(js)              → CDP Runtime.evaluate → результат
  - get_url()             → текущий URL вкладки
  - get_dom(sel?)         → outerHTML элемента или body
  - wait(sec)             → asyncio.sleep
  - network_endpoints()   → MSB NetworkCapture: все эндпоинты за сессию
  - network_requests()    → MSB NetworkCapture: запросы с телами
  - network_clear()       → сбросить буфер перед новой сессией
  - storage()             → localStorage + sessionStorage + IndexedDB

Паттерн агента (control loop):
    async with AgentBrowser.start(PROFILE_ID) as browser:
        await browser.navigate("https://ggsel.net/en/catalog")
        img = await browser.screenshot("catalog")   # → видим что на экране
        # ... анализируем img через read_file ...
        await browser.click_element("input[type=search]")
        await browser.type("steam")
        await browser.key("Enter")
        await browser.wait(2)
        img2 = await browser.screenshot("search_results")
        eps = browser.network_endpoints()           # → что перехватили

Запуск всего стека:
  1. MSB запущен (start.vbs)
  2. mitmweb запущен (mitmweb.vbs) → прокси 127.0.0.1:18100
  3. AgentBrowser.start() — сам запускает профиль, читает debugPort

Скриншоты сохраняются в SCREENSHOTS_DIR.
Файл исключён из file_scan_exclusions — агент ВИДИТ их через read_file.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx

from parser.cdp_cookies import _CDPSession, eval_via_cdp, find_page_ws_url, navigate as _navigate
from parser.msb_agent_panel import AgentPanel
from parser.msb_network_capture import MsbNetworkCapture

logger = logging.getLogger("ggselv7.agent_browser")

PROFILE_ID     = "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB_API        = "http://127.0.0.1:17248"
SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"  # корень проекта/screenshots — виден агенту


# ── Запуск / остановка профиля ────────────────────────────────────────────────

async def _start_profile(profile_id: str, msb_url: str) -> Optional[int]:
    """POST /profiles/:id/start → debugPort."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{msb_url}/profiles/{profile_id}/start", json={})
            d = r.json()
            port = (
                d.get("data", {}).get("debugPort")
                or d.get("debugPort")
                or d.get("data", {}).get("debug_port")
                or d.get("debug_port")
            )
            if port:
                logger.info("Профиль запущен, debugPort=%s", port)
                return int(port)
            # Уже запущен — читаем статус
            r2 = await c.get(f"{msb_url}/profiles/{profile_id}/status")
            d2 = r2.json()
            port2 = d2.get("data", {}).get("debugPort") or d2.get("data", {}).get("debug_port")
            if port2:
                logger.info("Профиль уже запущен, debugPort=%s", port2)
                return int(port2)
    except Exception as e:
        logger.error("start_profile: %s", e)
    return None


async def _stop_profile(profile_id: str, msb_url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(f"{msb_url}/profiles/{profile_id}/stop", json={})
            logger.info("Профиль остановлен")
    except Exception as e:
        logger.warning("stop_profile: %s", e)


# ── AgentBrowser ─────────────────────────────────────────────────────────────

class AgentBrowser:
    """
    Полный контроль браузера для агента.

    Создавать через AgentBrowser.start() (async context manager):

        async with AgentBrowser.start(PROFILE_ID) as b:
            await b.navigate("https://ggsel.net")
            img = await b.screenshot("start")
            # читай img через read_file и анализируй
    """

    def __init__(
        self,
        debug_port: int,
        profile_id: str = PROFILE_ID,
        msb_url: str = MSB_API,
        agent_name: str = "Zed Agent",
        task: str = "",
        stop_on_exit: bool = True,
    ):
        self.debug_port   = debug_port
        self.profile_id   = profile_id
        self.msb_url      = msb_url
        self.stop_on_exit = stop_on_exit
        self._nc  = MsbNetworkCapture(msb_url)
        self._panel = AgentPanel(profile_id, msb_url, agent_name, task=task)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Фабрика ──────────────────────────────────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def start(
        cls,
        profile_id: str = PROFILE_ID,
        msb_url: str = MSB_API,
        agent_name: str = "Zed Agent",
        task: str = "",
        clear_network: bool = True,
        stop_on_exit: bool = False,   # обычно не останавливаем — пользователь хочет видеть браузер
    ):
        """
        Запускает профиль, получает debugPort, возвращает AgentBrowser.

        async with AgentBrowser.start() as b:
            await b.navigate(...)
        """
        logger.info("AgentBrowser.start: profile=%s", profile_id)
        port = await _start_profile(profile_id, msb_url)
        if not port:
            raise RuntimeError(f"Не удалось получить debugPort для профиля {profile_id}")

        await asyncio.sleep(2)  # дать браузеру подняться

        browser = cls(
            debug_port=port,
            profile_id=profile_id,
            msb_url=msb_url,
            agent_name=agent_name,
            task=task,
            stop_on_exit=stop_on_exit,
        )
        if clear_network:
            browser.network_clear()

        browser._panel.start()
        try:
            yield browser
        finally:
            browser._panel.stop()
            browser._nc.close()
            if stop_on_exit:
                await _stop_profile(profile_id, msb_url)

    # ── Вижу: скриншот ───────────────────────────────────────────────────────

    async def screenshot(self, name: str = "screen") -> Path:
        """
        Сделать скриншот → сохранить PNG → вернуть Path.

        Path НЕ в file_scan_exclusions → агент ВИДИТ файл через read_file.

        Использование:
            img = await b.screenshot("after_click")
            # потом в Zed: read_file(str(img)) → визуальный анализ
        """
        self._panel.action("Скриншот", "screenshot")
        ws_url = find_page_ws_url(self.debug_port)
        if not ws_url:
            raise RuntimeError(f"CDP page не найдена на порту {self.debug_port}")
        async with _CDPSession(ws_url) as s:
            result = await s.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result.get("data", ""))
        ts = int(time.time())
        path = SCREENSHOTS_DIR / f"{name}_{ts}.png"
        path.write_bytes(data)
        logger.info("screenshot → %s (%d bytes)", path, len(data))
        return path

    # ── Вижу: DOM ────────────────────────────────────────────────────────────

    async def get_url(self) -> str:
        """Текущий URL активной вкладки."""
        return await self.eval("location.href") or ""

    async def get_title(self) -> str:
        """Заголовок страницы."""
        return await self.eval("document.title") or ""

    async def get_dom(self, selector: str = "body") -> str:
        """outerHTML элемента (по умолчанию body). Для анализа структуры страницы."""
        js = f"document.querySelector({json.dumps(selector)})?.outerHTML?.slice(0,20000)"
        return await self.eval(js) or ""

    async def get_text(self, selector: str = "body") -> str:
        """innerText элемента — только текст без тегов."""
        js = f"document.querySelector({json.dumps(selector)})?.innerText?.slice(0,10000)"
        return await self.eval(js) or ""

    async def find_links(self, pattern: str = "") -> list[str]:
        """Все href на странице, опционально фильтр по подстроке."""
        js = f"""
        JSON.stringify(
            [...document.querySelectorAll('a[href]')]
            .map(a => a.href)
            .filter(h => h.includes({json.dumps(pattern)}))
            .filter((v,i,a) => a.indexOf(v)===i)
            .slice(0, 100)
        )
        """
        raw = await self.eval(js)
        try:
            return json.loads(raw) if raw else []
        except Exception:
            return []

    async def eval(self, js: str) -> Any:
        """Выполнить JS → вернуть результат. Основной инструмент чтения DOM."""
        try:
            return await eval_via_cdp(self.debug_port, js)
        except Exception as e:
            logger.warning("eval: %s", e)
            return None

    # ── Управляю: навигация ──────────────────────────────────────────────────

    async def navigate(self, url: str, wait: float = 2.5) -> None:
        """Перейти по URL → подождать загрузки."""
        self._panel.navigate(url)
        logger.info("navigate → %s", url)
        try:
            await _navigate(self.debug_port, url, timeout=25.0)
        except Exception as e:
            logger.warning("navigate timeout (не критично): %s", e)
        await asyncio.sleep(wait)

    async def reload(self, wait: float = 2.0) -> None:
        """Перезагрузить текущую страницу."""
        await self.eval("location.reload()")
        await asyncio.sleep(wait)

    # ── Управляю: мышь ───────────────────────────────────────────────────────

    async def click(self, x: int, y: int) -> None:
        """
        Кликнуть по координатам (пиксели от левого верхнего угла viewport).
        Показывает ripple-анимацию в браузере через extension.
        """
        self._panel.click(f"({x}, {y})")
        ws_url = find_page_ws_url(self.debug_port)
        async with _CDPSession(ws_url) as s:
            for ev_type in ("mousePressed", "mouseReleased"):
                await s.send("Input.dispatchMouseEvent", {
                    "type": ev_type, "x": x, "y": y,
                    "button": "left", "clickCount": 1,
                    "modifiers": 0,
                })
        # визуальный ripple через extension
        await self.eval(f"window.msbClick && window.msbClick({x}, {y})")

    async def click_element(self, selector: str) -> bool:
        """
        Найти элемент в DOM → вычислить центр → кликнуть.
        Возвращает True если элемент найден и кликнут.
        """
        self._panel.click(selector)
        rect_raw = await self.eval(
            f"JSON.stringify(document.querySelector({json.dumps(selector)})?.getBoundingClientRect())"
        )
        if not rect_raw:
            logger.warning("click_element: элемент не найден: %s", selector)
            return False
        try:
            rect = json.loads(rect_raw)
            x = int(rect["left"] + rect["width"] / 2)
            y = int(rect["top"] + rect["height"] / 2)
            await self.click(x, y)
            return True
        except Exception as e:
            logger.warning("click_element parse: %s", e)
            return False

    async def hover(self, x: int, y: int) -> None:
        """Переместить мышь (без клика). Обновляет cursor dot агента."""
        ws_url = find_page_ws_url(self.debug_port)
        async with _CDPSession(ws_url) as s:
            await s.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
            })
        await self.eval(f"window.msbMoveCursor && window.msbMoveCursor({x}, {y})")

    # ── Управляю: клавиатура ─────────────────────────────────────────────────

    async def type(self, text: str) -> None:
        """
        Ввести текст. Элемент должен быть в фокусе (сначала click_element).
        Для специальных символов — используй key().
        """
        self._panel.action(f"Ввожу: {text[:30]}", "type")
        ws_url = find_page_ws_url(self.debug_port)
        # Вводим чанками по 500 символов
        for i in range(0, len(text), 500):
            chunk = text[i:i+500]
            async with _CDPSession(ws_url) as s:
                await s.send("Input.insertText", {"text": chunk})

    async def key(self, key: str) -> None:
        """
        Нажать специальную клавишу.
        Примеры: "Enter", "Tab", "Escape", "ArrowDown", "Backspace"
        """
        ws_url = find_page_ws_url(self.debug_port)
        async with _CDPSession(ws_url) as s:
            for ev in ("keyDown", "keyUp"):
                await s.send("Input.dispatchKeyEvent", {
                    "type": ev,
                    "key": key,
                    "code": key,
                    "windowsVirtualKeyCode": _KEY_CODES.get(key, 0),
                })

    # ── Управляю: скролл ─────────────────────────────────────────────────────

    async def scroll(self, dy: int = 400, smooth: bool = True) -> None:
        """Прокрутить страницу вниз (dy > 0) или вверх (dy < 0)."""
        behavior = "smooth" if smooth else "instant"
        await self.eval(f"window.scrollBy({{top:{dy}, behavior:'{behavior}'}})")
        await asyncio.sleep(0.5)

    async def scroll_to_bottom(self) -> None:
        """Прокрутить в самый низ страницы."""
        await self.eval("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
        await asyncio.sleep(1)

    async def scroll_to_top(self) -> None:
        await self.eval("window.scrollTo({top: 0, behavior: 'smooth'})")
        await asyncio.sleep(0.5)

    # ── Управляю: панель агента ──────────────────────────────────────────────

    def say(self, text: str, kind: str = "info") -> None:
        """Показать сообщение в синей панели агента в браузере."""
        self._panel.action(text, kind)

    def get_hint(self) -> Optional[str]:
        """Прочитать подсказку от пользователя из панели браузера (одноразово)."""
        return self._panel.get_hint()

    # ── Вижу: сетевой трафик ─────────────────────────────────────────────────

    def network_clear(self) -> None:
        """Сбросить буфер NetworkCapture — делать перед новым сценарием."""
        self._nc.clear(self.profile_id)
        logger.info("NetworkCapture буфер очищен")

    def network_endpoints(self, host: str = None, pattern: str = None) -> list:
        """Все эндпоинты за сессию, сгруппированные по шаблону пути."""
        return self._nc.endpoints(self.profile_id, host=host, pattern=pattern)

    def network_requests(self, host: str = None, pattern: str = None) -> list:
        """Все запросы с телами ответов (JSON ≤64KB)."""
        return self._nc.requests(self.profile_id, host=host, pattern=pattern)

    def network_status(self) -> dict:
        """{ active, count, oldestAt, newestAt }"""
        return self._nc.network_status(self.profile_id) if hasattr(self._nc, 'network_status') else self._nc.status(self.profile_id)

    def network_har(self) -> dict:
        """Экспорт трафика в HAR 1.2 (открывается в DevTools → Network)."""
        return self._nc.har(self.profile_id)

    # ── Вижу: хранилище браузера ─────────────────────────────────────────────

    async def storage(self) -> dict:
        """
        localStorage + sessionStorage + IndexedDB список.
        Там часто хранятся токены, настройки, кэш ggsel.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{self.msb_url}/profiles/{self.profile_id}/storage")
                return r.json().get("data", {})
        except Exception as e:
            logger.warning("storage: %s", e)
            return {}

    # ── Утилиты ──────────────────────────────────────────────────────────────

    async def wait(self, seconds: float) -> None:
        """Подождать N секунд (явный sleep для загрузки страниц / анимаций)."""
        await asyncio.sleep(seconds)

    async def wait_for_selector(self, selector: str, timeout: float = 10.0) -> bool:
        """
        Ждать появления элемента в DOM.
        Возвращает True если появился, False если таймаут.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            found = await self.eval(f"!!document.querySelector({json.dumps(selector)})")
            if found:
                return True
            await asyncio.sleep(0.4)
        logger.warning("wait_for_selector: %s не появился за %ss", selector, timeout)
        return False

    async def wait_for_url_contains(self, substring: str, timeout: float = 10.0) -> bool:
        """Ждать пока URL не будет содержать подстроку."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            url = await self.get_url()
            if substring in url:
                return True
            await asyncio.sleep(0.4)
        return False


# ── Таблица кодов клавиш ─────────────────────────────────────────────────────

_KEY_CODES = {
    "Enter":     13,
    "Tab":       9,
    "Escape":    27,
    "Backspace": 8,
    "Delete":    46,
    "ArrowUp":   38,
    "ArrowDown": 40,
    "ArrowLeft": 37,
    "ArrowRight":39,
    "Home":      36,
    "End":       35,
    "PageUp":    33,
    "PageDown":  34,
    "Space":     32,
    "F5":        116,
}


# ── CLI / быстрый тест ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    async def demo():
        print("AgentBrowser demo — подключаюсь к профилю...")
        async with AgentBrowser.start(
            task="Демо: открыть ggsel и сделать скриншот"
        ) as b:
            b.say("Агент подключился", "info")

            await b.navigate("https://ggsel.net/en/catalog")
            img = await b.screenshot("demo_catalog")
            print(f"Скриншот: {img}")
            print(f"URL: {await b.get_url()}")
            print(f"Title: {await b.get_title()}")

            eps = b.network_endpoints(host="api.ggsel.com")
            print(f"NetworkCapture: {len(eps)} эндпоинтов api.ggsel.com")

            b.say("Демо завершён", "success")

    asyncio.run(demo())
