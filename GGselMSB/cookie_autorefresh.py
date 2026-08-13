# -*- coding: utf-8 -*-
"""
cookie_autorefresh.py — фоновое автообновление Seller-куков через MSB.

Запускает daemon-поток-планировщик при старте Flask.
Если куки устарели (> REFRESH_TTL_SEC) — открывает MSB в background-режиме,
обходит ggsel.net/catalog и seller.ggsel.com, собирает Qrator-куки, закрывает браузер.
"""
import asyncio
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("cookie_autorefresh")

# ── Настройки ────────────────────────────────────────────────────────────────
REFRESH_TTL_SEC     = 7200   # обновлять если куки старше 2 часов
CHECK_INTERVAL_SEC  = 1800   # проверять каждые 30 минут
SELLER_COOKIES_PATH = Path(__file__).parent / "data" / "seller_cookies.json"



_QRATOR_KEYS = {"qrator_msid2", "qrator_jsid", "qrator_ssid", "qrator_ssid2",
                "qrator_jsr", "__qrator_jsid"}

# ── Состояние (in-memory) ─────────────────────────────────────────────────────
_state: dict = {
    "enabled":           True,
    "running":           False,
    "last_refresh_ts":   None,   # unix timestamp
    "last_status":       "idle", # idle | running | ok | warn | error
    "last_msg":          "",
    "next_check_ts":     None,   # unix timestamp
}
_lock = threading.Lock()


def _get_state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw):
    with _lock:
        _state.update(kw)


# ── Утилиты ───────────────────────────────────────────────────────────────────
def _cookie_age_sec():
    """Возраст файла куков в секундах. None если файла нет."""
    try:
        return time.time() - SELLER_COOKIES_PATH.stat().st_mtime
    except OSError:
        return None


def _needs_refresh() -> bool:
    age = _cookie_age_sec()
    return age is None or age >= REFRESH_TTL_SEC


def _filter_cookies(raw: dict) -> dict:
    return {
        k: v for k, v in raw.items()
        if any(x in k.lower() for x in ("ggsel", "qrator", "session", "auth", "token"))
    }


# ── Основная async-задача ────────────────────────────────────────────────────
async def _do_refresh() -> tuple[int, bool]:
    """
    Открывает MSB-профиль SellerGGsel в background-режиме,
    забирает актуальные куки из уже залогиненного профиля, закрывает браузер.
    """
    from parser.msb_client import MsbClient

    async with MsbClient() as cl:
        # 1. Найти профиль SellerGGsel
        _SELLER_GROUP_NAMES = {"sellerggsel", "seller ggsel", "seller", "sellerggsel1"}
        groups = await cl.get_groups()
        seller_profile_ids: list[str] = []
        for g in groups:
            if (g.get("name") or "").lower().replace(" ", "") in {
                n.replace(" ", "") for n in _SELLER_GROUP_NAMES
            }:
                seller_profile_ids = [str(x) for x in (g.get("profileIds") or [])]
                break

        if not seller_profile_ids:
            raise RuntimeError("Группа SellerGGsel не найдена в MSB")

        profile_id = seller_profile_ids[0]

        # 2. Запустить в фоне — профиль уже залогинен, навигация не нужна
        _set(last_msg="Запускаем профиль в фоне...")
        await cl.start_profile(profile_id, launchMode="background")
        await asyncio.sleep(5)  # ждём полной инициализации

        # 3. Забираем куки напрямую — навигация не нужна, профиль уже имеет всё
        _set(last_msg="Читаем куки из профиля...")
        cookies = _filter_cookies(await cl.get_cookies(profile_id))

        # 4. Закрыть браузер
        try:
            await cl.stop_profile(profile_id)
        except Exception as e:
            log.warning("autorefresh: stop_profile failed: %s", e)

        if not cookies:
            raise RuntimeError("Куки не получены — профиль пуст?")

        # 5. Сохраняем
        SELLER_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SELLER_COOKIES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SELLER_COOKIES_PATH)

        has_qrator = bool(_QRATOR_KEYS & set(cookies.keys()))
        return len(cookies), has_qrator


def _run_refresh():
    """Запускает async _do_refresh() в отдельном event loop (вызывается из потока)."""
    _set(running=True, last_status="running", last_msg="Запуск фонового обновления...")
    try:
        count, has_qrator = asyncio.run(_do_refresh())
        status = "ok" if has_qrator else "warn"
        msg = f"Сохранено {count} куков | Qrator: {'✓' if has_qrator else '✗ нет'}"
        _set(running=False, last_refresh_ts=time.time(), last_status=status, last_msg=msg)
        log.info("autorefresh OK: %s", msg)
    except Exception as e:
        _set(running=False, last_status="error", last_msg=str(e)[:300])
        log.error("autorefresh ERROR: %s", e)


# ── Планировщик ───────────────────────────────────────────────────────────────
def _scheduler_loop():
    log.info("Cookie autorefresh scheduler started (TTL=%ds, interval=%ds)",
             REFRESH_TTL_SEC, CHECK_INTERVAL_SEC)
    while True:
        next_ts = time.time() + CHECK_INTERVAL_SEC
        _set(next_check_ts=next_ts)
        time.sleep(CHECK_INTERVAL_SEC)

        s = _get_state()
        if not s["enabled"]:
            continue
        if s["running"]:
            log.debug("autorefresh: already running, skip")
            continue
        if not _needs_refresh():
            log.debug("autorefresh: cookies fresh (age=%.0fs), skip", _cookie_age_sec() or 0)
            continue

        log.info("autorefresh: cookies stale — starting background refresh")
        threading.Thread(target=_run_refresh, daemon=True, name="cookie-refresh").start()


def start_scheduler():
    """Запускает планировщик. Вызывается один раз при старте Flask."""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name="cookie-autorefresh-sched")
    t.start()
    log.info("Cookie autorefresh scheduler thread started")


# ── Flask Blueprint ────────────────────────────────────────────────
from flask import Blueprint, jsonify
auto_bp = Blueprint("cookie_auto", __name__)

@auto_bp.route("/api/cookie/auto/status", methods=["GET"])
def api_cookie_auto_status():
    s = _get_state()
    return jsonify({
        "ok":                 True,
        "enabled":            s["enabled"],
        "running":            s["running"],
        "last_status":        s["last_status"],
        "last_msg":           s["last_msg"],
        "last_refresh_ts":    s["last_refresh_ts"],
        "next_check_ts":      s["next_check_ts"],
        "cookie_age_sec":     _cookie_age_sec(),
        "needs_refresh":      _needs_refresh(),
        "refresh_ttl_sec":    REFRESH_TTL_SEC,
        "check_interval_sec": CHECK_INTERVAL_SEC,
    })

@auto_bp.route("/api/cookie/auto/enable", methods=["POST"])
def api_cookie_auto_enable():
    _set(enabled=True)
    return jsonify({"ok": True, "enabled": True})

@auto_bp.route("/api/cookie/auto/disable", methods=["POST"])
def api_cookie_auto_disable():
    _set(enabled=False)
    return jsonify({"ok": True, "enabled": False})

@auto_bp.route("/api/cookie/auto/trigger", methods=["POST"])
def api_cookie_auto_trigger():
    s = _get_state()
    if s["running"]:
        return jsonify({"ok": False, "error": "Уже запущено"})
    threading.Thread(target=_run_refresh, daemon=True, name="cookie-refresh-manual").start()
    return jsonify({"ok": True, "msg": "Фоновое обновление запущено"})


def register_routes(app):
    """Регистрирует Blueprint. Повторный вызов игнорируется Flask."""
    app.register_blueprint(auto_bp)
