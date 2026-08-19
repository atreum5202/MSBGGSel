# -*- coding: utf-8 -*-
"""
Синхронизирует недостающие id_section из seller API в seller_categories.
Использует seller_cookies.json (обновляются cookie_autorefresh.py).
Запуск: python _sync_missing_cats.py
"""
import httpx, json, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / 'data' / 'db' / 'parser.db'
COOK_PATH = ROOT / 'data' / 'seller_cookies.json'
FEES_PATH = ROOT / 'cat_fees.json'

# ── Загрузка куков ───────────────────────────────────────────────────────────
if not COOK_PATH.exists():
    raise SystemExit('seller_cookies.json не найден — запусти cookie_autorefresh.py')
seller_cookies = {k: v for k, v in json.loads(COOK_PATH.read_text(encoding='utf-8')).items() if isinstance(v, str)}
print(f'Куки загружены: {list(seller_cookies.keys())}')

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# ── Ensure columns ───────────────────────────────────────────────────────────
have_cols = {r[1] for r in conn.execute('PRAGMA table_info(seller_categories)').fetchall()}
for col, typ in [('ggsel_digi_catalog','INTEGER'), ('ancestor_ids','TEXT'),
                  ('unit','TEXT'), ('default_payment_system_fee','REAL'), ('text_for_sellers','TEXT')]:
    if col not in have_cols:
        conn.execute(f'ALTER TABLE seller_categories ADD COLUMN {col} {typ}')
conn.commit()

# ── Найти все id_section из API которых нет в seller_categories ──────────────
# Запрашиваем 5 самых популярных категорий
tok = json.loads((ROOT / 'data' / 'ggsel_tokens.json').read_text(encoding='utf-8'))
H_BUYER = {'Authorization': 'Bearer ' + tok['access_token'], 'Content-Type': 'application/json'}
H_SELLER = {'Accept': 'application/json', 'Content-Type': 'application/json'}

SCAN_SLUGS = [
    'roblox', 'spotify-premium', 'games-steam', 'podpisochnye-servisy',
    'valorant-9149', 'fortnite', 'mobile-legends', 'pubg-mobile',
    'microsoft-office', 'adobe-creative-cloud', 'apple-id', 'game-currency',
    'genshin-impact', 'clash-royale-9584', 'brawl-stars-9063', 'igry-po-nazvaniyu',
    'programs-new', 'xbox-game-pass-1', 'dead-by-daylight-9610', 'honkai-star-rail',
    'apex-legends-1', 'cyberpunk-2077', 'clash-of-clans-9576', 'mobile-games',
    'robux', 'minecraft-10054',
]

print(f'\nСканируем {len(SCAN_SLUGS)} категорий для сбора id_section...')
missing_ids = set()

for slug in SCAN_SLUGS:
    try:
        r = httpx.post('https://api.ggsel.com/elastic/goods/categories',
            headers=H_BUYER,
            json={'limit': 60, 'lang': 'ru', 'page': 1, 'sort': 'sortByRec',
                  'category_url': slug, 'query_string': '', 'search_after': [],
                  'with_filters': False, 'is_preorders': False, 'with_forbidden': False,
                  'min_price': '', 'max_price': ''},
            timeout=10)
        if r.status_code != 200:
            print(f'  {slug}: HTTP {r.status_code}')
            continue
        items = r.json().get('data', {}).get('items', [])
        for it in items:
            sid = it.get('id_section')
            if not sid:
                continue
            row = conn.execute('SELECT id FROM seller_categories WHERE id=?', (sid,)).fetchone()
            if not row:
                missing_ids.add(sid)
        time.sleep(0.25)
    except Exception as e:
        print(f'  {slug}: ERROR {e}')

print(f'id_section отсутствующих в БД: {len(missing_ids)}')
if not missing_ids:
    print('Все id_section уже в БД — синхронизация не нужна')
    conn.close()
    raise SystemExit(0)

# ── Получаем tree для каждого missing id через seller API ────────────────────
print(f'\nЗапрашиваем tree для {len(missing_ids)} категорий...')
synced = 0
errors = 0

for cat_id in sorted(missing_ids):
    try:
        r = httpx.get(
            f'https://seller.ggsel.com/api/v1/categories/{cat_id}/tree',
            cookies=seller_cookies,
            headers=H_SELLER,
            timeout=8
        )
        if r.status_code != 200:
            print(f'  id={cat_id}: HTTP {r.status_code}')
            errors += 1
            continue

        chain = r.json().get('data', [])
        if not chain:
            print(f'  id={cat_id}: пустой ответ')
            errors += 1
            continue

        # Сохраняем каждый узел цепочки
        for node in chain:
            nid = node.get('id')
            if not nid:
                continue
            title = node.get('title', '')
            fee = node.get('fee')
            content_type = node.get('content_type', '')
            has_children = 1 if node.get('kind') == 'main' else 0
            ggsel_digi = node.get('ggsel_digi_catalog')
            ancestor_ids = json.dumps(node.get('ancestor_ids', []))
            unit = json.dumps(node.get('unit')) if node.get('unit') else None
            dpf = node.get('default_payment_system_fee')
            tfs = node.get('text_for_sellers', '')

            # Строим tree как путь через title_with_ancestors
            tree = node.get('title_with_ancestors') or title

            conn.execute('''
                INSERT INTO seller_categories (id, title, tree, content_type, fee, has_children,
                    ggsel_digi_catalog, ancestor_ids, unit, default_payment_system_fee, text_for_sellers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, tree=excluded.tree,
                    content_type=excluded.content_type, fee=excluded.fee,
                    has_children=excluded.has_children,
                    ggsel_digi_catalog=COALESCE(excluded.ggsel_digi_catalog, seller_categories.ggsel_digi_catalog),
                    ancestor_ids=COALESCE(excluded.ancestor_ids, seller_categories.ancestor_ids),
                    unit=COALESCE(excluded.unit, seller_categories.unit),
                    default_payment_system_fee=COALESCE(excluded.default_payment_system_fee, seller_categories.default_payment_system_fee),
                    text_for_sellers=COALESCE(NULLIF(excluded.text_for_sellers,''), seller_categories.text_for_sellers)
            ''', (nid, title, tree, content_type, fee, has_children,
                  ggsel_digi, ancestor_ids, unit, dpf, tfs))

        leaf = chain[-1]
        print(f'  id={cat_id} OK: fee={leaf.get("fee")} | {leaf.get("title_with_ancestors","")[:55]}')
        synced += 1
        time.sleep(0.2)

    except Exception as e:
        print(f'  id={cat_id}: ERROR {e}')
        errors += 1

conn.commit()
print(f'\nСинхронизировано: {synced}, ошибок: {errors}')

# ── Обновляем cat_fees.json ───────────────────────────────────────────────────
fees = {}
for sid, fee in conn.execute('SELECT id, fee FROM seller_categories WHERE fee IS NOT NULL').fetchall():
    fees[str(int(sid))] = round(float(fee), 4)
FEES_PATH.write_text(json.dumps(fees, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'cat_fees.json обновлён: {len(fees)} записей')

# ── Финальная проверка ────────────────────────────────────────────────────────
still_missing = []
for mid in missing_ids:
    row = conn.execute('SELECT id, fee FROM seller_categories WHERE id=?', (mid,)).fetchone()
    if not row:
        still_missing.append(mid)

if still_missing:
    print(f'Ещё не в БД ({len(still_missing)}): {still_missing}')
else:
    print('Все id_section теперь в seller_categories ✅')

conn.close()
