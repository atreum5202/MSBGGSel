import os
import httpx
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv as _ld
    _ld(Path(__file__).resolve().parent.parent / ".env", override=False)
except Exception:
    pass

# Импортируем маппер категорий
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
try:
    from map_categories import find_seller_category_id as _find_seller_cat
except Exception:
    _find_seller_cat = None


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
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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

        # Определяем seller category_id через маппер по slug
        seller_cat_id = None
        slug = (product.get("category_slug") or product.get("slug") or "").strip()
        if slug and _find_seller_cat is not None:
            try:
                db_path = str(_root / "data" / "db" / "parser.db")
                seller_cat_id = _find_seller_cat(slug, db_path=db_path)
            except Exception:
                pass

        # Фоллбэк: используем category_id из БД парсера (может быть ID с ggsel.net, не seller)
        if not seller_cat_id:
            seller_cat_id = product.get("category_id") or 121712  # Цифровые товары > Другое

        url = f"{self.base_url}/offers"
        payload = {
            "title_ru": product.get("generated_title") or product.get("title") or product.get("original_title", "Без названия"),
            "description_ru": product.get("generated_desc") or product.get("original_desc") or product.get("description", ""),
            "price": float(sell_price),
            "category_id": int(seller_cat_id),
            "is_autoselling": True,
        }
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
