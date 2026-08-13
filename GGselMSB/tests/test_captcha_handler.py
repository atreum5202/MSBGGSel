"""
Тесты CaptchaHandler.
Запуск: python -m pytest tests/test_captcha_handler.py -v
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.captcha_handler import (  # noqa: E402
    CaptchaHandler,
    CaptchaDetection,
    CAPTCHA_PATTERNS,
    SUCCESS_MARKERS,
)


# ── Тесты детекта ─────────────────────────────────────────────────────────

def test_detect_recaptcha_v2():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<html><body><div class="g-recaptcha" data-sitekey="abc"></div></body></html>'
    d = h.is_captcha_page(html)
    assert d.is_captcha, f"g-recaptcha должен детектиться, получили {d}"
    assert "g-recaptcha" in d.matched_pattern
    print(f"  ✓ g-recaptcha v2: {d.matched_pattern}")


def test_detect_recaptcha_v3():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<script src="https://www.google.com/recaptcha/api.js"></script>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ recaptcha api.js: {d.matched_pattern}")


def test_detect_hcaptcha():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<div class="h-captcha" data-sitekey="xyz"></div>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ h-captcha: {d.matched_pattern}")


def test_detect_hcaptcha_js():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<script src="https://js.hcaptcha.com/1/api.js"></script>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ hcaptcha.com: {d.matched_pattern}")


def test_detect_cloudflare_challenge():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<html><head><title>Just a moment...</title></head><body>cf-challenge-running</body></html>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ cloudflare 'just a moment': {d.matched_pattern}")


def test_detect_cf_chl():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<form id="challenge-form" data-ray="abc123">__cf_chl_tk</form>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ __cf_chl: {d.matched_pattern}")


def test_detect_qrator_qauth():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '<script src="/__qrator/qauth.js"></script>'
    d = h.is_captcha_page(html)
    assert d.is_captcha
    print(f"  ✓ qrator qauth.js: {d.matched_pattern}")


def test_normal_product_page_no_captcha():
    h = CaptchaHandler(http_client=AsyncMock())
    html = '''
    <html>
    <head><title>Купить Steam ключ</title></head>
    <body>
    <div class="ProductCard-module--card--abc123">
      <span class="ProductCard-module--name--xyz">Game Title</span>
      <span class="ProductCard-module--price--qwe">999 ₽</span>
    </div>
    </body>
    </html>
    '''
    d = h.is_captcha_page(html)
    assert not d.is_captcha, f"нормальная страница не должна быть captcha, получили {d}"
    print(f"  ✓ normal product page: clean (success marker: {d.matched_marker})")


def test_empty_html():
    h = CaptchaHandler(http_client=AsyncMock())
    assert not h.is_captcha_page("")
    assert not h.is_captcha_page(None)
    print("  ✓ empty/None HTML: no captcha")


def test_short_html():
    h = CaptchaHandler(http_client=AsyncMock())
    assert not h.is_captcha_page("<html></html>")
    print("  ✓ short HTML: no captcha")


def test_case_insensitive():
    h = CaptchaHandler(http_client=AsyncMock())
    assert h.is_captcha_page("G-RECAPTCHA").is_captcha
    assert h.is_captcha_page("gRecaptcha").is_captcha
    assert h.is_captcha_page("H-Captcha").is_captcha
    print("  ✓ case insensitive: works")


def test_captcha_detection_bool():
    """CaptchaDetection может использоваться в `if` напрямую."""
    h = CaptchaHandler(http_client=AsyncMock())
    d = h.is_captcha_page('<div class="g-recaptcha"></div>')
    if d:  # __bool__
        print("  ✓ __bool__ работает в if-условии")
    else:
        assert False, "должен быть truthy"


# ── Тесты solve_via_msb (с моком) ─────────────────────────────────────────

def test_solve_via_msb_disabled():
    """Если enabled=False, сразу возвращает None."""
    h = CaptchaHandler(http_client=AsyncMock(), enabled=False)
    result = asyncio.run(h.solve_via_msb("test-profile"))
    assert result is None
    print("  ✓ disabled → None")


def test_solve_via_msb_success_first_strategy():
    """Стратегия 1 (solveCaptcha=true) сразу вернула cookies."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "data": {
            "status": "ok",
            "cookies": [
                {"name": "__qrator_jsid", "value": "abc123", "domain": ".ggsel.net"},
                {"name": "session", "value": "xyz789", "domain": ".ggsel.net"},
            ],
            "cookieCount": 2,
        },
    }
    mock_client.post = AsyncMock(return_value=mock_resp)

    h = CaptchaHandler(http_client=mock_client, enabled=True)
    result = asyncio.run(h.solve_via_msb("test-profile"))

    assert result is not None
    assert result["__qrator_jsid"] == "abc123"
    assert result["session"] == "xyz789"
    assert len(result) == 2
    # Проверяем что был вызван post
    assert mock_client.post.call_count == 1
    # Проверяем что был передан solveCaptcha=true
    call_args = mock_client.post.call_args
    body = call_args.kwargs.get("json", {})
    assert body.get("scenario") == "ggsel-login"
    assert body.get("params", {}).get("solveCaptcha") is True
    print("  ✓ solveCaptcha стратегия: получили 2 cookies, solveCaptcha=true в payload")


