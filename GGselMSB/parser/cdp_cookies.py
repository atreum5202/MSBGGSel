"""
cdp_cookies.py — минимальный Chrome DevTools Protocol клиент.

Только то что нужно парсеру:
  - get_cookies_via_cdp(debug_port, domain, timeout)
  - set_cookies_via_cdp(debug_port, cookies, timeout)
  - eval_via_cdp(debug_port, expression, timeout)  (для captcha/warmer сценариев)

Подключение: ws://127.0.0.1:<debugPort>/devtools/page/<page_id>

page_id берётся через HTTP GET http://127.0.0.1:<debugPort>/json/list
(это требует включённого CDP в профиле — MoreLogin включает по умолчанию).

ЗАЧЕМ СВОЁ:
  - В MoreLogin (и в будущем MSB на новом движке) НЕТ REST-эндпоинта
    "отдай куки". Всё через CDP.
  - Selenium / pyppeteer тащат ~50 МБ зависимостей. Нам нужен
    только cookies/get + cookies/set + Runtime.evaluate (для сценариев).
  - ~120 строк кода, ноль тяжёлых зависимостей (только `websockets`
    и встроенный urllib.request для /json/list).

ПОРТИРУЕМОСТЬ:
  Этот модуль НЕ знает про MoreLogin/MSB. Принимает только debug_port.
  Работает одинаково с любым антидетектом, который отдаёт CDP-порт:
  MoreLogin, MSB (новый движок), AdsPower, Dolphin{Anty}, GoLogin и т.д.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional
from urllib.request import urlopen
from urllib.error import URLError

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
    _WS_OK = True
except ImportError:
    _WS_OK = False
    websockets = None  # type: ignore
    ConnectionClosed = Exception  # type: ignore
    WebSocketException = Exception  # type: ignore


logger = logging.getLogger("cdp_cookies")

# ── Внутренние хелперы ────────────────────────────────────────────────────


def _http_get_json(url: str, timeout: float = 3.0) -> Any:
    """GET URL → JSON. Без внешних зависимостей (только stdlib)."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug("cdp: GET %s — %s", url, e)
        return None


def find_page_ws_url(debug_port: int | str, prefer_type: str = "page") -> Optional[str]:
    """
    Возвращает ws:// endpoint для подключения CDP.

    prefer_type:
      - "page"  → первая page-вкладка (для get_cookies)
      - "browser" → browser-level (для Target.setDiscoverTargets)

    MoreLogin при start профиля обычно открывает ОДНУ page-вкладку.
    """
    port = int(debug_port)
    url = f"http://127.0.0.1:{port}/json/list"
    data = _http_get_json(url, timeout=3.0)
    if not isinstance(data, list) or not data:
        # fallback: /json (без /list) — старый CDP
        data = _http_get_json(f"http://127.0.0.1:{port}/json", timeout=3.0)
        if not isinstance(data, list) or not data:
            return None

    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == prefer_type and entry.get("webSocketDebuggerUrl"):
            return entry["webSocketDebuggerUrl"]

    # если не нашли нужный тип — вернём первый попавшийся
    for entry in data:
        if isinstance(entry, dict) and entry.get("webSocketDebuggerUrl"):
            return entry["webSocketDebuggerUrl"]

    return None


# ── Низкоуровневая отправка CDP-команды ───────────────────────────────────


