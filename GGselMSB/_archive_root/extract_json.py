import json, os

target_files = [
    "0161_GET_api_v1_promo_codes.json",
    "0155_GET_api_v1_promo_codes_filters_offers.json",
    "0156_GET_api_v1_promo_codes_filters_statuses.json",
    "0314_GET_profile.json",
    "0205_GET_api_v1_ledger_items.json",
    "0453_GET_api_v1_ledger_items_20538339.json",
    "0532_GET_api_v1_account_notifications.json",
    "0237_GET_api_v1_account_whitelisted_ips.json",
    "0022_GET_api_v1_dashboard.json",
    "0007_GET_cable.json"
]

base_path = r"C:\Users\Atreum\Desktop\GGsellerCopy\api_data"
os.makedirs("extracted_json", exist_ok=True)

import gzip
import brotli

for file in target_files:
    path = os.path.join(base_path, file)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            body_text = d.get('response', {}).get('body_text', '')
            try:
                parsed = json.loads(body_text)
            except:
                parsed = {"raw": body_text[:500]}
            
            with open(os.path.join("extracted_json", file), "w", encoding="utf-8") as out_f:
                json.dump(parsed, out_f, indent=2, ensure_ascii=False)
