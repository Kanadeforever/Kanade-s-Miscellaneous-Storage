#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon

SetWorkingDir(A_ScriptDir)

Launcher := Gui()
Launcher.OnEvent("Close", GuiEscape)
Launcher.OnEvent("Escape", GuiEscape)
Launcher.Opt("-MaximizeBox -MinimizeBox DPIScale")
Launcher.BackColor := "FFFFFF"

Launcher.Title := "Cold Fear Steam Ver. Fix Pack"
Launcher.Show("w232 h155")

Launcher.SetFont("s8 Bold", "Arial")

; 移除了旧版的 .Bind("Normal")，使用更简洁的 (*) 吸收参数
btnSteamInstall := Launcher.Add("Button", "x56 y30 w120 h32", "&安装Steam版修复")
btnSteamInstall.OnEvent("Click", SteamInstall)

btnSteamUninstall := Launcher.Add("Button", "x56 y80 w120 h32", "&卸载Steam版修复")
btnSteamUninstall.OnEvent("Click", SteamUninstall)
Return


SteamInstall(*)
{
    Launcher.Hide()

    ; HookSettings.ini.steam改名成HookSettings.ini (1表示允许覆盖)
    try FileMove("HookSettings.ini.steam", "HookSettings.ini", 1)

    ; ColdFear_retail.exe改名成ColdFear.exe (1表示允许覆盖)
    try FileMove("ColdFear_retail.exe", "ColdFear.exe", 1)
    
    ; ColdFear.exe把复制一份并改名ColdFear_retail.exe.bak
    try FileCopy("ColdFear.exe", "ColdFear_retail.exe.bak", 1)
    
    ; 把XidiLoader.exe改名成ColdFear_retail.exe
    try FileMove("XidiLoader.exe", "ColdFear_retail.exe", 1)
    
    ; 进入update文件夹，把后缀包含.steam的文件重命名
    try FileMove("update\ColdFear.WidescreenFix.asi.steam", "update\ColdFear.WidescreenFix.asi", 1)
    try FileMove("update\ColdFear.WidescreenFix.ini.steam", "update\ColdFear.WidescreenFix.ini", 1)
    
    ; 弹出提示并退出
    MsgBox("安装完成！", "提示", "Iconi")
    ExitApp()
}

SteamUninstall(*)
{
    Launcher.Hide() 
    
    ; 1、删除指定文件
    FilesToDelete := [
        "dinput8.dll", "HookSettings.ini", "HookSettings.ini.steam", "ColdFear_retail.exe",
        "ColdFear.exe", "ColdFear.exe.hookshot", "Hookshot.32.dll", "Hookshot.32.exe",
        "readme_fixpack.txt", "Xidi.32.dll", "Xidi.HookModule.32.dll","XidiLoader.exe", "xidi.ini"
    ]
    for file in FilesToDelete {
        try FileDelete(file)
    }
    
    ; 2、删除update文件夹 (参数1表示递归删除其内部所有子文件和文件夹)
    try DirDelete("update", 1)
    
    ; 3、把ColdFear_retail.exe.bak重命名为ColdFear_retail.exe
    try FileMove("ColdFear_retail.exe.bak", "ColdFear_retail.exe", 1)
    
    ; 4、弹出提示，告知卸载完成
    MsgBox("卸载完成！请务必进入Steam库内对游戏执行“验证完整性”操作！", "提示", "Iconi")
    
    ; 5、用户关闭确认框后删除这个程序本身
    ; 使用 cmd 利用 ping 命令延迟 2 秒后执行 del 删除当前运行的 exe
    Run(A_ComSpec ' /c ping 127.0.0.1 -n 2 > nul & del "' A_ScriptFullPath '"', , "Hide")
    ExitApp()
}

GuiEscape(*)
{
    ExitApp()
}