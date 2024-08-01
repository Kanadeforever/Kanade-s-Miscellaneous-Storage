#Requires Autohotkey v2.0

#SingleInstance Force

SendMode("Input")                                           ; SendMode("Input")指令设置发送模式为Input，这种模式比默认的Send模式更快、更可靠。
SetWorkingDir(A_ScriptDir)                                  ; SetWorkingDir指令将工作目录设置为脚本所在的目录，确保脚本在运行时使用一致的目录。

^+!Backspace::                                              ; 定义一个热键组合Ctrl+Shift+Alt+Backspace来触发以下代码。
{
    ActiveWindowTitle := WinGetTitle("AWindow")                   ; WinGetTitle指令获取当前活动窗口的标题，并将其存储在变量ActiveWindowTitle中。
    WinGetPos(, , &Width, &Height, ActiveWindowTitle)       ; WinGetPos指令获取当前活动窗口的宽度和高度，并将其分别存储在变量Width和Height中。
    TargetX := (A_ScreenWidth/2)-(Width/2)                  ; 计算目标X坐标，使窗口水平居中。
    TargetY := (A_ScreenHeight/2)-(Height/2)                ; 计算目标Y坐标，使窗口垂直居中。
    WinMove(TargetX, TargetY, , , ActiveWindowTitle)        ; WinMove指令将窗口移动到计算出的目标X和目标Y坐标位置。
    return
}
