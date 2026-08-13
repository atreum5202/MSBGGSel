"""
scratch/gemini_explore3.py — клик по +, загрузка файла, отправка промпта.

Шаги:
  1. Старт профиля, переход на Gemini
  2. Дождаться input area
  3. Найти и кликнуть кнопку "+" (upload)
  4. Снять DOM — что появилось? Какие пункты меню? Какой file input?
  5. Если есть hidden input[type=file] — загрузить
  6. Если есть кнопка "Upload file" — кликнуть её, дождаться input
  7. Впечатать промпт в contenteditable
  8. Найти и кликнуть Send (или Enter)
  9. Снимать состояние каждые 5с до 60с — ждать результат
 10. Если появилось изображение — скачать и сохранить
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

MORELOGIN_API_BASE = "http://127.0.0.1:40000"
MORELOGIN_API_ID = "1716740459457616"
MORELOGIN_API_KEY = "8afb02927e724b6caadc6363f13f3c61"

SCRATCH = pathlib.Path("scratch")
SCRATCH.mkdir(exist_ok=True)
LOG_PATH = SCRATCH / "gemini_explore3.log"

GEMINI_URL = "https://gemini.google.com/app"
PROMPT = "Опиши это изображение одним коротким предложением на русском"

def find_test_image() -> Optional[pathlib.Path]:
    for d in (pathlib.Path(os.path.expanduser("~/Desktop")),
              pathlib.Path("C:/Users/Atreum/Desktop")):
        if not d.exists(): continue
        for ext in (".jpg", ".jpeg", ".png"):
            cand = list(d.glob(f"*{ext}"))
            if cand: return cand[0]
    return None


class Tee:
    def __init__(self, path, stream):
        self._orig = stream
        self.f = open(path, "w", encoding="utf-8")
    def write(self, s):
        try: self._orig.write(s)
        except Exception: pass
        try: self.f.write(s); self.f.flush()
        except Exception: pass
    def flush(self):
        try: self._orig.flush()
        except Exception: pass
        try: self.f.flush()
        except Exception: pass


async def get_first_profile() -> str:
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post(f"{MORELOGIN_API_BASE}/api/env/page",
            json={"pageNo": 1, "pageSize": 5},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY})
        r.raise_for_status()
        data = r.json()
        d = data.get("data") or {}
        items = d.get("list") or d.get("dataList") or d.get("records") or []
        if isinstance(d, list): items = d
        if not items: raise RuntimeError(f"Нет профилей: {data!r}")
        prof = items[0]
        env_id = str(prof.get("envId") or prof.get("id") or prof.get("seq"))
        print(f"  Профиль: envId={env_id}, name={prof.get('envName') or prof.get('name')!r}")
        return env_id


async def start_profile(env_id: str) -> str:
    async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
        r = await c.post(f"{MORELOGIN_API_BASE}/api/env/start",
            json={"envId": env_id, "isHeadless": False, "cdpEvasion": True},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY})
        r.raise_for_status()
        dp = str(r.json()["data"]["debugPort"])
        print(f"  debugPort: {dp}")
        return dp


async def get_page_tab(dp: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        for path in ("/json/list", "/json"):
            r = await c.get(f"http://127.0.0.1:{dp}{path}")
            r.raise_for_status()
            tabs = r.json()
            page = [t for t in tabs if t.get("type") == "page" and t.get("url", "").startswith("http")]
            if page: return page[0]
    raise RuntimeError("Нет page-вкладки")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events = []
    async def send(self, method, params=None, timeout=30.0):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0: raise TimeoutError(f"CDP {method} timeout")
            raw = await asyncio.wait_for(self.ws.recv(), timeout=rem)
            try: msg = json.loads(raw)
            except Exception: continue
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                if len(self.events) > 50: self.events = self.events[-50:]
    async def eval(self, expr, await_promise=False, timeout=15.0):
        r = await self.send("Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
            timeout=timeout)
        if "exceptionDetails" in r:
            return {"__error__": r["exceptionDetails"]}
        return r.get("result", {}).get("value")
    async def screenshot(self, path, full=False):
        params = {"format": "jpeg", "quality": 80}
        if full: params["captureBeyondViewport"] = True
        r = await self.send("Page.captureScreenshot", params, 15.0)
        data = base64.b64decode(r["data"])
        path.write_bytes(data)
        return len(data)
    async def get_state(self):
        return await self.eval("""({
            url: location.href,
            title: document.title,
            hasContentEditable: !!document.querySelector('[contenteditable="true"]'),
            hasFileInput: !!document.querySelector('input[type=file]'),
            fileInputCount: document.querySelectorAll('input[type=file]').length,
            buttonCount: document.querySelectorAll('button').length,
            imgCount: document.querySelectorAll('img').length,
            largeImgCount: Array.from(document.querySelectorAll('img')).filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 256 && r.height > 256;
            }).length,
        })""") or {}
    async def dom_describe(self, label: str):
        """Снять подробный снимок DOM: file inputs, contenteditable, кнопки."""
        snap = await self.eval(r"""(() => {
            const desc = (el) => ({
                tag: el.tagName,
                id: el.id || null,
                cls: (typeof el.className === 'string' ? el.className : '').substring(0, 100),
                aria: el.getAttribute('aria-label'),
                title: el.title || null,
                text: (el.innerText || el.textContent || '').trim().substring(0, 60) || null,
                rect: (() => { const r = el.getBoundingClientRect();
                    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
                })(),
                visible: (() => { const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                })(),
            });
            return {
                fileInputs: Array.from(document.querySelectorAll('input[type=file]')).map(desc),
                contentEditables: Array.from(document.querySelectorAll('[contenteditable="true"]')).map(desc),
                textareas: Array.from(document.querySelectorAll('textarea')).map(desc),
                buttons: Array.from(document.querySelectorAll('button')).map(desc).filter(b => b.visible),
                menuItems: Array.from(document.querySelectorAll('[role=menuitem], [role=option]')).map(desc),
            };
        })()""")
        print(f"\n[DOM] === {label} ===")
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))
        return snap


async def main():
    out = Tee(LOG_PATH, sys.stdout)
    err = Tee(LOG_PATH, sys.stderr)
    sys.stdout = out
    sys.stderr = err
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} START ===")

    test_img = find_test_image()
    print(f"  Тестовое фото: {test_img}")

    env_id = await get_first_profile()
    dp = await start_profile(env_id)
    tab = await get_page_tab(dp)
    print(f"  Вкладка: url={tab.get('url')!r}")
    ws_url = tab["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024,
                                  ping_interval=20, ping_timeout=20) as ws:
        c = CDP(ws)
        await c.send("Page.enable")
        await c.send("DOM.enable")
        await c.send("Runtime.enable")
        await c.send("Network.enable")

        # ── 1. Навигация ─────────────────────────────────────────────────
        print(f"\n[NAV] → {GEMINI_URL}")
        await c.send("Page.navigate", {"url": GEMINI_URL})
        # Ждём появления input area
        for i in range(30):
            await asyncio.sleep(1)
            st = await c.get_state()
            if st.get("hasContentEditable"):
                print(f"  Input area появилась через {i+1}с")
                break
        else:
            print("  ⚠ Input area не появилась за 30с")

        await c.screenshot(SCRATCH / "gemini_3_step1_loaded.jpg")

        # ── 2. Клик по кнопке "+" (upload) ──────────────────────────────
        print("\n[CLICK +] Ищем кнопку загрузки")
        # Кнопка "+" — это иконка в начале input area
        # Ищем через разные стратегии
        plus_clicked = await c.eval(r"""(() => {
            // Стратегия 1: aria-label содержит upload/files/images
            const labels = ['upload', 'загруз', 'прикрепить', 'attach', 'file', 'image', 'media'];
            const buttons = Array.from(document.querySelectorAll('button, [role=button]'));
            for (const b of buttons) {
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const title = (b.title || '').toLowerCase();
                if (labels.some(l => aria.includes(l) || title.includes(l))) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        b.click();
                        return {ok: true, by: 'aria', label: b.getAttribute('aria-label') || b.title, rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
                    }
                }
            }
            // Стратегия 2: mat-icon с плюсом / upload
            const icons = Array.from(document.querySelectorAll('mat-icon, .material-icons, [class*=icon]'));
            for (const i of icons) {
                const t = (i.textContent || i.getAttribute('font-icon') || '').toLowerCase();
                if (['add', 'plus', 'upload_file', 'attach_file', 'image', 'add_photo'].some(k => t.includes(k))) {
                    const btn = i.closest('button') || i.parentElement.closest('button');
                    if (btn) {
                        const r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {
                            btn.click();
                            return {ok: true, by: 'icon', icon: t, rect: {x: r.x, y: r.y, w: r.width, h: r.height}};
                        }
                    }
                }
            }
            return {ok: false, reason: 'no_plus_button_found', visibleButtons: buttons.filter(b => {
                const r = b.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }).slice(0, 15).map(b => ({
                aria: b.getAttribute('aria-label'),
                text: (b.innerText || '').substring(0, 30),
                rect: (() => { const r = b.getBoundingClientRect();
                    return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
                })(),
            }))};
        })()""")
        print(f"  Результат: {json.dumps(plus_clicked, ensure_ascii=False, default=str)}")
        await asyncio.sleep(2)
        await c.screenshot(SCRATCH / "gemini_3_step2_after_plus.jpg")
        await c.dom_describe("после клика по +")

        # ── 3. Если появилось меню — кликнем "Upload file" / "Загрузить файл" ──
        st = await c.get_state()
        if not st.get("hasFileInput"):
            print("\n[CLICK Upload] Ищем пункт Upload в меню")
            up_clicked = await c.eval(r"""(() => {
                const labels = ['upload file', 'upload', 'загрузить', 'файл', 'с компьютера', 'компьютер', 'from computer', 'files', 'from this device'];
                const candidates = Array.from(document.querySelectorAll('button, [role=menuitem], [role=option], a, div[tabindex]'));
                for (const el of candidates) {
                    const txt = (el.innerText || el.textContent || '').toLowerCase().trim();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (labels.some(l => txt.includes(l) || aria.includes(l))) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.width < 400) {
                            el.click();
                            return {ok: true, text: (el.innerText || '').substring(0, 60)};
                        }
                    }
                }
                return {ok: false};
            })()""")
            print(f"  Результат: {json.dumps(up_clicked, ensure_ascii=False)}")
            await asyncio.sleep(2)
            await c.screenshot(SCRATCH / "gemini_3_step3_after_upload.jpg")
            await c.dom_describe("после клика Upload")

        # ── 4. Загрузим файл через DOM.setFileInputFiles ──────────────────
        st = await c.get_state()
        if st.get("hasFileInput") and test_img:
            print(f"\n[UPLOAD] Загружаем {test_img}")
            # Ищем nodeId
            doc = await c.send("DOM.getDocument")
            root_id = (doc.get("root") or {}).get("nodeId")
            for sel in ('input[type="file"][accept*="image"]',
                        'input[type="file"]'):
                r = await c.send("DOM.querySelector", {"nodeId": root_id, "selector": sel})
                nid = r.get("nodeId")
                if nid and nid != 0:
                    print(f"  [DOM] {sel} → nodeId={nid}")
                    try:
                        await c.send("DOM.setFileInputFiles",
                                     {"nodeId": nid, "files": [str(test_img)]}, 30.0)
                        print("  ✅ файл загружен через setFileInputFiles")
                        break
                    except Exception as e:
                        print(f"  ⚠ setFileInputFiles: {e}")
            await asyncio.sleep(3)
            await c.screenshot(SCRATCH / "gemini_3_step4_uploaded.jpg")
            await c.dom_describe("после загрузки файла")
        else:
            print(f"\n[UPLOAD] пропуск: hasFileInput={st.get('hasFileInput')}, test_img={test_img}")

        # ── 5. Впечатаем промпт ──────────────────────────────────────────
        print(f"\n[TYPE] Промпт: {PROMPT!r}")
        focused = await c.eval(r"""(() => {
            const el = document.querySelector('.ql-editor[contenteditable="true"]')
                    || document.querySelector('[contenteditable="true"]');
            if (!el) return false;
            el.focus();
            return true;
        })()""")
        print(f"  Focused: {focused}")
        if focused:
            # Очистим (на случай если там уже что-то есть)
            await c.eval(r"""(() => {
                const el = document.querySelector('.ql-editor[contenteditable="true"]');
                if (el) {
                    el.innerHTML = '';
                    el.dispatchEvent(new InputEvent('input', {bubbles: true}));
                }
            })()""")
            await c.send("Input.insertText", {"text": PROMPT}, 20.0)
            print(f"  ✅ впечатали {len(PROMPT)} символов")
        await asyncio.sleep(1)
        await c.screenshot(SCRATCH / "gemini_3_step5_typed.jpg")
        await c.dom_describe("после ввода промпта")

        # ── 6. Найти и кликнуть Send ─────────────────────────────────────
        print("\n[SEND] Ищем кнопку отправки")
        # После ввода текста Send-кнопка появляется. Ищем по разным признакам.
        send_info = await c.eval(r"""(() => {
            // Ищем все кнопки с Send
            const buttons = Array.from(document.querySelectorAll('button'));
            const candidates = [];
            for (const b of buttons) {
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const title = (b.title || '').toLowerCase();
                const cls = (b.className || '').toLowerCase();
                const text = (b.innerText || '').toLowerCase().trim();
                // Разные варианты: send, отправить, submit
                if (aria.includes('send') || title.includes('send') ||
                    aria.includes('отправ') || title.includes('отправ') ||
                    text === 'send' || text === '↑' || text === '➤' || text === 'submit' ||
                    cls.includes('send-button') || cls.includes('sendbutton') ||
                    b.querySelector('mat-icon')) {
                    const r = b.getBoundingClientRect();
                    candidates.push({
                        aria: b.getAttribute('aria-label'),
                        title: b.title,
                        text: (b.innerText || '').substring(0, 30),
                        cls: (b.className || '').substring(0, 80),
                        hasMatIcon: !!b.querySelector('mat-icon'),
                        matIconText: b.querySelector('mat-icon') ? (b.querySelector('mat-icon').textContent || '') : null,
                        disabled: b.disabled,
                        visible: r.width > 0 && r.height > 0,
                        rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                    });
                }
            }
            return candidates;
        })()""")
        print(f"  Кандидаты: {json.dumps(send_info, ensure_ascii=False, indent=2)}")

        # Кликаем первого видимого
        clicked = False
        if isinstance(send_info, list):
            for cand in send_info:
                if cand.get("visible") and not cand.get("disabled"):
                    # Кликаем через Input.dispatchMouseEvent (точнее чем click())
                    print(f"  → кликаем {cand.get('aria') or cand.get('text') or cand.get('cls')!r}")
                    await c.eval(r"""(() => {
                        const buttons = Array.from(document.querySelectorAll('button'));
                        for (const b of buttons) {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const title = (b.title || '').toLowerCase();
                            const cls = (b.className || '').toLowerCase();
                            const text = (b.innerText || '').toLowerCase().trim();
                            if (aria.includes('send') || title.includes('send') ||
                                aria.includes('отправ') || title.includes('отправ') ||
                                cls.includes('send-button')) {
                                b.click();
                                return 'clicked';
                            }
                        }
                        return 'not_found';
                    })()""")
                    clicked = True
                    break

        if not clicked:
            # Фоллбэк — Enter
            print("  → Send-кнопка не нашлась, жмём Enter")
            for ev in ("keyDown", "keyUp"):
                await c.send("Input.dispatchKeyEvent", {
                    "type": ev, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                }, 5.0)

        # ── 7. Ждём результат (60с максимум, каждые 5с снимок) ───────────
        print("\n[WAIT] Ждём результат до 60с")
        for i in range(12):
            await asyncio.sleep(5)
            st = await c.get_state()
            print(f"  T+{(i+1)*5:>3}с: state={st}")
            await c.screenshot(SCRATCH / f"gemini_3_wait_{(i+1)*5:03d}.jpg")
            if st.get("largeImgCount", 0) > 0:
                print(f"  ✅ Появилось большое изображение на T+{(i+1)*5}с")
                # Подождём ещё 3с на подстраховку
                await asyncio.sleep(3)
                break

        # ── 8. Достаём изображение ────────────────────────────────────────
        print("\n[EXTRACT] Ищем большую картинку для скачивания")
        extracted = await c.eval(r"""(async () => {
            const cands = Array.from(document.querySelectorAll('img'))
                .filter(img => {
                    const r = img.getBoundingClientRect();
                    return r.width > 256 && r.height > 256 && img.src && !img.src.includes('favicon');
                });
            if (!cands.length) return {error: 'no_large_image'};
            const img = cands[cands.length - 1];
            if (!img.complete || !img.naturalWidth) return {error: 'not_loaded'};
            const src = img.src;
            try {
                if (src.startsWith('data:')) {
                    return {type: 'dataUrl', value: src, w: img.naturalWidth, h: img.naturalHeight};
                }
                if (src.startsWith('blob:')) {
                    const resp = await fetch(src);
                    const blob = await resp.blob();
                    const dataUrl = await new Promise((resolve, reject) => {
                        const r = new FileReader();
                        r.onloadend = () => resolve(r.result);
                        r.onerror = reject;
                        r.readAsDataURL(blob);
                    });
                    return {type: 'dataUrl', value: dataUrl, w: img.naturalWidth, h: img.naturalHeight};
                }
                return {type: 'httpUrl', value: src, w: img.naturalWidth, h: img.naturalHeight};
            } catch (e) {
                return {error: 'fetch_failed: ' + (e.message || e)};
            }
        })()""", await_promise=True, timeout=15.0)
        print(f"  Результат: {json.dumps(extracted, ensure_ascii=False, default=str)[:500]}")

        if isinstance(extracted, dict) and extracted.get("value"):
            out_path = SCRATCH / "gemini_3_result.jpg"
            v = extracted["value"]
            if extracted["type"] == "dataUrl":
                b64 = v.split(",", 1)[1] if "," in v else v
                out_path.write_bytes(base64.b64decode(b64))
            elif extracted["type"] == "httpUrl":
                async with httpx.AsyncClient(timeout=60, trust_env=False) as cl:
                    r = await cl.get(v)
                    out_path.write_bytes(r.content)
            print(f"  ✅ сохранено: {out_path} ({out_path.stat().st_size} bytes)")

        # Финальный DOM
        await c.dom_describe("ФИНАЛ")
        await c.screenshot(SCRATCH / "gemini_3_final.jpg")

        print(f"\n=== DONE ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        print(f"\n!!! FATAL: {e}")
        traceback.print_exc()
        sys.exit(1)
