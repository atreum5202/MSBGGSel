Option Explicit

' ============================================================================
'  GGselV7 — единая точка запуска
'  Запускает Flask-панель, Telegram-бот и Watchdog в фоне (без окон).
'  Если Flask уже работает — просто открывает браузер.
'  Логи: logs\app.log, logs\bot.log, logs\watchdog.log
' ============================================================================

Const PORT    = 5000
Const URL     = "http://127.0.0.1:5000"
Const TIMEOUT = 25

Dim sh, fso, root, logs, chk, i

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
logs = root & "\logs"

If Not fso.FolderExists(logs) Then fso.CreateFolder logs

' ── 1. Если Flask уже работает — просто открываем браузер ──────────────────
chk = sh.Run("cmd /c curl -sf " & URL & " >nul 2>&1", 0, True)
If chk = 0 Then
    sh.Run "chrome.exe --profile-directory=""MSB"" " & URL, 1, False
    WScript.Quit 0
End If

' ── 2. Flask ──────────────────────────────────────────────────────────────────
sh.Run "cmd /c cd /d """ & root & """ && python app.py >> """ & logs & "\app.log"" 2>&1", 0, False

' ── 3. Telegram-бот ───────────────────────────────────────────────────────────
sh.Run "cmd /c cd /d """ & root & """ && python -m bot.main >> """ & logs & "\bot.log"" 2>&1", 0, False

' ── 4. Watchdog ───────────────────────────────────────────────────────────────
sh.Run "cmd /c cd /d """ & root & """ && python watchdog.py >> """ & logs & "\watchdog.log"" 2>&1", 0, False

' ── 5. Ждём пока Flask поднимется ────────────────────────────────────────────
For i = 1 To TIMEOUT
    WScript.Sleep 1000
    chk = sh.Run("cmd /c curl -sf " & URL & " >nul 2>&1", 0, True)
    If chk = 0 Then Exit For
Next

' ── 6. Открываем браузер ──────────────────────────────────────────────────────
sh.Run "chrome.exe --profile-directory=""MSB"" " & URL, 1, False

WScript.Quit 0
