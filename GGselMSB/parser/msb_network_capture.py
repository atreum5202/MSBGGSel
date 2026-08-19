"""
parser/msb_network_capture.py
=============================
Клиент к MSB NetworkCapture API.

Доступные эндпоинты:
  POST /profiles/:id/network/clear       — сбросить буфер
  GET  /profiles/:id/network/status      — { active, count, oldestAt, newestAt }
  GET  /profiles/:id/network/requests    — список запросов
  GET  /profiles/:id/network/requests/:n — одна запись
  GET  /profiles/:id/network/endpoints   — сгруппированные по шаблону пути
  GET  /profiles/:id/network/har         — HAR 1.2 JSON
  GET  /network/captures                 — все активные профили

Все ошибки логируются, наружу не бросаются — при ошибке возвращаются
пустые структуры ([], {}, False).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False
    httpx = None  # type: ignore

logger = logging.getLogger("msb.network_capture")


class MsbNetworkCapture:
    """
    HTTP-клиент к MSB NetworkCapture API.

    Пример:
        nc = MsbNetworkCapture()
        nc.clear(profile_id)
        await asyncio.sleep(5)
        eps = nc.endpoints(profile_id, host="api.ggsel.net")
    """

    def __init__(self, msb_url: str = "http://127.0.0.1:17248"):
        if not _HTTPX_OK:
            raise RuntimeError("httpx не установлен: pip install httpx")
        self._base = msb_url.rstrip("/")
        self._client = httpx.Client(timeout=15.0)

    # ── Внутренний хелпер ────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self._base}{path}"
        try:
            r = self._client.get(url, params={k: v for k, v in (params or {}).items() if v is not None})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("NetworkCapture GET %s — %s", path, e)
            return None

    def _post(self, path: str, json_body: Optional[dict] = None) -> Any:
        url = f"{self._base}{path}"
        try:
            r = self._client.post(url, json=json_body or {})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error("NetworkCapture POST %s — %s", path, e)
            return None

    # ── Публичный API ────────────────────────────────────────────────────────

    def clear(self, profile_id: str) -> dict:
        """Сбросить буфер захвата для профиля."""
        result = self._post(f"/profiles/{profile_id}/network/clear")
        return result if isinstance(result, dict) else {}

    def status(self, profile_id: str) -> dict:
        """Статус захвата: { active, count, oldestAt, newestAt }."""
        result = self._get(f"/profiles/{profile_id}/network/status")
        return result if isinstance(result, dict) else {}

    def endpoints(
        self,
        profile_id: str,
        pattern: Optional[str] = None,
        host: Optional[str] = None,
        limit: int = 200,
    ) -> list:
        """
        Список эндпоинтов, сгруппированных по шаблону пути.

        pattern — фильтр по паттерну пути (glob/regex, зависит от MSB)
        host    — фильтр по хосту
        limit   — максимум записей
        """
        result = self._get(
            f"/profiles/{profile_id}/network/endpoints",
            params={"pattern": pattern, "host": host, "limit": limit},
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # MSB иногда оборачивает в { data: [...] } или { endpoints: [...] }
            for key in ("data", "endpoints", "items"):
                if isinstance(result.get(key), list):
                    return result[key]
        logger.warning("endpoints: неожиданный ответ %r", type(result))
        return []

    def requests(
        self,
        profile_id: str,
        host: Optional[str] = None,
        pattern: Optional[str] = None,
        method: Optional[str] = None,
        status: Optional[int] = None,
        limit: int = 500,
    ) -> list:
        """Список перехваченных запросов с возможностью фильтрации."""
        result = self._get(
            f"/profiles/{profile_id}/network/requests",
            params={
                "host": host,
                "pattern": pattern,
                "method": method,
                "status": status,
                "limit": limit,
            },
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("data", "requests", "items"):
                if isinstance(result.get(key), list):
                    return result[key]
        return []

    def request_by_index(self, profile_id: str, index: int) -> dict:
        """Получить одну запись запроса по индексу."""
        result = self._get(f"/profiles/{profile_id}/network/requests/{index}")
        return result if isinstance(result, dict) else {}

    def har(self, profile_id: str) -> dict:
        """Экспорт трафика в формате HAR 1.2."""
        result = self._get(f"/profiles/{profile_id}/network/har")
        return result if isinstance(result, dict) else {}

    def captures(self) -> list:
        """Список всех профилей с активным захватом трафика."""
        result = self._get("/network/captures")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("data", "captures", "profiles"):
                if isinstance(result.get(key), list):
                    return result[key]
        return []

    def wait_for_traffic(
        self,
        profile_id: str,
        min_count: int = 10,
        timeout: float = 30.0,
    ) -> bool:
        """
        Синхронно ждёт, пока буфер захвата не накопит min_count запросов.

        Возвращает True если условие выполнено, False если истёк таймаут.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.status(profile_id)
            count = st.get("count", 0)
            logger.debug("wait_for_traffic: count=%d / min=%d", count, min_count)
            if count >= min_count:
                logger.info("wait_for_traffic: накоплено %d запросов", count)
                return True
            time.sleep(1.5)
        logger.warning(
            "wait_for_traffic: таймаут %ds истёк, count=%d < %d",
            int(timeout),
            self.status(profile_id).get("count", 0),
            min_count,
        )
        return False

    async def wait_for_traffic_async(
        self,
        profile_id: str,
        min_count: int = 10,
        timeout: float = 30.0,
    ) -> bool:
        """Асинхронная версия wait_for_traffic."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            st = self.status(profile_id)
            count = st.get("count", 0)
            logger.debug("wait_for_traffic_async: count=%d / min=%d", count, min_count)
            if count >= min_count:
                logger.info("wait_for_traffic_async: накоплено %d запросов", count)
                return True
            await asyncio.sleep(1.5)
        logger.warning("wait_for_traffic_async: таймаут истёк")
        return False

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── CLI для отладки ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    profile = sys.argv[1] if len(sys.argv) > 1 else "1873432d-b054-48a6-a031-b2bacc0fe77d"
    nc = MsbNetworkCapture()

    print(f"=== status({profile}) ===")
    print(json.dumps(nc.status(profile), indent=2, ensure_ascii=False))

    print(f"\n=== endpoints({profile}) ===")
    eps = nc.endpoints(profile, limit=20)
    for ep in eps[:10]:
        print(" ", ep)
    print(f"  ... всего {len(eps)}")
