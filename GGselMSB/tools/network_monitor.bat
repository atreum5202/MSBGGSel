@echo off
chcp 65001 >nul
title NetworkCapture Monitor — HTTP + WS live
color 0A
cd /d C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB

echo.
echo ============================================================
echo  NetworkCapture Live Monitor
echo  Все HTTP запросы + WebSocket фреймы браузера в реальном времени
echo  Ctrl+C — остановить
echo ============================================================
echo.

:loop
python network_monitor.py %1
timeout /t 3 /nobreak >nul
goto loop
