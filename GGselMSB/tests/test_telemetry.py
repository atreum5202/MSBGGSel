"""
Тесты Telemetry.
Запуск: python -m pytest tests/test_telemetry.py -v
"""
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.telemetry import Telemetry  # noqa: E402


def test_emit_creates_file():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        t.emit("parser.start", query="games", quantity=20)
        files = list(Path(td).glob("*.jsonl"))
        assert len(files) == 1, f"должен быть 1 файл, получили {len(files)}"
        content = files[0].read_text(encoding="utf-8").strip()
        assert content, "файл не пустой"
        record = json.loads(content)
        assert record["event"] == "parser.start"
        assert record["query"] == "games"
        assert record["quantity"] == 20
        assert "ts" in record
        assert "epoch" in record
        print(f"  ✓ emit создал файл {files[0].name} с 1 записью")


def test_emit_appends():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        for i in range(5):
            t.emit("parser.page_fetched", profile_id=f"p{i}", status=200, latency_ms=100 + i)
        files = list(Path(td).glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5, f"ожидалось 5 строк, получили {len(lines)}"
        # Каждая строка — валидный JSON
        for line in lines:
            r = json.loads(line)
            assert r["event"] == "parser.page_fetched"
        print(f"  ✓ 5 emit'ов → 5 строк в JSONL")


def test_emit_disabled_noop():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=False)
        result = t.emit("parser.start", query="x")
        assert result is None
        files = list(Path(td).glob("*.jsonl"))
        assert len(files) == 0, "при disabled файлы не создаются"
        print("  ✓ disabled → no file, return None")


def test_emit_records_return_value():
    """emit() возвращает dict события (для тестов)."""
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        record = t.emit("parser.error", error_type="NetworkError", detail="connection refused")
        assert record is not None
        assert record["event"] == "parser.error"
        assert record["error_type"] == "NetworkError"
        assert "ts" in record
        print("  ✓ emit возвращает dict записи")


def test_emit_sanitizes_cookies():
    """Значения cookies не пишутся в файл (security)."""
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        t.emit("parser.cookies_refreshed",
               profile_id="abc",
               cookies={"__qrator_jsid": "SECRET_VALUE", "session": "ANOTHER_SECRET"},
               source="msb")
        files = list(Path(td).glob("*.jsonl"))
        content = files[0].read_text(encoding="utf-8")
        assert "SECRET_VALUE" not in content, "значения cookies НЕ ДОЛЖНЫ попасть в лог!"
        assert "ANOTHER_SECRET" not in content
        # Но метаданные должны быть
        record = json.loads(content.strip())
        assert "cookies_count" in record
        assert "cookies_names" in record
        assert "__qrator_jsid" in record["cookies_names"]
        assert "session" in record["cookies_names"]
        print("  ✓ cookies sanitized: values НЕ записаны, только имена+количество")


def test_rotation_by_date():
    """Ротация при смене даты (имитация через подмену _today)."""
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        t.emit("parser.start", query="a")
        # Подменяем дату — должно сработать rotate
        t._current_date = "2020-01-01"
        t.emit("parser.start", query="b")
        files = sorted(Path(td).glob("*.jsonl"))
        # Должно быть 2 файла: events.jsonl (новый) и events-2020-01-01.jsonl (старый)
        assert len(files) == 2, f"ожидалось 2 файла, получили {[f.name for f in files]}"
        names = {f.name for f in files}
        assert "events.jsonl" in names
        assert "events-2020-01-01.jsonl" in names
        print(f"  ✓ rotation by date: {names}")


def test_read_recent():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        for i in range(20):
            t.emit("parser.product_saved", category="games", ai_enriched=True, took_ms=100)
        recent = t.read_recent(limit=5)
        assert len(recent) == 5, f"ожидалось 5 последних, получили {len(recent)}"
        # Все должны быть product_saved
        for r in recent:
            assert r["event"] == "parser.product_saved"
        # ts должен быть ISO формата
        assert "Z" in recent[0]["ts"]
        print(f"  ✓ read_recent(5) → 5 событий, все product_saved")


def test_read_recent_empty():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        assert t.read_recent() == []
        t.disable()
        assert t.read_recent() == []
        print("  ✓ read_recent на пустой/disabled: []")


def test_stats():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        t.emit("parser.start", x=1)
        t.emit("parser.run_complete", x=2)
        s = t.stats()
        assert s["enabled"] is True
        assert s["current_file"] == "events.jsonl"
        assert s["current_size_bytes"] > 0
        assert s["total_files"] == 1
        assert s["total_size_bytes"] > 0
        print(f"  ✓ stats: {s}")


def test_thread_safety():
    """100 emit'ов из 5 потоков = 100 строк, ни одна не потерялась."""
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        errors = []

        def worker(prefix):
            try:
                for i in range(20):
                    t.emit("parser.page_fetched", profile_id=f"{prefix}-{i}", status=200)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors
        files = list(Path(td).glob("*.jsonl"))
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 100, f"ожидалось 100, получили {len(lines)}"
        print(f"  ✓ 5 потоков x 20 emit = 100 строк в JSONL ✓")


def test_disable_enable():
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        t.emit("parser.start", x=1)
        t.disable()
        t.emit("parser.error", x=2)
        t.enable()
        t.emit("parser.run_complete", x=3)
        files = list(Path(td).glob("*.jsonl"))
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        # Между disable и enable ничего не записано
        assert len(lines) == 2, f"ожидалось 2 (start + complete), получили {len(lines)}"
        events = [json.loads(l)["event"] for l in lines]
        assert "parser.start" in events
        assert "parser.error" not in events
        assert "parser.run_complete" in events
        print(f"  ✓ disable/enable: events={events}")


def test_known_events_warning():
    """Неизвестное событие не валит, просто debug-лог."""
    with _tmpdir() as td:
        t = Telemetry(storage_dir=td, enabled=True)
        result = t.emit("custom.event", foo="bar")  # не в KNOWN_EVENTS
        assert result is not None
        assert result["event"] == "custom.event"
        files = list(Path(td).glob("*.jsonl"))
        assert len(lines := files[0].read_text(encoding="utf-8").strip().splitlines()) == 1
        print("  ✓ unknown event: не падает, просто лог debug")


# ── Утилиты ───────────────────────────────────────────────────────────────

import contextlib
import tempfile


@contextlib.contextmanager
def _tmpdir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Running Telemetry tests (plain mode)")
    print("=" * 60)
    tests = [
        test_emit_creates_file,
        test_emit_appends,
        test_emit_disabled_noop,
        test_emit_records_return_value,
        test_emit_sanitizes_cookies,
        test_rotation_by_date,
        test_read_recent,
        test_read_recent_empty,
        test_stats,
        test_thread_safety,
        test_disable_enable,
        test_known_events_warning,
    ]
    failed = 0
    for t in tests:
        print(f"\n[TEST] {t.__name__}")
        try:
            t()
            print("  PASS")
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            failed += 1
    print()
    print("=" * 60)
    if failed == 0:
        print(f"All {len(tests)} tests PASSED")
        return 0
    print(f"{failed} of {len(tests)} tests FAILED")
    return 1


if __name__ == "__main__":
    try:
        import pytest
        rc = pytest.main([__file__, "-v", "--tb=short"])
        sys.exit(rc)
    except ImportError:
        sys.exit(main())
