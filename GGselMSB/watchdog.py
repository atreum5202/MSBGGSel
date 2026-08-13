"""
watchdog.py — сторожевой процесс GGselV7.

Мониторит:
  - Flask-панель (http://127.0.0.1:5000)
  - MSB-сервис   (http://127.0.0.1:17248/health)

При падении:
  - Пишет в лог
  - Отправляет уведомление в Telegram через /api/bot/notify
  - Перезапускает Flask если он упал

Запуск: python watchdog.py  (из корня GGselV7)
"""
import sys
import time
import logging
import threading
import subprocess
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Логирование
logs_dir = BASE_DIR / "logs"
logs_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Watchdog] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(logs_dir / "watchdog.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("watchdog")

# ── Настройки ─────────────────────────────────────────────────────────────────

PANEL_URL  = "http://127.0.0.1:5000"
MSB_URL    = "http://127.0.0.1:17248/health"
CHECK_INTERVAL = 30          # сек между проверками
RESTART_COOLDOWN = 120       # сек после рестарта — не трогать снова

SERVICES = {
    "Panel (Flask)": PANEL_URL,
    "MSB":           MSB_URL,
}


# ── Утилиты ───────────────────────────────────────────────────────────────────

def check_http(url: str, timeout: int = 5) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def tg_alert(text: str, level: str = "error"):
    """Отправить алерт через Flask /api/bot/notify."""
    try:
        requests.post(
            f"{PANEL_URL}/api/bot/notify",
            json={"text": text, "level": level},
            timeout=5,
        )
    except Exception:
        pass  # если панель тоже упала — ничего не можем сделать


def restart_panel():
    log.info("Перезапускаю Flask-панель (app.py)...")
    cmd = (
        f'cmd /c start "GGselV7-Panel" /min '
        f'cmd /k "cd /d \\"{BASE_DIR}\\" && python app.py"'
    )
    try:
        subprocess.Popen(cmd, shell=True, cwd=str(BASE_DIR))
    except Exception as e:
        log.error("Не удалось запустить панель: %s", e)


# ── Главный цикл ──────────────────────────────────────────────────────────────

def main():
    # log_rotation модуль не используется в этой ветке

    log.info(
        "Watchdog запущен. Мониторим: %s",
        ", ".join(SERVICES.keys()),
    )

    # Ротация логов в фоне

    is_down: dict[str, bool] = {name: False for name in SERVICES}
    last_restart_at: dict[str, float] = {name: 0.0 for name in SERVICES}

    while True:
        for name, url in SERVICES.items():
            alive = check_http(url)

            if not alive and not is_down[name]:
                log.error("Сервис %s недоступен! (%s)", name, url)
                is_down[name] = True

                tg_alert(f"🚨 <b>Watchdog</b>: сервис <b>{name}</b> упал! ({url})")

                # Авто-рестарт только для Flask-панели
                if "Panel" in name:
                    now = time.time()
                    if now - last_restart_at[name] > RESTART_COOLDOWN:
                        restart_panel()
                        last_restart_at[name] = now
                    else:
                        log.info("Рестарт %s пропущен (cooldown)", name)

            elif alive and is_down[name]:
                log.info("Сервис %s восстановил работу.", name)
                is_down[name] = False
                tg_alert(f"✅ Сервис <b>{name}</b> снова доступен.", level="info")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
