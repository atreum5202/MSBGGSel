@echo off
title CDP Monitor — запросы браузера
color 0A
cd /d C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB
:loop
python cdp_monitor.py 1873432d-b054-48a6-a031-b2bacc0fe77d
timeout /t 3 /nobreak >nul
goto loop
