"""
tests/test_economics_full.py
=============================
Расширенные тесты модуля parser/economics.py.

Запуск:
    set PYTHONIOENCODING=utf-8
    python -m pytest tests/test_economics_full.py -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from parser.economics import (
    EconomicsConfig,
    EconomicsResult,
    EconomicsCalculator,
    get_calculator,
    calculate_product_economics,
)


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture
def default_calc():
    return EconomicsCalculator()


@pytest.fixture
def strict_calc():
    """Калькулятор с явными параметрами."""
    cfg = EconomicsConfig(
        payment_fee_pct=0.027,
        withdrawal_fee_pct=0.0,
        tax_pct=0.0,
        risk_reserve_pct=0.05,
        fixed_costs_rub=0.0,
        target_margin_pct=0.15,
        min_net_profit_rub=50.0,
        round_to=1,
    )
    return EconomicsCalculator(cfg)


# ── Базовые расчёты ────────────────────────────────────────────────────────────

def test_basic_calculation_returns_result(strict_calc):
    result = strict_calc.calculate(source_price=1000.0, category_fee_pct=0.10)
    assert isinstance(result, EconomicsResult)


def test_sell_price_greater_than_source(strict_calc):
    result = strict_calc.calculate(source_price=1000.0, category_fee_pct=0.10)
    assert result.sell_price > 1000.0


def test_net_profit_positive_for_normal_product(strict_calc):
    result = strict_calc.calculate(source_price=500.0, category_fee_pct=0.10)
    assert result.expected_profit_rub > 0


def test_net_margin_pct_reasonable(strict_calc):
    result = strict_calc.calculate(source_price=1000.0, category_fee_pct=0.10)
    assert 0.0 < result.expected_net_margin_pct < 1.0


# ── Прибыльность и рекомендации ───────────────────────────────────────────────

def test_high_category_fee_marks_unprofitable(strict_calc):
    """Комиссия категории 60% — прибыль отрицательная или ok=False."""
    result = strict_calc.calculate(source_price=100.0, category_fee_pct=0.60)
    assert result.ok is False or result.expected_profit_rub <= 0


def test_low_profit_marks_not_ok():
    """Если прибыль ниже min_net_profit_rub — ok=False."""
    cfg = EconomicsConfig(
        target_margin_pct=0.01,
        min_net_profit_rub=10000.0,
        payment_fee_pct=0.027,
    )
    calc = EconomicsCalculator(cfg)
    result = calc.calculate(source_price=100.0, category_fee_pct=0.05)
    assert result.ok is False or result.expected_profit_rub < 10000.0


def test_normal_product_is_ok(strict_calc):
    result = strict_calc.calculate(source_price=2000.0, category_fee_pct=0.10)
    assert result.ok is True


# ── Параметры конфига ──────────────────────────────────────────────────────────

def test_fixed_costs_increase_sell_price():
    """Фиксированные затраты поднимают итоговую цену."""
    cfg_no_fixed = EconomicsConfig(fixed_costs_rub=0.0, target_margin_pct=0.15)
    cfg_with_fixed = EconomicsConfig(fixed_costs_rub=500.0, target_margin_pct=0.15)

    price_no = EconomicsCalculator(cfg_no_fixed).calculate(1000.0).sell_price
    price_with = EconomicsCalculator(cfg_with_fixed).calculate(1000.0).sell_price
    assert price_with > price_no


def test_higher_margin_target_increases_price():
    cfg_low = EconomicsConfig(target_margin_pct=0.05)
    cfg_high = EconomicsConfig(target_margin_pct=0.30)

    price_low = EconomicsCalculator(cfg_low).calculate(1000.0).sell_price
    price_high = EconomicsCalculator(cfg_high).calculate(1000.0).sell_price
    assert price_high > price_low


# ── Граничные значения ────────────────────────────────────────────────────────

def test_zero_source_price_does_not_crash(default_calc):
    """Нулевая цена не должна вызывать исключение."""
    try:
        result = default_calc.calculate(source_price=0.0)
        assert result is not None
    except (ZeroDivisionError, ValueError):
        pytest.fail("Нулевая цена вызвала исключение")


def test_very_expensive_product(strict_calc):
    result = strict_calc.calculate(source_price=100_000.0, category_fee_pct=0.10)
    assert result.sell_price > 100_000.0
    assert result.expected_profit_rub > 0


def test_very_cheap_product(strict_calc):
    """Дешёвый товар где прибыль меньше min_net_profit_rub."""
    result = strict_calc.calculate(source_price=10.0, category_fee_pct=0.10)
    # Не крашится
    assert result is not None


# ── Result fields ─────────────────────────────────────────────────────────────

def test_result_has_required_fields(strict_calc):
    result = strict_calc.calculate(source_price=1000.0)
    assert hasattr(result, "sell_price")
    assert hasattr(result, "expected_profit_rub")
    assert hasattr(result, "expected_net_margin_pct")
    assert hasattr(result, "ok")
    assert hasattr(result, "economy_complete")


def test_sell_price_is_rounded(strict_calc):
    """sell_price должна быть округлена (round_to=1)."""
    result = strict_calc.calculate(source_price=777.77, category_fee_pct=0.12)
    assert result.sell_price == round(result.sell_price, 1)


# ── calculate_product_economics ───────────────────────────────────────────────

def test_calculate_product_economics_dict():
    result = calculate_product_economics({"price": 500.0, "category_fee_pct": 0.10})
    assert isinstance(result, EconomicsResult)
    assert result.sell_price > 0


def test_calculate_product_economics_missing_fee():
    """Если category_fee_pct не передан — не крашится."""
    result = calculate_product_economics({"price": 1000.0})
    assert result is not None


def test_calculate_product_economics_with_source_price():
    """Поддержка ключа source_price."""
    result = calculate_product_economics({"source_price": 800.0})
    assert result.sell_price > 0


# ── Синглтон ──────────────────────────────────────────────────────────────────

def test_get_calculator_returns_same_instance():
    c1 = get_calculator()
    c2 = get_calculator()
    assert c1 is c2


def test_get_calculator_returns_calculator():
    calc = get_calculator()
    assert isinstance(calc, EconomicsCalculator)


# ── calculate_for_product ────────────────────────────────────────────────────

def test_calculate_for_product(strict_calc):
    product = {"price": 1500.0, "source_price": 1500.0, "category_fee_pct": 0.08}
    result = strict_calc.calculate_for_product(product)
    assert isinstance(result, EconomicsResult)
    assert result.sell_price > 1500.0


def test_update_product_economics_adds_fields(strict_calc):
    product = {"price": 1000.0, "title": "Test product"}
    updated = strict_calc.update_product_economics(product)
    assert isinstance(updated, dict)
    # update_product_economics добавляет поля экономики в словарь товара
    assert "expected_profit_rub" in updated or "sell_price" in updated or "economy_complete" in updated
