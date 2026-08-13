"""
parser/scheduler.py
===================
Мигрировано из GGSeller/services/parser/scheduler.py (2026-07-27).

Планировщик автозапуска CompetitorScanner.

Логика:
  - Работает только в окне SCHEDULE_START..SCHEDULE_END
  - На каждом тике: scan_category("games"/"keys"/"software")
  - На Qrator-challenge — экспоненциальный backoff
  - Раз в SOURCE_CHECK_INTERVAL_HOURS — проверяет доступность источников
    (через competitor_scanner.check_source_availability)
  - Шлёт TG-уведомления при проблемах

Запуск:
    python -m parser.scheduler
    # или в фоне через watchdog.py (V7 корень)

ENV (в .env в корне V7):
  PARSER_SCHEDULE_START, PARSER_SCHEDULE_END    — окно работы (default 09:00-22:00)
  PARSER_DELAY_MIN, PARSER_DELAY_MAX            — пауза между тиками (default 5-10 сек)
  PARSER_MAX_RPH                                — защита от перерасхода (default 200)
  PARSER_CATEGORY_DELAY_MIN/MAX                 — пауза между категориями (default 2-5)
  PARSER_CHALLENGE_BACKOFF                      — стартовый backoff на Qrator (default 60s)
  SOURCE_CHECK_INTERVAL_HOURS                   — интервал проверки источников (default 6)
  COMPETITOR_DB_PATH                            — путь к БД (default shared/db/ggsel.db)
"""
import os
import sys
import time
import random
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# V7 структура
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

# V7: tg_bot и parser modules
try:
    from .tg_bot import send_notification_sync as _tg
except Exception:
    def _tg(msg, product_id=None):  # type: ignore
        log.debug("[tg] no tg_bot available: %s", msg[:80])

try:
    from .db_init import init_db
except Exception:
    def init_db():  # type: ignore
        log.warning("[init_db] parser.db_init недоступен")

try:
    from .competitor_scanner import CompetitorScanner, check_source_availability
    _PARSER_OK = True
    _PARSER_IMPORT_ERROR = None
except Exception as _e:
    _PARSER_OK = False
    _PARSER_IMPORT_ERROR = _e
    CompetitorScanner = None  # type: ignore
    check_source_availability = None  # type: ignore


log = logging.getLogger("ggselv7.scheduler")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(h)


# ── Configuration ────────────────────────────────────────────────
SCHEDULE_START = os.getenv("PARSER_SCHEDULE_START", "09:00")
SCHEDULE_END   = os.getenv("PARSER_SCHEDULE_END",   "22:00")
DELAY_MIN      = float(os.getenv("PARSER_DELAY_MIN", "5.0"))
DELAY_MAX      = float(os.getenv("PARSER_DELAY_MAX", "10.0"))
MAX_RPH        = int(os.getenv("PARSER_MAX_RPH", "200"))
DB_PATH        = os.getenv("COMPETITOR_DB_PATH", "shared/db/ggsel.db")

CATEGORY_DELAY_MIN = float(os.getenv("PARSER_CATEGORY_DELAY_MIN", "2.0"))
CATEGORY_DELAY_MAX = float(os.getenv("PARSER_CATEGORY_DELAY_MAX", "5.0"))

CHALLENGE_BACKOFF_BASE = float(os.getenv("PARSER_CHALLENGE_BACKOFF", "60.0"))

# Синглтон сканера
_scanner = None
_fetcher = None


def is_within_schedule() -> bool:
    now = datetime.now()
    try:
        start = datetime.strptime(SCHEDULE_START, "%H:%M").time()
        end   = datetime.strptime(SCHEDULE_END,   "%H:%M").time()
    except ValueError:
        return True
    if start <= end:
        return start <= now.time() <= end
    else:  # Crosses midnight
        return start <= now.time() or now.time() <= end


def _get_scanner():
    """Возвращает синглтон CompetitorScanner, создавая при первом вызове."""
    global _scanner, _fetcher
    if not _PARSER_OK:
        return None

    if _scanner is not None:
        if getattr(_scanner, "_is_running", False):
            log.warning("scheduler: _is_running=True у синглтона — сбрасываю флаг")
            _scanner._is_running = False
        return _scanner

    # V7 CascadeFetcher — берём из competitor_scanner (он импортирует из parser_engine)
    try:
        from .parser_engine import CascadeFetcher
        _fetcher = CascadeFetcher()
    except Exception as e:
        log.warning("Не удалось создать CascadeFetcher: %s", e)
        return None

    _scanner = CompetitorScanner(fetcher=_fetcher, max_pages=3)
    return _scanner


