"""
enrich_products.py - zapolnyaet probely v parsed_products cherez pryamye HTTP-zaprosy.

Chto delatet dlya kazhdogo tovara gde original_desc IS NULL:
  1. GET /goods/{id}/price       -> price, count (quantity_available)
  2. GET /elastic/goods/{id}     -> polnaya kartochka (esli est' endpoint)
  3. Parsonit' properties        -> region/platform/edition
  4. Parsonit' images            -> images_json
  5. Parsonit' description      -> original_desc
  6. UPDATE parsed_products

Bez brauzera. Bez MCP. Tol'ko Bearer token iz data/ggsel_tokens.json.

Zapusk: python enrich_products.py [--limit 100] [--offset 0] [--concurrency 5]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

DB_PATH = PROJECT / "data" / "db" / "parser.db"
TOKENS_PATH = PROJECT / "data" / "ggsel_tokens.json"
API_BASE = "https://api.ggsel.com"

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = PROJECT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("enrich")
log.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
fh = logging.FileHandler(LOG_DIR / "enrich.log", encoding="utf-8")
fh.setFormatter(fmt)
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
log.addHandler(sh)


# ── Token ────────────────────────────────────────────────────────────
def _load_token() -> str:
    if not TOKENS_PATH.exists():
        return ""
    try:
        return json.loads(TOKENS_PATH.read_text(encoding="utf-8")).get("access_token", "")
    except Exception:
        return ""


TOKEN = _load_token()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; GGSellerEnricher/1.0)",
}


# ── HTTP helper ─────────────────────────────────────────────────────
def _http_get(path: str, timeout: int = 10, retries: int = 3) -> dict | list | None:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    last_err = None
    for attempt in range(retries):
        req = Request(url, headers=HEADERS)
        try:
            with urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                # rate limit ili vremennaya oshibka - zhdём i povtoryaem
                wait = (2 ** attempt) + 1  # 2, 3, 5 sec
                log.debug("HTTP %d, retry %d/%d posle %ds", e.code, attempt+1, retries, wait)
                time.sleep(wait)
                last_err = e
                continue
            log.debug("HTTP %d %s: %s", e.code, path, e)
            return None
        except Exception as e:
            log.debug("GET %s error: %s", path, e)
            return None
    log.debug("Vse %d popytok dlya %s ischerpany: %s", retries, path, last_err)
    return None


# ── Region / platform / edition ─────────────────────────────────────
REGION_RE = re.compile(
    r"\b(TR|AR|EG|RU|UA|IN|CN|TR|USA|US|EU|Global|Worldwide|Region Free|"
    r"Turkey|Argentina|Egypt|Russia|India|China|Ukraine|Europe|Asia)\b",
    re.IGNORECASE,
)
PLATFORM_RE = re.compile(
    r"\b(Xbox|PS[345]|PlayStation|PC|Steam|Origin|Epic|Uplay|GOG|"
    r"Nintendo|Switch|iOS|Android|Mobile)\b", re.IGNORECASE,
)
EDITION_RE = re.compile(
    r"\b(Standard|Deluxe|Gold|Premium|Ultimate|Complete|Collection|"
    r"Bundle|Digital|Physische|Key|Account|Edition)\b", re.IGNORECASE,
)


def _normalize(value: str) -> str:
    if not value:
        return ""
    return value.strip().title()


def extract_meta(text: str) -> dict:
    """Vytyagivaem region/platform/edition iz teksta."""
    region = platform = edition = ""
    if text:
        m = REGION_RE.search(text)
        if m: region = m.group(1)
        m = PLATFORM_RE.search(text)
        if m: platform = m.group(1)
        m = EDITION_RE.search(text)
        if m: edition = m.group(1)
    return {
        "region":   _normalize(region),
        "platform": _normalize(platform),
        "edition":  _normalize(edition),
    }


# ── Enrichment for one product ─────────────────────────────────────
def enrich_one(product_id: str) -> dict | None:
    """Vozvraschaet dict s polyami dlya UPDATE, ili None esli nichego ne nashlos."""
    result = {}

    # 1. Price + count
    data = _http_get(f"/goods/{product_id}/price?currency=wmz")
    if data and isinstance(data, dict):
        d = data.get("data") or {}
        if isinstance(d, dict):
            if "amount" in d and d["amount"]:
                result["sell_price"] = float(d["amount"])
            if "count" in d and d["count"] is not None:
                result["quantity_available"] = int(d["count"])

    # 2. Product details (poprobuem neskol'ko endpointov)
    detail = None
    for path in (
        f"/elastic/goods/{product_id}",
        f"/goods/{product_id}",
        f"/elastic/goods/{product_id}/full",
    ):
        data = _http_get(path)
        if data and isinstance(data, dict) and data.get("data"):
            detail = data["data"]
            break

    if isinstance(detail, dict):
        # description
        desc = detail.get("description") or detail.get("desc") or ""
        if desc and len(desc) > 20:
            result["original_desc"] = desc[:5000]

        # images
        images = detail.get("images") or detail.get("photos") or []
        if isinstance(images, list) and images:
            result["images_json"] = json.dumps(images, ensure_ascii=False)
        elif isinstance(images, dict):
            arr = list(images.values())
            if arr: result["images_json"] = json.dumps(arr, ensure_ascii=False)

        # properties
        props = detail.get("properties") or detail.get("options") or {}
        if isinstance(props, dict) and props:
            result["properties_json"] = json.dumps(props, ensure_ascii=False)
        elif isinstance(props, list) and props:
            result["properties_json"] = json.dumps(props, ensure_ascii=False)

        # delivery
        auto = detail.get("autoselling")
        if auto is True:
            result["delivery_type"] = "auto"
        elif auto is False:
            result["delivery_type"] = "manual"

    # 3. Fallback meta - vytyagivaem iz name + search_title
    return result if result else None


# ── Apply to one product (gets name/search_title) ──────────────────
def apply_meta_from_text(result: dict, name: str, search_title: str) -> dict:
    """Esli net region/platform iz API, vytyagivaem iz teksta."""
    text = f"{name or ''} {search_title or ''}"
    if "region" not in result or not result.get("region"):
        m = REGION_RE.search(text)
        if m: result["region"] = _normalize(m.group(1))
    if "platform" not in result or not result.get("platform"):
        m = PLATFORM_RE.search(text)
        if m: result["platform"] = _normalize(m.group(1))
    if "edition" not in result or not result.get("edition"):
        m = EDITION_RE.search(text)
        if m: result["edition"] = _normalize(m.group(1))
    return result


# ── DB ops ──────────────────────────────────────────────────────────
def get_pending(conn: sqlite3.Connection, limit: int, offset: int) -> list:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT product_id, title, search_title
        FROM parsed_products
        WHERE original_desc IS NULL OR original_desc = ''
        ORDER BY last_parsed_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    return [dict(r) for r in cur.fetchall()]


def update_product(conn: sqlite3.Connection, product_id: str, fields: dict):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [datetime.utcnow().isoformat(), product_id]
    conn.execute(f"UPDATE parsed_products SET {cols}, updated_at = ? WHERE product_id = ?", vals)
    conn.commit()


# ── Main loop ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100, help="Сколько товаров обработать")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Параллельных запросов (1 = bezopasno, 2-3 = bystro)")
    parser.add_argument("--delay", type=float, default=0.4,
                        help="Pauza mezhdu zaprosami v sekundakh (default 0.4)")
    parser.add_argument("--dry-run", action="store_true", help="Только собрать, не писать в БД")
    args = parser.parse_args()

    if not TOKEN:
        log.error("Token ne zagruzhen. Prover data/ggsel_tokens.json")
        sys.exit(1)

    log.info("=== ENRICH START ===")
    log.info("limit=%d offset=%d concurrency=%d dry_run=%s",
             args.limit, args.offset, args.concurrency, args.dry_run)

    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    pending = get_pending(conn, args.limit, args.offset)
    log.info("Pending to enrich: %d", len(pending))

    if not pending:
        log.info("Nichego delat, vse obogoshcheno")
        return

    stats = {"ok": 0, "partial": 0, "skip": 0, "errors": 0}

    def _process(item):
        try:
            # Pomezhdu zaprosami delay pauzu chtoby ne ulozhit' rate limit
            time.sleep(args.delay)
            upd = enrich_one(item["product_id"])
            if upd:
                upd = apply_meta_from_text(upd, item.get("title", ""), item.get("search_title", ""))
                if not args.dry_run:
                    update_product(conn, item["product_id"], upd)
                return ("ok", len(upd))
            return ("skip", 0)
        except Exception as e:
            log.debug("Error for %s: %s", item["product_id"], e)
            return ("error", 0)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(_process, p): p for p in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            status, count = fut.result()
            if status == "ok":
                if count >= 4: stats["ok"] += 1
                else: stats["partial"] += 1
            elif status == "skip":
                stats["skip"] += 1
            else:
                stats["errors"] += 1
            if i % 10 == 0 or i == len(pending):
                log.info("Progress %d/%d  ok=%d partial=%d skip=%d err=%d",
                         i, len(pending), stats["ok"], stats["partial"],
                         stats["skip"], stats["errors"])

    log.info("=== ENRICH DONE ===  %s", stats)


if __name__ == "__main__":
    main()
