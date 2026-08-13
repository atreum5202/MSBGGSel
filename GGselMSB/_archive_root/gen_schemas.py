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

def get_schema(data):
    if isinstance(data, dict):
        res = {}
        for k, v in data.items():
            if isinstance(v, dict):
                res[k] = get_schema(v)
            elif isinstance(v, list):
                if len(v) > 0:
                    res[k] = [get_schema(v[0])]
                else:
                    res[k] = []
            else:
                res[k] = type(v).__name__
        return res
    elif isinstance(data, list):
        if len(data) > 0:
            return [get_schema(data[0])]
        return []
    else:
        return type(data).__name__

out = []
for file in target_files:
    path = os.path.join(base_path, file)
    out.append(f"## {file}\n")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            full_data = json.load(f)
            resp = full_data.get("response", {})
            body_json = resp.get("body_json")
            if body_json is None:
                try:
                    body_json = json.loads(resp.get("body_text", "{}"))
                except:
                    body_json = resp.get("body_text")
            
            schema = get_schema(body_json)
            out.append("```json\n" + json.dumps(schema, indent=2, ensure_ascii=False) + "\n```\n")
    else:
        out.append("File not found\n")

with open("schemas.md", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
