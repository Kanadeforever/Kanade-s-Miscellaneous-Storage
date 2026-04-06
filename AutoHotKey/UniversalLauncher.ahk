#Requires AutoHotkey v2.0+
#SingleInstance Force
Persistent ; 开启常驻，支持托盘运行
SetWorkingDir(A_ScriptDir)

; ==========================================
; 全局配置与初始化
; ==========================================
SplitPath(A_ScriptName, , , , &OutNameNoExt)
global IniFile := A_ScriptDir "\" OutNameNoExt ".ini"

global IL := 0 ; 全局图标库句柄

; ==========================================
; 托盘图标设置 (系统右下角)
; ==========================================
A_IconTip := "通用启动器"
A_TrayMenu.Delete() ; 清空默认菜单
A_TrayMenu.Add("显示程序界面", (*) => MainGui.Show())
A_TrayMenu.Default := "显示程序界面" ; 双击托盘图标默认执行此项
A_TrayMenu.Add() ; 分隔线
A_TrayMenu.Add("退出", (*) => ExitApp())

; ==========================================
; 构建主界面 (Main GUI)
; ==========================================
global MainGui := Gui("-MaximizeBox +DPIScale", "通用启动器")
MainGui.BackColor := "FFFFFF"

; MainGui.OnEvent("Close", (*) => MainGui.Hide()) ; 点击右上角 X 时隐藏到托盘，而不是退出
MainGui.OnEvent("Close", MainGuiCloseHandler)
MainGui.OnEvent("DropFiles", GuiDropFilesHandler) ; 注册文件拖放事件

; 头部标题
MainGui.SetFont("s16 Bold", "Microsoft YaHei UI")
MainGui.Add("Text", "x20 y15 w300 c202020", "通用启动器")

; 右上角运行状态指示
MainGui.SetFont("s9 Bold")
global txtStatus := MainGui.Add("Text", "x390 y20 w140 Right c888888", "待机")

MainGui.SetFont("s9 Norm", "Microsoft YaHei UI")
MainGui.Add("Text", "x20 y45 w500 c888888", "双击运行，或将 *.exe / *.lnk 文件直接拖入此窗口添加")

; 列表控件美化 (移除 -Multi 参数，恢复默认的 Ctrl/Shift 多选功能)
MainGui.SetFont("s10 c202020")
global LV := MainGui.Add("ListView", "x20 y80 w470 h240 -Grid -E0x200", ["显示名称", "目标路径", "ID"])
LV.ModifyCol(1, 150) ; 名称列宽
LV.ModifyCol(2, 300) ; 路径列宽
LV.ModifyCol(3, 0)   ; 完全隐藏 ID 列

LV.OnEvent("DoubleClick", RunApp)

; 列表排序按钮 (放置在列表右侧)
MainGui.SetFont("s12 Bold")
btnUp := MainGui.Add("Button", "x500 y130 w30 h60", "∧")
btnUp.OnEvent("Click", (*) => ReorderItems(-1))

btnDown := MainGui.Add("Button", "x500 y210 w30 h60", "∨")
btnDown.OnEvent("Click", (*) => ReorderItems(1))

; 底部按钮均匀排版 (去除 Emoji，重新计算间距)
MainGui.SetFont("s10 Bold")
btnRun := MainGui.Add("Button", "x20 y340 w90 h36 Default", "运行")
btnRun.OnEvent("Click", RunApp)

MainGui.SetFont("s10 Norm")
btnAdd := MainGui.Add("Button", "x135 y340 w80 h36", "新增")
btnAdd.OnEvent("Click", AddApp)

btnEdit := MainGui.Add("Button", "x240 y340 w80 h36", "编辑")
btnEdit.OnEvent("Click", EditApp)

btnDel := MainGui.Add("Button", "x345 y340 w80 h36", "删除")
btnDel.OnEvent("Click", DelApp)

btnAbout := MainGui.Add("Button", "x450 y340 w80 h36", "设置&&关于")
btnAbout.OnEvent("Click", ShowAboutGui)

LoadList() ; 初始化加载列表
MainGui.Show("w550 h400")

