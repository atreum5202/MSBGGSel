# -*- coding: utf-8 -*-
"""
parser/pricing.py
=================
Расчёт моей цены продажи на основе цены конкурента.

Комиссии берутся из cat_fees.json (генерируется fetch_all_cats.py → реальные данные V2 API).

Формула:
  total_fee    = category_fee + payment_fee  (комиссия площадки + комиссия платёжки)
  sell_price   = cost / (1 - total_fee - target_margin)
  net_profit   = sell_price * (1 - total_fee) - cost
  profit_score = net_profit * monthly_sales
"""
import os
import json
import logging
from pathlib import Path
from parser.economics import get_calculator, EconomicsConfig

log = logging.getLogger("ggselv7.pricing")

# ── Загрузка реальных комиссий из cat_fees.json ───────────────────────────
_CAT_FEES: dict = {}
_CAT_FEES_PATH = Path(__file__).resolve().parent.parent / "cat_fees.json"
_LAST_FEES_MTIME: float = 0.0


def _load_cat_fees() -> dict:
    global _LAST_FEES_MTIME
    if _CAT_FEES_PATH.exists():
        try:
            mtime = os.path.getmtime(_CAT_FEES_PATH)
            _LAST_FEES_MTIME = mtime
            with open(_CAT_FEES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            log.info("[pricing] Загружено %d комиссий из %s", len(data), _CAT_FEES_PATH)
            return data
        except Exception as e:
            log.warning("[pricing] Не удалось загрузить %s: %s", _CAT_FEES_PATH, e)
    else:
        log.warning("[pricing] %s не найден — запустите fetch_all_cats.py", _CAT_FEES_PATH)
    return {}


_CAT_FEES = _load_cat_fees()

# ── Константы ─────────────────────────────────────────────────────────────
# Комиссия площадки по умолчанию (если категория не найдена в cat_fees.json)
DEFAULT_FEE = float(os.getenv("COMMISSION_DEFAULT", "0.15"))

# Комиссия платёжной системы — константа, не отдаётся API
PAYMENT_FEE = float(os.getenv("PAYMENT_FEE_PCT", "0.027"))

# Маржа
TARGET_MARGIN = float(os.getenv("TARGET_MARGIN_PCT", "0.15"))   # целевая маржа 15%
MIN_MARGIN    = float(os.getenv("MIN_MARGIN",    "0.05"))    # минимальная маржа 5%

# Минимальная чистая прибыль в рублях (фильтр воронки)
MIN_NET_PROFIT = float(os.getenv("MIN_NET_PROFIT_RUB", "50"))

# Максимум офферов от одного продавца в топе (защита от монополии)
MAX_OFFERS_PER_SELLER = int(os.getenv("MAX_OFFERS_PER_SELLER", "5"))

# Размер топа
TOP_N = int(os.getenv("TOP_N", "100"))

# Ключевые слова для определения типа выдачи
AUTO_DELIVERY_KEYWORDS  = ["auto", "авто", "автовыдача", "24/7", "мгновенно", "instant"]
MANUAL_DELIVERY_KEYWORDS = ["ручн", "вручную", "в течение", "в ответ"]


def get_commission(category) -> float:
    """
    Возвращает комиссию площадки (category_fee) для категории.
    category: числовой ID (int/str) из V2 API или строковый slug.
    Горячая перезагрузка: если файл изменился на диске, перечитываем.
    """
    global _CAT_FEES, _LAST_FEES_MTIME
    if _CAT_FEES_PATH.exists():
        try:
            mtime = os.path.getmtime(_CAT_FEES_PATH)
            if mtime > _LAST_FEES_MTIME:
                log.info("[pricing] cat_fees.json изменился на диске, перезагружаем...")
                _CAT_FEES = _load_cat_fees()
        except Exception as e:
            log.debug("[pricing] Ошибка проверки mtime: %s", e)

    if category is None:
        return DEFAULT_FEE
    cat_key = str(category).strip()
    if cat_key in _CAT_FEES:
        return float(_CAT_FEES[cat_key])
    log.debug("[pricing] Категория %r не найдена в cat_fees → дефолт %.0f%%", category, DEFAULT_FEE * 100)
    return DEFAULT_FEE


def get_total_fee(category) -> float:
    """Полная комиссия = комиссия площадки + комиссия платёжки."""
    return get_commission(category) + PAYMENT_FEE


def calculate_my_price(source_price: float, category=None) -> float:
    """
    Цена продажи с учётом полной комиссии и целевой маржи.
    Pricing теперь тонкая обёртка над EconomicsCalculator.
    """
    if not source_price or source_price <= 0:
        return 0.0

    calc = get_calculator()
    cat_fee = get_commission(category)
    result = calc.calculate(
        source_price=source_price,
        category_fee_pct=cat_fee
    )
    return result.sell_price


def calculate_profit(sell_price: float, cost: float, category=None) -> dict:
    """
    Рассчитывает полную экономику товара с помощью EconomicsCalculator.
    """
    calc = get_calculator()
    cat_fee = get_commission(category)
    result = calc.calculate(
        source_price=cost,
        category_fee_pct=cat_fee
    )
    
    cfg = calc.config
    withdrawal_fee_pct = cfg.withdrawal_fee_pct
    payment_fee_pct = cfg.payment_fee_pct
    tax_pct = cfg.tax_pct
    risk_reserve_pct = cfg.risk_reserve_pct
    
    total_fee_pct = cat_fee + payment_fee_pct + withdrawal_fee_pct
    total_costs = cost * (1.0 + total_fee_pct + tax_pct + risk_reserve_pct) + cfg.fixed_costs_rub
    net_profit = sell_price - total_costs
    margin_rub = sell_price - cost
    margin_pct = (net_profit / sell_price * 100) if sell_price else 0.0

    return {
        "sell_price":       round(sell_price, 2),
        "cost":             round(cost, 2),
        "category_fee_pct": round(cat_fee * 100, 2),
        "payment_fee_pct":  round(payment_fee_pct * 100, 2),
        "withdrawal_fee_pct": round(withdrawal_fee_pct * 100, 2),
        "tax_pct":           round(tax_pct * 100, 2),
        "risk_reserve_pct":  round(risk_reserve_pct * 100, 2),
        "total_fee_pct":    round(total_fee_pct * 100, 2),
        "category_fee_rub": round(sell_price * cat_fee, 2),
        "payment_fee_rub":  round(sell_price * payment_fee_pct, 2),
        "withdrawal_fee_rub": round(sell_price * withdrawal_fee_pct, 2),
        "total_fee_rub":    round(sell_price * total_fee_pct, 2),
        "net_profit":       round(net_profit, 2),
        "margin_rub":       round(margin_rub, 2),
        "margin_pct":       round(margin_pct, 2),
    }


def calculate_profit_score(net_profit: float, monthly_sales: int) -> float:
    """Скор для ранжирования: чистая прибыль × объём продаж."""
    return round(net_profit * max(0, monthly_sales), 2)


# Обратная совместимость — старые вызовы calculate_margin не сломаются
def calculate_margin(my_price: float, source_price: float, category=None) -> dict:
    return calculate_profit(my_price, source_price, category)


def reload_fees():
    """Перезагружает комиссии из cat_fees.json."""
    global _CAT_FEES
    _CAT_FEES = _load_cat_fees()
    return len(_CAT_FEES)

