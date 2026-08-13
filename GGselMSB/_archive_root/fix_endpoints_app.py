import sys

app_py_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/app.py"
with open(app_py_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix promo_codes
old_promo = """@app.route("/api/promo_codes", methods=["GET"])
def get_promo_codes():
    url = "https://seller.ggsel.com/api_sellers/api/promo_codes"
    status, data = _cookie_get(url, params=request.args)
    return jsonify(data), status"""

new_promo = """@app.route("/api/promo_codes", methods=["GET"])
def get_promo_codes():
    url = "https://seller.ggsel.com/api/v1/promo_codes"
    try:
        status, data = _cookie_get(url, params=request.args)
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes", params=request.args)
    except:
        status, data = _v1_get("/api/v1/promo_codes", params=request.args)
    return jsonify(data), status"""

content = content.replace(old_promo, new_promo)

# Fix promo filters
old_promo_filters = """@app.route("/api/promo_codes/filters", methods=["GET"])
def get_promo_codes_filters():
    url = "https://seller.ggsel.com/api_sellers/api/promo_codes/filters"
    status, data = _cookie_get(url)
    return jsonify(data), status"""

new_promo_filters = """@app.route("/api/promo_codes/filters", methods=["GET"])
def get_promo_codes_filters():
    try:
        status1, data1 = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/statuses")
        if status1 == 401:
            status1, data1 = _v1_get("/api/v1/promo_codes/filters/statuses")
            
        status2, data2 = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/offers")
        if status2 == 401:
            status2, data2 = _v1_get("/api/v1/promo_codes/filters/offers")
            
        return jsonify({
            "statuses": data1.get("data", data1) if isinstance(data1, dict) else data1,
            "offers": data2.get("data", data2) if isinstance(data2, dict) else data2
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500"""

content = content.replace(old_promo_filters, new_promo_filters)

# Fix ledger
old_ledger = """@app.route("/api/ledger", methods=["GET"])
def get_ledger():
    url = "https://seller.ggsel.com/api_sellers/api/ledger"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    # Fallback error for JS to handle
    return jsonify({"error": "cookie_auth_required"}), 401"""

new_ledger = """@app.route("/api/ledger", methods=["GET"])
def get_ledger():
    url = "https://seller.ggsel.com/api/v1/ledger_items"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    return jsonify({"ok": False, "stub": True, "items": []}), 401"""

content = content.replace(old_ledger, new_ledger)

with open(app_py_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated app.py endpoints")
