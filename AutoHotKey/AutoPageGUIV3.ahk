#Requires AutoHotkey v2.0+ 64-bit    ; 指定需要使用 AutoHotkey v2.0 或更高版本
#Warn All, MsgBox                    ; 开启所有警告，以消息框形式显示，便于调试和发现潜在问题
#SingleInstance Force                ; 保证脚本只运行一个实例，防止重复运行导致冲突
SendMode("Input")                    ; 将键盘发送模式设置为 Input，保证输入操作快速又准确
SetWorkingDir(A_ScriptDir)           ; 将脚本工作目录设置为当前脚本所在目录，方便引用文件

;--------------------------------------------------------------------------------------------
;                   全局快捷键：通过快捷键启动/停止脚本以及退出整个程序
;--------------------------------------------------------------------------------------------


^+s::                       ; Ctrl+Shift+S：启动自动按键脚本（仅当脚本尚未处于运行状态时有效）
{
    if (!scriptRunning)
        Start("Hotkey", "", "", "")        ; 调用 Start() 函数启动自动按键
    else
        ; MsgBox("脚本运行中")               ; 如果脚本已经在运行，则弹出提示
        MsgBox("脚本运行中", "提示", MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST)
}

^+x::                       ; Ctrl+Shift+X：停止自动按键脚本（仅当脚本正处于运行状态时有效）
{
    if (scriptRunning)
        Stop("Hotkey", "", "", "")         ; 调用 Stop() 函数停止自动按键
    else
        ; MsgBox("脚本未运行")               ; 如果脚本没运行，则弹出提示
        MsgBox("脚本未运行", "提示", MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST)
}

^!+q::                                     ; Ctrl+Shift+Alt+Q：立即退出整个脚本
{
    ExitApp
}


;--------------------------------------------------------------------------------------------
;                   全局变量：用于保存脚本运行状态以及用户配置参数
;--------------------------------------------------------------------------------------------
; 脚本运行状态标志，false表示未运行；true时表示正在执行自动按键操作
global scriptRunning := false
; 声明所有在脚本中使用的全局参数
global Key, Duration, Sleeptime, Iterations, LogEnable, LogFile
; 提示框变量
global MB_ICONINFORMATION := 0x40    ; 0x40 即 64，信息图标
global MB_SYSTEMMODAL     := 0x1000   ; 0x1000 即 4096，使消息框系统模态
global MB_TOPMOST         := 0x40000  ; 0x40000 即 262144，置顶显示

;--------------------------------------------------------------------------------------------
;                       主 GUI 窗口及控件：添加更多自定义选项
;--------------------------------------------------------------------------------------------
; 创建一个新的 GUI 窗口对象
myGui := Gui()
; 当用户手动关闭 GUI 窗口时，调用 GuiClose() 函数退出脚本
myGui.OnEvent("Close", GuiClose)
; 设置主窗口标题
myGui.Title := "自动翻页威力加强版V3"
; Opt 选项说明：
;  - 禁用最大化和最小化按钮
;  + 设置窗口始终为最前端
;  + 自动根据 DPI 进行缩放
myGui.Opt("-MaximizeBox -MinimizeBox +DPIScale")  

; 调整窗口尺寸以适应所有控件，新窗口宽360像素、高260像素
myGui.Show("w440 h200")

;--------------- 行1：文本提示（按键、按住时间和间隔时间标签） ---------------
; 在 (20,10) 位置显示 “按键:” 标签
myGui.Add("Text", "x20 y10", "要重复的按键:")
; 在 (120,10) 位置显示 “按住秒数(0.01):” 标签（输入值乘以0.01为实际持续时间）
myGui.Add("Text", "x160 y10", "按住秒数 (N * 0.01):")
; 在 (220,10) 位置显示 “间隔秒数(0.01):” 标签（输入值乘以0.01为实际间隔时间）
myGui.Add("Text", "x300 y10", "间隔秒数 (N * 0.01):")

