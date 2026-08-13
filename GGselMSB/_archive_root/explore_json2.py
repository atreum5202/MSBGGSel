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
        for k, v in list(data.items())[:8]:
            print("  " * indent + str(k) + ": " + type(v).__name__)
            if isinstance(v, (dict, list)):
                print_structure(v, indent + 1)
        if len(data) > 8:
            print("  " * indent + "... and " + str(len(data)-8) + " more keys")
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
            full_data = json.load(f)
            # The actual API response is usually inside response_body
            if "response_body" in full_data:
                try:
                    data = json.loads(full_data["response_body"])
                except:
                    data = full_data["response_body"]
                print_structure(data)
            else:
                print_structure(full_data)
    else:
        print("File not found")
