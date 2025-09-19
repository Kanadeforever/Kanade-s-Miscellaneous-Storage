#Requires AutoHotkey v2.0+
#SingleInstance Force

A_TrayMenu.Delete()
A_TrayMenu.Add("说明", ShowHelp)
A_TrayMenu.Add("退出", (*) => ExitApp())
TraySetIcon("shell32.dll", 44)
A_IconTip:= "WIN+V 组合键已被本程序接管`n`n请确认Ditto已经启动并且设置好对应快捷键`n`n如需恢复 WIN+V 原本的功能请退出本工具"
TrayTip "剪贴板已被接管", "WIN+V 快捷键代理工具", 1

#v::{
    Send "+!{NumpadMult}"
    Sleep(50)
    if WinExist("ahk_class QPasteClass ahk_exe Ditto.exe")
        WinActivate()
    return
}

ShowHelp(*) {
    MsgBox "这是一个用于接管系统默认的`n`n【 Win+V 打开剪贴板 】功能的工具。`n`n工具需要配合`"Ditto`"，或是其他可以自定义快捷键的剪贴板管理器工具一起使用。单独使用仅能屏蔽【WIN+V】与发送组合按键`n【SHIFT+ALT+*】`n在使用之前，请务必要提前把剪贴板软件的快捷键设置为`n`n【 SHIFT + ALT + NumpadMult(小键盘的星号/乘号键) 】`n`n作者：Luminous", "工具说明", 32
}

