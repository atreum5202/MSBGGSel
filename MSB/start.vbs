Option Explicit

Dim sh, fso, root, appdata, msbData
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root    = fso.GetParentFolderName(WScript.ScriptFullName)
appdata = sh.ExpandEnvironmentStrings("%APPDATA%")
msbData = appdata & "\MSB"

If Not fso.FolderExists(msbData)               Then fso.CreateFolder msbData
If Not fso.FolderExists(msbData & "\profiles") Then fso.CreateFolder msbData & "\profiles"
If Not fso.FolderExists(msbData & "\logs")     Then fso.CreateFolder msbData & "\logs"

Dim logFile
logFile = msbData & "\logs\msb.log"

Dim port
port = "17248"
Dim envFile
envFile = root & "\.env"
If fso.FileExists(envFile) Then
    Dim ts, line
    Set ts = fso.OpenTextFile(envFile, 1)
    Do While Not ts.AtEndOfStream
        line = Trim(ts.ReadLine())
        If Left(line, 12) = "MSB_API_PORT" Then
            Dim parts
            parts = Split(line, "=")
            If UBound(parts) >= 1 Then
                Dim v
                v = Trim(parts(1))
                If v <> "" And Left(v, 1) <> "#" Then port = v
            End If
        End If
    Loop
    ts.Close
End If

Dim chk
chk = sh.Run("cmd /c curl -sf http://127.0.0.1:" & port & "/health >nul 2>&1", 0, True)
If chk = 0 Then
    sh.Run "cmd /c curl -sf -X POST http://127.0.0.1:" & port & "/api/show-window >nul 2>&1", 0, False
    WScript.Quit 0
End If

Dim needInstall
needInstall = Not fso.FolderExists(root & "\node_modules")

If needInstall Then
    Dim installLog
    installLog = msbData & "\logs\msb-install.log"
    sh.Run "cmd /c cd /d """ & root & """ && npm install --legacy-peer-deps --no-audit --no-fund >""" & installLog & """ 2>&1", 0, True
End If

Dim needBuild
needBuild = Not fso.FileExists(root & "\dist\renderer\index.html")

If needBuild Then
    Dim buildLog
    buildLog = msbData & "\logs\msb-build.log"
    sh.Run "cmd /c cd /d """ & root & """ && npm run build:renderer >>""" & buildLog & """ 2>&1", 0, True
End If

Dim envArg
If Not fso.FileExists(root & "\.env") Then
    If fso.FileExists(root & "\.env.example") Then
        fso.CopyFile root & "\.env.example", root & "\.env"
    End If
End If

Dim cmd
cmd = "cmd /c set MSB_API_PORT=" & port & "& " & _
      "set MSB_PROFILES_DIR=" & msbData & "\profiles& " & _
      "set MSB_LOG_DIR=" & msbData & "\logs& " & _
      "cd /d """ & root & """ && " & _
      "npm run start " & _
      ">""" & logFile & """ 2>&1"

sh.Run cmd, 0, False

Dim waited, ready
waited = 0
ready = False
Do While waited < 30
    WScript.Sleep 1000
    waited = waited + 1
    Dim chk2
    chk2 = sh.Run("cmd /c curl -sf http://127.0.0.1:" & port & "/health >nul 2>&1", 0, True)
    If chk2 = 0 Then
        ready = True
        Exit Do
    End If
Loop
If ready Then
    WScript.Sleep 500
    sh.Run "cmd /c curl -sf -X POST http://127.0.0.1:" & port & "/api/show-window >nul 2>&1", 0, False
End If
