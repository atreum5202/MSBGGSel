import requests

# 1. Start flask in background
import subprocess
import time
import os
import signal

flask_proc = subprocess.Popen(["python", "app.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(4)

try:
    # 2. Get real ID
    r = requests.get("http://127.0.0.1:5000/api/offers", timeout=5)
    data = r.json()
    items = data.get("items", [])
    if not items and "data" in data and "data" in data["data"]:
        items = data["data"]["data"]
    
    if not items:
        print("No offers found!")
        sys.exit(1)
        
    real_id = items[0].get("id")
    print(f"Found real ID: {real_id}")
    
    # 3. Test public endpoint
    r2 = requests.get(f"http://127.0.0.1:5000/api/offer/{real_id}/public")
    print(f"Public endpoint status: {r2.status_code}")
finally:
    flask_proc.terminate()
