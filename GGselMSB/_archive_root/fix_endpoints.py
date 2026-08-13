import sys

app_py_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/app.py"
with open(app_py_path, "r", encoding="utf-8") as f:
    content = f.read()

new_routes = '''
@app.route("/api/notifications")
def api_notifications():
    try:
        data = _cookie_get("https://seller.ggsel.com/api/v1/account/notifications")
        return jsonify({"ok": True, "items": data})
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "items": []})

@app.route("/api/whitelisted_ips")
def api_whitelisted_ips():
    try:
        data = _cookie_get("https://seller.ggsel.com/api/v1/account/whitelisted_ips")
        return jsonify({"ok": True, "items": data})
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "items": []})
'''

if "/api/notifications" not in content:
    # insert before @app.route("/api/dashboard")
    idx = content.find('@app.route("/api/dashboard")')
    if idx != -1:
        content = content[:idx] + new_routes + "\n" + content[idx:]
    else:
        content += "\n" + new_routes

with open(app_py_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated app.py")
