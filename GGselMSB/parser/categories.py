"""
parser/categories.py
====================
Справочник категорий ggsel.net.

Источники данных (в data/):
  category_map.json      — slug каталога  →  id_section (93 записи)
  seller_categories.json — id_section     →  {title, content_type, fee, tree, has_children}

content_type (строка) соответствует slug из /main/content-types API:
  keys, accounts, currency, dlc, item, service, rent, gifts, ...

Использование:
    from parser.categories import get_category_info, slug_to_section_id, all_slugs

    info = get_category_info("spotify-premium")
    # {
    #   "slug":         "spotify-premium",
    #   "id_section":   108961,
    #   "title":        "Spotify Premium",
    #   "content_type": "subscription-services",
    #   "content_type_id": 18,          # числовой ID для API
    #   "fee":          0.15,
    #   "tree":         "...",
    #   "has_children": False,
    # }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("ggselparser.categories")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── Жёстко известный маппинг content_type → числовой id из /main/content-types
# Получен запросом GET https://api.ggsel.com/main/content-types
# Маппинг content_type (строка из seller_categories.json) → числовой content_type_id из API
CONTENT_TYPE_IDS: Dict[str, int] = {
    # ── Основные 19 категорий full-scan ──────────────────────────────────────
    "keys":                     2,
    "gifts":                    48,
    "dlc":                      19,
    "Purchase-to-your-account": 54,
    "accounts":                 1,
    "item":                     10,
    "rent":                     25,
    "Activation":               33,
    "currency":                 9,
    "cards":                    8,
    "service":                  11,
    "subscription-services":    18,
    "bonus-codes":              6,
    "gift-card":                52,
    "promocods":                26,
    "qr-code":                  62,
    "as-a-gift":                53,
    "purchasing-subscription":  57,
    "server-hostings":          55,
    # ── Дополнительные ──────────────────────────────────────────────────────
    "game":                     12,
    "offline-accounts":         31,
    "random-keys":              30,
    "items-221":                51,
    "random-account":           29,
    "soft":                     23,
    "ready-made-subscription":  58,
    "sticker":                  61,
    "course":                   63,
    "other":                    5,
    "books-213":                49,
    "platform":                 13,
    "physical-goods":           17,
    "popolnenie":               50,
}

# Сколько товаров в каждой верхней категории (из замера 2026-08-14)
CONTENT_TYPE_TOTALS: Dict[int, int] = {
    2:  82_065,   # keys
    48: 78_382,   # gifts
    19: 51_801,   # dlc
    54: 35_024,   # Purchase-to-your-account
    1:  26_754,   # accounts
    10: 11_708,   # item
    25: 10_104,   # rent
    33: 10_072,   # Activation
    9:   8_730,   # currency
    8:   7_810,   # cards
    11:  3_832,   # service
    18:  3_497,   # subscription-services
    6:   1_475,   # bonus-codes
    52:  1_605,   # gift-card
    26:    600,   # promocods
    62:    598,   # qr-code
    42:    741,   # Actia/Sale
    57:    272,   # purchasing-subscription
    55:    105,   # server-hostings
    63:      8,   # course
    49:      8,   # books
    23:      1,   # soft
    56:     30,   # gifts (CN)
    53:     15,   # as-a-gift
}


# ── Кеш ────────────────────────────────────────────────────────────────────────
_slug_to_section:  Optional[Dict[str, int]]  = None   # slug → id_section
_section_to_cat:   Optional[Dict[int, dict]] = None   # id_section → запись


def _load() -> None:
    global _slug_to_section, _section_to_cat

    if _slug_to_section is not None:
        return

    # category_map.json
    cmap_path = _DATA_DIR / "category_map.json"
    if cmap_path.exists():
        raw = json.loads(cmap_path.read_text(encoding="utf-8"))
        _slug_to_section = {k: int(v) for k, v in raw.items() if k}
    else:
        log.warning("category_map.json не найден: %s", cmap_path)
        _slug_to_section = {}

    # seller_categories.json
    scat_path = _DATA_DIR / "seller_categories.json"
    if scat_path.exists():
        cats = json.loads(scat_path.read_text(encoding="utf-8"))
        _section_to_cat = {int(c["id"]): c for c in cats if c.get("id")}
    else:
        log.warning("seller_categories.json не найден: %s", scat_path)
        _section_to_cat = {}

    log.info(
        "Категории загружены: %d slugs, %d sections",
        len(_slug_to_section), len(_section_to_cat),
    )


# ── Публичный API ───────────────────────────────────────────────────────────────

def all_slugs() -> List[str]:
    """Все известные slugs каталога."""
    _load()
    return sorted(s for s in _slug_to_section if s)


def slug_to_section_id(slug: str) -> Optional[int]:
    """slug → id_section. None если slug неизвестен."""
    _load()
    return _slug_to_section.get(slug)


def section_to_info(section_id: int) -> Optional[dict]:
    """id_section → полная запись из seller_categories."""
    _load()
    return _section_to_cat.get(section_id)


def get_category_info(slug: str) -> Optional[dict]:
    """
    Полная информация о категории по slug.

    Возвращает dict:
      slug, id_section, title, content_type, content_type_id,
      fee, tree, has_children
    Или None если slug неизвестен.
    """
    _load()
    sid = _slug_to_section.get(slug)
    if sid is None:
        return None

    cat = _section_to_cat.get(sid, {})
    ct  = cat.get("content_type", "")
    return {
        "slug":            slug,
        "id_section":      sid,
        "title":           cat.get("title", slug),
        "content_type":    ct,
        "content_type_id": CONTENT_TYPE_IDS.get(ct),
        "fee":             cat.get("fee"),
        "tree":            cat.get("tree", ""),
        "has_children":    cat.get("has_children", False),
    }


def slug_to_content_type_id(slug: str) -> Optional[int]:
    """
    Главное для парсера: slug → числовой content_type_id для API-фильтра.
    Используется в /elastic/goods/categories как content_type_ids=[N].
    """
    info = get_category_info(slug)
    if info:
        return info.get("content_type_id")
    return None


def all_categories_table() -> List[dict]:
    """
    Таблица всех известных категорий — для отображения и выбора.
    Отсортировано по количеству товаров (убывание).
    """
    _load()
    rows = []
    for slug, sid in sorted(_slug_to_section.items()):
        cat = _section_to_cat.get(sid, {})
        ct  = cat.get("content_type", "")
        ctid = CONTENT_TYPE_IDS.get(ct)
        rows.append({
            "slug":            slug,
            "id_section":      sid,
            "title":           cat.get("title", slug),
            "content_type":    ct,
            "content_type_id": ctid,
            "total_approx":    CONTENT_TYPE_TOTALS.get(ctid, 0) if ctid else 0,
            "fee":             cat.get("fee"),
            "tree":            cat.get("tree", ""),
            "has_children":    cat.get("has_children", False),
        })
    return sorted(rows, key=lambda r: -r["total_approx"])