;--------------- 行2：基本输入控件（按键选择、持续时间和间隔时间设置） ---------------
; 下拉列表控件：用户选择需要自动按下的按键，绑定到变量 vKey
ogcDropDownListKey := myGui.Add("DropDownList", "x20 y30 w120 vKey", 
    ["Up", "Down", "Right", "Left", "Space", "F1", "F2", "F3", "F4", "F5", "F6", 
     "F7", "F8", "F9", "F10", "F11", "F12", "a", "b", "c", "d", "e", "f", "g", "h", 
     "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", 
     "x", "y", "z", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "Shift", 
     "Ctrl", "Alt", "Insert", "Delete", "Backspace", "Tab", "Enter", "Esc", "Space", 
     "PgUp", "PgDn", "End", "Home", "PrintScreen", "Pause", "NumLock", "CapsLock", 
     "ScrollLock", "~", "-", "=", ",", ".", "/", "\\", "NumPad0", "NumPad1", "NumPad2", 
     "NumPad3", "NumPad4", "NumPad5", "NumPad6", "NumPad7", "NumPad8", "NumPad9", "NumPadDot", 
     "NumPadEnter", "NumPadDiv", "NumPadMult", "NumPadAdd", "NumPadSub", "LButton", "RButton", "MButton"])

; 编辑框及 UpDown 控件：设置按下持续时间，默认值为30，对应3秒（300*0.01秒）
ogcEditDuration := myGui.Add("Edit", "x160 y30 w120 vDuration")
ogcUpDownDuration := myGui.Add("UpDown", "vUpDownDuration Range1-2000", 300)

; 编辑框及 UpDown 控件：设置按键间隔时间，默认值为4，对应0.4秒（40*0.01秒）
ogcEditSleep := myGui.Add("Edit", "x300 y30 w120 vSleep")
ogcUpDownSleep := myGui.Add("UpDown", "vUpDownSleep Range1-2000", 40)

;--------------- 行3：重复次数与日志记录选项 ---------------
; 重复次数：指定按键循环的次数，0或留空表示无限循环
myGui.Add("Text", "x20 y60", "重复次数 (见说明):")
ogcEditIter := myGui.Add("Edit", "x20 y85 w120 vIter")
ogcUpDownIter := myGui.Add("UpDown", "vUpDownIter Range0-10000", 0)

; 日志记录选项：复选框用于开启/关闭日志记录功能
ogcCheckboxLog := myGui.Add("Checkbox", "x190 y90 vLogEnable", "日志记录")
; 旁边添加文本和编辑框，用于指定日志文件路径（默认文件名 自动翻页日志.txt）
myGui.Add("Text", "x300 y60", "日志文件完整路径:")
ogcEditLogFile := myGui.Add("Edit", "x300 y85 w120 vLogFile")
; 默认日志文件设置为 “自动翻页日志.txt”，储存在脚本所在目录
ogcEditLogFile.Value := "自动翻页日志.txt"

;--------------- 行4：控制按钮（开始、停止、关于和说明） ---------------
ogcButtonStart := myGui.Add("Button", "x20 y130 w120 h30", "开始 (Ctrl+Shift+S)")
; “开始”按钮绑定 Start() 函数启动自动按键
ogcButtonStart.OnEvent("Click", Start.Bind("Normal"))
ogcButtonStop := myGui.Add("Button", "x160 y130 w120 h30", "停止 (Ctrl+Shift+X)")
; “停止”按钮绑定 Stop() 函数停止自动按键
ogcButtonStop.OnEvent("Click", Stop.Bind("Normal"))
ogcButtonAbout := myGui.Add("Button", "x300 y130 w120 h30", "关于&&使用说明")
; “关于&说明”按钮（使用 && 显示单个 & 符号）绑定 About() 函数，显示相关信息和使用说明
ogcButtonAbout.OnEvent("Click", About.Bind("Normal"))

;--------------- 状态栏：显示当前脚本运行状态信息 ---------------
; 状态栏用于反馈脚本运行情况，例如“脚本运行中”或“脚本已停止”
guiStatusBar := myGui.Add("StatusBar", "", "　当前状态：　脚本初始化完成")

; 主线程结束，等待用户操作
return

