import requests, os
from dotenv import load_dotenv

load_dotenv('.env')

API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')

headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}

updates = [
    {
        "envId": "2084336484026486784",  # biranol
        "proxyInfo": {
            "proxyType": "socks5",
            "host": "185.148.24.128",
            "port": "8000",
            "proxyUser": "hwXLQL",
            "proxyPassword": "82Kv91"
        }
    },
    {
        "envId": "2084336471078670336",  # ristarel1
        "proxyInfo": {
            "proxyType": "socks5",
            "host": "185.148.27.78",
            "port": "8000",
            "proxyUser": "hwXLQL",
            "proxyPassword": "82Kv91"
        }
    },
    {
        "envId": "2084336475029704704",  # vernadilo1
        "proxyInfo": {
            "proxyType": "socks5",
            "host": "147.45.59.102",
            "port": "8000",
            "proxyUser": "qYesko",
            "proxyPassword": "zMnYyh"
        }
    },
    {
        "envId": "2084336479580524544",  # vernadilo2
        "proxyInfo": {
            "proxyType": "socks5",
            "host": "147.45.57.59",
            "port": "8000",
            "proxyUser": "qYesko",
            "proxyPassword": "zMnYyh"
        }
    }
]

for item in updates:
    r = requests.post(f"{API_BASE}/api/env/update", json=item, headers=headers)
    print(f"Updated {item['envId']}: {r.json()}")
