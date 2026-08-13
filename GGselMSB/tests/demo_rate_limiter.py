"""Демонстрация AdaptiveRateLimiter с реальным путём data/rate_state.json."""
import sys
import io
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parser.adaptive_rate_limiter import AdaptiveRateLimiter  # noqa: E402

# Демо: используем реальный путь, как в проде
lim = AdaptiveRateLimiter(storage_path="data/rate_state.json")
print("Инициализирован с путём data/rate_state.json")
print("Config:")
for k, v in lim.summary().items():
    if k != "profiles":
        print(f"  {k} = {v}")
print()

# Сбросим чтобы демо было чистым
lim.reset()

print("Demo: один профиль, симулируем серию запросов:")
for status in [200, 200, 200, 429, 200, 401, 200, 503]:
    pid = "demo-profile"
    lim.record(pid, status)
    s = lim.get_state(pid)
    flag = " (PROBLEMATIC!)" if s["is_problematic"] else ""
    print(f"  record({status:>3}) -> delay={s['delay']:.2f}s, ok={s['consecutive_ok']}, err={s['consecutive_errors']}{flag}")

lim.force_save()
print()
print("Содержимое data/rate_state.json:")
state = Path("data/rate_state.json")
if state.exists():
    print(state.read_text(encoding="utf-8"))
else:
    print("  (файл не создан)")