; ==========================================
; 核心：文件拖放处理
; ==========================================
GuiDropFilesHandler(GuiObj, GuiCtrlObj, FileArray, X, Y) {
    for filePath in FileArray {
        SplitPath(filePath, , &OutDir, &OutExt, &OutNameNoExt)
        ext := StrLower(OutExt)

        ; 仅允许添加可执行文件和快捷方式
        if (ext != "exe" && ext != "lnk" && ext != "bat" && ext != "cmd")
            continue

        targetPath := filePath
        args := ""
        workDir := OutDir
        name := OutNameNoExt

        if (ext == "lnk") {
            FileGetShortcut(filePath, &targetPath, &workDir, &args)
            ; 部分特殊快捷方式(如UWP)提取不到真实路径，退回使用 lnk 自身路径
            if (targetPath == "")
                targetPath := filePath
        }

        ; 自动写入 INI 配置文件,更长的随机数或包含日期的字符串
        ; sec := "App_" A_TickCount "_" A_Index
        sec := "App_" A_Now "_" A_TickCount "_" A_Index
        IniWrite(name, IniFile, sec, "Name")
        IniWrite(targetPath, IniFile, sec, "Path")
        IniWrite(args, IniFile, sec, "Args")
        IniWrite(workDir, IniFile, sec, "WorkDir")
        IniWrite("Normal", IniFile, sec, "WinState")
        IniWrite("0", IniFile, sec, "RunAdmin")
        IniWrite("1", IniFile, sec, "WaitExit") ; 默认挂钩
    }
    LoadList()
}

; ==========================================
; 核心：列表排序重构 (上移/下移)
; ==========================================
ReorderItems(direction) {
    selRows := []
    row := 0
    while (row := LV.GetNext(row))
        selRows.Push(row)

    if !selRows.Length
        return

    ; 边界检查
    if (direction == -1 && selRows[1] == 1)
        return
    if (direction == 1 && selRows[selRows.Length] == LV.GetCount())
        return

    ; 将整个列表数据读入数组
    items := []
    Loop LV.GetCount() {
        sec := LV.GetText(A_Index, 3)
        obj := {}
        obj.Section := sec
        obj.Name := IniRead(IniFile, sec, "Name", "")
        obj.Path := IniRead(IniFile, sec, "Path", "")
        obj.Args := IniRead(IniFile, sec, "Args", "")
        obj.WorkDir := IniRead(IniFile, sec, "WorkDir", "")
        obj.WinState := IniRead(IniFile, sec, "WinState", "Normal")
        obj.RunAdmin := IniRead(IniFile, sec, "RunAdmin", "0")
        obj.WaitExit := IniRead(IniFile, sec, "WaitExit", "1")
        items.Push(obj)
    }

    newSelRows := []

    ; 冒泡置换数组中的对象
    if (direction == -1) { ; 上移 (从上往下遍历置换)
        for r in selRows {
            if (r > 1) {
                temp := items[r-1]
                items[r-1] := items[r]
                items[r] := temp
                newSelRows.Push(r-1)
            }
        }
    } else { ; 下移 (从下往上遍历置换)
        i := selRows.Length
        while (i > 0) {
            r := selRows[i]
            if (r < items.Length) {
                temp := items[r+1]
                items[r+1] := items[r]
                items[r] := temp
                newSelRows.Push(r+1)
            }
            i--
        }
    }

    ; 完全重写 INI 文件以固定顺序
    if FileExist(IniFile)
        FileDelete(IniFile)

    for item in items {
        IniWrite(item.Name, IniFile, item.Section, "Name")
        IniWrite(item.Path, IniFile, item.Section, "Path")
        IniWrite(item.Args, IniFile, item.Section, "Args")
        IniWrite(item.WorkDir, IniFile, item.Section, "WorkDir")
        IniWrite(item.WinState, IniFile, item.Section, "WinState")
        IniWrite(item.RunAdmin, IniFile, item.Section, "RunAdmin")
        IniWrite(item.WaitExit, IniFile, item.Section, "WaitExit")
    }

    ; 重新加载列表并恢复选中状态
    LoadList()
    for r in newSelRows
        LV.Modify(r, "Select Focus")
}

