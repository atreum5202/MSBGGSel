"""
parser/morelogin_gemini.py
==========================
Автоматизация gemini.google.com через CDP-профиль MSB (CloakBrowser).

Пайплайн (функция `restyle_image_via_browser`):
  1. MsbClient.start_profile()     → запускает профиль, получает debugPort
  2. CDP Page.navigate              → https://gemini.google.com/app
  3. Ждём загрузку SPA (4-7с), появления input area
  4. Скачиваем image_url (httpx)   → tempfile
  5. Кликаем "Загрузка и инструменты" (mat-icon fonticon="plus")
  6. В меню выбираем "Загрузить файл" → создаётся <input type=file>
  7. DOM.setFileInputFiles           → загружаем файл
  8. Вводим промпт в div.ql-editor, отправляем
  9. Ждём результат: сначала CDP-скриншот, fallback JS-извлечение blob
 10. Сохраняем в save_path

Требования: websockets>=12, httpx>=0.27
Движок: CloakBrowser через MSB (engine="cloakbrowser") — 66 C++-патчей
         против детектирования (canvas, WebGL, fonts, GPU, WebRTC и др.)
launch_mode: "background" (скрыто) | "visible" (окно на экране)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .msb_client import MsbClient
from . import cdp_cookies

# Используем стандартный RuntimeError для всех ошибок MSB/Gemini
MoreLoginError = RuntimeError  # backward-compat alias, не использовать в новом коде

logger = logging.getLogger("morelogin_gemini")


# ── Конфиг ────────────────────────────────────────────────────────────────

GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_PAGE_LOAD_DELAY = 4.0      # секунд после navigate (SPA тяжёлый)
DEFAULT_NAV_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 30.0
TYPING_TIMEOUT = 30.0
EXTRACT_JS_TIMEOUT = 55   # на fetch blob → base64 крупного изображения нужно время
SCREENSHOT_TIMEOUT  = 20.0 # CDP Page.captureScreenshot
POLL_INTERVAL = 2.0
MIN_IMAGE_SIZE = 256               # мин. размер сгенерённой картинки
TEXT_CHUNK = 4000                  # Input.insertText чанками

# Селекторы (зафиксировано по результатам разведки)
SEL_EDITOR = '.ql-editor[contenteditable="true"]'
SEL_UPLOAD_BTN = 'button[aria-label="Загрузка и инструменты"]'
# Подстроки для поиска пункта меню "Загрузить файл"
MENU_ITEM_KEYWORDS = (
    "upload file", "upload from", "с компьютер", "загрузить файл",
    "загрузить с", "from computer", "from this device", "with this device",
    "files", "загрузк", "file", "файл", "device", "устройств",
    "photo", "фото", "image", "изображение", "media",
)


# ── 1. Скачивание image_url → temp файл ───────────────────────────────────


async def _download_image_to_tempfile(image_url: str) -> str:
    if not image_url:
        raise RuntimeError("image_url пустой")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        raise RuntimeError(f"Не удалось скачать {image_url}: {e}") from e
    if not data:
        raise RuntimeError(f"Скачали 0 байт с {image_url}")

    lower = image_url.lower().split("?", 1)[0]
    if lower.endswith(".png"):
        suffix = ".png"
    elif lower.endswith(".webp"):
        suffix = ".webp"
    elif lower.endswith(".gif"):
        suffix = ".gif"
    else:
        suffix = ".jpg"

    fd, path = tempfile.mkstemp(prefix="gemini_src_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            os.unlink(path)
        except Exception:
            pass
        raise
    logger.info("gemini: скачали %d байт → %s", len(data), path)
    return path


# ── 2. Тонкая CDP-обёртка ────────────────────────────────────────────────


async def _cdp_call(debug_port, method, params=None, timeout=15.0):
    ws_url = cdp_cookies.find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        raise RuntimeError(
            f"cdp: debug_port={debug_port} — page не найдена (/json/list пуст)"
        )
    async with cdp_cookies._CDPSession(ws_url, timeout=timeout) as s:
        return await s.send(method, params=params or {}, timeout=timeout)


# ── 3. Загрузка файла: клик → menu → input[type=file] → setFileInputFiles ─


async def _ensure_input_ready(debug_port, timeout: float = 15.0) -> bool:
    """Ждём появления div.ql-editor[contenteditable=true]."""
    js = f"""
    (async () => {{
        const deadline = Date.now() + {int(timeout * 1000)};
        while (Date.now() < deadline) {{
            const el = document.querySelector({json.dumps(SEL_EDITOR)});
            if (el) return true;
            await new Promise(r => setTimeout(r, 250));
        }}
        return false;
    }})()
    """
    try:
        return bool(await cdp_cookies.eval_via_cdp(
            debug_port, js, await_promise=True, timeout=timeout + 2.0,
        ))
    except Exception:
        return False


async def _focus_editor(debug_port) -> bool:
    """Фокус на div.ql-editor (без него upload-кнопка может быть скрыта)."""
    js = f"""
    (() => {{
        const el = document.querySelector({json.dumps(SEL_EDITOR)});
        if (!el) return false;
        el.focus();
        try {{ el.click(); }} catch (e) {{}}
        return true;
    }})()
    """
    try:
        return bool(await cdp_cookies.eval_via_cdp(debug_port, js, timeout=5.0))
    except Exception:
        return False


async def _open_upload_menu(debug_port) -> bool:
    """Клик по кнопке 'Загрузка и инструменты' → открыть CDK menu.
    Пробуем несколько стратегий: JS click, dispatchEvent, CDP coordinate click.
    """
    # --- Стратегия 1: получаем координаты кнопки и кликаем через CDP Input ---
    get_rect_js = f"""
    (() => {{
        const btn = document.querySelector({json.dumps(SEL_UPLOAD_BTN)});
        if (!btn) return null;
        btn.scrollIntoView({{behavior:'instant', block:'center'}});
        const r = btn.getBoundingClientRect();
        return {{x: r.left + r.width/2, y: r.top + r.height/2,
                w: r.width, h: r.height}};
    }})()
    """
    try:
        rect = await cdp_cookies.eval_via_cdp(debug_port, get_rect_js, timeout=5.0)
        if rect and isinstance(rect, dict) and rect.get("w", 0) > 0:
            x, y = rect["x"], rect["y"]
            ws_url = cdp_cookies.find_page_ws_url(debug_port, prefer_type="page")
            if ws_url:
                async with cdp_cookies._CDPSession(ws_url, timeout=10.0) as s:
                    for ev in ("mousePressed", "mouseReleased"):
                        await s.send("Input.dispatchMouseEvent", {
                            "type": ev, "x": x, "y": y,
                            "button": "left", "clickCount": 1,
                        }, timeout=5.0)
                    await asyncio.sleep(0.3)
                logger.info("gemini: _open_upload_menu: CDP click @ (%.0f, %.0f)", x, y)
                return True
    except Exception as e:
        logger.warning("gemini: _open_upload_menu CDP click: %s", e)

    # --- Стратегия 2: JS dispatchEvent (MouseEvent) ---
    js_dispatch = f"""
    (() => {{
        const btn = document.querySelector({json.dumps(SEL_UPLOAD_BTN)});
        if (!btn) return false;
        btn.scrollIntoView({{behavior:'instant', block:'center'}});
        ['mousedown','mouseup','click'].forEach(ev => {{
            btn.dispatchEvent(new MouseEvent(ev, {{bubbles:true, cancelable:true,
                view:window, buttons:1}}));
        }});
        return true;
    }})()
    """
    try:
        res = await cdp_cookies.eval_via_cdp(debug_port, js_dispatch, timeout=5.0)
        if res is True:
            logger.info("gemini: _open_upload_menu: JS dispatchEvent OK")
            return True
    except Exception as e:
        logger.warning("gemini: _open_upload_menu JS dispatch: %s", e)

    logger.warning("gemini: _open_upload_menu: все стратегии не сработали")
    return False


async def _click_upload_menu_item(debug_port, timeout: float = 8.0) -> bool:
    """В открытом CDK-меню кликнуть пункт 'Загрузить файл' / 'Upload file'."""
    js = f"""
    (async () => {{
        const keywords = {json.dumps(list(MENU_ITEM_KEYWORDS))};
        const isVisible = (el) => {{
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0
                && s.visibility !== 'hidden' && s.display !== 'none';
        }};
        const matches = (el) => {{
            const t = (el.innerText || '').toLowerCase();
            const a = (el.getAttribute('aria-label') || '').toLowerCase();
            return keywords.some(k => t.includes(k) || a.includes(k));
        }};
        const deadline = Date.now() + {int(timeout * 1000)};
        // CDK overlay появляется с задержкой
        while (Date.now() < deadline) {{
            const overlayItems = Array.from(
                document.querySelectorAll(
                    '.cdk-overlay-pane button, .mat-mdc-menu-panel button, '
                    + '[role=menuitem], [role=option], '
                    + '.mat-bottom-sheet-container button, '
                    + 'mat-bottom-sheet-container button, '
                    + '.tool-list button, .tool-list [role=menuitem], '
                    + 'gem-tool-list button'
                )
            );
            for (const el of overlayItems) {{
                if (isVisible(el) && matches(el)) {{
                    el.click();
                    return 'clicked: ' + (el.innerText || '').substring(0, 50);
                }}
            }}
            await new Promise(r => setTimeout(r, 150));
        }}
        return 'not_found';
    }})()
    """
    try:
        res = await cdp_cookies.eval_via_cdp(
            debug_port, js, await_promise=True, timeout=timeout + 2.0,
        )
        if isinstance(res, str) and res.startswith("clicked"):
            logger.info("gemini: menu item: %s", res)
            return True
        logger.warning("gemini: menu item не найден: %r", res)
        return False
    except Exception as e:
        logger.warning("gemini: _click_upload_menu_item: %s", e)
        return False


async def _wait_for_file_input(debug_port, timeout: float = 5.0) -> bool:
    """После клика по 'Загрузить файл' в DOM должен появиться <input type=file>."""
    js = f"""
    (async () => {{
        const deadline = Date.now() + {int(timeout * 1000)};
        while (Date.now() < deadline) {{
            const fi = document.querySelector('input[type=file]');
            if (fi) return true;
            await new Promise(r => setTimeout(r, 150));
        }}
        return false;
    }})()
    """
    try:
        return bool(await cdp_cookies.eval_via_cdp(
            debug_port, js, await_promise=True, timeout=timeout + 2.0,
        ))
    except Exception:
        return False


async def _set_file_input(debug_port, file_path: str) -> None:
    """Найти <input type=file> и загрузить в него файл."""
    doc = await _cdp_call(debug_port, "DOM.getDocument", timeout=15.0)
    root_id = (doc.get("root") or {}).get("nodeId")
    if not root_id:
        raise RuntimeError("DOM.getDocument: нет root nodeId")

    nid = None
    for sel in (
        'input[type="file"][accept*="image"]',
        'input[type="file"][accept*="application"]',
        'input[type="file"]',
    ):
        try:
            r = await _cdp_call(
                debug_port, "DOM.querySelector",
                {"nodeId": root_id, "selector": sel}, 10.0,
            )
            v = r.get("nodeId")
            if v and v != 0:
                nid = v
                logger.debug("gemini: нашли %s, nodeId=%d", sel, nid)
                break
        except Exception as e:
            logger.debug("gemini: querySelector %s — %s", sel, e)
    if not nid:
        raise RuntimeError("Не нашли <input type=file> после открытия меню")

    await _cdp_call(
        debug_port, "DOM.setFileInputFiles",
        {"nodeId": nid, "files": [file_path]},
        timeout=UPLOAD_TIMEOUT,
    )
    logger.info("gemini: файл %s загружен", os.path.basename(file_path))


async def _wait_for_image_preview(debug_port, timeout: float = 8.0) -> bool:
    """Превью загруженного фото в чате (img blob:/data: внутри input area)."""
    js = f"""
    (async () => {{
        const deadline = Date.now() + {int(timeout * 1000)};
        while (Date.now() < deadline) {{
            // Превью загруженного фото — img с blob:/data: внутри message-area
            const prev = document.querySelector(
                'img[src^="blob:"], img[src^="data:image"]'
            );
            if (prev && prev.getBoundingClientRect().width > 32) return true;
            await new Promise(r => setTimeout(r, 250));
        }}
        return false;
    }})()
    """
    try:
        return bool(await cdp_cookies.eval_via_cdp(
            debug_port, js, await_promise=True, timeout=timeout + 2.0,
        ))
    except Exception:
        return False


async def _count_blob_images(debug_port) -> int:
    """Считаем blob/data-img видимые на странице (baseline перед вставкой файла)."""
    js = """
    (() => {
        return Array.from(document.querySelectorAll('img[src^="blob:"], img[src^="data:image"]'))
            .filter(img => img.getBoundingClientRect().width > 10).length;
    })()
    """
    try:
        result = await cdp_cookies.eval_via_cdp(debug_port, js, timeout=5.0)
        return int(result) if isinstance(result, int) else 0
    except Exception:
        return 0


async def _wait_for_new_attachment(debug_port, baseline: int = 0, timeout: float = 10.0) -> bool:
    """
    Ждём появления нового blob/data-img сверх baseline-количества.
    Baseline = количество blob-img ДО попытки вставки файла.
    Это надёжнее, чем искать любой img — не путаем с иконками UI.
    """
    js = f"""
    (async () => {{
        const deadline = Date.now() + {int(timeout * 1000)};
        const baseline = {baseline};
        while (Date.now() < deadline) {{
            const count = Array.from(
                document.querySelectorAll('img[src^="blob:"], img[src^="data:image"]')
            ).filter(img => img.getBoundingClientRect().width > 10).length;
            if (count > baseline) return count;
            await new Promise(r => setTimeout(r, 300));
        }}
        return 0;
    }})()
    """
    try:
        result = await cdp_cookies.eval_via_cdp(
            debug_port, js, await_promise=True, timeout=timeout + 2.0,
        )
        return bool(result and int(result) > 0)
    except Exception:
        return False


async def upload_file(debug_port, file_path: str) -> None:
    """
    Прикрепить фото к сообщению Gemini.

    Стратегии (в порядке попытки):
    1. Clipboard paste через synthetic ClipboardEvent (быстро, без меню).
    2. Fallback: меню "Загрузка и инструменты" → DOM.setFileInputFiles (надёжно).

    Обе стратегии проверяются по факту: ждём появления нового blob-img
    в DOM сверх baseline (не доверяем "ok" от JS dispatch).

    Raises RuntimeError если обе стратегии не сработали.
    """
    if not os.path.isfile(file_path):
        raise RuntimeError(f"Файл не найден: {file_path}")

    # Baseline: сколько blob-img было ДО наших действий
    baseline = await _count_blob_images(debug_port)
    logger.info("gemini: upload_file baseline blob-imgs=%d", baseline)

    # ── Стратегия 1: Clipboard paste ──────────────────────────────────────
    with open(file_path, "rb") as _f:
        img_b64 = base64.b64encode(_f.read()).decode()

    ext = file_path.lower().rsplit(".", 1)[-1]
    mime = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")

    ws_url = cdp_cookies.find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        raise RuntimeError(f"upload_file: page ws_url не найден для debug_port={debug_port}")

    try:
        paste_js = f"""
        (async () => {{
            const SEL  = {json.dumps(SEL_EDITOR)};
            const b64  = {json.dumps(img_b64)};
            const mime = {json.dumps(mime)};
            const editor = document.querySelector(SEL);
            if (!editor) return 'no_editor';
            const bin  = atob(b64);
            const arr  = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
            const blob = new Blob([arr], {{type: mime}});
            const file = new File([blob], 'image.jpg', {{type: mime}});
            const dt   = new DataTransfer();
            dt.items.add(file);
            editor.focus();
            const ev = new ClipboardEvent('paste', {{
                bubbles: true, cancelable: true, clipboardData: dt
            }});
            editor.dispatchEvent(ev);
            return 'ok';
        }})()
        """
        async with cdp_cookies._CDPSession(ws_url, timeout=30.0) as s:
            res = await s.send("Runtime.evaluate", {
                "expression":    paste_js,
                "awaitPromise":  True,
                "returnByValue": True,
                "userGesture":   True,
            }, timeout=15.0)
        val = (res.get("result") or {}).get("value")
        if val == "ok":
            logger.info("gemini: clipboard paste dispatched (%s)", mime)
            # Проверяем по ФАКТУ — ждём новый blob-img
            if await _wait_for_new_attachment(debug_port, baseline=baseline, timeout=8.0):
                logger.info("gemini: файл прикреплён через clipboard paste ✓")
                return
            logger.warning("gemini: clipboard paste dispatched, но превью не появилось за 8с")
        else:
            logger.warning("gemini: clipboard paste: val=%r (редактор не найден?)", val)
    except Exception as e:
        logger.warning("gemini: clipboard paste exception: %s", e)

    # ── Стратегия 2: меню → DOM.setFileInputFiles ─────────────────────────
    logger.info("gemini: пробую fallback через file-input меню…")

    if not await _open_upload_menu(debug_port):
        raise RuntimeError("upload_file: не удалось открыть меню 'Загрузка и инструменты'")

    await asyncio.sleep(0.5)

    if not await _click_upload_menu_item(debug_port, timeout=8.0):
        raise RuntimeError("upload_file: пункт меню 'Загрузить файл' не найден")

    if not await _wait_for_file_input(debug_port, timeout=5.0):
        raise RuntimeError("upload_file: <input type=file> не появился после клика меню")

    await _set_file_input(debug_port, file_path)
    await asyncio.sleep(1.0)  # даём Gemini время обработать загрузку

    if await _wait_for_new_attachment(debug_port, baseline=baseline, timeout=10.0):
        logger.info("gemini: файл прикреплён через file-input fallback ✓")
        return

    raise RuntimeError(
        "upload_file: фото не прикреплено к сообщению — "
        "обе стратегии (clipboard paste + file-input меню) не сработали. "
        "Возможные причины: Gemini изменил UI, профиль не в CloakBrowser-режиме, "
        "страница не загружена полностью."
    )


# ── 4. Ввод промпта + отправка ────────────────────────────────────────────


async def _dismiss_welcome(debug_port) -> None:
    """Закрыть welcome-экран / согласие при первом визите."""
    js = """
    (() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        for (const b of buttons) {
            const t = (b.innerText || b.textContent || '').trim().toLowerCase();
            if (!t) continue;
            if (['i agree','agree','accept','accept all','got it','continue',
                 'i accept','принимаю','согласен','продолжить','принять','ок']
                .includes(t)) {
                try { b.click(); return 'clicked: ' + t; } catch (e) {}
            }
        }
        return 'none';
    })()
    """
    try:
        res = await cdp_cookies.eval_via_cdp(debug_port, js, timeout=5.0)
        if res and "clicked" in str(res):
            logger.info("gemini: welcome closed (%s)", res)
            await asyncio.sleep(0.8)
    except Exception:
        pass


async def _check_signed_in(debug_port) -> Tuple[bool, str]:
    """
    Проверяем что профиль залогинен в Google.
    Надёжный признак — наличие редактора .ql-editor[contenteditable].
    Если редактор есть — пользователь вошёл, можно вводить запрос.
    """
    js = """
    (() => {
        const url = location.href;
        // Единственный безошибочный признак: передирект на страницу входа
        if (url.includes('accounts.google.com')) {
            return {signedIn: false, reason: 'redirect_to_login', url};
        }
        // Если редактор Gemini есть — значит вошли (самый надёжный признак)
        const hasEditor = !!document.querySelector('.ql-editor[contenteditable="true"]');
        if (hasEditor) return {signedIn: true, reason: 'editor_present', url};
        // Фоллбэк: если редактор ещё загружается — не считаем ошибкой
        return {signedIn: true, reason: 'no_editor_yet_assume_ok', url};
    })()
    """
    try:
        res = await cdp_cookies.eval_via_cdp(debug_port, js, timeout=5.0)
        if isinstance(res, dict):
            return bool(res.get("signedIn")), str(res.get("reason") or "")
    except Exception:
        pass
    return True, "check_failed"


async def _type_prompt_and_send(debug_port, prompt_text: str) -> None:
    if not prompt_text:
        raise RuntimeError("prompt_text пустой")
    ws_url = cdp_cookies.find_page_ws_url(debug_port, prefer_type="page")
    if not ws_url:
        raise RuntimeError(f"cdp: debug_port={debug_port} — page не найдена")

    async with cdp_cookies._CDPSession(ws_url, timeout=TYPING_TIMEOUT) as s:
        # 1. Фокус на editor (повторно — после upload мог сбиться)
        await s.send(
            "Runtime.evaluate",
            {
                "expression": f"""
                (() => {{
                    const el = document.querySelector({json.dumps(SEL_EDITOR)});
                    if (!el) return false;
                    el.focus();
                    return true;
                }})()
                """,
                "returnByValue": True,
            },
            timeout=10.0,
        )

        # 2. Input.insertText по чанкам
        for i in range(0, len(prompt_text), TEXT_CHUNK):
            chunk = prompt_text[i:i + TEXT_CHUNK]
            await s.send("Input.insertText", {"text": chunk}, timeout=20.0)

        # 3. Пауза, чтобы UI обновил состояние Send-кнопки
        await asyncio.sleep(0.5)

        # 4. Пробуем кликнуть Send
        click_js = """
        (() => {
            // Send-кнопка появляется после ввода текста.
            // Признаки: mat-icon fonticon="send", класс с "send", aria-label.
            const all = Array.from(document.querySelectorAll('button'));
            for (const b of all) {
                if (b.disabled) continue;
                const r = b.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                const cls = (b.className || '').toLowerCase();
                const ic = b.querySelector('mat-icon');
                const fi = ic && (
                    ic.getAttribute('fonticon') || ic.getAttribute('data-mat-icon-name') || ''
                ).toLowerCase();
                if (aria.includes('send') || aria.includes('отправ') ||
                    cls.includes('send-button') || cls.includes('sendbutton') ||
                    fi === 'send' || fi === 'arrow_forward' || fi === 'send-fill') {
                    b.click();
                    return 'clicked: aria=' + aria + ' icon=' + fi + ' cls=' + cls.substring(0, 50);
                }
            }
            return 'no_send_button';
        })()
        """
        click_res = await s.send(
            "Runtime.evaluate",
            {"expression": click_js, "returnByValue": True},
            timeout=10.0,
        )
        clicked = str((click_res or {}).get("result", {}).get("value") or "")

        if clicked.startswith("clicked"):
            logger.info("gemini: Send нажат (%s)", clicked)
        else:
            # Фоллбэк — Enter
            logger.debug("gemini: Send-кнопка не найдена (%r), жмём Enter", clicked)
            for ev_type in ("keyDown", "keyUp"):
                await s.send(
                    "Input.dispatchKeyEvent",
                    {
                        "type": ev_type,
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        "nativeVirtualKeyCode": 13,
                    },
                    timeout=10.0,
                )

    logger.info("gemini: промпт отправлен (%d символов)", len(prompt_text))


# ── 5. Polling: ждём появления сгенерённой картинки ────────────────────────

# JS: внутри — ждём до 8с появления большой картинки, извлекаем base64/url
_EXTRACT_IMAGE_JS = r"""
(async () => {
    // Проверяем ошибки
    if (location.href.includes('accounts.google.com'))
        return {error: 'not_signed_in', url: location.href};
    const body = document.body;
    const bodyText = (body && body.innerText) || '';
    if (bodyText.includes('(1060)'))
        return {error: 'gemini_error_1060', text: bodyText.slice(0, 200)};

    // Закрываем A/B-сравнение если появилось
    if (bodyText.toLowerCase().includes('какой из ответ') ||
        bodyText.toLowerCase().includes('which response')) {
        const btns = Array.from(document.querySelectorAll('button'));
        const voteBtn = btns.find(b => {
            const t = (b.innerText || '').toLowerCase();
            return t.includes('полезне') || t.includes('helpful');
        }) || btns.find(b => {
            const a = (b.getAttribute('aria-label') || '').toLowerCase();
            return a.includes('close') || a.includes('закр');
        });
        if (voteBtn) voteBtn.click();
        return {error: 'ab_modal_dismissed', generating: true};
    }

    // Снимаем lazy для принудительной загрузки
    document.querySelectorAll('img[loading="lazy"]').forEach(img => {
        img.loading = 'eager';
        img.scrollIntoView({behavior: 'instant', block: 'center'});
    });

    // Домены Gemini-UI которые НЕ являются сгенерированными картинками
    const BLOCKED_DOMAINS = [
        'gstatic.com', 'googleapis.com/identitytoolkit',
        'accounts.google.com', 'fonts.gstatic.com',
        'googleusercontent.com/a/',  // аватары
    ];
    const isBlockedSrc = src => BLOCKED_DOMAINS.some(d => src.includes(d));

    // Находим лучшего кандидата
    const AI_ALT = ['создано иск', 'ai-gen', 'generated', 'created by ai', ', ai'];
    const allImgs = Array.from(document.querySelectorAll('img'));

    const ranked = allImgs.map(img => {
        const src = img.src || '';
        if (!src.startsWith('blob:') && !src.startsWith('data:image') &&
            !src.startsWith('https://')) return null;
        // Фильтруем UI-иконки Gemini и аватары
        if (isBlockedSrc(src)) return null;
        if (src.includes('favicon') || src.includes('avatar') ||
            src.includes('logo') || src.includes('icon') ||
            src.includes('sparkle') || src.includes('aurora') ||
            src.includes('.svg')) return null;
        const alt = (img.alt || '').toLowerCase();
        const nw = img.naturalWidth || 0;
        const r  = img.getBoundingClientRect();
        const sz = Math.max(nw, r.width || 0);
        // Минимум 200px: сгенерированные картинки крупные, UI-иконки мелкие (150px и меньше)
        if (sz > 0 && sz < 200) return null;
        let score = sz;
        if (AI_ALT.some(k => alt.includes(k))) score += 10000;
        if (src.startsWith('blob:')) score += 5000;
        return {img, src, score};
    }).filter(Boolean).sort((a, b) => b.score - a.score);

    // Если кандидатов нет — проверяем есть ли blob вообще (Gemini генерирует)
    if (!ranked.length) {
        const isGen = /generating|thinking|генерир|обрабатыв/i.test(bodyText);
        const blobImgs = document.querySelectorAll('img[src^="blob:"]');
        return {error: 'no_candidates', generating: isGen,
                blobCount: blobImgs.length};
    }

    // Если лучший кандидат — httpUrl (не blob), но на странице есть blob-картинки
    // значит blob ещё загружается — ждём
    const best = ranked[0];
    if (best.src.startsWith('https://')) {
        const blobImgs = document.querySelectorAll('img[src^="blob:"]');
        if (blobImgs.length > 0) {
            // На странице есть blob — это, скорее всего, превью загруженного фото.
            // Но вдруг сгенерированный blob ещё не отрисован? Ждём.
            return {error: 'no_candidates', generating: true,
                    blobCount: blobImgs.length, waiting_for_blob: true};
        }
    }

    // Вспомогательная функция: вытащить dataUrl через Canvas (надёжнее fetch для blob)
    // Blob может быть revoked Gemini-ем после отображения — но img-элемент держит пиксели
    function extractViaCanvas(img) {
        const r = img.getBoundingClientRect();
        const w = img.naturalWidth  || Math.round(r.width)  || img.width  || 0;
        const h = img.naturalHeight || Math.round(r.height) || img.height || 0;
        if (w < 1 || h < 1) return {ok: false, reason: 'zero_dims nw=' + img.naturalWidth + ' rw=' + Math.round(r.width)};
        try {
            const canvas = document.createElement('canvas');
            canvas.width  = w;
            canvas.height = h;
            canvas.getContext('2d').drawImage(img, 0, 0);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
            if (!dataUrl || dataUrl === 'data:,' || dataUrl.length < 1000)
                return {ok: false, reason: 'empty_dataurl len=' + (dataUrl||'').length};
            return {ok: true, type: 'dataUrl', value: dataUrl, width: w, height: h, method: 'canvas'};
        } catch (e) {
            return {ok: false, reason: 'canvas_err: ' + e.message};
        }
    }

    const errors = [];
    // Берём лучшего и пытаемся вытащить
    for (const {img, src} of ranked) {
        if (src.startsWith('data:image')) {
            return {type: 'dataUrl', value: src,
                    width: img.naturalWidth, height: img.naturalHeight};
        }
        if (src.startsWith('blob:')) {
            // Сначала Canvas (не требует fetch, работает даже если blob revoked)
            const cv = extractViaCanvas(img);
            if (cv.ok) return cv;
            errors.push('canvas:' + cv.reason);

            // Запасной: fetch + FileReader
            try {
                const resp = await fetch(src);
                const blob = await resp.blob();
                if (blob.size < 1000) { errors.push('fetch:blob_tiny=' + blob.size); continue; }
                const dataUrl = await new Promise((res, rej) => {
                    const rd = new FileReader();
                    rd.onloadend = () => res(rd.result);
                    rd.onerror   = rej;
                    rd.readAsDataURL(blob);
                });
                return {type: 'dataUrl', value: dataUrl,
                        width: img.naturalWidth, height: img.naturalHeight, method: 'fetch'};
            } catch (fe) {
                errors.push('fetch:' + fe.message);
            }
            continue;
        }
        // httpUrl — только если нет blob-кандидатов лучшего качества
        if (src.startsWith('https://')) {
            return {type: 'httpUrl', value: src,
                    width: img.naturalWidth, height: img.naturalHeight};
        }
    }

    return {error: 'fetch_failed', candidates: ranked.length, errors: errors.join(' | ')};
})()
"""


# ── JS для поиска сгенерированного изображения и получения его координат ──

_FIND_AND_SCROLL_JS = r"""
(() => {
    const BLOCKED = ['gstatic.com','googleapis.com/identitytoolkit',
                     'accounts.google.com','googleusercontent.com/a/'];
    const AI_ALT  = ['создано иск','ai-gen','generated','created by ai',', ai'];
    const skip    = ['.svg','sparkle','aurora','icon','avatar','logo','favicon'];
    const found = Array.from(document.querySelectorAll('img'))
        .filter(img => {
            const s = img.src || '';
            if (!s.startsWith('blob:') && !s.startsWith('data:image')) return false;
            if (BLOCKED.some(d => s.includes(d))) return false;
            if (skip.some(k => s.includes(k))) return false;
            const alt = (img.alt||'').toLowerCase();
            if (!AI_ALT.some(k => alt.includes(k))) return false;
            const r = img.getBoundingClientRect();
            return r.width >= 150 && r.height >= 150;
        });
    if (!found.length) return {found: false};
    const img = found[found.length - 1]; // последняя = самая новая = сгенерированная
    img.scrollIntoView({behavior:'instant',block:'center',inline:'center'});
    return {found: true, alt: (img.alt||'').slice(0,50)};
})()
"""

_GET_RECT_JS = r"""
(() => {
    const BLOCKED = ['gstatic.com','googleapis.com/identitytoolkit',
                     'accounts.google.com','googleusercontent.com/a/'];
    const AI_ALT  = ['создано иск','ai-gen','generated','created by ai',', ai'];
    const skip    = ['.svg','sparkle','aurora','icon','avatar','logo','favicon'];
    const found = Array.from(document.querySelectorAll('img'))
        .filter(img => {
            const s = img.src || '';
            if (!s.startsWith('blob:') && !s.startsWith('data:image')) return false;
            if (BLOCKED.some(d => s.includes(d))) return false;
            if (skip.some(k => s.includes(k))) return false;
            const alt = (img.alt||'').toLowerCase();
            if (!AI_ALT.some(k => alt.includes(k))) return false;
            const r = img.getBoundingClientRect();
            return r.width >= 150 && r.height >= 150;
        });
    if (!found.length) return null;
    const img = found[found.length - 1];
    const r   = img.getBoundingClientRect();
    return {
        x: Math.max(0, Math.round(r.left)),
        y: Math.max(0, Math.round(r.top)),
        w: Math.round(r.width),
        h: Math.round(r.height),
        nw: img.naturalWidth,
        nh: img.naturalHeight,
    };
})()
"""


async def _wait_for_screenshot(
    debug_port,
    timeout: int = 120,
) -> Optional[bytes]:
    """
    Ожидаем появления AI-картинки на странице Gemini,
    затем делаем CDP-скриншот её области. Возвращает JPEG-байты или None.
    Не зависит от blob/canvas/fetch — берёт пиксели прямо с экрана.
    """
    deadline = time.monotonic() + float(timeout)
    attempt  = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            # Шаг 1: проверяем есть ли сгенерированное фото и скроллим его
            scroll_res = await cdp_cookies.eval_via_cdp(
                debug_port, _FIND_AND_SCROLL_JS, timeout=8.0)
            if not (isinstance(scroll_res, dict) and scroll_res.get("found")):
                logger.info("gemini: screenshot #%d — AI-картинка ещё не появилась",
                            attempt)
                await asyncio.sleep(min(4.0, deadline - time.monotonic()))
                continue

            # Шаг 2: ждём окончания скролла
            await asyncio.sleep(0.5)

            # Шаг 3: берём точные координаты
            rect = await cdp_cookies.eval_via_cdp(
                debug_port, _GET_RECT_JS, timeout=8.0)
            if not isinstance(rect, dict) or rect.get("w", 0) < 100:
                logger.warning("gemini: screenshot #%d — плохой rect: %r", attempt, rect)
                await asyncio.sleep(min(4.0, deadline - time.monotonic()))
                continue

            logger.info(
                "gemini: screenshot #%d — clip %dx%d at (%d,%d) nw=%d",
                attempt, rect["w"], rect["h"], rect["x"], rect["y"], rect.get("nw", 0)
            )

            # Шаг 4: CDP-скриншот
            ws_url = cdp_cookies.find_page_ws_url(debug_port, prefer_type="page")
            if not ws_url:
                logger.warning("gemini: screenshot — page WS URL не найден")
                break

            async with cdp_cookies._CDPSession(ws_url, timeout=SCREENSHOT_TIMEOUT) as sess:
                ss_result = await sess.send(
                    "Page.captureScreenshot",
                    {
                        "format": "jpeg",
                        "quality": 92,
                        "clip": {
                            "x": float(rect["x"]),
                            "y": float(rect["y"]),
                            "width":  float(rect["w"]),
                            "height": float(rect["h"]),
                            "scale": 1.0,
                        },
                        "fromSurface": True,
                        "captureBeyondViewport": False,
                    },
                    timeout=SCREENSHOT_TIMEOUT,
                )

            raw = base64.b64decode(ss_result.get("data", ""))
            if len(raw) < 5000:
                logger.warning("gemini: screenshot слишком мало (%d б), повтор #%d",
                                len(raw), attempt)
                await asyncio.sleep(min(4.0, deadline - time.monotonic()))
                continue

            logger.info("gemini: screenshot успех %d байт (%dx%d)",
                        len(raw), rect["w"], rect["h"])
            return raw

        except Exception as e:
            logger.warning("gemini: screenshot #%d — %s", attempt, e)
            await asyncio.sleep(min(4.0, deadline - time.monotonic()))
            continue

    logger.warning("gemini: screenshot истек таймаут (%dс, %d попыток)", timeout, attempt)
    return None


async def _wait_for_image(debug_port, timeout: int = 120) -> Dict[str, Any]:
    deadline = time.monotonic() + float(timeout)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            res = await cdp_cookies.eval_via_cdp(
                debug_port, _EXTRACT_IMAGE_JS,
                await_promise=True, timeout=EXTRACT_JS_TIMEOUT,
            )
        except Exception as e:
            logger.debug("gemini: extract #%d — %s", attempt, e)
            res = None

        if isinstance(res, dict):
            err = res.get("error")
            if err:
                if "not_signed_in" in str(err):
                    raise RuntimeError(
                        "Профиль MSB не залогинен в Google — "
                        "страница ушла на accounts.google.com"
                    )
                if "1060" in str(err):
                    raise RuntimeError(
                        "Gemini отклонил запрос (ошибка 1060 = нужно войти в Google). "
                        "Профиль не залогинен или сессия истекла. "
                        f"Текст ошибки: {res.get('text', '')}"
                    )
                logger.info(
                    "gemini: extract #%d — %s (gen=%s, blobs=%s, err=%s)",
                    attempt, err, res.get("generating"),
                    res.get("blobCount") or res.get("candidates"),
                    res.get("errors") or res.get("waiting_for_blob") or "",
                )
            else:
                logger.info(
                    "gemini: получили картинку с попытки #%d (%dx%d, type=%s)",
                    attempt,
                    int(res.get("width") or 0),
                    int(res.get("height") or 0),
                    res.get("type"),
                )
                return res

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(4.0, max(0.5, remaining)))  # 4с между попытками

    raise RuntimeError(
        f"Не дождались изображения от Gemini за {timeout}с "
        f"({attempt} попыток polling). Возможно превышена квота или отказ."
    )


# ── 6. Сохранение результата ─────────────────────────────────────────────


async def _save_extracted(extracted: Dict[str, Any], save_path: str) -> str:
    etype = extracted.get("type")
    val = extracted.get("value")
    if not val:
        raise RuntimeError(f"extracted.value пустой: {extracted}")

    if etype == "dataUrl":
        b64 = val.split(",", 1)[1] if "," in val else val
        try:
            data = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise RuntimeError(f"base64 decode: {e}") from e
    elif etype == "httpUrl":
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(val)
            resp.raise_for_status()
            data = resp.content
    else:
        raise RuntimeError(f"Неизвестный type: {etype!r}")

    if not data:
        raise RuntimeError(f"0 байт для сохранения (type={etype})")

    out = Path(save_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)

    logger.info(
        "gemini: сохранили %d байт (%dx%d) → %s",
        len(data),
        int(extracted.get("width") or 0),
        int(extracted.get("height") or 0),
        out,
    )
    return str(out)


# ── Win32 window hider (Windows only) ────────────────────────────────────


def _win_hide_pid_windows(pid: int, stop_event, log) -> None:
    """
    Фоновый тред (только Windows): каждые 50мс ищет видимые окна процесса
    с заданным PID и прячет их через Win32 ShowWindow(SW_HIDE=0).

    Запускается сразу после получения PID браузера и работает до
    завершения автоматизации. Прячет окно если оно появляется при:
    - навигации на gemini.google.com (которая может de-minimize)
    - focus-запросах со стороны Gemini SPA
    - ошибках CDP minimize в MSB
    """
    import sys
    if sys.platform != 'win32':
        return
    import ctypes
    import ctypes.wintypes
    import time

    user32 = ctypes.windll.user32
    SW_HIDE = 0
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    hidden: set[int] = set()

    while not stop_event.is_set():
        found = []

        def _cb(hwnd, _lp):
            win_pid = ctypes.wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
            if win_pid.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        try:
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass

        for hwnd in found:
            if hwnd not in hidden:
                try:
                    user32.ShowWindow(hwnd, SW_HIDE)
                    hidden.add(hwnd)
                    log.info("gemini: browser window hidden (pid=%d hwnd=%d)", pid, hwnd)
                except Exception:
                    pass

        time.sleep(0.05)


def _win_bring_to_front(pid: int, log) -> None:
    """
    Windows only: ищет главное окно процесса с PID и поднимает его на передний план.
    Вызывается один раз после старта профиля в visible-режиме.
    """
    import sys
    if sys.platform != 'win32' or not pid:
        return
    import ctypes
    import ctypes.wintypes
    import time

    user32   = ctypes.windll.user32
    SW_RESTORE   = 9
    WNDENUMPROC  = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    # Даём браузеру секунду создать окно
    for _ in range(20):          # до 2 секунд
        found = []

        def _cb(hwnd, _lp):
            win_pid = ctypes.wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
            # Берём только реальные top-level окна с заголовком
            if win_pid.value == pid and user32.GetParent(hwnd) == 0:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    found.append(hwnd)
            return True

        try:
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass

        if found:
            hwnd = found[0]
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                log.info("gemini: browser window brought to front (pid=%d hwnd=%d)", pid, hwnd)
            except Exception as e:
                log.warning("gemini: bring to front failed: %s", e)
            return

        time.sleep(0.1)

    log.warning("gemini: no window found for pid=%d after 2s", pid)


def _start_win_hider(pid: int, log):  # -> threading.Event | None
    """
    Запустить фоновый Win32-hider для PID (только Windows).
    Возвращает threading.Event для остановки, или None если не Windows / нет PID.
    """
    import sys
    if sys.platform != 'win32' or not pid:
        return None
    import threading
    stop = threading.Event()
    t = threading.Thread(
        target=_win_hide_pid_windows,
        args=(pid, stop, log),
        daemon=True,
        name=f"win-hider-{pid}",
    )
    t.start()
    log.info("gemini: Win32 window hider started (pid=%d)", pid)
    return stop


# ── 7. Главная функция ────────────────────────────────────────────────────


async def restyle_image_via_browser(
    image_url: str,
    prompt_text: str,
    profile_id: str,
    save_path: str,
    timeout: int = 120,
    status_callback=None,
    headless: bool = False,
    launch_mode: Optional[str] = None,
    engine: Optional[str] = None,
) -> str:
    """
    Прогнать фото через Gemini (restyle) и сохранить результат.

    Args:
        image_url:   URL исходного фото (http/https).
        prompt_text: промпт для Gemini.
        profile_id:  envId профиля.
        save_path:   абсолютный путь к .jpg для результата.
        timeout:     максимум секунд ждать появления картинки (default 120).
        launch_mode: "visible" | "minimized" | "background" | "headless".
                     Для Gemini/Google рекомендуется "background".
        engine:      движок MSB ("cloakbrowser" | "patchright" | "auto").
                     Для Gemini/Google рекомендуется "cloakbrowser":
                     66 C++-патчей (canvas, WebGL, fonts, GPU, WebRTC,
                     авто-сигналы) без CDP Emulation-домена.

    Returns:
        save_path (та же строка) при успехе.

    Raises:
        RuntimeError с понятным сообщением.
    """
    if not image_url:
        raise RuntimeError("image_url пустой")
    if not prompt_text:
        raise RuntimeError("prompt_text пустой")
    if not profile_id:
        raise RuntimeError("profile_id пустой")
    if not save_path:
        raise RuntimeError("save_path пустой")

    save_path_abs = str(Path(save_path).resolve())
    Path(save_path_abs).parent.mkdir(parents=True, exist_ok=True)

    ml = MsbClient()
    temp_image: Optional[str] = None
    _win_hider_stop = None

    def _status(msg: str) -> None:
        logger.info("gemini-status: %s", msg)
        if callable(status_callback):
            try:
                status_callback(msg)
            except Exception:
                pass

    try:
        # 1. Запустить профиль
        effective_launch_mode = launch_mode or ("headless" if headless else "background")
        _status(f"Запускаю профиль Gemini ({effective_launch_mode})…")
        logger.info("gemini: запускаем профиль %s, launchMode=%s", profile_id, effective_launch_mode)
        start_opts: dict = {"launchMode": effective_launch_mode, "headless": headless}
        if engine:
            start_opts["engine"] = engine
        started = await ml.start_profile(profile_id, **start_opts)
        debug_port = (started or {}).get("debugPort") if isinstance(started, dict) else ""
        if not debug_port:
            raise RuntimeError(
                f"Не удалось запустить профиль {profile_id} — debugPort пустой. "
                f"Ответ: {started!r}"
            )
        logger.info(
            "gemini: профиль запущен, debugPort=%s, launchMode=%s, backgroundApplied=%s, focusSuppressed=%s",
            debug_port,
            (started or {}).get("launchMode"),
            (started or {}).get("backgroundApplied"),
            (started or {}).get("focusSuppressed"),
        )
        _browser_pid = int((started or {}).get("pid") or 0)
        if effective_launch_mode == "visible":
            # Поднимаем окно на передний план — MSB может запустить минимизированным
            import threading as _thr
            _thr.Thread(
                target=_win_bring_to_front,
                args=(_browser_pid, logger),
                daemon=True, name="win-raise",
            ).start()
        else:
            # Скрываем окно если background/minimized
            _win_hider_stop = _start_win_hider(_browser_pid, logger)

        # 2. Viewport: CloakBrowser устанавливает через --fingerprint-platform=windows
        # НЕ вызываем Emulation.setDeviceMetricsOverride — это активирует CDP Emulation-домен
        # (прямой антибот-сигнал), перекрывая бинарные настройки CloakBrowser.
        # Если Gemini уходит в mobile-режим — это значит профиль запущен без CloakBrowser
        # или без правильного --fingerprint-platform=windows.
        logger.info("gemini: viewport управляется CloakBrowser (--fingerprint-platform=windows)")

        # 3. Навигация
        _status("Открываю Gemini…")
        logger.info("gemini: переходим на %s", GEMINI_URL)
        nav_ok = await cdp_cookies.navigate(
            debug_port, GEMINI_URL, timeout=DEFAULT_NAV_TIMEOUT,
        )
        if not nav_ok:
            logger.warning("gemini: navigate вернул False, продолжаем")
        await asyncio.sleep(DEFAULT_PAGE_LOAD_DELAY)

        # 3. Проверить input area
        _status("Жду загрузку интерфейса Gemini…")
        if not await _ensure_input_ready(debug_port, timeout=15.0):
            raise RuntimeError("Input area Gemini не появилась за 15с")

        # 4. Проверить залогиненность
        _status("Проверяю авторизацию Google…")
        signed_in, reason = await _check_signed_in(debug_port)
        if not signed_in:
            raise RuntimeError(
                f"Профиль MSB не залогинен в Google (reason={reason}). "
                "Открой gemini.google.com в MSB вручную и войди в Google-аккаунт."
            )

        # 5. Закрыть welcome-диалог если есть
        await _dismiss_welcome(debug_port)

        # 6. Скачать исходное фото
        _status("Готовлю исходное фото…")
        temp_image = await _download_image_to_tempfile(image_url)

        try:
            # 7. Загрузить файл: focus → click + → menu → click item → setFile
            _status("Загружаю фото в Gemini…")
            logger.info("gemini: загружаем файл")
            await upload_file(debug_port, temp_image)

            # 8. Вписать промпт + отправить
            _status("Отправляю промпт в Gemini…")
            logger.info("gemini: вводим промпт и отправляем")
            await _type_prompt_and_send(debug_port, prompt_text)

            # 9. Ждём результат
            _status("Жду генерацию изображения…")
            logger.info("gemini: ждём результат (до %dс)", timeout)

            # Основной путь: CDP-скриншот (не зависит от blob/canvas/fetch)
            img_bytes = await _wait_for_screenshot(debug_port, timeout=timeout)
            if img_bytes:
                _status("Сохраняю готовую картинку…")
                Path(save_path_abs).parent.mkdir(parents=True, exist_ok=True)
                Path(save_path_abs).write_bytes(img_bytes)
                logger.info("gemini: сохранили %d байт (screenshot) → %s",
                            len(img_bytes), save_path_abs)
                return save_path_abs

            # Fallback: JS-извлечение blob/canvas
            logger.warning("gemini: screenshot не сработал, пробуем JS-извлечение")
            _status("Пробую резервное извлечение картинки…")
            extracted = await _wait_for_image(debug_port, timeout=max(30, timeout // 3))

            # 10. Сохранить
            await _save_extracted(extracted, save_path_abs)
            return save_path_abs

        finally:
            if temp_image:
                try:
                    os.unlink(temp_image)
                except Exception:
                    pass

    except RuntimeError:
        raise
    except Exception as e:
        logger.error("gemini: MSB: %s", e)
        raise RuntimeError(f"MSB: {e}") from e
    finally:
        # Останавливаем Win32-hider
        if _win_hider_stop is not None:
            _win_hider_stop.set()
        # Останавливаем профиль браузера — чтобы процесс не висел в фоне вечно
        if profile_id:
            try:
                await ml.stop_profile(profile_id)
                logger.info("gemini: профиль %s остановлен", profile_id)
            except Exception as _e:
                logger.warning("gemini: не удалось остановить профиль: %s", _e)
        try:
            await ml.close()
        except Exception:
            pass


# ── 8. Синхронная обёртка ────────────────────────────────────────────────


def restyle_image_sync(
    image_url: str,
    prompt_text: str,
    profile_id: str,
    save_path: str,
    timeout: int = 120,
    status_callback=None,
    headless: bool = False,
    launch_mode: Optional[str] = None,
    engine: Optional[str] = None,
) -> str:
    """Синхронная обёртка над :func:`restyle_image_via_browser`."""
    return asyncio.run(
        restyle_image_via_browser(
            image_url=image_url,
            prompt_text=prompt_text,
            profile_id=profile_id,
            save_path=save_path,
            timeout=timeout,
            status_callback=status_callback,
            headless=headless,
            launch_mode=launch_mode,
            engine=engine,
        )
    )


# ── 9. CLI для отладки ────────────────────────────────────────────────────


async def _cli_main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="morelogin_gemini",
        description="Автоматизация Gemini через MSB CDP",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("restyle", help="Загрузить фото + промпт в Gemini → результат")
    r.add_argument("--profile", required=True, help="envId профиля MSB")
    r.add_argument("--image", required=True, help="URL исходного фото")
    r.add_argument("--prompt", required=True, help="Промпт (в кавычках)")
    r.add_argument("--out", required=True, help="Путь сохранения (.jpg)")
    r.add_argument("--timeout", type=int, default=120, help="Таймаут генерации, сек")

    args = p.parse_args()
    if args.cmd == "restyle":
        try:
            out = await restyle_image_via_browser(
                image_url=args.image,
                prompt_text=args.prompt,
                profile_id=args.profile,
                save_path=args.out,
                timeout=args.timeout,
            )
            print(f"OK: {out}")
        except Exception as e:
            print(f"ERR: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    asyncio.run(_cli_main())
