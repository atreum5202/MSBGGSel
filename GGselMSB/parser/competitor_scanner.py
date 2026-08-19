"""
parser/competitor_scanner.py
============================
Мигрировано из GGSeller/services/parser/ggsel_parser_v2.py (2026-07-27).

Что делает:
  - Сканирует категории / продавцов / конкретные URL ggsel.net
  - Скачивает карточки, парсит, фильтрует дубли, AI-обогащает, сохраняет в БД
  - Делает top-5 по продажам, скачивает изображения, шлёт TG-уведомления
  - Имеет защиту от параллельного запуска (singleton-style _is_running флаг)

Что НЕ включено (по запросу пользователя):
  - ProxyPool (нет прокси-ротации на уровне сканера — V7 использует
    per-profile паузы через AdaptiveRateLimiter в MsbFetcher)
  - BrowserCookieFetcher / SessionFetcher (V7 использует QratorCookieMiddleware
    через MSB)
  - AI Gateway (out of scope для парсера)

Что подключено из V7:
  - ParserEngine, Product, FetchResult, ParseResult, GGselHTMLParser,
    CffiFetcher, CascadeFetcher, KNOWN_CATEGORIES, BASE_URL  ← parser_engine.py
  - QratorCookieMiddleware  ← msb_cookies.py
  - calculate_my_price  ← pricing.py
  - enrich_product  ← content_gen.py
  - send_notification_sync  ← tg_bot.py
  - is_fresh, is_rejected  ← dedup.py

Зависимости от GGSeller-стиля:
  - БД: V7 использует `parsed_products` (не `products`)
  - TG: V7 использует `send_notification_sync(msg)` (не `tg(msg)`)
  - AI: V7 использует `enrich_product(raw)` (не `process_product(raw)`)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import sqlite3
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── V7 модули (всё что раньше было в monolith ggsel_parser_v2.py) ─────────
from .parser_engine import (
    BASE_URL,
    KNOWN_CATEGORIES,
    DEFAULT_MAX_PAGES as PAGE_LIMIT,  # V7 алиас
    Product,
    FetchResult,
    ParseResult,
    GGselHTMLParser,
    CascadeFetcher,
)
from .msb_cookies import QratorCookieMiddleware
from .pricing import calculate_my_price
from .dedup import is_fresh, is_rejected
from .category_resolver import find_seller_category_id

# AI: V7 content_gen.enrich_product(raw_product) -> dict
# Подпись GGSeller: process_product(raw_product) -> dict (другая, но поля похожие)
try:
    from .content_gen import enrich_product as _enrich_product
except Exception:  # pragma: no cover
    _enrich_product = None

# TG: V7 tg_bot.send_notification_sync(message, product_id=None) — GGSeller использовал notify.tg
try:
    from .tg_bot import send_notification_sync as _tg_notify
except Exception:  # pragma: no cover
    _tg_notify = None

# ── Опциональные зависимости (requests, PIL, httpx) ────────────────────────
try:
    import httpx as _httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    from PIL import Image as _PILImage
    import io as _io
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    _PILImage = None  # type: ignore

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

# ── Логирование ───────────────────────────────────────────────────────────
log = logging.getLogger("ggselv7.competitor_scanner")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ── Дефолты (по аналогии с GGSeller, но без ссылок на монолит) ────────────
REQUEST_DELAY = 4.0
REQUEST_DELAY_JITTER = 2.0

# Путь к БД — V7 по умолчанию использует свой parser.db,
# но GGSeller-стиль хранит всё в shared/db/ggsel.db.
# Делаем настраиваемым через ENV: COMPETITOR_DB_PATH
DB_PATH = os.getenv("COMPETITOR_DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "db" / "parser.db"))
IMAGE_DIR = Path(os.getenv("COMPETITOR_IMAGE_DIR", "shared/images/products"))


# ═════════════════════════════════════════════════════════════════════════
# Утилиты (мигрированы из GGSeller/ggsel_parser_v2.py)
# ═════════════════════════════════════════════════════════════════════════

def _smart_sleep(base: float = REQUEST_DELAY, jitter: float = REQUEST_DELAY_JITTER) -> None:
    """Пауза с логнормальным распределением."""
    import math
    mu = math.log(base)
    sigma = 0.3
    delay = random.lognormvariate(mu, sigma)
    delay = max(base * 0.5, min(delay, base + jitter * 2))
    log.debug("[Sleep] Пауза %.1f сек", delay)
    time.sleep(delay)


def _make_soup(html: str):
    if not html or not html.strip() or not _BS4_AVAILABLE:
        return None
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None


def _log_fetch(result: FetchResult) -> None:
    """Подробный лог каждого HTTP-запроса (как в GGSeller)."""
    status_str = f"HTTP {result.status_code}" if result.status_code else "NO RESPONSE"
    challenge_str = " [⚠️ QRATOR CHALLENGE]" if getattr(result, "is_challenge", False) else ""
    if result.success and not getattr(result, "is_challenge", False):
        log.info(
            "✅ FETCH OK | %s | %s | %dms | %d chars | strategy=%s",
            status_str, result.url, result.duration_ms, len(result.html), result.strategy_used,
        )
    else:
        log.warning(
            "❌ FETCH FAIL | %s%s | %s | %dms | strategy=%s | error=%s",
            status_str, challenge_str, result.url, result.duration_ms,
            result.strategy_used, result.error or "challenge",
        )


def top_products_by_sales(products: List[Product], n: int = 5) -> List[Product]:
    """Сортирует список товаров по extra['sales_count'] (None → 0), возвращает топ n."""
    return sorted(
        products,
        key=lambda p: (p.extra.get("sales_count") or 0),
        reverse=True,
    )[:n]


# ═════════════════════════════════════════════════════════════════════════
# Адаптер enrich_product — V7 принимает dict, GGSeller-style требует поля
# {product_id, title, description, image_url}
# ═════════════════════════════════════════════════════════════════════════

def _adapt_enrich(raw_product: dict) -> dict:
    """Обёртка над V7.enrich_product, чтобы вернуть словарь с полями,
    совместимыми со старым GGSeller.process_product:
      - generated_title, generated_desc, generated_image_url, status, product_id
    """
    if _enrich_product is None:
        # fallback: пустышка (как при ошибке AI)
        return {
            "generated_title": raw_product.get("title"),
            "generated_desc": "",
            "generated_image_url": "",
            "status": "queued",
            "product_id": raw_product.get("product_id"),
        }
    try:
        enriched = _enrich_product(raw_product)
        # V7.enrich_product может вернуть {title, description, tags, ...}
        # Приводим к GGSeller-формату.
        return {
            "generated_title": enriched.get("title") or enriched.get("generated_title"),
            "generated_desc":  enriched.get("description") or enriched.get("generated_desc") or "",
            "generated_image_url": enriched.get("image_url") or enriched.get("generated_image_url") or "",
            "status": enriched.get("_status", "queued"),
            "product_id": raw_product.get("product_id"),
        }
    except Exception as e:
        log.warning("[enrich] Ошибка enrich_product: %s", e)
        return {
            "generated_title": raw_product.get("title"),
            "generated_desc": "",
            "generated_image_url": "",
            "status": "gen_failed",
            "product_id": raw_product.get("product_id"),
        }


# ═════════════════════════════════════════════════════════════════════════
# Скачивание изображений (мигрировано)
# ═════════════════════════════════════════════════════════════════════════

async def download_product_image(image_url: str, product_id: str) -> Optional[str]:
    """
    Скачивает изображение по URL, сохраняет в IMAGE_DIR/{product_id}.jpg.
    Поддерживает jpg/png/webp (webp → jpg через PIL если доступен).
    Возвращает локальный путь или None при ошибке.
    """
    if not image_url or not _HTTPX_AVAILABLE:
        return None

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    timeout = float(os.getenv("IMAGE_DOWNLOAD_TIMEOUT", "15"))

    try:
        async with _httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            data = resp.content
    except Exception as e:
        log.warning("[image_dl] Не удалось скачать %s: %s", image_url, e)
        return None

    out_path = IMAGE_DIR / f"{product_id}.jpg"
    url_lower = image_url.lower().split("?")[0]

    try:
        if url_lower.endswith(".webp") and _PIL_AVAILABLE:
            img = _PILImage.open(_io.BytesIO(data)).convert("RGB")
            img.save(str(out_path), "JPEG")
        else:
            out_path.write_bytes(data)
        log.info("[image_dl] Сохранено: %s", out_path)
        return str(out_path)
    except Exception as e:
        log.warning("[image_dl] Ошибка сохранения %s: %s", out_path, e)
        return None


# ═════════════════════════════════════════════════════════════════════════
# Проверка доступности источника (мигрировано)
# ═════════════════════════════════════════════════════════════════════════

async def check_source_availability(product_id: str, source_url: str) -> bool:
    """HEAD-запрос к source_url. 200-399 → True.
    При False — помечает товар как недоступный (in_stock=0) в parsed_products.
    """
    if not _HTTPX_AVAILABLE:
        log.warning("[check_source] httpx недоступен, пропускаем проверку %s", source_url)
        return True

    available = False
    try:
        async with _httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.head(source_url)
            available = 200 <= resp.status_code < 400
    except Exception as e:
        log.warning("[check_source] HEAD %s ошибка: %s", source_url, e)
        available = False

    now = datetime.utcnow().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            if not available:
                conn.execute(
                    "UPDATE parsed_products SET in_stock=0, source_checked_at=? WHERE product_id=?",
                    (now, product_id),
                )
                log.warning("[check_source] Недоступен: %s → in_stock=0", source_url)
            else:
                # V7 schema parsed_products имеет last_parsed_at, но не source_checked_at.
                # Используем last_parsed_at как proxy.
                conn.execute(
                    "UPDATE parsed_products SET last_parsed_at=? WHERE product_id=?",
                    (now, product_id),
                )
                log.info("[check_source] Доступен: %s", source_url)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.error("[check_source] DB ошибка: %s", e)

    return available


# ═════════════════════════════════════════════════════════════════════════
# CompetitorScanner — основной класс (мигрирован с адаптацией)
# ═════════════════════════════════════════════════════════════════════════

class CompetitorScanner:
    """
    Сканер категорий / продавцов / URL на ggsel.net.

    Потокобезопасность: флаг _is_running защищает от параллельного запуска
    (например, если бот и scheduler одновременно триггернули скан).
    """

    # Флаг для защиты от параллельного запуска
    _is_running: bool = False

    def __init__(self, fetcher: Any = None, max_pages: int = PAGE_LIMIT):
        self._fetcher = fetcher or CascadeFetcher()
        self._parser = GGselHTMLParser()
        self._max_pages = max_pages
        self._msb: Optional[QratorCookieMiddleware] = None
        self._msb_r = None  # placeholder, populated in connect_msb()

    def _resolve_category(self, slug: Optional[str]) -> int:
        """Резолвит slug → ggsel_digi_catalog (fallback 33833 = "Цифровые товары > Другое").

        Использует category_resolver.find_seller_category_id.
        Возвращает int или 33833 (fallback).
        """
        FALLBACK_SELLER_ID = 33833
        if not slug:
            return FALLBACK_SELLER_ID
        try:
            cid = find_seller_category_id(slug)
        except Exception as e:
            log.warning("[scanner] resolver failed for slug=%r: %s", slug, e)
            cid = None
        if not cid:
            log.debug("[scanner] no match for slug=%r → fallback %s", slug, FALLBACK_SELLER_ID)
            return FALLBACK_SELLER_ID
        return int(cid)

    def scan_url(self, url: str) -> ParseResult:
        """Сканирует одну страницу (категория / продавец / карточка товара)."""
        log.info("→ Фетчим: %s", url)
        fetch = self._fetcher.fetch(url)
        if not fetch.success:
            result = ParseResult()
            result.errors.append(f"Fetch error: {fetch.error} (status={fetch.status_code})")
            return result
        return self._parser.parse(fetch.html, hint_url=url)

    def _save_products_batch(self, new_products: List[Product]) -> List[Product]:
        """Сохраняет батч в БД (parsed_products), вызывает AI-обогащение,
        скачивает картинки. Возвращает список успешно сохранённых.
        """
        if not new_products:
            return []

        # Top-5 по продажам — ДО сохранения
        top5 = top_products_by_sales(new_products, n=5)
        top5_ids = {p.external_id for p in top5}

        saved: List[Product] = []
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            for p in new_products:
                extra = p.extra or {}

                # Реальный slug категории товара из детальной цепочки API;
                # fallback — slug запроса (p.category), если обогащение не прошло
                real_cat_slug = extra.get("category_slug") or p.category or ""

                # category_id: приоритет — обогащённый через _resolve_category_id,
                # затем id_section из API-листинга (всегда присутствует),
                # fallback — резолв по slug через category_resolver
                cat_id_from_extra = (
                    extra.get("category_id")
                    or (int(extra["id_section"]) if extra.get("id_section") else None)
                )
                if cat_id_from_extra:
                    seller_cat_id = int(cat_id_from_extra)
                else:
                    seller_cat_id = self._resolve_category(real_cat_slug or p.category)

                my_price = calculate_my_price(p.price, seller_cat_id)
                is_top = 1 if p.external_id in top5_ids else 0

                # breadcrumb из extra (строится в _enrich_one / to_engine_product)
                breadcrumb_val = extra.get("breadcrumb") or ""

                try:
                    # V7 schema: parsed_products. product_id = external_id.
                    conn.execute("""
                        INSERT INTO parsed_products (
                            product_id, title, price, my_price, category, url,
                            category_id, breadcrumb, in_stock, sales_count, source_price, is_top,
                            updated_at, last_parsed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, datetime('now'), datetime('now'))
                        ON CONFLICT(product_id) DO UPDATE SET
                            price        = excluded.price,
                            my_price     = excluded.my_price,
                            category     = excluded.category,
                            category_id  = excluded.category_id,
                            breadcrumb   = COALESCE(NULLIF(excluded.breadcrumb,''), parsed_products.breadcrumb),
                            sales_count  = excluded.sales_count,
                            source_price = excluded.source_price,
                            is_top       = excluded.is_top,
                            updated_at   = datetime('now'),
                            last_parsed_at = datetime('now')
                    """, (
                        p.external_id, p.name, p.price, my_price, real_cat_slug, p.url,
                        seller_cat_id, breadcrumb_val,
                        p.sales_count or 0, p.price, is_top,
                    ))

                    raw_product = {
                        "product_id": p.external_id,
                        "title": p.name,
                        "description": "",
                        "image_url": p.image_url,
                    }
                    enriched = _adapt_enrich(raw_product)
                    conn.execute("""
                        UPDATE parsed_products SET
                            generated_title     = ?,
                            generated_desc      = ?,
                            generated_image_url = ?,
                            status              = ?
                        WHERE product_id = ?
                    """, (
                        enriched.get("generated_title"),
                        enriched.get("generated_desc"),
                        enriched.get("generated_image_url"),
                        enriched.get("status", "pending"),
                        enriched["product_id"],
                    ))

                    # Скачиваем изображение (async внутри sync)
                    if p.image_url:
                        try:
                            try:
                                asyncio.get_running_loop()
                                import concurrent.futures as _cf
                                with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                                    local_path = _ex.submit(
                                        asyncio.run,
                                        download_product_image(p.image_url, p.external_id),
                                    ).result(timeout=20)
                            except RuntimeError:
                                local_path = asyncio.run(
                                    download_product_image(p.image_url, p.external_id)
                                )
                            # V7 schema parsed_products не имеет local_image_path, но
                            # если когда-то добавим — оставлено здесь:
                            # if local_path:
                            #     conn.execute(
                            #         "UPDATE parsed_products SET local_image_path=? WHERE product_id=?",
                            #         (local_path, p.external_id),
                            #     )
                        except Exception as img_err:
                            log.warning("[image_dl] %s: %s", p.external_id, img_err)

                    saved.append(p)
                except Exception as e:
                    log.error("DB/AI ошибка для %s: %s", p.external_id, e)

            conn.commit()
        finally:
            conn.close()

        return saved

    def _notify_tg(self, message: str) -> None:
        """Обёртка над V7 tg_bot.send_notification_sync."""
        if _tg_notify is None:
            log.debug("[tg] tg_bot недоступен, уведомление: %s", message[:80])
            return
        try:
            _tg_notify(message)
        except Exception as e:
            log.warning("[tg] Ошибка отправки: %s", e)

    def scan_category(self, category_slug: str) -> List[Product]:
        """Сканирует все страницы категории."""
        if CompetitorScanner._is_running:
            log.warning("[Scanner] Уже запущен — пропускаем категорию '%s'", category_slug)
            return []

        CompetitorScanner._is_running = True
        url = f"{BASE_URL}/catalog/{category_slug}"
        products: List[Product] = []
        page = 1

        try:
            while url and page <= self._max_pages:
                result = self.scan_url(url)
                if not result.success:
                    log.warning("Прерываем '%s' на стр.%d: %s", category_slug, page, result.errors)
                    break

                # Фильтруем дубли
                page_new: List[Product] = []
                for p in result.products:
                    if not p.category:
                        p.category = category_slug
                    if is_fresh(p.external_id) or is_rejected(p.external_id):
                        log.info("Пропуск (дубль или отклонён): %s", p.external_id)
                        continue
                    page_new.append(p)

                if page_new:
                    saved = self._save_products_batch(page_new)
                    products.extend(saved)

                    if saved:
                        names = "\n".join(f"• {p.name}" for p in saved[:10])
                        suffix = f"\n...и ещё {len(saved) - 10}" if len(saved) > 10 else ""
                        self._notify_tg(
                            f"🆕 {len(saved)} новых товаров [{category_slug}]:\n{names}{suffix}"
                        )

                log.info("  Стр.%d: +%d новых товаров", page, len(page_new))
                url = result.next_page_url
                if url:
                    page += 1
                    _smart_sleep()

        finally:
            CompetitorScanner._is_running = False

        log.info("Категория '%s': %d новых товаров за %d стр.", category_slug, len(products), page)
        return products

    def scan_seller(self, seller_id: str) -> List[Product]:
        """Сканирует все товары продавца."""
        if CompetitorScanner._is_running:
            log.warning("[Scanner] Уже запущен — пропускаем продавца '%s'", seller_id)
            return []

        CompetitorScanner._is_running = True
        url = f"{BASE_URL}/sellers/{seller_id}"
        products: List[Product] = []
        page = 1

        try:
            while url and page <= self._max_pages:
                result = self.scan_url(url)
                if not result.success:
                    break

                page_new: List[Product] = []
                for p in result.products:
                    if is_fresh(p.external_id) or is_rejected(p.external_id):
                        log.info("Пропуск (дубль или отклонён): %s", p.external_id)
                        continue
                    page_new.append(p)

                if page_new:
                    saved = self._save_products_batch(page_new)
                    products.extend(saved)

                    if saved:
                        names = "\n".join(f"• {p.name}" for p in saved[:10])
                        suffix = f"\n...и ещё {len(saved) - 10}" if len(saved) > 10 else ""
                        self._notify_tg(
                            f"🆕 {len(saved)} новых товаров [seller:{seller_id}]:\n{names}{suffix}"
                        )

                log.info("  Продавец стр.%d: +%d новых товаров", page, len(page_new))
                url = result.next_page_url
                if url:
                    page += 1
                    _smart_sleep()

        finally:
            CompetitorScanner._is_running = False

        return products

    def scan_all_categories(self) -> List[Product]:
        all_products: List[Product] = []
        for slug in KNOWN_CATEGORIES:
            log.info("\n=== Категория: %s ===", slug)
            products = self.scan_category(slug)
            all_products.extend(products)
            _smart_sleep(REQUEST_DELAY * 2, REQUEST_DELAY_JITTER * 2)
        log.info("\nИтого: %d товаров по всем категориям", len(all_products))
        return all_products

    def discover_categories(self) -> List[str]:
        """Ищет категории на главной ggsel.net."""
        log.info("Ищем категории на главной...")
        fetch = self._fetcher.fetch(BASE_URL)
        _log_fetch(fetch)
        if not fetch.success:
            log.warning("Не удалось получить главную, используем KNOWN_CATEGORIES")
            return KNOWN_CATEGORIES
        soup = _make_soup(fetch.html)
        if not soup:
            return KNOWN_CATEGORIES
        categories = []
        for a in soup.find_all("a", href=re.compile(r"/catalog/[a-z0-9-]+$")):
            href = a["href"]
            if "/product/" not in href:
                slug = href.rsplit("/", 1)[-1]
                if slug not in categories:
                    categories.append(slug)
        log.info("Найдено %d категорий: %s", len(categories), categories)
        return categories or KNOWN_CATEGORIES

    def close(self):
        try:
            self._fetcher.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════
# CLI (для отладки)
# ═════════════════════════════════════════════════════════════════════════

def _print_products(products: List[Product], limit: int = 20) -> None:
    if not products:
        print("  (нет товаров)")
        return
    print(f"\n{'#':>4}  {'Цена':>10}  {'Продажи':>8}  {'Продавец':<20}  Название")
    print("─" * 80)
    for i, p in enumerate(products[:limit], 1):
        sales = str(p.extra.get("sales_count", "?")) if p.extra.get("sales_count") else "?"
        seller = (p.seller or "")[:20]
        name = p.name[:40]
        print(f"  {i:>3}. {p.price:>9.0f}₽  {sales:>8}  {seller:<20}  {name}")
    if len(products) > limit:
        print(f"  ... и ещё {len(products) - limit} товаров")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="GGsel Competitor Scanner (мигрировано из GGSeller v2)",
    )
    ap.add_argument("--url", help="Конкретный URL для парсинга")
    ap.add_argument("--category", help="Slug категории (games, keys, software...)")
    ap.add_argument("--seller", help="ID продавца")
    ap.add_argument("--all-categories", action="store_true")
    ap.add_argument("--discover", action="store_true", help="Найти категории с главной")
    ap.add_argument("--max-pages", type=int, default=PAGE_LIMIT)
    ap.add_argument("--output", help="Сохранить JSON в файл")
    args = ap.parse_args()

    scanner = CompetitorScanner(max_pages=args.max_pages)
    all_products: List[Product] = []

    try:
        if args.url:
            result = scanner.scan_url(args.url)
            print(f"\nТип страницы: {result.page_type}")
            print(f"Товаров: {len(result.products)}")
            _print_products(result.products)
            all_products = result.products

        elif args.category:
            all_products = scanner.scan_category(args.category)
            _print_products(all_products)

        elif args.seller:
            all_products = scanner.scan_seller(args.seller)
            _print_products(all_products)

        elif args.discover:
            cats = scanner.discover_categories()
            print("Найденные категории:")
            for c in cats:
                print(f"  {BASE_URL}/catalog/{c}")

        elif args.all_categories:
            all_products = scanner.scan_all_categories()
            _print_products(all_products, limit=50)

        else:
            ap.print_help()
            sys.exit(0)

        if args.output and all_products:
            import json
            data = [asdict(p) for p in all_products]
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("Сохранено %d товаров в %s", len(all_products), args.output)

    except KeyboardInterrupt:
        log.info("Прервано")
    finally:
        scanner.close()


if __name__ == "__main__":
    main()
