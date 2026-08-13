"""
parser/my_shop_scraper.py
=========================
Мигрировано из GGSeller/services/parser/my_shop_scraper.py (2026-07-27).

Парсер собственного магазина на ggsel.net (seller-side):
  - scrape_my_offers()    — список моих офферов
  - scrape_orders()       — список заказов
  - scrape_seller_avatar() — аватар продавца
  - reply_to_review()     — ответить на отзыв
  - toggle_promotion()    — вкл/выкл промо
  - run_and_save()        — скрапить и сохранить в локальную SQLite

Зависимости:
  pip install requests beautifulsoup4 apscheduler

ENV (опционально):
  MY_SHOP_DB_PATH — путь к локальной SQLite (default: shared/db/my_shop.db)
  MY_SHOP_SELLER_ID, MY_SHOP_COOKIES_JSON — для автозапуска через scheduler
"""
import time
import logging
import os
import sqlite3
import json
import requests
from bs4 import BeautifulSoup
from pathlib import Path

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

logger = logging.getLogger("ggselv7.my_shop_scraper")

# Путь к БД (configurable)
DB_PATH = Path(os.getenv("MY_SHOP_DB_PATH", "shared/db/my_shop.db"))


def init_db():
    """Создаёт таблицы для хранения скрейпнутых данных магазина."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shop_images (
            offer_id TEXT PRIMARY KEY,
            image_url TEXT,
            avatar_url TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraped_orders (
            order_id TEXT PRIMARY KEY,
            avatar_url TEXT,
            buyer_name TEXT,
            item_icon TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


class MyShopScraper:
    """Скрейпер собственного магазина через requests + cookies сессии."""

    def __init__(self, seller_id: str, cookies: dict):
        self.seller_id = seller_id
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://ggsel.net/",
        })
        self.request_count = 0

    def _safe_get(self, url: str):
        if self.request_count >= 30:
            logger.warning("Max 30 requests reached. Stopping.")
            return None
        time.sleep(2)
        self.request_count += 1
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code in (403, 429):
                logger.warning(f"Stopped parsing: received {resp.status_code}")
                return None
            if "login" in resp.url or resp.status_code == 401:
                logger.warning("Cookie истекли, обновите в настройках")
                return None
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def _safe_post(self, url: str, json_data: dict = None, data: dict = None):
        if self.request_count >= 30:
            logger.warning("Max 30 requests reached. Stopping.")
            return None
        time.sleep(2)
        self.request_count += 1
        try:
            resp = self.session.post(url, json=json_data, data=data, timeout=10)
            if resp.status_code in (403, 429):
                logger.warning(f"Stopped posting: received {resp.status_code}")
                return None
            if "login" in resp.url or resp.status_code == 401:
                logger.warning("Cookie истекли, обновите в настройках")
                return None
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.error(f"Error posting to {url}: {e}")
            return None

    def reply_to_review(self, review_id: str, text: str) -> bool:
        REVIEW_REPLY_URL = "https://ggsel.net/api/seller/review/reply"
        resp = self._safe_post(REVIEW_REPLY_URL, json_data={"review_id": review_id, "text": text})
        return resp is not None and resp.ok

    def toggle_promotion(self, offer_id: str, enable: bool) -> bool:
        PROMOTION_URL = "https://ggsel.net/api/seller/promotion/toggle"
        resp = self._safe_post(PROMOTION_URL, json_data={"offer_id": offer_id, "enable": enable})
        return resp is not None and resp.ok

    def scrape_my_offers(self) -> list:
        url = f"https://ggsel.net/seller/{self.seller_id}"
        resp = self._safe_get(url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        offers = []
        for item in soup.select(".item, .product-card"):
            link = item.select_one("a[href*='/catalog/product/'], a[href*='/product/']")
            if not link:
                continue
            href = link.get("href", "")
            offer_id = href.split("/")[-1].split("?")[0]
            img = item.select_one("img")
            image_url = (img.get("src") or img.get("data-src")) if img else ""
            title_el = item.select_one(".title, .name")
            title = title_el.text.strip() if title_el else ""
            if offer_id:
                offers.append({
                    "offer_id": offer_id,
                    "image_url": image_url,
                    "title": title,
                })
        return offers

    def scrape_orders(self) -> list:
        url = "https://seller.ggsel.com/sales"
        resp = self._safe_get(url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        orders = []
        for row in soup.select("tr.sale-row, .order-item, .table-row"):
            id_el = row.select_one(".order-id, [data-id]")
            order_id = (id_el.text.strip().replace("#", "") if id_el
                        else row.get("data-id", ""))
            avatar_el = row.select_one(".buyer-avatar img, img.avatar")
            avatar_url = avatar_el.get("src") if avatar_el else ""
            buyer_el = row.select_one(".buyer-name, .user-name")
            buyer_name = buyer_el.text.strip() if buyer_el else ""
            icon_el = row.select_one(".item-icon img, img.product-icon")
            item_icon = icon_el.get("src") if icon_el else ""
            if order_id:
                orders.append({
                    "order_id": order_id,
                    "avatar_url": avatar_url,
                    "buyer_name": buyer_name,
                    "item_icon": item_icon,
                })
        return orders

    def scrape_seller_avatar(self) -> str | None:
        url = f"https://ggsel.net/seller/{self.seller_id}"
        resp = self._safe_get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        avatar_img = soup.select_one(".seller-avatar img, .profile-avatar img")
        return avatar_img.get("src") if avatar_img else None

    def scrape_categories(self) -> list:
        return []

    def run_and_save(self):
        init_db()
        logger.info(f"Starting parsing for seller {self.seller_id}")
        avatar_url = self.scrape_seller_avatar()
        offers = self.scrape_my_offers()
        if not offers and not avatar_url:
            logger.info("Nothing parsed or cookies expired.")
            return
        conn = sqlite3.connect(DB_PATH)
        for offer in offers:
            conn.execute("""
                INSERT INTO shop_images (offer_id, image_url, avatar_url, scraped_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(offer_id) DO UPDATE SET
                    image_url=excluded.image_url,
                    avatar_url=excluded.avatar_url,
                    scraped_at=CURRENT_TIMESTAMP
            """, (str(offer["offer_id"]), offer.get("image_url", ""), avatar_url or ""))
        conn.commit()
        conn.close()
        logger.info(f"Saved {len(offers)} offers to DB.")


# ── Опциональный APScheduler-обвес (для автозапуска) ────────────────────
if _APSCHEDULER_AVAILABLE:
    scheduler = AsyncIOScheduler()
    _parsing_lock = False

    async def parse_job(seller_id: str, cookies: dict):
        global _parsing_lock
        if _parsing_lock:
            logger.info("Parsing is already running. Skipping.")
            return
        _parsing_lock = True
        try:
            scraper = MyShopScraper(seller_id, cookies)
            import asyncio
            await asyncio.to_thread(scraper.run_and_save)
        finally:
            _parsing_lock = False

    def trigger_scrape_now(seller_id: str, cookies: dict):
        import asyncio
        asyncio.create_task(parse_job(seller_id, cookies))

    def setup_scheduler(seller_id: str, cookies: dict):
        if not scheduler.running:
            scheduler.start()
        scheduler.remove_all_jobs()
        scheduler.add_job(parse_job, "interval", hours=2, args=[seller_id, cookies])
else:
    scheduler = None  # type: ignore

    async def parse_job(seller_id: str, cookies: dict):  # type: ignore
        raise RuntimeError("apscheduler не установлен — pip install apscheduler")

    def trigger_scrape_now(seller_id: str, cookies: dict):  # type: ignore
        raise RuntimeError("apscheduler не установлен")

    def setup_scheduler(seller_id: str, cookies: dict):  # type: ignore
        raise RuntimeError("apscheduler не установлен")
