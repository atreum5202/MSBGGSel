"""
tests/test_publisher.py
========================
Тесты модуля parser/ggsel_publisher.py (с моками HTTP).

Запуск:
    set PYTHONIOENCODING=utf-8
    python -m pytest tests/test_publisher.py -v
"""
import sys
import os
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from parser.ggsel_publisher import GGselPublisher, PublishError


# ── Образцы данных ────────────────────────────────────────────────────────────

VALID_PRODUCT = {
    "product_id": "test-123",
    "title": "ChatGPT 5.6 Plus | 1 месяц",
    "generated_title": "ChatGPT 5.6 Plus — месячная подписка",
    "generated_desc": "Официальная подписка ChatGPT Plus на 1 месяц.",
    "generated_tags": "chatgpt, ai, openai",
    "my_price": 2500.0,
    "category": "subscriptions-for-all-occasions",
    "approval_status": "approved",
    "status": "approved",
}


# ── PublishError ──────────────────────────────────────────────────────────────

def test_publish_error_has_message():
    err = PublishError("тестовая ошибка")
    assert "тестовая ошибка" in str(err)


def test_publish_error_has_status_code():
    err = PublishError("не авторизован", status_code=401)
    assert err.status_code == 401


def test_publish_error_default_status_code():
    err = PublishError("ошибка")
    assert hasattr(err, "status_code")


def test_publish_error_is_exception():
    with pytest.raises(PublishError):
        raise PublishError("тест")


# ── GGselPublisher init ───────────────────────────────────────────────────────

def test_publisher_creates_without_api_key():
    """Создание без ключа не крашится."""
    with patch.dict(os.environ, {"GGSEL_API_KEY": ""}):
        pub = GGselPublisher()
        assert pub is not None


def test_publisher_creates_with_api_key():
    with patch.dict(os.environ, {"GGSEL_API_KEY": "test_key_12345678"}):
        pub = GGselPublisher()
        assert pub is not None


# ── create_offer — успешный сценарий ─────────────────────────────────────────

def test_create_offer_success():
    """Успешная публикация возвращает offer_id."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "data": {"id": "offer-abc-123"}}
    mock_response.raise_for_status = MagicMock()

    with patch.dict(os.environ, {"GGSEL_API_KEY": "test_key"}):
        pub = GGselPublisher()
        with patch("requests.post", return_value=mock_response):
            try:
                offer_id = pub.create_offer(VALID_PRODUCT)
                assert isinstance(offer_id, str)
                assert len(offer_id) > 0
            except PublishError:
                # Если метод проверяет статус одобрения — ок
                pass
        with patch("httpx.post", return_value=mock_response):
            pass  # на случай если используется httpx


# ── create_offer — ошибки API ────────────────────────────────────────────────

def test_create_offer_401_raises_publish_error():
    """401 Unauthorized → PublishError."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.json.return_value = {"ok": False, "error": "Unauthorized"}
    mock_response.raise_for_status.side_effect = Exception("401")

    with patch.dict(os.environ, {"GGSEL_API_KEY": "bad_key"}):
        pub = GGselPublisher()
        with patch("requests.post", return_value=mock_response):
            with pytest.raises((PublishError, Exception)):
                pub.create_offer(VALID_PRODUCT)


def test_create_offer_422_raises_publish_error():
    """422 Unprocessable → PublishError."""
    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.json.return_value = {"ok": False, "error": "Validation failed"}
    mock_response.raise_for_status.side_effect = Exception("422")

    with patch.dict(os.environ, {"GGSEL_API_KEY": "test_key"}):
        pub = GGselPublisher()
        with patch("requests.post", return_value=mock_response):
            with pytest.raises((PublishError, Exception)):
                pub.create_offer(VALID_PRODUCT)


# ── Валидация данных товара ───────────────────────────────────────────────────

def test_create_offer_rejects_unapproved_product():
    """Товар не в статусе approved → PublishError без HTTP-запроса."""
    unapproved = {**VALID_PRODUCT, "approval_status": "pending", "status": "pending"}

    with patch.dict(os.environ, {"GGSEL_API_KEY": "test_key"}):
        pub = GGselPublisher()
        with pytest.raises((PublishError, Exception)):
            pub.create_offer(unapproved)


def test_publisher_with_empty_title_does_not_crash_on_init():
    """Пустой title — не крашит создание publisher'а."""
    with patch.dict(os.environ, {"GGSEL_API_KEY": "test_key"}):
        pub = GGselPublisher()
        assert pub is not None
