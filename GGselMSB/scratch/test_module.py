"""
scratch/test_module.py — тест parser/morelogin_gemini.py с локальным HTTP-сервером.
"""
from __future__ import annotations
import asyncio
import os
import pathlib
import socket
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

import httpx

sys.path.insert(0, "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin")

from parser.morelogin_gemini import restyle_image_via_browser

ML_BASE = "http://127.0.0.1:40000"
ML_ID = "1716740459457616"
ML_KEY = "8afb02927e724b6caadc6363f13f3c61"

SCRATCH = pathlib.Path("scratch")
SCRATCH.mkdir(exist_ok=True)
OUT_PATH = SCRATCH / "test_gemini_out.jpg"


def find_test_image():
    for d in (pathlib.Path("~/Desktop").expanduser(),
              pathlib.Path("C:/Users/Atreum/Desktop")):
        if not d.exists(): continue
        for ext in (".jpg", ".jpeg", ".png"):
            c = list(d.glob(f"*{ext}"))
            if c: return c[0]
    return None


class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()
    def log_message(self, format, *args):
        # Подавляем спам
        pass


def start_server(directory: str, port: int = 0):
    """Запустить HTTP-сервер в отдельном потоке. Возвращает (port, server)."""
    os.chdir(directory)
    handler = CORSRequestHandler
    server = HTTPServer(("127.0.0.1", port), handler)
    actual_port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return actual_port, server


async def get_first_profile():
    async with httpx.AsyncClient(timeout=20, trust_env=False) as c:
        r = await c.post(f"{ML_BASE}/api/env/page",
            json={"pageNo": 1, "pageSize": 3},
            headers={"X-Api-Id": ML_ID, "X-Api-Key": ML_KEY})
        r.raise_for_status()
        d = r.json().get("data") or {}
        items = d.get("list") or d.get("dataList") or []
        if not items: raise RuntimeError("no profiles")
        it = items[0]
        return str(it.get("envId") or it.get("id")), it.get("envName") or it.get("name")


async def main():
    img = find_test_image()
    if not img:
        print("[ERR] no test image on desktop")
        return
    print(f"[IMG] {img}")

    # Запустим локальный HTTP-сервер для картинки
    port, server = start_server(str(img.parent))
    img_name = img.name
    img_url = f"http://127.0.0.1:{port}/{img_name}"
    print(f"[HTTP] serving {img.parent} at :{port}")
    print(f"[URL]  {img_url}")

    # Проверим что сервер отдаёт файл
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(img_url)
        print(f"[HTTP check] status={r.status_code}, bytes={len(r.content)}")

    env_id, name = await get_first_profile()
    print(f"[PROFILE] {env_id} ({name})")

    prompt = "Опиши это фото одним предложением на русском"
    print(f"[PROMPT] {prompt}")
    print(f"[OUT]    {OUT_PATH}")
    print(f"[TIMEOUT] 90s")
    print()

    t0 = time.monotonic()
    try:
        result = await restyle_image_via_browser(
            image_url=img_url,
            prompt_text=prompt,
            profile_id=env_id,
            save_path=str(OUT_PATH),
            timeout=90,
        )
        dt = time.monotonic() - t0
        print(f"\n[OK in {dt:.1f}s] {result}")
        if os.path.exists(result):
            print(f"   size: {os.path.getsize(result)} bytes")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"\n[ERR in {dt:.1f}s] {e}")
        import traceback
        traceback.print_exc()
    finally:
        server.shutdown()


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    asyncio.run(main())
