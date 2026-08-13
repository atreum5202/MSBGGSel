# -*- coding: utf-8 -*-
"""
GGselV7 — единая точка конфигурации.
======================================

Все ключи/пути живут здесь. Секреты — в ENV (НЕ коммитим).
Файл можно править руками или переопределять через переменные окружения.

Структура:
  - GGSEL API ключи (для seller.ggsel.com)        ← НЕ ТРОГАТЬ без согласования
  - Локальный Flask-сервер
  - MSB Integration
  - Profile Pool
  - Adaptive Rate Limit
  - Telemetry
  - Captcha
  - Воронка топ-100
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
except Exception:
    pass


def _parse_csv_env(name: str, fallback_names: tuple[str, ...] = ()) -> list[str]:
    raw = os.getenv(name, "")
    if not raw:
        for fallback in fallback_names:
            raw = os.getenv(fallback, "")
            if raw:
                break
    return [item.strip() for item in raw.split(",") if item.strip()]

# ═══════════════════════════════════════════════════════════════════════════
#  GGSEL API настройки (НЕ ТРОГАТЬ — это ключи продавца)
# ═══════════════════════════════════════════════════════════════════════════
GGSEL_API_KEY = os.getenv(
    "GGSEL_API_KEY",
    "afbb737281b23621707ff00be1ee31dee1e6667820ebb3d11b289f4fc707b9fc",
)
GGSEL_SELLER_ID = int(os.getenv("GGSEL_SELLER_ID", "114509777"))

# Базовый URL GGSEL Seller API
BASE_URL = "https://seller.ggsel.com"

# Таймаут HTTP-запросов к GGSEL (сек)
HTTP_TIMEOUT = int(os.getenv("GGSEL_HTTP_TIMEOUT", "30"))
# Сколько раз повторять таймаут-запросы
HTTP_RETRIES = int(os.getenv("GGSEL_HTTP_RETRIES", "2"))

# ═══════════════════════════════════════════════════════════════════════════
#  Локальный Flask-сервер
# ═══════════════════════════════════════════════════════════════════════════
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "5000"))

# ═══════════════════════════════════════════════════════════════════════════
#  Антидетект — активный бэкенд
# ═══════════════════════════════════════════════════════════════════════════
ANTIDETECT_BACKEND = "msb"

# ═══════════════════════════════════════════════════════════════════════════
#  MSB Integration
# ═══════════════════════════════════════════════════════════════════════════
MSB_API_BASE = os.getenv("MSB_API_BASE", "http://127.0.0.1:17248")
MSB_API_TOKEN = os.getenv("MSB_API_TOKEN", "")
MSB_PROFILE_ID = os.getenv("MSB_PROFILE_ID", "")
MSB_HEADLESS = os.getenv("MSB_HEADLESS", "false").lower() == "true"
MSB_COOKIE_TTL = int(os.getenv("MSB_COOKIE_TTL", "3600"))
MSB_GGSEL_DOMAIN = "ggsel.net"

# Конкретные ID профилей (если пусто — берём все доступные профили)
PARSER_PROFILE_IDS: list[str] = _parse_csv_env("MSB_PROFILE_IDS")

_APPDATA_FALLBACK = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
MSB_PROFILES_DIR = Path(
    os.getenv("MSB_PROFILES_DIR", str(_APPDATA_FALLBACK / "MSB" / "profiles"))
)

# ═══════════════════════════════════════════════════════════════════════════
#  Profile Pool (ротация, anti-hijack) — общая для обоих бэкендов
# ═══════════════════════════════════════════════════════════════════════════
POOL_MAX_HITS = int(os.getenv("POOL_MAX_HITS", "80"))
POOL_REST_SEC = int(os.getenv("POOL_REST_SEC", "300"))
POOL_SKIP_RUNNING = os.getenv("POOL_SKIP_RUNNING", "true").lower() == "true"

# ═══════════════════════════════════════════════════════════════════════════
#  Adaptive Rate Limit
# ═══════════════════════════════════════════════════════════════════════════
RATE_BASE_DELAY = float(os.getenv("RATE_BASE_DELAY", "4.0"))
RATE_MIN_DELAY = float(os.getenv("RATE_MIN_DELAY", "2.0"))
RATE_MAX_DELAY = float(os.getenv("RATE_MAX_DELAY", "60.0"))
RATE_OK_DECAY = float(os.getenv("RATE_OK_DECAY", "0.95"))
RATE_429_MULT = float(os.getenv("RATE_429_MULT", "2.0"))
RATE_401_MULT = float(os.getenv("RATE_401_MULT", "3.0"))
RATE_5XX_MULT = float(os.getenv("RATE_5XX_MULT", "1.5"))
RATE_STATE_FILE = os.getenv("RATE_STATE_FILE", "data/rate_state.json")

# ═══════════════════════════════════════════════════════════════════════════
#  Telemetry (локальная, без отправки наружу)
# ═══════════════════════════════════════════════════════════════════════════
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"
TELEMETRY_DIR = os.getenv("TELEMETRY_DIR", "data/telemetry")
TELEMETRY_MAX_FILE_MB = float(os.getenv("TELEMETRY_MAX_FILE_MB", "50.0"))

# ═══════════════════════════════════════════════════════════════════════════
#  Captcha (auto-solve через MSB)
# ═══════════════════════════════════════════════════════════════════════════
CAPTCHA_ENABLED = os.getenv("CAPTCHA_ENABLED", "true").lower() == "true"
CAPTCHA_SCENARIO_PARAM = {"solveCaptcha": True}
CAPTCHA_MAX_RETRIES = int(os.getenv("CAPTCHA_MAX_RETRIES", "1"))

# ═══════════════════════════════════════════════════════════════════════════
#  Parser Engine
# ═══════════════════════════════════════════════════════════════════════════
PARSER_USE_MSB = os.getenv("PARSER_USE_MSB", "true").lower() == "true"
PARSER_MAX_QUANTITY = int(os.getenv("PARSER_MAX_QUANTITY", "100"))
PARSER_MAX_PAGES = int(os.getenv("PARSER_MAX_PAGES", "3"))

# ═══════════════════════════════════════════════════════════════════════════
#  Воронка топ-100
#  Правь эти значения для настройки под свои нужды
# ═══════════════════════════════════════════════════════════════════════════

# ── Whitelist / Blacklist (ШАГ 7) ──────────────────────────────────────────
# ВНИМАНИЕ: пустой ENABLED_CATEGORY_IDS = НЕ ПАРСИТЬ НИЧЕГО
class DynamicCategoryList(list):
    @property
    def _list(self):
        path = Path(__file__).resolve().parent / "data" / "selected_categories.json"
        if path.exists():
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [int(x) for x in data]
            except Exception:
                pass
        return [47488, 34063, 34066, 34072, 35526]

    def __iter__(self):
        return iter(self._list)

    def __len__(self):
        return len(self._list)

    def __getitem__(self, index):
        return self._list[index]

    def __repr__(self):
        return repr(self._list)

    def __bool__(self):
        return bool(self._list)

    def copy(self):
        return self._list.copy()

ENABLED_CATEGORY_IDS = DynamicCategoryList()

# Продавцы: белый список (пустой = все разрешены). Чёрный список — высокий приоритет.
SELLER_WHITELIST: list[str] = []
SELLER_BLACKLIST: list[str] = []

# Стоп-слова в названии товара → reject с кодом high_risk
PRODUCT_KEYWORD_BLACKLIST: list[str] = []

# ── Экономические параметры ─────────────────────────────────────────────────
# Все экономические параметры теперь управляются через parser/economics.py
# Здесь оставляем только совместимые алиасы для устаревшего кода
MAX_OFFERS_PER_SELLER = int(os.getenv("MAX_OFFERS_PER_SELLER", "3"))
MAX_OFFERS_PER_CATEGORY = int(os.getenv("MAX_OFFERS_PER_CATEGORY", "10"))
TOP_N = int(os.getenv("TOP_N", "100"))
AUTO_DELIVERY_KEYWORDS   = ["auto", "авто", "автовыдача", "24/7", "мгновенно", "instant"]
MANUAL_DELIVERY_KEYWORDS = ["ручн", "вручную", "в течение", "в ответ"]

# Совместимые алиасы для устаревшего кода из parser/economics.py / env
WITHDRAWAL_FEE          = float(os.getenv("WITHDRAWAL_FEE_PCT", "0.02"))
TAX_PCT                 = float(os.getenv("TAX_PCT", "0.0"))
FIXED_COSTS_RUB         = float(os.getenv("FIXED_COSTS_RUB", "0.0"))
PAYMENT_FEE             = float(os.getenv("PAYMENT_FEE_PCT", "0.027"))
RISK_RESERVE            = float(os.getenv("RISK_RESERVE_PCT", "0.05"))
TARGET_NET_MARGIN       = float(os.getenv("TARGET_MARGIN_PCT", "0.20"))
MIN_EXPECTED_PROFIT_RUB = float(os.getenv("MIN_NET_PROFIT_RUB", "50.0"))

# Синонимы/совместимость
TARGET_MARGIN           = TARGET_NET_MARGIN
MIN_NET_PROFIT          = MIN_EXPECTED_PROFIT_RUB

# Путь к файлу кэша категорий
CATEGORIES_CACHE_PATH = os.getenv(
    "CATEGORIES_CACHE_PATH",
    str(Path(__file__).resolve().parent / "categories_cache.json")
)

# Выходные файлы воронки
CANDIDATES_OUTPUT = os.getenv("CANDIDATES_OUTPUT", "data/candidates.json")
TOP100_OUTPUT     = os.getenv("TOP100_OUTPUT",     "data/top100.json")


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════
def dump() -> dict:
    """Возвращает все настройки (для /api/parser/config)."""
    cfg = {
        "antidetect_backend": ANTIDETECT_BACKEND,
        "ggsel": {
            "api_key_set": bool(GGSEL_API_KEY),
            "seller_id": GGSEL_SELLER_ID,
            "base_url": BASE_URL,
            "timeout": HTTP_TIMEOUT,
            "retries": HTTP_RETRIES,
        },
        "server": {"local_port": LOCAL_PORT},
        "msb": {
            "api_base": MSB_API_BASE,
            "token_set": bool(MSB_API_TOKEN),
            "profile_id": MSB_PROFILE_ID or None,
            "headless": MSB_HEADLESS,
            "cookie_ttl": MSB_COOKIE_TTL,
            "profiles_dir": str(MSB_PROFILES_DIR),
        },
        "pool": {
            "max_hits": POOL_MAX_HITS,
            "rest_sec": POOL_REST_SEC,
            "skip_running": POOL_SKIP_RUNNING,
            "profile_ids": PARSER_PROFILE_IDS,
        },
        "rate_limit": {
            "base_delay": RATE_BASE_DELAY,
            "min_delay": RATE_MIN_DELAY,
            "max_delay": RATE_MAX_DELAY,
            "ok_decay": RATE_OK_DECAY,
            "mult_429": RATE_429_MULT,
            "mult_401": RATE_401_MULT,
            "mult_5xx": RATE_5XX_MULT,
            "state_file": RATE_STATE_FILE,
        },
        "telemetry": {
            "enabled": TELEMETRY_ENABLED,
            "dir": TELEMETRY_DIR,
            "max_file_mb": TELEMETRY_MAX_FILE_MB,
        },
        "captcha": {
            "enabled": CAPTCHA_ENABLED,
            "max_retries": CAPTCHA_MAX_RETRIES,
        },
        "parser": {
            "use_msb": PARSER_USE_MSB,
            "max_quantity": PARSER_MAX_QUANTITY,
            "max_pages": PARSER_MAX_PAGES,
        },
        "pipeline": {
            "enabled_category_ids":   ENABLED_CATEGORY_IDS,
            "seller_whitelist":       SELLER_WHITELIST,
            "seller_blacklist":       SELLER_BLACKLIST,
            "keyword_blacklist":      PRODUCT_KEYWORD_BLACKLIST,
            # экономика
            "target_net_margin":       TARGET_NET_MARGIN,
            "min_expected_profit_rub": MIN_EXPECTED_PROFIT_RUB,
            "payment_fee":             PAYMENT_FEE,
            "withdrawal_fee":          WITHDRAWAL_FEE,
            "tax_pct":                 TAX_PCT,
            "risk_reserve":            RISK_RESERVE,
            "fixed_costs_rub":         FIXED_COSTS_RUB,
            # совместимость
            "payment_fee_compat": PAYMENT_FEE,
            "target_margin":      TARGET_MARGIN,
            "min_net_profit":     MIN_NET_PROFIT,
            "max_offers_per_seller": MAX_OFFERS_PER_SELLER,
            "top_n":              TOP_N,
            "categories_cache":   CATEGORIES_CACHE_PATH,
            "candidates_output":  CANDIDATES_OUTPUT,
            "top100_output":      TOP100_OUTPUT,
        },
    }

    # ── Mtime cat_fees.json (для отображения даты последнего обновления комиссий) ──
    import os as _os
    from pathlib import Path as _Path
    _cat_fees_path = _Path(__file__).resolve().parent / "cat_fees.json"
    cfg["cat_fees_updated"] = (
        int(_os.path.getmtime(_cat_fees_path))
        if _cat_fees_path.exists() else None
    )
    return cfg


if __name__ == "__main__":
    import json
    print(json.dumps(dump(), indent=2, ensure_ascii=False))
