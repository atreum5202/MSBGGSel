"""
parser/ggsel_api_client.py
==========================
Прямой клиент к api.ggsel.com — без браузера, без Qrator, без прокси.

API использует Bearer-токен из профиля браузера (access_token в куках ggsel.net).
Токен живёт 31 день, обновляется через refresh_token.

Возможности:
  - Листинг товаров по категории/запросу (POST /elastic/goods/rec-goods)
  - Бестселлеры (POST /elastic/goods/bestsellers)
  - Цена товара (GET /goods/{id}/price)
  - Категории (GET /main/content-types)
  - Данные аккаунта + баланс (GET /accounts)
  - Детальная карточка через LD+JSON из HTML

Интеграция:
  Используется в CascadeFetcher как приоритетный источник.
  Если токен истёк или API недоступен — CascadeFetcher падает на HTML (curl-cffi).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except ImportError:
    _CFFI_OK = False

log = logging.getLogger("ggselv7.api_client")

# ── Конфигурация ──────────────────────────────────────────────────────────────

API_BASE    = "https://api.ggsel.com"
SITE_BASE   = "https://ggsel.net"
BACK_OFFICE = "https://back-office.ggsel.com"

# Путь к файлу с токенами (сохраняется при каждом обновлении)
_TOKEN_FILE = Path(__file__).parent.parent / "data" / "ggsel_tokens.json"

# Дефолтные токены из профиля браузера (2026-08-14, живут до 2026-09-12)
_DEFAULT_ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnZ3NlbCIsInN1YiI6NzI1NDg4OCwia"
    "WF0IjoxNzg2NjU3ODM1LCJleHAiOjE3ODkzMzYyMzUsImp0aSI6IjYxMzM0NGUy"
    "LTY0YzQtNGI5My04MmRjLWZmYjhiMGM3YjY5MCIsInVzZXIiOnsiaWQiOjcyNTQ"
    "4ODgsImVtYWlsIjoiYXRyZXVtLjUyMDJAZ21haWwuY29tIn0sIm5hbWVzcGFjZSI"
    "6ImFwaV9jdXN0b21lciJ9.74zNSpgzSbGNSigJ7rmaFs1LZl-Edaoa2dpIlBlA1TQ"
)
_DEFAULT_REFRESH_TOKEN = "6c57c4cf49ad904d863c83991fac8473a7afde792d1713613dd57b969226e45d"


# ── Модели ────────────────────────────────────────────────────────────────────

@dataclass
class ApiProduct:
    """Товар из API. Маппится в parser_engine.Product."""
    id_goods: int = 0
    name: str = ""
    url: str = ""                    # slug (без домена)
    image: str = ""
    price_wmz: float = 0.0
    price_wmr: float = 0.0
    price_wme: float = 0.0
    cnt_sell: int = 0
    is_active: bool = True
    autoselling: bool = False
    is_preorder: bool = False
    sale: Optional[float] = None
    content_type_id: Optional[int] = None
    rating: Optional[float] = None
    id_seller: int = 0
    seller_name: str = ""
    id_section: int = 0
    search_title: str = ""          # название подкатегории
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_url(self) -> str:
        if self.url.startswith("http"):
            return self.url
        return f"{SITE_BASE}/en/catalog/product/{self.url}"

    @property
    def price_usd(self) -> float:
        """wmz ≈ USD (курс ~1:1 исторически)."""
        return self.price_wmz

    @property
    def price_rub(self) -> float:
        return self.price_wmr


@dataclass
class ProductDetail:
    """Детали товара из LD+JSON (HTML страницы)."""
    name: str = ""
    description: str = ""
    image: str = ""
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    price: Optional[float] = None
    price_currency: str = ""
    in_stock: bool = True
    seller_name: str = ""
    seller_url: str = ""


# ── Основной клиент ───────────────────────────────────────────────────────────

class GgselApiClient:
    """
    Клиент к api.ggsel.com.

    Singleton — используй get_client() вместо прямого создания.

    Автоматически:
    - Загружает токены из файла (если есть) или из дефолтов
    - Обновляет access_token при получении 401
    - Сохраняет свежие токены в файл
    """

    _instance: Optional["GgselApiClient"] = None

    def __init__(self):
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_exp: int = 0     # unix timestamp истечения
        self._client: Optional[httpx.Client] = None
        # Привязка профиля: заполняется при загрузке токенов
        self.profile_name: str = ""     # название MSB-профиля (P-15, ggsel_parser_1, ...)
        self.account_email: str = ""    # email аккаунта ggsel из JWT
        self.ggsel_user_id: str = ""    # id пользователя ggsel из JWT
        self._load_tokens()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "GgselApiClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Токены ────────────────────────────────────────────────────────────────

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        """Быстрое декодирование JWT payload (без проверки подписи, нам нужен только email+id)."""
        try:
            import base64
            parts = token.split(".")
            if len(parts) != 3:
                return {}
            # Добавляем padding
            payload_b64 = parts[1] + "==" * ((4 - len(parts[1]) % 4) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            return {}

    def _load_tokens(self):
        """Загрузить токены: сначала файл, потом дефолты."""
        if _TOKEN_FILE.exists():
            try:
                data = json.loads(_TOKEN_FILE.read_text())
                self._access_token  = data.get("access_token", "")
                self._refresh_token = data.get("refresh_token", "")
                self.profile_name   = data.get("profile_name", "")
                # Декодируем email и user_id из JWT payload
                payload = self._decode_jwt_payload(self._access_token)
                user = payload.get("user") or {}
                self.account_email  = user.get("email") or data.get("account_email", "")
                self.ggsel_user_id  = str(user.get("id") or payload.get("sub") or "")
                # exp: предпочитаем JWT payload.exp — файл может содержать exp=0
                exp_file = data.get("exp", 0)
                exp_jwt  = int(payload.get("exp") or 0)
                self._token_exp = exp_jwt if exp_jwt > exp_file else exp_file
                log.info("api_client: токены загружены (%s / %s), exp=%d",
                         self.profile_name, self.account_email, self._token_exp)
                return
            except Exception as e:
                log.warning("api_client: не удалось прочитать токены из файла: %s", e)

        # Дефолтные токены из браузера
        self._access_token  = _DEFAULT_ACCESS_TOKEN
        self._refresh_token = _DEFAULT_REFRESH_TOKEN
        payload = self._decode_jwt_payload(self._access_token)
        # Берём exp из JWT payload — надёжнее захардкоженной константы
        exp_jwt = int(payload.get("exp") or 1789336235)
        self._token_exp = exp_jwt
        user = payload.get("user") or {}
        self.account_email = user.get("email", "")
        self.ggsel_user_id = str(user.get("id") or payload.get("sub") or "")
        self.profile_name  = "default"
        log.info("api_client: используем дефолтные токены (%s)", self.account_email)

    def _save_tokens(self):
        try:
            _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Если exp не задан или уже истёк — перечитываем из JWT payload
            exp_to_save = self._token_exp
            if exp_to_save <= int(time.time()):
                payload = self._decode_jwt_payload(self._access_token)
                exp_from_jwt = int(payload.get("exp") or 0)
                if exp_from_jwt > int(time.time()):
                    exp_to_save = exp_from_jwt
                    self._token_exp = exp_from_jwt
                    log.info("api_client: exp восстановлен из JWT: %d (%dd)",
                             exp_from_jwt, (exp_from_jwt - int(time.time())) // 86400)
            _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_text(json.dumps({
                "access_token":  self._access_token,
                "refresh_token": self._refresh_token,
                "exp":           exp_to_save,
                "updated_at":    int(time.time()),
            }, indent=2))
        except Exception as e:
            log.warning("api_client: не удалось сохранить токены: %s", e)

    def update_tokens(self, access_token: str, refresh_token: str = "", exp: int = 0):
        """Обновить токены (например, вытащенные из браузера через CDP)."""
        self._access_token  = access_token
        if refresh_token:
            self._refresh_token = refresh_token
        if exp:
            self._token_exp = exp
        self._save_tokens()
        log.info("api_client: токены обновлены вручную")

    @property
    def is_token_valid(self) -> bool:
        if not self._access_token:
            return False
        # Считаем истёкшим за 5 минут до реального expiry
        return time.time() < self._token_exp - 300

    def _try_refresh(self) -> bool:
        """Попробовать обновить access_token через refresh_token."""
        if not self._refresh_token:
            return False
        try:
            r = httpx.post(
                f"{SITE_BASE}/api/auth/refresh",
                json={"refresh_token": self._refresh_token},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                at = data.get("access_token", "")
                rt = data.get("refresh_token", self._refresh_token)
                exp_in = data.get("expires_in", 31 * 86400)
                if at:
                    self._access_token  = at
                    self._refresh_token = rt
                    self._token_exp     = int(time.time()) + exp_in
                    self._save_tokens()
                    log.info("api_client: токен обновлён")
                    return True
        except Exception as e:
            log.warning("api_client: refresh failed: %s", e)
        return False

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def _get(self, path: str, params: dict = None, host: str = API_BASE) -> Optional[dict]:
        if not _HTTPX_OK:
            return None
        try:
            r = httpx.get(f"{host}{path}", headers=self._headers(),
                          params=params, timeout=15)
            if r.status_code == 401:
                if self._try_refresh():
                    r = httpx.get(f"{host}{path}", headers=self._headers(),
                                  params=params, timeout=15)
                else:
                    log.warning("api_client: 401 и refresh не помог")
                    return None
            if r.status_code == 200:
                return r.json()
            log.warning("api_client GET %s → %d", path, r.status_code)
        except Exception as e:
            log.warning("api_client GET %s error: %s", path, e)
        return None

    def _post(self, path: str, body: dict, host: str = API_BASE) -> Optional[dict]:
        if not _HTTPX_OK:
            return None
        try:
            r = httpx.post(f"{host}{path}", headers=self._headers(),
                           json=body, timeout=15)
            if r.status_code == 401:
                if self._try_refresh():
                    r = httpx.post(f"{host}{path}", headers=self._headers(),
                                   json=body, timeout=15)
                else:
                    return None
            if r.status_code == 200:
                return r.json()
            log.warning("api_client POST %s → %d: %s", path, r.status_code, r.text[:200])
        except Exception as e:
            log.warning("api_client POST %s error: %s", path, e)
        return None

    # ── Публичный API ─────────────────────────────────────────────────────────

    def get_products(
        self,
        category_slug: str = "",
        search: str = "",
        page: int = 1,
        limit: int = 50,
        currency: str = "wmz",
        sort: str = "sortByRec",
    ) -> List[ApiProduct]:
        """
        Получить список товаров из каталога.

        category_slug — слаг категории (например 'games-steam', 'spotify-premium')
        search        — поисковый запрос
        page          — страница (1-based)
        limit         — товаров на страницу (макс ~100)
        """
        body: Dict[str, Any] = {
            "limit":                 limit,
            "content_type_ids":     [],
            "search_after":         [],
            "with_filters":         True,
            "is_preorders":         False,
            "ab_test_without_emoji": True,
            "sort":                  sort,
            "query_string":          search or "",
            "with_forbidden":        False,
            "min_price":             "",
            "max_price":             "",
            "lang":                  "ru",
            "is_russian_ip":         1,
            "page":                  page,
        }
        if category_slug:
            body["category_url"] = category_slug

        data = self._post("/elastic/goods/categories", body)
        if not data or not data.get("data"):
            return []

        items = data["data"].get("items") or data["data"].get("goods") or []
        return [self._parse_api_product(item) for item in items if item]

    def get_products_by_type(
        self,
        content_type_id: int,
        page: int = 1,
        limit: int = 100,
        currency: str = "wmz",
        sort: str = "sortByRec",
    ) -> List[ApiProduct]:
        """
        Товары по числовому content_type_id — покрывает ВСЕ дочерние подкатегории.
        Используется в full scan для обхода всего каталога.

        content_type_id:
          2=Keys  48=Gifts  19=DLC  54=Purchasing-for-account
          1=Accounts  10=Item  25=Rent  33=Activation
          9=Currency  8=Cards  11=Services  18=Subscriptions
        """
        body: Dict[str, Any] = {
            "lang":             "ru",
            "currency":         currency,
            "limit":            limit,
            "page":             page,
            "sort":             sort,
            "query_string":     "",
            "content_type_ids": [content_type_id],
            "search_after":     [],
            "with_filters":     False,
            "is_preorders":     False,
            "ab_test_without_emoji": True,
            "with_forbidden":   False,
            "min_price":        "",
            "max_price":        "",
            "is_russian_ip":    1,
        }
        data = self._post("/elastic/goods/categories", body)
        if not data or not data.get("data"):
            return []
        items = data["data"].get("items") or data["data"].get("goods") or []
        total = data["data"].get("total", 0)
        log.debug("get_products_by_type ct=%d page=%d: %d/%d",
                  content_type_id, page, len(items), total)
        return [self._parse_api_product(i) for i in items if i]

    def get_total_by_type(self, content_type_id: int) -> int:
        """Сколько всего товаров в категории content_type_id (один запрос с limit=1)."""
        data = self._post("/elastic/goods/categories", {
            "lang": "ru", "currency": "wmz", "limit": 1, "page": 1,
            "sort": "sortByRec", "query_string": "",
            "content_type_ids": [content_type_id], "search_after": [],
            "with_filters": False, "is_preorders": False,
            "ab_test_without_emoji": True, "with_forbidden": False,
            "min_price": "", "max_price": "", "is_russian_ip": 1,
        })
        if not data or not data.get("data"):
            return 0
        return int(data["data"].get("total") or 0)

    def get_bestsellers(
        self,
        category_slug: str = "",
        limit: int = 50,
        currency: str = "wmz",
    ) -> List[ApiProduct]:
        """Бестселлеры."""
        body: Dict[str, Any] = {"lang": "en", "currency": currency, "limit": limit}
        if category_slug:
            body["category"] = category_slug

        data = self._post("/elastic/goods/bestsellers", body)
        if not data or not data.get("data"):
            return []
        items = data["data"].get("items") or []
        return [self._parse_api_product(item) for item in items if item]

    def get_price(self, good_id: int, currency: str = "wmz") -> Optional[float]:
        """Актуальная цена товара."""
        data = self._get(f"/goods/{good_id}/price", params={"currency": currency})
        if data and data.get("data"):
            return data["data"].get("amount")
        return None

    def get_categories(self) -> List[Dict[str, Any]]:
        """Список категорий.
        Поля: id, url (slug), title, goods_name.
        """
        data = self._get("/main/content-types", params={"lang": "en"})
        if data and data.get("data"):
            cats = data["data"] if isinstance(data["data"], list) else []
            # Нормализуем поле name для совместимости
            for c in cats:
                if isinstance(c, dict) and 'name' not in c:
                    c['name'] = c.get('title', c.get('goods_name', ''))
            return cats
        return []

    def get_account(self) -> Optional[Dict[str, Any]]:
        """Данные аккаунта (имя, email, баланс)."""
        data = self._get("/accounts", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"]
        return None

    def get_bestsellers(
        self,
        category_slug: str = "",
        limit: int = 50,
        currency: str = "wmz",
    ) -> List["ApiProduct"]:
        """Бестселлеры. POST /elastic/goods/bestsellers."""
        body: Dict[str, Any] = {"lang": "en", "currency": currency, "limit": limit}
        if category_slug:
            body["category"] = category_slug
        data = self._post("/elastic/goods/bestsellers", body)
        if not data or not data.get("data"):
            return []
        items = data["data"].get("items") or []
        return [self._parse_api_product(i) for i in items if i]

    def get_category_types(self) -> List[Dict[str, Any]]:
        """Типы категорий. GET /main/category-types."""
        data = self._get("/main/category-types", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def get_category_compilations(self) -> List[Dict[str, Any]]:
        """Подборки категорий. GET /main/category-types/compilation."""
        data = self._get("/main/category-types/compilation", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def get_menu(self) -> List[Dict[str, Any]]:
        """Меню сайта. GET /main/menu."""
        data = self._get("/main/menu", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def search_categories(
        self,
        query: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Поиск категорий. POST /elastic/goods/categories."""
        body: Dict[str, Any] = {"lang": "en", "limit": limit}
        if query:
            body["search"] = query
        data = self._post("/elastic/goods/categories", body)
        if not data or not data.get("data"):
            return []
        return data["data"] if isinstance(data["data"], list) else []

    def get_lotteries(self) -> List[Dict[str, Any]]:
        """Активные розыгрыши. GET /lotteries/active."""
        data = self._get("/lotteries/active", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def get_current_draw(self) -> Optional[Dict[str, Any]]:
        """Текущий розыгрыш. GET /accounts/current-draw."""
        data = self._get("/accounts/current-draw", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"]
        return None

    def get_currencies(self) -> List[Dict[str, Any]]:
        """Курсы валют. GET /currencies.
        Возвращает: [{name: 'wmr', rate: '1'}, {name: 'wmz', rate: '83.8'}, {name: 'wme', rate: '96.7'}]
        """
        data = self._get("/currencies")
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def get_currency_rates(self) -> Dict[str, float]:
        """Курсы валют в удобном формате: {'wmr': 1.0, 'wmz': 83.8, 'wme': 96.7}."""
        rates = {}
        for item in self.get_currencies():
            try:
                rates[item["name"]] = float(item["rate"])
            except (KeyError, ValueError):
                pass
        return rates

    def get_favorites(self) -> List[Dict[str, Any]]:
        """Избранное текущего пользователя. GET /accounts/favorites."""
        data = self._get("/accounts/favorites", params={"lang": "en"})
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def add_favorite(self, good_id: int) -> bool:
        """Добавить в избранное. POST /accounts/favorites."""
        data = self._post("/accounts/favorites", {"id_goods": good_id})
        return bool(data and data.get("success"))

    def remove_favorite(self, good_id: int) -> bool:
        """Удалить из избранного. DELETE /accounts/favorites."""
        if not _HTTPX_OK:
            return False
        try:
            r = httpx.delete(
                f"{API_BASE}/accounts/favorites",
                headers=self._headers(),
                json={"id_goods": good_id},
                timeout=15,
            )
            return r.status_code in (200, 204)
        except Exception as e:
            log.warning("api_client DELETE /accounts/favorites: %s", e)
            return False

    # ── back-office.ggsel.com ─────────────────────────────────────────────────

    def get_orders(self, page: int = 1, limit: int = 20) -> List[Dict[str, Any]]:
        """История заказов покупателя. POST /api_customer/v1/orders."""
        data = self._post(
            "/api_customer/v1/orders",
            {"page": page, "limit": limit, "lang": "en"},
            host=BACK_OFFICE,
        )
        if data and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    def get_order(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Детали заказа. GET /api_customer/v1/orders/{uuid}."""
        data = self._get(f"/api_customer/v1/orders/{uuid}", host=BACK_OFFICE)
        if data and data.get("data"):
            return data["data"]
        return None

    def get_me(self) -> Optional[Dict[str, Any]]:
        """Профиль через back-office. GET /api_customer/v1/users/me."""
        data = self._get("/api_customer/v1/users/me", host=BACK_OFFICE)
        if data and data.get("data"):
            return data["data"]
        return None

    def get_product_detail_from_html(self, url: str) -> Optional[ProductDetail]:
        """
        Получить детали товара через HTML страницы.
        Данные в LD+JSON — рендерятся сервером (SSR), API не нужен.
        """
        if not _CFFI_OK and not _HTTPX_OK:
            return None

        try:
            if _CFFI_OK:
                # curl-cffi для обхода Qrator если нужно
                r = cffi_requests.get(url, impersonate="chrome120", timeout=15,
                                      cookies={"access_token": self._access_token})
            else:
                r = httpx.get(url, timeout=15,
                              cookies={"access_token": self._access_token})

            if r.status_code != 200:
                return None

            return self._parse_ld_json(r.text)

        except Exception as e:
            log.warning("api_client get_product_detail %s: %s", url, e)
            return None

    def get_product_detail_batch(
        self, products: List[ApiProduct]
    ) -> Dict[int, ProductDetail]:
        """
        Получить детали для списка товаров.
        Возвращает словарь {id_goods: ProductDetail}.
        """
        result = {}
        for prod in products:
            detail = self.get_product_detail_from_html(prod.full_url)
            if detail:
                result[prod.id_goods] = detail
        return result

    # ── Внутренние ────────────────────────────────────────────────────────────

    def _parse_api_product(self, item: dict) -> ApiProduct:
        return ApiProduct(
            id_goods        = item.get("id_goods", 0),
            name            = item.get("name", ""),
            url             = item.get("url", ""),
            image           = item.get("image", ""),
            price_wmz       = float(item.get("price_wmz") or item.get("price_wmz_for_one") or 0),
            price_wmr       = float(item.get("price_wmr") or item.get("price_wmr_for_one") or 0),
            price_wme       = float(item.get("price_wme") or item.get("price_wme_for_one") or 0),
            cnt_sell        = int(item.get("cnt_sell") or 0),
            is_active       = bool(item.get("is_active", True)),
            autoselling     = bool(item.get("autoselling", False)),
            is_preorder     = bool(item.get("is_preorder", False)),
            sale            = item.get("sale"),
            content_type_id = item.get("content_type_id"),
            rating          = float(item.get("rating") or 0) or None,
            id_seller       = int(item.get("id_seller") or 0),
            seller_name     = item.get("seller_name", ""),
            id_section      = int(item.get("id_section") or 0),
            search_title    = item.get("search_title", ""),
            raw             = item,
        )

    def _parse_ld_json(self, html: str) -> Optional[ProductDetail]:
        """Извлечь данные товара из LD+JSON в HTML."""
        matches = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        )
        for raw in matches:
            try:
                d = json.loads(raw.strip())
            except Exception:
                continue

            if not isinstance(d, dict) or d.get("@type") != "Product":
                continue

            offers = d.get("offers", {})
            agg    = d.get("aggregateRating", {})

            # Цена из priceSpecification (USD)
            price = None
            price_cur = ""
            specs = offers.get("priceSpecification", [])
            for spec in specs:
                if isinstance(spec, dict) and spec.get("priceCurrency") in ("USD", "wmz"):
                    price     = float(spec.get("price", 0) or 0)
                    price_cur = spec.get("priceCurrency", "")
                    break
            if price is None:
                price     = float(offers.get("price", 0) or 0)
                price_cur = offers.get("priceCurrency", "")

            # Seller URL
            seller_obj = offers.get("seller", {})
            seller_name = seller_obj.get("name", "") if isinstance(seller_obj, dict) else ""

            # In stock
            avail = offers.get("availability", "")
            in_stock = "InStock" in avail if avail else True

            return ProductDetail(
                name          = d.get("name", "").replace("$ $", "").strip(),
                description   = d.get("description", ""),
                image         = d.get("image", ""),
                rating        = float(agg.get("ratingValue", 0) or 0) or None,
                reviews_count = int(agg.get("reviewCount", 0) or 0) or None,
                price         = price,
                price_currency= price_cur,
                in_stock      = in_stock,
                seller_name   = seller_name,
                seller_url    = offers.get("url", ""),
            )

        return None

    # ── Совместимость с parser_engine.Product ─────────────────────────────────

    def to_engine_product(
        self,
        api_prod: ApiProduct,
        detail: Optional[ProductDetail] = None,
        category: str = "",
    ):
        """
        Конвертировать ApiProduct → parser_engine.Product.
        Импорт parser_engine делается здесь чтобы избежать циклического импорта.
        """
        try:
            from .parser_engine import Product
        except ImportError:
            return None

        p = Product()
        p.external_id    = str(api_prod.id_goods)
        p.name           = (detail.name if detail and detail.name else api_prod.name).strip()
        p.price          = api_prod.price_wmr   # основная цена в RUB
        p.currency       = "RUB"
        p.url            = api_prod.full_url
        p.image_url      = api_prod.image
        p.sales_count    = api_prod.cnt_sell
        p.in_stock       = api_prod.is_active
        p.category       = category

        p.seller = (detail.seller_name if (detail and detail.seller_name) else api_prod.seller_name)
        p.rating = (detail.rating if (detail and detail.rating) else api_prod.rating)

        if detail:
            p.reviews_count = detail.reviews_count

        p.extra = {
            "price_wmz":     api_prod.price_wmz,
            "price_wmr":     api_prod.price_wmr,
            "price_wme":     api_prod.price_wme,
            "autoselling":   api_prod.autoselling,
            "is_preorder":   api_prod.is_preorder,
            "content_type":  api_prod.content_type_id,
            "sale":          api_prod.sale,
            "id_seller":     api_prod.id_seller,
            "seller_name":   api_prod.seller_name,
            "id_section":    api_prod.id_section,
            "search_title":  api_prod.search_title,
            "rating_raw":    api_prod.rating,
        }

        return p


    def get_products_by_section(
        self,
        section_id: int,
        content_type_id: int = 0,
        page: int = 1,
        limit: int = 50,
        currency: str = "wmz",
    ) -> List["ApiProduct"]:
        """Товары по конкретной секции (id_section / подкатегории)."""
        body: Dict[str, Any] = {
            "lang": "en", "currency": currency,
            "limit": limit, "page": page, "sort": "sortByRec",
            "query_string": "", "search_after": [],
            "with_filters": False, "is_preorders": False,
            "with_forbidden": False, "min_price": "", "max_price": "",
            "section_ids": [section_id],
        }
        if content_type_id:
            body["content_type_ids"] = [content_type_id]
        data = self._post("/elastic/goods/categories", body)
        if not data or not data.get("data"):
            return []
        return [self._parse_api_product(i) for i in (data["data"].get("items") or []) if i]

    def get_total_by_section(self, section_id: int, content_type_id: int = 0) -> int:
        """Сколько товаров в конкретной секции (id_section)."""
        body: Dict[str, Any] = {
            "lang": "en", "currency": "wmz", "limit": 1, "page": 1,
            "sort": "sortByRec", "query_string": "", "search_after": [],
            "with_filters": False, "section_ids": [section_id],
        }
        if content_type_id:
            body["content_type_ids"] = [content_type_id]
        data = self._post("/elastic/goods/categories", body)
        if data and data.get("data"):
            return int(data["data"].get("total", 0))
        return 0

    def get_product_detail_api(self, good_id: int):
        """Детальная карточка товара: GET /goods/{id}.
        Возвращает (ProductDetail, cnt_sell, seller_rating, good_reviews, bad_reviews).
        """
        data = self._get(f"/goods/{good_id}", params={"lang": "en"})
        if not data or not data.get("data"):
            return None, None, None, None, None
        d = data["data"]
        seller_obj    = d.get("seller") or {}
        seller_name   = seller_obj.get("name_seller", "") or seller_obj.get("name", "")
        seller_rating = None
        sr = (seller_obj.get("statistics") or {}).get("rating") or seller_obj.get("rating")
        if sr:
            try: seller_rating = float(sr)
            except: pass
        rating = None
        rv = d.get("rating")
        if rv:
            try: rating = float(rv)
            except: pass
        good_r = int(d.get("cnt_goodresponses") or 0)
        bad_r  = int(d.get("cnt_badresponses") or 0)
        reviews_count = good_r + bad_r if (good_r + bad_r) > 0 else None
        cnt_sell_api  = None
        cs = d.get("cnt_sell")
        if cs is not None:
            try: cnt_sell_api = int(cs)
            except: pass
        desc = d.get("info") or d.get("add_info") or d.get("description") or ""
        img  = d.get("images", "")
        if isinstance(img, list): img = img[0] if img else ""
        return ProductDetail(
            name=d.get("name", ""),
            description=desc,
            image=img or "",
            rating=rating,
            reviews_count=reviews_count,
            price=float(d.get("price_wmz") or 0),
            price_currency="wmz",
            in_stock=bool(d.get("is_active", True)),
            seller_name=seller_name,
            seller_url="",
        ), cnt_sell_api, seller_rating, good_r, bad_r


# ── Singleton helper ──────────────────────────────────────────────────────────

def get_client() -> GgselApiClient:
    return GgselApiClient.get()


# ── Быстрая проверка токена ───────────────────────────────────────────────────

def check_token() -> bool:
    """Проверить что текущий токен рабочий.

    exp=0 в файле токенов делает is_token_valid всегда False,
    поэтому сначала делаем живой запрос — он является
    единственным надёжным источником истины.
    """
    client = get_client()
    # Живой запрос — истечение по exp ненадёжно (exp=0 в файле)
    data = client.get_account()
    if data:
        log.info("api_client: токен рабочий, аккаунт: %s", data.get("nickname", "?"))
        return True
    # Если 401 / нет данных — пробуем refresh
    log.warning("api_client: токен не работает, пробуем refresh")
    return client._try_refresh()


# ── Мульти-аккаунт (для full scan) ─────────────────────────────────────────────

def load_all_accounts() -> List[dict]:
    """
    Загружает все аккаунты из data/ggsel_tokens.json.
    Основной токен + дополнительные из поля extra_tokens[].
    Если файла нет — возвращает один дефолтный аккаунт.
    """
    accounts: List[dict] = []
    if _TOKEN_FILE.exists():
        try:
            data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
            if data.get("access_token"):
                accounts.append({
                    "name":          data.get("profile_name", "main"),
                    "access_token":  data["access_token"],
                    "refresh_token": data.get("refresh_token", ""),
                    "exp":           data.get("exp", 0),
                })
            for extra in data.get("extra_tokens", []):
                if extra.get("access_token"):
                    accounts.append({
                        "name":          extra.get("profile_name", f"extra_{len(accounts)}"),
                        "access_token":  extra["access_token"],
                        "refresh_token": extra.get("refresh_token", ""),
                        "exp":           extra.get("exp", 0),
                    })
        except Exception as e:
            log.warning("load_all_accounts: %s", e)

    if not accounts:
        accounts.append({
            "name":          "default",
            "access_token":  _DEFAULT_ACCESS_TOKEN,
            "refresh_token": _DEFAULT_REFRESH_TOKEN,
            "exp":           1789336235,
        })
    log.info("load_all_accounts: %d аккаунт(ов): %s",
             len(accounts), [a["name"] for a in accounts])
    return accounts


def make_client(account: dict) -> GgselApiClient:
    """
    Создаёт независимый (не singleton) клиент для конкретного аккаунта.
    Используется в мульти-воркерном full scan: каждый воркер получает свой клиент.
    """
    c = object.__new__(GgselApiClient)
    c._access_token  = account["access_token"]
    c._refresh_token = account.get("refresh_token", "")
    c._token_exp     = account.get("exp", 0)
    c._client        = None
    c.profile_name   = account.get("name", "")
    c.account_email  = ""
    c.ggsel_user_id  = ""
    return c