class _CDPSession:
    """Контекст-менеджер: открыли WS, держим, закрыли."""

    def __init__(self, ws_url: str, timeout: float = 10.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = None

    async def __aenter__(self) -> "_CDPSession":
        if not _WS_OK:
            raise RuntimeError("websockets не установлен: pip install websockets>=12")
        # CDP держит соединение открытым, надо выставить ping_interval
        self._ws = await asyncio.wait_for(
            websockets.connect(
                self.ws_url,
                max_size=8 * 1024 * 1024,    # 8 МБ, для больших cookies
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ),
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *_):
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def send(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """
        Отправить CDP-команду, дождаться ответа с тем же id.
        Таймаут — на ОДНУ команду.
        """
        if self._ws is None:
            raise RuntimeError("CDP session not opened")
        msg_id = random_id()
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params

        t0 = time.monotonic()
        await self._ws.send(json.dumps(payload))
        # читаем пока не дождёмся ответа с нашим id (могут прилетать events)
        while True:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=timeout if timeout is not None else self.timeout,
            )
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == msg_id:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logger.debug("cdp: %s → %dms", method, elapsed_ms)
                if "error" in resp:
                    raise RuntimeError(f"CDP error in {method}: {resp['error']}")
                return resp.get("result", {})
            # иначе это event — пропускаем


# ── Публичные функции ────────────────────────────────────────────────────


def random_id() -> int:
    return random.randint(1, 2**31 - 1)


async def get_cookies_via_cdp(
    debug_port: int | str,
    domain: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, str]:
    """
    Достать все куки профиля через CDP.

    domain — если задан, фильтрует по нему (например "ggsel.net" → qq.ggsel.net тоже попадёт).

    Возвращает dict {name: value}.
    """
    ws_url = find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        logger.warning("cdp: debug_port=%s — page не найдена (/json/list пуст)", debug_port)
        return {}

    async with _CDPSession(ws_url, timeout=timeout) as s:
        result = await s.send("Network.getAllCookies", timeout=timeout)
        raw_cookies = result.get("cookies") or []

    return _filter_cookies(raw_cookies, domain_filter=domain)


async def set_cookies_via_cdp(
    debug_port: int | str,
    cookies: List[dict],
    timeout: float = 10.0,
) -> int:
    """
    Записать куки в профиль через CDP.

    cookies: список объектов в формате CDP Network.Cookie:
      {name, value, domain, path?, expires?, httpOnly?, secure?, sameSite?}

    Возвращает количество успешно записанных куков.
    """
    if not cookies:
        return 0

    ws_url = find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        logger.warning("cdp: debug_port=%s — page не найдена", debug_port)
        return 0

    async with _CDPSession(ws_url, timeout=timeout) as s:
        ok = 0
        for c in cookies:
            try:
                params = {
                    "name":     c.get("name"),
                    "value":    c.get("value", ""),
                    "domain":   c.get("domain", ""),
                    "path":     c.get("path", "/"),
                }
                if "expires" in c:
                    # CDP ждёт UNIX-секунды; cookie.expires может быть -1
                    exp = c.get("expires")
                    if isinstance(exp, (int, float)) and exp > 0:
                        params["expires"] = int(exp)
                if c.get("httpOnly"):
                    params["httpOnly"] = True
                if c.get("secure"):
                    params["secure"] = True
                if c.get("sameSite"):
                    params["sameSite"] = c["sameSite"]

                await s.send("Network.setCookie", params=params, timeout=timeout)
                ok += 1
            except Exception as e:
                logger.warning("cdp: setCookie(%s) — %s", c.get("name"), e)

    logger.info("cdp: импортировано %d/%d куков через debug_port=%s", ok, len(cookies), debug_port)
    return ok


async def eval_via_cdp(
    debug_port: int | str,
    expression: str,
    await_promise: bool = False,
    timeout: float = 10.0,
) -> Any:
    """
    Выполнить JS-выражение в активной page-вкладке через Runtime.evaluate.

    Используется для сценариев прогрева/капчи (например, кликнуть по чекбоксу
    "I'm not a robot" или дождаться загрузки DOM).

    Возвращает result.value (или result.exceptionDetails.exception при ошибке).
    """
    ws_url = find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        raise RuntimeError(f"cdp: debug_port={debug_port} — page не найдена")

    async with _CDPSession(ws_url, timeout=timeout) as s:
        result = await s.send(
            "Runtime.evaluate",
            params={
                "expression":    expression,
                "awaitPromise":  await_promise,
                "returnByValue": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS error: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")


async def wait_for_url(
    debug_port: int | str,
    url_contains: str,
    timeout: float = 15.0,
) -> bool:
    """
    Подождать пока активная вкладка не загрузит URL, содержащий url_contains.
    Полезно после goto — чтобы знать, что страница реально отрисовалась.
    """
    expr = f"""
    (() => {{
        const target = {json.dumps(url_contains)};
        if (location.href.includes(target)) return true;
        return new Promise(resolve => {{
            const check = () => {{
                if (location.href.includes(target)) resolve(true);
                else setTimeout(check, 200);
            }};
            check();
            setTimeout(() => resolve(false), {int(timeout * 1000)});
        }});
    }})()
    """
    try:
        result = await eval_via_cdp(debug_port, expr, await_promise=True, timeout=timeout + 2)
        return bool(result)
    except Exception as e:
        logger.warning("cdp: wait_for_url(%r) — %s", url_contains, e)
        return False


async def navigate(
    debug_port: int | str,
    url: str,
    wait_until: str = "load",
    timeout: float = 30.0,
) -> bool:
    """
    Перейти по URL через CDP Page.navigate.
    wait_until: "load" | "domcontentloaded" | "networkidle" (эвристика).
    """
    ws_url = find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        raise RuntimeError(f"cdp: debug_port={debug_port} — page не найдена")

    async with _CDPSession(ws_url, timeout=timeout) as s:
        await s.send("Page.navigate", params={"url": url}, timeout=timeout)
    if wait_until == "load":
        return await wait_for_url(debug_port, url, timeout=timeout)
    return True


# ── Хелперы для фильтрации ────────────────────────────────────────────────


def _filter_cookies(
    cookies: List[dict],
    domain_filter: Optional[str] = None,
) -> Dict[str, str]:
    """
    CDP-формат: {name, value, domain, path, expires, httpOnly, secure, sameSite}
    → {name: value}, опционально с фильтром по domain.
    """
    result: Dict[str, str] = {}
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        if domain_filter:
            cd = (c.get("domain") or "").lstrip(".")
            if domain_filter not in cd:
                continue
        result[name] = c.get("value", "")
    return result


def list_to_cdp_cookies(cookie_dict: Dict[str, str], domain: str) -> List[dict]:
    """
    Утилита: из {name: value} + domain → список в формате CDP
    (для set_cookies_via_cdp).
    """
    return [
        {"name": n, "value": v, "domain": domain, "path": "/"}
        for n, v in cookie_dict.items()
    ]


# ── CLI для отладки ────────────────────────────────────────────────────────


async def _cli_main() -> None:
    import sys
    if len(sys.argv) < 3:
        print("usage: cdp_cookies.py <debug_port> [domain]")
        print("       cdp_cookies.py list <debug_port>")
        sys.exit(2)

    if sys.argv[1] == "list":
        port = sys.argv[2]
        # /json/list — синхронный
        data = _http_get_json(f"http://127.0.0.1:{port}/json/list", timeout=3.0)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    port = int(sys.argv[1])
    domain = sys.argv[2] if len(sys.argv) > 2 else None
    cookies = await get_cookies_via_cdp(port, domain=domain, timeout=10.0)
    print(json.dumps(cookies, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_cli_main())
