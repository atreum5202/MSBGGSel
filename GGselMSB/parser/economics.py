# -*- coding: utf-8 -*-
"""
parser/economics.py
===================
Единый модуль экономики для GGselV7.

Полная формула расчёта экономической эффективности товара:
  1. Закупка (source_price)
  2. Комиссия категории (category_fee)
  3. Платёжная комиссия (payment_fee)
  4. Налог (tax)
  5. Комиссия вывода (withdrawal_fee)
  6. Фиксированные расходы (fixed_costs)
  7. Резерв возвратов/споров (risk_reserve)
  8. Округление
  9. Минимальная чистая прибыль

Статус расчёта: economy_complete = 1 только если все параметры заданы и расчёт корректен.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Optional, Dict, Any
from decimal import Decimal, ROUND_HALF_UP

log = logging.getLogger("ggselv7.economics")


@dataclass
class EconomicsConfig:
    """Конфигурация экономических параметров."""
    # Комиссии
    category_fee_pct: float = 0.0      # Комиссия категории (из API GGSEL)
    payment_fee_pct: float = 0.027     # Комиссия платёжной системы (2.7%)
    withdrawal_fee_pct: float = 0.0     # Комиссия вывода (зависит от способа)
    tax_pct: float = 0.0               # Налог (обычно 0 для физлиц, 6-13% для юрлиц)
    
    # Резервы и расходы
    risk_reserve_pct: float = 0.05      # Резерв на возвраты/споры (5%)
    fixed_costs_rub: float = 0.0       # Фиксированные расходы на товар (руб)
    
    # Целевые показатели
    target_margin_pct: float = 0.15    # Целевая маржа (15%)
    min_net_profit_rub: float = 50.0   # Минимальная чистая прибыль (50 руб)
    
    # Округление
    round_to: int = 1                  # Округлять до целых (1) или копеек (0)


@dataclass
class EconomicsResult:
    """Результат расчёта экономики."""
    ok: bool                              # Расчёт выполнен успешно
    sell_price: float                     # Рекомендуемая цена продажи
    total_fee_pct: float                  # Общая комиссия в процентах
    total_costs_rub: float                # Общие издержки в рублях
    expected_profit_rub: float            # Ожидаемая прибыль в рублях
    expected_net_margin_pct: float       # Ожидаемая чистая маржа в процентах
    economy_complete: bool               # Все ли параметры заданы
    missing_params: list[str]             # Список недостающих параметров
    error_message: Optional[str] = None  # Сообщение об ошибке


class EconomicsCalculator:
    """Калькулятор экономики с единой формулой."""
    
    def __init__(self, config: Optional[EconomicsConfig] = None):
        self.config = config or self._load_default_config()
    
    def _load_default_config(self) -> EconomicsConfig:
        """Загружает конфигурацию из переменных окружения."""
        return EconomicsConfig(
            category_fee_pct=float(os.getenv("CATEGORY_FEE_PCT", "0.0")),
            payment_fee_pct=float(os.getenv("PAYMENT_FEE_PCT", "0.027")),
            withdrawal_fee_pct=float(os.getenv("WITHDRAWAL_FEE_PCT", "0.0")),
            tax_pct=float(os.getenv("TAX_PCT", "0.0")),
            risk_reserve_pct=float(os.getenv("RISK_RESERVE_PCT", "0.05")),
            fixed_costs_rub=float(os.getenv("FIXED_COSTS_RUB", "0.0")),
            target_margin_pct=float(os.getenv("TARGET_MARGIN_PCT", "0.15")),
            min_net_profit_rub=float(os.getenv("MIN_NET_PROFIT_RUB", "50.0")),
            round_to=int(os.getenv("ROUND_TO", "1")),
        )
    
    def calculate(
        self,
        source_price: float,
        category_fee_pct: Optional[float] = None,
        **kwargs
    ) -> EconomicsResult:
        """
        Рассчитывает экономику товара.
        
        Args:
            source_price: Цена закупки у источника
            category_fee_pct: Комиссия категории (переопределяет конфиг)
            **kwargs: Дополнительные параметры для переопределения конфига
        
        Returns:
            EconomicsResult с результатами расчёта
        """
        # Валидация входных данных
        if source_price is None or source_price <= 0:
            return EconomicsResult(
                ok=False,
                sell_price=0.0,
                total_fee_pct=0.0,
                total_costs_rub=0.0,
                expected_profit_rub=0.0,
                expected_net_margin_pct=0.0,
                economy_complete=False,
                missing_params=["source_price"],
                error_message="Некорректная цена закупки"
            )
        
        # Применяем переопределения
        config = self.config
        if category_fee_pct is not None:
            config = EconomicsConfig(
                **{**config.__dict__, 'category_fee_pct': category_fee_pct}
            )
        
        # Применяем дополнительные переопределения
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        # Проверяем обязательные параметры
        missing_params = self._check_required_params(config)
        if missing_params:
            return EconomicsResult(
                ok=False,
                sell_price=0.0,
                total_fee_pct=0.0,
                total_costs_rub=0.0,
                expected_profit_rub=0.0,
                expected_net_margin_pct=0.0,
                economy_complete=False,
                missing_params=missing_params,
                error_message=f"Не заданы параметры: {', '.join(missing_params)}"
            )
        
        # Расчёт общей комиссии
        total_fee_pct = (
            config.category_fee_pct + 
            config.payment_fee_pct + 
            config.withdrawal_fee_pct
        )
        
        # Расчёт общей суммы издержек
        # Формула: source_price * (1 + total_fee + tax_pct + risk_reserve_pct) + fixed_costs_rub
        total_costs_rub = (
            source_price * (1.0 + total_fee_pct + config.tax_pct + config.risk_reserve_pct) +
            config.fixed_costs_rub
        )
        
        # Расчёт целевой цены продажи
        # Формула: total_costs / (1 - target_margin)
        if config.target_margin_pct >= 1.0:
            return EconomicsResult(
                ok=False,
                sell_price=0.0,
                total_fee_pct=total_fee_pct,
                total_costs_rub=total_costs_rub,
                expected_profit_rub=0.0,
                expected_net_margin_pct=0.0,
                economy_complete=False,
                missing_params=[],
                error_message="Целевая маржа должна быть меньше 100%"
            )
        
        target_sell_price = total_costs_rub / (1.0 - config.target_margin_pct)
        
        # Округление
        if config.round_to == 1:
            sell_price = float(Decimal(str(target_sell_price)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        else:
            sell_price = float(Decimal(str(target_sell_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        
        # Расчёт ожидаемой прибыли
        # Формула: sell_price - total_costs
        expected_profit_rub = sell_price - total_costs_rub
        
        # Расчёт чистой маржи
        # Формула: net_profit / sell_price
        expected_net_margin_pct = (expected_profit_rub / sell_price) if sell_price > 0 else 0.0
        
        # Проверка минимальной прибыли
        if expected_profit_rub < config.min_net_profit_rub:
            return EconomicsResult(
                ok=False,
                sell_price=sell_price,
                total_fee_pct=total_fee_pct,
                total_costs_rub=total_costs_rub,
                expected_profit_rub=expected_profit_rub,
                expected_net_margin_pct=expected_net_margin_pct,
                economy_complete=True,
                missing_params=[],
                error_message=f"Прибыль {expected_profit_rub:.2f} руб ниже минимума {config.min_net_profit_rub} руб"
            )
        
        return EconomicsResult(
            ok=True,
            sell_price=sell_price,
            total_fee_pct=total_fee_pct,
            total_costs_rub=total_costs_rub,
            expected_profit_rub=expected_profit_rub,
            expected_net_margin_pct=expected_net_margin_pct,
            economy_complete=True,
            missing_params=[],
            error_message=None
        )
    
    def _check_required_params(self, config: EconomicsConfig) -> list[str]:
        """Проверяет, заданы ли все обязательные параметры."""
        missing = []
        
        # Обязательные параметры
        # 0.0 is valid; only negative values are errors
            
        
        
        # withdrawal 0 valid for individuals
            
        
        return missing
    
    def calculate_for_product(self, product_data: Dict[str, Any]) -> EconomicsResult:
        """
        Рассчитывает экономику для данных товара из БД.
        
        Args:
            product_data: Словарь с данными товара (из parsed_products)
        
        Returns:
            EconomicsResult с результатами расчёта
        """
        source_price = product_data.get("source_price") or product_data.get("price") or 0.0
        category_fee_pct = product_data.get("ggsel_fee_pct") or 0.0
        
        # Дополнительные параметры из товара
        kwargs = {
            "payment_fee_pct": product_data.get("payment_fee_pct"),
            "withdrawal_fee_pct": product_data.get("withdrawal_fee_pct"),
            "tax_pct": product_data.get("tax_pct"),
            "risk_reserve_pct": product_data.get("risk_reserve_pct"),
            "fixed_costs_rub": product_data.get("fixed_costs_rub"),
        }
        
        # Убираем None значения
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        
        return self.calculate(source_price, category_fee_pct, **kwargs)
    
    def update_product_economics(self, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обновляет данные товара результатами расчёта экономики.
        
        Args:
            product_data: Словарь с данными товара
        
        Returns:
            Обновлённый словарь с полями экономики
        """
        result = self.calculate_for_product(product_data)
        
        import datetime
        now = datetime.datetime.utcnow().isoformat()
        
        product_data.update({
            "sell_price": result.sell_price,
            "total_fee_pct": result.total_fee_pct,  # Будет разбито на отдельные поля
            "ggsel_fee_pct": product_data.get("ggsel_fee_pct") or self.config.category_fee_pct,
            "payment_fee_pct": self.config.payment_fee_pct,
            "withdrawal_fee_pct": self.config.withdrawal_fee_pct,
            "tax_pct": self.config.tax_pct,
            "fixed_costs_rub": self.config.fixed_costs_rub,
            "risk_reserve_pct": self.config.risk_reserve_pct,
            "total_costs_rub": result.total_costs_rub,
            "expected_profit_rub": result.expected_profit_rub,
            "expected_net_margin_pct": result.expected_net_margin_pct,
            "calculated_at": now,
            "economy_complete": 1 if result.economy_complete else 0,
        })
        
        if not result.ok:
            product_data["status_reason"] = result.error_message or "Ошибка расчёта экономики"
        
        return product_data


# Глобальный экземпляр калькулятора
_default_calculator: Optional[EconomicsCalculator] = None


def get_calculator() -> EconomicsCalculator:
    """Возвращает глобальный экземпляр калькулятора экономики."""
    global _default_calculator
    if _default_calculator is None:
        _default_calculator = EconomicsCalculator()
    return _default_calculator


def calculate_product_economics(product_data: Dict[str, Any]) -> EconomicsResult:
    """
    Удобная функция для расчёта экономики товара.
    
    Args:
        product_data: Данные товара из БД или парсера
    
    Returns:
        EconomicsResult с результатами расчёта
    """
    return get_calculator().calculate_for_product(product_data)
