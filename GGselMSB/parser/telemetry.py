"""
parser/telemetry.py
===================
Локальная телеметрия парсера. Append-only JSONL файл.

ВАЖНО: ничего не отправляется наружу. Только локальный файл на диске.
События можно потом анализировать через pandas/jq на JSONL.

События:
  parser.start            {query, category, quantity, profile_count}
  parser.page_fetched     {profile_id, status, latency_ms, rate_delay, is_challenge, captcha}
  parser.cookies_refreshed{profile_id, source: snapshot|api|msb|manual, took_ms, cookies_count}
  parser.profile_rested   {profile_id, reason: hits|401|429|captcha, duration_sec}
  parser.product_saved    {category, ai_enriched, took_ms}
  parser.error            {error_type, detail, profile_id?}
  parser.run_complete     {total_products, total_pages, duration_sec, status}
  parser.fallback         {reason, used: CffiFetcher}

Файлы:
  data/telemetry/events.jsonl                  — текущий (по умолчанию)
  data/telemetry/events-2026-07-25.jsonl       — за вчера (ротация)
  data/telemetry/events-2026-07-24.jsonl       — за позавчера
  ...

Потокобезопасно. События сериализуются как одна JSON-линия.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ggselv7.telemetry")


class Telemetry:
    """
    Append-only локальная телеметрия.

    Использование:
        t = Telemetry(storage_dir="data/telemetry")
        t.emit("parser.start", query="games", quantity=20)
        # ... позже ...
        t.emit("parser.run_complete", total_products=18, total_pages=3)

        # На graceful shutdown:
        t.flush()
    """

    # Известные события (для подсказок в логах, не валидация)
    KNOWN_EVENTS = {
        "parser.start",
        "parser.page_fetched",
        "parser.cookies_refreshed",
        "parser.profile_rested",
        "parser.product_saved",
        "parser.error",
        "parser.run_complete",
        "parser.fallback",
        "parser.captcha_detected",
        "parser.captcha_solved",
        "parser.rate_limited",
        # Внутренние
        "_internal.init",
        "_internal.flush",
    }

    def __init__(
        self,
        storage_dir: str | Path = "data/telemetry",
        enabled: bool = True,
        max_file_size_mb: float = 50.0,
    ):
        """
        Args:
            storage_dir: директория для JSONL файлов
            enabled: если False — emit() ничего не делает (быстрое отключение)
            max_file_size_mb: порог для ротации (default 50MB)
        """
        self._dir = Path(storage_dir)
        self._enabled = enabled
        self._max_bytes = int(max_file_size_mb * 1024 * 1024)

        self._lock = threading.Lock()
        self._current_file: Optional[Path] = None
        self._current_date: Optional[str] = None
        self._buffered_events: int = 0
        self._flush_every: int = 1  # flush после каждого события (для crash-recovery)

        if self._enabled:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._current_file = self._path_for_date(self._today())
            self._current_date = self._today()
            logger.info("telemetry: dir=%s, current=%s", self._dir, self._current_file.name)
        else:
            logger.info("telemetry: disabled (emit() будет no-op)")

    # ── Публичный API ─────────────────────────────────────────────────────

    def emit(self, event: str, **data: Any) -> Optional[Dict[str, Any]]:
        """
        Записать событие. data сериализуется в JSON, добавляется timestamp.

        Returns:
            Словарь события (полезно для тестов/логов), или None если disabled.
        """
        if not self._enabled:
            return None

        if event not in self.KNOWN_EVENTS:
            # Не валимся на неизвестных событиях, только лог debug
            logger.debug("telemetry: emit неизвестного события '%s'", event)

        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "epoch": time.time(),
            "event": event,
            **self._sanitize(data),
        }

        with self._lock:
            try:
                # Ротация: дата сменилась ИЛИ файл перерос
                today = self._today()
                if today != self._current_date:
                    self._rotate(today)
                elif self._max_bytes and self._current_file and self._current_file.exists():
                    if self._current_file.stat().st_size > self._max_bytes:
                        self._rotate(today, size_based=True)

                # Append
                with open(self._current_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

                self._buffered_events += 1
                if self._buffered_events >= self._flush_every:
                    self._buffered_events = 0
                    # file.close() уже сделал flush, но fsync — overkill для телеметрии
            except Exception as e:
                logger.warning("telemetry: не удалось записать '%s': %s", event, e)
                return None
        return record

    def flush(self) -> None:
        """Принудительный flush. На POSIX-файлах append+close уже синхронен,
        но оставляем для совместимости с будущими бэкендами."""
        with self._lock:
            self._buffered_events = 0
        logger.debug("telemetry: flush()")

    def read_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Возвращает последние N событий из текущего файла.
        Полезно для /api/parser/telemetry/recent эндпоинта.
        """
        if not self._enabled or not self._current_file or not self._current_file.exists():
            return []
        try:
            with open(self._current_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            recent = lines[-limit:] if limit and limit > 0 else lines
            out = []
            for line in recent:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out
        except Exception as e:
            logger.warning("telemetry: не удалось прочитать recent: %s", e)
            return []

    def stats(self) -> Dict[str, Any]:
        """Сводка по телеметрии: размер текущего файла, кол-во файлов, etc."""
        if not self._enabled:
            return {"enabled": False}
        out = {
            "enabled": True,
            "storage_dir": str(self._dir),
            "current_file": self._current_file.name if self._current_file else None,
            "max_file_size_mb": self._max_bytes / 1024 / 1024,
        }
        try:
            files = sorted(self._dir.glob("events*.jsonl"))
            out["total_files"] = len(files)
            out["total_size_bytes"] = sum(f.stat().st_size for f in files)
            if self._current_file and self._current_file.exists():
                out["current_size_bytes"] = self._current_file.stat().st_size
        except Exception:
            pass
        return out

    def disable(self) -> None:
        """Отключить (emit() станет no-op)."""
        self._enabled = False

    def enable(self) -> None:
        """Включить обратно."""
        if self._enabled:
            return
        self._enabled = True
        self._dir.mkdir(parents=True, exist_ok=True)
        self._current_file = self._path_for_date(self._today())
        self._current_date = self._today()

    # ── Внутренние ────────────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        """YYYY-MM-DD (UTC)."""
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _path_for_date(self, date_str: str) -> Path:
        if date_str == self._today():
            return self._dir / "events.jsonl"
        return self._dir / f"events-{date_str}.jsonl"

    def _rotate(self, new_date: str, size_based: bool = False) -> None:
        """Ротация файла. Закрываем текущий, открываем новый."""
        if self._current_file and self._current_file.exists():
            old_size = self._current_file.stat().st_size
            if size_based:
                # По размеру — переименовываем в events-YYYY-MM-DD-HHMMSS.jsonl
                ts = datetime.utcnow().strftime("%H%M%S")
                new_name = f"events-{self._current_date}-{ts}.jsonl"
            else:
                # По дате — events-YYYY-MM-DD.jsonl
                new_name = f"events-{self._current_date}.jsonl"
            new_path = self._dir / new_name
            try:
                if not new_path.exists():
                    self._current_file.rename(new_path)
                    logger.info("telemetry: rotated → %s (%d bytes)", new_name, old_size)
                else:
                    # Файл с этой датой уже есть — дописываем в него
                    with open(new_path, "a", encoding="utf-8") as dst, open(self._current_file, "r", encoding="utf-8") as src:
                        dst.write(src.read())
                    self._current_file.unlink()
                    logger.info("telemetry: merged into %s", new_name)
            except Exception as e:
                logger.warning("telemetry: rotation failed: %s", e)

        self._current_date = new_date
        self._current_file = self._path_for_date(new_date)

    @staticmethod
    def _sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Убираем из data значения которые не сериализуются в JSON.
        Не логируем значения cookies (только имена и длины) — это в security checklist.
        """
        out: Dict[str, Any] = {}
        for k, v in data.items():
            if k in ("cookies", "cookie_values", "snapshot"):
                # Безопасный whitelist: только метаданные
                if isinstance(v, dict):
                    out[f"{k}_count"] = len(v)
                    out[f"{k}_names"] = list(v.keys())[:20]
                elif isinstance(v, list):
                    out[f"{k}_count"] = len(v)
                continue
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                out[k] = v
            else:
                out[k] = str(v)
        return out


# ── Синглтон ──────────────────────────────────────────────────────────────
_telemetry: Optional[Telemetry] = None
_telemetry_lock = threading.Lock()


def get_telemetry(storage_dir: str | Path = "data/telemetry") -> Telemetry:
    """Глобальный синглтон. Создаётся при первом обращении."""
    global _telemetry
    if _telemetry is None:
        with _telemetry_lock:
            if _telemetry is None:
                _telemetry = Telemetry(storage_dir=storage_dir)
    return _telemetry
