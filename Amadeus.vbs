Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)

' 1. Launch Python backend daemon headlessly using pythonw.exe (0 = hidden window)
pythonwExe = strPath & "\.venv\Scripts\pythonw.exe"
wsScript = strPath & "\Codebase\amadeus_ws.py"
WshShell.Run chr(34) & pythonwExe & chr(34) & " " & chr(34) & wsScript & chr(34), 0, False

' 2. Brief wait for WebSocket server to bind port 8765
WScript.Sleep 1500

' 3. Launch Tauri Desktop Client (standalone Amadeus.exe if built, else run_desktop.bat)
releaseExe = strPath & "\desktop\src-tauri\target\release\Amadeus.exe"
If fso.FileExists(releaseExe) Then
    WshShell.Run chr(34) & releaseExe & chr(34), 1, False
Else
    WshShell.Run chr(34) & strPath & "\run_desktop.bat" & chr(34), 1, False
End If
