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
global ItemsArray := [] ; 在内存中维护一份列表数据，用于搜索和排序

; 读取全局设置
global GlobalCloseToTray := IniRead(IniFile, "Settings", "CloseToTray", "0")
global GlobalFreqSort    := IniRead(IniFile, "Settings", "FreqSort", "0")
global GlobalAutoRelPath := IniRead(IniFile, "Settings", "AutoRelPath", "1")
global GlobalRememberPos := IniRead(IniFile, "Settings", "RememberPos", "0")

; ==========================================
; 托盘图标设置
; ==========================================
A_IconTip := "通用启动器"
A_TrayMenu.Delete() ; 清空默认菜单
A_TrayMenu.Add("显示程序界面", (*) => ShowMainGui())
A_TrayMenu.Default := "显示程序界面" ; 双击托盘图标默认执行此项
A_TrayMenu.Add() ; 分隔线
A_TrayMenu.Add("退出", GuiExitHandler)

; ==========================================
; 构建主界面 (Main GUI)
; ==========================================
global MainGui := Gui("-MaximizeBox +DPIScale", "通用启动器")
MainGui.BackColor := "FFFFFF"
MainGui.OnEvent("Close", MainGuiCloseHandler)
MainGui.OnEvent("DropFiles", GuiDropFilesHandler) ; 注册文件拖放事件

; 头部标题
MainGui.SetFont("s16 Bold", "Microsoft YaHei UI")
MainGui.Add("Text", "x20 y15 w150 c202020", "通用启动器")

; 搜索功能组合 (与右侧排序按钮右对齐)
MainGui.SetFont("s9 Norm", "Microsoft YaHei UI")
global edSearch := MainGui.Add("Edit", "x330 y15 w140 Hidden", "")
edSearch.OnEvent("Change", FilterList)
global btnSearch := MainGui.Add("Button", "x480 y14 w50 h24", "搜索")
btnSearch.OnEvent("Click", ToggleSearch)

; 状态指示 (移至右侧搜索按钮下方，并设置为右对齐)
MainGui.SetFont("s9 Bold")
global txtStatus := MainGui.Add("Text", "x430 y45 w100 Right c888888", "待机")

MainGui.Add("Text", "x20 y45 w400 c888888", "双击运行，或将 *.exe/cmd/bat/lnk 文件直接拖入此窗口添加")

; 列表控件
MainGui.SetFont("s10 c202020")
global LV := MainGui.Add("ListView", "x20 y80 w470 h240 -Grid -E0x200", ["显示名称", "目标路径", "ID"])
LV.ModifyCol(1, 150) ; 名称列宽
LV.ModifyCol(2, 300) ; 路径列宽
LV.ModifyCol(3, 0)   ; 完全隐藏 ID 列

LV.OnEvent("DoubleClick", RunApp)
LV.OnEvent("ContextMenu", ShowContextMenu) ; 绑定右键菜单

; 排序按钮 (放置在列表右侧)
MainGui.SetFont("s12 Bold")
global btnUp := MainGui.Add("Button", "x500 y130 w30 h60", "∧")
btnUp.OnEvent("Click", (*) => ReorderItems(-1))

global btnDown := MainGui.Add("Button", "x500 y210 w30 h60", "∨")
btnDown.OnEvent("Click", (*) => ReorderItems(1))

; 底部按钮
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

; 构建右键菜单
global ContextMenu := Menu()
ContextMenu.Add("运行", (*) => RunApp())
ContextMenu.Add("以管理员身份运行", (*) => RunApp(,, true))
ContextMenu.Add()
ContextMenu.Add("打开文件所在位置", OpenFileLocation)
ContextMenu.Add()
ContextMenu.Add("编辑", EditApp)
ContextMenu.Add("删除", DelApp)

LoadList()
ShowMainGui()

; ==========================================
; 右键菜单处理
; ==========================================
ShowContextMenu(GuiCtrlObj, Item, IsRightClick, X, Y) {
    if (Item > 0)
        ContextMenu.Show()
}

OpenFileLocation(*) {
    row := LV.GetNext(0)
    if !row
        return
    sec := LV.GetText(row, 3)
    path := IniRead(IniFile, sec, "Path", "")
    absPath := ResolvePath(path)
    if FileExist(absPath)
        Run('explorer.exe /select,"' absPath '"')
    else
        MsgBox("文件不存在，无法定位所在目录！", "错误", 16)
}

