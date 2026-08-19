Set WshShell = CreateObject("WScript.Shell")
' Сначала останавливаем MSB
WshShell.Run "cmd /c ""C:\Users\Atreum\Desktop\MSBWorkshop\MSB\stop.bat""", 1, True
WScript.Sleep 3000
' Запускаем MSB заново
WshShell.Run """C:\Users\Atreum\Desktop\MSBWorkshop\MSB\start.vbs""", 1, False
