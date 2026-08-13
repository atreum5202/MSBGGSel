"""
msb_cookies.py — получение Qrator-куков ggsel.net через MSB REST API.

MSB — антидетект-браузер. Локальный REST API слушает
на http://127.0.0.1:58888. Используется так же, как MSB: запускаем профиль,
прогоняем сценарий ggsel-login, забираем cookies, закрываем.

Логика работы:
  1. Пробуем прочитать куки из AppData-снапшота профиля (cookies-snapshot.json)
     — если они свежие (< COOKIE_TTL_SECONDS), возвращаем их без запроса к API.
  2. Проверяем доступность MSB API.
  3. Если MSB доступен — используем его (берём/создаём профиль,
     запускаем, прогоняем сценарий, забираем куки).
  4. Если MSB недоступен — логируем ошибку, куки не возвращаем.

Профили НЕ удаляются. Снапшоты куков хранятся в %APPDATA%\\MSB\\profiles.

Использование:
    from msb_cookies import QratorCookieMiddleware

    async with QratorCookieMiddleware() as mw:
        cookies = await mw.cookies()
        # cookies — dict вида {"__qrator_jsid": "...", ...}
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv

from .msb_client import MsbClient  # noqa: E402

# Импортируем валидатор куков из основного модуля (или определяем локально)
try:
    from ggsel_parser_v2 import validate_qrator_cookies, QRATOR_COOKIE_KEYS
except ImportError:
    QRATOR_COOKIE_KEYS = {"qrator_msid2", "__ddg1_", "__ddg2_", "qrator_jsid", "__qrator_jsid", "qrator_ssid"}
    def validate_qrator_cookies(cookies: dict) -> bool:
        # ggsel.net использует Qrator (не Cloudflare) — обязательна qrator_msid2
        if not cookies:
            return False
        return bool(cookies.get("qrator_msid2", "").strip())

# Загружаем .env из GGselV7/.env (2 уровня вверх от parser/msb_cookies.py).
# Если файла нет — молча работаем на os.getenv-дефолтах. Это не GGSeller,
# у нас свой изолированный конфиг.
try:
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(dotenv_path=_ENV_PATH, override=False)
except Exception:
    pass

logger = logging.getLogger("msb_cookies")  # имя логгера оставлено для совместимости

# ──────────────────────────────────────────────────────────────────
# Конфиг
# ──────────────────────────────────────────────────────────────────
MSB_API_BASE = os.getenv("MSB_API_BASE", "http://127.0.0.1:17248")
MSB_API_KEY = os.getenv("MSB_API_TOKEN", "")
# Параметр сохранён для обратной совместимости (старый код мог передавать profile_id явно).
# В MSB id узнаётся через get_profiles()/start_profile(), прямого env обычно нет.
MSB_PROFILE_ID = os.getenv("MSB_PROFILE_ID", "")

SCENARIO_NAME    = "ggsel-login"
# Совместимость со старым именем: MSB_SCENARIO_TIMEOUT_MS.
SCENARIO_TIMEOUT = int(
    os.getenv("MSB_SCENARIO_TIMEOUT_MS", "45000")
)  # мс

# Куки считаются «свежими» столько секунд. Приоритет у нового имени, fallback на MSB_*.
COOKIE_TTL_SECONDS = int(
    os.getenv("MSB_COOKIE_TTL", "3600")
)

# AppData-папка для наших снапшотов куков.
# По умолчанию — %APPDATA%\\MSB\\profiles (путь к снапшотам).
_APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
GGSEL_PROFILES_DIR: Path = Path(
    os.getenv("GGSEL_PROFILES_DIR", str(_APPDATA / "MSB" / "profiles"))
)


# ──────────────────────────────────────────────────────────────────
# Вспомогательные функции для работы с AppData-снапшотами
# ──────────────────────────────────────────────────────────────────

def _snapshot_path(profile_id: str) -> Path:
    return GGSEL_PROFILES_DIR / profile_id / "cookies-snapshot.json"


def _meta_path(profile_id: str) -> Path:
    return GGSEL_PROFILES_DIR / profile_id / "meta.json"


def _index_path() -> Path:
    return GGSEL_PROFILES_DIR / "index.json"


def _load_snapshot_from_disk(profile_id: str) -> Optional[dict]:
    """Читает cookies-snapshot.json с диска. Возвращает dict или None."""
    p = _snapshot_path(profile_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Поддерживаем как список объектов, так и dict {name: value}
        if isinstance(data, list):
            return {c["name"]: c["value"] for c in data if c.get("name")}
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _snapshot_age_seconds(profile_id: str) -> float:
    """Возвращает возраст снапшота в секундах. inf если файла нет."""
    p = _snapshot_path(profile_id)
    try:
        return time.time() - p.stat().st_mtime
    except OSError:
        return float("inf")


def _save_snapshot_to_disk(profile_id: str, cookies: dict) -> None:
    """Сохраняет dict куков в cookies-snapshot.json атомарно (write + rename).

    Атомарная запись гарантирует что при сбое (disk full, kill процесса)
    snap_file либо полностью старый, либо полностью новый — никогда не битый.
    """
    snap_file = _snapshot_path(profile_id)
    snap_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = snap_file.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.replace(snap_file)  # атомарный rename (POSIX) / atomic на Windows NTFS
        logger.debug("msb_cookies: снапшот сохранён → %s", snap_file)
    except Exception:
        try:
            tmp_file.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _find_ggsel_profile_id_from_disk() -> Optional[str]:
    """
    Ищет профиль «ggsel-parser» в AppData (GGSEL_PROFILES_DIR).
    Возвращает его id или None.
    """
    index = _index_path()
    if index.exists():
        try:
            idx = json.loads(index.read_text(encoding="utf-8"))
            for p in idx.get("profiles", []):
                if p.get("name", "").lower() in ["atreum.5202@gmail.com", "ggsel-parser"]:
                    return p["id"]
            # Нет именного — берём первый
            if idx.get("profiles"):
                return idx["profiles"][0]["id"]
        except Exception:
            pass

    # Сканируем папки
    try:
        for entry in GGSEL_PROFILES_DIR.iterdir():
            if entry.is_dir():
                meta_file = entry / "meta.json"
                if meta_file.exists():
                    return entry.name
    except OSError:
        pass
    return None


# ──────────────────────────────────────────────────────────────────
# Middleware для парсера (публичный интерфейс не менялся)
# ──────────────────────────────────────────────────────────────────

class QratorCookieMiddleware:
    """
    Обёртка над MsbClient — автоматически обновляет куки при 401.

    Внутри держит свой in-memory кеш + AppData-снапшот, так что при наличии
    свежего cookies.json ходить в API не нужно.

    Использование в парсере:
        middleware = QratorCookieMiddleware()
        await middleware.init()

        cookies = await middleware.cookies()

        # Если получили 401:
        cookies = await middleware.cookies(force_refresh=True)

    Принимает **kwargs для обратной совместимости со старыми вызовами:
      - base_url, api_key, timeout  — напрямую в MsbClient
      - token                      — legacy-алиас для api_key
      - profile_id, cookie_ttl     — legacy-параметры (используются где возможно)
    """

    def __init__(self, **kwargs: Any):
        # Backward compat: api_key → token (MsbClient принимает token=)
        if "api_key" in kwargs and "token" not in kwargs:
            kwargs["token"] = kwargs.pop("api_key")
        # Legacy параметры: profile_id оставим в self, cookie_ttl в self
        self._explicit_profile_id: Optional[str] = kwargs.pop("profile_id", None) or MSB_PROFILE_ID or None
        self._cookie_ttl: int = int(kwargs.pop("cookie_ttl", COOKIE_TTL_SECONDS))

        # Всё остальное уйдёт в MsbClient (base_url, token, timeout)
        self._client = MsbClient(**kwargs)
        self._lock = asyncio.Lock()

        # Кеш (in-memory)
        self._cache: Optional[Dict[str, str]] = None
        self._fetched_at: float = 0.0

        # profile_id, выбранный для текущей сессии
        self._profile_id: Optional[str] = self._explicit_profile_id

    # ── Lifecycle (интерфейс не менялся) ──────────────────────────

    async def init(self) -> None:
        # MsbClient ленивый — http-клиент создаётся при первом запросе.
        # Здесь просто логируем готовность.
        logger.debug("QratorCookieMiddleware: init, base=%s, token_set=%s",
                     self._client.base_url, bool(self._client.token))

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass

    async def __aenter__(self) -> "QratorCookieMiddleware":
        await self.init()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Публичный интерфейс (не менялся) ─────────────────────────

    async def cookies(self, force_refresh: bool = False) -> Dict[str, str]:
        async with self._lock:
            return await self._get_cookies(force_refresh)

    def invalidate(self) -> None:
        """Сбрасывает in-memory кеш."""
        self._cache = None
        self._fetched_at = 0.0
        logger.info("msb_cookies: in-memory кеш инвалидирован")

    async def cookies_validated(self, force_refresh: bool = False) -> Dict[str, str]:
        """
        Возвращает куки и проверяет их через validate_qrator_cookies.
        Если валидация не проходит — форсирует обновление через MSB API.
        """
        async with self._lock:
            cookies = await self._get_cookies(force_refresh=force_refresh)
            if not validate_qrator_cookies(cookies):
                logger.warning("msb_cookies: куки не прошли валидацию — принудительное обновление")
                self.invalidate()
                cookies = await self._get_cookies(force_refresh=True)
                if not validate_qrator_cookies(cookies):
                    logger.error("msb_cookies: повторное обновление тоже вернуло невалидные куки: %s",
                                 list(cookies.keys()) if cookies else "пусто")
            return cookies

    # ── Внутренняя логика (переписана на MsbClient) ────────

    async def _get_cookies(self, force_refresh: bool) -> Dict[str, str]:
        """
        Возвращает dict с куками ggsel.net.
        Порядок: in-memory кеш → AppData снапшот → MSB API.
        """
        # 1. In-memory кеш
        if not force_refresh and self._is_cache_valid():
            logger.debug("msb_cookies: возвращаю куки из in-memory кеша")
            return self._cache or {}

        # 2. AppData снапшот (диск)
        if not force_refresh:
            disk_cookies = await self._try_disk_cache()
            if disk_cookies:
                self._cache = disk_cookies
                self._fetched_at = time.monotonic()
                return disk_cookies

        # 3. MSB API
        try:
            available = await self._client.is_available()
        except Exception as e:
            logger.warning("msb_cookies: не удалось проверить доступность MSB: %s", e)
            available = False

        if not available:
            logger.error("msb_cookies: MSB API недоступен. Проверь что он запущен на %s",
                         self._client.base_url)
            return {}

        try:
            cookies = await self._fetch_cookies_via_msb()
        except Exception as e:
            logger.warning("msb_cookies: ошибка MSB API: %s", e)
            return {}

        if cookies:
            self._cache = cookies
            self._fetched_at = time.monotonic()
            if self._profile_id:
                try:
                    _save_snapshot_to_disk(self._profile_id, cookies)
                except Exception as e:
                    logger.debug("msb_cookies: не удалось сохранить снапшот: %s", e)
            logger.info("msb_cookies: получено %d куков ggsel.net", len(cookies))
        return cookies

    async def _try_disk_cache(self) -> Optional[Dict[str, str]]:
        """Пробует загрузить свежие куки с диска."""
        pid = self._profile_id or _find_ggsel_profile_id_from_disk()
        if not pid:
            return None
        self._profile_id = pid

        age = _snapshot_age_seconds(pid)
        if age > self._cookie_ttl:
            logger.debug("msb_cookies: снапшот устарел (%ss > %ss)", f"{age:.0f}", self._cookie_ttl)
            return None

        cookies = _load_snapshot_from_disk(pid)
        if cookies:
            logger.info("msb_cookies: куки загружены с диска (возраст %ss), профиль %s",
                        f"{age:.0f}", pid)
            return cookies
        return None

    async def _fetch_cookies_via_msb(self) -> Dict[str, str]:
        """
        Полный цикл через MsbClient:
          resolve profile → ensure running → run scenario → get cookies → stop.
        """
        await self._resolve_profile()
        await self._ensure_profile_running()

        # Запускаем сценарий
        scenario_result = await self._client.start_scenario(
            self._profile_id,  # type: ignore[arg-type]
            SCENARIO_NAME,
            params={"timeoutMs": SCENARIO_TIMEOUT},
        )
        logger.debug("msb_cookies: сценарий завершён: %s", scenario_result)

        # Сценарий может вернуть куки прямо в теле
        if isinstance(scenario_result, dict) and scenario_result.get("cookies"):
            clist = scenario_result["cookies"]
            cookies = self._cookies_list_to_dict(clist)
            if cookies:
                return cookies

        # Иначе — забираем cookies отдельным вызовом (фильтр по домену)
        cookies = await self._client.get_cookies(
            self._profile_id,  # type: ignore[arg-type]
            domain="ggsel.net",
        )
        return cookies

    async def _resolve_profile(self) -> None:
        """
        Если profile_id задан явно — используем его.
        Иначе — берём первый профиль из MSB (или ищем по имени ggsel-parser).
        """
        if self._profile_id:
            # Проверим что он существует
            prof = await self._client.get_profile(self._profile_id)
            if prof:
                logger.debug("msb_cookies: используем профиль %s", self._profile_id)
                return
            logger.warning("msb_cookies: профиль %r не найден в MSB, берём из списка",
                           self._profile_id)
            self._profile_id = None

        profiles = await self._client.get_profiles()
        if not profiles:
            raise RuntimeError(
                "MSB не вернул ни одного профиля. Создайте профиль в MSB UI."
            )

        # Ищем именной профиль пользователя или «ggsel-parser»
        for p in profiles:
            if (p.get("name") or "").lower() in ["atreum.5202@gmail.com", "ggsel-parser"]:
                self._profile_id = str(p["id"])
                logger.info("msb_cookies: выбран профиль '%s': %s", p.get("name"), self._profile_id)
                return

        # Иначе — первый
        self._profile_id = str(profiles[0]["id"])
        logger.info("msb_cookies: выбран первый профиль: %s", self._profile_id)

    async def _ensure_profile_running(self) -> None:
        """Запускает профиль если он не запущен."""
        status = await self._client.get_profile_status(self._profile_id)  # type: ignore[arg-type]
        if status == "running":
            logger.debug("msb_cookies: профиль %s уже запущен", self._profile_id)
            return

        logger.info("msb_cookies: запускаю профиль %s...", self._profile_id)
        result = await self._client.start_profile(self._profile_id)  # type: ignore[arg-type]
        if not result:
            raise RuntimeError(f"Не удалось запустить профиль {self._profile_id} в MSB")
        # Дать браузеру время на инициализацию перед сценарием
        await asyncio.sleep(2)

    # ── Кеш (in-memory) ──────────────────────────────────────────

    def _is_cache_valid(self) -> bool:
        if not self._cache:
            return False
        if not validate_qrator_cookies(self._cache):
            logger.warning("msb_cookies: кеш содержит куки без Qrator-ключей — инвалидируем")
            self.invalidate()
            return False
        return (time.monotonic() - self._fetched_at) < self._cookie_ttl

    @staticmethod
    def _cookies_list_to_dict(cookies: Any) -> Dict[str, str]:
        """
        Поддерживает разные форматы: list[{name,value}], dict{name:value}.
        """
        if not cookies:
            return {}
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items() if v}
        if isinstance(cookies, list):
            out: Dict[str, str] = {}
            for c in cookies:
                if isinstance(c, dict) and c.get("name") is not None:
                    out[str(c["name"])] = str(c.get("value", ""))
            return out
        return {}


# ──────────────────────────────────────────────────────────────────
# CLI тест
# ──────────────────────────────────────────────────────────────────

async def _main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

    print(f"AppData профили: {GGSEL_PROFILES_DIR}")
    async with QratorCookieMiddleware() as mw:
        print("Получаю куки ggsel.net...")
        cookies = await mw.cookies()
        if cookies:
            print(f"\nУспех! Получено {len(cookies)} куков:")
            for name, value in cookies.items():
                preview = value[:40] + "..." if len(value) > 40 else value
                print(f"  {name} = {preview}")
        else:
            print("Куки не получены — проверь логи выше")


if __name__ == "__main__":
    asyncio.run(_main())

