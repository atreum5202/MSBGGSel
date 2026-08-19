"""
parser/msb_agent_panel.py
=========================
Хелпер для агентов — сообщает MSB что агент делает в браузере.
Extension в браузере подхватывает и показывает панель поверх страницы.

Использование:
    from parser.msb_agent_panel import AgentPanel

    panel = AgentPanel(profile_id="1873432d-...", agent_name="Zed Agent")

    with panel:
        panel.navigate("https://ggsel.net/en/catalog")
        panel.read("Читаю список товаров...")
        panel.success("Нашёл 45 товаров")

        hint = panel.get_hint()
        if hint:
            print(f"Подсказка: {hint}")

Иконки (type → icon):
    navigate  🔗   click    👆   type     ⌨️
    read      👁️   think    🤔   search   🔍
    success   ✅   error    ❌   wait     ⏳
    info      ℹ️   extract  📋   screenshot 📸
"""
from __future__ import annotations

import contextlib
import logging
import threading
from typing import Optional

logger = logging.getLogger("ggselv7.agent_panel")

_ICONS = {
    "navigate":   "🔗",
    "click":      "👆",
    "type":       "⌨️",
    "read":       "👁️",
    "think":      "🤔",
    "search":     "🔍",
    "success":    "✅",
    "error":      "❌",
    "wait":       "⏳",
    "info":       "ℹ️",
    "extract":    "📋",
    "screenshot": "📸",
    "hint":       "💬",
}

try:
    import httpx as _httpx
    _HTTP_LIB = "httpx"
except ImportError:
    try:
        import requests as _requests
        _HTTP_LIB = "requests"
    except ImportError:
        _HTTP_LIB = None


def _post(url: str, data: dict, timeout: float = 1.5) -> bool:
    """Простой POST — fire-and-forget, никогда не бросает исключений."""
    try:
        if _HTTP_LIB == "httpx":
            _httpx.post(url, json=data, timeout=timeout)
        elif _HTTP_LIB == "requests":
            _requests.post(url, json=data, timeout=timeout)
        return True
    except Exception as e:
        logger.debug("agent_panel _post failed: %s", e)
        return False


def _get(url: str, timeout: float = 1.5) -> Optional[dict]:
    """Простой GET — возвращает dict или None."""
    try:
        if _HTTP_LIB == "httpx":
            r = _httpx.get(url, timeout=timeout)
            return r.json()
        elif _HTTP_LIB == "requests":
            r = _requests.get(url, timeout=timeout)
            return r.json()
    except Exception as e:
        logger.debug("agent_panel _get failed: %s", e)
    return None


class AgentPanel:
    """
    Отправляет статус агента в MSB, extension в браузере показывает панель.
    Полностью fire-and-forget — ошибки сети не бросают исключений.
    """

    def __init__(
        self,
        profile_id: str,
        msb_url: str = "http://127.0.0.1:17248",
        agent_name: str = "Zed Agent",
        model: str = "",
        task: str = "",
    ):
        self.profile_id = profile_id
        self.msb_url = msb_url.rstrip("/")
        self.agent_name = agent_name
        self.model = model
        self.task = task
        self._lock = threading.Lock()

    # ── Context manager ──────────────────────────────────────────────────────

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    @contextlib.contextmanager
    def session(self, task: str = "", agent_name: str = "", model: str = ""):
        """Алиас для with-блока с возможностью задать параметры на месте."""
        if task:      self.task = task
        if agent_name: self.agent_name = agent_name
        if model:     self.model = model
        self.start()
        try:
            yield self
        finally:
            self.stop()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        _post(self._url("start"), {
            "agentName": self.agent_name,
            "model": self.model,
            "task": self.task,
        })

    def stop(self):
        _post(self._url("stop"), {})

    # ── Действия (логируются в панель) ───────────────────────────────────────

    def action(self, text: str, type: str = "info"):
        """Универсальное действие — подбирает иконку по типу."""
        icon = _ICONS.get(type, "ℹ️")
        _post(self._url("action"), {"text": text, "icon": icon, "type": type})

    # Удобные алиасы

    def navigate(self, url: str):
        self.action(f"Открываю {url}", "navigate")

    def click(self, target: str):
        self.action(f"Нажимаю: {target}", "click")

    def type(self, target: str, value: str = ""):
        text = f"Ввожу в {target}" + (f": {value[:40]}" if value else "")
        self.action(text, "type")

    def read(self, what: str):
        self.action(what, "read")

    def search(self, query: str):
        self.action(f"Ищу: {query}", "search")

    def think(self, what: str):
        self.action(what, "think")

    def success(self, what: str):
        self.action(what, "success")

    def error(self, what: str):
        self.action(what, "error")

    def wait(self, what: str = "Ожидание..."):
        self.action(what, "wait")

    def extract(self, what: str):
        self.action(what, "extract")

    def screenshot(self):
        self.action("Делаю скриншот", "screenshot")

    def info(self, text: str):
        self.action(text, "info")

    # ── Подсказки от пользователя ────────────────────────────────────────────

    def get_hint(self) -> Optional[str]:
        """
        Читает подсказку которую пользователь написал в панели браузера.
        Одноразово — после чтения подсказка сбрасывается.
        Возвращает строку или None.
        """
        data = _get(self._url("hint"))
        if data and data.get("ok"):
            return data.get("data", {}).get("hint") or None
        return None

    # ── Утилиты ─────────────────────────────────────────────────────────────

    def _url(self, endpoint: str) -> str:
        return f"{self.msb_url}/profiles/{self.profile_id}/agent/{endpoint}"


# ── Быстрый тест ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time, sys
    pid = sys.argv[1] if len(sys.argv) > 1 else "1873432d-b054-48a6-a031-b2bacc0fe77d"
    print(f"Testing AgentPanel for profile {pid}")
    panel = AgentPanel(pid, agent_name="Test Agent", model="claude-sonnet-4-6", task="Тест панели")
    with panel:
        for step in [
            ("navigate",   "Открываю ggsel.net/en/catalog"),
            ("read",       "Читаю список товаров..."),
            ("search",     "Steam ключи"),
            ("extract",    "Нашёл 24 товара"),
            ("think",      "Анализирую цены..."),
            ("success",    "Готово — 24 товара обработано"),
        ]:
            panel.action(step[1], step[0])
            print(f"  {step[0]}: {step[1]}")
            time.sleep(1.5)
            hint = panel.get_hint()
            if hint:
                print(f"  💬 Подсказка: {hint}")
    print("Done.")
