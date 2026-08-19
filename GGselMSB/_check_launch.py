# -*- coding: utf-8 -*-
"""
Быстрая проверка готовности системы к запуску парсера.
Запуск: python _check_launch.py
"""
import sys, os, json, time, sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
OK = True

def check(name, cond, fix_hint=''):
    global OK
    status = 'OK' if cond else 'FAIL'
    if not cond:
        OK = False
    print(f'  [{status}] {name}' + (f' — {fix_hint}' if not cond and fix_hint else ''))

print('=== GGselMSB Launch Readiness Check ===\n')

# 1. Python deps
print('[1] Python зависимости:')
for mod in ['flask','httpx','curl_cffi','bs4','dotenv']:
    try:
        __import__(mod.replace('-','_'))
        check(mod, True)
    except ImportError:
        check(mod, False, f'pip install {mod}')

# 2. .env
print('\n[2] .env файл:')
env = {}
env_path = ROOT / '.env'
check('.env существует', env_path.exists(), 'создай .env из .env.example')
if env_path.exists():
    for l in env_path.read_text(encoding='utf-8').splitlines():
        if '=' in l and not l.startswith('#'):
            k,v = l.split('=',1)
            env[k.strip()] = v.strip()
    check('GGSEL_API_KEY', bool(env.get('GGSEL_API_KEY')), 'нужен ключ продавца для публикации')
    check('MSB_API_BASE', bool(env.get('MSB_API_BASE','http://127.0.0.1:17248')), '')
    check('GEMINI_API_KEYS', bool(env.get('GEMINI_API_KEYS')), 'нужен для AI-генерации')

# 3. Токены ggsel
print('\n[3] Токены ggsel (api.ggsel.com):')
tok_path = ROOT / 'data' / 'ggsel_tokens.json'
check('data/ggsel_tokens.json существует', tok_path.exists(), 'запусти _refresh_token.py')
if tok_path.exists():
    t = json.loads(tok_path.read_text(encoding='utf-8'))
    exp = int(t.get('exp') or 0)
    valid = exp > time.time()
    days = round((exp - time.time())/86400, 1) if valid else 0
    check(f'access_token действителен ({days} дн)', valid, 'запусти python _refresh_token.py')
    check('refresh_token присутствует', bool(t.get('refresh_token')), 'нужен ре-логин в браузере')

# 4. БД
print('\n[4] База данных:')
db_path = ROOT / 'data' / 'db' / 'parser.db'
check('parser.db существует', db_path.exists(), 'запусти python app.py (init_db)')
if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in ['parsed_products','seller_categories','category_slug_mapping']:
        cnt = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] if t in tables else 0
        check(f'{t} ({cnt} rows)', t in tables and cnt > 0, f'таблица пустая или отсутствует')
    conn.close()

# 5. cat_fees.json
print('\n[5] cat_fees.json (комиссии категорий):')
fees_path = ROOT / 'cat_fees.json'
check('cat_fees.json существует', fees_path.exists(), 'запусти: python -m tools.refresh_cat_fees')
if fees_path.exists():
    fees = json.loads(fees_path.read_text(encoding='utf-8'))
    check(f'cat_fees.json заполнен ({len(fees)} записей)', len(fees) > 100, 'python -m tools.refresh_cat_fees')

# 6. MSB
print('\n[6] MSB (антидетект-браузер):')
try:
    import httpx
    r = httpx.get('http://127.0.0.1:17248/health', timeout=3)
    check('MSB доступен (:17248)', r.status_code == 200)
    data = r.json()
    check('MSB версия: ' + str(data.get('version', '?')), True)
except Exception as e:
    check('MSB доступен (:17248)', False, f'запусти MSB/start.vbs ({e})')

# 7. Flask
print('\n[7] Flask (панель управления):')
try:
    import httpx
    r = httpx.get('http://127.0.0.1:5000/api/parser/status', timeout=3)
    check('Flask доступен (:5000)', r.status_code == 200)
except Exception:
    check('Flask доступен (:5000)', False, 'запусти start.vbs или python app.py')

# 8. Slug покрытие
print('\n[8] Категории (slug mapping):')
if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    n95 = conn.execute('SELECT COUNT(*) FROM category_slug_mapping WHERE match_score >= 0.95').fetchone()[0]
    total = conn.execute('SELECT COUNT(*) FROM category_slug_mapping').fetchone()[0]
    pct = round(n95/total*100,1) if total else 0
    check(f'slug покрытие score>=0.95: {n95}/{total} ({pct}%)', pct >= 30,
          'запусти python -m tools.match_slugs_html (нужен MSB + ggsel.net)')
    conn.close()

print('\n' + ('='*40))
if OK:
    print('СТАТУС: ВСЁ ГОТОВО — можно запускать парсер')
else:
    print('СТАТУС: ЕСТЬ БЛОКЕРЫ — исправь [FAIL] пункты выше')
print('='*40)