; ==========================================
; 显示与关闭逻辑 (包含窗口记忆)
; ==========================================
ShowMainGui() {
    posStr := ""
    if (GlobalRememberPos) {
        winX := IniRead(IniFile, "Settings", "WinX", "")
        winY := IniRead(IniFile, "Settings", "WinY", "")
        if (winX != "" && winY != "")
            posStr := " x" winX " y" winY
    }
    MainGui.Show("w550 h400" posStr)
}

SaveWinPos() {
    if (GlobalRememberPos) {
        MainGui.GetPos(&x, &y)
        IniWrite(x, IniFile, "Settings", "WinX")
        IniWrite(y, IniFile, "Settings", "WinY")
    }
}

MainGuiCloseHandler(*) {
    SaveWinPos()
    if (GlobalCloseToTray == "1")
        MainGui.Hide()
    else
        ExitApp()
}

GuiExitHandler(*) {
    SaveWinPos()
    ExitApp()
}

; ==========================================
; 搜索/过滤功能
; ==========================================
ToggleSearch(*) {
    if (edSearch.Visible) {
        edSearch.Visible := false
        edSearch.Value := ""
        btnSearch.Text := "搜索"
        RenderList() ; 恢复全量列表
    } else {
        edSearch.Visible := true
        btnSearch.Text := "取消"
        edSearch.Focus()
    }
}

FilterList(*) {
    keyword := edSearch.Value
    RenderList(keyword)
}

; ==========================================
; 文件拖放处理 (包含防重与相对路径)
; ==========================================
GuiDropFilesHandler(GuiObj, GuiCtrlObj, FileArray, X, Y) {
    addedCount := 0
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

        ; 自动转换为相对路径 (如果开启)
        if (GlobalAutoRelPath) {
            if (InStr(targetPath, A_ScriptDir) == 1) {
                ; 仅截掉开头的脚本目录部分，保留后续路径
                targetPath := ".\" LTrim(SubStr(targetPath, StrLen(A_ScriptDir) + 1), "\")
            }
        }

        ; 查重验证
        isDuplicate := false
        for item in ItemsArray {
            if (item.Path == targetPath) {
                isDuplicate := true
                break
            }
        }
        if (isDuplicate)
            continue

        ; 自动写入 INI 配置文件,更长的随机数或包含日期的字符串
        sec := "App_" A_Now "_" A_TickCount "_" A_Index
        IniWrite(name, IniFile, sec, "Name")
        IniWrite(targetPath, IniFile, sec, "Path")
        IniWrite(args, IniFile, sec, "Args")
        IniWrite(workDir, IniFile, sec, "WorkDir")
        IniWrite("", IniFile, sec, "IconPath") ; 默认无自定义图标
        IniWrite("Normal", IniFile, sec, "WinState")
        IniWrite("0", IniFile, sec, "RunAdmin")
        IniWrite("1", IniFile, sec, "WaitExit")
        IniWrite("0", IniFile, sec, "RunCount")
        IniWrite("0", IniFile, sec, "LastRun")
        
        addedCount++
    }
    if (addedCount > 0)
        LoadList()
}

; ==========================================
; 核心：列表数据加载与渲染
; ==========================================
LoadList(*) {
    global ItemsArray := []
    
    if !FileExist(IniFile)
        return

    sections := IniRead(IniFile)
    for section in StrSplit(sections, "`n") {
        if (InStr(section, "App_") == 1) {
            obj := {}
            obj.Section := section
            obj.Name := IniRead(IniFile, section, "Name", "")
            obj.Path := IniRead(IniFile, section, "Path", "")
            obj.Args := IniRead(IniFile, section, "Args", "")
            obj.WorkDir := IniRead(IniFile, section, "WorkDir", "")
            obj.IconPath := IniRead(IniFile, section, "IconPath", "")
            obj.WinState := IniRead(IniFile, section, "WinState", "Normal")
            obj.RunAdmin := IniRead(IniFile, section, "RunAdmin", "0")
            obj.WaitExit := IniRead(IniFile, section, "WaitExit", "1")
            obj.RunCount := IniRead(IniFile, section, "RunCount", "0")
            obj.LastRun := IniRead(IniFile, section, "LastRun", "0")
            ItemsArray.Push(obj)
        }
    }

    ; 频次排序逻辑处理
    if (GlobalFreqSort == "1") {
        btnUp.Opt("+Disabled")
        btnDown.Opt("+Disabled")
        SortArrayByFreq(&ItemsArray)
    } else {
        btnUp.Opt("-Disabled")
        btnDown.Opt("-Disabled")
    }

    RenderList()
}

