import requests, os
from dotenv import load_dotenv

load_dotenv('.env')

API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')

headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}

updates = [
    {
        "id": "1719037824648252",  # biranol
        "proxyType": "socks5",
        "proxyIp": "185.148.24.128",
        "proxyPort": 8000,
        "username": "hwXLQL",
        "password": "82Kv91"
    },
    {
        "id": "1719037826644973",  # ristarel1
        "proxyType": "socks5",
        "proxyIp": "185.148.27.78",
        "proxyPort": 8000,
        "username": "hwXLQL",
        "password": "82Kv91"
    },
    {
        "id": "1719037830921422",  # vernadilo1
        "proxyType": "socks5",
        "proxyIp": "147.45.59.102",
        "proxyPort": 8000,
        "username": "qYesko",
        "password": "zMnYyh"
    },
    {
        "id": "1719037832944516",  # vernadilo2
        "proxyType": "socks5",
        "proxyIp": "147.45.57.59",
        "proxyPort": 8000,
        "username": "qYesko",
        "password": "zMnYyh"
    }
]

for item in updates:
    r = requests.post(f"{API_BASE}/api/proxyInfo/update", json=item, headers=headers)
    print(f"Updated {item['id']}: {r.json()}")
