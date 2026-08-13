# -*- coding: utf-8 -*-
"""
cookie_status_routes.py — Flask blueprint для статуса и обновления куков ЛК.
Регистрируется в app.py через: from cookie_status_routes import register_cookie_status
"""
import json
import time
import threading
import asyncio
from pathlib import Path

# Путь к файлу куков продавца (обновляется через кнопку MSB Seller)
_SELLER_COOKIES_PATH = Path(__file__).parent / "data" / "seller_cookies.json"


def _get_cookies_path():
    """Возвращает путь к файлу куков продавца.

    Приоритет:
      1. data/seller_cookies.json  — обновляется кнопкой "Seller MSB"
      2. Снапшот профиля парсера (AppData) — для обратной совместимости
    """
    if _SELLER_COOKIES_PATH.exists():
        return _SELLER_COOKIES_PATH
    # fallback: снапшот профиля парсера
    try:
        from parser.msb_cookies import _find_ggsel_profile_id_from_disk, _snapshot_path
        pid = _find_ggsel_profile_id_from_disk()
        if pid:
            snap = _snapshot_path(pid)
            if snap.exists():
                return snap
    except Exception:
        pass
    return _SELLER_COOKIES_PATH  # несуществующий — caller проверяет .exists()

# Qrator-ключевые куки — если хотя бы один есть, сессия считается живой
QRATOR_KEYS = {
    "qrator_msid2", "qrator_jsid", "qrator_ssid", "qrator_ssid2",
    "qrator_jsr", "__qrator_jsid",
}
AUTH_KEYS = {
    "access_token", "refresh_token", "chat_token",
    "session", "auth", "token", "sid",
}


def _cookie_info():
    """Возвращает dict с состоянием cookies.json."""
    cookie_path = _get_cookies_path()
    if not cookie_path.exists():
        return {"exists": False, "fresh": False, "count": 0, "age_sec": None,
                "has_qrator": False, "qrator_keys": []}
    age = int(time.time() - cookie_path.stat().st_mtime)
    fresh = age < 3600  # свежее 1 часа
    try:
        data = json.loads(cookie_path.read_text(encoding="utf-8"))
        cookie_dict = {}  # {name: value} — для отображения в UI
        if isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and c.get("name"):
                    cookie_dict[c["name"]] = str(c.get("value", ""))
        elif isinstance(data, dict):
            cookie_dict = {k: str(v) for k, v in data.items()}
        names = list(cookie_dict.keys())
        names_lower_map = {n.lower(): n for n in names}  # lower → original
        has_qrator = bool(QRATOR_KEYS & set(names_lower_map)) or bool(AUTH_KEYS & set(names_lower_map))
        qrator_found = [names_lower_map[n] for n in (QRATOR_KEYS | AUTH_KEYS) if n in names_lower_map]
    except Exception:
        names, has_qrator, qrator_found, cookie_dict = [], False, [], {}
    return {
        "exists": True,
        "fresh": fresh,
        "count": len(names),
        "age_sec": age,
        "age_seconds": age,   # alias: JS читает age_seconds
        "has_qrator": has_qrator,
        "qrator_keys": qrator_found,
        "cookies": cookie_dict,  # {name: value} — для модала
    }


def _do_refresh(force=False):
    """Синхронная обёртка над async QratorCookieMiddleware."""
    info = _cookie_info()
    if not force and info.get("fresh"):
        return True, "already_fresh"
    try:
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).parent))
        from parser.msb_cookies import QratorCookieMiddleware
    except ImportError as e:
        return False, f"import_error: {e}"

    async def _run():
        async with QratorCookieMiddleware() as mw:
            return await mw.cookies(force_refresh=True)

    try:
        cookies = asyncio.run(_run())
    except Exception as e:
        return False, f"msb_error: {e}"

    if not cookies:
        return False, "empty_cookies"

    # MSB Cookie middleware already saves the snapshot internally
    return True, f"saved_{len(cookies)}_cookies"


def register_cookie_status(app):
    """Регистрирует /api/cookie/* маршруты в Flask-приложении."""
    from flask import jsonify, request as flask_request

    @app.route("/api/cookie/status", methods=["GET"])
    def api_cookie_status():
        info = _cookie_info()
        return jsonify({"ok": True, **info})

    @app.route("/api/cookie/refresh", methods=["POST"])
    def api_cookie_refresh():
        body = flask_request.get_json(silent=True) or {}
        force = bool(body.get("force", True))

        result = {}

        def run():
            ok, msg = _do_refresh(force=force)
            result["ok"] = ok
            result["msg"] = msg

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=90)

        if "ok" not in result:
            return jsonify({"ok": False, "error": "timeout_90s"}), 504

        info = _cookie_info()
        return jsonify({"ok": result["ok"], "msg": result["msg"], **info})