SortArrayByFreq(&arr) {
    ; 自定义冒泡排序：先按 RunCount 降序，再按 LastRun 降序
    len := arr.Length
    Loop len {
        i := A_Index
        Loop len - i {
            j := A_Index
            a := arr[j], b := arr[j+1]
            swap := false

            if (Integer(a.RunCount) < Integer(b.RunCount))
                swap := true
            else if (Integer(a.RunCount) == Integer(b.RunCount) && Integer(a.LastRun) < Integer(b.LastRun))
                swap := true

            if (swap) {
                temp := arr[j]
                arr[j] := arr[j+1]
                arr[j+1] := temp
            }
        }
    }
}

RenderList(filterKeyword := "") {
    LV.Delete()
    
    global IL
    newIL := IL_Create(10, 10, 1)
    
    ; 先将新的 IL 绑定到 ListView，并获取旧的 IL
    oldIL := LV.SetImageList(newIL, 1)
    if (oldIL)
        IL_Destroy(oldIL) ; 安全销毁旧句柄
    
    IL := newIL ; 更新全局变量

    if (IL)
        IL_Destroy(IL)
    IL := IL_Create(10, 10, 1)
    LV.SetImageList(IL, 1)

    kw := StrLower(filterKeyword)

    for item in ItemsArray {
        ; 搜索匹配 (对名称或路径)
        if (kw != "") {
            if !(InStr(StrLower(item.Name), kw) || InStr(StrLower(item.Path), kw))
                continue
        }

        iconSource := (item.IconPath != "") ? item.IconPath : item.Path
        absIcon := ResolvePath(iconSource)
        iconIdx := IL_Add(IL, absIcon, 1)
        if (!iconIdx)
            iconIdx := IL_Add(IL, "shell32.dll", 3)

        LV.Add("Icon" iconIdx, item.Name, item.Path, item.Section)
    }
}

; ==========================================
; 核心：运行程序 (含参数拼接与频次统计)
; ==========================================
RunApp(GuiObj:=0, Row:=0, ForceAdmin:=false) {
    if !Row
        Row := LV.GetNext(0)
    if !Row
        return

    sec      := LV.GetText(Row, 3)
    name     := IniRead(IniFile, sec, "Name", "Unknown")
    path     := IniRead(IniFile, sec, "Path", "")
    args     := IniRead(IniFile, sec, "Args", "")
    workDir  := IniRead(IniFile, sec, "WorkDir", "")
    runAdmin := IniRead(IniFile, sec, "RunAdmin", "0")
    waitExit := IniRead(IniFile, sec, "WaitExit", "1")
    winState := IniRead(IniFile, sec, "WinState", "Normal")
    runCount := IniRead(IniFile, sec, "RunCount", "0")

    if (ForceAdmin)
        runAdmin := "1"

    absPath := ResolvePath(path)
    absWorkDir := (workDir == "" || workDir == ".") ? A_ScriptDir : ResolvePath(workDir)

    if !FileExist(absPath) {
        MsgBox("找不到程序文件：`n" absPath, "启动失败", 16)
        return
    }

    ; 安全拼接指令（双引号包裹路径 + 外部参数）
    targetWithArgs := '"' absPath '"'
    if (args != "")
        targetWithArgs .= " " Trim(args)

    execStr := (runAdmin == "1") ? "*RunAs " targetWithArgs : targetWithArgs
    runOpt := (winState != "Normal") ? winState : ""

    try {
        txtStatus.Value := "正在运行:" name ""
        txtStatus.SetFont("c008000")
        
        ; 记录频次
        IniWrite(runCount + 1, IniFile, sec, "RunCount")
        IniWrite(A_Now, IniFile, sec, "LastRun")

        if (waitExit == "1") {
            MainGui.Hide()
            RunWait(execStr, absWorkDir, runOpt)
            ShowMainGui()
            txtStatus.Value := "待机"
            txtStatus.SetFont("c888888")
        } else {
            Run(execStr, absWorkDir, runOpt)
            txtStatus.Value := "待机"
            txtStatus.SetFont("c888888")
        }
        
        ; 如果开启了频次排序，静默重载列表以更新顺序
        if (GlobalFreqSort == "1")
            LoadList()

    } catch as err {
        MsgBox("启动失败！`n指令：" execStr "`n起始位置：" absWorkDir "`n`n反馈：" err.Message, "错误", 16)
        ShowMainGui()
        txtStatus.Value := "待机"
        txtStatus.SetFont("c888888")
    }
}

