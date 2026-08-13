import json, os, base64, zlib
try:
    import brotli
except ImportError:
    brotli = None

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

for file in target_files:
    path = os.path.join(base_path, file)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            resp = d.get('response', {})
            text = resp.get('body_text', '')
            headers = resp.get('headers', {})
            encoding = headers.get('content-encoding', '')
            
            # if the text is binary string represented in python?
            # actually if it's brotli/gzip but stored as unicode string, it's corrupted.
            # let's hope it's base64 or just raw json.
            # let's try to parse as json directly
            try:
                parsed = json.loads(text)
            except:
                # maybe base64?
                try:
                    decoded = base64.b64decode(text)
                    if 'br' in encoding and brotli:
                        decoded = brotli.decompress(decoded)
                    elif 'gzip' in encoding:
                        decoded = zlib.decompress(decoded, 16 + zlib.MAX_WBITS)
                    parsed = json.loads(decoded.decode('utf-8'))
                except Exception as e:
                    parsed = {"error": str(e), "raw": text[:50]}
            
            with open(os.path.join("extracted_json", file), "w", encoding="utf-8") as out_f:
                json.dump(parsed, out_f, indent=2, ensure_ascii=False)
