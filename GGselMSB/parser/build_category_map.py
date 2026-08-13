"""
parser/build_category_map.py
=============================
Скрипт для прохода по всем slug из category_slugs, загрузки их HTML-страниц
и извлечения числового ID категории из JSON-структур Next.js.
Найденный ID используется для матчинга с таблицей categories и получения комиссии (fee).
"""
import asyncio
import logging
import re
import sqlite3
import time

from curl_cffi import requests as cffi

from .db_init import get_db_path
from .msb_cookies import QratorCookieMiddleware

log = logging.getLogger("ggselv7.build_category_map")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    db_path = get_db_path()

    # 1. Загружаем slugs, у которых еще нет актуального ID (например, id < 0) или нужно обновить
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    # Берем все slugs, чтобы собрать 100% карту, либо только те, где fee IS NULL
    # Возьмем пока все, где id < 0 (без точного матча) или fee IS NULL
    rows = conn.execute("""
        SELECT _rowid_, id, slug, title 
        FROM category_slugs 
        WHERE slug != '' AND (id <= 0 OR fee IS NULL)
    """).fetchall()
    
    slugs = [dict(r) for r in rows]
    if not slugs:
        # Если все id > 0, возьмем просто все
        rows = conn.execute("SELECT _rowid_, id, slug, title FROM category_slugs WHERE slug != ''").fetchall()
        slugs = [dict(r) for r in rows]
        
    log.info("К обработке %d slug", len(slugs))

    # 2. Инициализация сессии и кук
    mw = QratorCookieMiddleware()
    try:
        cookies = await mw.cookies()
    except Exception as e:
        log.warning("Не удалось получить куки: %s", e)
        cookies = {}

    session = cffi.Session(impersonate="chrome131")
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="ggsel.net")

    # Регулярка для поиска ID внутри Next.js JSON
    # Пример: "id":8930,"digi_catalog":33760,"name":"Spotify Premium"
    id_pattern = re.compile(r'"id"\s*:\s*(\d+),\s*"digi_catalog"\s*:\s*\d+')

    matched_count = 0
    errors_count = 0

    for i, item in enumerate(slugs, 1):
        slug = item["slug"]
        rowid = item["id"]
        url = f"https://ggsel.net/catalog/{slug}"

        try:
            r = session.get(url, timeout=15)
            if r.status_code != 200:
                log.warning("[%d/%d] %s: HTTP %d", i, len(slugs), slug, r.status_code)
                errors_count += 1
                await asyncio.sleep(2)
                continue

            match = id_pattern.search(r.text)
            if match:
                cat_id = int(match.group(1))
                # Ищем этот ID в старой таблице categories
                cat_row = conn.execute("SELECT fee, full_path FROM categories WHERE id=?", (cat_id,)).fetchone()
                
                fee = cat_row["fee"] if cat_row else 0.15
                full_path = cat_row["full_path"] if cat_row else item["title"]

                # Обновляем БД (сохраняем после каждого)
                conn.execute("""
                    UPDATE category_slugs 
                    SET id=?, fee=?, full_path=? 
                    WHERE id=?
                """, (cat_id, fee, full_path, rowid))
                conn.commit()

                matched_count += 1
                log.info("[%d/%d] MATCHED %s -> ID=%d, fee=%.2f", i, len(slugs), slug, cat_id, fee)
            else:
                log.warning("[%d/%d] NO ID FOUND IN HTML: %s", i, len(slugs), slug)
                errors_count += 1

            await asyncio.sleep(1)  # Защита от бана

        except Exception as e:
            log.error("[%d/%d] Ошибка %s: %s", i, len(slugs), slug, e)
            errors_count += 1
            await asyncio.sleep(3)

    conn.close()
    log.info("=== Готово! Обработано %d, проматчено %d, ошибок %d ===", len(slugs), matched_count, errors_count)

if __name__ == "__main__":
    asyncio.run(main())
