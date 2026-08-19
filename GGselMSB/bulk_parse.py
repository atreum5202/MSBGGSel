"""
bulk_parse.py — параллельная выкачка всей базы ggsel.net.
4 воркера одновременно → ~4x быстрее.

Запуск:        python bulk_parse.py
С воркерами:   python bulk_parse.py --workers 6
С позиции:     python bulk_parse.py --start-from 500
Только N кат:  python bulk_parse.py --limit 20
"""
import asyncio, json, logging, argparse, re, sqlite3, time, sys
from pathlib import Path
from datetime import datetime

import httpx
from parser.category_resolver import find_seller_category_id

logging.basicConfig(
    level=logging.WARNING,  # подавляем httpx и прочие
    format="%(asctime)s %(message)s"
)
_log = logging.getLogger("bulk")
_log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
# Принудительно UTF-8 на Windows (иначе CP1251 → ромбики)
if hasattr(_handler.stream, 'reconfigure'):
    try:
        _handler.stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_log.addHandler(_handler)
_log.propagate = False

API_BASE  = "https://api.ggsel.com"
PER_PAGE  = 50
MAX_PAGES = 10   # макс 500 товаров на категорию
IMG_DIR   = Path("static/products")
IMG_DIR.mkdir(parents=True, exist_ok=True)

# ── Токен ─────────────────────────────────────────────────────────────────────
# --token-index 0  → первый токен (default, ggsel_parser_1)
# --token-index 1  → второй токен (ggsel_parser_2, из extra_tokens[0])

def _load_token(index: int = 0) -> str:
    _TOKEN_FILE = Path("data/ggsel_tokens.json")
    try:
        data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        if index == 0:
            return data["access_token"]
        extras = data.get("extra_tokens", [])
        if index - 1 < len(extras):
            return extras[index - 1]["access_token"]
    except Exception as e:
        print(f"[WARN] Не удалось загрузить токен #{index}: {e}")
    from parser.ggsel_api_client import get_client
    return get_client()._access_token

# Токен выбирается после парсинга --token-index (до main чтобы HEADERS был готов)
import sys as _sys
_tok_idx = 0
for _i, _a in enumerate(_sys.argv):
    if _a == '--token-index' and _i + 1 < len(_sys.argv):
        try: _tok_idx = int(_sys.argv[_i + 1])
        except: pass

TOKEN   = _load_token(_tok_idx)
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}

# ── БД ────────────────────────────────────────────────────────────────────────

from parser.db_init import get_db_path
DB_PATH = get_db_path()
_db_lock = asyncio.Lock()

def _db_save(items: list, cat_tree: str, cat_fee: float, cat_slug: str = "") -> int:
    """Синхронная запись батча в SQLite. Вызывается в thread pool."""
    if not items:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=DELETE")  # без WAL — пишем сразу в файл
    conn.execute("PRAGMA synchronous=NORMAL")
    now = datetime.utcnow().isoformat()
    saved = 0

    # Резолвим seller_id один раз на весь батч (slug категории одинаков для всех items)
    seller_cat_id = find_seller_category_id(cat_slug) if cat_slug else None

    for item in items:
        pid   = str(item.get("id_goods", ""))
        name  = item.get("name", "")
        if not pid or not name:
            continue
        wmr   = float(item.get("price_wmr") or item.get("price_wmr_for_one") or 0)
        wmz   = float(item.get("price_wmz") or item.get("price_wmz_for_one") or 0)
        img   = item.get("image", "")
        slug  = item.get("url", "")
        url   = f"https://ggsel.net/en/catalog/product/{slug}" if slug else ""
        seller= item.get("seller_name", "")
        sales = int(item.get("cnt_sell") or 0)
        active= int(bool(item.get("is_active", True)))
        fee   = cat_fee or 0.15
        my_p  = round(wmr * (1 + fee), 2)
        score = min(100.0, sales / 10.0) * (1.0 if active else 0.3)
        try:
            conn.execute("""
                INSERT INTO parsed_products
                    (product_id, title, original_title, price, currency, my_price,
                     source_price, category, category_id, url, seller_name, sales_count,
                     image_url, in_stock, profit_score, status, approval_status,
                     last_parsed_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending','pending',?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET
                    price          = excluded.price,
                    my_price       = excluded.my_price,
                    sales_count    = excluded.sales_count,
                    image_url      = CASE WHEN excluded.image_url!='' THEN excluded.image_url
                                         ELSE parsed_products.image_url END,
                    profit_score   = excluded.profit_score,
                    category_id    = CASE WHEN excluded.category_id IS NOT NULL
                                         THEN excluded.category_id
                                         ELSE parsed_products.category_id END,
                    last_parsed_at = excluded.last_parsed_at,
                    updated_at     = excluded.updated_at
            """, (pid, name, name, wmr, "RUB", my_p, wmz,
                  cat_tree[:80], seller_cat_id, url, seller, sales, img, active, score,
                  now, now, now))
            saved += 1
        except Exception:
            pass
        # FIX 2026-08-16: После list-save делаем detail-fetch для original_desc + breadcrumb.
        # List API их не возвращает — нужен GET /goods/{id}.
        # Без rate-limiter'а: ~200ms на запрос; 1.7M товаров = ~95 часов на всех.
        # Рекомендация: запускать только для товаров с sales >= 100 (top sellers).
        if sales >= 100:
            try:
                _enrich_product_detail(pid)
            except Exception:
                pass
    conn.commit()
    # Флашим WAL сразу чтобы данные были видны и не терялись при kill
    try:
        conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
    except Exception:
        pass
    conn.close()
    return saved

