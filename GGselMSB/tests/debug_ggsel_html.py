"""Диагностика: что реально приходит от ggsel через MSB."""
import asyncio
import sys
import io
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.msb_fetcher import MsbFetcher
from parser.profile_pool import get_pool


async def main():
    pool = await get_pool()
    fetcher = MsbFetcher(pool=pool)
    try:
        result = await fetcher.fetch("https://ggsel.net/catalog/games")
        print(f"Success: {result.success}")
        print(f"Status: {result.status_code}")
        print(f"Profile: {result.profile_id}")
        print(f"Captcha: {result.captcha_detected}")
        print(f"Strategy: {result.strategy}")
        print(f"Source: {result.cookies_source}")
        print(f"Error: {result.error}")
        print(f"HTML length: {len(result.html)}")
        print()
        print("─" * 60)
        print("First 2000 chars of HTML:")
        print("─" * 60)
        print(result.html[:2000])
        print()
        print("─" * 60)
        print("Поиск маркеров:")
        markers = [
            "ProductCard", "productCard", "product-card",
            "qrator", "qauth", "challenge", "Just a moment",
            "g-recaptcha", "hcaptcha",
            "404", "captcha", "forbidden", "Доступ",
        ]
        for m in markers:
            count = result.html.lower().count(m.lower())
            print(f"  {m:<20}  {count} раз(а)")
    finally:
        await fetcher.close()


asyncio.run(main())
