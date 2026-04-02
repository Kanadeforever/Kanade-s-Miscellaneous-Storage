#Requires AutoHotkey v2.0+
#SingleInstance Force
#NoTrayIcon

SetWorkingDir(A_ScriptDir)

; ==========================================
; 1. 全局配置初始化
; ==========================================
global IniFile := A_ScriptDir "\LauncherConfig.ini"

; 如果配置文件不存在，则写入默认值
if !FileExist(IniFile) {
    IniWrite("ArnosurgeDX", IniFile, "App1", "Name")
    IniWrite("ArnosurgeDX.exe", IniFile, "App1", "Path")
    IniWrite("LR", IniFile, "App1", "EmuMode")
    IniWrite("99e3cc35-3478-4f8a-ac52-42ffd838e37e", IniFile, "App1", "LRGuid")

    IniWrite("ArnosurgeDX_Env", IniFile, "App2", "Name")
    IniWrite("ArnosurgeDX_Env_Env.exe", IniFile, "App2", "Path")
    IniWrite("LE", IniFile, "App2", "EmuMode")
    IniWrite("", IniFile, "App2", "LRGuid")
}

; 读取初始按钮名称
global App1Name := IniRead(IniFile, "App1", "Name", "App 1")
global App2Name := IniRead(IniFile, "App2", "Name", "App 2")

; ==========================================
; 2. 构建主界面 (Main GUI)
; ==========================================
MainGui := Gui("-MaximizeBox -MinimizeBox +DPIScale", "Universal Game Launcher")
MainGui.OnEvent("Close", (*) => ExitApp())
MainGui.OnEvent("Escape", (*) => ExitApp())
MainGui.BackColor := "FFFFFF"

MainGui.SetFont("s11 Bold", "Microsoft YaHei UI")
MainGui.Add("Text", "x0 y20 w280 Center c333333", "Universal Game Launcher")

MainGui.SetFont("s9 Norm")
global btn1 := MainGui.Add("Button", "x40 y60 w200 h38", App1Name)
btn1.OnEvent("Click", (*) => ExecuteApp("App1"))

global btn2 := MainGui.Add("Button", "x40 y110 w200 h38", App2Name)
btn2.OnEvent("Click", (*) => ExecuteApp("App2"))

MainGui.SetFont("s9")
MainGui.Add("Button", "x40 y165 w95 h30", "⚙️ 设 置").OnEvent("Click", ShowConfig)
MainGui.Add("Button", "x145 y165 w95 h30", "ℹ️ 关 于").OnEvent("Click", ShowAbout)

MainGui.Show("w280 h220")

; ==========================================
; 3. 核心启动逻辑
; ==========================================
ExecuteApp(Section) {
    MainGui.Hide()
    
    appPath := IniRead(IniFile, Section, "Path", "")
    emuMode := IniRead(IniFile, Section, "EmuMode", "None")
    lrGuid  := IniRead(IniFile, Section, "LRGuid", "")

    try {
        if (emuMode = "LR") {
            RunWait('".\LocaleRemulator\LRProc.exe" ' lrGuid ' "' appPath '"')
        } else if (emuMode = "LE") {
            RunWait('".\LocaleEmulator\LEProc.exe" -run "' appPath '"')
        } else {
            if (appPath != "") {
                RunWait('"' appPath '"')
            } else {
                MsgBox("目标程序路径为空，请在设置中配置。", "提示", 48)
            }
        }
    } catch as err {
        MsgBox("启动失败，请检查路径或环境配置！`n`n错误信息：" err.Message, "执行错误", 16)
    }
    
    MainGui.Restore()
}

