import requests, os
from dotenv import load_dotenv

load_dotenv('.env')
API_BASE = os.environ.get('MORELOGIN_API_BASE', 'http://127.0.0.1:40000')
API_ID = os.environ.get('MORELOGIN_API_ID')
API_KEY = os.environ.get('MORELOGIN_API_KEY')
headers = {'X-Api-Id': API_ID, 'X-Api-Key': API_KEY}

# First fetch the proxy info
r = requests.post(f"{API_BASE}/api/env/page", json={'pageNo': 1, 'pageSize': 50}, headers=headers)
data = r.json().get('data', {}).get('dataList', [])
target = next((x for x in data if x['id'] == "2084336484026486784"), None)

if target and target.get('proxy'):
    proxy = target['proxy']
    print("Original proxy:", proxy)
    
    # Try to update it using proxyInfo/update
    # We copy the original proxy and update only the fields we want to change
    proxy_update = proxy.copy()
    proxy_update['proxyIp'] = "185.148.24.128"
    proxy_update['host'] = "185.148.24.128"
    proxy_update['port'] = 8000
    proxy_update['proxyPort'] = 8000
    proxy_update['username'] = "hwXLQL"
    proxy_update['proxyUser'] = "hwXLQL"
    proxy_update['password'] = "82Kv91"
    proxy_update['proxyPassword'] = "82Kv91"
    
    # Also fix types that might cause "Http message not readable"
    # Make sure we don't send `None` to fields that don't accept it, although this is just the copied object
    
    res = requests.post(f"{API_BASE}/api/proxyInfo/update", json=proxy_update, headers=headers)
    print("Update result:", res.json())
else:
    print("Target not found")
