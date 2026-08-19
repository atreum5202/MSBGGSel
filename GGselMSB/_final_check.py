# -*- coding: utf-8 -*-
"""Финальный сквозной тест: реальный API -> id_section -> fee -> my_price"""
import sqlite3, json, httpx, time
from pathlib import Path
from parser.pricing import calculate_my_price

ROOT = Path(__file__).parent
conn = sqlite3.connect(str(ROOT / 'data' / 'db' / 'parser.db'))
tok = json.loads((ROOT / 'data' / 'ggsel_tokens.json').read_text(encoding='utf-8'))
H = {'Authorization': 'Bearer ' + tok['access_token'], 'Content-Type': 'application/json'}

def get_fee_and_path(cat_id):
    if not cat_id:
        return None, None
    row = conn.execute('SELECT fee, tree FROM seller_categories WHERE id=?', (int(cat_id),)).fetchone()
    if row and row[0] is not None:
        return float(row[0]), (row[1] or str(cat_id))[:45]
    return None, None

TEST_SLUGS = ['roblox', 'spotify-premium', 'games-steam', 'valorant-9149', 'microsoft-office']

print(f"{'name':<34} {'id_sec':>10} {'fee%':>7} {'my@100':>8}  path")
print('-' * 100)

all_ok = all_miss = 0
missed_ids = []

for slug in TEST_SLUGS:
    try:
        r = httpx.post('https://api.ggsel.com/elastic/goods/categories',
            headers=H,
            json={'limit': 5, 'lang': 'ru', 'page': 1, 'sort': 'sortByRec',
                  'category_url': slug, 'query_string': '', 'search_after': [],
                  'with_filters': False, 'is_preorders': False,
                  'with_forbidden': False, 'min_price': '', 'max_price': ''},
            timeout=10)
        items = r.json().get('data', {}).get('items', []) if r.status_code == 200 else []
    except Exception as e:
        print(f'ERROR {slug}: {e}')
        continue

    for it in items[:3]:
        name = (it.get('name') or '')[:33]
        price = float(it.get('price_wmr') or 0)
        id_sec = it.get('id_section')
        fee_val, path = get_fee_and_path(id_sec)
        if fee_val is not None:
            mp = calculate_my_price(price, id_sec)
            all_ok += 1
            print(f'OK   {name:<34} {id_sec:>10} {fee_val*100:>6.1f}%  {mp:>8.0f}  {path}')
        else:
            all_miss += 1
            missed_ids.append(id_sec)
            print(f'MISS {name:<34} {id_sec:>10} {"?":>7} {"?":>8}  NOT IN seller_categories')
    time.sleep(0.3)

print()
print(f'OK={all_ok}  MISS={all_miss}  total={all_ok+all_miss}')
if missed_ids:
    print(f'Пропущенные id_section: {sorted(set(missed_ids))}')
    print('-> Запусти _sync_missing_cats.py для их добавления')
else:
    print('Все id_section известны — fee определяется точно для каждого товара')

conn.close()
