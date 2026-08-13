"""
bot/bot.py — aiogram 3.x Telegram бот управления GGselMSB.

Команды:
  /start        — приветствие + помощь
  /connect КОД  — привязать чат по коду из панели (Settings → Telegram)
  /disconnect   — отвязать этот чат
  /status       — статус парсера, очереди, ключей Gemini
  /queue        — товары в очереди (pending), inline кнопки одобрения
  /approve_all  — одобрить все товары с profit_score >= 80 и риском != high
  /parse        — запустить парсер вручную (через API панели)
  /pause        — поставить парсер на паузу
  /resume       — возобновить парсер
  /logs         — последние 10 ошибок из parser_log
  /help         — справка
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from bot.config import config
from bot import db

log = logging.getLogger("ggsel.bot")

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kb_panel(path: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🌐 Открыть панель",
            url=f"{config.PANEL_URL}{path}",
        )
    ]])


async def _require_linked(message: Message) -> bool:
    """Проверить, что этот чат привязан. Иначе сообщить."""
    if not db.is_connected(message.chat.id):
        await message.answer(
            "❌ Чат не привязан к панели.\n"
            "Открой панель → Settings → Telegram → нажми <b>«Получить код»</b>\n"
            "Затем отправь: <code>/connect КОД</code>",
            parse_mode="HTML",
            reply_markup=_kb_panel("/"),
        )
        return False
    return True


def _product_kb(product_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",  callback_data=f"v7_approve:{product_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"v7_reject:{product_id}"),
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"v7_skip:{product_id}"),
    ]])


# ── Commands ──────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Привет! Я бот панели GGselMSB.</b>\n\n"
        "Привязка к панели:\n"
        "1. Открой панель → Settings → Telegram\n"
        "2. Нажми <b>«Получить код»</b>\n"
        "3. Отправь: <code>/connect КОД</code>\n\n"
        "Напиши /help для списка команд.",
        parse_mode="HTML",
        reply_markup=_kb_panel("/"),
    )


@router.message(Command("connect"))
async def cmd_connect(message: Message, command: CommandObject):
    code = (command.args or "").strip()
    if not code:
        await message.answer(
            "❗ Укажи код: <code>/connect КОД</code>",
            parse_mode="HTML",
        )
        return

    shop_id = db.consume_connect_code(code)
    if not shop_id:
        await message.answer(
            "❌ Код неверный или истёк.\n"
            "Открой панель → Settings → Telegram → получи новый код."
        )
        return

    db.save_chat_id(shop_id, message.chat.id)
    await message.answer(
        "✅ <b>Чат успешно привязан к панели!</b>\n\n"
        "Теперь вы будете получать уведомления о новых товарах и ошибках.\n"
        "Напиши /status чтобы проверить состояние системы.",
        parse_mode="HTML",
        reply_markup=_kb_panel("/"),
    )


@router.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    shop = db.get_shop_by_chat_id(message.chat.id)
    if not shop:
        await message.answer("ℹ️ Этот чат не был привязан.")
        return
    db.disconnect_shop(shop["shop_id"])
    await message.answer("✅ Чат отвязан. Уведомления больше не будут приходить.")


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not await _require_linked(message):
        return

    pending  = db.get_queue_count()
    approved = db.get_approved_count()
    run      = db.get_last_parser_run()

    parser_status = run["status"] if run else "не запускался"
    parser_time   = run.get("finished_at") or run.get("started_at") if run else "—"

    # Статус ключей Gemini
    keys_text = ""
    try:
        from parser.content_gen import get_key_pool
        pool = get_key_pool()
        total = len(pool._keys)
        ok    = sum(1 for k in pool._keys if k.get("status") == "ok")
        keys_text = f"\n🔑 Ключи Gemini: {ok}/{total} доступны"
    except Exception:
        pass

    await message.answer(
        f"📊 <b>Статус системы</b>\n\n"
        f"📦 В очереди (pending): <b>{pending}</b>\n"
        f"✅ Одобрено: <b>{approved}</b>\n"
        f"⚙️ Парсер: <b>{parser_status}</b>\n"
        f"🕐 Последний запуск: {parser_time}"
        f"{keys_text}",
        parse_mode="HTML",
        reply_markup=_kb_panel("/"),
    )


@router.message(Command("queue"))
async def cmd_queue(message: Message):
    if not await _require_linked(message):
        return

    products = db.get_pending_products(limit=10)
    if not products:
        await message.answer("✅ Очередь пуста — нет товаров в ожидании.")
        return

    await message.answer(
        f"⏳ <b>Товары в очереди ({len(products)}):</b>\n"
        "Используй кнопки для одобрения или отклонения.",
        parse_mode="HTML",
    )

    for p in products:
        pid    = p.get("product_id", "")
        title  = p.get("generated_title") or p.get("title", "—")
        price  = p.get("my_price") or p.get("price")
        score  = p.get("profit_score")
        cat    = p.get("category", "")

        price_str = f"{price:.2f} ₽" if price else "—"
        score_str = f" | Score: {score:.0f}" if score else ""

        text = (
            f"📦 <b>{title}</b>\n"
            f"💰 Цена: {price_str}{score_str}\n"
            f"🏷 Категория: {cat or '—'}"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=_product_kb(pid),
        )


@router.message(Command("approve_all"))
async def cmd_approve_all(message: Message):
    if not await _require_linked(message):
        return

    products = db.get_pending_products(limit=500)
    approved = 0
    for p in products:
        score = p.get("profit_score") or 0
        tags  = (p.get("generated_tags") or "").lower()
        if score >= 80 and "risk: high" not in tags:
            db.set_product_status(p["product_id"], "approved")
            approved += 1

    await message.answer(
        f"✅ Одобрено <b>{approved}</b> из {len(products)} товаров (score ≥ 80, риск не высокий).",
        parse_mode="HTML",
    )


@router.message(Command("parse"))
async def cmd_parse(message: Message):
    if not await _require_linked(message):
        return

    await message.answer("🔄 Запускаю парсер...")
    try:
        import requests as _r
        resp = _r.post(
            f"{config.PANEL_URL}/api/parser/start",
            json={"category": "Все", "quantity": 50, "max_pages": 5},
            timeout=10,
        )
        if resp.status_code < 400:
            await message.answer(
                "✅ Парсер запущен. Результаты появятся в очереди.",
                reply_markup=_kb_panel("/"),
            )
        else:
            await message.answer(f"❌ Ошибка запуска: HTTP {resp.status_code}")
    except Exception as e:
        await message.answer(f"❌ Не удалось подключиться к панели: {e}")


@router.message(Command("pause"))
async def cmd_pause(message: Message):
    if not await _require_linked(message):
        return
    try:
        import requests as _r
        resp = _r.post(
            f"{config.PANEL_URL}/api/parser/stop",
            timeout=10,
        )
        if resp.status_code < 400:
            await message.answer("⏸ Парсер остановлен.")
        else:
            await message.answer(f"❌ Ошибка: HTTP {resp.status_code}")
    except Exception as e:
        await message.answer(f"❌ Не удалось подключиться к панели: {e}")


@router.message(Command("resume"))
async def cmd_resume(message: Message):
    if not await _require_linked(message):
        return
    try:
        import requests as _r
        resp = _r.post(
            f"{config.PANEL_URL}/api/parser/start",
            json={"category": "Все", "quantity": 50, "max_pages": 5},
            timeout=10,
        )
        if resp.status_code < 400:
            await message.answer("▶️ Парсер возобновлён.")
        else:
            await message.answer(f"❌ Ошибка: HTTP {resp.status_code}")
    except Exception as e:
        await message.answer(f"❌ Не удалось подключиться к панели: {e}")


@router.message(Command("logs"))
async def cmd_logs(message: Message):
    if not await _require_linked(message):
        return

    try:
        from bot.db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT level, message, ts FROM parser_log "
                "WHERE level IN ('error','warning') "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
    except Exception as e:
        await message.answer(f"❌ Ошибка чтения логов: {e}")
        return

    if not rows:
        await message.answer("✅ Последних ошибок нет.")
        return

    lines = ["📋 <b>Последние ошибки:</b>\n"]
    for r in rows:
        icon = "🔴" if r[0] == "error" else "🟡"
        ts   = (r[2] or "")[:16]
        msg  = (r[1] or "")[:120]
        lines.append(f"{icon} <code>[{ts}]</code> {msg}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📋 <b>Команды бота:</b>\n\n"
        "/status     — статус парсера и ключей\n"
        "/queue      — товары в очереди (до 10 шт.)\n"
        "/approve_all — одобрить все товары (score ≥ 80)\n"
        "/parse      — запустить парсер\n"
        "/pause      — остановить парсер\n"
        "/resume     — возобновить парсер\n"
        "/logs       — последние ошибки\n"
        "/disconnect — отвязать чат\n"
        "/help       — эта справка\n\n"
        "<b>Привязка:</b> /connect КОД",
        parse_mode="HTML",
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("v7_approve:"))
async def cb_approve(callback: CallbackQuery):
    pid = callback.data.split(":", 1)[1]
    await callback.answer("✅ Одобряю...")
    try:
        db.set_product_status(pid, "approved")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"✅ Товар <b>{pid}</b> одобрен.", parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")


@router.callback_query(F.data.startswith("v7_reject:"))
async def cb_reject(callback: CallbackQuery):
    pid = callback.data.split(":", 1)[1]
    await callback.answer("❌ Отклоняю...")
    try:
        db.set_product_status(pid, "rejected")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"❌ Товар <b>{pid}</b> отклонён.", parse_mode="HTML")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")


@router.callback_query(F.data.startswith("v7_skip:"))
async def cb_skip(callback: CallbackQuery):
    await callback.answer("⏭ Пропущено")
    try:
        await callback.message.delete()
    except Exception:
        pass


# ── Push-уведомления (вызываются из парсера) ──────────────────────────────────

async def notify_new_products(count: int, samples: list[dict] | None = None):
    """Уведомить о новых товарах из парсера."""
    chat_id = db.get_first_chat_id()
    if not chat_id:
        return

    text = f"🆕 <b>Парсер нашёл {count} новых товаров!</b>\n"
    if samples:
        text += "\nПримеры:\n"
        for p in samples[:3]:
            title = p.get("generated_title") or p.get("title", "—")
            price = p.get("my_price") or p.get("price")
            price_str = f" — {price:.2f} ₽" if price else ""
            text += f"• {title}{price_str}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"📋 Смотреть очередь ({count})", callback_data="noop"),
    ]])

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_kb_panel("/"),
        )
    except Exception as e:
        log.error("notify_new_products: %s", e)


async def notify_parser_error(error_msg: str):
    """Уведомить об ошибке парсера."""
    chat_id = db.get_first_chat_id()
    if not chat_id:
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"🚨 <b>Ошибка парсера:</b>\n<code>{error_msg[:400]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error("notify_parser_error: %s", e)


async def notify_session_expired():
    """Уведомить об истёкшей сессии GGsel."""
    chat_id = db.get_first_chat_id()
    if not chat_id:
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "🚨 <b>Сессия GGsel истекла!</b>\n\n"
                "Необходимо обновить cookies или токен.\n"
                "Зайди в панель → Parser → Cookies."
            ),
            parse_mode="HTML",
            reply_markup=_kb_panel("/"),
        )
    except Exception as e:
        log.error("notify_session_expired: %s", e)


async def notify_text(text: str, level: str = "info"):
    """Отправить произвольное уведомление (вызывается из watchdog и парсера)."""
    chat_id = db.get_first_chat_id()
    if not chat_id:
        return
    icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(level, "ℹ️")
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"{icon} {text}",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error("notify_text: %s", e)


def send_notification_sync(message_text: str, product_id: str = None, level: str = "info"):
    """
    Синхронная отправка (для вызова из синхронного кода парсера).
    Использует requests напрямую через Bot API.
    """
    from bot.config import config as _cfg
    import requests as _r

    token = _cfg.TELEGRAM_BOT_TOKEN
    chat_id = db.get_first_chat_id()

    if not token or not chat_id:
        # Fallback: читаем из env если DB пустая
        import os
        chat_id = chat_id or int(os.getenv("TELEGRAM_CHAT_ID", "0") or 0)

    if not token or not chat_id:
        return

    icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(level, "ℹ️")
    text = f"{icon} {message_text}"

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if product_id:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "✅ Одобрить", "callback_data": f"v7_approve:{product_id}"},
                {"text": "❌ Отклонить", "callback_data": f"v7_reject:{product_id}"},
            ]]
        }
    try:
        _r.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=5,
        )
    except Exception as e:
        log.error("send_notification_sync: %s", e)


# ── Фоновая задача: таймаут одобрения ────────────────────────────────────────

async def _timeout_checker():
    """Каждые 6 часов: товары в pending > APPROVAL_TIMEOUT_HOURS → rejected."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            from bot.db import _conn
            cutoff = (
                datetime.utcnow() - timedelta(hours=config.APPROVAL_TIMEOUT_HOURS)
            ).strftime("%Y-%m-%d %H:%M:%S")
            with _conn() as conn:
                rows = conn.execute(
                    "SELECT product_id FROM parsed_products "
                    "WHERE status='pending' AND created_at < ?",
                    (cutoff,)
                ).fetchall()
                if rows:
                    ids = [r[0] for r in rows]
                    conn.execute(
                        f"UPDATE parsed_products SET status='timeout' "
                        f"WHERE product_id IN ({','.join('?'*len(ids))})",
                        ids,
                    )
                    conn.commit()
                    await notify_text(
                        f"⏰ Таймаут: {len(ids)} товаров переведены в timeout "
                        f"(не одобрены за {config.APPROVAL_TIMEOUT_HOURS}ч)",
                        level="warning",
                    )
        except Exception as e:
            log.error("[timeout_checker] %s", e)


async def on_startup():
    asyncio.create_task(_timeout_checker())


dp.startup.register(on_startup)