def test_solve_via_msb_fallback_to_retry():
    """Стратегия 1 упала, стратегия 2 (retry) вернула cookies."""
    mock_client = AsyncMock()
    fail_resp = MagicMock()
    fail_resp.status_code = 500
    fail_resp.text = "internal error"

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {
        "ok": True,
        "data": {
            "status": "ok",
            "cookies": [{"name": "fresh", "value": "cookie_value", "domain": ".ggsel.net"}],
            "cookieCount": 1,
        },
    }
    # Первый вызов — 500, второй — 200
    mock_client.post = AsyncMock(side_effect=[fail_resp, ok_resp])

    h = CaptchaHandler(http_client=mock_client, enabled=True)
    result = asyncio.run(h.solve_via_msb("test-profile"))

    assert result is not None
    assert result["fresh"] == "cookie_value"
    assert mock_client.post.call_count == 2
    print("  ✓ fallback: первая стратегия 500, вторая 200 → cookies получены")


def test_solve_via_msb_no_cookies():
    """Обе стратегии не вернули cookies."""
    mock_client = AsyncMock()
    fail_resp = MagicMock()
    fail_resp.status_code = 200
    fail_resp.json.return_value = {"ok": True, "data": {"status": "error", "error": "no cookies"}}

    mock_client.post = AsyncMock(return_value=fail_resp)

    h = CaptchaHandler(http_client=mock_client, enabled=True)
    result = asyncio.run(h.solve_via_msb("test-profile"))

    assert result is None
    assert mock_client.post.call_count == 2
    print("  ✓ обе стратегии без cookies → None")


def test_solve_via_msb_timeout():
    """Timeout при вызове → None."""
    import httpx
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    h = CaptchaHandler(http_client=mock_client, enabled=True)
    result = asyncio.run(h.solve_via_msb("test-profile"))

    assert result is None
    assert mock_client.post.call_count == 2
    print("  ✓ timeout → None (2 попытки)")


def test_extract_cookies_already_dict():
    """Если cookies уже в формате dict — корректно извлекаем."""
    h = CaptchaHandler(http_client=AsyncMock())
    out = h._extract_cookies({"cookies": {"a": "1", "b": "2"}})
    assert out == {"a": "1", "b": "2"}
    print("  ✓ extract dict format: {a:1, b:2}")


def test_extract_cookies_list_format():
    """Если cookies — список объектов."""
    h = CaptchaHandler(http_client=AsyncMock())
    out = h._extract_cookies({
        "cookies": [
            {"name": "x", "value": "1"},
            {"name": "y", "value": "2"},
            {"name": "z"},  # без value — игнорируем
            {"value": "no_name"},  # без name — игнорируем
        ]
    })
    assert out == {"x": "1", "y": "2"}
    print("  ✓ extract list format: фильтрует пустые/пустые name/value")


def test_extract_cookies_nested_data():
    """Если ответ обёрнут в data.cookies (двойная обёртка)."""
    h = CaptchaHandler(http_client=AsyncMock())
    out = h._extract_cookies({"data": {"cookies": [{"name": "deep", "value": "val"}]}})
    assert out == {"deep": "val"}
    print("  ✓ extract data.cookies: работает с двойной обёрткой")


def test_stats():
    h = CaptchaHandler(http_client=AsyncMock(), enabled=True)
    s = h.stats()
    assert s["enabled"] is True
    assert s["patterns_count"] == len(CAPTCHA_PATTERNS)
    assert s["success_markers_count"] == len(SUCCESS_MARKERS)
    print(f"  ✓ stats: {s['patterns_count']} patterns, {s['success_markers_count']} success markers")


# ── Entry point ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Running CaptchaHandler tests (plain mode)")
    print("=" * 60)
    tests = [
        # Detection
        test_detect_recaptcha_v2,
        test_detect_recaptcha_v3,
        test_detect_hcaptcha,
        test_detect_hcaptcha_js,
        test_detect_cloudflare_challenge,
        test_detect_cf_chl,
        test_detect_qrator_qauth,
        test_normal_product_page_no_captcha,
        test_empty_html,
        test_short_html,
        test_case_insensitive,
        test_captcha_detection_bool,
        # Solve
        test_solve_via_msb_disabled,
        test_solve_via_msb_success_first_strategy,
        test_solve_via_msb_fallback_to_retry,
        test_solve_via_msb_no_cookies,
        test_solve_via_msb_timeout,
        # Extract
        test_extract_cookies_already_dict,
        test_extract_cookies_list_format,
        test_extract_cookies_nested_data,
        test_stats,
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
    else:
        print(f"{failed} of {len(tests)} tests FAILED")
        return 1


if __name__ == "__main__":
    try:
        import pytest
        rc = pytest.main([__file__, "-v", "--tb=short"])
        sys.exit(rc)
    except ImportError:
        sys.exit(main())