def _enrich_product_detail(pid: str) -> None:
    """FIX 2026-08-16: detail-fetch через GET /goods/{id} — заполняет original_desc + breadcrumb.
    List API (get_products) их не возвращает, без этого UI показывает "Описание отсутствует".
    Стоимость: 1 доп. HTTP запрос ~200ms. Запускается только для sales >= 100 (top sellers).
    """
    try:
        r = httpx.get(
            f"{API_BASE}/goods/{pid}",
            headers=HEADERS,
            params={"lang": "ru"},
            timeout=10,
        )
        if r.status_code != 200:
            return
        data = r.json().get("data") or {}
    except Exception:
        return
    info = (data.get("info") or "").strip()
    if not info:
        return
    # Breadcrumb из category chain: cat.parent.parent... → "A › B › C"
    cat = data.get("category") or {}
    crumbs: list = []
    cur = cat
    while cur:
        title = cur.get("title") or cur.get("breadcrumbs_title") or ""
        if title:
            # strip HTML tags
            title = re.sub(r"<[^>]+>", "", str(title)).strip()
            crumbs.append(title)
        cur = cur.get("parent")
    breadcrumb = " › ".join(reversed(crumbs))[:200] if crumbs else ""
    # Update DB
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            "UPDATE parsed_products SET original_desc=?, breadcrumb=? WHERE product_id=?",
            (info[:5000], breadcrumb, pid),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _db_needs_image(pid: str) -> bool:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    row = conn.execute(
        "SELECT local_image_path FROM parsed_products WHERE product_id=?", (pid,)
    ).fetchone()
    conn.close()
    return not row or not row[0]

def _db_set_image(pid: str, path: str):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("UPDATE parsed_products SET local_image_path=? WHERE product_id=?", (path, pid))
    conn.commit()
    conn.close()

# ── Скачивание фото ───────────────────────────────────────────────────────────

async def _download_image(session: httpx.AsyncClient, pid: str, url: str):
    if not url or not _db_needs_image(pid):
        return
    ext = Path(url.split("?")[0]).suffix.lower() or ".webp"
    if ext not in (".jpg",".jpeg",".png",".webp",".gif"):
        ext = ".webp"
    dest = IMG_DIR / f"{pid}{ext}"
    if dest.exists():
        return
    try:
        r = await session.get(url, timeout=10,
            headers={"Referer": "https://ggsel.net/", "Accept": "image/*"})
        if r.status_code == 200 and r.content:
            dest.write_bytes(r.content)
            _db_set_image(pid, f"/static/products/{pid}{ext}")
    except Exception:
        pass

# ── Воркер ────────────────────────────────────────────────────────────────────

