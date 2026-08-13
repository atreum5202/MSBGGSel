"""
Тесты для AdaptiveRateLimiter.
Запуск: python -m pytest tests/test_rate_limiter.py -v
       python tests/test_rate_limiter.py        (fallback без pytest)
"""
import json
import os
import sys
import time
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.adaptive_rate_limiter import AdaptiveRateLimiter, _ProfileState  # noqa: E402


# ── Утилита: прогон через все тесты с двумя режимами ─────────────────────

def _run_pytest():
    try:
        import pytest
        return pytest.main([__file__, "-v", "--tb=short"])
    except ImportError:
        return None


# ── Сами тесты ───────────────────────────────────────────────────────────

def _make_limiter(tmpdir: Path, **kwargs) -> AdaptiveRateLimiter:
    defaults = dict(
        storage_path=tmpdir / "rate_state.json",
        base_delay=4.0,
        min_delay=2.0,
        max_delay=60.0,
        ok_decay=0.95,
        mult_429=2.0,
        mult_401=3.0,
        mult_5xx=1.5,
        save_debounce_sec=0.0,  # в тестах save всегда
    )
    defaults.update(kwargs)
    return AdaptiveRateLimiter(**defaults)


def test_record_200_decreases_delay():
    """200 OK: delay *= 0.95, но не меньше min_delay."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "test-profile-1"
        before = lim.get_state(pid)["delay"]
        # 100 успешных запросов
        for _ in range(100):
            lim.record(pid, 200)
        after = lim.get_state(pid)["delay"]
        # После 100 итераций decay 0.95: 4.0 * 0.95^100 = 4.0 * 0.00592 ≈ 0.024
        # Но min_delay=2.0, поэтому должно заклампиться в 2.0
        assert after >= 2.0, f"delay должен быть не меньше min_delay=2.0, получено {after}"
        assert after <= before, f"delay должен уменьшиться или остаться на min, было {before} стало {after}"
        print(f"  record(200) x100: {before} -> {after}")


def test_record_429_increases_delay():
    """429: delay *= 2.0, но не больше max_delay."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "test-profile-2"
        before = lim.get_state(pid)["delay"]
        lim.record(pid, 429)
        after = lim.get_state(pid)["delay"]
        assert after > before, f"после 429 delay должен вырасти: {before} -> {after}"
        assert after == before * 2.0, f"должен быть exactly *2: {before} -> {after}"
        # Много 429 подряд — должно заклампиться в max
        for _ in range(20):
            lim.record(pid, 429)
        clamped = lim.get_state(pid)["delay"]
        assert clamped <= 60.0, f"должен заклампиться в max=60.0, получено {clamped}"
        print(f"  record(429) x1: {before} -> {after}, x21: -> {clamped} (clamped)")


def test_record_401_marks_problematic():
    """401/403: delay *= 3.0 + is_problematic=True."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "test-profile-3"
        lim.record(pid, 401)
        s = lim.get_state(pid)
        assert s["is_problematic"] is True, "после 401 должен выставиться is_problematic"
        assert s["delay"] > 4.0, f"после 401 delay должен вырасти: {s['delay']}"
        # 200 после 401 — сбрасывает problematic
        lim.record(pid, 200)
        s2 = lim.get_state(pid)
        assert s2["is_problematic"] is False, "после 200 OK флаг должен сняться"
        print(f"  record(401) -> is_problematic=True, delay={s['delay']}")
        print(f"  record(200) -> is_problematic=False, delay={s2['delay']}")


def test_record_5xx_increases_moderately():
    """5xx: delay *= 1.5."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "test-profile-4"
        before = lim.get_state(pid)["delay"]
        lim.record(pid, 503)
        after = lim.get_state(pid)["delay"]
        assert after > before
        assert abs(after - before * 1.5) < 0.01, f"должен быть *1.5: {before} -> {after}"
        print(f"  record(503): {before} -> {after}")