;--------------------------------------------------------------------------------------------
;                               函数：Start - 启动自动按键操作
;--------------------------------------------------------------------------------------------
Start(A_GuiEvent, GuiCtrlObj, Info, *)
{
    global scriptRunning, Key, Duration, Sleeptime, Iterations, LogEnable, LogFile, firstCycle

    ; 如果脚本已经处于运行状态，则提示用户并不重复启动
    if (scriptRunning)
    {
        MsgBox("脚本已经在运行中。")
        return
    }

    ; 提交所有控件的当前值，返回的对象 oSaved 包含各控件中输入的值
    oSaved := myGui.Submit("0")
    Key        := oSaved.Key           ; 获取用户选定的按键
    Duration   := oSaved.Duration      ; 获取按下持续时间（单位为0.01秒）
    Sleeptime  := oSaved.Sleep         ; 获取按键之间的间隔时间（单位为0.01秒）
    Iterations := oSaved.Iter          ; 获取重复次数设置（0或留空则表示无限循环）
    LogEnable  := oSaved.LogEnable     ; 获取日志记录复选框的状态（1表示开启）
    LogFile    := oSaved.LogFile       ; 获取日志文件路径

    ; 如果重复次数为空或不是数字，则默认为 0（无限循环）
    if (Iterations = "" or !IsNumber(Iterations))
        Iterations := 0
    Iterations := Floor(Iterations)   ; 取整数部分

    ; 输入检查：按下持续时间需至少为 1（0.01秒为最小单位）
    if (!IsNumber(Duration) or Duration < 1)
    {
        MsgBox("请在'按住秒数'中输入至少1（0.01秒为最小单位）的数字。")
        return
    }
    ; 输入检查：间隔时间必须大于或等于0
    if (!IsNumber(Sleeptime) or Sleeptime < 0)
    {
        MsgBox("请在'间隔秒数'中输入大于等于0的数字。")
        return
    }
    ; 如果启用了日志记录但未指定日志文件，则使用默认文件名 "自动翻页日志.txt"
    if (LogEnable and (LogFile = ""))
        LogFile := "自动翻页日志.txt"

    scriptRunning := true   ; 标记脚本已启动运行
    firstCycle := true   ; 初始化标记，第一次跳过按键发送

    ; 输出调试信息到调试器（使用 OutputDebug，可用于跟踪变量状态）
    OutputDebug("启动脚本：按键=" . Key . " 持续时间=" . Duration . " 间隔=" . Sleeptime . " 重复次数=" . Iterations . " 日志记录=" . LogEnable . " 日志文件=" . LogFile)

    guiStatusBar.SetText("　当前状态：　脚本运行中")  ; 更新状态栏显示
    ; 启动定时器，自动调用 Repeat() 函数进行循环操作
    SetTimer(Repeat)
    return
}

;--------------------------------------------------------------------------------------------
;                               函数：Stop - 停止自动按键操作
;--------------------------------------------------------------------------------------------
Stop(A_GuiEvent, GuiCtrlObj, Info, *)
{
    global scriptRunning
    ; 如脚本未运行，则直接返回
    if (!scriptRunning)
        return

    ; 停止定时器，结束对 Repeat() 函数的周期性调用
    SetTimer(Repeat, 0)
    ; 重置运行标志
    scriptRunning := false
    ; 更新状态栏显示
    guiStatusBar.SetText("　当前状态：　脚本已停止")
    ; 输出调试信息
    OutputDebug("停止脚本。")
    return
}

