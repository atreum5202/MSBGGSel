import requests, os
from dotenv import load_dotenv

load_dotenv('.env')

API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')
headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}

# Try different formats
payloads = [
    {
        "envId": "2084336484026486784",
        "proxyInfo": {
            "proxyType": "socks5",
            "proxyIp": "185.148.24.128",
            "proxyPort": 8000,
            "proxyUser": "hwXLQL",
            "proxyPassword": "82Kv91"
        }
    },
    {
        "envId": "2084336484026486784",
        "proxyInfo": {
            "proxyType": 0,
            "proxyIp": "185.148.24.128",
            "proxyPort": "8000",
            "username": "hwXLQL",
            "password": "82Kv91"
        }
    }
]

for p in payloads:
    r = requests.post(f"{API_BASE}/api/env/update", json=p, headers=headers)
    print("Payload:", p, "Result:", r.json())

# Check if it worked
r = requests.post(f"{API_BASE}/api/env/page", json={'pageNo': 1, 'pageSize': 50}, headers=headers)
for item in r.json().get('data', {}).get('dataList', []):
    if item['id'] == "2084336484026486784":
        print("Now:", item.get('proxy'))
