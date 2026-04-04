#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
SetWorkingDir(A_ScriptDir)

; ==========================================
; 配置文件路径
; ==========================================
; 下面是固定文件名称
; global IniFile := A_ScriptDir "\UniversalLauncher.ini"

; 下面是动态文件名；
; A_ScriptName 在脚本运行时是 "文件名.ahk"，编译后是 "文件名.exe"
SplitPath(A_ScriptName, , , , &OutNameNoExt)
global IniFile := A_ScriptDir "\" OutNameNoExt ".ini"

global IL := 0 ; 全局图标库句柄



; ==========================================
; 主界面 (Main GUI) - 现代化美化
; ==========================================
global MainGui := Gui("-MaximizeBox +DPIScale", "Universal Launcher")
MainGui.BackColor := "FFFFFF" ; 纯白背景色更显现代感

; 现代化头部标题
MainGui.SetFont("s16 Bold", "Microsoft YaHei UI")
MainGui.Add("Text", "x20 y15 w500 c202020", "🚀 我的启动库")

MainGui.SetFont("s9 Norm", "Microsoft YaHei UI")
MainGui.Add("Text", "x20 y45 w500 c888888", "双击列表项即可极速启动对应的应用程序或游戏")

; 列表控件美化：去除网格(-Grid)，去除3D下沉边框(-E0x200)，开启整行选中
MainGui.SetFont("s10 c202020")
global LV := MainGui.Add("ListView", "x20 y80 w500 h240 -Grid -Multi -E0x200", ["显示名称", "目标路径", "ID"])
LV.ModifyCol(1, 160) ; 名称列宽
LV.ModifyCol(2, 320) ; 路径列宽
LV.ModifyCol(3, 0)   ; 完全隐藏 ID 列

LV.OnEvent("DoubleClick", RunApp)

; 底部按钮均匀排版
MainGui.SetFont("s10 Bold")
btnRun := MainGui.Add("Button", "x28 y340 w100 h36 Default", "▶ 运行")
btnRun.OnEvent("Click", RunApp)

MainGui.SetFont("s10 Norm")
btnAdd := MainGui.Add("Button", "x156 y340 w100 h36", "➕ 新增")
btnAdd.OnEvent("Click", AddApp)

btnEdit := MainGui.Add("Button", "x284 y340 w100 h36", "⚙️ 编辑")
btnEdit.OnEvent("Click", EditApp)

btnDel := MainGui.Add("Button", "x412 y340 w100 h36", "🗑️ 删除")
btnDel.OnEvent("Click", DelApp)

LoadList() ; 初始化加载列表
MainGui.Show("w540 h400")


; ==========================================
; 核心逻辑：动态加载列表与图标
; ==========================================
LoadList(*) {
    LV.Delete()
    
    global IL
    if (IL)
        IL_Destroy(IL) ; 重新加载时销毁旧图标库，防止内存泄露
        
    ; 创建带大图标 (32x32) 的 ImageList，利用它撑开 ListView 的行高
    IL := IL_Create(10, 10, 1) 
    LV.SetImageList(IL, 1)

    if !FileExist(IniFile)
        return

    sections := IniRead(IniFile)
    for section in StrSplit(sections, "`n") {
        if (InStr(section, "App_") == 1) {
            name := IniRead(IniFile, section, "Name", "")
            path := IniRead(IniFile, section, "Path", "")
            
            ; 动态提取程序的真实图标
            iconIdx := IL_Add(IL, path, 1)
            if (!iconIdx) ; 如果提取失败(比如只是个参数或文件不存在)，用系统默认空白文件图标兜底
                iconIdx := IL_Add(IL, "shell32.dll", 3)
            
            ; 插入数据：列1=名称, 列2=路径, 列3=隐藏的ID
            LV.Add("Icon" iconIdx, name, path, section)
        }
    }
}


; ==========================================
; 核心逻辑：运行程序
; ==========================================
RunApp(*) {
    row := LV.GetNext(0)
    if !row {
        MsgBox("请先在列表中选择要运行的程序！", "提示", 48)
        return
    }

    sec := LV.GetText(row, 3) ; 关键：现在从第 3 列读取隐藏的 Section ID
    path := IniRead(IniFile, sec, "Path", "")
    args := IniRead(IniFile, sec, "Args", "")
    workDir := IniRead(IniFile, sec, "WorkDir", "")
    winState := IniRead(IniFile, sec, "WinState", "Normal")
    runAdmin := IniRead(IniFile, sec, "RunAdmin", "0")

    if (path == "") {
        MsgBox("未配置有效的目标路径！", "错误", 16)
        return
    }

    targetCmd := path
    if InStr(path, " ")
        targetCmd := '"' path '"'
    if (args != "")
        targetCmd .= " " args

    if (workDir == "")
        SplitPath(path, , &workDir)

    runOpt := (winState != "Normal") ? winState : ""
    
    try {
        MainGui.Hide()
        if (runAdmin == "1")
            RunWait("*RunAs " targetCmd, workDir, runOpt)
        else
            RunWait(targetCmd, workDir, runOpt)
        MainGui.Restore()
    } catch as err {
        MsgBox("启动失败！`n目标命令：" targetCmd "`n起始位置：" workDir "`n`n系统反馈：" err.Message, "错误", 16)
        MainGui.Restore()
    }
}


