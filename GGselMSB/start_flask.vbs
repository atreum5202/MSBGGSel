Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c cd /d """ & root & """ && python app.py >> """ & root & "\logs\app.log"" 2>&1", 0, False
