' EZ Audio Studio launcher without a console window.
' First run (no shared Python environment yet) opens start.bat visibly so pip progress
' and errors stay readable. Afterwards pythonw.exe starts the app with no cmd window.
Option Explicit
Dim shell, fso, root, venv, pythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
venv = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\EZAudioStudio\venv-py312"
pythonw = venv & "\Scripts\pythonw.exe"
shell.CurrentDirectory = root
If fso.FileExists(pythonw) Then
    shell.Run """" & pythonw & """ """ & root & "\bootstrap.py"" --windowed", 0, False
Else
    shell.Run """" & root & "\start.bat""", 1, False
End If