; ==========================================
; 助手函数：解析环境变量与绝对路径
; ==========================================
ExpandEnvVars(str) {
    if !InStr(str, "%")
        return str
    VarSetStrCapacity(&buf, 32767)
    DllCall("ExpandEnvironmentStrings", "Str", str, "Str", buf, "UInt", 32767)
    return buf
}

ResolvePath(P) {
    if (P == "")
        return ""
    
    P := ExpandEnvVars(P) ; 先展开系统变量
    
    ; 如果已经是绝对路径
    if (SubStr(P, 2, 1) == ":" || SubStr(P, 1, 2) == "\\")
        return P
    
    ; 相对路径解析 (如 .\ 或 ..\)
    resolved := ""
    Loop Files, A_ScriptDir "\" P, "DF" {
        resolved := A_LoopFileFullPath
        break
    }
    return (resolved != "") ? resolved : A_ScriptDir "\" LTrim(P, "\")
}

; ==========================================
; 排序逻辑 (修复跨节点污染)
; ==========================================
ReorderItems(direction) {
    if (GlobalFreqSort == "1")
        return ; 频次排序开启时禁用手动排序

    ; 【新增拦截】搜索状态下禁止手动排序
    if (edSearch.Visible && edSearch.Value != "") {
        MsgBox("搜索状态下禁止手动排序，请先取消搜索！", "提示", 48)
        return
    }

    selRows := []
    row := 0
    while (row := LV.GetNext(row))
        selRows.Push(row)

    if !selRows.Length
        return

    if (direction == -1 && selRows[1] == 1) || (direction == 1 && selRows[selRows.Length] == LV.GetCount())
        return

    ; 将当前列表的 Section 顺序映射出来
    seq := []
    Loop LV.GetCount()
        seq.Push(LV.GetText(A_Index, 3))

    newSelRows := []

    if (direction == -1) {
        for r in selRows {
            if (r > 1) {
                temp := seq[r-1], seq[r-1] := seq[r], seq[r] := temp
                newSelRows.Push(r-1)
            }
        }
    } else {
        i := selRows.Length
        while (i > 0) {
            r := selRows[i]
            if (r < seq.Length) {
                temp := seq[r+1], seq[r+1] := seq[r], seq[r] := temp
                newSelRows.Push(r+1)
            }
            i--
        }
    }

    ; 保留 Settings
    settingsData := ""
    try settingsData := IniRead(IniFile, "Settings")

    ; 重建 INI 文件
    if FileExist(IniFile)
        FileDelete(IniFile)

    ; 1. 恢复 Settings
    if (settingsData != "")
        IniWrite(settingsData, IniFile, "Settings")

    ; 2. 按新顺序写入 App_
    for sec in seq {
        for item in ItemsArray {
            if (item.Section == sec) {
                IniWrite(item.Name, IniFile, sec, "Name")
                IniWrite(item.Path, IniFile, sec, "Path")
                IniWrite(item.Args, IniFile, sec, "Args")
                IniWrite(item.WorkDir, IniFile, sec, "WorkDir")
                IniWrite(item.IconPath, IniFile, sec, "IconPath")
                IniWrite(item.WinState, IniFile, sec, "WinState")
                IniWrite(item.RunAdmin, IniFile, sec, "RunAdmin")
                IniWrite(item.WaitExit, IniFile, sec, "WaitExit")
                IniWrite(item.RunCount, IniFile, sec, "RunCount")
                IniWrite(item.LastRun, IniFile, sec, "LastRun")
                break
            }
        }
    }

    LoadList()
    for r in newSelRows
        LV.Modify(r, "Select Focus")
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
    row := 0
    selRows := []
    while (row := LV.GetNext(row))
        selRows.Push(row)

    if !selRows.Length {
        MsgBox("请先选择要删除的程序！", "提示", 48)
        return
    }

    msg := (selRows.Length == 1) ? "确定删除【" LV.GetText(selRows[1], 1) "】吗？" : "确定要批量删除选中的 " selRows.Length " 个程序吗？"

    if (MsgBox(msg, "确认删除", 52) == "Yes") {
        for r in selRows {
            sec := LV.GetText(r, 3)
            IniDelete(IniFile, sec)
        }
        LoadList()
    }
}

