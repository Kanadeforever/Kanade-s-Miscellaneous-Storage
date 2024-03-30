Set WshShell = CreateObject("WScript.Shell")
' 设置工作目录为vbs脚本所在目录
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' 运行 Game.exe
WshShell.Run "Game.exe", 1, False
' 如果启动程序的路径或者名称有空格，则需要添加额外的双引号，比如下面这样
' WshShell.Run """E:\Software\Magpie fix\Magpie.exe""",

' 等待5秒
WScript.Sleep 3000

' 运行 Magpie.exe
WshShell.Run """C:\Program Files\Ext\XXX.exe""", 7, False

' 将 A.exe 设置为前台窗口
' WshShell.AppActivate "Game.exe"

' 每隔3秒检测一次Game.exe是否在运行，如果没有运行则结束Magpie.exe
Do While True
    ' 检查Game.exe是否在运行
    Set colProcessList = GetObject("Winmgmts:").ExecQuery ("Select * from Win32_Process Where Name = 'Game.exe'")
    If colProcessList.Count = 0 Then
        ' 如果Game.exe没有运行，结束Magpie.exe
        WshShell.Run "taskkill /F /IM Magpie.exe", 1, False
        ' 如果要结束管理员权限运行的窗口，使用这段：
        ' WshShell.Run "powershell -Command ""(Get-WmiObject -Class Win32_Process -Filter 'name = ''你的exe名字.exe''').Terminate()""", 1, False
        ' 上面这段如果要同时结束多个exe，则在exe的两个引号后接【OR name = ''cheatengine-i386.exe''】，包含空格
        Exit Do
    End If
    ' 等待3秒
    WScript.Sleep 3000
Loop

'如果想显示中文提示，需要把脚本的编码由UTF-8改成GBK