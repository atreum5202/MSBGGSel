"""
profile_pool.py — пул профилей с умной ротацией, прокси-менеджментом и health-tracking.

Бэкенд: MSB (MyStealthBrowser). Прямые REST-вызовы не делаем — ходим через
MsbClient (parser/msb_client.py, импортирован под алиасом MsbClient
для обратной совместимости).

Логика:
  - PoolProfile хранит hit_count, error_count, consecutive_errors, last_success_at,
    кеш куков, статус отдыха, proxy_host (хост прокси для группировки)
  - ProxyHealth отслеживает состояние каждого прокси: сколько профилей, сколько failed,
    бан на 30 мин если failure_rate >= 70%
  - ProfilePool.init() — загружает все профили через MsbClient, восстанавливает proxy_host
  - get_cookies() — умный выбор: не отдыхает → прокси не забанен → consecutive_errors<3 →
    свежие куки → min(hit_count), tie → min(error_count)
  - report_error() — увеличивает consecutive_errors, помечает прокси failed,
    банит прокси если 70% профилей failed
  - report_success() — сбрасывает consecutive_errors, обновляет ProxyHealth
  - assign_proxies(proxies) — round-robin через update_profile (PATCH), обновляет ProxyHealth
  - Глобальный синглтон get_pool() — один экземпляр на весь процесс
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from dotenv import load_dotenv

from .msb_client import MsbClient  # noqa: E402  # активный бэкенд (MSB)

# Загружаем .env из GGselV7/.env (2 уровня вверх). Если файла нет — молча.
try:
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
except Exception:
    pass

logger = logging.getLogger("profile_pool")

# ── Отдельный логгер для статистики пула (хиты/ошибки/тайминги).
prof_logger = logging.getLogger("profile_pool.stats")
prof_logger.setLevel(logging.INFO)
if not prof_logger.handlers:
    try:
        _PROF_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
        _PROF_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _prof_fh = logging.FileHandler(_PROF_LOG_DIR / "profiles.log", encoding="utf-8")
        _prof_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        prof_logger.addHandler(_prof_fh)
    except Exception:
        pass

# ─── Конфиг (MSB — активный бэкенд) ─────────────────────────────────────────
MSB_API_BASE  = os.getenv("MSB_API_BASE",   "http://127.0.0.1:17248")
MSB_API_TOKEN = os.getenv("MSB_API_TOKEN",  "")
MSB_GROUP_NAME = os.getenv("MSB_GROUP_NAME", "GGSeller")

# Разрешённые ID профилей (пусто = все профили группы MSB_GROUP_NAME)
ALLOWED_PROFILE_IDS = {
    item.strip()
    for item in os.getenv("MSB_PROFILE_IDS", "").split(",")
    if item.strip()
}

MAX_HITS_PER_PROFILE: int = int(os.getenv("POOL_MAX_HITS", "40"))
POOL_REST_SEC:        int = int(os.getenv("POOL_REST_SEC", "180"))

COOKIE_TTL_SECONDS: int = int(os.getenv("MSB_COOKIE_TTL", os.getenv("MORELOGIN_COOKIE_TTL", "900")))
SCENARIO_NAME = "ggsel-login"

# ── Proxy health ────────────────────────────────────────────────────────────
PROXY_FAIL_TRIGGER: int = 3          # consecutive_errors >= N → профиль "failed"
PROXY_BAN_THRESHOLD: float = 0.7     # 70% failed на одном прокси → бан
PROXY_BAN_DURATION: int = 1800       # 30 мин бан


# ─── Структура профиля ────────────────────────────────────────────────────────

@dataclass
class PoolProfile:
    profile_id: str
    name: str = ""
    hit_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    last_success_at: float = 0.0
    cookies: Optional[Dict[str, str]] = None
    cookies_fetched_at: float = 0.0
    resting_until: float = 0.0
    proxy: Optional[dict] = None
    proxy_host: str = ""

    @property
    def is_resting(self) -> bool:
        return time.time() < self.resting_until

    @property
    def rest_remaining(self) -> int:
        return max(0, int(self.resting_until - time.time()))

    @property
    def has_fresh_cookies(self) -> bool:
        if not self.cookies:
            return False
        return (time.time() - self.cookies_fetched_at) < COOKIE_TTL_SECONDS

    @property
    def cookies_age(self) -> int:
        if not self.cookies_fetched_at:
            return -1
        return int(time.time() - self.cookies_fetched_at)

    def start_rest(self, seconds: int = POOL_REST_SEC) -> None:
        self.resting_until = time.time() + seconds
        logger.info("[Pool] Profile %s resting for %d sec", self.profile_id, seconds)

    def reset_hits(self) -> None:
        self.hit_count = 0
        self.resting_until = 0.0

    def clear_cookies(self) -> None:
        self.cookies = None
        self.cookies_fetched_at = 0.0

    def to_dict(self) -> dict:
        return {
            "profile_id":         self.profile_id,
            "name":               self.name,
            "hit_count":          self.hit_count,
            "error_count":        self.error_count,
            "consecutive_errors": self.consecutive_errors,
            "last_success_at":    self.last_success_at,
            "has_cookies":        bool(self.cookies),
            "cookies_age":        self.cookies_age,
            "is_resting":         self.is_resting,
            "rest_remaining":     self.rest_remaining,
            "resting_until":      self.resting_until,
            "proxy_host":         self.proxy_host,
        }


# ─── Здоровье прокси ──────────────────────────────────────────────────────────

@dataclass
class ProxyHealth:
    host: str
    total_profiles: int = 0
    failed_profiles: int = 0
    banned_until: float = 0.0

    @property
    def is_banned(self) -> bool:
        return time.time() < self.banned_until

    @property
    def failure_rate(self) -> float:
        if self.total_profiles == 0:
            return 0.0
        return self.failed_profiles / self.total_profiles

    def to_dict(self) -> dict:
        return {
            "host":              self.host,
            "total":             self.total_profiles,
            "failed":            self.failed_profiles,
            "failure_rate":      round(self.failure_rate, 3),
            "banned":            self.is_banned,
            "ban_remaining_sec": max(0, int(self.banned_until - time.time())) if self.banned_until else 0,
        }


# ─── Утилита: нормализация прокси ─────────────────────────────────────────────

def _normalize_proxy(p: Union[str, dict]) -> Optional[dict]:
    if not p:
        return None
    if isinstance(p, dict):
        out = {
            "protocol": (p.get("protocol") or "http").lower(),
            "host":     p.get("host") or "",
            "port":     int(p.get("port") or 0),
        }
        if p.get("username"):
            out["username"] = str(p["username"])
        if p.get("password"):
            out["password"] = str(p["password"])
        if not out["host"] or out["port"] <= 0:
            return None
        return out
    if isinstance(p, str):
        s = p if "://" in p else f"http://{p}"
        try:
            u = urlparse(s)
        except Exception:
            return None
        protocol = (u.scheme or "http").lower()
        host = u.hostname or ""
        port = u.port or 0
        if not host or port <= 0:
            return None
        out = {"protocol": protocol, "host": host, "port": int(port)}
        if u.username:
            out["username"] = u.username
        if u.password:
            out["password"] = u.password
        return out
    return None


# ─── Пул ─────────────────────────────────────────────────────────────────────

class ProfilePool:
    def __init__(self) -> None:
        self._profiles: Dict[str, PoolProfile] = {}
        self._proxy_health: Dict[str, ProxyHealth] = {}
        self._lock = asyncio.Lock()
        self._ml: Optional[MsbClient] = None
        self._initialized = False
        self._started_by_pool: set = set()

    def _build_client(self) -> MsbClient:
        return MsbClient(
            base_url=MSB_API_BASE,
            token=MSB_API_TOKEN,
            timeout=30.0,
        )

    async def _get_client(self) -> MsbClient:
        if self._ml is None:
            self._ml = self._build_client()
        return self._ml

    async def init(self) -> int:
        async with self._lock:
            ml = await self._get_client()
            try:
                profiles_list = await ml.get_profiles(group_name=MSB_GROUP_NAME)
                if not isinstance(profiles_list, list):
                    profiles_list = []
            except Exception as e:
                logger.warning("[Pool] Failed to load profiles from MSB: %s", e)
                return 0

            existing_ids = set(self._profiles.keys())
            new_ids: set = set()

            for p in profiles_list:
                pid = p.get("envId") or p.get("id", "")
                if not pid:
                    continue
                pid = str(pid)
                # Фильтр по MORELOGIN_PROFILE_IDS (если задан)
                if ALLOWED_PROFILE_IDS and pid not in ALLOWED_PROFILE_IDS:
                    continue
                new_ids.add(pid)
                api_proxy = p.get("proxy") or None
                api_proxy_host = (
                    (api_proxy or {}).get("host", "")
                    or (api_proxy or {}).get("proxyIp", "")
                    or (api_proxy or {}).get("proxyName", "")
                )
                if pid not in self._profiles:
                    self._profiles[pid] = PoolProfile(
                        profile_id=pid,
                        name=p.get("name", pid),
                        proxy=api_proxy,
                        proxy_host=api_proxy_host,
                    )
                else:
                    self._profiles[pid].name = p.get("name", pid)
                    if api_proxy:
                        self._profiles[pid].proxy = api_proxy
                        self._profiles[pid].proxy_host = api_proxy_host

            for gone in existing_ids - new_ids:
                del self._profiles[gone]
                logger.info("[Pool] Profile %s removed (not in MSB GGSeller group)", gone)

            self._rebuild_proxy_health()
            self._initialized = True

            if ALLOWED_PROFILE_IDS:
                missing = sorted(ALLOWED_PROFILE_IDS - new_ids)
                if missing:
                    logger.warning("[Pool] Allowed profile ids not found in MSB: %s", missing)
                logger.info("[Pool] Allowed profile ids active: %s", sorted(new_ids))

            logger.info("[Pool] Initialized: %d profiles, %d proxies tracked",
                        len(self._profiles), len(self._proxy_health))
            return len(self._profiles)

    def _rebuild_proxy_health(self) -> None:
        host_total: Dict[str, int] = {}
        host_failed: Dict[str, int] = {}
        for p in self._profiles.values():
            if not p.proxy_host:
                continue
            host_total[p.proxy_host] = host_total.get(p.proxy_host, 0) + 1
            if p.consecutive_errors >= PROXY_FAIL_TRIGGER:
                host_failed[p.proxy_host] = host_failed.get(p.proxy_host, 0) + 1
        seen = set()
        for host, total in host_total.items():
            seen.add(host)
            if host not in self._proxy_health:
                self._proxy_health[host] = ProxyHealth(host=host)
            ph = self._proxy_health[host]
            ph.total_profiles = total
            ph.failed_profiles = host_failed.get(host, 0)
        for gone in list(self._proxy_health.keys()):
            if gone not in seen:
                del self._proxy_health[gone]

    def _recalc_proxy_for_host(self, host: str) -> None:
        if not host or host not in self._proxy_health:
            return
        ph = self._proxy_health[host]
        ph.failed_profiles = sum(
            1 for p in self._profiles.values()
            if p.proxy_host == host and p.consecutive_errors >= PROXY_FAIL_TRIGGER
        )

    def _check_and_ban_proxy(self, host: str) -> None:
        if not host or host not in self._proxy_health:
            return
        ph = self._proxy_health[host]
        if ph.total_profiles == 0:
            return
        if ph.failure_rate >= PROXY_BAN_THRESHOLD and not ph.is_banned:
            ph.banned_until = time.time() + PROXY_BAN_DURATION
            logger.error(
                "[Pool] Proxy %s BANNED for %ds (failure rate %d/%d = %.0f%% >= %d%%)",
                host, PROXY_BAN_DURATION, ph.failed_profiles, ph.total_profiles,
                ph.failure_rate * 100, int(PROXY_BAN_THRESHOLD * 100),
            )

    async def _get_running_profile_ids(self) -> set:
        try:
            ml = await self._get_client()
            ids = await ml.get_running_profiles()
            if isinstance(ids, list):
                return set(ids)
        except Exception as e:
            logger.debug("[Pool] get_running_profiles unavailable (not blocking): %s", e)
        return set()

    async def get_cookies(self) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        all_running = await self._get_running_profile_ids()
        busy_ids = all_running - self._started_by_pool
        if busy_ids:
            logger.debug("[Pool] Profiles busy in UI: %s", busy_ids)

        async with self._lock:
            if not self._profiles:
                logger.warning("[Pool] Pool is empty — profiles not loaded")
                return None, None

            candidates = []
            for p in self._profiles.values():
                if p.is_resting:
                    continue
                if p.profile_id in busy_ids:
                    continue
                if p.proxy_host:
                    ph = self._proxy_health.get(p.proxy_host)
                    if ph and ph.is_banned:
                        continue
                if p.consecutive_errors >= PROXY_FAIL_TRIGGER:
                    continue
                candidates.append(p)

            if not candidates:
                total = len(self._profiles)
                resting = sum(1 for p in self._profiles.values() if p.is_resting)
                busy = sum(1 for p in self._profiles.values() if p.profile_id in busy_ids)
                banned_proxies = sum(1 for ph in self._proxy_health.values() if ph.is_banned)
                failed_profiles = sum(1 for p in self._profiles.values() if p.consecutive_errors >= PROXY_FAIL_TRIGGER)
                logger.warning(
                    "[Pool] No available profiles (total=%d, resting=%d, busy_in_ui=%d, "
                    "banned_proxies=%d, failed_profiles=%d)",
                    total, resting, busy, banned_proxies, failed_profiles,
                )
                return None, None

            with_fresh = [p for p in candidates if p.has_fresh_cookies]
            if with_fresh:
                profile = min(with_fresh, key=lambda p: (p.hit_count, p.error_count))
                profile.hit_count += 1
                if profile.hit_count >= MAX_HITS_PER_PROFILE:
                    profile.start_rest()
                logger.debug("[Pool] Profile %s (hit=%d) returns cached cookies (proxy=%s)",
                             profile.profile_id, profile.hit_count, profile.proxy_host or "none")
                return dict(profile.cookies), profile.profile_id  # type: ignore

            profile = min(candidates, key=lambda p: (p.hit_count, p.error_count))
            chosen_id = profile.profile_id

        cookies = await self._fetch_cookies(chosen_id)

        async with self._lock:
            p = self._profiles.get(chosen_id)
            if p is None:
                return None, None
            if cookies:
                p.cookies = cookies
                p.cookies_fetched_at = time.time()
                p.hit_count += 1
                if p.hit_count >= MAX_HITS_PER_PROFILE:
                    p.start_rest()
                return dict(cookies), p.profile_id
            else:
                p.error_count += 1
                p.consecutive_errors += 1
                p.start_rest()
                if p.proxy_host and p.consecutive_errors >= PROXY_FAIL_TRIGGER:
                    self._recalc_proxy_for_host(p.proxy_host)
                    self._check_and_ban_proxy(p.proxy_host)
                return None, None

    async def _fetch_cookies(self, profile_id: str) -> Optional[Dict[str, str]]:
        import time as _time
        t0 = _time.time()
        p = self._profiles.get(profile_id)
        p_name = p.name if p else "Unknown"

        status_before = "unknown"
        try:
            ml = await self._get_client()
            status_before = await ml.get_profile_status(profile_id)
        except Exception:
            pass

        if p and not p.proxy_host:
            logger.warning("[Pool] Profile %s has NO proxy assigned — will use system IP", profile_id)

        try:
            res = await self._fetch_cookies_original(profile_id)
            ms = int((_time.time() - t0) * 1000)
            status_result = "success" if res else "error"
            prof_logger.info(
                f"[{datetime.now().isoformat()}] Profile: {profile_id} ({p_name}) | "
                f"Status: {status_before} | Result: {status_result} | {ms}ms"
            )
            return res
        except Exception as e:
            ms = int((_time.time() - t0) * 1000)
            status_result = f"error: {type(e).__name__}"
            prof_logger.error(
                f"[{datetime.now().isoformat()}] Profile: {profile_id} ({p_name}) | "
                f"Status: {status_before} | Result: {status_result} | {ms}ms"
            )
            raise

    async def _fetch_cookies_original(self, profile_id: str) -> Optional[Dict[str, str]]:
        ml = await self._get_client()
        did_start = False
        debug_port: Optional[str] = None

        try:
            status = await ml.get_profile_status(profile_id)
            is_running = (status == "running")

            if not is_running:
                logger.info("[Pool] Starting profile %s...", profile_id)
                start_result = await ml.start_profile(profile_id)
                did_start = True
                self._started_by_pool.add(profile_id)
                debug_port = str(start_result.get("debugPort", "")) if isinstance(start_result, dict) else ""
                await asyncio.sleep(4)
            else:
                self._started_by_pool.add(profile_id)
                detail = await ml.get_profile(profile_id)
                if isinstance(detail, dict):
                    debug_port = str(detail.get("debugPort", "") or "")
                if not debug_port:
                    logger.info("[Pool] Profile %s already running but no debugPort — restarting", profile_id)
                    try:
                        await ml.stop_profile(profile_id)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                    start_result = await ml.start_profile(profile_id)
                    did_start = True
                    debug_port = str(start_result.get("debugPort", "")) if isinstance(start_result, dict) else ""
                    await asyncio.sleep(4)

            if debug_port:
                try:
                    await self._cdp_navigate_to_ggsel(debug_port)
                    await asyncio.sleep(3)
                except Exception as cdp_err:
                    logger.warning("[Pool] CDP navigation failed for %s: %s", profile_id, cdp_err)
            else:
                logger.warning("[Pool] No debugPort for %s — skipping CDP navigation", profile_id)
                await asyncio.sleep(3)

            cookies: Optional[Dict[str, str]] = None
            for attempt in range(6):
                raw = await ml.get_cookies(profile_id, domain="ggsel.net")
                if raw and self._has_qrator_cookies(raw):
                    cookies = raw
                    logger.info("[Pool] Profile %s got Qrator cookies on attempt %d", profile_id, attempt + 1)
                    break
                logger.debug("[Pool] Profile %s: no Qrator cookies yet (%d/6)", profile_id, attempt + 1)
                await asyncio.sleep(5)

            if not cookies:
                cookies = await ml.get_cookies(profile_id, domain="ggsel.net")
                if cookies:
                    logger.warning("[Pool] Profile %s: fallback non-Qrator cookies", profile_id)

            if cookies:
                await self.report_success(profile_id)
            return cookies or None

        except Exception as e:
            logger.warning("[Pool] Error getting cookies for %s: %s", profile_id, e)
            return None

        finally:
            if did_start:
                try:
                    await ml.stop_profile(profile_id)
                    self._started_by_pool.discard(profile_id)
                    logger.info("[Pool] Profile %s stopped after cookie fetch", profile_id)
                except Exception as stop_err:
                    logger.debug("[Pool] stop_profile(%s) error (ok): %s", profile_id, stop_err)

    @staticmethod
    def _has_qrator_cookies(cookies: Dict[str, str]) -> bool:
        QRATOR_KEYS = {
            "qrator_msid", "qrator_msid2",
            "qrator_jsid", "qrator_jsr", "qrator_ssid", "qrator_clientid",
        }
        return bool(QRATOR_KEYS & set(k.lower() for k in cookies.keys()))

    async def _cdp_navigate_to_ggsel(self, debug_port: str) -> None:
        import json as _json
        import httpx as _httpx

        cdp_base = f"http://127.0.0.1:{debug_port}"

        async with _httpx.AsyncClient(timeout=10.0) as http:
            try:
                resp = await http.get(f"{cdp_base}/json")
                tabs = resp.json() if resp.status_code == 200 else []
            except Exception:
                tabs = []

            ws_url: Optional[str] = None
            if tabs and isinstance(tabs, list):
                for tab in tabs:
                    if isinstance(tab, dict) and tab.get("type") == "page":
                        ws_url = tab.get("webSocketDebuggerUrl", "")
                        break

            if not ws_url:
                try:
                    r = await http.get(f"{cdp_base}/json/new")
                    tab_info = r.json() if r.status_code == 200 else {}
                    ws_url = tab_info.get("webSocketDebuggerUrl", "")
                except Exception:
                    pass

            if not ws_url:
                raise RuntimeError(f"No CDP tab on port {debug_port}")

            try:
                import websockets as _ws
                async with _ws.connect(ws_url, open_timeout=5) as ws:
                    cmd = _json.dumps({
                        "id": 1,
                        "method": "Page.navigate",
                        "params": {"url": "https://ggsel.net/catalog/games"}
                    })
                    await ws.send(cmd)
                    await asyncio.wait_for(ws.recv(), timeout=10)
                    logger.info("[Pool] CDP navigated to ggsel.net on port %s", debug_port)
            except ImportError:
                await http.get(
                    f"{cdp_base}/json/new",
                    params={"url": "https://ggsel.net/catalog/games"},
                    timeout=5.0,
                )
                logger.info("[Pool] CDP HTTP fallback: opened ggsel.net on port %s", debug_port)

    async def report_error(self, profile_id: str) -> None:
        async with self._lock:
            p = self._profiles.get(profile_id)
            if p is None:
                return
            p.error_count += 1
            p.consecutive_errors += 1
            p.clear_cookies()
            p.start_rest()
            logger.info(
                "[Pool] Profile %s — error %d (consecutive=%d), cookies cleared, resting %ds (proxy=%s)",
                profile_id, p.error_count, p.consecutive_errors, POOL_REST_SEC, p.proxy_host or "none",
            )
            if p.proxy_host and p.consecutive_errors >= PROXY_FAIL_TRIGGER:
                self._recalc_proxy_for_host(p.proxy_host)
                self._check_and_ban_proxy(p.proxy_host)

    async def report_success(self, profile_id: str) -> None:
        async with self._lock:
            p = self._profiles.get(profile_id)
            if p is None:
                return
            p.consecutive_errors = 0
            p.last_success_at = time.time()
            if p.proxy_host:
                self._recalc_proxy_for_host(p.proxy_host)
            logger.debug("[Pool] Profile %s — success at %s (proxy=%s)",
                         profile_id, datetime.fromtimestamp(p.last_success_at).isoformat(),
                         p.proxy_host or "none")

    async def assign_proxies(self, proxies: List[Union[str, dict]]) -> dict:
        normalized: List[dict] = []
        for p in proxies:
            n = _normalize_proxy(p)
            if n:
                normalized.append(n)
        if not normalized:
            logger.warning("[Pool] No valid proxies to assign")
            return {"assigned": 0, "failed": 0, "skipped": 0, "distribution": {}}

        async with self._lock:
            if not self._profiles:
                logger.error("[Pool] No profiles to assign proxies to")
                return {"assigned": 0, "failed": 0, "skipped": 0, "distribution": {}}

            ml = await self._get_client()
            distribution: Dict[str, int] = {}
            assigned = 0
            failed = 0
            skipped = 0
            profiles = list(self._profiles.values())

            for i, profile in enumerate(profiles):
                proxy = normalized[i % len(normalized)]
                proxy_host = proxy.get("host", "")

                if profile.proxy and profile.proxy.get("host") == proxy_host:
                    distribution[proxy_host] = distribution.get(proxy_host, 0) + 1
                    assigned += 1
                    skipped += 1
                    continue

                try:
                    result = await ml.update_profile(profile.profile_id, proxy=proxy)
                    if result:
                        profile.proxy = proxy
                        profile.proxy_host = proxy_host
                        distribution[proxy_host] = distribution.get(proxy_host, 0) + 1
                        assigned += 1
                    else:
                        logger.warning("[Pool] update_profile failed for %s (empty response)", profile.profile_id)
                        failed += 1
                except Exception as e:
                    logger.warning("[Pool] update_profile error for %s: %s", profile.profile_id, e)
                    failed += 1

            self._rebuild_proxy_health()
            logger.info(
                "[Pool] Proxies assigned: %d OK (%d new, %d skipped), %d failed. Distribution: %s",
                assigned, assigned - skipped, skipped, failed, distribution,
            )
            return {"assigned": assigned, "failed": failed, "skipped": skipped, "distribution": distribution}

    async def reset_errors(self) -> None:
        async with self._lock:
            for p in self._profiles.values():
                p.error_count = 0
                p.hit_count = 0
                p.consecutive_errors = 0
                p.resting_until = 0.0
            for ph in self._proxy_health.values():
                ph.failed_profiles = 0
                ph.banned_until = 0.0
            logger.info("[Pool] Counters reset for %d profiles", len(self._profiles))

    async def status(self) -> dict:
        async with self._lock:
            profiles_list = [p.to_dict() for p in self._profiles.values()]
            active = sum(1 for p in self._profiles.values() if not p.is_resting)
            return {
                "initialized":  self._initialized,
                "total":        len(self._profiles),
                "active":       active,
                "resting":      len(self._profiles) - active,
                "max_hits":     MAX_HITS_PER_PROFILE,
                "rest_sec":     POOL_REST_SEC,
                "profiles":     profiles_list,
                "proxy_health": {
                    host: ph.to_dict() for host, ph in self._proxy_health.items()
                },
            }

    async def reload(self) -> int:
        return await self.init()

    async def close(self) -> None:
        if self._ml is not None:
            try:
                await self._ml.close()
            except Exception:
                pass
            self._ml = None


# ─── Синглтон ─────────────────────────────────────────────────────────────────

_pool_instance: Optional[ProfilePool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> ProfilePool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = ProfilePool()
        await _pool_instance.init()
    return _pool_instance


def get_pool_sync() -> Optional[ProfilePool]:
    return _pool_instance
