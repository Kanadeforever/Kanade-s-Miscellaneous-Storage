;============================================================
; 窗口控制调整脚本
; 版本: 0.1
; 作者: Microsoft Copilot Think Deeper
; 描述: 本脚本用于精确调整目标窗口的客户区尺寸，
;       使调整后的客户区（内容区域）尺寸达到预期。
;
; 注意：
; 1. 默认操作当前活动窗口，经 MsgBox 显示当前活动窗口信息供用
;    户确认。（无输入窗口操作界面）
; 2. 采用 InputBox 分步输入客户区宽和高（不含标题栏和边框）。
; 3. 通过调用 GetClientRect 与 WinGetPos 动态计算窗口非客户区
;    （边框与标题栏）尺寸差值，从而计算出调整整体窗口所需的补
;    偿值，保证客户区尺寸精准。
; 4. 为支持不同 DPI 设置，本脚本在启动时
;    调用 SetProcessDPIAware。
;
; 可按需扩展：例如增加常用尺寸预设、多显示器下窗口居中、指定位
;             置调整等功能。
;============================================================

#Requires AutoHotkey v1.1+
#NoEnv
SendMode Input
SetWorkingDir %A_ScriptDir%

Menu, Tray, NoStandard                          ; 删除所有默认菜单项，确保只显示自定义项
Menu, Tray, Add, 帮助, ShowHelp                 ; 添加“帮助”菜单项，关联 ShowHelp 标签
Menu, Tray, Add,                                ; 添加分隔符
Menu, Tray, Add, 退出程序, ExitApplication      ; 添加一个自定义的退出菜单项

; 为确保在高DPI环境下精确测量，设置当前进程为 DPI aware
DllCall("SetProcessDPIAware")

