"""
Быстрый скриншот текущего браузера через CDP.
Запуск: python take_shot.py
"""
import asyncio
import base64
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from parser.cdp_cookies import _CDPSession, find_page_ws_url

DEBUG_PORT = 62032
OUT_DIR = Path("screenshots")

async def main():
    OUT_DIR.mkdir(exist_ok=True)
    ws_url = find_page_ws_url(DEBUG_PORT)
    if not ws_url:
        print(f"[ERROR] CDP page не найдена на порту {DEBUG_PORT}")
        return

    print(f"[OK] CDP WS: {ws_url}")

    async with _CDPSession(ws_url) as s:
        # Получить URL
        url_r = await s.send("Runtime.evaluate", {"expression": "location.href"})
        print(f"[URL] {url_r.get('result', {}).get('value', '?')}")

        # Скриншот
        result = await s.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(result.get("data", ""))
        ts = int(time.time())
        out = OUT_DIR / f"shot_{ts}.png"
        out.write_bytes(data)
        print(f"[SHOT] {out} ({len(data)} bytes)")

asyncio.run(main())