; ==========================================
; 列表加载与执行引擎
; ==========================================
LoadList(*) {
    LV.Delete()

    global IL
    if (IL)
        IL_Destroy(IL)

    IL := IL_Create(10, 10, 1)
    LV.SetImageList(IL, 1)

    if !FileExist(IniFile)
        return

    sections := IniRead(IniFile)
    for section in StrSplit(sections, "`n") {
        if (InStr(section, "App_") == 1) {
            name := IniRead(IniFile, section, "Name", "")
            path := IniRead(IniFile, section, "Path", "")

            iconIdx := IL_Add(IL, path, 1)
            if (!iconIdx)
                iconIdx := IL_Add(IL, "shell32.dll", 3)

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

    ; --- 1. 读取配置 ---
    sec      := LV.GetText(row, 3)
    name     := IniRead(IniFile, sec, "Name", "Unknown Program")
    path     := IniRead(IniFile, sec, "Path", "")
    args     := IniRead(IniFile, sec, "Args", "")
    workDir  := IniRead(IniFile, sec, "WorkDir", "")
    runAdmin := IniRead(IniFile, sec, "RunAdmin", "0")
    waitExit := IniRead(IniFile, sec, "WaitExit", "1")
    winState := IniRead(IniFile, sec, "WinState", "Normal")

    ; --- 2. 核心路径解析 ---
    ; 解析程序绝对路径
    absPath := ResolvePath(path)
    
    ; 解析工作目录：留空或 . 则指向 A_ScriptDir；支持 ..
    if (workDir == "" || workDir == ".") {
        absWorkDir := A_ScriptDir
    } else {
        absWorkDir := ResolvePath(workDir)
    }

    ; --- 3. 校验 ---
    if !FileExist(absPath) {
        MsgBox("找不到程序文件：`n" absPath, "启动失败", 16)
        return
    }
    if !DirExist(absWorkDir) {
        MsgBox("找不到起始目录：`n" absWorkDir, "路径错误", 16)
        return
    }

    ; --- 4. 构造 AHK 运行指令 ---
    ; 规则：如果路径包含空格，必须用双引号括起来
    ; 我们统一给 absPath 加双引号以确保安全
    targetWithArgs := '"' absPath '"'
    
    ; 即使 INI 删除了空格，这里强制补一个空格再加参数
    if (args != "")
        targetWithArgs .= " " Trim(args)

    ; 处理管理员权限前缀
    execStr := (runAdmin == "1") ? "*RunAs " targetWithArgs : targetWithArgs
    
    ; 处理运行状态 (Normal/Max/Min/Hide)
    runOpt := (winState != "Normal") ? winState : ""

    ; --- 5. 执行 ---
    try {
        txtStatus.Text := "正在运行:" name ""
        txtStatus.Opt("c008000")
        
        if (waitExit == "1") {
            MainGui.Hide()
            ; RunWait 的第二个参数即为 WorkingDir
            RunWait(execStr, absWorkDir, runOpt)
            MainGui.Restore()
            
            txtStatus.Text := "待机"
            txtStatus.Opt("c888888")
        } else {
            Run(execStr, absWorkDir, runOpt)
        }
    } catch as err {
        MsgBox("启动失败！`n指令：" execStr "`n起始位置：" absWorkDir "`n`n反馈：" err.Message, "错误", 16)
        MainGui.Restore()
        txtStatus.Text := "待机"
        txtStatus.Opt("c888888")
    }
}

; 助手函数：将路径解析为标准的绝对路径 (处理 .. 和 .)
ResolvePath(P) {
    if (P == "") {
        return
    }
    ; 如果是绝对路径
    if (SubStr(P, 2, 1) == ":" || SubStr(P, 1, 2) == "\\")
        return P
    
    ; 利用 Loop Files 获取 Windows 规范化的绝对路径 (可自动解析 ..)
    resolved := ""
    Loop Files, A_ScriptDir "\" P, "DF" {
        resolved := A_LoopFileFullPath
        break
    }
    ; 如果文件尚未存在(无法解析)，则手动拼接
    return (resolved != "") ? resolved : A_ScriptDir "\" LTrim(P, "\")
}

; ==========================================
; 增删改管理逻辑
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
    ; 1. 收集所有选中的行号
    row := 0
    selRows := []
    while (row := LV.GetNext(row))
        selRows.Push(row)

    if !selRows.Length {
        MsgBox("请先选择要删除的程序！", "提示", 48)
        return
    }

    ; 2. 根据选中数量动态生成提示信息
    if (selRows.Length == 1) {
        msg := "确定删除【" LV.GetText(selRows[1], 1) "】吗？"
    } else {
        msg := "确定要批量删除选中的 " selRows.Length " 个程序吗？"
    }

    ; 3. 确认后批量删除
    if (MsgBox(msg, "确认删除", 52) == "Yes") {
        for r in selRows {
            sec := LV.GetText(r, 3) ; 读取隐藏的 ID
            IniDelete(IniFile, sec) ; 从 INI 中删除对应节点
        }
        LoadList() ; 统一重新加载列表
    }
}

; ==========================================
; 关闭程序 逻辑
; ==========================================
MainGuiCloseHandler(*) {
    ; 读取全局设置，默认值为 1 (关闭到托盘)
    isCloseToTray := IniRead(IniFile, "Settings", "CloseToTray", "1")

    if (isCloseToTray == "1")
        MainGui.Hide()
    else
        ExitApp()
}