; 定义热键，使用 Ctrl+Shift+F1 激活窗口调整
^+`::
    ;--------------------------------------------------------------------------------------
    ; 第一步：获取当前活动窗口的句柄
    ;--------------------------------------------------------------------------------------
    WinGet, activeHwnd, ID, A
    if (activeHwnd = "")
    {
        MsgBox, 错误：无法获取活动窗口的句柄！
        Return
    }

    ;--------------------------------------------------------------------------------------
    ; 第二步：显示当前活动窗口信息供用户确认操作
    ;--------------------------------------------------------------------------------------
    ; 获取当前活动窗口的标题信息
    WinGetTitle, activeWindowTitle, ahk_id %activeHwnd%
    ; 使用 MsgBox 显示当前活动窗口的标题和句柄，让用户确认是否继续
    MsgBox, 4, 窗口信息, 当前活动窗口为:`n`n标题: %activeWindowTitle%`n句柄: %activeHwnd%`n`n是否继续操作？
    IfMsgBox, No
        Return
    ; 确认后，将目标窗口直接设置为当前活动窗口
    targetWindow := activeHwnd

    ;--------------------------------------------------------------------------------------
    ; 第三步：分步提示用户输入目标客户区的宽度和高度（不含边框和标题栏）
    ;--------------------------------------------------------------------------------------
    InputBox, desiredWidth, 窗口宽度, 请输入目标窗口客户区宽度`n不含边框和标题栏:, , 220, 150
    if ErrorLevel
        Return
    desiredWidth := desiredWidth + 0  ; 将输入转换成数字

    InputBox, desiredHeight, 窗口高度, 请输入目标窗口客户区高度`n不含边框和标题栏:, , 220, 150
    if ErrorLevel
        Return
    desiredHeight := desiredHeight + 0  ; 将输入转换成数字

    ;--------------------------------------------------------------------------------------
    ; 第四步：获取目标窗口整体尺寸与客户区实际尺寸，计算非客户区（边框与标题栏）尺寸差值
    ;--------------------------------------------------------------------------------------
    WinGetPos, winX, winY, winWidth, winHeight, ahk_id %targetWindow%
    
    ; 分配内存用于存储客户区 RECT 结构（各4字节，顺序为 left, top, right, bottom，总计 16 字节）
    VarSetCapacity(clientRect, 16, 0)
    if (!DllCall("GetClientRect", "Ptr", targetWindow, "Ptr", &clientRect))
    {
        MsgBox, 错误：无法获取窗口客户区尺寸！
        Return
    }
    ; GetClientRect 返回的 left 与 top 为 0，其 right、bottom 分别为客户区宽、高
    clientW := NumGet(clientRect, 8, "Int")
    clientH := NumGet(clientRect, 12, "Int")
    
    ; 计算窗口非客户区尺寸：整体窗口尺寸减去客户区尺寸
    nonClientWidth := winWidth - clientW
    nonClientHeight := winHeight - clientH

    ;--------------------------------------------------------------------------------------
    ; 第五步：根据用户输入的目标客户区尺寸，加上以上计算得到的补偿值，
    ;         计算出目标整体窗口尺寸，从而保证客户区尺寸准确
    ;--------------------------------------------------------------------------------------
    newFullWidth := desiredWidth + nonClientWidth
    newFullHeight := desiredHeight + nonClientHeight

    ; 调整目标窗口的位置和整体尺寸（窗口位置保持不变，只调整宽高）
    WinMove, ahk_id %targetWindow%, , winX, winY, newFullWidth, newFullHeight
    if ErrorLevel
    {
        MsgBox, 错误：无法调整窗口尺寸！
        Return
    }

    ;--------------------------------------------------------------------------------------
    ; 第六步：延时后重新获取调整后窗口的客户区尺寸，以验证调整是否成功
    ;--------------------------------------------------------------------------------------
    Sleep, 100  ; 延时 100 毫秒，等待窗口更新

    VarSetCapacity(newClientRect, 16, 0)
    if (!DllCall("GetClientRect", "Ptr", targetWindow, "Ptr", &newClientRect))
    {
        MsgBox, 错误：无法获取调整后窗口客户区尺寸！
        Return
    }
    newClientW := NumGet(newClientRect, 8, "Int")
    newClientH := NumGet(newClientRect, 12, "Int")

    ; 获取目标窗口标题信息，用于后续提示显示
    WinGetTitle, targetTitle, ahk_id %targetWindow%

    ;--------------------------------------------------------------------------------------
    ; 第七步：验证调整后客户区尺寸是否与用户预期一致，并在消息框中显示详细窗口信息
    ;--------------------------------------------------------------------------------------
    if (newClientW != desiredWidth or newClientH != desiredHeight)
    {
        MsgBox, 调整后窗口显示区尺寸与预期不符`n`n窗口: %targetTitle%`n句柄: %targetWindow%`n`n预期: %desiredWidth%x%desiredHeight%`n实际: %newClientW%x%newClientH%
    }
    else
    {
        MsgBox, 窗口显示区尺寸已调整为： %desiredWidth%x%desiredHeight%`n`n窗口: %targetTitle%`n句柄: %targetWindow%
    }

    ;------------------------------------------------------------
    ; 第八步：询问用户是否立即结束脚本。
    ;         如果选择“是”，则退出整个程序；如果选择“否”，则继续继续运行。
    ;------------------------------------------------------------
    MsgBox, 260, 操作确认, 是否结束程序？`n`n选择“是”则脚本结束`n选择“否”则继续运行。
    IfMsgBox, Yes
    {
        ExitApp
    }
    else
    {
        ; 此处可继续扩展其他功能，此示例仅提示并结束当前热键流程
        Return
    }
Return

; 托盘菜单内容
ShowHelp:
{
    ; 弹出消息框，显示脚本的使用说明、操作流程等信息
    MsgBox, 64, 程序使用说明, 本程序用于精确调整目标窗口显示区的尺寸。`n`n按 Ctrl+Shift+`` (Esc下面的键) 热键启动窗口调整操作。`n流程如下：`n　1. 自动获取当前活动窗口，并显示窗口信息供用户确认;`n　2. 依次输入目标显示区的宽度和高度;`n　3. 动态计算窗口边框与标题栏占用的像素，并调整整体窗口尺寸;`n　4. 调整后会显示验证信息;`n　5. 根据提示选择是否结束程序。`n`n你可通过托盘菜单中的“退出程序”来结束本程序。
    Return
}

ExitApplication:
    {
        ExitApp
    }
