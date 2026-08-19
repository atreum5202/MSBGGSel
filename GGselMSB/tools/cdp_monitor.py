"""
cdp_monitor.py — CDP Ring Buffer монитор в реальном времени.
Показывает все запросы браузера (XHR, fetch, document) по мере их появления.

Запуск: python cdp_monitor.py [profile_id]
"""
import sys, time, httpx, json, datetime

PROFILE_ID = sys.argv[1] if len(sys.argv) > 1 else "1873432d-b054-48a6-a031-b2bacc0fe77d"
MSB = "http://127.0.0.1:17248"
POLL_SEC = 1.0

COLORS = {
    "GET":    "\033[96m",   # cyan
    "POST":   "\033[93m",   # yellow
    "PUT":    "\033[95m",   # magenta
    "DELETE": "\033[91m",   # red
    "PATCH":  "\033[95m",
}
GREEN  = "\033[92m"
GRAY   = "\033[90m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def method_color(m):
    return COLORS.get(m, "\033[97m")

def status_color(s):
    if not s:      return GRAY
    if s < 300:    return GREEN
    if s < 400:    return "\033[93m"
    return "\033[91m"

seen_seqs = set()

print(f"{BOLD}MSB CDP Monitor{RESET} — профиль {PROFILE_ID[:8]}...")
print(f"{GRAY}Ctrl+C для остановки{RESET}\n")

while True:
    try:
        r = httpx.get(f"{MSB}/profiles/{PROFILE_ID}/network/requests",
                      params={"limit": 200}, timeout=2)
        if r.status_code == 200:
            entries = r.json().get("data", [])
            new = [e for e in entries if e.get("n") not in seen_seqs]
            for e in reversed(new):
                seen_seqs.add(e.get("n"))
                m   = e.get("method", "?")
                url = e.get("request", {}).get("url") or e.get("url", "")
                st  = e.get("status")
                ct  = e.get("response", {}).get("mimeType", "")
                ts  = e.get("capturedAt", "")
                if ts:
                    try:
                        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        ts = t.strftime("%H:%M:%S")
                    except Exception:
                        ts = ts[:8]

                # Только интересные типы
                skip = any(x in url for x in [
                    "fonts.gstatic", "fonts.googleapis", "yastatic",
                    "data:image", "favicon"
                ])
                if skip:
                    continue

                is_json = "json" in ct
                icon = "📦" if is_json else "📄"
                mc   = method_color(m)
                sc   = status_color(st)
                st_s = str(st) if st else "---"

                # Укорачиваем URL
                short_url = url
                if len(short_url) > 90:
                    short_url = short_url[:87] + "..."

                print(f"{GRAY}{ts}{RESET} {icon} {mc}{m:6}{RESET} {sc}{st_s}{RESET}  {short_url}")

    except httpx.ConnectError:
        print(f"{GRAY}[MSB недоступен, жду...]{RESET}")
    except KeyboardInterrupt:
        print("\nОстановлено.")
        break
    except Exception as e:
        print(f"{GRAY}[err: {e}]{RESET}")

    time.sleep(POLL_SEC)
