import sys
import os

app_py_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/app.py"
with open(app_py_path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: /api/offer/{id}/public
old_offer_public = """@app.route("/api/offer/<int:offer_id>/public", methods=["GET"])
def api_offer_public(offer_id):
    try:
        status, data = _cookie_get(f"https://seller.ggsel.com/api/v1/offers/ggsel/{offer_id}")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500"""

new_offer_public = """@app.route("/api/offer/<int:offer_id>/public", methods=["GET"])
def api_offer_public(offer_id):
    try:
        # User requested to try /api/v1/offers/{offer_id} instead of /api/v1/offers/ggsel/{offer_id}
        status, data = _cookie_get(f"https://seller.ggsel.com/api/v1/offers/{offer_id}")
        if status in [401, 404]:
            status, data = _v1_get(f"/api/v1/offers/{offer_id}")
        return jsonify(data), status
    except Exception as e:
        return jsonify({"ok": False, "stub": True, "error": str(e)}), 500"""

if old_offer_public in content:
    content = content.replace(old_offer_public, new_offer_public)

# Add WebSocket
ws_code = """
try:
    from flask_sock import Sock
    sock = Sock(app)
    
    @sock.route('/ws/chats')
    def ws_chats(ws):
        import websocket
        import threading
        import re
        
        try:
            # Client might send token or just a ping
            client_msg = ws.receive()
        except:
            client_msg = ""
            
        token = client_msg if len(client_msg) > 20 else ""
        if not token:
            h = _get_cookie_header()
            m = re.search(r'ACCESS_TOKEN=([^;]+)', h)
            if m: token = m.group(1)
            
        try:
            upstream = websocket.create_connection(
                f"wss://wss.ggsel.com/cable?access_token={token}",
                timeout=10
            )
            def relay():
                try:
                    while True: 
                        ws.send(upstream.recv())
                except: pass
            threading.Thread(target=relay, daemon=True).start()
            while True:
                upstream.send(ws.receive())
        except:
            pass
except ImportError:
    pass
"""
if "@sock.route('/ws/chats')" not in content:
    content += ws_code

with open(app_py_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated app.py")
