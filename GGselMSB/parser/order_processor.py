import sqlite3
import logging
import os
from pathlib import Path
from parser.db_init import get_db_path

log = logging.getLogger("ggselv7.order_processor")

def process_sales(sales_list: list):
    """
    Принимает список продаж из API seller-last-sales, находит соответствия в parsed_products
    и добавляет/обновляет связки в таблице order_links.
    """
    if not sales_list:
        return
    
    conn = sqlite3.connect(get_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        for sale in sales_list:
            order_id = str(sale.get("invoice_id") or sale.get("order_id") or "")
            my_offer_id = str(sale.get("goods_id") or sale.get("offer_id") or "")
            if not order_id or not my_offer_id:
                continue
                
            # Check if this order is already linked
            existing = conn.execute("SELECT 1 FROM order_links WHERE order_id = ?", (order_id,)).fetchone()
            if existing:
                continue # already processed
                
            # Find matching product in parsed_products
            product = conn.execute(
                "SELECT product_id, seller_id, source_price, sell_price, expected_profit_rub, title "
                "FROM parsed_products WHERE offer_id = ? OR product_id = ?",
                (my_offer_id, my_offer_id)
            ).fetchone()
            
            if not product:
                log.info(f"Товар с offer_id={my_offer_id} не найден в parsed_products")
                continue
                
            # We found a match!
            source_offer_id = product["product_id"]       # ID оригинала у конкурента
            source_seller_id = product["seller_id"]       # ID продавца оригинала
            source_price = product["source_price"]
            my_price = product["sell_price"] or sale.get("amount") or 0.0
            profit_rub = product["expected_profit_rub"] or (float(my_price) - float(source_price))
            
            conn.execute(
                """
                INSERT OR IGNORE INTO order_links 
                (order_id, my_offer_id, source_offer_id, source_seller_id, source_price, my_price, profit_rub, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
                """,
                (order_id, my_offer_id, source_offer_id, source_seller_id, source_price, my_price, profit_rub)
            )
            conn.commit()
            
            log.info(f"Создана связка для заказа {order_id} -> оригинал {source_offer_id}")
            
            # Отправка уведомления в Telegram через tg_bot.py
            try:
                from parser.tg_bot import send_notification_sync
                original_url = f"https://ggsel.net/goods/{source_offer_id}"
                message_text = (
                    f"🔔 <b>Новый заказ!</b>\n\n"
                    f"📦 <b>Товар:</b> {product['title']}\n"
                    f"💵 <b>Сумма:</b> {my_price} руб.\n"
                    f"💰 <b>Ожидаемая прибыль:</b> {profit_rub:.2f} руб.\n"
                    f"🔗 <a href='{original_url}'>Ссылка на оригинал</a>"
                )
                send_notification_sync(message_text, source_offer_id)
            except Exception as tg_err:
                log.warning(f"Не удалось отправить уведомление в Telegram: {tg_err}")
                
    except Exception as e:
        log.exception(f"Ошибка в process_sales: {e}")
    finally:
        conn.close()
