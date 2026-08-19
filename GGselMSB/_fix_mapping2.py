# -*- coding: utf-8 -*-
"""
Второй проход маппинга: добавляем slug-ы которые не смогли вставить из-за коллизии seller_id PK.
Стратегия: берём свободные seller_id из seller_categories (листья Цифровые товары > Другое).
Запуск: python _fix_mapping2.py
"""
import sqlite3, json
conn = sqlite3.connect('data/db/parser.db')
conn.row_factory = sqlite3.Row

fees = json.load(open('cat_fees.json', encoding='utf-8'))
FALLBACK_TREE = 33833  # базовый узел

# slug-ы которые MISS
missing = [
    'grand-theft-auto-5-first',
    'playstation-games',
    'helldivers-2',
    'ea-sports-fc-26-fifa-26',
    'arena-breakout-infinite',
    'europa-universalis-v',
    'dispatch',
    'games-anno-117-pax-romana',
    'call-of-duty-mobile',
    'standoff-2',
    'zenless-zone-zero',
    'world-of-tanks-blitz',
    'albion-online',
    'microsoft-office-365',
    'antivirus-eset',
    'unlocktool',
    'voicemod-pro',
    'exitlag',
    'autodesk',
    'software-for-gamers-and-streaming',
    'seo-software',
    'subscriptions-for-all-occasions',
    'other-games-currency',
]

# Для некоторых у нас есть конкретные seller_id (найденные ранее но занятые другим slug)
# Для них делаем UPDATE существующей записи — меняем slug на более подходящий
specific = {
    'grand-theft-auto-5-first': (59,    'GTA Аккаунты', 'Игры > Grand Theft Auto: The Trilogy > Аккаунты > Steam', 0.80),
    'playstation-games':        (43973, 'PlayStation',   'PlayStation',                                              0.90),
    'call-of-duty-mobile':      (4021,  'CoD Аккаунты', 'Игры > Call of Duty: Vanguard (2021) > Услуги',            0.75),
    'microsoft-office-365':     (138327,'Office 365',   '3650',                                                     0.70),
    'subscriptions-for-all-occasions': (174679, 'Подписки', 'Подписки',                                            0.80),
    'other-games-currency':     (28073, 'Другое',       'Другое',                                                   0.85),
}

updated_specific = 0
for slug, (sid, title, tree, score) in specific.items():
    # Проверяем: slug уже есть?
    row = conn.execute('SELECT seller_id, match_score FROM category_slug_mapping WHERE slug=?', (slug,)).fetchone()
    if row:
        print(f'  SKIP {slug} — уже есть (seller_id={row[0]}, score={row[1]})')
        missing.remove(slug) if slug in missing else None
        continue
    # seller_id занят?
    row_sid = conn.execute('SELECT slug, match_score FROM category_slug_mapping WHERE seller_id=?', (sid,)).fetchone()
    if row_sid:
        if score > row_sid['match_score']:
            conn.execute(
                'UPDATE category_slug_mapping SET slug=?, seller_title=?, seller_tree=?, match_score=? WHERE seller_id=?',
                (slug, title, tree, float(score), int(sid))
            )
            print(f'  UPD  {slug} => seller_id={sid} (перебил slug={row_sid["slug"]})')
            updated_specific += 1
        else:
            print(f'  SKIP {slug} => seller_id={sid} занят {row_sid["slug"]} (score={row_sid["match_score"]} >= {score})')
        missing.remove(slug) if slug in missing else None
    else:
        conn.execute(
            'INSERT INTO category_slug_mapping (seller_id, slug, seller_title, seller_tree, match_score) VALUES (?,?,?,?,?)',
            (int(sid), slug, title, tree, float(score))
        )
        print(f'  INS  {slug} => seller_id={sid}')
        updated_specific += 1
        missing.remove(slug) if slug in missing else None

conn.commit()
print(f'\nspecific: {updated_specific} обработано, осталось miss={len(missing)}')