; ==========================================
; 高级参数配置界面 (含自定义图标)
; ==========================================
ShowConfigGui() {
    MainGui.Opt("+Disabled")
    ConfGui := Gui("-MaximizeBox +DPIScale +Owner" MainGui.Hwnd, (EditSectionID == "") ? "添加新程序" : "编辑程序配置")
    ConfGui.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))
    ConfGui.BackColor := "FFFFFF"

    ConfGui.SetFont("s14 Bold", "Microsoft YaHei UI")
    ConfGui.Add("Text", "x25 y15 w350 c202020", (EditSectionID == "") ? "添加新程序" : "编辑程序配置")

    ConfGui.SetFont("s9 Norm", "Microsoft YaHei UI")

    vName := "", vPath := "", vArgs := "", vWorkDir := "", vIcon := "", vWinState := "Normal", vRunAdmin := 0, vWaitExit := 1
    if (EditSectionID != "") {
        vName := IniRead(IniFile, EditSectionID, "Name", "")
        vPath := IniRead(IniFile, EditSectionID, "Path", "")
        vArgs := IniRead(IniFile, EditSectionID, "Args", "")
        vWorkDir := IniRead(IniFile, EditSectionID, "WorkDir", "")
        vIcon := IniRead(IniFile, EditSectionID, "IconPath", "")
        vWinState := IniRead(IniFile, EditSectionID, "WinState", "Normal")
        vRunAdmin := IniRead(IniFile, EditSectionID, "RunAdmin", 0)
        vWaitExit := IniRead(IniFile, EditSectionID, "WaitExit", 1)
    }

    ConfGui.Add("Text", "x25 y65 w70", "显示名称:")
    edName := ConfGui.Add("Edit", "x100 y62 w260", vName)

    ConfGui.Add("Text", "x25 y105 w70", "目标程序:")
    edPath := ConfGui.Add("Edit", "x100 y102 w200", vPath)
    ConfGui.Add("Button", "x310 y101 w50 h24", "浏览").OnEvent("Click", (*) => (
        sel := FileSelect(3, A_WorkingDir, "选择目标程序", "可执行文件 (*.exe; *.bat; *.cmd; *.lnk)"),
        (sel != "") ? edPath.Value := sel : ""
    ))

    ConfGui.Add("Text", "x25 y145 w70", "自定义图标:")
    edIcon := ConfGui.Add("Edit", "x100 y142 w200", vIcon)
    ConfGui.Add("Button", "x310 y141 w50 h24", "浏览").OnEvent("Click", (*) => (
        sel := FileSelect(3, A_WorkingDir, "选择图标文件", "图标资源 (*.ico; *.exe; *.dll)"),
        (sel != "") ? edIcon.Value := sel : ""
    ))
    ConfGui.Add("Text", "x100 y170 w260 c888888", "(留空则自动提取目标程序的图标)")

    ConfGui.Add("Text", "x25 y210 w70", "启动参数:")
    edArgs := ConfGui.Add("Edit", "x100 y207 w260", vArgs)

    ConfGui.Add("Text", "x25 y250 w70", "起始位置:")
    edWorkDir := ConfGui.Add("Edit", "x100 y247 w260", vWorkDir)

    ConfGui.Add("Text", "x25 y300 w70", "运行状态:")
    idx := (vWinState="Max") ? 2 : (vWinState="Min") ? 3 : (vWinState="Hide") ? 4 : 1
    ddlWinState := ConfGui.Add("DropDownList", "x100 y297 w100 Choose" idx, ["Normal", "Max", "Min", "Hide"])

    chkAdmin := ConfGui.Add("Checkbox", "x220 y300 w140 Checked" vRunAdmin, "以管理员身份运行")
    chkWait := ConfGui.Add("Checkbox", "x100 y335 w260 Checked" vWaitExit, "挂起启动器 (等待运行结束后继续)")

    ConfGui.Add("Button", "x160 y375 w90 h32 Default", "保存").OnEvent("Click", SaveConfig)
    ConfGui.Add("Button", "x270 y375 w90 h32", "取消").OnEvent("Click", (*) => (MainGui.Opt("-Disabled"), ConfGui.Destroy()))

    ConfGui.Show("w390 h430")

    SaveConfig(*) {
        if (edName.Value == "" || edPath.Value == "") {
            MsgBox("【显示名称】和【目标程序】为必填项！", "提示", 48)
            return
        }

        sec := (EditSectionID == "") ? "App_" A_Now "_" A_TickCount : EditSectionID

        IniWrite(edName.Value, IniFile, sec, "Name")
        IniWrite(edPath.Value, IniFile, sec, "Path")
        IniWrite(edArgs.Value, IniFile, sec, "Args")
        IniWrite(edWorkDir.Value, IniFile, sec, "WorkDir")
        IniWrite(edIcon.Value, IniFile, sec, "IconPath")
        IniWrite(ddlWinState.Text, IniFile, sec, "WinState")
        IniWrite(chkAdmin.Value, IniFile, sec, "RunAdmin")
        IniWrite(chkWait.Value, IniFile, sec, "WaitExit")

        if (EditSectionID == "") {
            IniWrite("0", IniFile, sec, "RunCount")
            IniWrite("0", IniFile, sec, "LastRun")
        }

        MainGui.Opt("-Disabled")
        ConfGui.Destroy()
        LoadList()
    }
}

