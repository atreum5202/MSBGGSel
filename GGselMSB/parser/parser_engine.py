"""
parser/parser_engine.py
=======================
Адаптированный движок парсера ggsel.net, перенесённый из GGSeller.

Отличия от GGSeller/services/parser/ggsel_parser_v2.py:
  - Убран MSB (QratorCookieMiddleware) — не требует отдельного микросервиса
  - Убран profile_warmer — нагрев профиля на разовый запуск не нужен
  - Убран auto-scheduler — парсер запускается ТОЛЬКО вручную из GUI
  - Оставлено критичное: curl-cffi против Qrator, exponential backoff, smart sleep
  - Лимит на количество спаршенных товаров через BoundedQueue

Безопасность:
  - single-worker mode (1 запрос за раз, никаких параллельных сессий)
  - REQUEST_DELAY 4-6 сек между страницами
  - exponential backoff при 429 (Retry-After → base*2^attempt, cap 5 мин)
  - bounded quantity: парсер остановится при достижении лимита
  - Qrator detection + 30 сек пауза перед retry
  - не падает на сетевых ошибках, не уходит в бесконечный цикл
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import random
import re
import sqlite3
import sys
import time
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ── Опциональные зависимости (graceful degradation) ─────────────────────────
try:
    from bs4 import BeautifulSoup, Tag
    _BS4_OK = True
except ImportError:
    _BS4_OK = False

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except ImportError:
    _CFFI_OK = False

try:
    import httpx as _httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

# ── Путь к пакету (для относительных импортов внутри parser/) ────────────────
_PARSER_DIR = Path(__file__).resolve().parent
if str(_PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(_PARSER_DIR))


# ═══════════════════════════════════════════════════════════════════════════
#  Логирование
# ═══════════════════════════════════════════════════════════════════════════
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path(os.getenv("PARSER_LOG_FILE", str(LOG_DIR / "parser.log")))

log = logging.getLogger("ggselv7.parser")
if not log.handlers:
    log.setLevel(logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    log.addHandler(sh)

    # Дублируем хэндлеры на дочерние/сиблинговые логгеры —
    # иначе их сообщения уходят в /dev/null (нет хэндлеров, root пустой).
    for sibling_name in (
        "cdp_cookies", "profile_pool", "profile_pool.stats",
        "msb_client", "msb_cookies", "msb_fetcher",
        "morelogin_gemini",
    ):
        sl = logging.getLogger(sibling_name)
        if not sl.handlers:
            sl.setLevel(logging.INFO)
            sl.addHandler(fh)
            sl.addHandler(sh)
            sl.propagate = False


# ═══════════════════════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════════════════════
BASE_URL = "https://ggsel.net"
KNOWN_CATEGORIES = [
    # ══════════════════════════════════════════
    # ГЛАВНЫЕ РАЗДЕЛЫ ggsel.net/catalog
    # ══════════════════════════════════════════
    "igry-po-nazvaniyu",        # Все игры (103 772 товара)
    "game-currency",            # Внутриигровые товары и валюта
    "mobile-games",             # Мобильные игры (4 657 товаров)
    "podpisochnye-servisy",     # Сервисы и соцсети
    "programs-new",             # Программное обеспечение (5 151 товар)

    # ══════════════════════════════════════════
    # ИГРЫ — подкатегории
    # ══════════════════════════════════════════
    "grand-theft-auto-5-first",
    "grand-theft-auto-vi",
    "playstation-games",
    "xbox-game-pass-1",
    "minecraft-10054",
    "valorant-9149",
    "roblox",
    "robux",
    "dead-by-daylight-9610",
    "fortnite",
    "brawl-stars-9063",
    "genshin-impact",
    "apex-legends-1",
    "arc-raiders",
    "red-dead-redemption-2-10191",
    "helldivers-2",
    "cyberpunk-2077",
    "clash-royale-9584",
    "battlefield-6-160525",
    "ea-sports-fc-26-fifa-26",
    "arena-breakout-infinite",
    "europa-universalis-v",
    "dispatch",
    "games-anno-117-pax-romana",
    "games-steam",
    "other-1",

    # ══════════════════════════════════════════
    # МОБИЛЬНЫЕ ИГРЫ — подкатегории
    # ══════════════════════════════════════════
    "mobile-legends",
    "pubg-mobile",
    "clash-of-clans-9576",
    "call-of-duty-mobile",
    "standoff-2",
    "zenless-zone-zero",
    "world-of-tanks-blitz",
    "albion-online",
    "honkai-star-rail",

    # ══════════════════════════════════════════
    # ПРОГРАММНОЕ ОБЕСПЕЧЕНИЕ — подкатегории
    # ══════════════════════════════════════════
    "microsoft-office",
    "adobe-creative-cloud",
    "capcut",
    "microsoft-office-365",
    "antivirus-eset",
    "unlocktool",
    "voicemod-pro",
    "exitlag",
    "jetbrains",
    "autodesk",
    "malwarebytes-premium",
    "os",
    "software",
    "software-for-gamers-and-streaming",
    "programming-software",
    "seo-software",

    # ══════════════════════════════════════════
    # СЕРВИСЫ — подкатегории
    # ══════════════════════════════════════════
    "spotify-premium",
    "apple-id",
    "subscriptions-for-all-occasions",
    "other-games-currency",
]

# ── SAFETY: rate limits (must be safe-by-default) ────────────────────────────
# GGSeller подвергается Qrator — слишком частые запросы = бан.
# Значения ENV-управляемые; дефолты = консервативные.
REQUEST_DELAY       = float(os.getenv("PARSER_REQUEST_DELAY", "4.0"))
REQUEST_DELAY_JITTER = float(os.getenv("PARSER_REQUEST_DELAY_JITTER", "2.0"))
CHALLENGE_BACKOFF   = float(os.getenv("PARSER_CHALLENGE_BACKOFF", "30.0"))

# 429 backoff
BACKOFF_BASE_SECS    = float(os.getenv("PARSER_BACKOFF_BASE", "10.0"))
BACKOFF_FACTOR       = float(os.getenv("PARSER_BACKOFF_FACTOR", "2.0"))
BACKOFF_CAP_SECS     = float(os.getenv("PARSER_BACKOFF_CAP", "300.0"))
BACKOFF_JITTER_SECS  = float(os.getenv("PARSER_BACKOFF_JITTER", "5.0"))
BACKOFF_MAX_ATTEMPTS = int(os.getenv("PARSER_BACKOFF_MAX_ATTEMPTS", "3"))

# Hard cap на количество страниц и товаров за один запуск
DEFAULT_MAX_PAGES = int(os.getenv("PARSER_MAX_PAGES", "3"))
MAX_QUANTITY_HARD_CAP = int(os.getenv("PARSER_MAX_QUANTITY", "100"))

# Признаки WAF challenge
QRATOR_SIGNATURES = [
    "qrator", "just a moment", "ddos-guard", "please wait",
    "checking your browser", "enable javascript",
    "__cf_chl", "challenge-form",
]

# Фильтры профита
MIN_MARGIN = float(os.getenv("PARSER_MIN_MARGIN", "0.0"))
MIN_PROFIT_SCORE = float(os.getenv("PARSER_MIN_PROFIT_SCORE", "0.0"))


# ═══════════════════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Product:
    external_id: str = ""
    name: str = ""
    price: float = 0.0
    currency: str = "RUB"
    url: str = ""
    seller: str = ""
    seller_id: str = ""
    seller_rating: Optional[float] = None
    rating: Optional[float] = None
    sales_count: Optional[int] = None
    reviews_count: Optional[int] = None
    category: str = ""
    image_url: str = ""
    in_stock: bool = True
    profit_score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    success: bool
    url: str
    html: str = ""
    status_code: Optional[int] = None
    error: str = ""
    duration_ms: int = 0
    strategy_used: str = ""
    is_challenge: bool = False


@dataclass
class ParseResult:
    success: bool = False
    page_type: str = "unknown"
    url: str = ""
    products: List[Product] = field(default_factory=list)
    category_name: str = ""
    seller_name: str = ""
    next_page_url: Optional[str] = None
    total_pages: Optional[int] = None
    errors: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  Safety: stop event (для ручной остановки парсера)
# ═══════════════════════════════════════════════════════════════════════════
class _StopEvent:
    """Потокобезопасный флаг остановки. Ставится из GUI при Stop."""
    def __init__(self):
        self._flag = threading.Event()

    def set(self) -> None:
        self._flag.set()

    def is_set(self) -> bool:
        return self._flag.is_set()

    def clear(self) -> None:
        self._flag.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  Utilities
# ═══════════════════════════════════════════════════════════════════════════
def _smart_sleep(base: Optional[float] = None, jitter: Optional[float] = None) -> None:
    """Пауза с логнормальным распределением."""
    b = base if base is not None else REQUEST_DELAY
    j = jitter if jitter is not None else REQUEST_DELAY_JITTER
    import math
    mu = math.log(b)
    sigma = 0.3
    delay = random.lognormvariate(mu, sigma)
    delay = max(b * 0.5, min(delay, b + j * 2))
    time.sleep(delay)


def _is_challenge_page(html: str, status_code: Optional[int] = None) -> bool:
    if not html:
        return False
    lower = html.lower()
    for sig in QRATOR_SIGNATURES:
        if sig in lower:
            return True
    if status_code == 200 and "productcard-module" not in lower and "<h1" not in lower:
        return True
    return False


def _parse_retry_after(headers: Any) -> Optional[float]:
    raw = None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        pass
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import timezone, datetime as _dt
        dt = parsedate_to_datetime(raw)
        return max(0.0, (dt - _dt.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _backoff_sleep(attempt: int, resp_headers: Any = None, fetcher_name: str = "") -> float:
    server_wait = _parse_retry_after(resp_headers) if resp_headers is not None else None
    if server_wait is not None:
        wait = server_wait + random.uniform(0, BACKOFF_JITTER_SECS)
        log.warning("[%s] 429 — Retry-After %.0fs. Пауза %.1fs (попытка %d/%d)",
                    fetcher_name or "Fetcher", server_wait, wait, attempt + 1, BACKOFF_MAX_ATTEMPTS)
        time.sleep(wait)
        return wait
    exp = BACKOFF_BASE_SECS * (BACKOFF_FACTOR ** attempt)
    capped = min(exp, BACKOFF_CAP_SECS)
    wait = capped + random.uniform(0, BACKOFF_JITTER_SECS)
    log.warning("[%s] 429 — backoff %.1fs (попытка %d/%d)",
                fetcher_name or "Fetcher", wait, attempt + 1, BACKOFF_MAX_ATTEMPTS)
    time.sleep(wait)
    return wait


# ═══════════════════════════════════════════════════════════════════════════
#  Fetcher: curl-cffi (имитация TLS fingerprint Chrome)
# ═══════════════════════════════════════════════════════════════════════════
_UA_POOL = [
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "impersonate": "chrome131"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
     "impersonate": "chrome136"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "impersonate": "chrome131"},
]


def _random_browser_headers(is_navigation: bool = True) -> dict:
    entry = random.choice(_UA_POOL)
    sec_fetch_site = "none" if is_navigation else "same-origin"
    return {
        "User-Agent": entry["ua"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": "https://ggsel.net/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": sec_fetch_site,
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _parse_proxy_list() -> list:
    """
    Парсит PARSER_PROXIES из ENV.
    Формат: "socks5://user:pass@ip:port,socks5://... , http://..."
    Возвращает список URL прокси. Пустой список если ничего не задано.
    """
    raw = os.getenv("PARSER_PROXIES", "").strip()
    if not raw:
        return []
    out = []
    for p in raw.split(","):
        p = p.strip()
        if p:
            out.append(p)
    return out


def _mask_proxy(p: str) -> str:
    """Скрывает user:pass в proxy URL для логов."""
    if not p:
        return ""
    if "@" in p:
        scheme_user, rest = p.split("@", 1)
        scheme = scheme_user.split("://", 1)[0] if "://" in scheme_user else scheme_user
        return f"{scheme}://***@{rest}"
    return p


# Каталог для локально скачанных фото товаров. Лежит в static/, чтобы Flask отдавал
# файлы напрямую без отдельного прокси.
_PRODUCT_IMAGE_DIR = Path(__file__).resolve().parent.parent / "static" / "products"
_DOWNLOAD_IMAGES_ENABLED = os.getenv("PARSER_DOWNLOAD_IMAGES", "true").lower() == "true"


def _download_product_image(url: str, referer: str = BASE_URL, timeout: float = 12.0) -> Optional[bytes]:
    """
    Лучше стараться скачать фото товара сразу во время парсинга, тем же способом,
    что и HTML (curl-cffi с имитацией TLS-отпечатка Chrome). Зачем: админка
    потом отдаёт уже скачанный локальный файл, без обращения к CDN при каждом
    просмотре страницы. Не критично для основного пайплайна — любая ошибка просто
    оставляет товар без локальной копии (фронтенд тогда сам попробует прокси).
    """
    if not url or not _CFFI_OK:
        return None
    try:
        entry = random.choice(_UA_POOL)
        proxies = _parse_proxy_list()
        proxy = random.choice(proxies) if proxies else None
        kwargs: Dict[str, Any] = {
            "headers": {
                "User-Agent": entry["ua"],
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                "Referer": referer or BASE_URL,
            },
            "impersonate": entry["impersonate"],
            "timeout": timeout,
            "allow_redirects": True,
        }
        if proxy:
            kwargs["proxy"] = proxy
        resp = cffi_requests.get(url, **kwargs)
        if resp.status_code != 200 or not resp.content:
            return None
        return resp.content
    except Exception as e:
        log.debug("[image_dl] %s: %s", url, e)
        return None


def _save_product_image_locally(url: str, product_id: str, referer: str = BASE_URL) -> Optional[str]:
    """
    Скачивает фото товара и сохраняет в static/products/. Возвращает относительный
    URL вида /static/products/<id>.<ext> или None при неудаче.
    """
    data = _download_product_image(url, referer=referer)
    if not data:
        return None
    try:
        _PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        ct_ext = os.path.splitext(urlparse(url).path)[1].lower()
        if ct_ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ct_ext = ".jpg"
        safe_id = "".join(c if c.isalnum() else "_" for c in str(product_id))[:120]
        filename = f"{safe_id}{ct_ext}"
        filepath = _PRODUCT_IMAGE_DIR / filename
        filepath.write_bytes(data)
        return f"/static/products/{filename}"
    except Exception as e:
        log.debug("[image_dl] сохранение %s: %s", product_id, e)
        return None


class CffiFetcher:
    """curl-cffi с rotation UA и ротацией прокси. Главная защита от Qrator."""
    name = "cffi"

    def __init__(self, timeout: float = 30.0, proxies: Optional[List[str]] = None):
        self._timeout = timeout
        # Если proxies не переданы — читаем из ENV
        self._proxies: List[str] = list(proxies) if proxies else _parse_proxy_list()
        self._session = None
        self._current_proxy: Optional[str] = None
        self._impersonate: str = ""
        self._available = False
        self._init()

    def _make_session(self, proxy: Optional[str] = None):
        """Создаёт новую сессию с заданным (или случайным) прокси + UA."""
        entry = random.choice(_UA_POOL)
        imp = entry["impersonate"]
        if proxy:
            try:
                sess = cffi_requests.Session(impersonate=imp, proxy=proxy)
            except TypeError:
                # старые версии curl-cffi не принимают proxy в Session
                sess = cffi_requests.Session(impersonate=imp)
        else:
            sess = cffi_requests.Session(impersonate=imp)
        sess.headers.update(_random_browser_headers())
        # Сохраним impersonate для логов + прокси для передачи в .get()
        self._impersonate = imp
        self._current_proxy = proxy
        return sess

    def _init(self):
        if not _CFFI_OK:
            log.warning("[CffiFetcher] curl-cffi не установлен: pip install curl-cffi")
            return
        try:
            proxy = random.choice(self._proxies) if self._proxies else None
            self._session = self._make_session(proxy)
            self._available = True
            if proxy:
                log.info("[CffiFetcher] OK (impersonate=%s, proxy=%s, pool=%d)",
                         self._impersonate, _mask_proxy(proxy), len(self._proxies))
            else:
                log.info("[CffiFetcher] OK (impersonate=%s, no proxy)",
                         self._impersonate)
        except Exception as e:
            log.warning("[CffiFetcher] Init failed: %s", e)

    @property
    def available(self) -> bool:
        return self._available

    def fetch(self, url: str) -> FetchResult:
        if not self._available:
            return FetchResult(False, url, error="not_available", strategy_used=self.name)

        last_result: Optional[FetchResult] = None
        for attempt in range(BACKOFF_MAX_ATTEMPTS):
            t0 = time.monotonic()
            # На каждой попытке — ротация прокси + пересоздание сессии
            if self._proxies:
                proxy = random.choice(self._proxies)
                if proxy != self._current_proxy:
                    try:
                        if self._session:
                            self._session.close()
                    except Exception:
                        pass
                    try:
                        self._session = self._make_session(proxy)
                    except Exception as e:
                        log.warning("[CffiFetcher] session recreate failed: %s", e)
            try:
                # На каждой попытке — обновляем headers (UA rotation)
                self._session.headers.update(_random_browser_headers())
                # curl-cffi .get() поддерживает proxies={"https": ..., "http": ...}
                # Если прокси не был принят Session — fallback на передачу в .get()
                kwargs = {"timeout": self._timeout, "allow_redirects": True}
                if self._current_proxy and self._proxies:
                    # Проверяем что proxy реально в сессии (новые версии curl-cffi
                    # принимают его в Session, и тогда передавать в get не нужно)
                    # Безопаснее передать — лишнее проигнорируется.
                    kwargs["proxies"] = {"http": self._current_proxy, "https": self._current_proxy}
                resp = self._session.get(url, **kwargs)
                ms = int((time.monotonic() - t0) * 1000)
                html = resp.text
                is_challenge = _is_challenge_page(html, resp.status_code)

                if resp.status_code == 200 and not is_challenge:
                    return FetchResult(True, url, html, resp.status_code,
                                       duration_ms=ms, strategy_used=self.name,
                                       is_challenge=False)

                last_result = FetchResult(False, url, html, resp.status_code,
                                          error=f"HTTP {resp.status_code}",
                                          duration_ms=ms, strategy_used=self.name,
                                          is_challenge=is_challenge)
                if resp.status_code == 429 and attempt < BACKOFF_MAX_ATTEMPTS - 1:
                    _backoff_sleep(attempt, resp.headers, self.name)
                    continue
                if is_challenge:
                    # 30+ сек пауза на Qrator challenge
                    pause = CHALLENGE_BACKOFF + random.uniform(0, 10)
                    log.warning("[CffiFetcher] Qrator challenge — пауза %.0fs", pause)
                    time.sleep(pause)
                return last_result
            except Exception as e:
                ms = int((time.monotonic() - t0) * 1000)
                # Прокси сдох — пробуем другой на следующей попытке
                err_text = str(e)[:200]
                if "proxy" in err_text.lower() or "socks" in err_text.lower() or "407" in err_text:
                    log.warning("[CffiFetcher] proxy error: %s — будет ротация", err_text)
                    if self._proxies and attempt < BACKOFF_MAX_ATTEMPTS - 1:
                        # Не возвращаем, пустим в следующую попытку
                        continue
                return FetchResult(False, url, error=err_text,
                                   duration_ms=ms, strategy_used=self.name)
        return last_result or FetchResult(False, url, error="backoff_exhausted",
                                          strategy_used=self.name)

    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
#  HTML parsing
# ═══════════════════════════════════════════════════════════════════════════
def _make_soup(html: str):
    if not _BS4_OK or not html:
        return None
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception:
            return None


def _extract_id_from_url(url: str) -> str:
    """Извлекает ID товара из URL вида /catalog/games/123456 или /product/123456."""
    if not url:
        return ""
    m = re.search(r"/(?:product|goods|item|lot|catalog/[^/]+)/(\d+)", url)
    if m:
        return m.group(1)
    # fallback — последний числовой сегмент
    parts = [p for p in url.rstrip("/").split("/") if p]
    for p in reversed(parts):
        if p.isdigit():
            return p
    return url


def _parse_price(text: str) -> Tuple[float, str]:
    """Парсит '1 234,56 ₽' → (1234.56, 'RUB')."""
    if not text:
        return 0.0, ""
    m = re.search(r"([\d\s]+(?:[.,]\d+)?)", text.replace("\u00a0", " ").replace(" ", " "))
    if not m:
        return 0.0, ""
    raw = m.group(1).replace(" ", "").replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return 0.0, ""
    cur = "RUB"
    upper = text.upper()
    if "USD" in upper or "$" in text:
        cur = "USD"
    elif "EUR" in upper or "€" in text:
        cur = "EUR"
    return val, cur


def _parse_sales(text: str) -> Optional[int]:
    """Парсит '1.2k продано' или '1234 продаж' → 1234."""
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(" ", " ")
    m = re.search(r"([\d.,]+)\s*([kкKК]?)\s*(?:продано|продаж|продажи|sold|sales)?", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).replace(",", ".")
    try:
        n = float(raw)
    except ValueError:
        return None
    if m.group(2).lower() in ("k", "к"):
        n *= 1000
    return int(n)


def _canonical_url(soup) -> str:
    """Возвращает <link rel='canonical'> или текущий URL."""
    if not soup:
        return ""
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        return link["href"]
    return ""


def _detect_page_type(soup, url: str) -> str:
    if not soup or not url:
        return "unknown"
    if "/catalog/" in url or "/category/" in url:
        return "category"
    if re.search(r"/sellers?/[\w-]+", url):
        return "seller"
    if re.search(r"/(?:product|goods|item|lot)/[\w-]+", url):
        return "product"
    return "unknown"


def _find_next_page(soup, current_url: str) -> Optional[str]:
    """Ищет ссылку на следующую страницу в пагинации."""
    if not soup:
        return None
    # Ищем ссылки с rel=next
    nxt = soup.find("a", rel="next")
    if nxt and nxt.get("href"):
        return nxt["href"]
    # Ищем по тексту "следующая" / "→"
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True).lower()
        if txt in ("→", "›", ">", "следующая", "next", "вперёд"):
            return a["href"]
    return None


def _calc_raw_score(sales_count: int, seller_rating: float, reviews_count: int, in_stock: bool) -> float:
    """
    Первичный скор на этапе листинга (без AI).
      raw = (sales * 0.5) + (rating * 20) + (reviews * 0.3)
      if in_stock: raw *= 1.2
    Нормализация в 0..100.
    """
    raw = (sales_count or 0) * 0.5 + (seller_rating or 0.0) * 20.0 + (reviews_count or 0) * 0.3
    if in_stock:
        raw *= 1.2
    # Нормализация: шкала 0..100 (max при 200 sales, 5.0 rating, 300 reviews, in_stock)
    return max(0.0, min(100.0, raw))


def _calc_my_price_fallback(price: float) -> float:
    """Дефолтная наценка 20%, округление до .99 (если AI недоступен)."""
    if not price or price <= 0:
        return 0.0
    return round(float(price) * 1.20 - 0.01, 2)


# ═══════════════════════════════════════════════════════════════════════════
#  Парсинг детальной страницы товара
# ═══════════════════════════════════════════════════════════════════════════
_DETAIL_IMG_SELECTORS = [
    "div[class*='gallery'] img[src]",
    "div[class*='image'] img[src]",
    "div[class*='slider'] img[src]",
    "[data-fancybox][href]",
    "a[data-fancybox] img[src]",
    "img.product-image[src]",
    ".swiper img[src]",
    ".carousel img[src]",
]
_DETAIL_DESC_SELECTORS = [
    "div[class*='description']",
    "div[class*='about']",
    "div[class*='detail']",
    "[itemprop='description']",
    ".product-desc",
    ".offer-description",
    "section.description",
]
_DETAIL_PROP_SELECTORS = [
    "table[class*='prop'] tr",
    "table[class*='char'] tr",
    "ul[class*='prop'] li",
    "ul[class*='char'] li",
    "dl[class*='prop'] dt + dd",
    "div[class*='spec'] tr",
    ".characteristics li",
    ".product-properties tr",
]
_DETAIL_SELLER_SELECTORS = [
    "a[href*='/seller/']",
    "a[href*='/shop/']",
    ".seller-name a[href]",
    ".seller a[href]",
    "a[class*='seller']",
]
_DETAIL_QTY_SELECTORS = [
    "span[class*='quantity']",
    "[data-quantity]",
    ".in-stock-count",
    ".stock-count",
    ".product-quantity",
]
_DETAIL_DATE_SELECTORS = [
    "[datetime]",
    "time[datetime]",
    "meta[itemprop='datePublished']",
    ".product-date",
    ".published-at",
]


def _parse_product_detail(html: str, base_url: str) -> dict:
    """
    Парсит детальную страницу товара ggsel.net.
    Возвращает dict с полями (все опциональны):
        images_json: str — JSON-массив URL
        original_desc: str
        properties_json: str — JSON-массив [{key, value}]
        quantity_available: int | None
        seller_url: str
        published_at: str
    """
    out: Dict[str, Any] = {}
    if not html or not _BS4_OK:
        return out
    soup = _make_soup(html)
    if soup is None:
        return out

    def _abs_url(u: str) -> str:
        if not u:
            return ""
        u = u.strip()
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return BASE_URL + u
        if not u.startswith("http"):
            return BASE_URL + "/" + u
        return u

    # ── 1. Изображения (галерея)
    imgs: List[str] = []
    seen_imgs: set = set()
    # 1a. selectors img[src]
    for sel in _DETAIL_IMG_SELECTORS:
        if "[href]" in sel:
            continue  # обработаем отдельно
        for el in soup.select(sel):
            src = el.get("src") or el.get("data-src") or el.get("data-original") or ""
            if src:
                src = _abs_url(src)
                if src and src not in seen_imgs:
                    seen_imgs.add(src)
                    imgs.append(src)
    # 1b. data-fancybox href
    for el in soup.select("[data-fancybox]"):
        href = el.get("href") or ""
        if href:
            href = _abs_url(href)
            if href and href not in seen_imgs:
                seen_imgs.add(href)
                imgs.append(href)
    # 1c. og:image как fallback
    if not imgs:
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            imgs.append(_abs_url(og["content"]))
    if imgs:
        out["images_json"] = json.dumps(imgs[:20], ensure_ascii=False)

    # ── 2. Полное описание
    for sel in _DETAIL_DESC_SELECTORS:
        for el in soup.select(sel):
            text = el.get_text("\n", strip=True)
            if text and len(text) >= 30:
                out["original_desc"] = text[:5000]
                break
        if out.get("original_desc"):
            break

    # ── 3. Характеристики (таблица / список)
    props: List[Dict[str, str]] = []
    seen_prop_keys: set = set()
    for sel in _DETAIL_PROP_SELECTORS:
        for el in soup.select(sel):
            if el.name == "tr":
                kvs = el.find_all(["th", "td"])
                if len(kvs) >= 2:
                    k = kvs[0].get_text(strip=True)
                    v = kvs[1].get_text(strip=True)
                else:
                    continue
            else:
                # li: "Key: Value" или отдельные dt/dd
                txt = el.get_text(" ", strip=True)
                m = re.match(r"^([^\:]+?):\s*(.+)$", txt)
                if m:
                    k, v = m.group(1).strip(), m.group(2).strip()
                else:
                    continue
            if not k or not v or len(k) > 80 or len(v) > 300:
                continue
            if k.lower() in seen_prop_keys:
                continue
            seen_prop_keys.add(k.lower())
            props.append({"key": k, "value": v})
        if len(props) >= 3:
            break
    if props:
        out["properties_json"] = json.dumps(props[:30], ensure_ascii=False)

    # ── 4. Ссылка на магазин продавца
    for sel in _DETAIL_SELLER_SELECTORS:
        el = soup.select_one(sel)
        if el and el.get("href"):
            href = el["href"]
            if href.startswith("/"):
                href = BASE_URL + href
            elif href.startswith("//"):
                href = "https:" + href
            out["seller_url"] = href
            break

    # ── 5. Количество в наличии
    for sel in _DETAIL_QTY_SELECTORS:
        el = soup.select_one(sel)
        if not el:
            continue
        # data-quantity
        dq = el.get("data-quantity")
        if dq:
            try:
                out["quantity_available"] = int(re.sub(r"[^\d]", "", dq) or 0)
                break
            except (TypeError, ValueError):
                pass
        text = el.get_text(" ", strip=True)
        m = re.search(r"(\d[\d\s]*)", text)
        if m:
            try:
                out["quantity_available"] = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
                break
            except (TypeError, ValueError):
                pass

    # ── 6. Дата публикации
    for sel in _DETAIL_DATE_SELECTORS:
        el = soup.select_one(sel)
        if not el:
            continue
        if el.name == "meta":
            dt = el.get("content", "")
        else:
            dt = el.get("datetime") or el.get_text(strip=True)
        if dt and len(dt) >= 4:
            out["published_at"] = dt[:50]
            break

    return out


def _parse_product_card(card, category: str = "") -> Optional[Product]:
    """Извлекает Product из карточки ggsel. Поддерживает старый и новый формат классов."""
    if not _BS4_OK or card is None:
        return None
    try:
        # Ищем фото карточки по стабильному data-testid (ggsel Next.js SSR).
        # Fallback на первый img — на случай если ggsel сменит разметку.
        img_el = (
            card.find("img", attrs={"data-testid": "card-image"})
            or card.find("img")
        )
        name = ""
        if img_el and img_el.get("alt"):
            name = img_el.get("alt").strip()

        if not name:
            name_el = card.find(class_=re.compile(r"(ProductCard(-module)?.*?|ProductPrice.*?|ProductHeader.*?)(name|title|description)"))
            if name_el:
                name = name_el.get_text(strip=True)

        if not name:
            name_el = card.find("a", href=True)
            if name_el and name_el.get_text(strip=True):
                name = name_el.get_text(strip=True)

        if not name:
            return None

        link_el = card.find("a", href=True)
        url = link_el["href"] if link_el else ""
        if url and not url.startswith("http"):
            url = BASE_URL + url

        price_el = card.find(class_=re.compile(r"(ProductCard(-module)?.*?|ProductPrice.*?)(price|amount)"))
        if not price_el:
            price_el = card.find(class_=re.compile(r"price"))
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        price, currency = _parse_price(price_text)

        seller_el = card.find(class_=re.compile(r"ProductCard(-module)?.*?seller"))
        seller = seller_el.get_text(strip=True) if seller_el else ""

        rating = None
        rating_el = card.find(class_=re.compile(r"ProductCard(-module)?.*?(rating|rate)"))
        if rating_el:
            m = re.search(r"(\d+[.,]\d+)", rating_el.get_text())
            if m:
                try:
                    rating = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass

        sales_count = None
        sales_el = card.find(class_=re.compile(r"(ProductCard(-module)?.*?|stat.*?)(sales|sold|counter)"))
        if not sales_el:
            sales_el = card.find(attrs={"data-testid": "card-counter"})
        if sales_el:
            sales_count = _parse_sales(sales_el.get_text())

        image_url = ""
        if img_el:
            # src присутствует в SSR-HTML Next.js; data-src — legacy/lazy-load fallback
            image_url = img_el.get("src") or img_el.get("data-src") or img_el.get("data-lazy-src") or ""
            if image_url and image_url.startswith("/"):
                image_url = BASE_URL + image_url

        pid = _extract_id_from_url(url)

        p = Product(
            name=name,
            url=url,
            price=price,
            external_id=pid,
            seller=seller,
            rating=rating,
            sales_count=sales_count,
            category=category,
            image_url=image_url[:500],
            in_stock=True,
        )
        return p
    except Exception as e:
        log.debug("[Parser] error parsing product card: %s", e)
        return None
class CascadeFetcher:
    """
    Каскадный fetcher: сначала пробует MsbFetcher (MSB + Qrator куки),
    при недоступности / ошибке — падает на CffiFetcher (curl-cffi).

    Используется в ParserEngine._get_fetcher() как единая точка входа.
    """
    name = "cascade"

    def __init__(self):
        self._cffi: Optional[CffiFetcher] = None
        self._msb = None   # MsbFetcher — lazy init
        self._msb_ok: Optional[bool] = None  # None = не проверяли

    # ── async (для ParserEngine._do_fetch) ──────────────────────────────────

    async def fetch(self, url: str) -> "FetchResult":
        """Async точка входа. Пробует MSB, fallback на cffi."""
        # 1. MsbFetcher
        msb = await self._get_msb()
        if msb is not None:
            try:
                result = await msb.fetch(url)
                if result.success:
                    return result
                # MSB не смог — логируем, идём в cffi
                log.warning("[Cascade] MsbFetcher failed (%s), fallback cffi", result.error)
            except Exception as e:
                log.warning("[Cascade] MsbFetcher exception: %s, fallback cffi", e)

        # 2. CffiFetcher (синхронный — в thread pool)
        cffi = self._get_cffi()
        if cffi.available:
            return await asyncio.to_thread(cffi.fetch, url)

        return FetchResult(False, url, error="no_fetcher_available", strategy_used="cascade")

    # ── Lazy init helpers ────────────────────────────────────────────────────

    async def _get_msb(self):
        """Lazy init MsbFetcher. Возвращает None если недоступен."""
        if self._msb_ok is False:
            return None  # уже знаем что нет
        if self._msb is not None:
            return self._msb

        try:
            from .msb_fetcher import MsbFetcher
            from .profile_pool import get_pool
            from .adaptive_rate_limiter import get_limiter
            from .telemetry import get_telemetry
            import httpx as _httpx

            http_client = _httpx.AsyncClient(timeout=60.0)
            from .captcha_handler import CaptchaHandler
            captcha = CaptchaHandler(
                http_client=http_client,
                base_url=os.getenv("MSB_API_BASE", "http://127.0.0.1:17248"),
                token=os.getenv("MSB_API_TOKEN", ""),
                enabled=True,
            )
            pool = await get_pool()
            self._msb = MsbFetcher(
                pool=pool,
                rate_limiter=get_limiter(),
                captcha_handler=captcha,
                telemetry=get_telemetry(),
                msb_api_base=os.getenv("MSB_API_BASE", "http://127.0.0.1:17248"),
            )
            self._msb_ok = True
            log.info("[Cascade] MsbFetcher инициализирован")
        except Exception as e:
            self._msb_ok = False
            log.warning("[Cascade] MsbFetcher недоступен: %s — используем только cffi", e)

        return self._msb

    def _get_cffi(self) -> CffiFetcher:
        if self._cffi is None:
            self._cffi = CffiFetcher()
        return self._cffi

    @property
    def available(self) -> bool:
        cffi = self._get_cffi()
        return cffi.available or self._msb_ok is not False

class GGselHTMLParser:
    """Парсер HTML страниц ggsel.net → ParseResult."""

    def parse(self, html: str, hint_url: str = "") -> ParseResult:
        result = ParseResult()
        if not html or not html.strip():
            result.errors.append("Пустой HTML")
            return result

        soup = _make_soup(html)
        if soup is None:
            result.errors.append("Не удалось разобрать HTML")
            return result

        page_url = _canonical_url(soup) or hint_url
        result.url = page_url
        page_type = _detect_page_type(soup, page_url)
        result.page_type = page_type

        try:
            if page_type == "category":
                self._parse_category(result, soup, page_url)
            elif page_type == "seller":
                self._parse_seller(result, soup, page_url)
            elif page_type == "product":
                self._parse_product(result, soup, page_url)
            else:
                # Универсальный парсинг — ищем все карточки на любой странице
                self._parse_category(result, soup, page_url)
            result.success = True
        except Exception as e:
            log.exception("[Parser] error: %s", e)
            result.errors.append(str(e))
        return result

    def _parse_category(self, result: ParseResult, soup, url: str) -> None:
        h1 = soup.find("h1")
        result.category_name = h1.get_text(strip=True) if h1 else ""

        # Ищем корневые карточки товаров.
        cards = soup.find_all(attrs={"data-testid": "card"})
        if not cards:
            cards = soup.find_all(class_=re.compile(r"ProductCard.*card\b"))
        if not cards:
            cards = soup.find_all(attrs={"data-product-id": True})

        for card in cards:
            p = _parse_product_card(card, result.category_name)
            if p:
                result.products.append(p)
        result.next_page_url = _find_next_page(soup, url)
        log.info("[Parser] category '%s': %d cards", result.category_name, len(result.products))
    def _parse_product(self, result: ParseResult, soup, url: str) -> None:
        # Берём JSON-LD Product
        jld = {}
        for s in soup.find_all("script", type="application/ld+json"):
            txt = s.string or ""
            if '"@type":"Product"' in txt and '"offers"' in txt:
                try:
                    data = json.loads(txt)
                    offers = data.get("offers", {})
                    agg = data.get("aggregateRating", {})
                    jld = {
                        "name": data.get("name", ""),
                        "image": data.get("image"),
                        "price": offers.get("price") if isinstance(offers, dict) else None,
                        "currency": offers.get("priceCurrency", "RUB") if isinstance(offers, dict) else "RUB",
                        "rating": agg.get("ratingValue") if isinstance(agg, dict) else None,
                    }
                except Exception:
                    pass
                break
        h1 = soup.find("h1")
        name = (h1.get_text(strip=True) if h1 else jld.get("name", ""))
        name = re.sub(r"^Купить\s*(\$\s*)?", "", name).strip()
        amount_el = soup.find(class_=re.compile(r"ProductBuyBlock-module.*?amount"))
        price, currency = _parse_price(amount_el.get_text(" ", strip=True)) if amount_el else (0.0, "")
        if not price and jld.get("price"):
            try:
                price = float(jld["price"])
            except (TypeError, ValueError):
                pass
        rating = None
        if jld.get("rating"):
            try:
                rating = float(jld["rating"])
            except (TypeError, ValueError):
                pass
        pid = _extract_id_from_url(url)
        if pid and name:
            p = Product(
                external_id=pid, name=name, price=price,
                currency=currency or jld.get("currency", "RUB"),
                url=url, rating=rating, in_stock=True,
            )
            result.products = [p]


# ═══════════════════════════════════════════════════════════════════════════
#  Scanner: orchestrator
# ═══════════════════════════════════════════════════════════════════════════
class ParserEngine:
    """
    Главный оркестратор парсера. Один экземпляр живёт в parser/__init__.py.

    Lifecycle:
      - created once at app startup
      - start(query, category, quantity, ...) → запускает парсинг в фоне
      - stop() → ставит stop flag, парсер корректно завершает страницу
      - status() → dict для GUI
    """

    def __init__(self):
        self._fetcher: Optional[CffiFetcher] = None  # fallback (sync, curl-cffi)
        self._msb_fetcher = None  # type: ignore  # основной (async, MSB + pool + rate + captcha)
        self._use_msb: bool = True  # переключатель
        self._msb_unavailable_reason: Optional[str] = None
        self._html_parser = GGselHTMLParser()
        self._is_running = False
        self._stop_event = _StopEvent()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Stats
        self._current_run_id: Optional[int] = None
        self._stats = {
            "status": "idle",
            "products_found": 0,
            "products_saved": 0,
            "products_ai_enriched": 0,
            "pages_scanned": 0,
            "errors_count": 0,
            "last_started_at": None,
            "last_finished_at": None,
            "last_query": None,
            "last_category": None,
            "fetcher_used": "",  # "msb" | "cffi" | "msb_unavailable"
        }

    # ── Публичное API ──────────────────────────────────────────────────────
    def status(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def is_running(self) -> bool:
        return self._is_running

    def start(self, query: str = "", category: str = "",
              quantity: int = 20, max_pages: int = DEFAULT_MAX_PAGES,
              run_ai_enrichment: bool = True) -> dict:
        """
        Запускает парсинг в фоновом потоке.

        Args:
            query:            поисковый запрос (название товара, категория или пусто)
            category:         slug категории (games, keys, ...). Если задан query — ищем по всему сайту.
            quantity:         макс. кол-во товаров (default 20, hard cap PARSER_MAX_QUANTITY)
            max_pages:        макс. страниц пагинации (default 3)
            run_ai_enrichment: True → прогон через Gemini для каждого нового товара

        Returns:
            dict {ok, run_id, message}
        """
        with self._lock:
            if self._is_running:
                return {"ok": False, "message": "Парсер уже запущен"}
            # SAFETY: hard cap
            if quantity > MAX_QUANTITY_HARD_CAP:
                quantity = MAX_QUANTITY_HARD_CAP
            if quantity < 1:
                quantity = 1
            if max_pages < 1:
                max_pages = 1
            if max_pages > 10:
                max_pages = 10

            self._stop_event.clear()
            self._is_running = True
            self._reset_stats()
            self._stats["last_query"] = query
            self._stats["last_category"] = category

            # Создаём run в БД
            from .db_init import get_db_path
            self._current_run_id = self._create_run(query, category, quantity, max_pages)

            self._thread = threading.Thread(
                target=self._run_safe,
                args=(query, category, quantity, max_pages, run_ai_enrichment),
                daemon=True,
                name="parser-engine",
            )
            self._thread.start()
            self._stats["status"] = "running"
            self._stats["last_started_at"] = datetime.utcnow().isoformat()
            return {
                "ok": True,
                "run_id": self._current_run_id,
                "message": f"Запущен парсинг (q={query!r}, cat={category!r}, qty={quantity})",
            }

    def stop(self) -> dict:
        """Ставит stop flag. Парсер остановится на текущей итерации."""
        with self._lock:
            if not self._is_running:
                return {"ok": False, "message": "Парсер не запущен"}
            self._stop_event.set()
            self._log_event("info", "Получен сигнал остановки")
            return {"ok": True, "message": "Stop signal sent"}

    # ── Внутренние методы ──────────────────────────────────────────────────
    def _reset_stats(self):
        self._stats.update({
            "status": "starting",
            "products_found": 0,
            "products_saved": 0,
            "products_ai_enriched": 0,
            "pages_scanned": 0,
            "errors_count": 0,
        })

    def _create_run(self, query, category, quantity, max_pages) -> int:
        from .db_init import get_db_path
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        try:
            cur = conn.execute(
                "INSERT INTO parser_runs (started_at, status, query, category, quantity, max_pages) "
                "VALUES (?, 'running', ?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), query, category, quantity, max_pages),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _finish_run(self, status: str, products_saved: int, products_ai: int, errors: str = ""):
        from .db_init import get_db_path
        conn = sqlite3.connect(get_db_path(), timeout=10.0)
        try:
            conn.execute(
                "UPDATE parser_runs SET finished_at=?, status=?, products_saved=?, "
                "products_ai_enriched=?, errors=? WHERE run_id=?",
                (datetime.utcnow().isoformat(), status, products_saved, products_ai, errors[:500],
                 self._current_run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _log_event(self, level: str, message: str):
        try:
            from .db_init import get_db_path
            conn = sqlite3.connect(get_db_path(), timeout=5.0)
            try:
                conn.execute(
                    "INSERT INTO parser_log (run_id, level, message) VALUES (?, ?, ?)",
                    (self._current_run_id, level, message[:1000]),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _run_safe(self, query, category, quantity, max_pages, run_ai_enrichment):
        """Entry point фонового потока. Любое исключение ловится.

        Запускает async _run_async() через asyncio.run() — это даёт нам event loop
        для MsbFetcher (async) без переделки всего на asyncio. Каждый запуск
        парсера = свой loop, чистое состояние.
        """
        import asyncio as _aio
        try:
            _aio.run(self._run_async(query, category, quantity, max_pages, run_ai_enrichment))
            self._stats["status"] = "done"
            self._finish_run("done", self._stats["products_saved"],
                             self._stats["products_ai_enriched"])
        except Exception as e:
            log.exception("[Parser] fatal error: %s", e)
            self._log_event("error", f"Fatal: {e}")
            self._stats["status"] = "error"
            self._stats["errors_count"] += 1
            try:
                self._finish_run("error", self._stats["products_saved"],
                                 self._stats["products_ai_enriched"], str(e))
            except Exception:
                pass
        finally:
            self._is_running = False
            self._stats["last_finished_at"] = datetime.utcnow().isoformat()
            # Закрываем оба фетчера если есть
            if self._fetcher:
                try:
                    self._fetcher.close()
                except Exception:
                    pass
                self._fetcher = None
            if self._msb_fetcher is not None:
                try:
                    _aio.run(self._msb_fetcher.close())
                except Exception:
                    pass
                self._msb_fetcher = None
            # Rate limiter — flush на диск
            try:
                from .adaptive_rate_limiter import get_limiter
                get_limiter().force_save()
            except Exception:
                pass
            # Telemetry — финальный emit
            try:
                from .telemetry import get_telemetry
                tel = get_telemetry()
                if self._stats.get("status") in ("done", "error"):
                    tel.emit("parser.run_complete",
                             total_products=self._stats["products_saved"],
                             total_pages=self._stats["pages_scanned"],
                             duration_sec=0,  # заполняется раньше в _run_async
                             status=self._stats["status"],
                             fetcher_used=self._stats.get("fetcher_used", ""))
                    tel.flush()
            except Exception:
                pass

    def _build_url(self, query: str, category: str) -> str:
        """
        Строит стартовый URL для скрапинга публичного сайта ggsel.net.
        category — slug из KNOWN_CATEGORIES (например 'igry-po-nazvaniyu')
                   или числовой ID (тогда используем /catalog/<id>).
        query    — текстовый поиск через /search.
        """
        import re
        if category:
            return f"{BASE_URL}/catalog/{category.strip()}"
        if query:
            # Если query выглядит как slug (только строчные буквы, цифры, дефисы) — это категория
            if re.match(r'^[a-z0-9][a-z0-9\-]*$', query):
                return f"{BASE_URL}/catalog/{query.strip()}"
            # Иначе — текстовый поиск
            import urllib.parse
            return f"{BASE_URL}/search?q={urllib.parse.quote(query)}"
        # fallback — самая крупная категория
        return f"{BASE_URL}/catalog/igry-po-nazvaniyu"

    async def _run_async(self, query, category, quantity, max_pages, run_ai_enrichment):
        """
        Async главный цикл парсинга. Скрапит публичный ggsel.net через CascadeFetcher
        (MoreLogin CDP → curl-cffi fallback), парсит HTML карточек конкурентов.
        """
        # Telemetry: emit start
        try:
            from .telemetry import get_telemetry
            tel = get_telemetry()
            tel.emit("parser.start", query=query, category=category, quantity=quantity)
        except Exception:
            pass

        self._log_event("info", f"Старт: q={query!r} cat={category!r} qty={quantity} pages={max_pages}")

        from .dedup import is_fresh, is_rejected, is_duplicate_name, invalidate_name_cache

        start_url = self._build_url(query, category)
        self._log_event("info", f"Start URL: {start_url}")

        fetcher = await self._get_fetcher()
        self._stats["fetcher_used"] = getattr(fetcher, "name", type(fetcher).__name__)

        page_url: str | None = start_url
        page = 0
        saved_total = 0
        ai_enriched_total = 0
        seen_ids: set = set()

        while page_url and page < max_pages and saved_total < quantity:
            if self._stop_event.is_set():
                self._log_event("info", "Остановка по сигналу")
                break

            page += 1
            self._stats["pages_scanned"] = page
            self._log_event("info", f"Страница {page}: GET {page_url}")

            # Fetch
            try:
                fetch_result = await self._do_fetch(fetcher, page_url)
            except Exception as e:
                self._stats["errors_count"] += 1
                self._log_event("error", f"Fetch exception стр.{page}: {e}")
                break

            if not fetch_result.success:
                self._stats["errors_count"] += 1
                self._log_event("error", f"Fetch fail стр.{page}: {fetch_result.error} (challenge={fetch_result.is_challenge})")
                if fetch_result.is_challenge:
                    self._log_event("warn", "Qrator challenge — пауза 30с")
                    import asyncio
                    await asyncio.sleep(30)
                break

            # Parse HTML
            parse_result = self._html_parser.parse(fetch_result.html, page_url)
            if not parse_result.success:
                self._stats["errors_count"] += 1
                self._log_event("error", f"Parse fail стр.{page}: {parse_result.errors}")
                break

            products = parse_result.products
            self._stats["products_found"] += len(products)
            self._log_event("info", f"Стр.{page}: найдено {len(products)} карточек")

            if not products:
                self._log_event("warn", f"Стр.{page}: 0 карточек — возможно WAF или пустая категория")
                break

            # Дедупликация + raw profit_score
            batch = []
            for p in products:
                if saved_total + len(batch) >= quantity:
                    break
                if not p.external_id or p.external_id in seen_ids:
                    continue
                if is_fresh(p.external_id):
                    continue
                if is_rejected(p.external_id):
                    continue
                if is_duplicate_name(p.name):
                    continue
                p.profit_score = _calc_raw_score(
                    sales_count=p.sales_count or 0,
                    seller_rating=(p.rating or 0.0),
                    reviews_count=0,
                    in_stock=bool(p.in_stock),
                )
                seen_ids.add(p.external_id)
                batch.append(p)

            if not batch:
                self._log_event("info", f"Стр.{page}: все дубли/skip — переходим к следующей странице")
            else:
                batch.sort(key=lambda x: x.profit_score, reverse=True)
                top_n = min(len(batch), quantity - saved_total)
                batch = batch[:top_n]
                self._log_event("info", f"Стр.{page}: {len(batch)} товаров идут в БД")

                saved = self._save_batch(batch, category)
                saved_total += len(saved)
                self._stats["products_saved"] = saved_total
                self._log_event("info", f"Стр.{page}: сохранено {len(saved)} (итого {saved_total})")
                invalidate_name_cache()

                if run_ai_enrichment and saved:
                    ai_ok = self._ai_enrich_batch(saved)
                    ai_enriched_total += ai_ok
                    self._stats["products_ai_enriched"] = ai_enriched_total

            if saved_total >= quantity:
                self._log_event("info", f"Лимит qty={quantity} достигнут")
                break

            # Следующая страница
            page_url = parse_result.next_page_url
            if page_url and not page_url.startswith("http"):
                page_url = BASE_URL + page_url

            if page_url:
                _smart_sleep()

        self._log_event("info", f"Готово: сохранено {saved_total}, AI {ai_enriched_total}, страниц {page}")

    async def _get_fetcher(self):
        """
        Возвращает fetcher для парсинга.

        По умолчанию (config.PARSER_USE_MSB=True) — CascadeFetcher
        (MoreLogin MSB → CffiFetcher fallback). Используется когда нужны
        живые куки для обхода Qrator.

        Если PARSER_USE_MSB=False — CffiFetcher напрямую, с ротацией прокси.
        Это быстрее и не зависит от MoreLogin.
        """
        if self._fetcher is not None:
            return self._fetcher

        from config import PARSER_USE_MSB as use_msb

        if use_msb:
            # MSB путь (как раньше) — MoreLogin → CffiFetcher fallback
            self._fetcher = CascadeFetcher()
            self._stats["fetcher_used"] = "cascade"
            self._log_event("info", "CascadeFetcher инициализирован (MoreLogin → cffi)")
        else:
            # Прямой путь — только CffiFetcher с ротацией прокси
            proxies = _parse_proxy_list()
            self._fetcher = CffiFetcher(proxies=proxies)
            self._stats["fetcher_used"] = "cffi"
            if proxies:
                self._log_event("info",
                                f"CffiFetcher инициализирован с {len(proxies)} прокси")
            else:
                self._log_event("info", "CffiFetcher инициализирован (без прокси)")
        return self._fetcher

    async def _do_fetch(self, fetcher, url: str):
        """
        Универсальный wrapper: MsbFetcher и CascadeFetcher уже async, CffiFetcher sync.
        Определяем тип по классу, а не по inspect — надёжнее.
        """
        if type(fetcher).__name__ == 'CascadeFetcher':
            return await fetcher.fetch(url)

        try:
            from .msb_fetcher import MsbFetcher as _MsbFetcher
            if type(fetcher).__name__ == 'MsbFetcher':
                return await fetcher.fetch(url)
        except ImportError:
            pass
        # Sync (CffiFetcher) — в thread pool
        return await asyncio.to_thread(fetcher.fetch, url)

    async def _fetch_product_detail(self, fetcher, url: str) -> dict:
        """
        Парсит детальную страницу товара: галерея, описание, характеристики, продавец.
        Возвращает dict (может быть пустой, если fetch упал или WAF).
        """
        try:
            fetch = await self._do_fetch(fetcher, url)
        except Exception as e:
            self._log_event("debug", f"_fetch_product_detail exception: {e}")
            return {}
        if not getattr(fetch, "success", False):
            return {}
        try:
            return _parse_product_detail(fetch.html, url) or {}
        except Exception as e:
            self._log_event("debug", f"_parse_product_detail exception: {e}")
            return {}

    def _save_batch(self, products: List[Product], category: str) -> List[dict]:
        """
        Сохраняет батч в БД. Возвращает список сохранённых dict-ов
        (нужен для последующего AI-enrichment).
        """
        from .db_init import get_db_path
        from .pricing import calculate_my_price

        # Получаем category_id из таблицы category_slugs по slug категории
        def _get_category_id(conn, slug: str) -> Optional[int]:
            if not slug:
                return None
            try:
                row = conn.execute(
                    "SELECT id FROM category_slugs WHERE slug = ? AND id IS NOT NULL LIMIT 1",
                    (slug,)
                ).fetchone()
                return row[0] if row else None
            except Exception:
                return None

        if not products:
            return []

        saved_dicts: List[dict] = []
        conn = sqlite3.connect(get_db_path(), timeout=15.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            now = datetime.utcnow().isoformat()
            for p in products:
                my_price = calculate_my_price(p.price, category or p.category or "default")
                # Если есть images_json и original_desc из детальной страницы — обновляем
                # image_url на первый URL из галереи (если раньше был пустой или дубликат)
                extra = p.extra or {}
                detail_image = ""
                if extra.get("images_json"):
                    try:
                        imgs = json.loads(extra["images_json"])
                        if imgs:
                            detail_image = imgs[0]
                    except Exception:
                        pass
                # итоговое фото: детальное имеет приоритет
                final_image = detail_image or p.image_url or ""
                cat_id = _get_category_id(conn, category or p.category)
                try:
                    conn.execute("""
                        INSERT INTO parsed_products
                            (product_id, title, original_title, original_desc, price, my_price,
                             category, url, seller_name, rating, sales_count, image_url,
                             in_stock, source_price, profit_score, status,
                             images_json, properties_json, quantity_available,
                             seller_url, published_at, last_parsed_at, created_at, updated_at,
                             approval_status, category_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'pending',
                                ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                        ON CONFLICT(product_id) DO UPDATE SET
                            price              = excluded.price,
                            my_price           = excluded.my_price,
                            source_price       = excluded.source_price,
                            profit_score       = excluded.profit_score,
                            seller_name        = excluded.seller_name,
                            rating             = excluded.rating,
                            sales_count        = excluded.sales_count,
                            image_url          = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE parsed_products.image_url END,
                            original_desc      = CASE WHEN excluded.original_desc IS NOT NULL THEN excluded.original_desc ELSE parsed_products.original_desc END,
                            images_json        = COALESCE(excluded.images_json, parsed_products.images_json),
                            properties_json    = COALESCE(excluded.properties_json, parsed_products.properties_json),
                            quantity_available = COALESCE(excluded.quantity_available, parsed_products.quantity_available),
                            seller_url         = COALESCE(excluded.seller_url, parsed_products.seller_url),
                            published_at       = COALESCE(excluded.published_at, parsed_products.published_at),
                            status             = CASE WHEN parsed_products.status = 'new' THEN 'pending' ELSE parsed_products.status END,
                            approval_status    = COALESCE(parsed_products.approval_status, 'pending'),
                            last_parsed_at     = excluded.last_parsed_at,
                            updated_at         = excluded.updated_at,
                            category_id        = excluded.category_id
                    """, (
                        p.external_id, p.name[:300], p.name[:300], extra.get("original_desc"),
                        p.price, my_price, category or p.category, p.url, p.seller,
                        p.rating, p.sales_count or 0, final_image,
                        p.price, p.profit_score,
                        extra.get("images_json"),
                        extra.get("properties_json"),
                        extra.get("quantity_available"),
                        extra.get("seller_url"),
                        extra.get("published_at"),
                        now, now, now, cat_id
                    ))
                    saved_dicts.append({
                        "product_id":     p.external_id,
                        "title":          p.name,
                        "price":          p.price,
                        "category":       category or p.category or "default",
                        "image_url":      final_image,
                        "sales_count":    p.sales_count or 0,
                        "seller_rating":  p.rating or 0.0,
                        "reviews_count":  0,
                    })

                    # Локальное кэширование фото: скачиваем сразу во время парсинга
                    # (тем же curl-cffi + Chrome TLS impersonation, что и HTML), чтобы админке
                    # не нужно было лезть на CDN повторно при каждом просмотре страницы.
                    # Скачиваем только если локальной копии ещё нет (идемпотентно, не бьём CDN
                    # повторно при каждом репарсинге). Отключается через PARSER_DOWNLOAD_IMAGES=false.
                    if final_image and _DOWNLOAD_IMAGES_ENABLED:
                        try:
                            existing_local = conn.execute(
                                "SELECT local_image_path FROM parsed_products WHERE product_id = ?",
                                (p.external_id,)
                            ).fetchone()
                        except Exception:
                            existing_local = None
                        if not existing_local or not existing_local[0]:
                            local_path = _save_product_image_locally(
                                final_image, p.external_id, referer=p.url or BASE_URL
                            )
                            if local_path:
                                conn.execute(
                                    "UPDATE parsed_products SET local_image_path = ? WHERE product_id = ?",
                                    (local_path, p.external_id)
                                )
                except Exception as e:
                    log.error("DB save error for %s: %s", p.external_id, e)
                    self._log_event("error", f"DB save {p.external_id}: {e}")
            conn.commit()
        finally:
            conn.close()
        return saved_dicts

    def _ai_enrich_batch(self, products: List[dict]) -> int:
        """Прогоняет каждый товар через Gemini. Возвращает кол-во успешных."""
        from .content_gen import enrich_product
        from .db_init import get_db_path
        ok = 0
        for p in products:
            if self._stop_event.is_set():
                break
            try:
                enriched = enrich_product(p)
                # Fallback на формулу если AI не дал profit_score/my_price
                if enriched.get("profit_score") is None:
                    ps = _calc_raw_score(
                        sales_count   = enriched.get("sales_count") or 0,
                        seller_rating = enriched.get("seller_rating") or 0.0,
                        reviews_count = enriched.get("reviews_count") or 0,
                        in_stock      = True,
                    )
                    enriched["profit_score"] = round(ps, 1)
                if enriched.get("my_price") is None and enriched.get("price"):
                    enriched["my_price"] = _calc_my_price_fallback(float(enriched["price"]))
                conn = sqlite3.connect(get_db_path(), timeout=10.0)
                try:
                    conn.execute("""
                        UPDATE parsed_products SET
                            generated_title        = ?,
                            generated_desc         = ?,
                            tags                   = ?,
                            generated_image_url    = ?,
                            profit_score           = ?,
                            my_price               = ?,
                            recommended_margin_pct = ?,
                            risk_level             = ?,
                            risk_reason            = ?,
                            status                 = ?,
                            last_enriched_at       = ?,
                            updated_at             = ?,
                            ai_error               = ?
                        WHERE product_id = ?
                    """, (
                        enriched.get("generated_title", ""),
                        enriched.get("generated_desc", ""),
                        enriched.get("generated_tags", ""),
                        enriched.get("generated_image_url", ""),
                        enriched.get("profit_score"),
                        enriched.get("my_price"),
                        enriched.get("recommended_margin_pct"),
                        enriched.get("risk_level", ""),
                        enriched.get("risk_reason", ""),
                        enriched.get("status", "ai_enriched"),
                        datetime.utcnow().isoformat(),
                        datetime.utcnow().isoformat(),
                        enriched.get("ai_error", ""),
                        p["product_id"],
                    ))
                    conn.commit()
                finally:
                    conn.close()
                if enriched.get("status") == "ai_enriched":
                    ok += 1
                self._log_event("info",
                                f"AI: {p['product_id']} → {enriched.get('status')}"
                                f" (profit={enriched.get('profit_score')}, "
                                f"my_price={enriched.get('my_price')}, "
                                f"risk={enriched.get('risk_level')})")
            except Exception as e:
                self._log_event("error", f"AI error for {p['product_id']}: {e}")
        return ok


# Синглтон создаётся лениво при первом обращении
_engine_singleton: Optional[ParserEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> ParserEngine:
    global _engine_singleton
    if _engine_singleton is None:
        with _engine_lock:
            if _engine_singleton is None:
                _engine_singleton = ParserEngine()
    return _engine_singleton