async def worker(wid: int, queue: asyncio.Queue, counters: dict, log):
    prefix = f"[W{wid}]"
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as api_sess:
        async with httpx.AsyncClient(timeout=10) as img_sess:
            while True:
                try:
                    idx, cat = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                cat_slug = cat.get("url") or cat.get("slug", "")
                cat_tree = cat.get("tree", cat.get("title", "?"))[:60]
                cat_fee  = cat.get("fee", 0.15)
                cat_fee  = float(cat_fee) if cat_fee else 0.15
                cat_saved = 0
                cat_found = 0
                seen_ids: set = set()

                log.info(f"{prefix} [{idx}] slug={cat_slug!r} fee={cat_fee*100:.0f}% {cat_tree}")

                for page in range(1, MAX_PAGES + 1):
                    try:
                        body = {"lang":"en","currency":"wmz",
                                "limit":PER_PAGE,"page":page}
                        if cat_slug:
                            body["category"] = cat_slug
                        r = await api_sess.post(
                            f"{API_BASE}/elastic/goods/rec-goods",
                            json=body,
                        )
                        items = r.json().get("data", {}).get("items", [])
                    except Exception as e:
                        log.warning(f"{prefix} HTTP err: {e}")
                        break

                    if not items:
                        break

                    # Детект повторяющейся страницы: API иногда возвращает
                    # одно и то же на page=2,3,... для маленьких категорий
                    page_ids = {it.get("id_goods") for it in items if it.get("id_goods")}
                    new_ids = page_ids - seen_ids
                    if not new_ids:
                        break  # всё уже видели — категория исчерпана
                    seen_ids |= page_ids

                    new_items = [it for it in items if it.get("id_goods") in new_ids]
                    cat_found += len(new_items)

                    n = await asyncio.to_thread(_db_save, new_items, cat_tree, cat_fee, cat_slug)
                    cat_saved += n

                    img_tasks = [
                        _download_image(img_sess, str(it.get("id_goods","")), it.get("image",""))
                        for it in new_items if it.get("id_goods") and it.get("image")
                    ]
                    if img_tasks:
                        await asyncio.gather(*img_tasks, return_exceptions=True)

                    if len(items) < PER_PAGE:
                        break
                    await asyncio.sleep(0.3)

                counters["saved"] += cat_saved
                counters["found"] += cat_found
                if cat_found:
                    log.info(f"{prefix} ✓ [{idx}] saved={cat_saved} found={cat_found}")
                queue.task_done()

# ── Прогресс ──────────────────────────────────────────────────────────────────

def save_progress(counters, total_cats, worker_id: int = 0):
    """Пишет прогресс в отдельный файл для каждого процесса-воркера.
    worker_id соответствует --token-index, т.е. W1–W5 в parse_all.py."""
    fname = f"data/bulk_parse_progress_w{worker_id}.json"
    Path(fname).write_text(json.dumps({
        "worker_id": worker_id,
        "total_cats": total_cats,
        "saved": counters["saved"],
        "found": counters["found"],
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2), encoding="utf-8")

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers",     type=int, default=4)
    ap.add_argument("--start-from",  type=int, default=0)
    ap.add_argument("--limit",       type=int, default=0)
    ap.add_argument("--token-index", type=int, default=0, help="0=parser_1, 1=parser_2")
    args = ap.parse_args()

    log = logging.getLogger("bulk")

    # Категории — берём из API (там есть url=slug, нужный для фильтрации)
    log.info("Загружаем категории из API...")
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as _s:
        _r = await _s.get(f"{API_BASE}/main/content-types", params={"lang": "en"})
        _data = _r.json().get("data", [])
    leaves = [c for c in _data if isinstance(c, dict) and c.get("url")]
    log.info("Категорий из API: %d", len(leaves))
    leaves = leaves[args.start_from:]
    if args.limit:
        leaves = leaves[:args.limit]

    account = "ggsel_parser_1" if args.token_index == 0 else f"ggsel_parser_{args.token_index + 1}"
    log.info("Категорий: %d | Воркеров: %d | Аккаунт: %s", len(leaves), args.workers, account)

    # Очередь
    queue = asyncio.Queue()
    for i, cat in enumerate(leaves, args.start_from + 1):
        queue.put_nowait((i, cat))

    counters = {"saved": 0, "found": 0}
    t0 = time.time()
    _worker_id = args.token_index  # каждый процесс пишет свой progress-файл

    # Запуск воркеров
    workers = [
        asyncio.create_task(worker(wid+1, queue, counters, log))
        for wid in range(args.workers)
    ]

    # Прогресс каждые 30 сек
    async def progress_reporter():
        while not all(w.done() for w in workers):
            await asyncio.sleep(30)
            elapsed = (time.time() - t0) / 60
            done = len(leaves) - queue.qsize()
            rate = done / elapsed if elapsed > 0 else 0
            eta  = (queue.qsize() / rate) if rate > 0 else 0
            log.info("── Прогресс: %d/%d кат | saved=%d | %.1f мин прошло | ETA ~%.0f мин",
                     done, len(leaves), counters["saved"], elapsed, eta)
            save_progress(counters, len(leaves), _worker_id)

    await asyncio.gather(*workers, progress_reporter())

    elapsed = (time.time() - t0) / 60
    imgs = len(list(IMG_DIR.glob("*.*")))
    log.info("═" * 60)
    log.info("ГОТОВО: найдено=%d сохранено=%d фото=%d за %.1f мин",
             counters["found"], counters["saved"], imgs, elapsed)
    save_progress(counters, len(leaves), _worker_id)

if __name__ == "__main__":
    asyncio.run(main())