; ==========================================
; 设置与关于界面 (左右重构版)
; ==========================================
ShowAboutGui(*) {
    MainGui.Opt("+Disabled")
    AboutDlg := Gui("-MaximizeBox +DPIScale +Owner" MainGui.Hwnd, "设置 & 关于")
    AboutDlg.OnEvent("Close", (*) => (MainGui.Opt("-Disabled"), AboutDlg.Destroy()))
    AboutDlg.BackColor := "FFFFFF"

    ; --- 左侧：关于信息 ---
    AboutDlg.SetFont("s11 Bold", "Microsoft YaHei UI")
    AboutDlg.Add("Text", "x20 y20 w200 c202020", "关于程序")

    AboutDlg.SetFont("s9 Norm")
    AboutDlg.Add("Text", "x20 y55 w190", "通用启动器 (增强版)`n基于 AutoHotkey v2 构建。`n`n支持文件拖放防重、右键菜单、环境变量解析、自定义图标及全局设定功能。")
    AboutDlg.Add("Text", "x20 y160 w190", "by Luminous && Gemini && Copilot")

    AboutDlg.Add("Link", "x20 y190 w190", "技术支持：<a href=`"https://gemini.google.com`">Gemini 官网</a>")
    AboutDlg.Add("Link", "x20 y210 w190", "　　　　　<a href=`"https://copilot.microsoft.com/?cc=us`">Copilot 官网</a>")

    ; --- 中间：分隔线 ---
    AboutDlg.Add("Text", "x220 y20 w1 h240 +BackgroundE0E0E0")

    ; --- 右侧：全局设置 ---
    AboutDlg.SetFont("s11 Bold")
    AboutDlg.Add("Text", "x240 y20 w200 c202020", "全局设置")

    AboutDlg.SetFont("s9 Norm")
    
    chkTray := AboutDlg.Add("Checkbox", "x240 y55 w200 Checked" GlobalCloseToTray, "关闭按钮最小化到托盘")
    
    chkFreq := AboutDlg.Add("Checkbox", "x240 y90 w200 Checked" GlobalFreqSort, "启用频次排序 (最近运行靠前)")
    AboutDlg.Add("Text", "x260 y110 w180 c888888", "(注：开启后将禁用手动排序功能)")

    chkRel := AboutDlg.Add("Checkbox", "x240 y145 w200 Checked" GlobalAutoRelPath, "添加文件时自动使用相对路径")
    
    chkPos := AboutDlg.Add("Checkbox", "x240 y180 w200 Checked" GlobalRememberPos, "记忆窗口关闭时的位置")

    ; 保存按钮居中对齐
    btnSaveAbout := AboutDlg.Add("Button", "x180 y280 w100 h32 Default", "保存并生效")
    btnSaveAbout.OnEvent("Click", SaveGlobalSettings)

    AboutDlg.Show("w460 h330")

    SaveGlobalSettings(*) {
        IniWrite(chkTray.Value, IniFile, "Settings", "CloseToTray")
        IniWrite(chkFreq.Value, IniFile, "Settings", "FreqSort")
        IniWrite(chkRel.Value, IniFile, "Settings", "AutoRelPath")
        IniWrite(chkPos.Value, IniFile, "Settings", "RememberPos")

        ; 更新全局变量
        global GlobalCloseToTray := chkTray.Value
        global GlobalFreqSort    := chkFreq.Value
        global GlobalAutoRelPath := chkRel.Value
        global GlobalRememberPos := chkPos.Value

        MainGui.Opt("-Disabled")
        AboutDlg.Destroy()
        LoadList() ; 重载列表以应用可能的排序变化
    }
}
