#SingleInstance Force   ; 当脚本已经运行时自动重启脚本。
                        ; #SingleInstance: Force - 自动重启; Ignore - 不重启; Prompt - 询问是否重启（默认选项）; Off - 允许同时运行多个实例.
#NoEnv                  ; 为了性能和与未来AutoHotkey版本的兼容性，推荐使用。
#NoTrayIcon             ; 不显示托盘图标。
; #Warn                 ; 启用警告，以协助检测常见错误。


SetWorkingDir %A_ScriptDir%     ; 设置脚本所在位置为工作目录。
SetBatchLines -1                ; 设置脚本运行速度；
                                ; SetBatchLines: -1 - 全速运行；20ms - 每次运行 20 ms 之后休眠 10 ms；LineCount(即直接输入数字而无ms结尾) - 休眠前要执行脚本的行数，与ms模式互斥。

Gui -MaximizeBox +AlwaysOnTop -DPIScale

Gui Show, w900 h360, Fallout 3 Launcher

Gui Font, s15 Bold, Arial
Gui Add, Text, x56 y66 w220 h35 ,  A
Gui Add, Text, x56 y101 w220 h35 , Simple
Gui Add, Text, x56 y136 w220 h35 , Fallout 3
Gui Add, Text, x56 y171 w220 h35 , With
Gui Add, Text, x56 y206 w220 h35 , Mod Organizer
Gui Add, Text, x56 y241 w220 h35 , Launcher

Gui Font, s9 Bold, Arial
Gui Add, Button, gFOSE x336 y56 w200 h50, &Fallout 3 With MO2
Gui Add, Button, gFallout3 x336 y146 w200 h50, &Fallout 3
Gui Add, Button, gMO2 x336 y236 w200 h50, &Mod Organizer 2
Gui Add, Button, gFLauncherMO2 x600 y56 w200 h50, &Fallout 3 Launcher With MO2
Gui Add, Button, gFLauncher x600 y146 w200 h50, &Fallout 3 Launcher
Gui Add, Button, gLOOT x600 y236 w200 h50, &LOOT

Return


FOSE:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:FOSE",
ExitApp

Fallout3:
  WinMinimize
    Runwait, ".\Fallout3.exe"
ExitApp

MO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe"
  WinRestore
Return

FLauncherMO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Fallout Launcher",
ExitApp


FLauncher:
  WinMinimize
    Runwait, ".\OblivionLauncher.exe"
ExitApp

LOOT:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:LOOT",
  WinRestore
Return

GuiEscape:
GuiClose:
  ExitApp
