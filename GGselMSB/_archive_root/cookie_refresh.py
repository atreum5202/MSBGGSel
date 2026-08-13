# -*- coding: utf-8 -*-
"""
cookie_refresh.py — утилита для обновления cookies.json из MSB.

Запускает MSB-профиль, прогоняет сценарий ggsel-login,
сохраняет куки в cookies.json (тот же файл что читает _get_cookie_header в app.py).

Использование:
    python cookie_refresh.py                  # обновить если устарели (> 1 часа)
    python cookie_refresh.py --force          # принудительно обновить
    python cookie_refresh.py --path C:\путь\cookies.json  # явный путь к файлу
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cookie_refresh")

# Путь по умолчанию — тот же что в _get_cookie_header()
DEFAULT_COOKIES_PATH = Path(r"C:\Users\Atreum\Desktop\GGsellerCopy\cookies.json")

# TTL в секундах (1 час)
DEFAULT_TTL = 3600


def cookies_are_fresh(path: Path, ttl: int = DEFAULT_TTL) -> bool:
    """Возвращает True если файл существует и моложе ttl секунд."""
    try:
        age = time.time() - path.stat().st_mtime
        return age < ttl
    except OSError:
        return False


async def refresh_cookies(cookies_path: Path = DEFAULT_COOKIES_PATH, force: bool = False) -> bool:
    """
    Получает свежие куки через MSB и сохраняет в cookies.json.
    Возвращает True при успехе.
    """
    if not force and cookies_are_fresh(cookies_path):
        age = time.time() - cookies_path.stat().st_mtime
        log.info("Куки свежие (возраст %.0f сек), обновление не нужно. Используй --force для принудительного обновления.", age)
        return True

    # Импортируем MSB клиент из проекта
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from parser.msb_cookies import QratorCookieMiddleware
    except ImportError as e:
        log.error("Не удалось импортировать QratorCookieMiddleware: %s", e)
        return False

    log.info("Получаем куки через MSB...")
    async with QratorCookieMiddleware() as mw:
        cookies = await mw.cookies(force_refresh=True)

    if not cookies:
        log.error("MSB не вернул куки. Убедись что MSB запущен и сценарий ggsel-login настроен.")
        return False

    # Преобразуем dict {name: value} в формат Netscape/CDP [{name, value, domain, ...}]
    # — тот же формат что читает _get_cookie_header()
    cookies_list = [
        {
            "name": name,
            "value": value,
            "domain": "seller.ggsel.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
        }
        for name, value in cookies.items()
    ]

    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cookies_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cookies_list, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cookies_path)
        log.info("Куки сохранены → %s (%d штук)", cookies_path, len(cookies_list))
        for name, value in cookies.items():
            preview = value[:40] + "..." if len(value) > 40 else value
            log.info("  %s = %s", name, preview)
        return True
    except Exception as e:
        log.error("Ошибка сохранения: %s", e)
        tmp.unlink(missing_ok=True)
        return False


# ── Flask-эндпоинт (для вызова из интерфейса) ─────────────────────────────

def register_cookie_refresh_route(app):
    """
    Регистрирует маршрут /api/cookies/refresh в Flask-приложении.
    Добавь в app.py: from cookie_refresh import register_cookie_refresh_route; register_cookie_refresh_route(app)
    """
    import threading

    @app.route("/api/cookies/refresh", methods=["POST"])
    def api_cookies_refresh():
        from flask import request, jsonify
        force = (request.get_json(silent=True) or {}).get("force", False)

        result = {"status": "started"}

        def run():
            ok = asyncio.run(refresh_cookies(force=force))
            result["ok"] = ok

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=60)

        if "ok" not in result:
            return jsonify({"ok": False, "error": "Таймаут — MSB не ответил за 60 сек"}), 504
        return jsonify({"ok": result["ok"], "path": str(DEFAULT_COOKIES_PATH)})

    @app.route("/api/cookies/status", methods=["GET"])
    def api_cookies_status():
        from flask import jsonify
        path = DEFAULT_COOKIES_PATH
        exists = path.exists()
        fresh = cookies_are_fresh(path) if exists else False
        age = None
        count = 0
        if exists:
            age = int(time.time() - path.stat().st_mtime)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                count = len(data)
            except Exception:
                pass
        return jsonify({
            "ok": True,
            "exists": exists,
            "fresh": fresh,
            "age_sec": age,
            "count": count,
            "path": str(path),
        })


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Принудительно обновить куки")
    parser.add_argument("--path", type=Path, default=DEFAULT_COOKIES_PATH, help="Путь к cookies.json")
    args = parser.parse_args()

    ok = asyncio.run(refresh_cookies(cookies_path=args.path, force=args.force))
    sys.exit(0 if ok else 1)
