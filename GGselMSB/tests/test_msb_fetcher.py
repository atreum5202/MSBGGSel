"""
РўРµСЃС‚С‹ MsbFetcher (СЃ РјРѕРєР°РјРё вЂ” Р±РµР· СЂРµР°Р»СЊРЅРѕРіРѕ MSB/curl).
Р—Р°РїСѓСЃРє: python -m pytest tests/test_msb_fetcher.py -v
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.msb_fetcher import MsbFetcher, FetchResult  # noqa: E402
from parser.profile_pool import ProfilePool, PoolProfile  # noqa: E402
from parser.adaptive_rate_limiter import AdaptiveRateLimiter  # noqa: E402
from parser.captcha_handler import CaptchaHandler, CaptchaDetection  # noqa: E402
from parser.telemetry import Telemetry  # noqa: E402
from parser.msb_client import MsbClient  # noqa: E402


# в”Ђв”Ђ РЈС‚РёР»РёС‚С‹ РґР»СЏ РјРѕРєРѕРІ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def _make_mock_pool(profile_id: str = "test-profile", cookies: dict = None) -> ProfilePool:
    """Pool, РєРѕС‚РѕСЂС‹Р№ РЅРµ Р»РµР·РµС‚ РІ СЃРµС‚СЊ. Р’РѕР·РІСЂР°С‰Р°РµС‚ Р·Р°СЂР°РЅРµРµ Р·Р°РґР°РЅРЅС‹Рµ cookies."""
    pool = ProfilePool.__new__(ProfilePool)  # Р±РµР· __init__
    pool._profiles = {
        profile_id: PoolProfile(
            profile_id=profile_id,
            name="test",
            cookies=cookies or {"__qrator_jsid": "valid", "session": "abc"},
            cookies_fetched_at=0,
        )
    }
    pool._lock = asyncio.Lock()
    pool._http = None
    pool._initialized = True
    pool._started_by_pool = set()

    async def _get_cookies_mock():
        return (pool._profiles[profile_id].cookies, profile_id)

    pool.get_cookies = _get_cookies_mock
    return pool


def _make_cffi_response(html: str = "<html>OK</html>", status: int = 200):
    """РџРѕРґРґРµР»РєР° РѕС‚РІРµС‚Р° curl-cffi."""
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.headers = {}
    return resp


# в”Ђв”Ђ РўРµСЃС‚С‹ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def test_fetch_success_200():
    """РЈСЃРїРµС€РЅС‹Р№ 200 СЃ РІР°Р»РёРґРЅС‹РјРё cookies."""
    pool = _make_mock_pool(cookies={"__qrator_jsid": "valid", "session": "abc"})
    fetcher = MsbFetcher(pool=pool)

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=("<html>ProductCard-module--card</html>", 200)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.success is True
    assert result.status_code == 200
    assert result.profile_id == "test-profile"
    assert "ProductCard" in result.html
    assert result.error == ""
    print(f"  вњ“ 200 OK: success={result.success}, profile={result.profile_id}")


def test_fetch_challenge_detected():
    """Qrator challenge РІ HTML вЂ” is_challenge=True, success=False."""
    pool = _make_mock_pool()
    fetcher = MsbFetcher(pool=pool)
    challenge_html = "<html>__qrator/qauth.js running...</html>"

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=(challenge_html, 200)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.success is False
    assert result.is_challenge is True
    assert result.error == "qrator_challenge"
    print(f"  вњ“ challenge detected: is_challenge={result.is_challenge}, error={result.error}")


def test_fetch_401_reports_error_to_pool():
    """401 в†’ pool.report_error() + is_challenge=False, error=auth_failed_401."""
    pool = _make_mock_pool()
    fetcher = MsbFetcher(pool=pool)
    report_called = []
    pool.report_error = AsyncMock(side_effect=lambda pid: report_called.append(pid))

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=("<html>Forbidden</html>", 401)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.success is False
    assert result.status_code == 401
    assert "auth_failed" in result.error
    assert report_called == ["test-profile"]
    print(f"  вњ“ 401: report_error called, error={result.error}")


def test_fetch_429_marks_rate_limited():
    """429 в†’ telemetry emits parser.rate_limited."""
    pool = _make_mock_pool()
    tel = Telemetry(storage_dir="_test_tel", enabled=True)
    # РСЃРїРѕР»СЊР·СѓРµРј РІСЂРµРјРµРЅРЅСѓСЋ РґРёСЂРµРєС‚РѕСЂРёСЋ вЂ” РѕС‡РёСЃС‚РёРј РІ РєРѕРЅС†Рµ
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    tel = Telemetry(storage_dir=tmpdir, enabled=True)
    fetcher = MsbFetcher(pool=pool, telemetry=tel)

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=("<html>Too Many Requests</html>", 429)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.status_code == 429
    assert "rate_limited" in result.error

    events = [r["event"] for r in tel.read_recent(limit=20)]
    assert "parser.rate_limited" in events
    assert "parser.page_fetched" in events
    print(f"  вњ“ 429: telemetry emits: {events[:3]}...")
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_fetch_5xx_increases_rate_delay():
    """5xx в†’ rate_limiter РІРёРґРёС‚ СЃС‚Р°С‚СѓСЃ Рё РїРѕРґРЅРёРјР°РµС‚ delay."""
    pool = _make_mock_pool()
    rl = AdaptiveRateLimiter(storage_path="_test_rl.json", save_debounce_sec=0.0)
    fetcher = MsbFetcher(pool=pool, rate_limiter=rl)
    try:
        with patch.object(MsbFetcher, "_do_request_sync",
                          return_value=("<html>Server Error</html>", 503)):
            asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

        s = rl.get_state("test-profile")
        assert s["last_status"] == 503
        assert s["consecutive_errors"] == 1
        # delay РґРѕР»Р¶РµРЅ РІС‹СЂР°СЃС‚Рё
        assert s["delay"] > 4.0
        print(f"  вњ“ 503: rate_limiter delay={s['delay']:.2f}s, err={s['consecutive_errors']}")
    finally:
        Path("_test_rl.json").unlink(missing_ok=True)


def test_fetch_captcha_detected_and_solved():
    """HTML СЃРѕРґРµСЂР¶РёС‚ g-recaptcha в†’ captcha handler в†’ retry в†’ success."""
    pool = _make_mock_pool()
    captcha_html = '<html><div class="g-recaptcha"></div></html>'
    ok_html = '<html><div class="ProductCard-module--card">OK</div></html>'

    mock_http = AsyncMock()
    # solve_via_msb в†’ runScenario в†’ РІРµСЂРЅС‘С‚ cookies
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "data": {"status": "ok", "cookies": [{"name": "fresh", "value": "cookie"}]},
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    captcha_handler = CaptchaHandler(http_client=mock_http, enabled=True)
    fetcher = MsbFetcher(pool=pool, captcha_handler=captcha_handler, msb_api_base="http://fake")

    # РџРµСЂРІС‹Р№ Р·Р°РїСЂРѕСЃ в†’ captcha, РІС‚РѕСЂРѕР№ (РїРѕСЃР»Рµ solve) в†’ OK
    with patch.object(MsbFetcher, "_do_request_sync",
                      side_effect=[(captcha_html, 200), (ok_html, 200)]):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.success is True
    assert result.captcha_detected is True
    assert result.strategy == "msb_captcha_solved"
    assert mock_http.post.call_count >= 1  # solve_via_msb Р±С‹Р» РІС‹Р·РІР°РЅ
    print(f"  вњ“ captcha detected + solved: strategy={result.strategy}, success={result.success}")


def test_fetch_captcha_unsolved_returns_fail():
    """Captcha РµСЃС‚СЊ, solve РЅРµ РґР°Р» cookies в†’ success=False, captcha_detected=True."""
    pool = _make_mock_pool()
    captcha_html = '<html><div class="g-recaptcha"></div></html>'

    mock_http = AsyncMock()
    fail_resp = MagicMock()
    fail_resp.status_code = 500
    fail_resp.text = "err"
    mock_http.post = AsyncMock(return_value=fail_resp)

    captcha_handler = CaptchaHandler(http_client=mock_http, enabled=True)
    fetcher = MsbFetcher(pool=pool, captcha_handler=captcha_handler)

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=(captcha_html, 200)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert result.success is False
    assert result.captcha_detected is True
    assert "captcha" in result.error
    print(f"  вњ“ captcha unsolved: error={result.error}")


def test_fetch_no_cookies_returns_error():
    """РџСѓР» РІРµСЂРЅСѓР» None в†’ FetchResult error='no_cookies'."""
    pool = ProfilePool.__new__(ProfilePool)
    pool._profiles = {}
    pool._lock = asyncio.Lock()
    pool._initialized = True
    async def _empty():
        return (None, None)
    pool.get_cookies = _empty
    pool.report_error = AsyncMock()

    fetcher = MsbFetcher(pool=pool)
    # _refresh_cookies С‚РѕР¶Рµ РїСЂРѕРІР°Р»РёС‚СЃСЏ вЂ” РјРѕРєР°РµРј С‡С‚Рѕ MSB РЅРµРґРѕСЃС‚СѓРїРµРЅ
    fetcher._refresh_cookies = AsyncMock(return_value=None)

    result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))
    assert result.success is False
    assert result.error == "no_cookies"
    print(f"  вњ“ no cookies: error={result.error}")


def test_fetch_invalid_cookies_triggers_refresh():
    """РљСѓРєРё РЅРµ РІР°Р»РёРґРЅС‹Рµ (РЅРµС‚ Qrator-РєР»СЋС‡РµР№) в†’ _refresh_cookies РІС‹Р·РІР°РЅ."""
    pool = _make_mock_pool(cookies={"random": "value", "another": "x"})
    fetcher = MsbFetcher(pool=pool)
    refresh_called = []
    async def _mock_refresh(pid):
        refresh_called.append(pid)
        return {"__qrator_jsid": "fresh_from_msb", "session": "y"}
    fetcher._refresh_cookies = _mock_refresh

    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=("<html>OK</html>", 200)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

    assert refresh_called == ["test-profile"]
    assert result.success is True
    assert result.cookies_source == "msb_refresh"
    print(f"  вњ“ invalid cookies в†’ refresh: source={result.cookies_source}")


def test_refresh_cookies_starts_and_stops():
    """_refresh_cookies: start в†’ runScenario в†’ stop. Stop РІС‹Р·РІР°РЅ РІ finally."""
    fetcher = MsbFetcher()
    # РњРѕРєР°РµРј MsbClient: start_profile / start_scenario / stop_profile
    mock_client = AsyncMock(spec=MsbClient)
    mock_client.start_profile = AsyncMock(return_value={"id": "test-pid"})
    mock_client.start_scenario = AsyncMock(return_value={
        "status": "ok",
        "cookies": [{"name": "qrator", "value": "fresh"}],
    })
    mock_client.stop_profile = AsyncMock(return_value={})
    fetcher._ml_client = mock_client
    fetcher._ml_client_initialized = True

    result = asyncio.run(fetcher._refresh_cookies("test-pid"))
    assert result is not None
    assert result["qrator"] == "fresh"
    # start + scenario + stop (РІ finally)
    assert mock_client.start_profile.call_count == 1
    assert mock_client.start_scenario.call_count == 1
    assert mock_client.stop_profile.call_count == 1
    # stop РІС‹Р·РІР°РЅ СЃ РїСЂР°РІРёР»СЊРЅС‹Рј profile_id
    mock_client.stop_profile.assert_called_with("test-pid")
    print(f"  вњ“ refresh: startв†’scenarioв†’stop (all 3 called, stop with right pid)")


def test_refresh_cookies_stops_even_on_error():
    """Р•СЃР»Рё runScenario РїР°РґР°РµС‚ вЂ” stop РІСЃС‘ СЂР°РІРЅРѕ РІС‹Р·С‹РІР°РµС‚СЃСЏ (defense in depth)."""
    fetcher = MsbFetcher()
    mock_client = AsyncMock(spec=MsbClient)
    mock_client.start_profile = AsyncMock(return_value={"id": "test-pid"})
    mock_client.start_scenario = AsyncMock(side_effect=RuntimeError("scenario failed"))
    # Fallback get_cookies С‚РѕР¶Рµ РЅРµ РІРѕР·РІСЂР°С‰Р°РµС‚ РІР°Р»РёРґРЅС‹С… РєСѓРєРѕРІ в†’ РёС‚РѕРі None
    mock_client.get_cookies = AsyncMock(return_value={})
    mock_client.stop_profile = AsyncMock(return_value={})
    fetcher._ml_client = mock_client
    fetcher._ml_client_initialized = True

    result = asyncio.run(fetcher._refresh_cookies("test-pid"))
    assert result is None
    # start (1) + scenario (raise) + get_cookies(fallback, {}) + stop (РІ finally)
    assert mock_client.start_profile.call_count == 1
    assert mock_client.start_scenario.call_count == 1
    assert mock_client.get_cookies.call_count == 1
    assert mock_client.stop_profile.call_count == 1
    print(f"  вњ“ refresh fail: start + scenario(raise) + get_cookies(fallback) + stop вЂ” defense in depth")


def test_rate_limit_waits_before_request():
    """rate_limiter.wait() РІС‹Р·С‹РІР°РµС‚СЃСЏ РїРµСЂРµРґ Р·Р°РїСЂРѕСЃРѕРј."""
    pool = _make_mock_pool()
    rl = AdaptiveRateLimiter(storage_path="_test_rl2.json", save_debounce_sec=0.0,
                             base_delay=0.1, min_delay=0.1, max_delay=5.0)
    fetcher = MsbFetcher(pool=pool, rate_limiter=rl)
    try:
        # РЎРґРµР»Р°С‚СЊ РїСЂРµРґС‹РґСѓС‰РёР№ Р·Р°РїСЂРѕСЃ С‡С‚РѕР±С‹ delay Р±С‹Р» РІС‹СЃС‚Р°РІР»РµРЅ
        with patch.object(MsbFetcher, "_do_request_sync",
                          return_value=("<html>OK</html>", 200)):
            asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

        # РўРµРїРµСЂСЊ Р·Р°СЃРµРєР°РµРј РІСЂРµРјСЏ РІС‚РѕСЂРѕРіРѕ fetch
        with patch.object(MsbFetcher, "_do_request_sync",
                          return_value=("<html>OK</html>", 200)) as mock_req:
            t0 = time.monotonic()
            asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))
            elapsed = time.monotonic() - t0
            # Р”РѕР»Р¶РµРЅ Р±С‹С‚СЊ wait (С…РѕС‚СЏ Р±С‹ РјРёРЅРёРјСѓРј 0.1 СЃРµРє)
            # РўРѕС‡РЅРµРµ: РїРѕСЃР»Рµ 200 OK delay = 4.0 * 0.95 = 3.8
            assert elapsed >= 0.1, f"fetch РґРѕР»Р¶РµРЅ РІРєР»СЋС‡Р°С‚СЊ wait, РїСЂРѕС€Р»Рѕ {elapsed:.2f}s"
            print(f"  вњ“ rate_limiter.wait(): fetch Р·Р°РЅСЏР» {elapsed:.2f}s (РІРєР»СЋС‡Р°СЏ wait)")
    finally:
        Path("_test_rl2.json").unlink(missing_ok=True)


def test_telemetry_emits_page_fetched():
    """РљР°Р¶РґС‹Р№ fetch в†’ parser.page_fetched РІ С‚РµР»РµРјРµС‚СЂРёРё."""
    import tempfile, shutil
    tmpdir = tempfile.mkdtemp()
    try:
        pool = _make_mock_pool()
        tel = Telemetry(storage_dir=tmpdir, enabled=True)
        fetcher = MsbFetcher(pool=pool, telemetry=tel)

        with patch.object(MsbFetcher, "_do_request_sync",
                          return_value=("<html>OK</html>", 200)):
            asyncio.run(fetcher.fetch("https://ggsel.net/catalog/games"))

        events = [r["event"] for r in tel.read_recent()]
        assert "parser.page_fetched" in events
        page_event = next(r for r in tel.read_recent() if r["event"] == "parser.page_fetched")
        assert page_event["profile_id"] == "test-profile"
        assert page_event["status"] == 200
        assert "latency_ms" in page_event
        print(f"  вњ“ telemetry: parser.page_fetched emitted, status=200, latency={page_event['latency_ms']}ms")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_status_dict():
    pool = _make_mock_pool()
    fetcher = MsbFetcher(pool=pool, base_url="https://ggsel.net",
                          msb_api_base="http://127.0.0.1:58888")
    s = fetcher.status()
    assert "msb_fetcher" in s
    inner = s["msb_fetcher"]
    # msb_api_base -- aktualnoe nazvanie polya
    assert inner["msb_api_base"] == "http://127.0.0.1:58888"
    assert inner["base_url"] == "https://ggsel.net"
    assert inner["pool_initialized"] is True
    assert inner["cffi_available"] is True
    assert "qrator_cookie_keys" in inner
    print(f"  вњ“ status: {sorted(inner.keys())}")


def test_fetch_profile_id_preserved_in_result():
    """profile_id РёР· РїСѓР»Р° РїРѕРїР°РґР°РµС‚ РІ FetchResult."""
    pool = _make_mock_pool(profile_id="specific-pid-123")
    fetcher = MsbFetcher(pool=pool)
    with patch.object(MsbFetcher, "_do_request_sync",
                      return_value=("<html>OK</html>", 200)):
        result = asyncio.run(fetcher.fetch("https://ggsel.net/x"))
    assert result.profile_id == "specific-pid-123"
    assert result.used_profile_id == "specific-pid-123"  # alias
    print(f"  вњ“ profile_id={result.profile_id}, used_profile_id={result.used_profile_id}")


# в”Ђв”Ђ Entry point в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

import time  # РґР»СЏ С‚РµСЃС‚Р° rate limit

def main():
    print("=" * 60)
    print("Running MsbFetcher tests (plain mode)")
    print("=" * 60)
    tests = [
        test_fetch_success_200,
        test_fetch_challenge_detected,
        test_fetch_401_reports_error_to_pool,
        test_fetch_429_marks_rate_limited,
        test_fetch_5xx_increases_rate_delay,
        test_fetch_captcha_detected_and_solved,
        test_fetch_captcha_unsolved_returns_fail,
        test_fetch_no_cookies_returns_error,
        test_fetch_invalid_cookies_triggers_refresh,
        test_refresh_cookies_starts_and_stops,
        test_refresh_cookies_stops_even_on_error,
        test_rate_limit_waits_before_request,
        test_telemetry_emits_page_fetched,
        test_status_dict,
        test_fetch_profile_id_preserved_in_result,
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
            import traceback
            print(f"  ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
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
