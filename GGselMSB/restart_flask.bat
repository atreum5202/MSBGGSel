@echo off
echo =========================================
echo       Restarting Flask (app.py)
echo =========================================
echo.

echo [~] Finding process on port 5000...
for /f "tokens=5" %%a in ('netstat -aon ^| find "LISTENING" ^| find ":5000"') do (
    echo [~] Killing PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)

echo [~] Waiting 2 seconds...
timeout /t 2 /nobreak >nul

echo [~] Starting Flask in background...
set VBS_SCRIPT="%TEMP%\run_flask_hidden.vbs"
echo Set sh = CreateObject("WScript.Shell") > %VBS_SCRIPT%
echo sh.CurrentDirectory = "%~dp0" >> %VBS_SCRIPT%
echo sh.Run "cmd /c python app.py ^>^> logs\app.log 2^>^&1", 0, False >> %VBS_SCRIPT%
cscript //nologo %VBS_SCRIPT%
del %VBS_SCRIPT%

echo [+] Flask restarted successfully!
timeout /t 3
