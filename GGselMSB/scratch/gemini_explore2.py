"""
scratch/gemini_explore2.py — углублённая разведка страницы Gemini.

Что делаем:
  1. Запускаем профиль
  2. Переходим на gemini.google.com/app
  3. Каждые 3с (до 30с) делаем скрин + читаем URL/title/DOM-критич.элементы
  4. Сохраняем ЛОГ каждого шага + ВСЕ промежуточные скрины
  5. Пробуем загрузить фото (если нашли input[type=file])
  6. Пробуем ввести промпт + отправить (если нашли input)
  7. Ждём результат, снимаем серию скринов каждые 5с
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

import httpx
import websockets

# ── MoreLogin settings (как в первом скрипте) ─────────────────────────────
MORELOGIN_API_BASE = "http://127.0.0.1:40000"
MORELOGIN_API_ID = "1716740459457616"
MORELOGIN_API_KEY = "8afb02927e724b6caadc6363f13f3c61"

# Куда пишем артефакты
SCRATCH = pathlib.Path("scratch")
SCRATCH.mkdir(exist_ok=True)
LOG_PATH = SCRATCH / "gemini_explore2.log"

# Целевой URL
GEMINI_URL = "https://gemini.google.com/app"

# Найдём тестовое фото
def find_test_image() -> Optional[pathlib.Path]:
    for d in (pathlib.Path(os.path.expanduser("~/Desktop")),
              pathlib.Path("C:/Users/Atreum/Desktop")):
        if not d.exists():
            continue
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            cand = list(d.glob(f"*{ext}"))
            if cand:
                return cand[0]
    return None


# ── Лог в файл + консоль ──────────────────────────────────────────────────

class Tee:
    """Пишет в файл + в консоль. Сохраняет оригинальный stdout ДО переназначения."""
    def __init__(self, path: pathlib.Path, stream):
        self._orig = stream
        self.f = open(path, "w", encoding="utf-8")
    def write(self, s: str):
        try:
            self._orig.write(s)
        except Exception:
            pass
        try:
            self.f.write(s)
            self.f.flush()
        except Exception:
            pass
    def flush(self):
        try: self._orig.flush()
        except Exception: pass
        try: self.f.flush()
        except Exception: pass


# ── MoreLogin helpers ─────────────────────────────────────────────────────

async def get_first_profile() -> str:
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post(
            f"{MORELOGIN_API_BASE}/api/env/page",
            json={"pageNo": 1, "pageSize": 5},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY},
        )
        r.raise_for_status()
        data = r.json()
        # MoreLogin формат — проверим оба варианта
        d = data.get("data") or {}
        items = d.get("list") or d.get("dataList") or d.get("records") or []
        if isinstance(d, list):
            items = d
        if not items:
            raise RuntimeError(f"Нет профилей. Ответ: {data!r}")
        prof = items[0]
        env_id = str(prof.get("envId") or prof.get("id") or prof.get("seq"))
        print(f"  Профиль: envId={env_id}, name={prof.get('envName') or prof.get('name')!r}")
        return env_id


async def start_profile(env_id: str) -> str:
    async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
        r = await c.post(
            f"{MORELOGIN_API_BASE}/api/env/start",
            json={"envId": env_id, "isHeadless": False, "cdpEvasion": True},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY},
        )
        r.raise_for_status()
        data = r.json()
        debug_port = str(data["data"]["debugPort"])
        print(f"  debugPort: {debug_port}")
        return debug_port


async def get_tabs(debug_port: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        r = await c.get(f"http://127.0.0.1:{debug_port}/json/list")
        r.raise_for_status()
        tabs = r.json()
    page_tabs = [t for t in tabs if t.get("type") == "page"]
    if not page_tabs:
        # fallback /json
        async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
            r = await c.get(f"http://127.0.0.1:{debug_port}/json")
            r.raise_for_status()
            tabs = r.json()
        page_tabs = [t for t in tabs if t.get("type") == "page"]
    return page_tabs or tabs


# ── CDP helpers ───────────────────────────────────────────────────────────

class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._events: List[dict] = []

    async def send(self, method: str, params: Optional[dict] = None,
                   timeout: float = 30.0) -> dict:
        self._id += 1
        msg_id = self._id
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP {method} (id={msg_id}) таймаут {timeout}с")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            # event — сохраним последние 30 для диагностики
            if "method" in msg:
                self._events.append(msg)
                if len(self._events) > 30:
                    self._events = self._events[-30:]

    async def navigate(self, url: str, wait_load: float = 5.0):
        await self.send("Page.enable")
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_load)

    async def screenshot(self, path: pathlib.Path, full: bool = False) -> int:
        params = {"format": "jpeg", "quality": 80}
        if full:
            params["captureBeyondViewport"] = True
        r = await self.send("Page.captureScreenshot", params, timeout=15.0)
        data = base64.b64decode(r["data"])
        path.write_bytes(data)
        return len(data)

    async def eval(self, expression: str, await_promise: bool = False,
                   timeout: float = 15.0) -> Any:
        r = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True,
             "awaitPromise": await_promise},
            timeout=timeout,
        )
        if "exceptionDetails" in r:
            return {"__error__": r["exceptionDetails"]}
        return r.get("result", {}).get("value")

    async def dom_querySelectorAll(self, selector: str) -> List[dict]:
        """Возвращает список {selector, attrs, rect} для всех элементов."""
        js = f"""
        (() => {{
            const els = Array.from(document.querySelectorAll({json.dumps(selector)}));
            return els.map(el => ({{
                tag: el.tagName,
                id: el.id || null,
                cls: (el.className && typeof el.className === 'string')
                     ? el.className.substring(0, 120) : null,
                name: el.getAttribute('name'),
                type: el.getAttribute('type'),
                accept: el.getAttribute('accept'),
                placeholder: el.getAttribute('placeholder'),
                ariaLabel: el.getAttribute('aria-label'),
                title: el.title || null,
                role: el.getAttribute('role'),
                contenteditable: el.getAttribute('contenteditable'),
                dataTestId: el.getAttribute('data-test-id') || el.getAttribute('data-testid'),
                textPreview: (el.innerText || el.textContent || '').substring(0, 80).trim() || null,
                rect: (() => {{
                    const r = el.getBoundingClientRect();
                    return {{x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height)}};
                }})(),
                visible: (() => {{
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'
                           && s.display !== 'none' && +s.opacity > 0;
                }})(),
            }}));
        }})()
        """
        return await self.eval(js) or []

    async def get_state(self) -> dict:
        js = """
        (() => ({
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            bodyText: (document.body && document.body.innerText || '').substring(0, 400).trim(),
            hasContentEditable: !!document.querySelector('[contenteditable="true"]'),
            hasTextarea: !!document.querySelector('textarea'),
            hasFileInput: !!document.querySelector('input[type=file]'),
            fileInputCount: document.querySelectorAll('input[type=file]').length,
            buttonCount: document.querySelectorAll('button').length,
            imgCount: document.querySelectorAll('img').length,
        }))()
        """
        return await self.eval(js) or {}

    async def find_input_nodeId(self) -> Optional[int]:
        """Найти <input type=file> через DOM.querySelector и вернуть nodeId."""
        doc = await self.send("DOM.getDocument", timeout=10.0)
        root_id = (doc.get("root") or {}).get("nodeId")
        if not root_id:
            return None
        for sel in (
            'input[type="file"][accept*="image"]',
            'input[type="file"][accept*="application"]',
            'input[type="file"]',
        ):
            try:
                r = await self.send("DOM.querySelector",
                                    {"nodeId": root_id, "selector": sel}, 10.0)
                nid = r.get("nodeId")
                if nid and nid != 0:
                    print(f"  [DOM] нашли input[type=file] по {sel!r}, nodeId={nid}")
                    return nid
            except Exception as e:
                print(f"  [DOM] querySelector {sel!r} — {e}")
        return None


# ── Главный сценарий разведки ─────────────────────────────────────────────

async def main():
    out = Tee(LOG_PATH, sys.stdout)
    err = Tee(LOG_PATH, sys.stderr)
    sys.stdout = out
    sys.stderr = err

    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} START ===")
    print(f"Python {sys.version.split()[0]}")
    print()

    # 1. Профиль
    env_id = await get_first_profile()
    debug_port = await start_profile(env_id)

    # 2. Список вкладок
    tabs = await get_tabs(debug_port)
    print(f"\n[CDP] Найдено вкладок: {len(tabs)}")
    for i, t in enumerate(tabs):
        print(f"  #{i}: type={t.get('type')}, url={t.get('url')!r}, title={t.get('title')!r}")
    if not tabs:
        raise RuntimeError("Нет ни одной CDP-вкладки")
    tab = tabs[0]
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"\n[CDP] Подключаемся к {ws_url}")

    test_img = find_test_image()
    print(f"\n[Setup] Тестовое фото: {test_img}")

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024,
                                  ping_interval=20, ping_timeout=20) as ws:
        c = CDP(ws)
        await c.send("Page.enable")
        await c.send("DOM.enable")
        await c.send("Runtime.enable")
        await c.send("Network.enable")

        # 3. Навигация
        print(f"\n[NAV] Переходим на {GEMINI_URL}")
        await c.send("Page.navigate", {"url": GEMINI_URL})

        # 4. Снимаем состояние каждые 3с, до 30с
        for sec in (3, 6, 10, 15, 20, 25, 30):
            await asyncio.sleep(sec - (sec - 3 if sec == 3 else
                                       [3, 6, 10, 15, 20, 25, 30][[3, 6, 10, 15, 20, 25, 30].index(sec) - 1]))
            st = await c.get_state()
            print(f"\n[T+{sec:>2}s] state:")
            for k, v in st.items():
                if k == "bodyText":
                    v = (v[:120] + "…") if isinstance(v, str) and len(v) > 120 else v
                print(f"   {k}: {v!r}")
            shot_path = SCRATCH / f"gemini_screenshot_T{sec:02d}.jpg"
            sz = await c.screenshot(shot_path)
            print(f"   screenshot → {shot_path.name} ({sz} bytes)")

            if "accounts.google.com" in st.get("url", ""):
                print("   ⚠️  РЕДИРЕКТ НА LOGIN — профиль не залогинен в Google")
            if st.get("hasContentEditable") and st.get("hasFileInput"):
                print("   ✅  Появились input + file input — страница готова")
                break

        # 5. Если всё ещё login — попробуем кликнуть "Add account" или "Use another account"
        st = await c.get_state()
        if "accounts.google.com" in st.get("url", ""):
            print("\n[LOGIN] Пытаемся пройти экран логина...")
            # На /accountchooser часто есть кнопка "Use another account"
            # Снимем кнопки
            btns = await c.dom_querySelectorAll("button, a[role=button]")
            print(f"  Найдено кнопок/ссылок: {len(btns)}")
            for b in btns[:30]:
                t = b.get("textPreview") or ""
                if t and len(t) < 80:
                    print(f"    - {b.get('tag')!r} aria={b.get('ariaLabel')!r} "
                          f"text={t!r} visible={b.get('visible')}")

        # 6. Разведка элементов на финальной странице
        print("\n[INSPECT] Все <input type=file>:")
        for el in await c.dom_querySelectorAll('input[type=file]'):
            print(f"  - {el}")

        print("\n[INSPECT] Все [contenteditable=true]:")
        for el in await c.dom_querySelectorAll('[contenteditable="true"]'):
            print(f"  - {el}")

        print("\n[INSPECT] Все <textarea>:")
        for el in await c.dom_querySelectorAll('textarea'):
            print(f"  - {el}")

        print("\n[INSPECT] Все <button>:")
        for el in await c.dom_querySelectorAll('button'):
            t = el.get("textPreview") or ""
            if t and (el.get("visible") or len(t) < 40):
                print(f"  - aria={el.get('ariaLabel')!r} text={t!r} "
                      f"visible={el.get('visible')} cls={el.get('cls')!r}")

        print("\n[INSPECT] Все <img> (первые 20):")
        imgs = await c.dom_querySelectorAll('img')
        for el in imgs[:20]:
            print(f"  - {el.get('rect')} visible={el.get('visible')}")

        # 7. Если есть file input и тестовое фото — загрузим
        file_inputs = await c.dom_querySelectorAll('input[type=file]')
        if file_inputs and test_img and test_img.exists():
            print(f"\n[UPLOAD] Загружаем {test_img}")
            node_id = await c.find_input_nodeId()
            if node_id:
                await c.send("DOM.setFileInputFiles",
                             {"nodeId": node_id, "files": [str(test_img)]},
                             timeout=30.0)
                print("  ✅ файл загружен")
                await asyncio.sleep(3)
                st = await c.get_state()
                print(f"  state после загрузки: {st}")
                await c.screenshot(SCRATCH / "gemini_screenshot_after_upload.jpg")
            else:
                print("  ❌ не нашли input через DOM.querySelector")
        else:
            print("\n[UPLOAD] пропуск: нет file input или тестового фото")

        # 8. Если есть contenteditable — впечатаем промпт
        ce = await c.dom_querySelectorAll('[contenteditable="true"]')
        if ce:
            print(f"\n[TYPE] Нашли {len(ce)} contenteditable — пробуем впечатать")
            prompt = "Опиши это фото в одном предложении"
            # Фокус + Input.insertText
            await c.eval(f"""
                (() => {{
                    const el = document.querySelector('[contenteditable="true"]');
                    if (el) el.focus();
                    return !!el;
                }})()
            """)
            try:
                await c.send("Input.insertText", {"text": prompt}, timeout=20.0)
                print(f"  ✅ Впечатали: {prompt!r}")
            except Exception as e:
                print(f"  ❌ Input.insertText: {e}")
            await asyncio.sleep(1)
            await c.screenshot(SCRATCH / "gemini_screenshot_typed.jpg")

            # Ищем кнопку Send
            print("\n[SEND] Ищем кнопку отправки")
            await c.eval("""
                (() => {
                    const btns = Array.from(document.querySelectorAll('button, [role=button]'));
                    window.__sendCandidates = btns.filter(b => {
                        const al = (b.getAttribute('aria-label') || '').toLowerCase();
                        const t = (b.innerText || '').toLowerCase();
                        return al.includes('send') || al.includes('submit') ||
                               al.includes('отправ') || t.includes('send') ||
                               t === '↑' || t === '➤' || b.querySelector('mat-icon');
                    }).map(b => ({
                        tag: b.tagName,
                        aria: b.getAttribute('aria-label'),
                        text: (b.innerText || '').substring(0, 30),
                        disabled: b.disabled,
                        cls: (b.className || '').substring(0, 80),
                    }));
                    return window.__sendCandidates.length;
                })()
            """)
            sends = await c.eval("window.__sendCandidates || []")
            print(f"  Кандидаты в send: {sends}")

        # 9. Сводный отчёт
        report = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "profile": env_id,
            "debug_port": debug_port,
            "tabs": [{"type": t.get("type"), "url": t.get("url"),
                      "title": t.get("title")} for t in tabs],
            "final_state": await c.get_state(),
            "file_inputs": await c.dom_querySelectorAll('input[type=file]'),
            "contenteditables": await c.dom_querySelectorAll('[contenteditable="true"]'),
            "textareas": await c.dom_querySelectorAll('textarea'),
            "test_image": str(test_img) if test_img else None,
            "events_tail": c._events[-15:],
        }
        (SCRATCH / "gemini_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n=== DONE === отчёт → {SCRATCH / 'gemini_report.json'}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        print(f"\n!!! FATAL: {e}")
        traceback.print_exc()
        sys.exit(1)