def run_one_scan() -> tuple:
    """
    Один проход парсера.
    Returns:
        (количество найденных товаров, был_ли_challenge)
    """
    if not _PARSER_OK:
        log.error("Парсер не загружен: %s", _PARSER_IMPORT_ERROR)
        return 0, False

    scanner = _get_scanner()
    if scanner is None:
        return 0, False

    if getattr(scanner, "_is_running", False):
        log.warning("scheduler: сканер уже запущен, пропускаю тик")
        return 0, False

    products = []
    had_challenge = False

    try:
        for cat in ("games", "keys", "software"):
            try:
                res = scanner.scan_category(cat)
                if res:
                    products.extend(res)
                time.sleep(random.uniform(CATEGORY_DELAY_MIN, CATEGORY_DELAY_MAX))
            except Exception as e:
                err_str = str(e).lower()
                if any(kw in err_str for kw in ("challenge", "qrator", "403", "blocked", "captcha")):
                    log.warning("scheduler: Qrator-challenge на категории %s: %s", cat, e)
                    had_challenge = True
                    break
                log.debug("Category %s failed: %s", cat, e)
    except AttributeError:
        # scan_category не существует — fallback
        try:
            products = scanner.scan_url("https://ggsel.net/") or []
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ("challenge", "qrator", "403", "blocked", "captcha")):
                had_challenge = True
            else:
                log.debug("scan_url failed: %s", e)
    finally:
        if hasattr(scanner, "_is_running"):
            scanner._is_running = False

    log.info("Scan finished: %d products, challenge=%s", len(products), had_challenge)
    _maybe_check_sources()
    return len(products), had_challenge


# Время последней проверки источников
_last_source_check: datetime = datetime.min


def _maybe_check_sources() -> None:
    """Проверяет доступность источников у товаров, которые давно не проверялись."""
    global _last_source_check
    if check_source_availability is None:
        return
    interval_hours = float(os.getenv("SOURCE_CHECK_INTERVAL_HOURS", "6"))
    if (datetime.utcnow() - _last_source_check).total_seconds() < interval_hours * 3600:
        return
    _last_source_check = datetime.utcnow()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT product_id, title, url FROM parsed_products "
            "WHERE status NOT IN ('rejected','timeout') AND in_stock=1 "
            "AND (last_parsed_at IS NULL OR last_parsed_at < datetime('now','-6 hours')) "
            "LIMIT 50"
        ).fetchall()
        conn.close()
    except Exception as e:
        log.warning("[source_check] DB ошибка: %s", e)
        return

    for row in rows:
        try:
            import asyncio
            available = asyncio.run(check_source_availability(row["product_id"], row["url"]))
            if not available:
                log.warning("[source_check] Недоступен: %s", row["url"])
                try:
                    _tg(f"⚠️ Источник недоступен: {row['title']}\n{row['url']}\nТовар скрыт")
                except Exception:
                    pass
        except Exception as e:
            log.debug("[source_check] %s: %s", row["product_id"], e)


def run_scheduler():
    init_db()
    log.info("Starting parser scheduler...")

    if not _PARSER_OK:
        log.warning(
            "Парсер не загружен — планировщик будет только логировать тики. Ошибка: %s",
            _PARSER_IMPORT_ERROR,
        )

    challenge_backoff = 0.0

    while True:
        if not is_within_schedule():
            log.info("Outside scheduled hours (%s-%s). Sleeping 5 min...", SCHEDULE_START, SCHEDULE_END)
            time.sleep(300)
            challenge_backoff = 0.0
            continue

        if challenge_backoff > 0:
            log.info("scheduler: ждём %.0fs после Qrator-challenge...", challenge_backoff)
            time.sleep(challenge_backoff)
            challenge_backoff = 0.0

        try:
            n, had_challenge = run_one_scan()
            if had_challenge:
                challenge_backoff = min(CHALLENGE_BACKOFF_BASE * 2, 1800.0)
                log.warning("scheduler: challenge — следующий тик через %.0fs", challenge_backoff)
                try:
                    _tg(f"⚠️ Qrator-challenge при сканировании. Пауза {challenge_backoff:.0f}s.")
                except Exception:
                    pass
            else:
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                log.info("Tick done: %d products, next in %.1fs", n, delay)
                time.sleep(delay)
        except Exception as e:
            log.error("Scheduler error: %s", e)
            try:
                _tg(f"⚠️ Ошибка в планировщике парсера: {e}")
            except Exception:
                pass
            time.sleep(60)


if __name__ == "__main__":
    run_scheduler()
