#Requires Autohotkey v2.0
#Warn All, MsgBox
#SingleInstance Force
SendMode("Input")
SetWorkingDir(A_ScriptDir)

^!+q::ExitApp

myGui := Gui()
myGui.OnEvent("Close", GuiClose)
myGui.Title := "自动翻页威力加强版V2"
myGui.Opt("-MaximizeBox MinimizeBox +AlwaysOnTop +DPIScale")
myGui.Show("w320 h150")
myGui.Add("Text", "x20 y10", "按键:")
ogcDropDownListKey := myGui.Add("DropDownList", "x20 y30 w85 vKey", ["Up", "Down", "Right", "Left", "Space", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "Shift", "Ctrl", "Alt", "Insert", "Delete", "Backspace", "Tab", "Enter", "Esc", "Space", "PgUp", "PgDn", "End", "Home", "PrintScreen", "Pause", "NumLock", "CapsLock", "ScrollLock", "~", "-", "=", ",", ".", "/", "\", "NumPad0", "NumPad1", "NumPad2", "NumPad3", "NumPad4", "NumPad5", "NumPad6", "NumPad7", "NumPad8", "NumPad9", "NumPadDot", "NumPadEnter", "NumPadDiv", "NumPadMult", "NumPadAdd", "NumPadSub", "LButton", "RButton", "MButton"])
myGui.Add("Text", "x120 y10", "按住秒数(0.1):")
ogcEditDuration := myGui.Add("Edit", "x120 y30 w80 vDuration")
ogcUpDownDuration := myGui.Add("UpDown", "vUpDownDuration Range1-200", 30)
myGui.Add("Text", "x220 y10", "间隔秒数(0.1):")
ogcEditSleep := myGui.Add("Edit", "x220 y30 w80 vSleep")
ogcUpDownSleep := myGui.Add("UpDown", "vUpDownSleep Range1-200", 4)
ogcButtonStart := myGui.Add("Button", "x20 y65 w80", "开始")
ogcButtonStart.OnEvent("Click", Start.Bind("Normal"))
ogcButtonStop := myGui.Add("Button", "x120 y65 w80", "停止")
ogcButtonStop.OnEvent("Click", Stop.Bind("Normal"))
ogcButtonAbout := myGui.Add("Button", "x220 y65 w80", "关于&&说明")
ogcButtonAbout.OnEvent("Click", About.Bind("Normal"))

myGui.Add("Text", "x20 y102", "目前还没能力实现按下快捷键停止脚本，先就这么用")
guiStatusBar := MyGui.Add("StatusBar",, "　当前状态：　脚本初始化完成")

return

Start(A_GuiEvent, GuiCtrlObj, Info, *)
{
    global Key, Duration, Sleeptime
    oSaved := myGui.Submit("0")
    Key := oSaved.Key
    Duration := oSaved.Duration
    Sleeptime := oSaved.Sleep
    guiStatusBar.SetText("　当前状态：　脚本运行中")
    SetTimer(Repeat)
    return
}

Stop(A_GuiEvent, GuiCtrlObj, Info, *)
{
    SetTimer(Repeat,0)
    guiStatusBar.SetText("　当前状态：　脚本已停止")
    return
}

Repeat()
{
    Send("{" Key " Down}")
    Sleep(Duration * 100)
    Send("{" Key " Up}")
    Sleep(Sleeptime * 100)
    return
}

About(A_GuiEvent, GuiCtrlObj, Info, *)
{
    SecondGui := Gui()
    SecondGui.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop +DPIScale")
    SecondGui.SetFont("s8")
    SecondGui.Show("w320 h320")
    SecondGui.Title := "关于&说明"
    SecondGui.Add("Text", , "正文：`n用来自动化的工具，作用是每隔一段时间按下按键。`n按下的时间和间隔时间都可以自定义。`n也就是说可以拿去做到“间隔N秒按住X键N秒”这样的事情。`n`n主要用途是拿来看韩漫的，但是拿来干其他事情也不是不行。`n`n`n`n时间设置上：`n`n【填的数字】* 0.1 = 实际时间`n`n也就是如果填了30，那么实际3秒。`n`n如果填了4，那么实际0.4秒`n`n`n重要事项：`n`n虽然目前可以实现连点鼠标，但没法用快捷键退出`n，所以最好不要用，后果自负")
    SecondGui.Add("Text", , "By Luminous 20240727`nPowered By Autohotkey 2.0.18")
    btnOK := SecondGui.Add("Button", "x230 y270 w70 h30", "&OK")
    btnOK.OnEvent("Click", OK.Bind("Normal"))
    return

    OK(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        SecondGui.Destroy
    }
}

GuiClose(*)
{
    ExitApp()
}