;--------------------------------------------------------------------------------------------
;       函数：Repeat - 执行一次按键循环，包括模拟按下与释放操作，并处理日志及重复次数
;--------------------------------------------------------------------------------------------
Repeat()
{
    global Key, Duration, Sleeptime, Iterations, scriptRunning, LogEnable, LogFile, firstCycle

    if (!scriptRunning)
        return

    ; 如果是第一次循环，就只等待间隔时间，不发送任何按键
    if (firstCycle) {
        firstCycle := false
        totalInterval := Sleeptime * 10  ; 整体等待时间（单位：毫秒）
        elapsed := 0
        while (elapsed < totalInterval) {
            if (!scriptRunning)
                return
            Sleep(10)
            elapsed += 10
        }
        return
    }

    OutputDebug("执行一次按键循环：按键 " . Key)  ; 调试输出当前按键动作

    ; 模拟按下操作（格式：{按键 Down}）
    Send("{" Key " Down}")
    ; 用循环实现可中断的等待（“按住”时间，0.01秒为基本单位）
    totalHold := Duration * 10  ; 总休眠毫秒数
    elapsed := 0
    while (elapsed < totalHold) {
        if (!scriptRunning)  ; 如果已被停止，则立即退出函数
            return
        Sleep(10)
        elapsed += 10
    }
    ; 模拟释放按键操作（格式：{按键 Up}）
    Send("{" Key " Up}")

    ; 当启用日志记录时，将每次按键事件写入指定日志文件中
    if (LogEnable)
    {
        ; 明确传入当前时间 A_Now，然后提供格式字符串作为第二个参数
        dateStr := FormatTime(A_Now, "yyyy'年'M'月'd'日'")  ; 得到例如 "2025年5月29日"
        timeStr := FormatTime(A_Now, "HH:mm:ss")             ; 得到例如 "23:59:59"
        ; 格式化日志条目，注意 Format() 参数索引从 1 开始
        logEntry := Format("{1} {2} 按键: {3}`n", dateStr, timeStr, Key)
        FileAppend(logEntry, LogFile)
    }

    ; 间隔等待部分，同样用循环检测中断
    totalInterval := Sleeptime * 10
    elapsed := 0
    while (elapsed < totalInterval) {
        if (!scriptRunning)
            return
        Sleep(10)
        elapsed += 10
    }

    ; 处理重复次数，如果设置了正数则逐次递减
    if (Iterations > 0)
    {
        Iterations--
        if (Iterations <= 0)
        {
            Stop("", "", "", "")  ; 当次数用完后，自动调用 Stop()，终止脚本运行
            return
        }
    }

    return
}

;--------------------------------------------------------------------------------------------
;           函数：About - 显示关于与使用说明的窗口，告知用户详细功能介绍及注意事项
;--------------------------------------------------------------------------------------------
About(A_GuiEvent, GuiCtrlObj, Info, *)
{
    ; 声明变量 aboutText ,用于储存大量多行文本;使用 local 声明局部变量
    local aboutText := "
    (
关于本程序：

　　这是一个自动翻页工具，每隔一段时间自动按下指定的按键，
　　可自定义按住时间、间隔时间、重复次数，并支持日志记录。

详细说明：

    1. 自定义按键（支持方向键、功能键、字母、数字等）；
    2. 按下持续时间和按键间隔单位均为 0.01 秒；
    3. 重复次数部分，0 或留空表示无限循环；
    4. 全局快捷键：
        启动：Ctrl+Shift+S
        停止：Ctrl+Shift+X
        退出：Ctrl+Shift+Alt+Q

注意事项：

    · 请选择合适的按键及键值，确保与系统一致；
    · 日志记录需指定有效路径；
    · 当设定重复次数时，达到设定次数脚本会自动停止。
    · 按下持续时间和按键间隔如果输入个位数的值会导
        致无法使用快捷键停止，此时可以结束脚本进程、
        关闭脚本、快速双击程序界面的“停止”按钮的方
        法中止脚本工作。


                                By Luminous 2024.07.27
                                Powered By  Autohotkey
    )"

    ; 创建一个新的 GUI 窗口，用于显示关于和说明信息
    SecondGui := Gui()
    ; 设置选项：禁用最大化/最小化按钮，窗口始终置顶，并支持 DPI 缩放
    SecondGui.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop +DPIScale")  
    ; 设置字体大小为 8，适合显示较多文字
    SecondGui.SetFont("s8")
    ; 设置关于窗口标题（显示为“关于&说明”，&&确保显示单个&）
    SecondGui.Title := "关于&说明"
    ; 显示关于窗口，尺寸宽360，高320像素
    SecondGui.Show("w360 h320")

    ; 添加详细说明文本，介绍该程序的功能、配置参数、全局快捷键以及使用注意事项
    SecondGui.Add("Text", , aboutText)
    ; 添加一个 “OK” 按钮，点击后关闭关于窗口
    btnOK := SecondGui.Add("Button", "x270 y280 w70 h30", "&OK")
    btnOK.OnEvent("Click", OK.Bind("Normal"))
    return

    ; 内部函数 OK：用于关闭关于说明窗口
    OK(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        ; 销毁关于窗口，释放资源
        SecondGui.Destroy
    }
}

;--------------------------------------------------------------------------------------------
;                       函数：GuiClose - 处理主 GUI 窗口关闭事件
;--------------------------------------------------------------------------------------------
GuiClose(*)
{
    ; 直接退出整个脚本，确保所有操作停止
    ExitApp()
}
