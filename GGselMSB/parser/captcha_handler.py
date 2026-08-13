"""
parser/captcha_handler.py
=========================
Детект и обработка captcha-страниц в HTML ggsel.net.

Когда Qrator выкидывает НЕ обычный JS-челлендж, а реальную captcha
(reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile) — обычный scenario
ggsel-login скорее всего её не пройдёт. Этот модуль:

  1. Детектит captcha-страницу по маркерам в HTML.
  2. Запрашивает у MSB перезапуск сценария с solveCaptcha=true.
  3. Если MSB не умеет решать (нет 2captcha-ключа или нет интеграции
     в ggselLogin.js) — пробует просто перезапустить scenario с
     увеличенными таймаутами (Qrator иногда re-rolls challenge).
  4. Возвращает новые cookies если получилось, иначе None.

ВАЖНО: на момент написания этого файла MSB scenario ggselLogin.js
НЕ ЧИТАЕТ params.solveCaptcha — он там игнорируется. Поддержка
должна быть добавлена в Controller/MSB/src/main/lib/scenarios/ggselLogin.js.
Мы делаем best-effort: посылаем флаг, надеемся на лучшее, фоллбечимся
на просто retry с большим таймаутом.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("ggselv7.captcha")

# ── Маркеры captcha в HTML ────────────────────────────────────────────────
# Каждый паттерн проверен на нескольких реальных страницах:
#  - Google reCAPTCHA:   g-recaptcha, grecaptcha, www.google.com/recaptcha
#  - hCaptcha:           h-captcha, hcaptcha, js.hcaptcha.com
#  - Cloudflare:         cf-challenge, __cf_chl, cf_clearance, "Just a moment"
#  - Qrator:             __qrator, qauth.js (обычно это JS-челлендж, но мы
#                        включаем чтобы отличать от чистой 200 OK страницы)
CAPTCHA_PATTERNS: List[re.Pattern] = [
    re.compile(r"g-recaptcha", re.IGNORECASE),
    re.compile(r"grecaptcha", re.IGNORECASE),
    re.compile(r"google\.com/recaptcha", re.IGNORECASE),
    re.compile(r"h-captcha", re.IGNORECASE),
    re.compile(r"hcaptcha\.com", re.IGNORECASE),
    re.compile(r"cf-challenge", re.IGNORECASE),
    re.compile(r"__cf_chl", re.IGNORECASE),
    re.compile(r"just a moment", re.IGNORECASE),  # Cloudflare
    re.compile(r"checking your browser", re.IGNORECASE),  # Cloudflare
    re.compile(r"__qrator/qauth\.js", re.IGNORECASE),  # Qrator JS challenge
]

# Маркеры "страница прошла challenge" — если они есть, captcha точно нет
SUCCESS_MARKERS: List[re.Pattern] = [
    re.compile(r"productcard-module", re.IGNORECASE),  # ggsel каталог
    re.compile(r"\"@type\"\s*:\s*\"product\"", re.IGNORECASE),  # JSON-LD product
    re.compile(r"<title>[^<]*купить", re.IGNORECASE),  # наша типичная страница
]


@dataclass
class CaptchaDetection:
    """Результат детекта captcha в HTML."""
    is_captcha: bool
    matched_pattern: Optional[str] = None
    matched_marker: Optional[str] = None

    def __bool__(self) -> bool:  # позволяет писать `if is_captcha_page(html):`
        return self.is_captcha


class CaptchaHandler:
    """
    Детектит captcha-страницы и запрашивает у MSB их решение.

    Использование:
        handler = CaptchaHandler(httpx_client)
        result = handler.is_captcha_page(html)   # sync
        if result:
            new_cookies = await handler.solve_via_msb(profile_id)
    """

    # Параметры которые посылаем в MSB scenario ggsel-login
    DEFAULT_SCENARIO_TIMEOUT_MS = 60_000   # дольше обычного (45s)
    DEFAULT_MAX_RETRIES = 5
    SOLVE_CAPTCHA_TIMEOUT_MS = 120_000     # если 2captcha — может быть долго

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:17248",
        token: str = "",
        enabled: bool = True,
    ):
        """
        Args:
            http_client: общий httpx.AsyncClient (для keep-alive).
            base_url:    MSB API base (default http://127.0.0.1:17248).
            token:       MSB_API_TOKEN если задан.
            enabled:     если False — solve_via_msb сразу возвращает None.
        """
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._enabled = enabled

    # ── Детект ─────────────────────────────────────────────────────────────

    def is_captcha_page(self, html: str) -> CaptchaDetection:
        """
        Синхронная проверка HTML на наличие captcha-маркеров.

        Returns:
            CaptchaDetection(is_captcha, matched_pattern, matched_marker)
        """
        if not html:
            return CaptchaDetection(is_captcha=False)
        # Сначала — success-маркеры (если страница явно прошла challenge)
        for m in SUCCESS_MARKERS:
            if m.search(html):
                return CaptchaDetection(is_captcha=False, matched_marker=m.pattern)
        # Потом — captcha-маркеры
        for p in CAPTCHA_PATTERNS:
            if p.search(html):
                return CaptchaDetection(is_captcha=True, matched_pattern=p.pattern)
        return CaptchaDetection(is_captcha=False)

    # ── Решение через MSB ──────────────────────────────────────────────────

    async def solve_via_msb(self, profile_id: str) -> Optional[Dict[str, str]]:
        """
        Просит MSB перезапустить сценарий ggsel-login для профиля.
        Пробует две стратегии:
          1. params.solveCaptcha=true (если MSB это поддерживает в будущем)
          2. params с увеличенными timeoutMs/maxRetries (текущий фоллбек)

        Возвращает dict {cookie_name: value} если получилось, иначе None.
        """
        if not self._enabled:
            logger.debug("captcha_handler: disabled в конфиге — пропускаем solve")
            return None

        # Стратегия 1: solveCaptcha=true
        cookies = await self._try_run_scenario(
            profile_id,
            params={
                "timeoutMs": self.SOLVE_CAPTCHA_TIMEOUT_MS,
                "maxRetries": self.DEFAULT_MAX_RETRIES,
                "solveCaptcha": True,
            },
            strategy="solveCaptcha",
        )
        if cookies:
            return cookies

        # Стратегия 2: просто retry с увеличенным таймаутом
        logger.info("captcha_handler: solveCaptcha не сработал, пробуем retry с большим таймаутом")
        cookies = await self._try_run_scenario(
            profile_id,
            params={
                "timeoutMs": self.DEFAULT_SCENARIO_TIMEOUT_MS,
                "maxRetries": self.DEFAULT_MAX_RETRIES,
            },
            strategy="retry-extended",
        )
        return cookies

    async def _try_run_scenario(
        self,
        profile_id: str,
        params: Dict[str, Any],
        strategy: str,
    ) -> Optional[Dict[str, str]]:
        """Один вызов runScenario + парсинг cookies."""
        url = f"{self._base_url}/profiles/{profile_id}/runScenario"
        body = {"scenario": "ggsel-login", "params": params}
        t0 = time.monotonic()
        try:
            resp = await self._http.post(url, json=body, timeout=180.0)
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code >= 500:
                logger.warning(
                    "captcha_handler: [%s] MSB вернул %d за %dms — server error",
                    strategy, resp.status_code, ms,
                )
                return None
            if resp.status_code >= 400:
                txt = resp.text[:200]
                logger.warning(
                    "captcha_handler: [%s] MSB вернул %d за %dms: %s",
                    strategy, resp.status_code, ms, txt,
                )
                return None

            data = resp.json()
            # MSB формат: {ok: true, data: {...}} — вытаскиваем inner
            inner = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(inner, dict):
                logger.warning("captcha_handler: [%s] неожиданный формат: %s", strategy, data)
                return None

            # Извлекаем cookies
            cookies = self._extract_cookies(inner)
            if cookies:
                logger.info(
                    "captcha_handler: [%s] ✓ получили %d cookies за %dms",
                    strategy, len(cookies), ms,
                )
                return cookies

            # Если scenario вернул status != ok — лог
            status = inner.get("status")
            if status and status != "ok":
                logger.warning(
                    "captcha_handler: [%s] scenario вернул status=%s за %dms: %s",
                    strategy, status, ms, str(inner)[:300],
                )
            else:
                logger.warning(
                    "captcha_handler: [%s] scenario ok но cookies пустые за %dms",
                    strategy, ms,
                )
            return None

        except httpx.TimeoutException:
            logger.warning("captcha_handler: [%s] timeout при вызове runScenario", strategy)
            return None
        except Exception as e:
            logger.warning("captcha_handler: [%s] ошибка: %s", strategy, str(e)[:200])
            return None

    @staticmethod
    def _extract_cookies(scenario_result: Dict[str, Any]) -> Dict[str, str]:
        """
        Вытаскивает cookies из ответа scenario.
        Поддерживает два формата:
          - список объектов: [{name, value, ...}, ...]
          - вложенные: {cookies: [...]}
        """
        if not isinstance(scenario_result, dict):
            return {}
        raw = scenario_result.get("cookies")
        if not raw:
            # Иногда data выглядит как {data: {cookies: [...]}} от двойной обёртки
            inner = scenario_result.get("data")
            if isinstance(inner, dict):
                raw = inner.get("cookies")
        if isinstance(raw, list):
            return {c["name"]: c["value"] for c in raw if c.get("name") and c.get("value")}
        if isinstance(raw, dict):
            # уже dict {name: value}
            return {k: v for k, v in raw.items() if v}
        return {}

    # ── Диагностика ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "base_url": self._base_url,
            "patterns_count": len(CAPTCHA_PATTERNS),
            "success_markers_count": len(SUCCESS_MARKERS),
        }


# ── Синглтон (для удобства) ───────────────────────────────────────────────
_handler: Optional[CaptchaHandler] = None


def get_handler(http_client: Optional[httpx.AsyncClient] = None, **kwargs) -> CaptchaHandler:
    """Синглтон CaptchaHandler. http_client создаётся если не передан."""
    global _handler
    if _handler is None:
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=60.0)
        _handler = CaptchaHandler(http_client=http_client, **kwargs)
    return _handler
