"""
bot/config.py — конфигурация Telegram-бота GGselMSB из .env
"""
import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=False)


@dataclass
class BotConfig:
    TELEGRAM_BOT_TOKEN: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    TELEGRAM_CHAT_ID: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )
    # URL панели (Flask на :5000)
    PANEL_URL: str = field(
        default_factory=lambda: os.getenv("PANEL_URL", "http://localhost:5000")
    )
    # Ограничение таймаута одобрения в часах
    APPROVAL_TIMEOUT_HOURS: int = field(
        default_factory=lambda: int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))
    )


config = BotConfig()
