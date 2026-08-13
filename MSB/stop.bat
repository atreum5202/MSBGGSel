@echo off
chcp 437 >nul 2>&1
title MSB - Stop
cd /d "%~dp0"

echo Stopping MSB...

curl -sf -X POST http://127.0.0.1:17248/api/shutdown >nul 2>&1
timeout /t 3 /nobreak >nul

taskkill /F /IM electron.exe /T >nul 2>&1
taskkill /F /IM node.exe /T     >nul 2>&1

for /f "tokens=5" %%A in ('netstat -aon 2^>nul ^| findstr /R ":17248 "') do (
    if "%%A" NEQ "0" taskkill /F /PID %%A >nul 2>&1
)
for /f "tokens=5" %%A in ('netstat -aon 2^>nul ^| findstr /R ":17246 "') do (
    if "%%A" NEQ "0" taskkill /F /PID %%A >nul 2>&1
)

timeout /t 2 /nobreak >nul
echo [OK] MSB stopped
pause
