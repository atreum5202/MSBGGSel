# -*- coding: utf-8 -*-
"""
Тест модуля economics.py
"""
import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

from parser.economics import EconomicsCalculator, EconomicsConfig

def test_basic_calculation():
    """Базовый тест расчёта экономики."""
    print("Начинаем тест economics.py...")
    
    # Простой тест импорта
    try:
        from parser.economics import EconomicsCalculator, EconomicsConfig
        print("✓ Импорт успешен")
    except Exception as e:
        print(f"✗ Импорт failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Создаём калькулятор с конфигом
    try:
        config = EconomicsConfig(
            category_fee_pct=0.10,
            payment_fee_pct=0.027,
            withdrawal_fee_pct=0.0,
            tax_pct=0.0,
            risk_reserve_pct=0.05,
            fixed_costs_rub=0.0,
            target_margin_pct=0.15,
            min_net_profit_rub=50.0,
        )
        calc = EconomicsCalculator(config)
        print("✓ Калькулятор создан")
    except Exception as e:
        print(f"✗ Создание калькулятора failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Тест 1: базовый расчёт
    try:
        result = calc.calculate(source_price=100.0, category_fee_pct=0.10)
        print(f"✓ Тест 1: result.ok={result.ok}, sell_price={result.sell_price}")
        if result.ok:
            print(f"  source_price=100, sell_price={result.sell_price:.2f}, profit={result.expected_profit_rub:.2f}")
        else:
            print(f"  error: {result.error_message}")
    except Exception as e:
        print(f"✗ Тест 1 failed с исключением: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nТест economics.py завершён")

if __name__ == "__main__":
    test_basic_calculation()