; ==========================================
; 增、删、改 触发接口
; ==========================================
global EditSectionID := ""

AddApp(*) {
    global EditSectionID := ""
    ShowConfigGui()
}

EditApp(*) {
    row := LV.GetNext(0)
    if !row {
        MsgBox("请先选择要编辑的程序！", "提示", 48)
        return
    }
    global EditSectionID := LV.GetText(row, 3)
    ShowConfigGui()
}

DelApp(*) {
    row := LV.GetNext(0)
    if !row {
        MsgBox("请先选择要删除的程序！", "提示", 48)
        return
    }
    
    name := LV.GetText(row, 1)
    if (MsgBox("确定要将【" name "】从列表中永久删除吗？", "确认删除", 52) == "Yes") {
        sec := LV.GetText(row, 3)
        IniDelete(IniFile, sec)  
        LoadList()               
    }
}


; ==========================================
; 高级参数配置界面 (Config GUI) - 同步美化
; ==========================================
ShowConfigGui() {
    MainGui.Opt("+Disabled")
    ConfGui := Gui("-MaximizeBox +DPIScale +Owner" MainGui.Hwnd, (EditSectionID == "") ? "添加新程序" : "编辑程序配置")
    ConfGui.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))
    ConfGui.BackColor := "FFFFFF"
    
    ConfGui.SetFont("s14 Bold", "Microsoft YaHei UI")
    ConfGui.Add("Text", "x25 y15 w350 c202020", (EditSectionID == "") ? "✨ 添加新程序" : "⚙️ 编辑程序配置")

    ConfGui.SetFont("s9 Norm", "Microsoft YaHei UI")

    vName := "", vPath := "", vArgs := "", vWorkDir := "", vWinState := "Normal", vRunAdmin := 0
    if (EditSectionID != "") {
        vName := IniRead(IniFile, EditSectionID, "Name", "")
        vPath := IniRead(IniFile, EditSectionID, "Path", "")
        vArgs := IniRead(IniFile, EditSectionID, "Args", "")
        vWorkDir := IniRead(IniFile, EditSectionID, "WorkDir", "")
        vWinState := IniRead(IniFile, EditSectionID, "WinState", "Normal")
        vRunAdmin := IniRead(IniFile, EditSectionID, "RunAdmin", 0)
    }

    ConfGui.Add("Text", "x25 y65 w70", "显示名称:")
    edName := ConfGui.Add("Edit", "x100 y62 w260", vName)

    ConfGui.Add("Text", "x25 y105 w70", "目标程序:")
    edPath := ConfGui.Add("Edit", "x100 y102 w200", vPath)
    ConfGui.Add("Button", "x310 y101 w50 h24", "浏览").OnEvent("Click", (*) => (
        sel := FileSelect(3, A_WorkingDir, "选择目标程序", "应用程序 (*.exe; *.bat; *.cmd; *.lnk)"),
        (sel != "") ? edPath.Value := sel : ""
    ))

    ConfGui.Add("Text", "x25 y145 w70", "启动参数:")
    edArgs := ConfGui.Add("Edit", "x100 y142 w260", vArgs)
    ConfGui.Add("Text", "x100 y170 w260 c888888", "(例如填写 -windowed 或 -run，无需求请留空)")

    ConfGui.Add("Text", "x25 y200 w70", "起始位置:")
    edWorkDir := ConfGui.Add("Edit", "x100 y197 w200", vWorkDir)
    ConfGui.Add("Button", "x310 y196 w50 h24", "浏览").OnEvent("Click", (*) => (
        sel := DirSelect(A_WorkingDir, 3, "选择起始位置 (工作目录)"),
        (sel != "") ? edWorkDir.Value := sel : ""
    ))

    ConfGui.Add("Text", "x25 y240 w70", "运行状态:")
    idx := (vWinState="Max") ? 2 : (vWinState="Min") ? 3 : (vWinState="Hide") ? 4 : 1
    ddlWinState := ConfGui.Add("DropDownList", "x100 y237 w100 Choose" idx, ["Normal", "Max", "Min", "Hide"])

    chkAdmin := ConfGui.Add("Checkbox", "x220 y240 w140 Checked" vRunAdmin, "以管理员身份运行")

    ConfGui.Add("Button", "x160 y295 w90 h32", "💾 保存").OnEvent("Click", SaveConfig)
    ConfGui.Add("Button", "x270 y295 w90 h32", "❌ 取消").OnEvent("Click", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))

    ConfGui.Show("w390 h350")

    SaveConfig(*) {
        if (edName.Value == "" || edPath.Value == "") {
            MsgBox("【显示名称】和【目标程序】为必填项！", "提示", 48)
            return
        }

        sec := (EditSectionID == "") ? "App_" A_TickCount : EditSectionID

        IniWrite(edName.Value, IniFile, sec, "Name")
        IniWrite(edPath.Value, IniFile, sec, "Path")
        IniWrite(edArgs.Value, IniFile, sec, "Args")
        IniWrite(edWorkDir.Value, IniFile, sec, "WorkDir")
        IniWrite(ddlWinState.Text, IniFile, sec, "WinState")
        IniWrite(chkAdmin.Value, IniFile, sec, "RunAdmin")

        MainGui.Opt("-Disabled")
        ConfGui.Destroy()
        
        LoadList() 
    }
}