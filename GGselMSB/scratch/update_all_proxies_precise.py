import requests, os
from dotenv import load_dotenv

load_dotenv('.env')
API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')
headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}

updates_map = {
    "1719037824648252": {  # biranol
        "proxyIp": "185.148.24.128",
        "proxyPort": "8000",
        "username": "hwXLQL",
        "password": "82Kv91"
    },
    "1719037826644973": {  # ristarel1
        "proxyIp": "185.148.27.78",
        "proxyPort": "8000",
        "username": "hwXLQL",
        "password": "82Kv91"
    },
    "1719037830921422": {  # vernadilo1
        "proxyIp": "147.45.59.102",
        "proxyPort": "8000",
        "username": "qYesko",
        "password": "zMnYyh"
    },
    "1719037832944516": {  # vernadilo2
        "proxyIp": "147.45.57.59",
        "proxyPort": "8000",
        "username": "qYesko",
        "password": "zMnYyh"
    }
}

r = requests.post(f"{API_BASE}/api/env/page", json={'pageNo': 1, 'pageSize': 50}, headers=headers)
data = r.json().get('data', {}).get('dataList', [])

for item in data:
    proxy = item.get('proxy')
    if proxy and proxy['id'] in updates_map:
        new_data = updates_map[proxy['id']]
        
        # update the proxy object
        proxy_update = proxy.copy()
        proxy_update['proxyIp'] = new_data['proxyIp']
        proxy_update['proxyPort'] = new_data['proxyPort']
        proxy_update['username'] = new_data['username']
        proxy_update['password'] = new_data['password']
        
        # some extra fields just in case
        proxy_update['host'] = new_data['proxyIp']
        proxy_update['port'] = int(new_data['proxyPort'])
        proxy_update['proxyUser'] = new_data['username']
        proxy_update['proxyPassword'] = new_data['password']
        
        res = requests.post(f"{API_BASE}/api/proxyInfo/update", json=proxy_update, headers=headers)
        print(f"Update {item['envName']}:", res.json())
