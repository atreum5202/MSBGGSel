Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /k ""cd /d C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB && set PYTHONIOENCODING=utf-8 && python app.py""", 1, False