# Для оставшихся — берём свободные seller_id из seller_categories (Цифровые товары > Другое листья)
free_ids = conn.execute('''
    SELECT sc.id, sc.title, sc.tree, sc.fee
    FROM seller_categories sc
    WHERE sc.has_children = 0
      AND sc.id NOT IN (SELECT seller_id FROM category_slug_mapping)
    ORDER BY sc.id
    LIMIT 100
''').fetchall()

# Пул свободных id
pool = [(r['id'], r['tree'] or r['title'], r['fee']) for r in free_ids]
print(f'Свободных seller_id в пуле: {len(pool)}')

inserted = 0
for i, slug in enumerate(missing):
    if i >= len(pool):
        print(f'  NO_SLOT {slug} — пул исчерпан')
        continue
    sid, tree, fee = pool[i]
    conn.execute(
        'INSERT INTO category_slug_mapping (seller_id, slug, seller_title, seller_tree, match_score) VALUES (?,?,?,?,?)',
        (int(sid), slug, slug, tree, 0.50)
    )
    print(f'  INS  {slug} => seller_id={sid} fee={fee} [{tree[:45]}]')
    inserted += 1

conn.commit()
print(f'\ninserted from pool: {inserted}')

# ── Финальный отчёт ─────────────────────────────────────────────────────────
KNOWN = [
    'igry-po-nazvaniyu','game-currency','mobile-games','podpisochnye-servisy','programs-new',
    'grand-theft-auto-5-first','grand-theft-auto-vi','playstation-games','xbox-game-pass-1',
    'minecraft-10054','valorant-9149','roblox','robux','dead-by-daylight-9610','fortnite',
    'brawl-stars-9063','genshin-impact','apex-legends-1','arc-raiders','red-dead-redemption-2-10191',
    'helldivers-2','cyberpunk-2077','clash-royale-9584','battlefield-6-160525',
    'ea-sports-fc-26-fifa-26','arena-breakout-infinite','europa-universalis-v','dispatch',
    'games-anno-117-pax-romana','games-steam','other-1',
    'mobile-legends','pubg-mobile','clash-of-clans-9576','call-of-duty-mobile','standoff-2',
    'zenless-zone-zero','world-of-tanks-blitz','albion-online','honkai-star-rail',
    'microsoft-office','adobe-creative-cloud','capcut','microsoft-office-365','antivirus-eset',
    'unlocktool','voicemod-pro','exitlag','jetbrains','autodesk','malwarebytes-premium',
    'os','software','software-for-gamers-and-streaming','programming-software','seo-software',
    'spotify-premium','apple-id','subscriptions-for-all-occasions','other-games-currency',
]

print()
print(f"{'slug':<42} {'sc':>5} {'sid':>10} {'fee':>6}")
print('-' * 75)
miss = []
fallbacks = []
for slug in KNOWN:
    row = conn.execute(
        'SELECT seller_id, match_score, seller_tree FROM category_slug_mapping WHERE slug=? ORDER BY match_score DESC LIMIT 1',
        (slug,)
    ).fetchone()
    if not row:
        miss.append(slug)
        print(f"{'MISS':<6} {slug}")
        continue
    sid = row['seller_id']
    sc = row['match_score']
    fee = fees.get(str(sid), 'N/A')
    tag = '[FB]' if sid == FALLBACK_TREE else ('[pool]' if sc <= 0.50 else '')
    if sc <= 0.50:
        fallbacks.append(slug)
    print(f"OK   {sc:>4.2f}  {sid:>10}  {str(fee):>6}  {tag}")

conn.close()
print()
print(f'MISSING ({len(miss)}): {miss}')
print(f'POOL/FALLBACK ({len(fallbacks)}): {len(fallbacks)} slug-ов с score<=0.50')
print(f'Total: {len(KNOWN)}, mapped: {len(KNOWN)-len(miss)}')
