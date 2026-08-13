"""
parser/adaptive_rate_limiter.py
===============================
Адаптивный rate limiter, который подстраивает паузу между запросами
по ответу Qrator / WAF. Состояние персистится на диск — после
перезапуска процесса не сбрасывается (чтобы не нарываться на бан
если парсер упал посреди backoff-серии).

Правила (per profile_id):
  - Стартовая пауза:  RATE_BASE_DELAY (default 4.0 сек)
  - 200 OK:           delay *= 0.95   (медленно снижаем к MIN)
  - 429:              delay *= 2.0    (резко вверх, до MAX)
  - 401/403:          delay *= 3.0    + ставим is_problematic=True
  - 5xx:              delay *= 1.5
  - 2xx кроме 200:    без изменений

  delay всегда клампится в [MIN, MAX] = [2.0, 60.0].

Использование:
    from parser.adaptive_rate_limiter import AdaptiveRateLimiter
    limiter = AdaptiveRateLimiter(storage_path="data/rate_state.json")

    # Перед запросом:
    limiter.wait(profile_id)
    resp = await session.get(url)

    # После получения ответа:
    limiter.record(profile_id, resp.status_code)

Потокобезопасно: внутренний lock + атомарная запись на диск.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("ggselv7.rate_limiter")


@dataclass
class _ProfileState:
    """Состояние одного профиля. Персистится целиком."""
    delay: float = 4.0
    consecutive_ok: int = 0
    consecutive_errors: int = 0
    is_problematic: bool = False
    last_status: int = 0
    last_updated: float = 0.0
    total_requests: int = 0


class AdaptiveRateLimiter:
    """
    Адаптивный rate limiter с per-profile состоянием и persist на диск.

    Parameters
    ----------
    storage_path : str | Path
        Путь к JSON файлу с состоянием. Если файла нет — создаст при первом save.
        Относительные пути резолвятся относительно CWD.
    base_delay : float
        Стартовая пауза (RATE_BASE_DELAY, default 4.0)
    min_delay, max_delay : float
        Клампинг паузы (default 2.0 / 60.0)
    ok_decay : float
        Множитель на 200 OK (default 0.95 — медленно снижаем)
    mult_429, mult_401, mult_5xx : float
        Множители на соответствующие статусы
    save_debounce_sec : float
        Минимальный интервал между записями на диск (default 1.0 сек).
        Меньше IO, при этом crash-recovery всё равно потеряет максимум save_debounce_sec.
    """

    def __init__(
        self,
        storage_path: str | Path = "data/rate_state.json",
        base_delay: float = 4.0,
        min_delay: float = 2.0,
        max_delay: float = 60.0,
        ok_decay: float = 0.95,
        mult_429: float = 2.0,
        mult_401: float = 3.0,
        mult_5xx: float = 1.5,
        save_debounce_sec: float = 1.0,
    ):
        self._storage = Path(storage_path)
        self._base = float(base_delay)
        self._min = float(min_delay)
        self._max = float(max_delay)
        self._ok_decay = float(ok_decay)
        self._mult_429 = float(mult_429)
        self._mult_401 = float(mult_401)
        self._mult_5xx = float(mult_5xx)
        self._save_debounce = float(save_debounce_sec)

        self._state: Dict[str, _ProfileState] = {}
        self._lock = threading.Lock()
        self._last_saved_at: float = 0.0

        self._load_from_disk()

    # ── Публичный API ─────────────────────────────────────────────────────

    def wait(self, profile_id: str) -> float:
        """
        Спит нужное количество секунд для данного профиля.
        Возвращает фактически задержанное время (для логов/телеметрии).

        Если профиля нет в state — берёт базовую паузу.
        """
        delay = self._get_delay(profile_id)
        if delay > 0:
            time.sleep(delay)
        return delay

    def record(self, profile_id: str, status: int) -> _ProfileState:
        """
        Записывает результат запроса и пересчитывает delay.
        Возвращает обновлённое состояние.
        """
        with self._lock:
            s = self._state.get(profile_id)
            if s is None:
                s = _ProfileState(delay=self._base)
                self._state[profile_id] = s
            self._apply_status(s, int(status))
            s.total_requests += 1
            s.last_updated = time.time()
            # clamp
            s.delay = max(self._min, min(self._max, s.delay))
        self._save_to_disk_debounced()
        return s

    def get_state(self, profile_id: str) -> dict:
        """Возвращает snapshot состояния (для API/логов)."""
        with self._lock:
            s = self._state.get(profile_id)
            if s is None:
                s = _ProfileState(delay=self._base)
            return {
                "profile_id": profile_id,
                "delay": round(s.delay, 2),
                "consecutive_ok": s.consecutive_ok,
                "consecutive_errors": s.consecutive_errors,
                "is_problematic": s.is_problematic,
                "last_status": s.last_status,
                "last_updated": s.last_updated,
                "total_requests": s.total_requests,
            }

    def summary(self) -> dict:
        """Сводка по всем профилям (для /api/parser/msb/status)."""
        with self._lock:
            return {
                "base_delay": self._base,
                "min_delay": self._min,
                "max_delay": self._max,
                "ok_decay": self._ok_decay,
                "mult_429": self._mult_429,
                "mult_401": self._mult_401,
                "mult_5xx": self._mult_5xx,
                "profiles": {
                    pid: {
                        "delay": round(s.delay, 2),
                        "consecutive_ok": s.consecutive_ok,
                        "consecutive_errors": s.consecutive_errors,
                        "is_problematic": s.is_problematic,
                        "last_status": s.last_status,
                        "total_requests": s.total_requests,
                    }
                    for pid, s in self._state.items()
                },
            }

    def reset(self, profile_id: Optional[str] = None) -> None:
        """Сбрасывает состояние: всех профилей или одного."""
        with self._lock:
            if profile_id:
                self._state.pop(profile_id, None)
            else:
                self._state.clear()
        self._save_to_disk(force=True)

    def force_save(self) -> None:
        """Принудительно сохранить state на диск (вызывать при graceful shutdown)."""
        self._save_to_disk(force=True)

    # ── Внутренние ────────────────────────────────────────────────────────

    def _get_delay(self, profile_id: str) -> float:
        with self._lock:
            s = self._state.get(profile_id)
            return s.delay if s else self._base

    def _apply_status(self, s: _ProfileState, status: int) -> None:
        """Применяет правила к состоянию. Без клампинга — это делает caller."""
        s.last_status = status
        if 200 <= status < 300:
            s.delay *= self._ok_decay
            s.consecutive_ok += 1
            s.consecutive_errors = 0
            s.is_problematic = False
        elif status == 429:
            s.delay *= self._mult_429
            s.consecutive_ok = 0
            s.consecutive_errors += 1
        elif status in (401, 403):
            s.delay *= self._mult_401
            s.consecutive_ok = 0
            s.consecutive_errors += 1
            s.is_problematic = True
        elif 500 <= status < 600:
            s.delay *= self._mult_5xx
            s.consecutive_ok = 0
            s.consecutive_errors += 1
        # 2xx кроме 200 (201, 204...) и прочее — без изменений delay,
        # но last_status всё равно записан

    def _load_from_disk(self) -> None:
        """Грузит state с диска если файл есть. Ошибки — игнорируем."""
        try:
            if not self._storage.exists():
                return
            raw = json.loads(self._storage.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            loaded = 0
            for pid, d in raw.items():
                if not isinstance(d, dict):
                    continue
                # Безопасный парсинг: только известные поля
                self._state[pid] = _ProfileState(
                    delay=float(d.get("delay", self._base)),
                    consecutive_ok=int(d.get("consecutive_ok", 0)),
                    consecutive_errors=int(d.get("consecutive_errors", 0)),
                    is_problematic=bool(d.get("is_problematic", False)),
                    last_status=int(d.get("last_status", 0)),
                    last_updated=float(d.get("last_updated", 0.0)),
                    total_requests=int(d.get("total_requests", 0)),
                )
                loaded += 1
            logger.info("rate_limiter: загружено %d профилей из %s", loaded, self._storage)
        except Exception as e:
            logger.warning("rate_limiter: не удалось загрузить state из %s: %s", self._storage, e)

    def _save_to_disk_debounced(self) -> None:
        """Сохраняет на диск не чаще раза в save_debounce сек."""
        now = time.time()
        if now - self._last_saved_at < self._save_debounce:
            return
        self._save_to_disk(force=True)

    def _save_to_disk(self, force: bool = False) -> None:
        """Атомарная запись state на диск (через .tmp + replace)."""
        try:
            with self._lock:
                data = {pid: asdict(s) for pid, s in self._state.items()}
            self._storage.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._storage.with_suffix(f".{os.getpid()}.{int(time.time() * 1000)}.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._storage)
            self._last_saved_at = time.time()
        except Exception as e:
            logger.warning("rate_limiter: не удалось сохранить state: %s", e)
            try:
                tmp.unlink(missing_ok=True)  # type: ignore[name-defined]
            except Exception:
                pass


# ── Синглтон (по желанию, не обязателен) ─────────────────────────────────
_limiter_singleton: Optional[AdaptiveRateLimiter] = None
_limiter_lock = threading.Lock()


def get_limiter(storage_path: str | Path = "data/rate_state.json") -> AdaptiveRateLimiter:
    """
    Возвращает глобальный синглтон лимитера.
    Создаётся при первом обращении.
    """
    global _limiter_singleton
    if _limiter_singleton is None:
        with _limiter_lock:
            if _limiter_singleton is None:
                _limiter_singleton = AdaptiveRateLimiter(storage_path=storage_path)
    return _limiter_singleton
