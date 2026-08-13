import sys
import os

app_py_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/app.py"
with open(app_py_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix promo_codes filters (split into two)
old_promo_filters = """@app.route("/api/promo_codes/filters", methods=["GET"])
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

new_promo_filters = """@app.route("/api/promo_codes/filters/statuses", methods=["GET"])
def get_promo_codes_filters_statuses():
    try:
        status, data = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/statuses")
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes/filters/statuses")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500

@app.route("/api/promo_codes/filters/offers", methods=["GET"])
def get_promo_codes_filters_offers():
    try:
        status, data = _cookie_get("https://seller.ggsel.com/api/v1/promo_codes/filters/offers")
        if status == 401:
            status, data = _v1_get("/api/v1/promo_codes/filters/offers")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500"""

if old_promo_filters in content:
    content = content.replace(old_promo_filters, new_promo_filters)

# Fix ledger to have fallback to api_receipts
old_ledger = """@app.route("/api/ledger", methods=["GET"])
def get_ledger():
    url = "https://seller.ggsel.com/api/v1/ledger_items"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    return jsonify({"ok": False, "stub": True, "items": []}), 401"""

new_ledger = """@app.route("/api/ledger", methods=["GET"])
def get_ledger():
    url = "https://seller.ggsel.com/api/v1/ledger_items"
    status, data = _cookie_get(url, params=request.args)
    if status == 200:
        return jsonify(data), 200
    
    # Fallback to receipts
    try:
        # We need to call the api_receipts logic, but it's another route. 
        # Let's just do v2_get("/api/v1/receipts") as receipts does
        c, d = v2_get("/api/v1/receipts", params=request.args)
        if c == 200:
            return jsonify(d), 200
    except:
        pass
        
    return jsonify({"ok": False, "stub": True, "items": []}), 401"""

if old_ledger in content:
    content = content.replace(old_ledger, new_ledger)

# Add Option 2: /api/offer/{id}/public
opt2 = """
@app.route("/api/offer/<int:offer_id>/public", methods=["GET"])
def api_offer_public(offer_id):
    try:
        status, data = _cookie_get(f"https://seller.ggsel.com/api/v1/offers/ggsel/{offer_id}")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500
"""
if "/api/offer/<int:offer_id>/public" not in content:
    content += opt2

with open(app_py_path, "w", encoding="utf-8") as f:
    f.write(content)

# Fix 3: create warmer_routes.py
warmer_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/warmer_routes.py"
with open(warmer_path, "w", encoding="utf-8") as f:
    f.write("from flask import Blueprint\n\nwarmer_bp = Blueprint('warmer', __name__)\n")

print("Updated app.py and created warmer_routes.py")
