"""
parser/
=======
Изолированный пакет парсера для GGselMSB.

Данные проекта лежат в data/:
  - data/db/parser.db      — SQLite хранилище (persistent между запусками)
  - data/logs/parser.log   — лог
  - data/images/generated/ — AI-сгенерированные картинки

Точка входа для Flask:
    from parser import get_engine
    engine = get_engine()
    engine.start(query=..., category=..., quantity=...)

Безопасность:
  - Парсер НЕ запускается сам — только через engine.start() (GUI)
  - Bounded quantity: hard cap 100 товаров за запуск
  - Bounded pages:    hard cap 10 страниц
  - Single worker:    один поток, никаких параллельных запросов
  - Rate limit:       4-6 сек между страницами + exponential backoff на 429
  - Qrator-safe:      curl-cffi с TLS fingerprint Chrome + 30 сек пауза на challenge

Ключевые модули:
  - msb_client:         MsbClient — управление профилями CloakBrowser через MSB API
  - msb_cookies:        экспорт/импорт cookie из MSB-профилей
  - morelogin_gemini:   автоматизация gemini.google.com через CDP (рестайл изображений)
  - cdp_cookies:        низкоуровневая работа с cookie через Chrome DevTools Protocol

Дополнительные модули (опциональные):
  - competitor_scanner: CompetitorScanner (scan_url/category/seller/all_categories)
  - my_shop_scraper:    MyShopScraper (seller API: offers, orders, reviews)
  - profile_warmer:     MSB+ClaudeVision нагул профиля (дополняет warm_profiles)
  - scheduler:          автозапуск CompetitorScanner по расписанию
"""
from .parser_engine import (
    ParserEngine,
    get_engine,
    BASE_URL,
    KNOWN_CATEGORIES,
    MAX_QUANTITY_HARD_CAP,
)
from .db_init import init_db, get_db_path, import_categories_from_json

# ── Дополнительные модули (миграция из GGSeller) — опциональный экспорт ──
# Не падаем если модуль не загрузился (например, отсутствует apscheduler).
import importlib as _importlib
_optional_imports = {
    "CompetitorScanner":  ("competitor_scanner", "CompetitorScanner"),
    "MyShopScraper":      ("my_shop_scraper",    "MyShopScraper"),
    "warm_profile":       ("profile_warmer",     "warm_profile"),
    "get_warm_status":    ("profile_warmer",     "get_warm_status"),
    "configure_profile_network": ("profile_warmer", "configure_profile_network"),
    "run_scheduler":      ("scheduler",          "run_scheduler"),
    "is_within_schedule": ("scheduler",          "is_within_schedule"),
    "run_one_scan":       ("scheduler",          "run_one_scan"),
}
for _name, (_mod, _attr) in _optional_imports.items():
    try:
        _mod_obj = _importlib.import_module(f".{_mod}", package=__name__)
        globals()[_name] = getattr(_mod_obj, _attr)
    except Exception as _e:
        # Не критично — модуль может требовать внешних зависимостей (httpx, apscheduler)
        import logging as _log
        _log.getLogger("parser").debug(
            "Optional import %s.%s failed: %s", _mod, _attr, _e,
        )
        globals()[_name] = None

# убираем временные переменные из namespace (только те, что реально есть)
for _tmp in ("_importlib", "_optional_imports", "_name", "_mod", "_attr", "_mod_obj", "_log", "_e"):
    if _tmp in globals():
        del globals()[_tmp]

__all__ = [
    "ParserEngine",
    "get_engine",
    "init_db",
    "get_db_path",
    "import_categories_from_json",
    "BASE_URL",
    "KNOWN_CATEGORIES",
    "MAX_QUANTITY_HARD_CAP",
    # Мигрированные из GGSeller (могут быть None если зависимости не установлены):
    "CompetitorScanner",
    "MyShopScraper",
    "warm_profile",
    "get_warm_status",
    "configure_profile_network",
    "run_scheduler",
    "is_within_schedule",
    "run_one_scan",
]
