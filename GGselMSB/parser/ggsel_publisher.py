import os
import sys
import logging
import httpx
from pathlib import Path

try:
    from dotenv import load_dotenv as _ld
    _ld(Path(__file__).resolve().parent.parent / ".env", override=False)
except Exception:
    pass

log = logging.getLogger("ggselv7.publisher")

# Импортируем маппер категорий (slug → seller API id)
# Сначала пробуем новый parser/category_resolver.py, иначе — старый map_categories.py
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
_find_seller_cat = None
try:
    from parser.category_resolver import find_seller_category_id as _find_seller_cat
except Exception as _e:
    log.warning("[publisher] parser.category_resolver not available: %s — fallback to old map_categories", _e)
    try:
        from map_categories import find_seller_category_id as _find_seller_cat  # type: ignore
    except Exception:
        log.warning("[publisher] map_categories not available either — category_id will use fallback only")


class PublishError(Exception):
    """Ошибка публикации с HTTP-кодом для Flask."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GGselPublisher:
    def __init__(self):
        self.api_key = os.getenv("GGSEL_API_KEY", "")
        self.base_url = "https://seller.ggsel.com/api_sellers/v2"
        # Auth scheme: "Bearer" (default) or "" (raw key).
        # Set GGSEL_API_AUTH_SCHEME="" if API expects raw key.
        auth_scheme = os.getenv("GGSEL_API_AUTH_SCHEME", "Bearer")
        if self.api_key and auth_scheme:
            auth_value = f"{auth_scheme} {self.api_key}"
        else:
            auth_value = self.api_key
        self.headers = {
            "Authorization": auth_value,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if not self.api_key:
            log.warning(
                "[publisher] GGSEL_API_KEY не задан в .env — все запросы к API вернут 401. "
                "Заполни GGSEL_API_KEY=<real_key> в .env, затем перезапусти Flask."
            )

    def _require_status(self, product: dict, allowed_statuses: list[str]) -> None:
        """Проверяет что товар имеет один из разрешённых статусов."""
        status = (product.get("status") or "").strip().lower()
        if status not in [s.lower() for s in allowed_statuses]:
            raise PublishError(
                f"Операция разрешена только для статусов {allowed_statuses} (сейчас: {status or 'unknown'})",
                status_code=400,
            )

    def _require_approved(self, product: dict) -> None:
        """Проверяет что товар имеет approval_status = 'approved'."""
        approval_status = (product.get("approval_status") or "").strip().lower()
        if approval_status != "approved":
            raise PublishError(
                f"Публикация заблокирована: товар должен иметь статус одобрения 'approved' (текущий: {approval_status or 'unknown'})",
                status_code=400,
            )

    def create_offer(self, product: dict) -> str:
        """POST /api_sellers/v2/offers, возвращает offer_id."""
        self._require_approved(product)

        sell_price = product.get("sell_price")
        if sell_price is None or float(sell_price) <= 0:
            raise PublishError("sell_price не задан или равен 0 — сначала рассчитайте экономику", 400)

        # 1) Резолвим seller category_id.
        # Приоритет источников:
        #   a) уже посчитанный seller_seller_id в payload
        #   b) category_slug / slug → find_seller_category_id() (из category_slug_mapping)
        #   c) category_id из БД парсера (если это валидный seller id — попробуем найти в маппинге)
        #   d) Фоллбэк: 33833 (Цифровые товары > Другое, seller API id) + лог.
        seller_cat_id = None
        resolution = None  # для лога

        # a) уже известный
        seller_cat_id = product.get("category_seller_id")
        if seller_cat_id:
            resolution = "explicit"
            try:
                seller_cat_id = int(seller_cat_id)
            except (TypeError, ValueError):
                seller_cat_id = None

        # b) slug → seller id
        # Проверяем что резолвинг вернул лист (has_children=0), а не родительский узел.
        if not seller_cat_id:
            slug = (product.get("category_slug") or product.get("slug") or "").strip()
            if slug and _find_seller_cat is not None:
                try:
                    db_path = str(_root / "data" / "db" / "parser.db")
                    resolved = _find_seller_cat(slug, db_path=db_path)
                    if resolved:
                        # Проверяем: если это родительский узел — отказываемся и идём дальше (путь c)
                        _is_parent = False
                        try:
                            import sqlite3 as _sq3
                            _sc = _sq3.connect(db_path, timeout=3)
                            _row = _sc.execute(
                                "SELECT has_children FROM seller_categories WHERE id=? LIMIT 1",
                                (int(resolved),)
                            ).fetchone()
                            _sc.close()
                            _is_parent = bool(_row and _row[0])
                        except Exception:
                            pass
                        if _is_parent:
                            log.debug(
                                "[publisher] slug=%r resolved to parent cat %s — skipping, trying category_id",
                                slug, resolved,
                            )
                        else:
                            seller_cat_id = int(resolved)
                            resolution = f"slug={slug!r}"
                except Exception as e:
                    log.warning("[publisher] resolver failed for slug=%r: %s", slug, e)

        # c) category_id из product (может быть seller id ИЛИ ggsel_digi_catalog)
        if not seller_cat_id:
            cid = product.get("category_id")
            if cid is not None:
                try:
                    cid_int = int(cid)
                    # Попробуем найти в category_slug_mapping по seller_id (точное совпадение)
                    if _find_seller_cat is not None:
                        try:
                            from parser.category_resolver import get_by_seller_id
                            hit = get_by_seller_id(cid_int, db_path=str(_root / "data" / "db" / "parser.db"))
                            if hit:
                                seller_cat_id = cid_int
                                resolution = f"category_id={cid_int} (seller, {hit.get('seller_tree','')})"
                        except Exception as e:
                            log.debug("[publisher] get_by_seller_id failed: %s", e)
                except (TypeError, ValueError):
                    pass

        # d) Фоллбэк
        FALLBACK_SELLER_ID = 33833  # Цифровые товары > Другое (seller API)
        if not seller_cat_id:
            seller_cat_id = FALLBACK_SELLER_ID
            resolution = f"FALLBACK ({FALLBACK_SELLER_ID} = 'Цифровые товары > Другое')"
            log.warning(
                "[publisher] category_id не определён через slug=%r / category_id=%r — "
                "использую фоллбэк seller_id=%s. Товар будет размещён в «Другое».",
                product.get("category_slug") or product.get("slug"),
                product.get("category_id"),
                FALLBACK_SELLER_ID,
            )

        url = f"{self.base_url}/offers"
        payload = {
            "title_ru": product.get("generated_title") or product.get("title") or product.get("original_title", "Без названия"),
            "description_ru": product.get("generated_desc") or product.get("original_desc") or product.get("description", ""),
            "price": float(sell_price),
            "category_id": int(seller_cat_id),
            "is_autoselling": True,
        }
        log.info(
            "[publisher] create_offer: product_id=%s seller_category_id=%s (%s)",
            product.get("product_id") or product.get("id"),
            seller_cat_id, resolution or "?",
        )
        resp = httpx.post(url, headers=self.headers, json=payload, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        offer_id = str(data.get("id") or data.get("offer_id") or data.get("data", {}).get("id") or "")
        if not offer_id:
            raise PublishError(f"API не вернул offer_id: {data!r}", 502)
        return offer_id

    def add_product(self, offer_id: str, product: dict):
        """POST /api_sellers/v2/offers/{id}/products — value = цифровой ключ товара."""
        self._require_approved(product)

        digital_key = (
            product.get("digital_key")
            or product.get("product_key")
            or product.get("value")
            or product.get("product_id")
        )
        if not digital_key:
            raise PublishError("Не найден цифровой ключ товара (product_id / digital_key)", 400)

        url = f"{self.base_url}/offers/{offer_id}/products"
        payload = {
            "products": [
                {"value": str(digital_key).strip()},
            ],
        }
        resp = httpx.post(url, headers=self.headers, json=payload, timeout=15.0)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {"ok": True}
        return resp.json()

    def publish_offer(self, offer_id: str):
        """POST /api_sellers/v2/offers/{id}/activate — публикация черновика."""
        url = f"{self.base_url}/offers/{offer_id}/activate"
        resp = httpx.post(url, headers=self.headers, timeout=15.0)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {"ok": True}
        return resp.json()
