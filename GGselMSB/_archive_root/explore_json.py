import json, os

target_files = [
    "0161_GET_api_v1_promo_codes.json",
    "0314_GET_profile.json",
    "0205_GET_api_v1_ledger_items.json",
    "0532_GET_api_v1_account_notifications.json",
    "0237_GET_api_v1_account_whitelisted_ips.json",
    "0022_GET_api_v1_dashboard.json"
]

base_path = r"C:\Users\Atreum\Desktop\GGsellerCopy\api_data"

def print_structure(data, indent=0):
    if isinstance(data, dict):
        for k, v in list(data.items())[:5]:
            print("  " * indent + str(k) + ": " + type(v).__name__)
            if isinstance(v, (dict, list)):
                print_structure(v, indent + 1)
        if len(data) > 5:
            print("  " * indent + "... and " + str(len(data)-5) + " more keys")
    elif isinstance(data, list):
        if len(data) > 0:
            print("  " * indent + "List of " + type(data[0]).__name__)
            print_structure(data[0], indent + 1)
        else:
            print("  " * indent + "Empty List")

for file in target_files:
    path = os.path.join(base_path, file)
    print(f"\n--- {file} ---")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            print_structure(data)
    else:
        print("File not found")
