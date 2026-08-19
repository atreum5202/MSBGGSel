"""
parser/routes.py
================
Flask blueprint с эндпоинтами для парсера.

Регистрируется в app.py через:
    from parser.routes import parser_bp
    app.register_blueprint(parser_bp)

Эндпоинты:
  POST /api/parser/start              — запуск
  POST /api/parser/stop               — остановка
  GET  /api/parser/status             — текущий статус + статистика
  GET  /api/parser/products           — список сохранённых товаров
  GET  /api/parser/products/<id>      — один товар
  DELETE /api/parser/products/<id>    — удалить
  GET  /api/parser/runs               — история запусков
  GET  /api/parser/runs/<id>/log      — лог запуска
  GET  /api/parser/stats              — сводная статистика
  GET  /api/parser/config             — настройки
  GET  /api/parser/gemini/status      — статус ключей Gemini (ротация)
  POST /api/parser/gemini/reset       — сбросить статус ключей
  POST /api/parser/gemini/test        — быстрый тест активного ключа
  GET  /api/parser/msb/status         — состояние MSB антидетект-менеджера
  POST /api/parser/msb/refresh/<id>   — refresh cookies через CDP
  GET  /api/parser/msb/rate           — snapshot AdaptiveRateLimiter
  POST /api/parser/msb/rate/reset     — сброс rate limiter
  GET  /api/parser/msb/groups         — список групп
  GET  /api/parser/msb/profile/<id>   — детали профиля
  POST /api/parser/msb/start/<id>     — запустить профиль
  POST /api/parser/msb/stop/<id>      — остановить профиль
  GET  /api/parser/telemetry/recent   — последние N событий
  GET  /api/parser/telemetry/stats    — сводка
  POST /api/parser/auto/start         — запустить авто-пилот (mode=test|turbo)
  POST /api/parser/auto/stop          — остановить авто-пилот
  GET  /api/parser/auto/status        — статус авто-пилота
  POST /api/parser/fullscan/start     — запустить полный скан (все категории, воркеры)
  POST /api/parser/fullscan/stop      — остановить полный скан
  GET  /api/parser/fullscan/status    — статус полного скана + прогресс воркеров
  GET  /api/parser/fullscan/categories           — список content_type категорий
  GET  /api/parser/fullscan/category-stats        — статистика категорий из БД
  POST /api/parser/fullscan/category-stats/scan   — запустить скан всех категорий
  GET  /api/parser/fullscan/category-stats/status — статус скана
  POST /api/parser/section-scan/start  — сканирование по подкатегориям (id_section) из БД
  POST /api/parser/section-scan/stop   — остановить
  GET  /api/parser/section-scan/status — статус + progress_pct
  POST /api/parser/price-scan/start    — полный сбор каталога (~384k) через ценовые диапазоны
  POST /api/parser/price-scan/stop     — остановить
  GET  /api/parser/price-scan/status   — статус price scan
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from . import get_engine, get_db_path, init_db, MAX_QUANTITY_HARD_CAP
from .parser_engine import (
    full_scan_start, full_scan_stop, full_scan_status,
    section_scan_start, section_scan_stop, section_scan_status,
    FULL_SCAN_CONTENT_TYPES, FULL_SCAN_CT_NAMES,
)
from .price_scan import (
    price_scan_start, price_scan_stop, price_scan_status,
)
from .category_catalog import get_leaf_category, search_leaf_categories

parser_bp = Blueprint("parser", __name__, url_prefix="/api/parser")

# Хосты CDN ggsel, с которых разрешено проксировать изображения товаров.
# ggsel.net проверяет Referer на своих CDN-хостах, поэтому браузер не может
# загрузить эти картинки напрямую со страницы, открытой на localhost —
# нужно прокидывать запрос через backend с правильным Referer.
_IMAGE_PROXY_ALLOWED_HOSTS = {"img.ggsel.net", "static.ggsel.com", "static.ggsel.net"}
_IMAGE_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "images" / "cache"
_BROWSER_IMAGE_JOBS: Dict[str, Dict[str, Any]] = {}
_BROWSER_IMAGE_JOBS_LOCK = threading.Lock()


def _browser_job_set(job_id: str, **patch: Any) -> Dict[str, Any]:
    with _BROWSER_IMAGE_JOBS_LOCK:
        job = _BROWSER_IMAGE_JOBS.get(job_id)
        if not job:
            job = {"job_id": job_id, "status": "queued", "stage": "", "message": "", "updated_at": time.time()}
            _BROWSER_IMAGE_JOBS[job_id] = job
        job.update(patch)
        job["updated_at"] = time.time()
        return dict(job)


def _browser_job_get(job_id: str) -> Dict[str, Any] | None:
    with _BROWSER_IMAGE_JOBS_LOCK:
        job = _BROWSER_IMAGE_JOBS.get(job_id)
        return dict(job) if job else None


def _browser_job_cleanup(max_age_sec: int = 3600) -> None:
    now = time.time()
    with _BROWSER_IMAGE_JOBS_LOCK:
        old_ids = [jid for jid, job in _BROWSER_IMAGE_JOBS.items() if now - float(job.get("updated_at") or 0) > max_age_sec]
        for jid in old_ids:
            _BROWSER_IMAGE_JOBS.pop(jid, None)


def _ensure_db():
    try:
        init_db()
    except Exception:
        pass


@parser_bp.before_request
def _before_request():
    _ensure_db()


# ═══════════════════════════════════════════════════════════════════════════
#  Start / Stop / Status
# ═══════════════════════════════════════════════════════════════════════════
@parser_bp.route("/start", methods=["POST"])
def start():
    body = request.get_json(silent=True) or {}
    engine = get_engine()

    if engine.is_running():
        return jsonify({
            "ok": False,
            "error": "Парсер уже запущен. Дождись окончания или нажми Stop.",
        }), 409

    query = (body.get("query") or "").strip()
    category_id = body.get("category_id")
    category = ""
    if category_id is not None and str(category_id).strip() != "":
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "category_id должен быть числом"}), 400
        leaf = get_leaf_category(category_id)
        if not leaf:
            return jsonify({
                "ok": False,
                "error": f"category_id={category_id} не найден в categories_cache.json — обновите кэш",
            }), 400
        category = str(category_id)
    # Поддержка category как slug строкой (например 'spotify-premium')
    elif body.get("category"):
        category = str(body["category"]).strip()
    try:
        quantity = int(body.get("quantity") or 20)
    except (TypeError, ValueError):
        quantity = 20
    try:
        max_pages = int(body.get("max_pages") or 3)
    except (TypeError, ValueError):
        max_pages = 3
    run_ai = bool(body.get("run_ai", False))

    if not query and not category:
        return jsonify({
            "ok": False,
            "error": "Укажи query или category_id — что парсить.",
        }), 400

    result = engine.start(
        query=query,
        category=category,
        quantity=quantity,
        max_pages=max_pages,
        run_ai_enrichment=run_ai,
    )
    code = 200 if result.get("ok") else 400
    result["hard_cap_quantity"] = MAX_QUANTITY_HARD_CAP
    return jsonify(result), code


@parser_bp.route("/stop", methods=["POST"])
def stop():
    return jsonify(get_engine().stop())


@parser_bp.route("/status", methods=["GET"])
def status():
    eng = get_engine()
    s = eng.status()
    s["is_running"] = eng.is_running()
    s["leaf_categories_count"] = len(search_leaf_categories(limit=200))
    s["hard_cap_quantity"] = MAX_QUANTITY_HARD_CAP
    return jsonify(s)


@parser_bp.route("/config", methods=["GET"])
def config():
    try:
        from config import dump
        cfg = dump()
    except Exception:
        cfg = {}
    cfg.setdefault("leaf_categories", search_leaf_categories(limit=100))
    cfg.setdefault("hard_cap_quantity", MAX_QUANTITY_HARD_CAP)
    # Добавляем сводку по Gemini ключам
    try:
        from .content_gen import get_key_pool
        cfg["gemini"] = get_key_pool().summary()
    except Exception:
        cfg["gemini"] = {"available": False}
    return jsonify(cfg)


# ═══════════════════════════════════════════════════════════════════════════
#  Gemini key management
# ═══════════════════════════════════════════════════════════════════════════
@parser_bp.route("/gemini/status", methods=["GET"])
def gemini_status():
    """
    Статус всех Gemini API ключей.
    Возвращает:
      {
        total: N,
        ok: N,
        exhausted: N,
        error: N,
        available: bool,
        keys: [{index, masked, status, last_error, last_used_at, fail_count, success_count}]
      }
    """
    from .content_gen import get_key_pool
    return jsonify(get_key_pool().summary())


@parser_bp.route("/gemini/reset", methods=["POST"])
def gemini_reset():
    """
    Сбросить статус ключей → ok.
    Body (опционально): {"index": 2} — сбросить конкретный ключ.
    Без body — сбросить все.
    """
    from .content_gen import get_key_pool
    body = request.get_json(silent=True) or {}
    pool = get_key_pool()
    if "index" in body:
        pool.reset_key(int(body["index"]))
        return jsonify({"ok": True, "reset": f"key #{body['index']}"})
    else:
        pool.reset_all()
        return jsonify({"ok": True, "reset": "all"})


@parser_bp.route("/gemini/test", methods=["POST", "GET"])
def gemini_test():
    """
    Быстрый тест Gemini — отправляет минимальный запрос и возвращает результат.
    Полезно для проверки ключей из UI без запуска парсера.
    """
    from .content_gen import generate_product_card, get_key_pool
    body = {}
    if request.method == "POST":
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}
    
    title        = body.get("title",        "Windows 11 Pro ключ активации")
    category     = body.get("category",     "Программное обеспечение")
    price        = float(body.get("price",  800.0))
    sales_count  = int(body.get("sales_count", 42))
    seller_rating= float(body.get("seller_rating", 4.8))
    reviews_count= int(body.get("reviews_count", 15))

    try:
        result = generate_product_card(
            title=title,
            category=category,
            price=price,
            sales_count=sales_count,
            seller_rating=seller_rating,
            reviews_count=reviews_count,
        )
        pool_status = get_key_pool().summary()
        return jsonify({"ok": True, "result": result, "gemini_pool": pool_status})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Products
# ═══════════════════════════════════════════════════════════════════════════
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


@parser_bp.route("/products", methods=["GET"])
def list_products():
    page  = max(1, request.args.get("page", 1, type=int))
    limit = min(200, max(1, request.args.get("limit", 50, type=int)))
    status_filter = request.args.get("status", "pending").strip()
    search = request.args.get("q", "").strip()

    offset = (page - 1) * limit
    where = []
    params: List[Any] = []
    if status_filter:
        if status_filter in ["pending", "approved", "rejected", "published"]:
            where.append("approval_status = ?")
        else:
            where.append("status = ?")
        params.append(status_filter)
    if search:
        where.append("(title LIKE ? OR generated_title LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM parsed_products {where_sql}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM parsed_products {where_sql} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({
        "page": page, "limit": limit, "total": total,
        "items": [_row_to_dict(r) for r in rows],
    })


@parser_bp.route("/image-proxy", methods=["GET"])
def image_proxy():
    """
    GET /api/parser/image-proxy?url=<encoded_url>

    Проксирует изображение товара с CDN ggsel (img.ggsel.net и т.п.).
    Нужен, потому что при прямой загрузке из браузера со страницы
    админки (localhost) Referer не совпадает с ggsel.net, и CDN отдаёт 403/пустой
    ответ (защита от хотлинкинга). Здесь запрос делается сервером с
    правильным Referer, а результат кэшируется на диске.
    """
    from flask import abort, send_file

    raw_url = request.args.get("url", "")
    if not raw_url:
        abort(400)
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _IMAGE_PROXY_ALLOWED_HOSTS:
        abort(403)

    _IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(parsed.path)[1][:8] or ".jpg"
    cache_key = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
    cache_path = _IMAGE_CACHE_DIR / f"{cache_key}{ext}"

    if not cache_path.is_file():
        # Используем curl-cffi вместо requests: CDN ggsel проверяет TLS-отпечаток;
        # обычный requests отличается от Chrome и даёт 403.
        try:
            try:
                from curl_cffi import requests as _cffi_rq
                import random as _random
                from parser.parser_engine import _UA_POOL
                _entry = _random.choice(_UA_POOL)
                resp = _cffi_rq.get(
                    raw_url,
                    headers={
                        "Referer": "https://ggsel.net/",
                        "User-Agent": _entry["ua"],
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    },
                    impersonate=_entry["impersonate"],
                    timeout=12,
                    allow_redirects=True,
                )
            except ImportError:
                # curl_cffi нет — fallback на обычный requests
                import requests as _rq
                resp = _rq.get(
                    raw_url,
                    headers={
                        "Referer": "https://ggsel.net/",
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"
                        ),
                        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    },
                    timeout=10,
                )
            if resp.status_code != 200 or not resp.content:
                abort(502)
            cache_path.write_bytes(resp.content)
        except Exception:
            abort(502)

    return send_file(str(cache_path), max_age=86400)


@parser_bp.route("/products/<path:product_id>", methods=["GET"])
def get_product(product_id: str):
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "product": _row_to_dict(row)})


@parser_bp.route("/products/<path:product_id>", methods=["DELETE"])
def delete_product(product_id: str):
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        cur = conn.execute(
            "DELETE FROM parsed_products WHERE product_id = ?", (product_id,)
        )
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    if deleted == 0:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "deleted": deleted})


@parser_bp.route("/products", methods=["DELETE"])
def delete_all_products():
    """
    DELETE /api/parser/products?status=parsed&confirm=YES
    Без `confirm=YES` отвечает 400 — защита от случайного клика.
    По умолчанию чистит ВСЕ товары. Опциональный ?status= чистит только этот статус.
    Возвращает: { ok: true, deleted: N }
    """
    if request.args.get("confirm") != "YES":
        return jsonify({
            "ok": False,
            "error": "Требуется confirm=YES для очистки всех товаров"
        }), 400

    status_filter = (request.args.get("status") or "").strip()
    where = ""
    params: List[Any] = []
    if status_filter:
        where = "WHERE status = ? OR approval_status = ?"
        params = [status_filter, status_filter]

    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        cur = conn.execute(f"DELETE FROM parsed_products {where}", params)
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": deleted})


@parser_bp.route("/products/<path:product_id>", methods=["PATCH"])
def patch_product(product_id: str):
    """
    PATCH /api/parser/products/<id>
    Обновить редактируемые поля товара (generated_title, generated_desc,
    generated_tags, my_price) перед одобрением.
    Body: { "generated_title": "...", "generated_desc": "...",
            "generated_tags": "...", "my_price": 999.0 }
    """
    body = request.get_json(silent=True) or {}
    allowed = {"generated_title", "generated_desc", "generated_tags", "my_price"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return jsonify({"ok": False, "error": "No valid fields to update"}), 400

    sets = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [datetime.utcnow().isoformat(), product_id]
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        cur = conn.execute(
            f"UPDATE parsed_products SET {sets}, updated_at = ? WHERE product_id = ?",
            vals,
        )
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "updated": list(updates.keys())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@parser_bp.route("/products/<path:product_id>/approve-for-draft", methods=["POST"])
def approve_product_for_draft(product_id: str):
    """
    Одобрение товара для создания черновика.
    Меняет статус на approved_by_owner без публикации.
    """
    from .event_logger import get_event_logger
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        
        product = _row_to_dict(row)
        current_status = product.get("status", "parsed")
        
        # Проверяем, что товар находится в подходящем статусе
        if current_status not in ["parsed", "economics_checked", "ai_recommended"]:
            return jsonify({
                "ok": False, 
                "error": f"Нельзя одобрить товар со статусом '{current_status}'"
            }), 400
        
        # Проверяем экономику
        if not product.get("economy_complete"):
            return jsonify({
                "ok": False,
                "error": "Экономика не рассчитана. Не хватает параметров для расчёта."
            }), 400
        
        # Меняем статус
        conn.execute(
            "UPDATE parsed_products SET status = 'approved_by_owner', updated_at = ? WHERE product_id = ?",
            (datetime.utcnow().isoformat(), product_id),
        )
        conn.commit()
        
        # Логируем событие
        logger = get_event_logger()
        logger.log_product_event(
            product_id=product_id,
            stage="approved_by_owner",
            level="info",
            message="Товар одобрен владельцем для создания черновика"
        )
        
        return jsonify({
            "ok": True, 
            "status": "approved_by_owner",
            "message": "Товар одобрен для создания черновика"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@parser_bp.route("/products/<path:product_id>/create-draft", methods=["POST"])
def create_product_draft(product_id: str):
    """
    Создание черновика оффера из одобренного товара.
    Создаёт оффер со статусом draft в GGSEL.
    """
    from .ggsel_publisher import GGselPublisher, PublishError
    from .event_logger import get_event_logger
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        
        product = _row_to_dict(row)
        current_status = product.get("status", "parsed")
        approval_status = product.get("approval_status", "pending")
        
        # Проверяем статус
        if approval_status != "approved" and current_status != "approved_by_owner":
            return jsonify({
                "ok": False,
                "error": f"Товар должен иметь статус одобрения approved, текущий: {approval_status}"
            }), 400
        
        # Создаём черновик оффера
        pub = GGselPublisher()
        offer_id = pub.create_offer(product)
        
        # Добавляем товар в оффер
        pub.add_product(offer_id, product)
        
        # Обновляем статус товара
        conn.execute(
            "UPDATE parsed_products SET status = 'draft_created', offer_id = ?, updated_at = ? WHERE product_id = ?",
            (offer_id, datetime.utcnow().isoformat(), product_id),
        )
        conn.commit()
        
        # Логируем событие
        logger = get_event_logger()
        logger.log_product_event(
            product_id=product_id,
            stage="draft_created",
            level="info",
            message=f"Создан черновик оффера {offer_id}",
            technical_detail=f"offer_id={offer_id}"
        )
        
        return jsonify({
            "ok": True,
            "status": "draft_created",
            "offer_id": offer_id,
            "message": "Черновик оффера создан"
        })
    except PublishError as e:
        return jsonify({"ok": False, "error": e.message}), e.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@parser_bp.route("/products/<path:product_id>/publish", methods=["POST"])
def publish_product(product_id: str):
    """
    Публикация черновика оффера.
    Переводит оффер из статуса draft в active.
    Требует явного подтверждения владельца.
    """
    from .ggsel_publisher import GGselPublisher, PublishError
    from .event_logger import get_event_logger
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        
        product = _row_to_dict(row)
        current_status = product.get("status", "parsed")
        offer_id = product.get("offer_id")
        
        # Проверяем статус
        if current_status != "draft_created":
            return jsonify({
                "ok": False,
                "error": f"Товар должен быть в статусе draft_created, текущий: {current_status}"
            }), 400
        
        if not offer_id:
            return jsonify({
                "ok": False,
                "error": "Нет связанного оффера. Сначала создайте черновик."
            }), 400
        
        # Проверяем явное подтверждение
        data = request.get_json() or {}
        confirmed = data.get("confirmed", False)
        if not confirmed:
            return jsonify({
                "ok": False,
                "error": "Требуется явное подтверждение публикации. Установите confirmed=true в запросе.",
                "requires_confirmation": True
            }), 400
        
        # Публикуем оффер
        pub = GGselPublisher()
        pub.publish_offer(offer_id)
        
        # Обновляем статус товара
        conn.execute(
            "UPDATE parsed_products SET status = 'published', approval_status = 'published', updated_at = ? WHERE product_id = ?",
            (datetime.utcnow().isoformat(), product_id),
        )
        conn.commit()
        
        # Логируем событие
        logger = get_event_logger()
        logger.log_product_event(
            product_id=product_id,
            stage="published",
            level="info",
            message=f"Оффер {offer_id} опубликован с явным подтверждением",
            technical_detail=f"offer_id={offer_id}"
        )
        
        return jsonify({
            "ok": True,
            "status": "published",
            "offer_id": offer_id,
            "message": "Оффер опубликован"
        })
    except PublishError as e:
        return jsonify({"ok": False, "error": e.message}), e.status_code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


@parser_bp.route("/products/<path:product_id>/generate-image", methods=["POST"])
def generate_product_image(product_id: str):
    """
    POST /api/parser/products/<id>/generate-image
    Генерирует AI-картинку через Gemini Imagen для товара.
    Сохраняет изображение в static/generated/ и обновляет generated_image_url в БД.
    Возвращает: { ok: true, image_url: "/static/generated/<pid>.jpg" }
    """
    import base64, os as _os, pathlib

    # Читаем товар
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        product = dict(row)
    finally:
        conn.close()

    title = product.get("generated_title") or product.get("title", "")
    desc  = product.get("generated_desc") or product.get("original_desc", "")
    prompt = (
        f"Professional product photo for digital marketplace. "
        f"Product: {title}. "
        f"Description context: {desc[:150] if desc else ''}. "
        f"Clean white or gradient background, studio lighting, high quality, "
        f"no text overlays, no watermarks, e-commerce style."
    )

    try:
        import google.generativeai as genai
        from .content_gen import get_key_pool

        pool = get_key_pool()
        if not pool.available():
            return jsonify({"ok": False, "error": "Нет доступных Gemini ключей"}), 503

        ks = pool.get_next_key()
        if not ks:
            return jsonify({"ok": False, "error": "Нет доступных Gemini ключей"}), 503
        api_key = ks.key
        genai.configure(api_key=api_key)

        # Используем Imagen через Gemini API
        try:
            imagen = genai.ImageGenerationModel("imagen-3.0-generate-002")
            result = imagen.generate_images(
                prompt=prompt,
                number_of_images=1,
                aspect_ratio="1:1",
            )
            img_bytes = result.images[0]._pil_image
            import io
            buf = io.BytesIO()
            img_bytes.save(buf, format="JPEG", quality=90)
            img_data = buf.getvalue()
        except Exception:
            # Fallback: gemini-2.0-flash-preview-image-generation
            model = genai.GenerativeModel("gemini-2.0-flash-preview-image-generation")
            resp = model.generate_content(
                prompt,
                generation_config={"response_modalities": ["IMAGE"]},
            )
            # Извлекаем байты изображения из ответа
            img_data = None
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    img_data = base64.b64decode(part.inline_data.data)
                    break
            if not img_data:
                return jsonify({"ok": False, "error": "Gemini не вернул изображение"}), 500

        # Сохраняем файл: static/products/{safe_pid}/ai.jpg
        safe_pid   = "".join(c if c.isalnum() else "_" for c in product_id)
        prod_dir   = pathlib.Path(__file__).resolve().parent.parent / "static" / "products" / safe_pid
        prod_dir.mkdir(parents=True, exist_ok=True)
        filepath   = prod_dir / "ai.jpg"
        filepath.write_bytes(img_data)

        image_url = f"/static/products/{safe_pid}/ai.jpg"

        # Обновляем БД
        conn2 = sqlite3.connect(get_db_path(), timeout=10.0)
        try:
            conn2.execute(
                "UPDATE parsed_products SET generated_image_url = ?, updated_at = ? WHERE product_id = ?",
                (image_url, datetime.utcnow().isoformat(), product_id),
            )
            conn2.commit()
        finally:
            conn2.close()

        return jsonify({"ok": True, "image_url": image_url})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:400]}), 500


# ───────────────────────────────────────────────────────────────────────────
#  Gemini Browser Group config helpers
# ───────────────────────────────────────────────────────────────────────────

def _read_parser_cfg() -> dict:
    """Read parser_config.json, return dict (never raises)."""
    import json as _json
    from pathlib import Path as _Path
    cfg_path = _Path(__file__).resolve().parent.parent / "parser_config.json"
    try:
        with open(cfg_path, encoding="utf-8") as _f:
            return _json.load(_f)
    except Exception:
        return {}


def _write_parser_cfg(cfg: dict) -> None:
    """Write parser_config.json atomically."""
    import json as _json
    from pathlib import Path as _Path
    cfg_path = _Path(__file__).resolve().parent.parent / "parser_config.json"
    tmp = cfg_path.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cfg_path)


@parser_bp.route("/gemini/browser-group", methods=["GET"])
def gemini_browser_group_get():
    """GET /api/parser/gemini/browser-group — текущая настройка группы."""
    cfg = _read_parser_cfg()
    return jsonify({"ok": True, "group_name": cfg.get("gemini_browser_group", "")})


@parser_bp.route("/gemini/browser-group", methods=["POST"])
def gemini_browser_group_set():
    """
    POST /api/parser/gemini/browser-group
    Body: {"group_name": "МояГруппа"}
    Сохраняет имя группы MoreLogin для браузерной генерации Gemini.
    """
    body = request.get_json(silent=True) or {}
    group_name = (body.get("group_name") or "").strip()
    cfg = _read_parser_cfg()
    cfg["gemini_browser_group"] = group_name
    try:
        _write_parser_cfg(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "group_name": group_name})


@parser_bp.route("/gemini/browser-group/profiles", methods=["GET"])
def gemini_browser_group_profiles():
    """
    GET /api/parser/gemini/browser-group/profiles
    Возвращает список профилей в настроенной группе.
    """
    import asyncio as _aio
    from .msb_client import MsbClient

    cfg = _read_parser_cfg()
    group_name = cfg.get("gemini_browser_group", "").strip()
    if not group_name:
        return jsonify({"ok": False, "error": "Группа не задана"}), 400

    async def _fetch():
        async with MsbClient() as ml:
            return await ml.get_profiles(group_name=group_name)

    try:
        profiles = _aio.run(_fetch())
        return jsonify({
            "ok": True,
            "group_name": group_name,
            "count": len(profiles),
            "profiles": [{"id": p["envId"], "name": p["name"]} for p in profiles],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]}), 500


def _run_browser_generate_image_job(job_id: str, product_id: str, launch_mode: str = "background") -> None:
    import asyncio as _aio
    import pathlib
    import random as _random
    import httpx as _httpx
    from .msb_client import MsbClient
    from .gemini_browser import restyle_image_sync

    _browser_job_set(job_id, status="running", stage="prepare", message="Читаю товар и готовлю задачу…")

    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT product_id, title, generated_title, image_url, generated_image_url "
            "FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            _browser_job_set(job_id, status="error", stage="prepare", error="Товар не найден", message="Товар не найден")
            return
        product = dict(row)
    finally:
        conn.close()

    source_url = product.get("image_url") or product.get("generated_image_url")
    if not source_url:
        _browser_job_set(job_id, status="error", stage="prepare", error="У товара нет исходного фото для генерации", message="Нет исходного фото")
        return

    title = product.get("generated_title") or product.get("title") or ""
    safe_pid = "".join(c if c.isalnum() else "_" for c in product_id)
    tmp_dir = pathlib.Path(__file__).resolve().parent.parent / "static" / "generated"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prod_dir = pathlib.Path(__file__).resolve().parent.parent / "static" / "products" / safe_pid
    prod_dir.mkdir(parents=True, exist_ok=True)

    _browser_job_set(job_id, stage="download", message="Скачиваю исходное фото товара…")
    try:
        if source_url.startswith("/"):
            local_file = pathlib.Path(__file__).resolve().parent.parent / source_url.lstrip("/")
            source_bytes = local_file.read_bytes()
        else:
            r = _httpx.get(
                source_url, timeout=30, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://ggsel.net/"}
            )
            r.raise_for_status()
            source_bytes = r.content
    except Exception as e:
        _browser_job_set(job_id, status="error", stage="download", error=f"Не удалось скачать фото товара: {e}", message="Ошибка скачивания фото")
        return

    src_filename = f"{safe_pid}_bgsrc.jpg"
    src_filepath = tmp_dir / src_filename
    src_filepath.write_bytes(source_bytes)

    cfg = _read_parser_cfg()
    gemini_group = cfg.get("gemini_browser_group", "").strip()
    local_port = cfg.get("server", {}).get("local_port", 5000)
    profile_id = None

    try:
        _browser_job_set(job_id, stage="profile", message="Выбираю профиль Gemini…")
        if gemini_group:
            async def _get_gemini_profiles():
                async with MsbClient() as ml:
                    return await ml.get_profiles(group_name=gemini_group)
            gemini_profiles = _aio.run(_get_gemini_profiles())
            if not gemini_profiles:
                _browser_job_set(job_id, status="error", stage="profile", error=f"Группа '{gemini_group}' пуста или не найдена в MSB. Проверь название группы в MSB UI.", message="Не найдены профили Gemini")
                return
            profile_id = _random.choice(gemini_profiles)["envId"]
        else:
            profile_ids = cfg.get("pool", {}).get("profile_ids", [])
            profile_id = profile_ids[0] if profile_ids else None

        if not profile_id:
            _browser_job_set(job_id, status="error", stage="profile", error="Не найден профиль для генерации. Укажи Gemini-группу в настройках MSB или заполни pool.profile_ids.", message="Профиль для генерации не найден")
            return

        image_url_local = f"http://127.0.0.1:{local_port}/static/generated/{src_filename}"
        prompt = (
            f"I'm showing you a product photo from a competitor's marketplace listing. "
            f"Please generate a NEW, UNIQUE promotional image for the SAME product "
            f"that I can use in MY online shop. "
            f"Requirements: keep the product clearly recognizable, "
            f"make it look premium and eye-catching, "
            f"use a clean white or soft gradient background, "
            f"professional studio lighting, sharp focus on the product. "
            f"NO text, NO watermarks, NO competitor logos, NO people. "
            f"Product name: {title}."
        )
        result_filepath = prod_dir / "ai.jpg"

        _browser_job_set(job_id, stage="browser", message="Запускаю профиль CloakBrowser и открываю Gemini…")
        # engine="cloakbrowser" — обязательно для Gemini/Google:
        #   66 C++-патчей в бинарнике: canvas, WebGL, fonts, GPU, WebRTC и др.
        #   Таймзон, локаль и вьюпорт через бинарные флаги (a не CDP Emulation домен).
        # launch_mode="background" — headed rendering (меньше риска детекта,
        #   чем настоящий headless), best-effort сворачивает окно.
        restyle_image_sync(
            image_url=image_url_local,
            prompt_text=prompt,
            profile_id=profile_id,
            save_path=str(result_filepath),
            timeout=180,
            status_callback=lambda msg: _browser_job_set(job_id, status="running", stage="browser", message=msg),
            headless=False,
            launch_mode=launch_mode,
            engine="cloakbrowser",   # 66 C++ fingerprint patches, without CDP Emulation domain
        )

        if not result_filepath.exists():
            _browser_job_set(job_id, status="error", stage="save", error="Браузер не вернул изображение (файл не создан)", message="Файл результата не создан")
            return

        web_url = f"/static/products/{safe_pid}/ai.jpg"
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        try:
            conn.execute(
                "UPDATE parsed_products SET generated_image_url = ?, updated_at = ? WHERE product_id = ?",
                (web_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), product_id),
            )
            conn.commit()
        finally:
            conn.close()

        _browser_job_set(job_id, status="done", stage="done", message="Картинка готова", image_url=web_url, product_id=product_id)
    except RuntimeError as e:
        err_msg = str(e)
        login_required = any(kw in err_msg.lower() for kw in (
            "не залогинен", "not signed in", "sign_in_button",
            "accounts.google.com", "нужно войти", "сессия истекла",
        ))
        _browser_job_set(job_id, status="error", stage="browser", error=err_msg, login_required=login_required, message=("Нужен вход в Google" if login_required else "Ошибка браузерной генерации"))
    except Exception as e:
        _browser_job_set(job_id, status="error", stage="browser", error=str(e)[:400], message="Непредвиденная ошибка генерации")
    finally:
        try:
            src_filepath.unlink()
        except Exception:
            pass
        _browser_job_cleanup()


@parser_bp.route("/products/<path:product_id>/browser-generate-image", methods=["POST"])
def browser_generate_image(product_id: str):
    body = request.get_json(silent=True) or {}
    launch_mode = body.get("launch_mode", "background")
    if launch_mode not in ("background", "visible", "minimized"):
        launch_mode = "background"
    job_id = uuid.uuid4().hex
    _browser_job_set(job_id, status="queued", stage="queued", message="Задача поставлена в очередь", product_id=product_id)
    t = threading.Thread(target=_run_browser_generate_image_job, args=(job_id, product_id, launch_mode), daemon=True)
    t.start()
    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": "queued",
        "launch_mode": launch_mode,
        "message": "Генерация запущена в фоне" if launch_mode == "background" else "Генерация запущена — браузер откроется",
    })


@parser_bp.route("/browser-image-jobs/<job_id>", methods=["GET"])
def browser_image_job_status(job_id: str):
    job = _browser_job_get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Задача не найдена"}), 404
    return jsonify({"ok": True, "job": job})


@parser_bp.route("/products/<path:product_id>/restyle-image", methods=["POST"])
def restyle_product_image(product_id: str):
    """
    POST /api/parser/products/<id>/restyle-image
    Берёт оригинальное фото товара (image_url из БД), отправляет в Gemini
    вместе с промптом «сделай красивое фото для маркетплейса».
    Возвращает: { ok: true, image_url: "/static/generated/<pid>_restyled.jpg" }
    """
    import base64 as _b64, pathlib, io

    # Читаем товар из БД
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT product_id, title, generated_title, original_desc, image_url, generated_image_url "
            "FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Товар не найден"}), 404
        product = dict(row)
    finally:
        conn.close()

    # URL фото — приоритет: оригинал, затем уже сгенерированный
    source_url = product.get("image_url") or product.get("generated_image_url")
    if not source_url:
        return jsonify({"ok": False, "error": "У товара нет исходного фото для обработки"}), 400

    title = product.get("generated_title") or product.get("title") or ""

    # Скачиваем оригинальное фото
    try:
        import httpx as _httpx
        r = _httpx.get(source_url, timeout=20, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        source_bytes = r.content
        # Определяем mime
        ct = r.headers.get("content-type", "image/jpeg")
        mime = ct.split(";")[0].strip() if ct else "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/jpeg"
    except Exception as e:
        return jsonify({"ok": False, "error": f"Не удалось скачать фото: {e}"}), 502

    # Gemini: отправляем фото + промпт → получаем новое фото
    try:
        import google.generativeai as genai
        from .content_gen import get_key_pool

        pool = get_key_pool()
        ks = pool.get_next_key()
        if not ks:
            return jsonify({"ok": False, "error": "Нет доступных Gemini ключей"}), 503

        genai.configure(api_key=ks.key)

        prompt_text = (
            f"This is a product photo from a marketplace. "
            f"Product name: {title}. "
            f"Please restyle this image: keep the main product clearly visible, "
            f"improve the background (clean white or subtle gradient), "
            f"enhance lighting and colors, make it look professional and appealing "
            f"for an e-commerce listing. "
            f"Output ONLY the restyled product image, no text, no watermarks."
        )

        img_data_out = None

        # Попытка 1: gemini-2.0-flash-preview-image-generation (vision+generation)
        try:
            model = genai.GenerativeModel("gemini-2.0-flash-preview-image-generation")
            img_part = {
                "inline_data": {
                    "mime_type": mime,
                    "data": _b64.b64encode(source_bytes).decode()
                }
            }
            resp = model.generate_content(
                [img_part, prompt_text],
                generation_config={"response_modalities": ["IMAGE", "TEXT"]},
            )
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                    img_data_out = _b64.b64decode(part.inline_data.data)
                    break
        except Exception as e1:
            pass  # fallback ниже

        # Попытка 2: gemini-1.5-flash — описание → Imagen текст→фото
        if not img_data_out:
            try:
                # Сначала описываем товар через vision
                desc_model = genai.GenerativeModel("gemini-1.5-flash")
                img_part = {
                    "inline_data": {
                        "mime_type": mime,
                        "data": _b64.b64encode(source_bytes).decode()
                    }
                }
                desc_resp = desc_model.generate_content([
                    img_part,
                    "Describe this product image in one sentence for an image generation prompt. Focus on the product itself, not the background."
                ])
                product_desc = desc_resp.text.strip()[:200]

                # Затем генерируем через Imagen
                imagen = genai.ImageGenerationModel("imagen-3.0-generate-002")
                imagen_prompt = (
                    f"Professional e-commerce product photo. {product_desc}. "
                    f"Product name: {title}. "
                    f"Clean white background, studio lighting, sharp focus, high resolution, "
                    f"no text, no watermarks, no people."
                )
                result = imagen.generate_images(
                    prompt=imagen_prompt,
                    number_of_images=1,
                    aspect_ratio="1:1",
                )
                pil_img = result.images[0]._pil_image
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=92)
                img_data_out = buf.getvalue()
            except Exception as e2:
                pool.mark_error(ks, f"restyle fallback: {e2}")
                return jsonify({"ok": False, "error": f"Gemini не смог обработать фото: {e2}"}), 500

        pool.mark_success(ks)

        # Сохраняем результат
        static_dir = pathlib.Path(__file__).resolve().parent.parent / "static" / "generated"
        static_dir.mkdir(parents=True, exist_ok=True)
        safe_pid = "".join(c if c.isalnum() else "_" for c in product_id)
        filename = f"{safe_pid}_restyled.jpg"
        filepath = static_dir / filename
        filepath.write_bytes(img_data_out)

        image_url = f"/static/generated/{filename}"

        # Сохраняем в БД
        conn2 = sqlite3.connect(get_db_path(), timeout=10.0)
        try:
            conn2.execute(
                "UPDATE parsed_products SET generated_image_url = ?, updated_at = ? WHERE product_id = ?",
                (image_url, datetime.utcnow().isoformat(), product_id),
            )
            conn2.commit()
        finally:
            conn2.close()

        return jsonify({"ok": True, "image_url": image_url})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:400]}), 500


@parser_bp.route("/products/<path:product_id>/rewrite", methods=["POST"])
def rewrite_product_text(product_id: str):
    """
    POST /api/parser/products/<id>/rewrite
    Перегенерирует AI-тексты (title, desc, tags) для товара через Gemini.
    Обновляет generated_title, generated_desc, generated_tags в БД.
    Возвращает: { ok: true, product: {...} }
    """
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        product = dict(row)
    finally:
        conn.close()

    try:
        from .content_gen import enrich_product, get_key_pool
        pool = get_key_pool()
        if not pool.available():
            return jsonify({"ok": False, "error": "Нет доступных Gemini ключей"}), 503

        enriched = enrich_product(product)

        # Обновляем все сгенерированные поля
        updates = {
            "generated_title": enriched.get("generated_title"),
            "generated_desc":  enriched.get("generated_desc"),
            "generated_tags":  enriched.get("generated_tags"),
            "my_price":        enriched.get("my_price"),
            "sell_price":      enriched.get("my_price"), # Для совместимости с create-draft
            "profit_score":    enriched.get("profit_score"),
            "recommended_margin_pct": enriched.get("recommended_margin_pct"),
            "risk_level":      enriched.get("risk_level"),
            "risk_reason":     enriched.get("risk_reason"),
            "last_enriched_at": datetime.utcnow().isoformat(),
            "updated_at":      datetime.utcnow().isoformat(),
        }
        updates = {k: v for k, v in updates.items() if v is not None}

        conn2 = sqlite3.connect(get_db_path(), timeout=10.0)
        conn2.row_factory = sqlite3.Row
        try:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn2.execute(
                f"UPDATE parsed_products SET {sets} WHERE product_id = ?",
                (*updates.values(), product_id),
            )
            conn2.commit()
            row2 = conn2.execute(
                "SELECT * FROM parsed_products WHERE product_id = ?", (product_id,)
            ).fetchone()
            updated_product = dict(row2) if row2 else {**product, **updates}
        finally:
            conn2.close()

        return jsonify({"ok": True, "product": updated_product})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:400]}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Runs / Log
# ═══════════════════════════════════════════════════════════════════════════
@parser_bp.route("/runs", methods=["GET"])
def list_runs():
    page  = max(1, request.args.get("page", 1, type=int))
    limit = min(100, max(1, request.args.get("limit", 20, type=int)))
    offset = (page - 1) * limit
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM parser_runs").fetchone()["n"]
        rows = conn.execute(
            "SELECT * FROM parser_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({
        "page": page, "limit": limit, "total": total,
        "items": [_row_to_dict(r) for r in rows],
    })


@parser_bp.route("/runs/<int:run_id>/log", methods=["GET"])
def get_run_log(run_id: int):
    limit = min(1000, max(1, request.args.get("limit", 200, type=int)))
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM parser_log WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"run_id": run_id, "items": [_row_to_dict(r) for r in rows]})


@parser_bp.route("/stats", methods=["GET"])
def stats():
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM parsed_products GROUP BY status"
        ).fetchall()
        last_run = conn.execute(
            "SELECT * FROM parser_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM parsed_products"
        ).fetchone()["n"]
    finally:
        conn.close()
    return jsonify({
        "total_products": total,
        "by_status": [_row_to_dict(r) for r in by_status],
        "last_run": _row_to_dict(last_run) if last_run else None,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  MSB Integration
# ═══════════════════════════════════════════════════════════════════════════
def _msb_is_alive() -> bool:
    try:
        import httpx
        base = os.getenv("MSB_API_BASE", "http://127.0.0.1:17248")
        with httpx.Client(timeout=2.0) as c:
            r = c.get(f"{base}/health")
            return r.status_code < 500
    except Exception:
        return False


def _get_msb_status_sync() -> dict:
    import asyncio as _aio
    from .adaptive_rate_limiter import get_limiter
    from .telemetry import get_telemetry

    result = {
        "msb_running": _msb_is_alive(),
        "fetcher_used": "",
        "msb_unavailable_reason": None,
        "pool": None,
        "rate_limiter": None,
        "telemetry": None,
    }
    try:
        eng = get_engine()
        result["fetcher_used"] = eng._stats.get("fetcher_used", "")
        result["msb_unavailable_reason"] = eng._msb_unavailable_reason
    except Exception:
        pass
    try:
        from .profile_pool import get_pool_sync
        pool = get_pool_sync()
        if pool is not None and pool._initialized:
            result["pool"] = _aio.run(pool.status())
    except Exception as e:
        result["pool"] = {"error": str(e)[:200]}
    try:
        result["rate_limiter"] = get_limiter().summary()
    except Exception as e:
        result["rate_limiter"] = {"error": str(e)[:200]}
    try:
        result["telemetry"] = get_telemetry().stats()
    except Exception as e:
        result["telemetry"] = {"error": str(e)[:200]}
    return result


# /msb/status — см. msb_status() ниже (объединённый эндпоинт)


@parser_bp.route("/msb/refresh/<profile_id>", methods=["POST"])
def msb_refresh(profile_id: str):
    import asyncio as _aio
    from .msb_client import MsbClient

    async def _do_refresh():
        async with MsbClient() as ml:
            await ml.start_profile(profile_id)
            try:
                cookies = await ml.get_cookies(profile_id, domain="ggsel.net")
            finally:
                await ml.stop_profile(profile_id)
            return cookies

    try:
        cookies = _aio.run(_do_refresh())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

    if cookies:
        return jsonify({
            "ok": True,
            "profile_id": profile_id,
            "cookies_count": len(cookies),
            "cookies_names": list(cookies.keys())[:20],
        })
    return jsonify({"ok": False, "error": "refresh failed"}), 502


@parser_bp.route("/msb/rate", methods=["GET"])
def msb_rate():
    from .adaptive_rate_limiter import get_limiter
    try:
        return jsonify(get_limiter().summary())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@parser_bp.route("/msb/rate/reset", methods=["POST"])
def msb_rate_reset():
    from .adaptive_rate_limiter import get_limiter
    body = request.get_json(silent=True) or {}
    pid = body.get("profile_id") if isinstance(body, dict) else None
    try:
        get_limiter().reset(pid)
        return jsonify({"ok": True, "reset": pid or "all"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@parser_bp.route("/msb/reset-errors", methods=["POST"])
def msb_reset_errors():
    import asyncio as _aio
    from .profile_pool import get_pool_sync
    try:
        pool = get_pool_sync()
        if pool is None or not pool._initialized:
            return jsonify({"ok": False, "error": "pool not initialized"}), 503
        _aio.run(pool.reset_errors())
        return jsonify({"ok": True, "message": "счётчики сброшены"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  MSB (активный бэкенд антидетект-менеджера)
#  Эндпоинты работают через MsbClient и CDP
#  (см. parser/msb_client.py, parser/cdp_cookies.py).
# ═══════════════════════════════════════════════════════════════════════════

@parser_bp.route("/msb/status", methods=["GET"])
def msb_status():
    """
    GET /api/parser/msb/status
    Полное состояние MSB: health + профили + pool + rate limiter + telemetry.
    """
    import asyncio as _aio
    from .msb_client import MsbClient

    async def _do():
        async with MsbClient() as ml:
            health = await ml.health()
            try:
                profiles = await ml.get_profiles()
            except Exception as e:
                profiles = []
                health["profiles_error"] = str(e)[:200]
            try:
                running = await ml.get_running_profiles()
            except Exception:
                running = []
            try:
                groups = await ml.get_groups()
            except Exception:
                groups = []
            return {
                "backend": "msb",
                "health": health,
                "profiles_count": len(profiles),
                "running_count": len(running),
                "running": running[:50],
                "groups": [g.get("name") for g in groups],
            }

    try:
        msb_data = _aio.run(_do())
    except Exception as e:
        msb_data = {"backend": "msb", "health": {"ok": False, "error": str(e)[:200]}}

    # Добавляем pool / rate_limiter / telemetry из синхронного статуса
    msb_data.update(_get_msb_status_sync())
    return jsonify(msb_data)


@parser_bp.route("/morelogin/refresh/<profile_id>", methods=["POST"])
def morelogin_refresh(profile_id: str):
    """Принудительное обновление cookies профиля через MSB + CDP."""
    import asyncio as _aio
    from .msb_client import MsbClient
    from . import cdp_cookies

    async def _do_refresh():
        async with MsbClient() as ml:
            start = await ml.start_profile(profile_id)
            if not start or not start.get("debugPort"):
                return None, "no_debug_port"
            debug_port = start["debugPort"]
            try:
                await cdp_cookies.navigate(
                    debug_port=debug_port, url="https://ggsel.net/",
                    wait_until="load", timeout=15.0,
                )
            except Exception as e:
                logger.warning("routes: CDP navigate failed: %s", e)
            await _aio.sleep(3)
            cookies = await cdp_cookies.get_cookies_via_cdp(
                debug_port=debug_port, domain="ggsel.net", timeout=10.0,
            )
            return cookies, None

    try:
        cookies, err = _aio.run(_do_refresh())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500

    if cookies:
        return jsonify({
            "ok": True,
            "profile_id": profile_id,
            "cookies_count": len(cookies),
            "cookies_names": list(cookies.keys())[:20],
        })
    return jsonify({"ok": False, "error": err or "refresh failed"}), 502


@parser_bp.route("/morelogin/groups", methods=["GET"])
def morelogin_groups():
    """Список групп в MSB."""
    import asyncio as _aio
    from .msb_client import MsbClient
    try:
        async def _do():
            async with MsbClient() as ml:
                return await ml.get_groups()
        return jsonify({"ok": True, "groups": _aio.run(_do())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@parser_bp.route("/morelogin/profile/<profile_id>", methods=["GET"])
def morelogin_profile(profile_id: str):
    """Детали профиля в MSB + статус + debugPort (если запущен)."""
    import asyncio as _aio
    from .msb_client import MsbClient
    try:
        async def _do():
            async with MsbClient() as ml:
                prof = await ml.get_profile(profile_id)
                status = await ml.get_profile_status(profile_id)
                return {"profile": prof, "status": status}
        return jsonify(_aio.run(_do()))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@parser_bp.route("/morelogin/start/<profile_id>", methods=["POST"])
def morelogin_start(profile_id: str):
    """Запустить профиль в MSB. Опционально ?url=... для навигации."""
    import asyncio as _aio
    from .msb_client import MsbClient
    url = request.args.get("url")
    try:
        async def _do():
            async with MsbClient() as ml:
                return await ml.start_profile(profile_id)
        return jsonify(_aio.run(_do()))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@parser_bp.route("/morelogin/stop/<profile_id>", methods=["POST"])
def morelogin_stop(profile_id: str):
    """Остановить профиль в MSB."""
    import asyncio as _aio
    from .msb_client import MsbClient
    try:
        async def _do():
            async with MsbClient() as ml:
                return await ml.stop_profile(profile_id)
        return jsonify({"ok": True, "data": _aio.run(_do())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  Telemetry
# ═══════════════════════════════════════════════════════════════════════════
@parser_bp.route("/telemetry/recent", methods=["GET"])
def telemetry_recent():
    from .telemetry import get_telemetry
    limit = min(1000, max(1, request.args.get("limit", 50, type=int)))
    try:
        events = get_telemetry().read_recent(limit=limit)
        return jsonify({"limit": limit, "count": len(events), "items": events})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@parser_bp.route("/telemetry/stats", methods=["GET"])
def telemetry_stats():
    from .telemetry import get_telemetry
    try:
        return jsonify(get_telemetry().stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@parser_bp.route("/telemetry/enabled", methods=["POST"])
def telemetry_toggle():
    from .telemetry import get_telemetry
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", True))
    try:
        tel = get_telemetry()
        if enabled:
            tel.enable()
        else:
            tel.disable()
        return jsonify({"ok": True, "enabled": tel._enabled})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@parser_bp.route("/products/<path:product_id>/approve", methods=["POST"])
def approve_product(product_id: str):
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        conn.execute(
            "UPDATE parsed_products SET approval_status = 'approved', updated_at = datetime('now') WHERE product_id = ?",
            (product_id,)
        )
        conn.commit()
        return jsonify({"ok": True, "product_id": product_id, "approval_status": "approved"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@parser_bp.route("/products/<path:product_id>/reject", methods=["POST"])
def reject_product(product_id: str):
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    try:
        conn.execute(
            "UPDATE parsed_products SET approval_status = 'rejected', updated_at = datetime('now') WHERE product_id = ?",
            (product_id,)
        )
        conn.commit()
        return jsonify({"ok": True, "product_id": product_id, "approval_status": "rejected"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@parser_bp.route("/products/batch_approve", methods=["POST"])
def batch_approve_products():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not ids:
        return jsonify({"ok": False, "error": "No IDs provided"}), 400
    conn = sqlite3.connect(get_db_path(), timeout=15.0)
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE parsed_products SET approval_status = 'approved', updated_at = datetime('now') WHERE product_id IN ({placeholders})",
            ids
        )
        conn.commit()
        return jsonify({"ok": True, "count": len(ids)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

@parser_bp.route("/products/batch_reject", methods=["POST"])
def batch_reject_products():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not ids:
        return jsonify({"ok": False, "error": "No IDs provided"}), 400
    conn = sqlite3.connect(get_db_path(), timeout=15.0)
    try:
        placeholders = ",".join(["?"] * len(ids))
        conn.execute(
            f"UPDATE parsed_products SET approval_status = 'rejected', updated_at = datetime('now') WHERE product_id IN ({placeholders})",
            ids
        )
        conn.commit()
        return jsonify({"ok": True, "count": len(ids)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  AUTO-PILOT  (test: 10 позиций / turbo: 100 позиций)
#
#  POST /api/parser/auto/start  { "mode": "test" }   — тест: 1 цикл, 10 поз
#  POST /api/parser/auto/start  { "mode": "turbo" }  — боевой: ∞ циклов, 100 поз
#  POST /api/parser/auto/stop
#  GET  /api/parser/auto/status
# ═══════════════════════════════════════════════════════════════════════════
import threading as _ap_threading
import random as _ap_random
import time as _ap_time

_autopilot_state: dict = {
    "running": False,
    "stopped": False,
    "thread": None,
    "current_category": None,
    "next_categories": [],
    "cycles": 0,
    "mode": "test",
    "limit": 10,
    "started_at": None,
    "last_error": None,
}


def _ap_worker(limit: int, mode: str):
    """Фоновый поток авто-пилота."""
    st = _autopilot_state
    st["cycles"] = 0
    st["started_at"] = datetime.utcnow().isoformat()

    # Use KNOWN_CATEGORIES slugs (correct ggsel.net/catalog/<slug> URLs)
    from .parser_engine import KNOWN_CATEGORIES as _AP_SLUGS
    _slug_pool = list(_AP_SLUGS)

    while not st["stopped"]:
        if not _slug_pool:
            from .parser_engine import KNOWN_CATEGORIES as _S2
            _slug_pool = list(_S2)
        _ap_random.shuffle(_slug_pool)
        batch_slugs = _slug_pool[:3]
        _slug_pool = _slug_pool[3:]
        st["next_categories"] = batch_slugs[1:]

        for cid in batch_slugs:
            if st["stopped"]:
                break
            st["current_category"] = cid
            try:
                eng = get_engine()
                if not eng.is_running():
                    eng.start(
                        query="",
                        category=cid,
                        quantity=limit,
                        max_pages=2,
                        run_ai_enrichment=True,
                    )
                # Ждём завершения (макс 5 мин)
                for _ in range(300):
                    if st["stopped"] or not eng.is_running():
                        break
                    _ap_time.sleep(1)
            except Exception as exc:
                st["last_error"] = str(exc)

            _ap_time.sleep(3)

        st["cycles"] += 1

        # Тест-режим: один полный цикл и стоп
        if mode == "test":
            break

        # Turbo: пауза перед следующим циклом
        _ap_time.sleep(30)

    st["running"] = False
    st["current_category"] = None


@parser_bp.route("/auto/start", methods=["POST"])
def auto_start():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "test")   # "test" | "turbo"
    if mode not in ("test", "turbo"):
        mode = "test"
    limit = 10 if mode == "test" else 100
    st = _autopilot_state

    if st["running"] and not st["stopped"]:
        return jsonify({
            "ok": False,
            "error": "Авто-пилот уже запущен. Сначала остановите его.",
            "state": {k: v for k, v in st.items() if k != "thread"},
        }), 409

    st.update({
        "running": True,
        "stopped": False,
        "mode": mode,
        "limit": limit,
        "current_category": None,
        "next_categories": [],
        "cycles": 0,
        "last_error": None,
        "started_at": None,
    })
    t = _ap_threading.Thread(target=_ap_worker, args=(limit, mode), daemon=True, name="autopilot")
    st["thread"] = t
    t.start()
    return jsonify({"ok": True, "mode": mode, "limit": limit})


@parser_bp.route("/auto/stop", methods=["POST"])
def auto_stop():
    _autopilot_state["stopped"] = True
    _autopilot_state["running"] = False
    try:
        get_engine().stop()
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Авто-пилот остановлен"})


@parser_bp.route("/auto/status", methods=["GET"])
def auto_status():
    s = {k: v for k, v in _autopilot_state.items() if k != "thread"}
    return jsonify(s)


# ═══════════════════════════════════════════════════════════════════════════
#  Full Scan — параллельный обход всех content_type категорий воркерами
# ═══════════════════════════════════════════════════════════════════════════

@parser_bp.route("/fullscan/start", methods=["POST"])
def fullscan_start():
    """
    POST /api/parser/fullscan/start
    Body (все опциональны):
      {
        "run_ai":             false,
        "workers_per_account": 4,
        "sort":               "sortByRec",
        "ct_ids":             [2, 48, 19]   // конкретные content_type_id, иначе все 19
      }
    """
    from .parser_engine import full_scan_start, full_scan_status, FULL_SCAN_CONTENT_TYPES

    body = request.get_json(silent=True) or {}

    # Если full scan уже активен
    st = full_scan_status()
    if st.get("running") and not st.get("stopped"):
        return jsonify({
            "ok": False,
            "error": "Full scan уже запущен",
            "status": st,
        }), 409

    run_ai = bool(body.get("run_ai", False))
    workers_per_account = max(1, min(int(body.get("workers_per_account") or 4), 16))
    sort = str(body.get("sort") or "sortByRec")

    raw_ct = body.get("ct_ids")
    if raw_ct and isinstance(raw_ct, list):
        try:
            ct_ids = [int(x) for x in raw_ct]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ct_ids должен быть списком чисел"}), 400
    else:
        ct_ids = None  # все

    result = full_scan_start(
        run_ai=run_ai,
        ct_ids=ct_ids,
        workers_per_account=workers_per_account,
        sort=sort,
    )
    code = 200 if result.get("ok") else 409
    return jsonify(result), code


@parser_bp.route("/fullscan/stop", methods=["POST"])
def fullscan_stop():
    """POST /api/parser/fullscan/stop"""
    from .parser_engine import full_scan_stop
    return jsonify(full_scan_stop())


@parser_bp.route("/fullscan/status", methods=["GET"])
def fullscan_status():
    """
    GET /api/parser/fullscan/status
    Возвращает:
      {
        running, stopped, workers_count,
        workers: [{worker_id, account, ct_id, ct_name, page, saved, ct_done}],
        total_saved, total_found,
        ct_done, ct_remaining,
        started_at, finished_at, last_error, run_ai
      }
    """
    from .parser_engine import full_scan_status, FULL_SCAN_CT_NAMES, FULL_SCAN_CONTENT_TYPES
    st = full_scan_status()
    # Добавляем читаемые имена категорий для фронта
    st["ct_names"] = FULL_SCAN_CT_NAMES
    st["all_ct_ids"] = FULL_SCAN_CONTENT_TYPES
    return jsonify(st)


@parser_bp.route("/fullscan/categories", methods=["GET"])
def fullscan_categories():
    """
    GET /api/parser/fullscan/categories
    Список всех поддерживаемых content_type с именами.
    """
    from .parser_engine import FULL_SCAN_CONTENT_TYPES, FULL_SCAN_CT_NAMES
    cats = [
        {"ct_id": ct_id, "name": FULL_SCAN_CT_NAMES.get(ct_id, str(ct_id))}
        for ct_id in FULL_SCAN_CONTENT_TYPES
    ]
    return jsonify({"ok": True, "categories": cats})


@parser_bp.route("/fullscan/category-stats", methods=["GET"])
def fullscan_category_stats():
    """
    GET /api/parser/fullscan/category-stats?slug=roblox
    Возвращает статистику категорий из БД.
    slug — опциональный фильтр по конкретному slug.
    """
    from .category_stats import get_stats, get_summary
    slug = request.args.get("slug")
    if slug:
        all_stats = get_stats()
        filtered = [s for s in all_stats if s["slug"] == slug]
        return jsonify({"ok": True, "data": filtered, "count": len(filtered)})
    summary = get_summary()
    return jsonify({"ok": True, **summary})


@parser_bp.route("/fullscan/category-stats/scan", methods=["POST"])
def fullscan_category_stats_scan():
    """
    POST /api/parser/fullscan/category-stats/scan
    Запускает фоновый скан всех категорий.
    Body (опционально): {"profile_id": "UUID"}
    """
    from .category_stats import start_scan_background
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profile_id")
    result = start_scan_background(profile_id=profile_id)
    return jsonify(result)


@parser_bp.route("/fullscan/category-stats/status", methods=["GET"])
def fullscan_category_stats_status():
    """
    GET /api/parser/fullscan/category-stats/status
    Статус текущего/последнего скана.
    """
    from .category_stats import get_scan_status
    return jsonify({"ok": True, **get_scan_status()})


# ══════════════════════════════════════════════════════════════════════════════
# Section Scan — сканирование по подкатегориям (id_section) из БД
# ══════════════════════════════════════════════════════════════════════════════

@parser_bp.route("/section-scan/start", methods=["POST"])
def section_scan_start_route():
    """
    POST /api/parser/section-scan/start

    Сканирование по секциям (подкатегориям) из БД.
    Для каждой секции: smart-skip если уже парсилось сегодня или db_count >= api_total.
    После волны — ищет новые секции и повторяет (iterative wave discovery).

    Body (optional):
      run_ai              bool       — AI-обогащение после сохранения (default false)
      workers_per_account int        — воркеров на аккаунт (default 4)
      ct_filter           list[int]  — фильтр content_type_id (default все)
    """
    body                = request.get_json(silent=True) or {}
    run_ai              = bool(body.get("run_ai", False))
    workers_per_account = int(body.get("workers_per_account", 4))
    ct_filter           = body.get("ct_filter")
    if ct_filter is not None:
        try:
            ct_filter = [int(x) for x in ct_filter]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ct_filter должен быть списком чисел"}), 400
    result = section_scan_start(
        run_ai=run_ai,
        workers_per_account=workers_per_account,
        ct_filter=ct_filter,
    )
    return jsonify(result), 200 if result.get("ok") else 409


@parser_bp.route("/section-scan/stop", methods=["POST"])
def section_scan_stop_route():
    """POST /api/parser/section-scan/stop"""
    return jsonify(section_scan_stop())


@parser_bp.route("/section-scan/status", methods=["GET"])
def section_scan_status_route():
    """GET /api/parser/section-scan/status"""
    st    = section_scan_status()
    total = st.get("total_sections", 0)
    done  = st.get("sections_done", 0)
    st["progress_pct"] = round(done / total * 100, 1) if total else 0
    return jsonify(st)


# ══════════════════════════════════════════════════════════════════════════════
# Price Scan — адаптивное ценовое разбиение для полного сбора каталога
# ══════════════════════════════════════════════════════════════════════════════

@parser_bp.route("/price-scan/start", methods=["POST"])
def price_scan_start_route():
    """
    POST /api/parser/price-scan/start

    Запускает Price Scan — единственный способ получить весь каталог (~384k товаров).
    Стандартный Full Scan ограничен ~10k/категорию; Price Scan обходит это через
    фильтры min_price/max_price с бинарным разбиением диапазонов.

    Body (optional):
      workers_per_account  int        — воркеров на аккаунт (default 4)
      enrich               bool       — запускать detail+review обогащение (default true)
      ct_ids               list[int]  — фильтр content_type_id (default все)
    """
    body                = request.get_json(silent=True) or {}
    workers_per_account = int(body.get("workers_per_account", 4))
    enrich              = bool(body.get("enrich", True))
    ct_ids              = body.get("ct_ids")
    if ct_ids is not None:
        try:
            ct_ids = [int(x) for x in ct_ids]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ct_ids должен быть списком чисел"}), 400
    result = price_scan_start(
        workers_per_account=workers_per_account,
        enrich=enrich,
        ct_ids=ct_ids,
    )
    return jsonify(result), 200 if result.get("ok") else 409


@parser_bp.route("/price-scan/stop", methods=["POST"])
def price_scan_stop_route():
    """POST /api/parser/price-scan/stop"""
    return jsonify(price_scan_stop())


@parser_bp.route("/price-scan/status", methods=["GET"])
def price_scan_status_route():
    """GET /api/parser/price-scan/status"""
    return jsonify(price_scan_status())