; ==========================================
; 关于界面 (About GUI)
; ==========================================
ShowAboutGui(*) {
    MainGui.Opt("+Disabled")
    AboutDlg := Gui("-MaximizeBox +DPIScale +Owner" MainGui.Hwnd, "设置`&关于 20260405")
    AboutDlg.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), AboutDlg.Destroy()))
    AboutDlg.BackColor := "FFFFFF"

    ; --- 全局设置区域 ---
    AboutDlg.SetFont("s10 Bold", "Microsoft YaHei UI")
    AboutDlg.Add("Text", "x20 y20 w260 c202020", "全局设置")

    AboutDlg.SetFont("s9 Norm")
    ; 读取当前设置状态
    currentSet := IniRead(IniFile, "Settings", "CloseToTray", "1")
    chkTray := AboutDlg.Add("Checkbox", "x30 y50 w240 Checked" currentSet, "点击关闭按钮时最小化到系统托盘")

    ; AboutDlg.Add("Text", "x30 y75 w240 c888888", "(如果不勾选，点击关闭将直接退出程序)")

    ; 分隔线
    AboutDlg.Add("Text", "x20 y150 w260 h1 +BackgroundE0E0E0")

    ; --- 关于信息区域 ---
    AboutDlg.SetFont("s10 Bold")
    AboutDlg.Add("Text", "x20 y160 w260 c202020", "关于程序")

    AboutDlg.SetFont("s9 Norm")
    AboutDlg.Add("Text", "x30 y190 w240", "这是一个轻量级通用启动器。`n基于 AutoHotkey v2 构建。`n支持拖放快速添加应用、批量排序、自定义运行参数以及托盘常驻等高级功能。")

    AboutDlg.Add("Text", "x30 y260 w240", "by Luminous && Gemini && Copilot")

    ; 网址占位符
    AboutDlg.Add("Link", "x30 y280 w240", "技术支持：<a href=`"https://gemini.google.com`">Gemini 官网</a>")
    AboutDlg.Add("Link", "x30 y300 w240", "　　　　　<a href=`"https://copilot.microsoft.com/?cc=us`">Copilot 官网</a>")

    ; 保存并关闭按钮
    btnSaveAbout := AboutDlg.Add("Button", "x110 y340 w80 h32 Default", "确定")
    btnSaveAbout.OnEvent("Click", (*) => (
        IniWrite(chkTray.Value, IniFile, "Settings", "CloseToTray"), ; 保存设置
        MainGui.Opt("-Disabled"),
        AboutDlg.Destroy()
    ))

    AboutDlg.Show("w300 h400")
}

; ==========================================
; 高级参数配置界面 (Config GUI)
; ==========================================
ShowConfigGui() {
    MainGui.Opt("+Disabled")
    ConfGui := Gui("-MaximizeBox +DPIScale +Owner" MainGui.Hwnd, (EditSectionID == "") ? "添加新程序" : "编辑程序配置")
    ConfGui.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))
    ConfGui.BackColor := "FFFFFF"

    ConfGui.SetFont("s14 Bold", "Microsoft YaHei UI")
    ConfGui.Add("Text", "x25 y15 w350 c202020", (EditSectionID == "") ? "添加新程序" : "编辑程序配置")

    ConfGui.SetFont("s9 Norm", "Microsoft YaHei UI")

    vName := "", vPath := "", vArgs := "", vWorkDir := "", vWinState := "Normal", vRunAdmin := 0, vWaitExit := 1
    if (EditSectionID != "") {
        vName := IniRead(IniFile, EditSectionID, "Name", "")
        vPath := IniRead(IniFile, EditSectionID, "Path", "")
        vArgs := IniRead(IniFile, EditSectionID, "Args", "")
        vWorkDir := IniRead(IniFile, EditSectionID, "WorkDir", "")
        vWinState := IniRead(IniFile, EditSectionID, "WinState", "Normal")
        vRunAdmin := IniRead(IniFile, EditSectionID, "RunAdmin", 0)
        vWaitExit := IniRead(IniFile, EditSectionID, "WaitExit", 1)
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
    edWorkDir := ConfGui.Add("Edit", "x100 y197 w260", vWorkDir)
    ConfGui.Add("Text", "x100 y225 w260 c888888", "(留空则默认使用程序所在目录，可手动粘贴路径)")

    ConfGui.Add("Text", "x25 y270 w70", "运行状态:")
    idx := (vWinState="Max") ? 2 : (vWinState="Min") ? 3 : (vWinState="Hide") ? 4 : 1
    ddlWinState := ConfGui.Add("DropDownList", "x100 y267 w100 Choose" idx, ["Normal", "Max", "Min", "Hide"])

    chkAdmin := ConfGui.Add("Checkbox", "x220 y270 w140 Checked" vRunAdmin, "以管理员身份运行")
    chkWait := ConfGui.Add("Checkbox", "x100 y305 w260 Checked" vWaitExit, "挂起启动器 (等待该程序结束后再继续)")

    ConfGui.Add("Button", "x160 y345 w90 h32", "保存").OnEvent("Click", SaveConfig)
    ConfGui.Add("Button", "x270 y345 w90 h32", "取消").OnEvent("Click", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))

    ConfGui.Show("w390 h400")

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
        IniWrite(chkWait.Value, IniFile, sec, "WaitExit")

        MainGui.Opt("-Disabled")
        ConfGui.Destroy()

        LoadList()
    }
}
