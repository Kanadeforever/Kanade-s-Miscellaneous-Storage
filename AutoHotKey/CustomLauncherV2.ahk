#Requires AutoHotkey v2.0
Persistent
#SingleInstance Force

IniFile := StrReplace(A_ScriptDir "\" A_ScriptName, ".exe", "") ".ini"

; 检查是否存在ini文件
if !FileExist(IniFile)
{
    MsgBox("请选择一个exe文件", "提示", 64)
    exePath := FileSelect(3, "", "选择exe文件", "*.exe")
    If (exePath != "")
    {
        ; 检查exe文件是否在脚本目录中
        If (SubStr(exePath, 1, InStr(exePath, "\", 0, -1) - 1) != A_ScriptDir)
            IniWrite(exePath, IniFile, "Settings", "target_name")
        Else
            IniWrite(StrSplit(exePath, "\").Pop(), IniFile, "Settings", "target_name")
        ; 写入OtherParams1
        IniWrite("", IniFile, "Settings", "other_params1")
        IniWrite("", IniFile, "Settings", "other_params2")
        MsgBox("设置文件已创建，请重新运行启动器", "提示", 64)
    }
    ExitApp()
}

; 读取ini文件中的exe路径和其他参数
exePath := IniRead(IniFile, "Settings", "target_name")
otherParams1 := IniRead(IniFile, "Settings", "other_params1")
otherParams2 := IniRead(IniFile, "Settings", "other_params2")

; 如果读取的值为空，则设置为空字符串
if (exePath = "ERROR")
    exePath := ""
if (otherParams1 = "ERROR")
    otherParams1 := ""
if (otherParams2 = "ERROR")
    otherParams2 := ""

; 创建主GUI
myGui := Gui()
myGui.Show("w520 h320")
myGui.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop +DPIScale")
myGui.SetFont("s12 Bold")
myGui.Add("Text", "x20 y20 w289 h42", "自定义启动器")
myGui.SetFont("s10")
myGui.Add("Text", "x20 y65", "当前启动参数：")
; 将启动参数显示在此控件中
commandText := myGui.Add("Edit", "x20 y90 w480 ReadOnly Multi -VScroll -HSCroll", exePath " " otherParams1 " " otherParams2)

ogcButtonLaunch := myGui.Add("Button", "x300 y165 w200 h50", "&Launch")
ogcButtonLaunch.OnEvent("Click", RunExe)
ogcButtonConfig := myGui.Add("Button", "x300 y235 w200 h50", "&Config")
ogcButtonConfig.OnEvent("Click", ReSelectExe)
ogcButtonAbout := myGui.Add("Button", "x24 y265 w60 h35", "&About")
ogcButtonAbout.OnEvent("Click", About.Bind("Normal"))

myGui.Title := "自定义启动器"

myGui.OnEvent('Close', (*) => ExitApp())

; 运行按钮功能
RunExe(*)
{
    global exePath, otherParams1, otherParams2
    If (exePath != "")
    {
        RunCommand := exePath
        if (otherParams1 != "")
            RunCommand .= " " otherParams1
        if (otherParams2 != "")
            RunCommand .= " " otherParams2
        Run(RunCommand)
    }
    Else
    {
        MsgBox("未找到exe文件路径", "错误", 48)
    }
    GuiClose()
}

; 设置按钮功能
ReSelectExe(*)
{
    global exePath, otherParams1, otherParams2, IniFile, commandText
    oGui2 := Gui()
    oGui2.Show("w620 h220")
    oGui2.Title := "设置"
    oGui2.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop +DPIScale")
    oGui2.Add("Text", "x40 y30 w98 h20", "EXE文件路径：")
    ogcEditNewExePath := oGui2.Add("Edit", "x140 y30 w320 h20 vNewExePath", exePath)
    ogcButtonSelect := oGui2.Add("Button", "x498 y30 w80 h23", "选择")
    ogcButtonSelect.OnEvent("Click", SelectExe.Bind(ogcEditNewExePath))
    oGui2.Add("Text", "x40 y60 w98 h20", "命令行参数1：")
    ogcEditOtherParams1 := oGui2.Add("Edit", "x140 y60 w320 h20 vOtherParams1", otherParams1)
    oGui2.Add("Text", "x40 y90 w98 h20", "命令行参数2：")
    ogcEditOtherParams2 := oGui2.Add("Edit", "x140 y90 w320 h20 vOtherParams2", otherParams2)
    ogcButtonSave := oGui2.Add("Button", "x115 y160 w80 h40", "保存")
    ogcButtonSave.OnEvent("Click", SaveSettings.Bind(oGui2, ogcEditNewExePath, ogcEditOtherParams1, ogcEditOtherParams2, commandText))
    ogcButtonClose := oGui2.Add("Button", "x425 y160 w80 h40", "关闭")
    ogcButtonClose.OnEvent("Click", CloseSettings.Bind(oGui2))
    oGui2.OnEvent("Close", CloseSettings)
}

; 选择exe文件按钮功能
SelectExe(ogcEditNewExePath, *)
{
    newExePath := FileSelect(3, "", "选择exe文件", "*.exe")
    If (newExePath != "")
    {
        ogcEditNewExePath.Value := newExePath
    }
}

; 保存按钮功能
SaveSettings(oGui2, ogcEditNewExePath, ogcEditOtherParams1, ogcEditOtherParams2, commandText, *)
{
    global IniFile, exePath, otherParams1, otherParams2
    NewExePath := ogcEditNewExePath.Value
    OtherParams1 := ogcEditOtherParams1.Value
    OtherParams2 := ogcEditOtherParams2.Value
    IniWrite(NewExePath, IniFile, "Settings", "target_name")
    IniWrite(OtherParams1, IniFile, "Settings", "other_params1")
    IniWrite(OtherParams2, IniFile, "Settings", "other_params2")
    exePath := NewExePath
    otherParams1 := OtherParams1
    otherParams2 := OtherParams2
    commandText.Value := exePath " " otherParams1 " " otherParams2
    oGui2.Destroy()
}

; 关于
About(A_GuiEvent, GuiCtrlObj, Info, *)
{
    oGui3 := Gui()
    oGui3.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop +DPIScale")
    oGui3.SetFont("s8")
    oGui3.Show("w300 h320")
    oGui3.Title := "关于&说明"
    oGui3.Add("Text", , "正文：`n`n按名字理解，自定义启动器`n`n你可以指定exe和命令行也可以拿来干其他的`n随你怎样`n`n一般来说是拿来代理steam上有些游戏的mod或者汉化的exe`n虽然有时候直接改名字也可以但如果你想保留原版exe`n那么它就解决这个问题了`n只需要按你想要的时间点改一下exe的设置就行了`n就是这样")
    oGui3.Add("Text", , "By Luminous 20240729`nPowered By Autohotkey 2.0.18 && Microsoft Copilot")
    oGui3OK := oGui3.Add("Button", "x210 y280 w60 h20", "&OK")
    oGui3OK.OnEvent("Click", OK.Bind("Normal"))
    return

    OK(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        oGui3.Destroy
    }
}

; 关闭按钮功能
CloseSettings(oGui2, *)
{
    oGui2.Destroy()
}

GuiClose(*)
{
    ExitApp()
}
