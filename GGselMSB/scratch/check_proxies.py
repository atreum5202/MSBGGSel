import requests, os
from dotenv import load_dotenv

load_dotenv('.env')

API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')

headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}
r = requests.post(f"{API_BASE}/api/env/page", json={'pageNo': 1, 'pageSize': 50}, headers=headers)

if r.status_code == 200:
    data = r.json()
    for item in data.get('data', {}).get('dataList', []):
        env_id = item.get('id')
        name = item.get('envName')
        proxy = item.get('proxy', {})
        if proxy:
            print(f"Profile {name} ({env_id}): {proxy.get('proxyType')}, IP: {proxy.get('proxyIp')}:{proxy.get('proxyPort', '8000')}, User: {proxy.get('username')}")
        else:
            print(f"Profile {name} ({env_id}): NO PROXY")
else:
    print("Error querying API:", r.text)
