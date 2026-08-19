"""
msb_client.py — REST-клиент к MyStealthBrowser (MSB) Local API.

Базовый URL: http://127.0.0.1:17248  (порт из MSB_API_BASE в .env, по умолчанию 17248)

ЭНДПОИНТЫ MSB:
  GET  /health                        — healthcheck
  GET  /profiles                      — список всех профилей
  GET  /profiles/running              — список запущенных (envId)
  POST /profiles                      — создать профиль
  GET  /profiles/:id                  — детали профиля
  PATCH /profiles/:id                 — обновить профиль
  DELETE /profiles/:id                — удалить профиль
  POST /profiles/bulk-delete          — массовое удаление

  POST /profiles/:id/start            — запустить браузер профиля
  POST /profiles/:id/stop             — остановить браузер профиля
  GET  /profiles/:id/status           — статус браузера профиля
  POST /profiles/:id/goto             — навигация в браузере
  POST /profiles/:id/runScenario      — запустить сценарий

  GET  /profiles/:id/cookies          — получить куки профиля
  POST /profiles/:id/cookies          — импортировать куки
  DELETE /profiles/:id/cookies        — очистить куки

  POST /profiles/:id/check-proxy      — проверить прокси профиля
  POST /profiles/:id/switchProxy      — сменить прокси на лету
  POST /profiles/:id/refreshFingerprint — обновить fingerprint

  GET  /browser/status                — статус всех запущенных браузеров
  GET  /stats                         — статистика

АВТОРИЗАЦИЯ:
  Опциональный Bearer-токен (MSB_API_TOKEN в .env).
  Если не задан — авторизация не нужна.

ОТВЕТ (прокси-слой Fastify):
  Успех: {"ok": true, "data": ...}
  Ошибка: {"ok": false, "error": "..."}

ОТЛИЧИЯ от MSB:
  - Порт: 17248 
  - ID профиля: UUID-строка (не числовой envId)
  - Эндпоинты: REST-стиль /profiles/:id (не POST /api/env/...)
  - Куки: GET /profiles/:id/cookies?format=json -> {format, data: [...], count}
  - Статус: GET /profiles/:id/status -> {running: bool, ...}
  - Прокси: объект {protocol, host, port, username?, password?}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Загружаем .env
try:
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
except Exception:
    pass

logger = logging.getLogger("msb_client")

# ──────────────────────────────────────────────────────────────────────────────
# Конфиг
# ──────────────────────────────────────────────────────────────────────────────

MSB_API_BASE: str = os.getenv("MSB_API_BASE", "http://127.0.0.1:17248").rstrip("/")
MSB_API_TOKEN: str = os.getenv("MSB_API_TOKEN", "")
MSB_GROUP_NAME: str = os.getenv("MSB_GROUP_NAME", "GGSeller")
MSB_OPEN_TIMEOUT: int = int(os.getenv("MSB_OPEN_TIMEOUT", "30"))
MSB_CDP_TIMEOUT: int = int(os.getenv("MSB_CDP_TIMEOUT", "10"))

DEFAULT_TIMEOUT: float = float(os.getenv("MSB_TIMEOUT", "60"))
HEALTHCHECK_TIMEOUT: float = 3.0


# ──────────────────────────────────────────────────────────────────────────────
# Клиент
# ──────────────────────────────────────────────────────────────────────────────

class MsbClient:
    """
    Асинхронный клиент к MyStealthBrowser Local REST API.

    Интерфейс совместим с MsbClient — можно использовать
    как drop-in замену в profile_pool.py и msb_fetcher.py.

    Публичные методы:
      get_profiles()                 — список всех профилей
      get_profile(profile_id)        — детали одного профиля
      start_profile(profile_id)      — запустить браузер
      stop_profile(profile_id)       — остановить браузер
      get_profile_status(profile_id) — "running" | "stopped" | "unknown"
      get_running_profiles()         — список ID запущенных профилей
      get_cookies(profile_id, domain)— куки профиля (dict name->value)
      update_profile(profile_id, **) — обновить поля профиля
      check_proxy(profile_id)        — проверить прокси (ip, latency, country)
      switch_proxy(profile_id, proxy)— сменить прокси на лету
      refresh_fingerprint(profile_id)— обновить fingerprint
      run_scenario(profile_id, ...)  — запустить сценарий в браузере
      goto(profile_id, url)          — навигация
      is_available()                 — healthcheck
    """

    def __init__(
        self,
        base_url: str = MSB_API_BASE,
        token: str = MSB_API_TOKEN,
        timeout: float = DEFAULT_TIMEOUT,
        open_timeout: int = MSB_OPEN_TIMEOUT,
        cdp_timeout: int = MSB_CDP_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.open_timeout = open_timeout
        self.cdp_timeout = cdp_timeout
        self.api_id: str = ""
        self.api_key: str = ""
        self._http: Optional[httpx.AsyncClient] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "MsbClient":
        await self._ensure_http()
        return self

    async def __aexit__(self, *_):
        await self.close()

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._build_headers(),
                timeout=self.timeout,
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    # ── Внутренние хелперы ────────────────────────────────────────────────────

    @staticmethod
    def _unwrap(response: Any) -> Any:
        """MSB оборачивает в {"ok": true, "data": ...} — возвращаем data."""
        if isinstance(response, dict):
            if "data" in response:
                return response["data"]
            # Если это сам объект без обёртки (например прямой ответ профиля)
        return response

    @staticmethod
    def _is_ok(response: Any) -> bool:
        if isinstance(response, dict):
            ok = response.get("ok")
            if ok is False:
                err = response.get("error", "unknown error")
                logger.warning("msb API error: %s", err)
                return False
        return True

    async def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Универсальный запрос. Возвращает unwrapped data или None при ошибке."""
        http = await self._ensure_http()
        t0 = time.monotonic()

        try:
            resp = await http.request(
                method,
                path,
                json=body,
                params=params,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.RequestError as e:
            logger.warning("msb: %s %s — network error: %s", method, path, e)
            raise

        took_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code >= 400:
            logger.warning("msb: %s %s -> %d (%dms): %s",
                           method, path, resp.status_code, took_ms, resp.text[:300])
            resp.raise_for_status()

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return resp.text

        if not self._is_ok(data):
            return None

        return self._unwrap(data)

    async def _get(self, path: str, params: Optional[dict] = None,
                   timeout: Optional[float] = None) -> Any:
        return await self._request("GET", path, params=params, timeout=timeout)

    async def _post(self, path: str, body: Optional[dict] = None,
                    timeout: Optional[float] = None) -> Any:
        return await self._request("POST", path, body=body or {}, timeout=timeout)

    async def _patch(self, path: str, body: Optional[dict] = None) -> Any:
        return await self._request("PATCH", path, body=body or {})

    async def _delete(self, path: str, body: Optional[dict] = None) -> Any:
        return await self._request("DELETE", path, body=body)

    # ── Healthcheck ───────────────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """Проверяем доступность MSB через GET /health."""
        try:
            http = await self._ensure_http()
            resp = await http.get("/health", timeout=HEALTHCHECK_TIMEOUT)
            if resp.status_code == 200:
                raw = resp.json()
                # MSB отвечает {ok: true, data: {status: "ok", ...}}
                inner = raw.get("data", raw) if isinstance(raw, dict) else {}
                return inner.get("status") == "ok"
        except Exception:
            pass
        return False

    async def health(self) -> dict:
        """Подробный health: ok, base_url, latency_ms, version."""
        import time as _time
        t0 = _time.monotonic()
        ok = await self.is_available()
        latency = int((_time.monotonic() - t0) * 1000)
        return {
            "ok": ok,
            "base_url": self.base_url,
            "token_set": bool(self.token),
            "latency_ms": latency,
        }

    # ── Профили ───────────────────────────────────────────────────────────────

    async def get_profiles(
        self,
        group_name: Optional[str] = None,
        group_id: Optional[int] = None,
        env_ids: Optional[List[str]] = None,
    ) -> List[dict]:
        """
        GET /profiles[?group=...] — список профилей.

        group_name — фильтр по группе (MSB: ?group=GGSeller)
        group_id   — игнорируется (не поддерживается MSB API)
        env_ids    — список конкретных ID (если задан — загружает по одному)
        """
        if env_ids:
            out: List[dict] = []
            for eid in env_ids:
                p = await self.get_profile(eid)
                if p:
                    out.append(p)
            return out

        params = {}
        if group_name:
            params["group"] = group_name

        data = await self._get("/profiles", params=params if params else None)
        if data is None:
            return []
        profiles = data if isinstance(data, list) else []

        # envId — алиас id (нужен в некоторых местах кода)
        for p in profiles:
            if isinstance(p, dict) and "id" in p and "envId" not in p:
                p["envId"] = p["id"]

        logger.info("msb: get_profiles(group=%r) -> %d профилей", group_name, len(profiles))
        return profiles

    async def get_profile(self, profile_id: str) -> Optional[dict]:
        """GET /profiles/:id — полные детали профиля."""
        if not profile_id:
            return None
        try:
            data = await self._get(f"/profiles/{profile_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("msb: профиль %s не найден", profile_id)
                return None
            raise
        return data if isinstance(data, dict) else None

    async def create_profile(self, **fields: Any) -> Optional[dict]:
        """
        POST /profiles — создать новый профиль.

        Поля (все опциональные):
          name        — имя профиля
          notes       — заметки
          engine      — "auto" | "cloakbrowser" | "patchright"
          proxy       — {protocol, host, port, username?, password?}
          startUrl    — стартовый URL
          humanize    — bool (человекоподобные задержки)
          account     — {email, password, tags}
        """
        try:
            data = await self._post("/profiles", body=fields or {})
        except httpx.HTTPStatusError as e:
            logger.warning("msb: create_profile — %s", e)
            return None
        return data if isinstance(data, dict) else None

    async def update_profile(self, profile_id: str, **fields: Any) -> dict:
        """
        PATCH /profiles/:id — обновить поля профиля.

        Обновляемые поля: name, notes, proxy, engine, startUrl,
        fingerprint, humanize, aggressiveFingerprint, account, sortOrder, flagged.
        """
        if not profile_id:
            raise ValueError("profile_id обязателен")
        if not fields:
            raise ValueError("нужно передать хотя бы одно поле для обновления")

        try:
            data = await self._patch(f"/profiles/{profile_id}", body=fields)
        except httpx.HTTPStatusError as e:
            logger.warning("msb: update_profile(%s) — %s", profile_id, e)
            return {}
        return data if isinstance(data, dict) else {}

    async def delete_profile(self, profile_id: str) -> bool:
        """DELETE /profiles/:id — удалить профиль (останавливает браузер если запущен)."""
        if not profile_id:
            return False
        try:
            data = await self._delete(f"/profiles/{profile_id}")
            return bool(data and data.get("deleted"))
        except httpx.HTTPStatusError:
            return False

    async def bulk_delete_profiles(self, profile_ids: List[str]) -> dict:
        """POST /profiles/bulk-delete — массовое удаление. Возвращает {deleted, errors}."""
        data = await self._post("/profiles/bulk-delete", body={"ids": profile_ids})
        return data if isinstance(data, dict) else {"deleted": 0, "errors": len(profile_ids)}

    # ── Браузер ───────────────────────────────────────────────────────────────

    async def start_profile(self, profile_id: str, **opts: Any) -> dict:
        """
        POST /profiles/:id/start — запустить браузер профиля.

        Поддерживает новый параметр MSB — launchMode:
          "visible" | "minimized" | "background" | "headless"

        Для совместимости со старым кодом продолжает принимать булев "headless":
          headless=True  -> launchMode="headless" (если launchMode явно не задан)
          headless=False -> только isHeadless=False, без влияния на launchMode

        Для Gemini/Google-сценариев рекомендуется явно передавать
        launchMode="background" вместо headless=True — он сохраняет обычный
        headed rendering pipeline (что снижает риск детекта) и делает best-effort
        подавление окна (start-minimized + off-screen + CDP minimize).

        Возвращает {id, running, debugPort?, wsEndpoint?, pid?, launchMode?,
                   headlessApplied?, backgroundApplied?, focusSuppressed?, ...}
        debugPort нужен для CDP/Puppeteer автоматизации.
        """
        if not profile_id:
            raise ValueError("profile_id обязателен")
        try:
            body = dict(opts or {})
            if "launchMode" not in body:
                if body.get("headless") or body.get("isHeadless"):
                    body["launchMode"] = "headless"
                else:
                    body["launchMode"] = "visible"
            if "headless" in body and "isHeadless" not in body:
                body["isHeadless"] = body["headless"]
            data = await self._post(f"/profiles/{profile_id}/start", body=body)
        except httpx.HTTPStatusError as e:
            logger.warning("msb: start_profile(%s) — %s", profile_id, e)
            return {}

        result = data if isinstance(data, dict) else {}
        debug_port = result.get("debugPort")
        launch_mode = result.get("launchMode")
        if debug_port:
            logger.info(
                "msb: профиль %s запущен, debugPort=%s, launchMode=%s, backgroundApplied=%s, focusSuppressed=%s",
                profile_id, debug_port, launch_mode,
                result.get("backgroundApplied"), result.get("focusSuppressed"),
            )
        else:
            logger.info("msb: профиль %s запущен", profile_id)
        return result

    async def stop_profile(self, profile_id: str) -> dict:
        """POST /profiles/:id/stop — остановить браузер профиля."""
        if not profile_id:
            raise ValueError("profile_id обязателен")
        try:
            data = await self._post(f"/profiles/{profile_id}/stop")
        except httpx.HTTPStatusError as e:
            logger.debug("msb: stop_profile(%s) — %s (ok)", profile_id, e)
            return {}
        logger.info("msb: профиль %s остановлен", profile_id)
        return data if isinstance(data, dict) else {}

    async def get_profile_status(self, profile_id: str) -> str:
        """
        GET /profiles/:id/status — статус браузера профиля.
        Возвращает "running" | "stopped" | "unknown".
        """
        if not profile_id:
            return "unknown"
        try:
            data = await self._get(f"/profiles/{profile_id}/status")
        except httpx.HTTPStatusError:
            return "unknown"

        if not isinstance(data, dict):
            return "unknown"

        # MSB возвращает {running: bool, id, ...}
        if data.get("running") is True:
            return "running"
        if data.get("running") is False:
            return "stopped"
        return "unknown"

    async def get_running_profiles(self) -> List[str]:
        """
        GET /profiles/running — список ID запущенных профилей.
        Возвращает список строк (UUID профилей).
        """
        try:
            data = await self._get("/profiles/running")
        except Exception:
            return []

        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, dict):
            ids = data.get("ids") or data.get("data") or []
            return [str(x) for x in ids] if isinstance(ids, list) else []
        return []

    async def browser_status_all(self) -> List[dict]:
        """GET /browser/status — статус всех запущенных браузеров."""
        data = await self._get("/browser/status")
        return data if isinstance(data, list) else []

    async def get_groups(self) -> List[dict]:
        """
        GET /groups — список групп профилей MSB.
        Возвращает [{name, count, profileIds, color}]
        """
        try:
            data = await self._get("/groups")
        except Exception as e:
            logger.warning("msb: get_groups() — %s", e)
            return []
        return data if isinstance(data, list) else []

    async def goto(self, profile_id: str, url: str) -> dict:
        """POST /profiles/:id/goto — навигация в браузере профиля."""
        data = await self._post(f"/profiles/{profile_id}/goto", body={"url": url})
        return data if isinstance(data, dict) else {}

    async def run_scenario(
        self,
        profile_id: str,
        scenario: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """
        POST /profiles/:id/runScenario — запустить сценарий в браузере.

        Аналог start_scenario() в MsbClient.
        Сценарий должен быть зарегистрирован в browserLauncher MSB.
        """
        logger.info("msb: run_scenario(%s, %r)", profile_id, scenario)
        try:
            data = await self._post(
                f"/profiles/{profile_id}/runScenario",
                body={"scenario": scenario, "params": params or {}},
                timeout=timeout,
            )
        except httpx.HTTPStatusError as e:
            logger.warning("msb: run_scenario(%s, %r) — %s", profile_id, scenario, e)
            return {}
        return data if isinstance(data, dict) else {}

    # Алиас для совместимости с MsbClient.start_scenario()
    async def start_scenario(
        self,
        profile_id: str,
        scenario: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """Алиас run_scenario() — совместимость с MsbClient."""
        return await self.run_scenario(profile_id, scenario, params, timeout)

    # ── Куки ──────────────────────────────────────────────────────────────────

    async def get_cookies(
        self,
        profile_id: str,
        domain: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        GET /profiles/:id/cookies?format=json — куки профиля.

        Если браузер запущен — берёт живые куки из CDP.
        Если остановлен — берёт последний snapshot с диска.

        Возвращает dict {name: value}.
        Если domain указан — фильтрует по домену.
        """
        if not profile_id:
            return {}
        try:
            data = await self._get(
                f"/profiles/{profile_id}/cookies",
                params={"format": "json"},
            )
        except httpx.HTTPStatusError as e:
            logger.warning("msb: get_cookies(%s) — %s", profile_id, e)
            return {}

        if not data:
            return {}

        # Ответ: {format: "json", data: [...cookies...], count: N}
        cookies_raw = None
        if isinstance(data, dict):
            cookies_raw = data.get("data")
        elif isinstance(data, list):
            cookies_raw = data

        if not cookies_raw:
            return {}

        return self._cookies_list_to_dict(cookies_raw, domain_filter=domain)

    async def import_cookies(
        self,
        profile_id: str,
        cookies: List[dict],
    ) -> dict:
        """POST /profiles/:id/cookies — импортировать куки в профиль."""
        data = await self._post(
            f"/profiles/{profile_id}/cookies",
            body={"cookies": cookies},
        )
        return data if isinstance(data, dict) else {}

    async def clear_cookies(self, profile_id: str) -> dict:
        """DELETE /profiles/:id/cookies — очистить куки профиля."""
        data = await self._delete(f"/profiles/{profile_id}/cookies")
        return data if isinstance(data, dict) else {}

    # ── Прокси ────────────────────────────────────────────────────────────────

    async def check_proxy(self, profile_id: str) -> dict:
        """
        POST /profiles/:id/check-proxy — проверить прокси профиля.

        Возвращает:
          {hasProxy, status: "ok"|"error"|"direct", ip, country, city, latencyMs, proxyLabel}
        """
        try:
            data = await self._post(f"/profiles/{profile_id}/check-proxy")
        except httpx.HTTPStatusError as e:
            logger.warning("msb: check_proxy(%s) — %s", profile_id, e)
            return {"hasProxy": False, "status": "error", "error": str(e)}

        # Ответ обёрнут в {ok, data: {...}}
        if isinstance(data, dict) and "hasProxy" in data:
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data if isinstance(data, dict) else {}

    async def switch_proxy(
        self,
        profile_id: str,
        proxy: Optional[dict],
    ) -> dict:
        """
        POST /profiles/:id/switchProxy — сменить прокси на лету (без перезапуска).

        proxy = {protocol, host, port, username?, password?} или None (убрать прокси).
        """
        try:
            data = await self._post(
                f"/profiles/{profile_id}/switchProxy",
                body={"proxy": proxy},
            )
        except httpx.HTTPStatusError as e:
            logger.warning("msb: switch_proxy(%s) — %s", profile_id, e)
            return {}
        return data if isinstance(data, dict) else {}

    # ── Fingerprint ───────────────────────────────────────────────────────────

    async def refresh_fingerprint(
        self,
        profile_id: str,
        fingerprint: Optional[dict] = None,
    ) -> dict:
        """
        POST /profiles/:id/refreshFingerprint — обновить fingerprint.

        fingerprint = None -> генерируется автоматически MSB.
        """
        try:
            data = await self._post(
                f"/profiles/{profile_id}/refreshFingerprint",
                body={"fingerprint": fingerprint},
            )
        except httpx.HTTPStatusError as e:
            logger.warning("msb: refresh_fingerprint(%s) — %s", profile_id, e)
            return {}
        return data if isinstance(data, dict) else {}

    # ── Утилиты для куков ─────────────────────────────────────────────────────

    @staticmethod
    def _cookies_list_to_dict(
        cookies: Any,
        domain_filter: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Конвертирует список куков MSB/CDP в dict {name: value}.

        Формат куков CDP/MSB: [{name, value, domain, path, expires, ...}]
        Если domain_filter указан — оставляем только куки для этого домена.
        """
        if not cookies:
            return {}

        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if v is not None}

        if isinstance(cookies, list):
            out: Dict[str, str] = {}
            for c in cookies:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                if name is None:
                    continue
                if domain_filter:
                    c_domain = c.get("domain", "") or ""
                    if not (
                        c_domain == domain_filter
                        or c_domain.endswith("." + domain_filter)
                        or c_domain.lstrip(".") == domain_filter
                    ):
                        continue
                out[str(name)] = str(c.get("value", ""))
            return out

        return {}


# ──────────────────────────────────────────────────────────────────────────────
# CLI sanity-check: python msb_client.py
# ──────────────────────────────────────────────────────────────────────────────

async def _main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    print(f"\nMSB endpoint: {MSB_API_BASE}")
    print(f"Token in .env: {'да' if MSB_API_TOKEN else 'нет (без авторизации)'}")

    async with MsbClient() as msb:
        print("\n── Healthcheck ──────────────────────────────────────────")
        ok = await msb.is_available()
        print(f"is_available: {ok}")
        if not ok:
            print("❌ MSB API недоступен — убедись что MSB запущен")
            return

        print("\n── Профили ──────────────────────────────────────────────")
        profiles = await msb.get_profiles()
        print(f"get_profiles() -> {len(profiles)} профилей")
        for p in profiles:
            pid   = p.get("id")
            name  = p.get("name", "—")
            proxy = p.get("proxyLabel") or "без прокси"
            engine = p.get("engine", "auto")
            print(f"  id={pid}  name={name!r}  proxy={proxy}  engine={engine}")

        print("\n── Запущенные браузеры ───────────────────────────────────")
        running = await msb.get_running_profiles()
        print(f"get_running_profiles() -> {running}")

        if profiles:
            pid = profiles[0]["id"]
            name = profiles[0].get("name", "—")
            print(f"\n── Статус профиля [{name}] ({pid}) ───────────────────")
            status = await msb.get_profile_status(pid)
            print(f"  status: {status}")

            print(f"\n── Детали профиля [{name}] ────────────────────────────")
            detail = await msb.get_profile(pid)
            if detail:
                print(f"  name:    {detail.get('name')}")
                print(f"  engine:  {detail.get('engine')}")
                print(f"  proxy:   {detail.get('proxy')}")
                fp = detail.get("fingerprint", {})
                print(f"  UA:      {fp.get('userAgent', '—')[:60]}...")
                print(f"  locale:  {fp.get('locale')}")
                print(f"  TZ:      {fp.get('timezone')}")

            print(f"\n── Куки профиля [{name}] (ggsel.net) ─────────────────")
            cookies = await msb.get_cookies(pid, domain="ggsel.net")
            print(f"  куков для ggsel.net: {len(cookies)}")
            for k, v in list(cookies.items())[:5]:
                print(f"    {k} = {v[:30]}...")

            print(f"\n── Проверка прокси [{name}] ───────────────────────────")
            proxy_info = await msb.check_proxy(pid)
            print(f"  hasProxy: {proxy_info.get('hasProxy')}")
            print(f"  status:   {proxy_info.get('status')}")
            print(f"  ip:       {proxy_info.get('ip')}")
            print(f"  country:  {proxy_info.get('country')}")
            print(f"  latency:  {proxy_info.get('latencyMs')} ms")


if __name__ == "__main__":
    asyncio.run(_main())
