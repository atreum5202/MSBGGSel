"""
parser/msb_fetcher.py
=====================
Главный fetcher для парсера ggsel.net. Использует:
  - ProfilePool (ротация профилей, anti-hijack)
  - MsbClient (получение Qrator-куков через MSB; импортирован под алиасом MsbClient)
  - curl-cffi (TLS-fingerprint Chrome)
  - AdaptiveRateLimiter (per-profile пауза по ответу)
  - CaptchaHandler (auto-solve через MSB)
  - Telemetry (локальная статистика)

Pre-check перед КАЖДЫМ запросом:
  1. Есть профиль с hit_count < MAX_HITS и не на отдыхе? (ProfilePool)
  2. Есть валидные cookies (whitelist Qrator-ключей)? (validate_qrator_cookies)
  3. Профиль не открыт в MSB UI? (ProfilePool → get_running_profiles)
  4. HEAD-проверка? — опционально, опускаем чтобы не тратить Qrator-бюджет

Если что-то не так → _refresh_cookies() (start + scenario + cookies + STOP).

Lazy init: pool инициализируется при первом fetch(). Это позволяет
MsbFetcher быть созданным без event loop, и если MSB недоступен —
первый fetch() вернёт понятную ошибку.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Опциональные зависимости ─────────────────────────────────────────────
try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except ImportError:
    _CFFI_OK = False

from .profile_pool import ProfilePool, get_pool  # noqa: E402
from .msb_client import MsbClient  # noqa: E402
from .msb_cookies import (  # noqa: E402
    validate_qrator_cookies,
    QRATOR_COOKIE_KEYS,
)
from .adaptive_rate_limiter import AdaptiveRateLimiter  # noqa: E402
from .captcha_handler import CaptchaHandler  # noqa: E402
from .telemetry import Telemetry  # noqa: E402

logger = logging.getLogger("ggselv7.msb_fetcher")


# ── WAF-сигнатуры (копия из parser_engine.py, для детекта challenge) ─────
QRATOR_SIGNATURES = [
    "qrator", "just a moment", "ddos-guard", "please wait",
    "checking your browser", "enable javascript",
    "__cf_chl", "challenge-form",
]


def _is_challenge_page(html: str, status_code: Optional[int] = None) -> bool:
    if not html:
        return False
    lower = html.lower()
    for sig in QRATOR_SIGNATURES:
        if sig in lower:
            return True
    return False


# ── UA pool (как в parser_engine.CffiFetcher) ─────────────────────────────
_UA_POOL = [
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "impersonate": "chrome120"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "impersonate": "chrome120"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
     "impersonate": "chrome120"},
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


# ── Result dataclass ──────────────────────────────────────────────────────
@dataclass
class FetchResult:
    """Результат одного fetch. Совместим с parser_engine.FetchResult по полям."""
    success: bool
    status_code: int
    html: str = ""
    profile_id: Optional[str] = None
    latency_ms: int = 0
    error: str = ""
    is_challenge: bool = False
    captcha_detected: bool = False
    strategy: str = "msb"  # "msb" | "snapshot" | "msb_fallback"
    cookies_source: str = ""  # "snapshot" | "api" | "msb_refresh" | ""
    rate_delay: float = 0.0
    used_profile_id: Optional[str] = None  # для обратной совместимости с parser_engine

    def __post_init__(self):
        # alias
        if self.used_profile_id is None and self.profile_id:
            self.used_profile_id = self.profile_id


# ── Главный класс ────────────────────────────────────────────────────────
class MsbFetcher:
    """
    curl-cffi + куки из MSB ProfilePool + adaptive rate + captcha + telemetry.

    Имя класса оставлено `MsbFetcher` для обратной совместимости (используется
    в parser_engine.py / routes.py), внутри бэкенд — MSB (MsbClient под алиасом MsbClient).

    Использование:
        fetcher = MsbFetcher(
            pool=await get_pool(),
            msb_client=MsbClient(),
            rate_limiter=AdaptiveRateLimiter(...),
            captcha_handler=CaptchaHandler(...),
            telemetry=Telemetry(...),
        )
        result = await fetcher.fetch("https://ggsel.net/catalog/games")
        # result: FetchResult(success=True, status_code=200, html="...", ...)
    """

    def __init__(
        self,
        pool: Optional[ProfilePool] = None,
        msb_client: Optional[MsbClient] = None,
        rate_limiter: Optional[AdaptiveRateLimiter] = None,
        captcha_handler: Optional[CaptchaHandler] = None,
        telemetry: Optional[Telemetry] = None,
        msb_api_base: str = "http://127.0.0.1:17248",
        msb_api_token: str = "",
        base_url: str = "https://ggsel.net",
        timeout: float = 30.0,
        max_captcha_retries: int = 1,
    ):
        # Имена параметров `msb_client` / `msb_api_base` / `msb_api_token` сохранены
        # для обратной совместимости с parser_engine.py и routes.py.
        # Семантика: `msb_api_base` теперь указывает на MSB (порт 17248),
        # `msb_api_token` — Bearer-токен MSB-аутентификации.
        self._pool = pool
        self._ml_client = msb_client
        self._rate_limiter = rate_limiter
        self._captcha = captcha_handler
        self._telemetry = telemetry
        self._ml_api_base = msb_api_base.rstrip("/")
        self._ml_api_key = msb_api_token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_captcha_retries = max_captcha_retries

        self._pool_initialized = pool is not None
        self._ml_client_initialized = msb_client is not None
        self._init_lock = asyncio.Lock()

        # curl-cffi session — не thread-safe, локаем
        self._session = None
        self._session_lock = threading.Lock()
        self._session_ua_index = -1

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def _ensure_pool(self) -> ProfilePool:
        """Lazy init: если pool не передан, создаём через get_pool()."""
        if self._pool is None:
            async with self._init_lock:
                if self._pool is None:
                    self._pool = await get_pool()
                    self._pool_initialized = True
        return self._pool

    async def _ensure_ml_client(self) -> MsbClient:
        """Lazy init: если client не передан, создаём MsbClient (под алиасом MsbClient)."""
        if self._ml_client is None:
            async with self._init_lock:
                if self._ml_client is None:
                    self._ml_client = MsbClient(
                        base_url=self._ml_api_base,
                        token=self._ml_api_key,
                    )
                    self._ml_client_initialized = True
        return self._ml_client

    def _ensure_session(self):
        """Lazy init curl-cffi Session. Под локом потому что не thread-safe."""
        if self._session is not None:
            return
        with self._session_lock:
            if self._session is not None:
                return
            if not _CFFI_OK:
                raise RuntimeError("curl-cffi не установлен: pip install curl-cffi")
            entry = _UA_POOL[0]
            self._session = cffi_requests.Session(impersonate=entry["impersonate"])
            self._session.headers.update(_random_browser_headers())
            logger.info("MsbFetcher: cffi session OK (impersonate=%s)", entry["impersonate"])

    async def close(self) -> None:
        """Cleanup. Идемпотентно."""
        with self._session_lock:
            if self._session:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
        if self._ml_client_initialized and self._ml_client is not None:
            try:
                await self._ml_client.close()
            except Exception:
                pass
            self._ml_client = None

    # ── Главный метод: fetch ──────────────────────────────────────────────

    async def fetch(self, url: str, **kwargs: Any) -> FetchResult:
        """
        Делает GET-запрос к ggsel.net с Qrator-куками.
        Полный flow: pre-check → rate wait → request → is_challenge → captcha.
        """
        t_total = time.monotonic()
        profile_id: Optional[str] = None

        # 1. Pre-check 1+2+3: получить валидные cookies
        try:
            cookies, profile_id, source = await self._get_fresh_cookies()
        except Exception as e:
            return FetchResult(
                success=False, status_code=0, error=f"precheck_failed: {e}",
                strategy="msb_precheck",
            )

        if not cookies or not profile_id:
            self._emit("parser.error", error_type="no_cookies", detail="пул вернул None")
            return FetchResult(
                success=False, status_code=0, error="no_cookies",
                strategy="msb_precheck",
                profile_id=profile_id,
            )

        # 2. Rate limit wait (per profile_id)
        rate_delay = 0.0
        if self._rate_limiter:
            rate_delay = self._rate_limiter.wait(profile_id)

        # 3. HTTP request
        t_req = time.monotonic()
        try:
            resp_text, status = await asyncio.to_thread(self._do_request_sync, url, cookies)
        except Exception as e:
            return FetchResult(
                success=False, status_code=0, error=f"http_error: {str(e)[:200]}",
                profile_id=profile_id, latency_ms=int((time.monotonic() - t_req) * 1000),
                strategy="msb", cookies_source=source, rate_delay=rate_delay,
            )
        latency_ms = int((time.monotonic() - t_req) * 1000)

        # 4. Rate limit record
        if self._rate_limiter:
            self._rate_limiter.record(profile_id, status)

        # 5. Telemetry
        is_challenge = _is_challenge_page(resp_text, status)
        self._emit("parser.page_fetched",
                   profile_id=profile_id, status=status, latency_ms=latency_ms,
                   rate_delay=rate_delay, is_challenge=is_challenge,
                   url=url[:200] if url else "")

        # 6. Success / fail базовое
        if status == 200 and not is_challenge:
            # Доп. проверка на captcha-страницу
            if self._captcha and self._captcha.is_captcha_page(resp_text):
                # Captcha — пробуем решить
                captcha_result = await self._handle_captcha(url, profile_id, cookies, resp_text)
                if captcha_result.success:
                    return captcha_result
                # Иначе возвращаем что есть, captcha_detected=True
                return FetchResult(
                    success=False, status_code=status, html=resp_text,
                    profile_id=profile_id, latency_ms=latency_ms,
                    is_challenge=True, captcha_detected=True,
                    error="captcha_unsolved", strategy="msb",
                    cookies_source=source, rate_delay=rate_delay,
                )
            return FetchResult(
                success=True, status_code=status, html=resp_text,
                profile_id=profile_id, latency_ms=latency_ms,
                strategy="msb", cookies_source=source, rate_delay=rate_delay,
            )

        # 7. Не-успех: 401/403/429/etc
        error_msg = f"http_{status}"
        if status in (401, 403):
            # Профиль на отдых, потом следующий
            try:
                pool = await self._ensure_pool()
                await pool.report_error(profile_id)
                self._emit("parser.profile_rested", profile_id=profile_id, reason="401", duration_sec=300)
            except Exception:
                pass
            error_msg = f"auth_failed_{status}"
        elif status == 429:
            # Rate limit — помечаем профиль через rate limiter (он сам уже)
            self._emit("parser.rate_limited", profile_id=profile_id, status=status)
            error_msg = "rate_limited_429"
        elif is_challenge:
            error_msg = "qrator_challenge"

        return FetchResult(
            success=False, status_code=status, html=resp_text,
            profile_id=profile_id, latency_ms=latency_ms,
            is_challenge=is_challenge, error=error_msg,
            strategy="msb", cookies_source=source, rate_delay=rate_delay,
        )

    # ── Cookie management ─────────────────────────────────────────────────

    async def _get_fresh_cookies(self) -> Tuple[Optional[Dict[str, str]], Optional[str], str]:
        """
        Pre-check + возврат (cookies, profile_id, source).
        source ∈ {"pool_cache", "snapshot", "api", "msb_refresh", ""}
        """
        # Pre-check 1+3: ProfilePool.get_cookies() уже фильтрует по:
        #   - hit_count < MAX (если hit_count == MAX, профиль на отдыхе)
        #   - is_resting (время отдыха ещё не вышло)
        #   - not in /profiles/running (юзер в UI)
        try:
            pool = await self._ensure_pool()
        except Exception as e:
            logger.warning("MsbFetcher: pool init failed: %s", e)
            raise RuntimeError(f"MSB недоступен: {e}") from e

        cookies, profile_id = await pool.get_cookies()

        if not cookies or not profile_id:
            # Пул не дал — пробуем refresh через прямой вызов MSB
            logger.info("MsbFetcher: pool вернул None — пробуем _refresh_cookies")
            new_cookies = await self._refresh_cookies(profile_id or "")
            if new_cookies:
                return new_cookies, profile_id, "msb_refresh"
            return None, None, ""

        # Pre-check 2: куки валидны (whitelist Qrator-ключей)?
        if not validate_qrator_cookies(cookies):
            logger.info("MsbFetcher: cookies не валидны (нет Qrator-ключей) — refresh")
            new_cookies = await self._refresh_cookies(profile_id)
            if new_cookies:
                return new_cookies, profile_id, "msb_refresh"
            # Куки есть, но невалидные — возвращаем как есть (fetch попробует)
            return cookies, profile_id, "pool_cache"

        return cookies, profile_id, "pool_cache"

    async def _refresh_cookies(self, profile_id: str) -> Optional[Dict[str, str]]:
        """
        Принудительное обновление cookies через MSB:
          start profile → runScenario → get cookies → STOP profile.

        Используется когда:
          - начальный pre-check не нашёл валидных cookies
          - после 401/captcha нужно переполучить куки
        """
        if not profile_id:
            return None

        client = await self._ensure_ml_client()
        t0 = time.monotonic()

        try:
            # 1. Start browser
            start_result = await client.start_profile(profile_id)
            if not start_result:
                logger.warning("MsbFetcher: start_profile(%s) failed (empty response)", profile_id)
                return None

            # 2. Run scenario
            try:
                scenario_result = await client.start_scenario(
                    profile_id,
                    "ggsel-login",
                    params={"timeoutMs": 45000},
                )
            except Exception as e:
                logger.warning("MsbFetcher: runScenario failed for %s: %s", profile_id, e)
                scenario_result = {}

            # Куки могут прийти в теле сценария
            cookies = self._extract_cookies(scenario_result)
            if not cookies:
                # Иначе — отдельный GET /cookies с фильтром по домену
                cookies = await client.get_cookies(profile_id, domain="ggsel.net")

            took_ms = int((time.monotonic() - t0) * 1000)
            self._emit("parser.cookies_refreshed",
                       profile_id=profile_id, source="msb", took_ms=took_ms,
                       cookies_count=len(cookies) if cookies else 0)

            return cookies if cookies else None

        except Exception as e:
            logger.warning("MsbFetcher: _refresh_cookies error: %s", e)
            return None
        finally:
            # ВСЕГДА закрываем профиль после refresh (даже при ошибках)
            try:
                await client.stop_profile(profile_id)
            except Exception as e:
                logger.debug("MsbFetcher: stop_profile(%s) exception (ok): %s", profile_id, e)

    @staticmethod
    def _extract_cookies(scenario_result: Any) -> Optional[Dict[str, str]]:
        if not isinstance(scenario_result, dict):
            return None
        raw = scenario_result.get("cookies")
        if not raw:
            inner = scenario_result.get("data")
            if isinstance(inner, dict):
                raw = inner.get("cookies")
        if isinstance(raw, list):
            out = {c["name"]: c["value"] for c in raw if c.get("name") and c.get("value")}
            return out if out else None
        if isinstance(raw, dict):
            return {k: v for k, v in raw.items() if v} or None
        return None

    # ── Captcha handling ──────────────────────────────────────────────────

    async def _handle_captcha(
        self, url: str, profile_id: str, original_cookies: Dict[str, str], html: str
    ) -> FetchResult:
        """
        Вызывается когда HTML содержит captcha-маркеры.
        Пробует решить через MSB (solveCaptcha / retry), потом retry запроса.
        """
        if not self._captcha:
            return FetchResult(success=False, status_code=200, html=html,
                               profile_id=profile_id, error="captcha_no_handler",
                               is_challenge=True, captcha_detected=True)

        for attempt in range(self._max_captcha_retries):
            new_cookies = await self._captcha.solve_via_msb(profile_id)
            if not new_cookies:
                break

            # Retry с новыми куками
            t_req = time.monotonic()
            try:
                resp_text, status = await asyncio.to_thread(
                    self._do_request_sync, url, new_cookies
                )
            except Exception as e:
                logger.warning("MsbFetcher: captcha retry http error: %s", e)
                break
            latency_ms = int((time.monotonic() - t_req) * 1000)
            is_challenge = _is_challenge_page(resp_text, status)
            is_still_captcha = self._captcha.is_captcha_page(resp_text)

            self._emit("parser.captcha_solved",
                       profile_id=profile_id, attempt=attempt + 1,
                       status=status, latency_ms=latency_ms)

            if status == 200 and not is_challenge and not is_still_captcha:
                # Успех после captcha
                if self._rate_limiter:
                    self._rate_limiter.record(profile_id, 200)
                # NOTE: раньше здесь писали куки в in-memory кеш MsbCookieClient.
                # В MSB-версии in-memory кеша на уровне fetcher нет —
                # ProfilePool владеет своим кешем; captcha-куки остаются в FetchResult.
                return FetchResult(
                    success=True, status_code=200, html=resp_text,
                    profile_id=profile_id, latency_ms=latency_ms,
                    captcha_detected=True, strategy="msb_captcha_solved",
                    cookies_source="msb_captcha",
                )
            # Иначе — следующая попытка
        return FetchResult(success=False, status_code=200, html=html,
                           profile_id=profile_id, error="captcha_unsolved",
                           captcha_detected=True, is_challenge=True)

    # ── Sync HTTP (curl-cffi) ─────────────────────────────────────────────

    def _do_request_sync(self, url: str, cookies: Dict[str, str]) -> Tuple[str, int]:
        """Sync HTTP GET. Запускается в thread pool (curl-cffi не async)."""
        self._ensure_session()
        with self._session_lock:
            # Обновляем UA на каждом запросе (лёгкая ротация)
            self._session_ua_index = (self._session_ua_index + 1) % len(_UA_POOL)
            self._session.headers.update(_random_browser_headers())
            resp = self._session.get(
                url, cookies=cookies, timeout=self._timeout, allow_redirects=True
            )
            return resp.text, resp.status_code

    # ── Telemetry helper ──────────────────────────────────────────────────

    def _emit(self, event: str, **data: Any) -> None:
        if self._telemetry:
            try:
                self._telemetry.emit(event, **data)
            except Exception as e:
                logger.debug("MsbFetcher: telemetry emit failed: %s", e)

    # ── Диагностика ───────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "msb_fetcher": {
                "msb_api_base": self._ml_api_base,
                "msb_api_key_set": bool(self._ml_api_key),
                "base_url": self._base_url,
                "pool_initialized": self._pool_initialized,
                "ml_client_initialized": self._ml_client_initialized,
                "cffi_available": _CFFI_OK,
                "session_created": self._session is not None,
                "has_rate_limiter": self._rate_limiter is not None,
                "has_captcha_handler": self._captcha is not None,
                "has_telemetry": self._telemetry is not None,
                "qrator_cookie_keys": sorted(QRATOR_COOKIE_KEYS),
            }
        }