; ==========================================
; 4. 设置界面 (Config GUI)
; ==========================================
ShowConfig(*) {
    MainGui.Opt("+Disabled") ; 禁用主界面，实现模态窗口效果
    ConfGui := Gui("-MaximizeBox -MinimizeBox +DPIScale +Owner" MainGui.Hwnd, "全局参数配置器")
    ConfGui.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))
    ConfGui.SetFont("s9", "Microsoft YaHei UI")

    ; ---- 启动项 1 配置 ----
    ConfGui.Add("GroupBox", "x10 y10 w350 h140", "启动项 1 (App 1)")
    
    ConfGui.Add("Text", "x25 y35 w60", "按钮名称:")
    edName1 := ConfGui.Add("Edit", "x85 y32 w250", IniRead(IniFile, "App1", "Name", ""))

    ConfGui.Add("Text", "x25 y65 w60", "目标程序:")
    edPath1 := ConfGui.Add("Edit", "x85 y62 w200", IniRead(IniFile, "App1", "Path", ""))
    ConfGui.Add("Button", "x290 y61 w50 h24", "浏览").OnEvent("Click", (*) => SelectFile(edPath1))

    ConfGui.Add("Text", "x25 y95 w60", "运行环境:")
    emu1 := IniRead(IniFile, "App1", "EmuMode", "None")
    drop1 := ConfGui.Add("DropDownList", "x85 y92 w65 Choose" (emu1="LR"?3:emu1="LE"?2:1), ["None", "LE", "LR"])

    ConfGui.Add("Text", "x160 y95 w35", "GUID:")
    edGuid1 := ConfGui.Add("Edit", "x200 y92 w135", IniRead(IniFile, "App1", "LRGuid", ""))

    ; ---- 启动项 2 配置 ----
    ConfGui.Add("GroupBox", "x10 y160 w350 h140", "启动项 2 (App 2)")
    
    ConfGui.Add("Text", "x25 y185 w60", "按钮名称:")
    edName2 := ConfGui.Add("Edit", "x85 y182 w250", IniRead(IniFile, "App2", "Name", ""))

    ConfGui.Add("Text", "x25 y215 w60", "目标程序:")
    edPath2 := ConfGui.Add("Edit", "x85 y212 w200", IniRead(IniFile, "App2", "Path", ""))
    ConfGui.Add("Button", "x290 y211 w50 h24", "浏览").OnEvent("Click", (*) => SelectFile(edPath2))

    ConfGui.Add("Text", "x25 y245 w60", "运行环境:")
    emu2 := IniRead(IniFile, "App2", "EmuMode", "None")
    drop2 := ConfGui.Add("DropDownList", "x85 y242 w65 Choose" (emu2="LR"?3:emu2="LE"?2:1), ["None", "LE", "LR"])

    ConfGui.Add("Text", "x160 y245 w35", "GUID:")
    edGuid2 := ConfGui.Add("Edit", "x200 y242 w135", IniRead(IniFile, "App2", "LRGuid", ""))

    ; ---- 保存与取消 ----
    ConfGui.Add("Button", "x170 y315 w90 h32", "保存配置").OnEvent("Click", SaveConfig)
    ConfGui.Add("Button", "x270 y315 w90 h32", "取消返回").OnEvent("Click", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))

    ConfGui.Show("w370 h365")

    ; 内部函数：文件选择
    SelectFile(EditCtrl) {
        selected := FileSelect(3, A_WorkingDir, "选择目标执行文件", "Programs (*.exe; *.bat)")
        if (selected != "") {
            EditCtrl.Value := selected
        }
    }

    ; 内部函数：保存配置
    SaveConfig(*) {
        IniWrite(edName1.Value, IniFile, "App1", "Name")
        IniWrite(edPath1.Value, IniFile, "App1", "Path")
        IniWrite(drop1.Text, IniFile, "App1", "EmuMode")
        IniWrite(edGuid1.Value, IniFile, "App1", "LRGuid")

        IniWrite(edName2.Value, IniFile, "App2", "Name")
        IniWrite(edPath2.Value, IniFile, "App2", "Path")
        IniWrite(drop2.Text, IniFile, "App2", "EmuMode")
        IniWrite(edGuid2.Value, IniFile, "App2", "LRGuid")

        ; 更新主界面按钮名称
        btn1.Text := edName1.Value
        btn2.Text := edName2.Value

        MainGui.Opt("-Disabled")
        ConfGui.Destroy()
    }
}

; ==========================================
; 5. 关于界面 (About GUI)
; ==========================================
ShowAbout(*) {
    MainGui.Opt("+Disabled")
    AboutGui := Gui("-MaximizeBox -MinimizeBox +DPIScale +Owner" MainGui.Hwnd, "关于 About")
    AboutGui.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), AboutGui.Destroy()))
    AboutGui.SetFont("s9", "Microsoft YaHei UI")

    AboutGui.Add("Text", "x20 y20 w360", "此启动器是一个高度通用、高独立性的本地工具。`n配置数据保存在同目录的 LauncherConfig.ini 中，`n即使跨设备或更换游戏，也能轻松迁移。")
    AboutGui.Add("Text", "x20 y75 w360", "游戏本体如果是64位程序，请在设置中选择 LR 环境；`n设置GUI等32位程序，请在设置中选择 LE 环境。")
    
    AboutGui.Add("Text", "x20 y125 w40", "链接：")
    AboutGui.Add("Link", "x65 y125", '<a href="https://github.com/xupefei/Locale-Emulator/">Locale Emulator GitHub</a>')
    AboutGui.Add("Link", "x65 y150", '<a href="https://github.com/InWILL/Locale_Remulator/">Locale Remulator GitHub</a>')

    AboutGui.Add("Button", "x150 y190 w100 h30", "确定 (OK)").OnEvent("Click", (*) => (MainGui.Opt("-Disabled"), AboutGui.Destroy()))

    AboutGui.Show("w400 h240")
}
