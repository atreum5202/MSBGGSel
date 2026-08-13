import os
import sys
import asyncio
import logging
import sqlite3
import threading
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

log = logging.getLogger("ggsel.bot")
log.setLevel(logging.INFO)

# Router
router = Router()

def get_db_conn():
    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "data" / "db" / "parser.db"
    return sqlite3.connect(str(db_path))

# ─── Commands ─────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Привет! Я бот управления магазином GGselV7.</b>\n\n"
        "Я буду присылать уведомления о новых товарах.\n"
        "Команды:\n"
        "  /status — статус парсера и ключей\n"
        "  /approve_all — одобрить все товары с profit_score >= 80 и risk != high\n",
        parse_mode="HTML"
    )

@router.message(Command("status"))
async def cmd_status(message: Message):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Товары в очереди
        c.execute("SELECT COUNT(*) FROM parsed_products WHERE status='pending'")
        pending_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM parsed_products WHERE status='approved'")
        approved_count = c.fetchone()[0]
        
        c.execute("SELECT status FROM parser_runs ORDER BY run_id DESC LIMIT 1")
        last_run = c.fetchone()
        parser_status = last_run[0] if last_run else "не запускался"
        
        conn.close()
        
        # Check Gemini keys
        from parser.content_gen import get_key_pool
        pool = get_key_pool()
        keys_total = len(pool._keys)
        keys_ok = sum(1 for k in pool._keys if k.get("status") == "ok")
        
        await message.answer(
            f"📊 <b>Статус системы</b>\n\n"
            f"📦 В очереди: {pending_count}\n"
            f"✅ Одобрено: {approved_count}\n"
            f"⚙️ Статус парсера: {parser_status}\n\n"
            f"🔑 Ключи Gemini: {keys_ok}/{keys_total} доступны",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"Error in /status: {e}")
        await message.answer(f"❌ Ошибка получения статуса: {e}")

@router.message(Command("approve_all"))
async def cmd_approve_all(message: Message):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        c.execute("SELECT product_id, generated_tags FROM parsed_products WHERE status='pending' AND profit_score >= 80")
        rows = c.fetchall()
        
        approved = 0
        for pid, tags in rows:
            risk = "low"
            if tags:
                # Basic risk parse from tags, assuming "risk: high" format
                if "risk: high" in tags.lower():
                    risk = "high"
            
            if risk != "high":
                c.execute("UPDATE parsed_products SET status='approved' WHERE product_id=?", (pid,))
                approved += 1
                
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Успешно одобрено {approved} товаров с хорошей маржой и низким риском.")
    except Exception as e:
        log.error(f"Error in /approve_all: {e}")
        await message.answer(f"❌ Ошибка: {e}")


# ─── Callbacks ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("approve_"))
async def cb_approve(query: CallbackQuery):
    pid = query.data.split("_")[1]
    conn = get_db_conn()
    conn.execute("UPDATE parsed_products SET status='approved' WHERE product_id=?", (pid,))
    conn.commit()
    conn.close()
    
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.reply(f"✅ Товар {pid} одобрен.")
    await query.answer()

@router.callback_query(F.data.startswith("reject_"))
async def cb_reject(query: CallbackQuery):
    pid = query.data.split("_")[1]
    conn = get_db_conn()
    conn.execute("UPDATE parsed_products SET status='rejected' WHERE product_id=?", (pid,))
    conn.commit()
    conn.close()
    
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.reply(f"❌ Товар {pid} отклонен.")
    await query.answer()


# ─── Entrypoint ───────────────────────────────────────────────────────────

def start_bot():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN is missing. Bot will not start.")
        return

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    
    # Check chat id
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        async def on_startup():
            try:
                await bot.send_message(chat_id=chat_id, text="🚀 Бот запущен! GGSellerV7 онлайн.")
            except Exception as e:
                log.error(f"Failed to send startup message: {e}")
        dp.startup.register(on_startup)
    
    log.info("Starting Telegram Bot polling...")
    asyncio.run(dp.start_polling(bot))

def start_bot_in_thread():
    thread = threading.Thread(target=start_bot, daemon=True, name="TgBotThread")
    thread.start()
    return thread

def send_notification_sync(message: str, product_id: str = None):
    """
    Utility for the parser to send notifications synchronously.
    If product_id is provided, includes inline buttons for Approve/Reject.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        return
        
    import requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    if product_id:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {"text": "✅ Одобрить", "callback_data": f"approve_{product_id}"},
                    {"text": "❌ Отклонить", "callback_data": f"reject_{product_id}"}
                ]
            ]
        }
        
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        log.error(f"Failed to send notification: {e}")

if __name__ == "__main__":
    start_bot()
