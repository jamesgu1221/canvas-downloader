Option Explicit

Dim oShell, oFSO, sDir, sPythonw, sTmp, sCmd, oFile

Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

sDir = oFSO.GetParentFolderName(WScript.ScriptFullName)
oShell.CurrentDirectory = sDir

' 用隐藏 cmd 获取 pythonw.exe 绝对路径，写入临时文件
sTmp = oShell.ExpandEnvironmentStrings("%TEMP%") & "\canvas_gui_pypath_" & Int(Timer * 1000) & ".txt"
sCmd = "cmd /c python -c """ & _
       "import sys,pathlib;print(pathlib.Path(sys.executable).with_name('pythonw.exe'))" & _
       """ > """ & sTmp & """"
oShell.Run sCmd, 0, True   ' 窗口样式 0=隐藏，True=等待结束

sPythonw = "pythonw"       ' 兜底：直接用 PATH 里的 pythonw
On Error Resume Next
If oFSO.FileExists(sTmp) Then
    Set oFile = oFSO.OpenTextFile(sTmp, 1)
    Dim sLine : sLine = Trim(oFile.ReadLine())
    oFile.Close
    oFSO.DeleteFile sTmp
    If Len(sLine) > 0 And oFSO.FileExists(sLine) Then sPythonw = sLine
End If
On Error GoTo 0

' 窗口样式 0=隐藏，False=不等待（立即退出 wscript）
oShell.Run """" & sPythonw & """ -m canvas_dl.gui", 0, False
