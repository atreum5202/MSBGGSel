"""
network_monitor.py — Live-монитор NetworkCapture MSB

Показывает все новые HTTP запросы и WebSocket фреймы в реальном времени.
Работает пока профиль запущен.

Запуск:
  python network_monitor.py
  python network_monitor.py 1873432d-b054-48a6-a031-b2bacc0fe77d
"""
import sys, time, json, urllib.request

PROFILE_ID = sys.argv[1] if len(sys.argv) > 1 else "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB_API    = "http://127.0.0.1:17248"
POLL_SEC   = 1.5

# Цвета для Windows CMD (ANSI)
R  = "\033[91m"  # красный  — ошибки
G  = "\033[92m"  # зелёный  — 2xx
Y  = "\033[93m"  # жёлтый   — 3xx / WS
C  = "\033[96m"  # циан     — заголовки
DIM = "\033[2m"
RST = "\033[0m"

METHODS = {"GET": "\033[94mGET\033[0m", "POST": "\033[95mPOST\033[0m",
           "PUT": "\033[93mPUT\033[0m", "DELETE": "\033[91mDEL\033[0m",
           "WS":  "\033[96mWS \033[0m"}

def get(path):
    try:
        with urllib.request.urlopen(f"{MSB_API}{path}", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None

def status_color(s):
    if not s: return DIM + "---" + RST
    s = int(s)
    if s < 300: return G + str(s) + RST
    if s < 400: return Y + str(s) + RST
    return R + str(s) + RST

def fmt(entry):
    method = entry.get("method", "GET")
    host   = entry.get("host", "")
    path   = entry.get("path", "")[:60]
    status = entry.get("status")
    ws     = entry.get("_ws")
    t      = entry.get("type", "")
    n      = entry.get("n", "")

    if ws:
        payload = (entry.get("payload") or "")[:60]
        icon = "↙" if t == "ws_frame_in" else ("↗" if t == "ws_frame_out" else "⚡")
        return f"  {Y}{icon} WS{RST}  {host}{path}  {DIM}{payload}{RST}"

    body_preview = ""
    resp = entry.get("response") or {}
    body = resp.get("body", "")
    if body:
        body_preview = DIM + "  " + str(body)[:80].replace("\n", " ") + RST

    m = METHODS.get(method, method)
    sc = status_color(status)
    return f"  {m}  {sc}  {host}{path}{body_preview}"

def main():
    import os
    os.system("color")  # включить ANSI в Windows CMD

    print(f"\n{C}NetworkCapture Monitor{RST}  profile={PROFILE_ID}")
    print(f"{DIM}MSB API: {MSB_API}  poll={POLL_SEC}s{RST}\n")

    seen_n = 0
    consecutive_errors = 0

    while True:
        data = get(f"/profiles/{PROFILE_ID}/network/requests?limit=500")
        if not data:
            consecutive_errors += 1
            if consecutive_errors == 1:
                print(f"{R}[!] NetworkCapture недоступен — профиль запущен?{RST}")
            time.sleep(POLL_SEC * 2)
            continue

        consecutive_errors = 0
        entries = data if isinstance(data, list) else (data.get("data") or [])
        new = [e for e in entries if e.get("n", 0) > seen_n]

        for e in new:
            print(fmt(e))
            seen_n = max(seen_n, e.get("n", 0))

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[остановлен]")
