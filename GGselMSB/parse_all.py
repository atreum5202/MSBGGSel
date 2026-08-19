"""
parse_all.py — единый запуск всех 5 воркеров с общим выводом в один терминал.

Запуск: python parse_all.py
"""
import json, sqlite3, subprocess, sys, threading, time
from pathlib import Path

PYTHON = sys.executable
SCRIPT = str(Path(__file__).parent / "bulk_parse.py")

# 2281 оставшихся категорий (1291–3572) делим на 5 аккаунтов = ~456 на каждого
# 3572 категории / 5 аккаунтов = 714 на каждого
WORKERS = [
    {"token": 0, "start":    0, "limit": 715, "account": "artur.doil1891"},
    {"token": 1, "start":  715, "limit": 715, "account": "abdurashidov.private"},
    {"token": 2, "start": 1430, "limit": 715, "account": "atreum.5202"},
    {"token": 3, "start": 2145, "limit": 715, "account": "abdurashidov.business"},
    {"token": 4, "start": 2860, "limit": 0,   "account": "boris.liron"},
]

COLORS = [
    "\033[96m",   # cyan
    "\033[92m",   # green
    "\033[93m",   # yellow
    "\033[95m",   # magenta
    "\033[94m",   # blue
]
RESET = "\033[0m"
BOLD  = "\033[1m"

_print_lock = threading.Lock()

# ── Путь к БД ─────────────────────────────────────────────────────────────────
_DB_PATH = str(Path(__file__).parent / "data" / "db" / "parser.db")


def _read_all_progress() -> str:
    """Читает все файлы bulk_parse_progress_wN.json и возвращает строку-сводку."""
    total_saved = 0
    total_found = 0
    parts = []
    for w in WORKERS:
        wid = w["token"]
        f = Path(__file__).parent / f"data/bulk_parse_progress_w{wid}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                saved = d.get("saved", 0)
                found = d.get("found", 0)
                total_found = d.get("total_cats", "?")
                total_saved += saved
                total_found += found
                parts.append(f"W{wid+1}={saved:,}")
            except Exception:
                parts.append(f"W{wid+1}=ERR")
        else:
            parts.append(f"W{wid+1}=—")
    summary = " | ".join(parts)
    return f"Прогресс воркеров: {summary} | Итого saved={total_saved:,}"


def _db_count() -> int:
    """Возвращает реальное количество товаров в SQLite (минуя WAL-буфер)."""
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        n = conn.execute("SELECT COUNT(*) FROM parsed_products").fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def _wal_size_mb() -> float:
    """Возвращает размер WAL-файла в МБ."""
    wal = Path(_DB_PATH + "-wal")
    return wal.stat().st_size / 1_048_576 if wal.exists() else 0.0


def _monitor_loop(procs: list, interval: int = 30):
    """Фоновый поток: каждые interval секунд печатает сводку по БД и прогрессу воркеров."""
    while True:
        time.sleep(interval)
        alive = sum(1 for p in procs if p.poll() is None)
        if alive == 0:
            break
        db_n  = _db_count()
        wal_mb = _wal_size_mb()
        prog  = _read_all_progress()
        with _print_lock:
            print(f"\n{BOLD}[MONITOR]{RESET} БД: {db_n:,} товаров | WAL: {wal_mb:.1f}MB | Процессов: {alive} живых")
            print(f"         {prog}\n", flush=True)

def stream(proc, wid, color, account):
    prefix = f"{color}{BOLD}[W{wid+1} {account}]{RESET} "
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        with _print_lock:
            print(f"{prefix}{line}", flush=True)

def main():
    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  GGSEL PARSER — 5 аккаунтов × 3 потока = 15 воркеров{RESET}")
    print(f"{BOLD}  Все 3572 категории | 5 аккаунтов x 3 потока | ETA: ~36 мин{RESET}")
    print(f"{BOLD}{'='*65}{RESET}\n")

    for i, w in enumerate(WORKERS):
        color = COLORS[i]
        end = w['start'] + (w['limit'] or 457)
        print(f"  {color}{BOLD}W{i+1}{RESET} {w['account']:35s} cats {w['start']}–{end}")
    print()

    procs   = []
    threads = []

    for i, w in enumerate(WORKERS):
        cmd = [
            PYTHON, SCRIPT,
            "--workers",     "4",
            "--start-from",  str(w["start"]),
            "--token-index", str(w["token"]),
        ]
        if w["limit"]:
            cmd += ["--limit", str(w["limit"])]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path(__file__).parent),
        )
        procs.append(proc)

        t = threading.Thread(
            target=stream,
            args=(proc, i, COLORS[i], w["account"]),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Запускаем фоновый монитор БД (каждые 30 сек)
    monitor = threading.Thread(target=_monitor_loop, args=(procs, 30), daemon=True)
    monitor.start()

    t0 = time.time()
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print(f"\n{BOLD}Остановка...{RESET}")
        for p in procs:
            try: p.terminate()
            except: pass

    elapsed = int(time.time() - t0)
    m, s = divmod(elapsed, 60)
    # Финальная сводка из БД
    db_final = _db_count()
    prog_final = _read_all_progress()
    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  Готово! Время: {m}м {s}с{RESET}")
    print(f"{BOLD}  БД (реально в файле): {db_final:,} товаров{RESET}")
    print(f"{BOLD}  {prog_final}{RESET}")
    print(f"{BOLD}{'='*65}{RESET}\n")

if __name__ == "__main__":
    main()
