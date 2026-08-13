"""Quick smoke test for api_parser_logs handler."""
import sys
import os
os.environ["FLASK_DEBUG"] = "0"

sys.path.insert(0, "C:\\Users\\Atreum\\Desktop\\MySoft\\GgsellerMoreLogin")

import app
from flask import request

def call():
    with app.app.test_request_context(request.full_path if hasattr(request, 'full_path') else request.url):
        resp = app.api_parser_logs()
        body = resp.get_json()
        return resp.status_code, body

print("--- Test 1: no args ---")
with app.app.test_request_context("/api/parser/logs"):
    status, body = call()
    print(f"  status: {status}, ok: {body.get('ok')}, logs: {len(body.get('logs', []))}, error: {body.get('error')}")

print("--- Test 2: with since_id + limit ---")
with app.app.test_request_context("/api/parser/logs?since_id=0&limit=5"):
    status, body = call()
    print(f"  status: {status}, ok: {body.get('ok')}, logs: {len(body.get('logs', []))}, last_id: {body.get('last_id')}")
    if body.get('logs'):
        for log in body['logs'][:3]:
            print(f"  - [{log['level']}] {log['message'][:80]}")

print("--- Test 3: invalid since_id (should not crash) ---")
with app.app.test_request_context("/api/parser/logs?since_id=abc"):
    try:
        status, body = call()
        print(f"  status: {status}, ok: {body.get('ok')}, error: {body.get('error')}")
    except Exception as e:
        print(f"  CRASHED: {type(e).__name__}: {e}")

print("DONE")
