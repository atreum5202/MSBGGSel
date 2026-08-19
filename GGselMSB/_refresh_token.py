# _refresh_token.py — запустить один раз для обновления токена ggsel
import sys
sys.path.insert(0, '.')
from parser.ggsel_api_client import GgselApiClient
import logging, json
logging.basicConfig(level=logging.INFO)

client = GgselApiClient()
print('Token valid:', client.is_token_valid)
if not client.is_token_valid:
    print('Refreshing...')
    ok = client._try_refresh()
    print('Refresh result:', ok)
    if ok:
        print('New token valid:', client.is_token_valid)
        # Проверяем что реально сохранилось
        import json as j
        t = j.loads(open('data/ggsel_tokens.json').read())
        import time
        print('Days left:', round((t['exp'] - time.time())/86400, 1))
    else:
        print('Refresh failed — need to re-login via browser')
else:
    print('Token already valid')
