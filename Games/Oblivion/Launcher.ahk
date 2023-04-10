#SingleInstance Force   ; 当脚本已经运行时自动重启脚本。
                        ; #SingleInstance: Force - 自动重启; Ignore - 不重启; Prompt - 询问是否重启（默认选项）; Off - 允许同时运行多个实例.
#NoEnv                  ; 为了性能和与未来AutoHotkey版本的兼容性，推荐使用。
#NoTrayIcon             ; 不显示托盘图标。
; #Warn                 ; 启用警告，以协助检测常见错误。


SetWorkingDir %A_ScriptDir%     ; 设置脚本所在位置为工作目录。
SetBatchLines -1                ; 设置脚本运行速度；
                                ; SetBatchLines: -1 - 全速运行；20ms - 每次运行 20 ms 之后休眠 10 ms；LineCount(即直接输入数字而无ms结尾) - 休眠前要执行脚本的行数，与ms模式互斥。

Gui -MaximizeBox -MinimizeBox +AlwaysOnTop -DPIScale

Gui Show, w273 h251, TESIV Launcher

Gui Font, s9 Bold, Arial
Gui Add, Button, gOblivionWithOBSE x50 y20 w172 h62, &Oblivion With OBSE
Gui Add, Button, gOblivionLauncher x50 y94 w172 h62, &Oblivion Launcher
Gui Add, Button, gOblivionModManager x50 y168 w172 h62, &Oblivion Mod Manager

Return


OblivionWithOBSE:
  WinMinimize
  Runwait, ".\obse_loader.exe",
ExitApp

OblivionLauncher:
  WinMinimize
  Runwait, ".\orgOblivionLauncher.exe"
ExitApp

OblivionModManager:
  WinMinimize
  Runwait, ".\OblivionModManager.exe"
WinRestore
Return

GuiEscape:
GuiClose:
  ExitApp