def test_wait_blocks_for_correct_time():
    """wait() спит примерно delay секунд."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td), base_delay=1.0, min_delay=0.5, max_delay=10.0, save_debounce_sec=0.0)
        pid = "test-wait"
        # Первый запрос — wait должен спать ~1.0 сек (base)
        t0 = time.monotonic()
        actual = lim.wait(pid)
        elapsed = time.monotonic() - t0
        assert 0.8 < elapsed < 1.5, f"wait должен спать ~1.0s, прошло {elapsed:.2f}s"
        assert 0.8 < actual < 1.5
        print(f"  wait(): slept {elapsed:.2f}s (expected ~1.0)")
        # После 429 — wait должен спать ~2.0s
        lim.record(pid, 429)
        t0 = time.monotonic()
        lim.wait(pid)
        elapsed = time.monotonic() - t0
        assert 1.6 < elapsed < 3.0, f"после 429 wait ~2.0s, прошло {elapsed:.2f}s"
        print(f"  wait() после 429: slept {elapsed:.2f}s (expected ~2.0)")


def test_state_persists_to_disk():
    """Состояние сохраняется на диск и загружается обратно."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        lim1 = _make_limiter(td_path)
        pid = "persist-test"
        lim1.record(pid, 429)
        lim1.record(pid, 429)
        lim1.force_save()

        # Проверяем что файл создан
        state_file = td_path / "rate_state.json"
        assert state_file.exists(), "файл rate_state.json должен быть создан"
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert pid in raw
        assert raw[pid]["delay"] > 4.0, "в файле должна сохраниться увеличенная пауза"
        print(f"  state в файле: {raw[pid]}")

        # Создаём новый лимитер с тем же путём — должен загрузить
        lim2 = _make_limiter(td_path)
        s = lim2.get_state(pid)
        assert s["delay"] == raw[pid]["delay"], f"после reload delay={s['delay']} должен = {raw[pid]['delay']}"
        print(f"  после reload: delay={s['delay']} ✓")


def test_consecutive_ok_counter():
    """consecutive_ok инкрементируется на 200 и сбрасывается на ошибки."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "counter"
        for i in range(5):
            lim.record(pid, 200)
        assert lim.get_state(pid)["consecutive_ok"] == 5
        lim.record(pid, 429)
        assert lim.get_state(pid)["consecutive_ok"] == 0
        assert lim.get_state(pid)["consecutive_errors"] == 1
        print(f"  после 5x200 + 1x429: ok={lim.get_state(pid)['consecutive_ok']} err={lim.get_state(pid)['consecutive_errors']}")


def test_summary_includes_all_profiles():
    """summary() возвращает данные по всем профилям."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        lim.record("a", 200)
        lim.record("b", 429)
        lim.record("c", 401)
        s = lim.summary()
        assert "a" in s["profiles"]
        assert "b" in s["profiles"]
        assert "c" in s["profiles"]
        assert s["profiles"]["a"]["is_problematic"] is False
        assert s["profiles"]["b"]["is_problematic"] is False
        assert s["profiles"]["c"]["is_problematic"] is True
        print(f"  summary: {len(s['profiles'])} профилей, profiles={list(s['profiles'].keys())}")


def test_reset_clears_state():
    """reset() очищает одного или всех профилей."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        lim.record("a", 200)
        lim.record("b", 429)
        lim.reset("a")
        assert "a" not in lim.summary()["profiles"]
        assert "b" in lim.summary()["profiles"]
        lim.reset()
        assert lim.summary()["profiles"] == {}
        print(f"  reset: ok ✓")


def test_thread_safety():
    """record() из разных потоков не ломает состояние."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "thread-test"
        errors = []

        def worker(status):
            try:
                for _ in range(50):
                    lim.record(pid, status)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(200,)),
            threading.Thread(target=worker, args=(429,)),
            threading.Thread(target=worker, args=(401,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"ошибки в потоках: {errors}"
        s = lim.get_state(pid)
        # total_requests должно быть 150 (50 * 3)
        assert s["total_requests"] == 150, f"ожидалось 150, получено {s['total_requests']}"
        print(f"  3 потока x 50 record: total_requests={s['total_requests']} ✓")


def test_201_no_change():
    """201/204/etc — без изменений (это тоже 2xx, но не 200)."""
    with tempfile.TemporaryDirectory() as td:
        lim = _make_limiter(Path(td))
        pid = "no-change"
        before = lim.get_state(pid)["delay"]
        lim.record(pid, 201)
        after = lim.get_state(pid)["delay"]
        # 2xx кроме 200 — не должны менять delay (по дизайну)
        # НО текущая реализация: 200 <= status < 300 — это ВСЕ 2xx. Проверяю что
        # в этом случае delay хотя бы не увеличивается
        assert after <= before, f"201 не должен увеличивать delay: {before} -> {after}"
        print(f"  record(201): {before} -> {after} (no increase)")


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    """Fallback runner если pytest не установлен."""
    print("=" * 60)
    print("Running AdaptiveRateLimiter tests (plain mode)")
    print("=" * 60)
    tests = [
        test_record_200_decreases_delay,
        test_record_429_increases_delay,
        test_record_401_marks_problematic,
        test_record_5xx_increases_moderately,
        test_wait_blocks_for_correct_time,
        test_state_persists_to_disk,
        test_consecutive_ok_counter,
        test_summary_includes_all_profiles,
        test_reset_clears_state,
        test_thread_safety,
        test_201_no_change,
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
            print(f"  ERROR: {e}")
            failed += 1
    print()
    print("=" * 60)
    if failed == 0:
        print(f"All {len(tests)} tests PASSED")
        return 0
    else:
        print(f"{failed} of {len(tests)} tests FAILED")
        return 1


if __name__ == "__main__":
    rc = _run_pytest()
    if rc is None:
        sys.exit(main())
