#Requires AutoHotkey v2.0+
#SingleInstance Force

; =========================
; Paths (ALL RELATIVE)
; =========================
AppDir   := A_ScriptDir
IniPath  := AppDir "\HookSettings.ini"
IniSec   := "Main"
IniKey   := "TargetExe"

; =========================
; Entry (with Shift override)
; =========================

forcePick := GetKeyState("Shift", "P")

if forcePick {
    targetExe := PickAndSaveTargetExe()
    if !targetExe {
        MsgBox "未选择目标 EXE，程序退出。", "HookRunner"
        ExitApp
    }
} else {
    targetExe := LoadTargetExe()
    if !targetExe || !FileExist(AppDir "\" targetExe) {
        targetExe := PickAndSaveTargetExe()
        if !targetExe {
            MsgBox "未选择目标 EXE，程序退出。", "HookRunner"
            ExitApp
        }
    }
}

fullTargetExe := AppDir "\" targetExe

; 确保 .exe.hookshot 存在
EnsureHookshotFile(fullTargetExe)

; 选择 Hookshot 32 / 64
hookshotExe := PickHookshotExe(AppDir)
if !hookshotExe {
    MsgBox "未找到 Hookshot.*.exe（需包含 32 或 64）。", "HookRunner"
    ExitApp
}

; 运行并等待目标 exe 结束
RunHookshotAndWait(hookshotExe, fullTargetExe)
ExitApp


; =========================
; Functions
; =========================

LoadTargetExe() {
    global IniPath, IniSec, IniKey
    if !FileExist(IniPath)
        return ""
    try {
        return Trim(IniRead(IniPath, IniSec, IniKey, ""))
    } catch {
        return ""
    }
}

PickAndSaveTargetExe() {
    global IniPath, IniSec, IniKey, AppDir

    selfExe := A_ScriptFullPath

    loop {
        full := FileSelect(1, AppDir, "选择目标 EXE", "Executable (*.exe)")
        if !full
            return ""

        SplitPath full, &name

        ; 禁止选择 Hookshot.*.exe
        if RegExMatch(name, "i)^Hookshot.*\.exe$") {
            MsgBox "不能选择 Hookshot 程序本身，请选择真正的目标 EXE。", "无效选择", "Icon!"
            continue
        }

        ; 禁止选择启动器自身（脚本态 / 编译态通吃）
        if full = selfExe {
            MsgBox "不能选择本启动器自身，请选择目标 EXE。", "无效选择", "Icon!"
            continue
        }

        ; 合法选择（只保存相对文件名）
        IniWrite(name, IniPath, IniSec, IniKey)
        return name
    }
}

EnsureHookshotFile(fullExe) {
    SplitPath fullExe, &name, &dir
    hook := dir "\" name ".hookshot" ; 必须包含 .exe
    if !FileExist(hook)
        FileAppend("", hook, "UTF-8")
}

PickHookshotExe(dir) {
    list32 := []
    list64 := []

    Loop Files dir "\Hookshot*.exe", "F" {
        fn := A_LoopFileName
        fp := A_LoopFileFullPath
        if InStr(fn, "32")
            list32.Push(fp)
        else if InStr(fn, "64")
            list64.Push(fp)
    }

    if list32.Length && !list64.Length
        return list32[1]
    if list64.Length && !list32.Length
        return list64[1]
    if !list32.Length && !list64.Length
        return ""

    r := MsgBox(
        "检测到 32 和 64 两个 Hookshot 版本：`n`nYes = 32`nNo = 64",
        "选择 Hookshot 版本",
        "YesNo Icon?"
    )
    return (r = "Yes") ? list32[1] : list64[1]
}

RunHookshotAndWait(hookshotExe, targetExe) {
    SplitPath targetExe, &exeName, &exeDir
    Run '"' hookshotExe '" "' targetExe '"', exeDir
    ProcessWait exeName
    ProcessWaitClose exeName
}
