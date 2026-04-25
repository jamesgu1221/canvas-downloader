Option Explicit

' 无黑窗启动 PySide6 GUI（canvas_dl.gui_qt）。
'
' 启动策略：
' 1. 通过 `python -c` 打印 sys.executable 和 pythonw.exe 路径，写入临时文件；
'    命令行能跑的那个 python 才是装了 PySide6 / qfluentwidgets 的解释器。
' 2. **用 pythonw.exe + intShow=1 (SW_SHOWNORMAL)**。原因：
'    Windows ShowWindow() 规则——进程第一次调 ShowWindow 时，nCmdShow 会被
'    STARTUPINFO.wShowWindow 覆盖。WshShell.Run 的 intShow=0 (SW_HIDE) 会把
'    SW_HIDE 写进子进程 STARTUPINFO，Qt 第一次 ShowWindow(hwnd, SW_SHOWNORMAL)
'    被劫持成 SW_HIDE，WS_VISIBLE 永远置不上——窗口对象存在、事件循环转着、
'    屏幕上却看不见，表现为"双击没反应"。
'    intShow=1 则把 SW_SHOWNORMAL 传给子进程，Qt 的 ShowWindow 正常生效。
'    pythonw.exe 本身无 console，intShow=1 不会产生黑窗。
' 3. 若 pythonw.exe 不可用（少见，自编译或精简发行版），降级到 python.exe
'    + intShow=1；此时会短暂闪一下 console 窗口，但至少主窗口能显示。
' 4. 实在连 python 路径都取不到，弹框提示安装。
'
' 日志：canvas_dl/gui_qt/__main__.py 在 import app 之前就把 sys.stderr 重定向到
' canvas_gui_qt.log，启动异常可诊断。VBS 侧不再 `2>>log`，避免和 Python 抢句柄。

Dim oShell, oFSO, sDir, sExe, sPythonw, sTmp, sCmd, oFile

Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

sDir = oFSO.GetParentFolderName(WScript.ScriptFullName)
oShell.CurrentDirectory = sDir

' 用隐藏 cmd 获取当前 python 的 sys.executable 和 pythonw.exe 路径（两行）。
sTmp = oShell.ExpandEnvironmentStrings("%TEMP%") & "\canvas_gui_qt_pypath_" & Int(Timer * 1000) & ".txt"
sCmd = "cmd /c python -c """ & _
       "import sys,pathlib;p=pathlib.Path(sys.executable);" & _
       "print(p);print(p.with_name('pythonw.exe'))" & _
       """ > """ & sTmp & """ 2>&1"
oShell.Run sCmd, 0, True

sExe = ""
sPythonw = ""
On Error Resume Next
If oFSO.FileExists(sTmp) Then
    Set oFile = oFSO.OpenTextFile(sTmp, 1)
    If Not oFile.AtEndOfStream Then sExe = Trim(oFile.ReadLine())
    If Not oFile.AtEndOfStream Then sPythonw = Trim(oFile.ReadLine())
    oFile.Close
    oFSO.DeleteFile sTmp
End If
On Error GoTo 0

Dim sTarget
If sPythonw <> "" And oFSO.FileExists(sPythonw) Then
    sTarget = sPythonw
ElseIf sExe <> "" And oFSO.FileExists(sExe) Then
    sTarget = sExe
Else
    oShell.Popup "未检测到可用的 Python 解释器。" & vbCrLf & vbCrLf & _
                 "请先安装 Python 3.10+ 并把 python.exe 所在目录加入 PATH，" & vbCrLf & _
                 "然后在项目目录运行：pip install -r requirements.txt", _
                 0, "Canvas 课件下载器", 16
    WScript.Quit 1
End If

' intShow=1 (SW_SHOWNORMAL)：避免 SW_HIDE 被写进子进程 STARTUPINFO 劫持 Qt 的
' 首次 ShowWindow。pythonw.exe 无 console 窗口，不会有黑窗闪现。
' bWaitOnReturn=False 让 wscript 立即退出。
oShell.Run """" & sTarget & """ -m canvas_dl.gui_qt", 1, False
