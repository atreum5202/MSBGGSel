import re
with open('templates/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

def count_tags(html, start_id, end_id=None):
    start_idx = text.find(f'id=\"{start_id}\"')
    if start_idx == -1: return 'not found'
    end_idx = text.find(f'id=\"{end_id}\"') if end_id else len(text)
    section = text[start_idx:end_idx]
    div_open = len(re.findall(r'<div', section, re.IGNORECASE))
    div_close = len(re.findall(r'</div', section, re.IGNORECASE))
    return div_open, div_close

print('view-messages:', count_tags(text, 'view-messages', 'view-reviews'))
print('view-promo-codes:', count_tags(text, 'view-promo-codes', 'view-messages'))
