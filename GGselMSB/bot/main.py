"""
bot/main.py — точка запуска Telegram бота GGselMSB.

Запуск: python -m bot.main  (из корня проекта)
   или: python bot/main.py
"""
import asyncio
import logging
import sys
from pathlib import Path

# Корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).resolve().parent.parent / "logs" / "bot.log",
            encoding="utf-8",
        ),
    ],
)

from bot.bot import bot, dp
from bot.config import config


async def heartbeat():
    """Каждые 60с проверяем что Flask панель доступна."""
    import requests as _r
    while True:
        try:
            _r.get(f"{config.PANEL_URL}/api/balance", timeout=5)
        except Exception:
            pass
        await asyncio.sleep(60)


async def main():
    if not config.TELEGRAM_BOT_TOKEN:
        logging.error(
            "TELEGRAM_BOT_TOKEN не задан в .env — бот не может запуститься.\n"
            "Добавь строку: TELEGRAM_BOT_TOKEN=<твой_токен>"
        )
        sys.exit(1)

    logging.info("GGselMSB Bot запускается...")
    asyncio.create_task(heartbeat())

    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
