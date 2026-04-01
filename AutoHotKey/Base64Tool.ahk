#Requires AutoHotkey v2.0+ 64-bit
; 以下脚本配置选项
#SingleInstance Force           ; 当脚本已经运行时自动重启脚本。

; ==========================================
; 创建 GUI 界面
; ==========================================
MyGui := Gui(, "Base64 编解码工具")
MyGui.SetFont("s10", "Segoe UI") ; 设置默认字体和大小

MyGui.Add("Text", "x15 y15", "输入:")
EditInput := MyGui.Add("Edit", "x15 y40 w420 h120 vInputText Multi")

; 按钮区域
BtnEncode := MyGui.Add("Button", "x15 y175 w90 h30", "编码 (Encode)")
BtnEncode.OnEvent("Click", EncodeBase64)

BtnDecode := MyGui.Add("Button", "x125 y175 w90 h30", "解码 (Decode)")
BtnDecode.OnEvent("Click", DecodeBase64)

BtnClear := MyGui.Add("Button", "x235 y175 w90 h30", "清空 (Clear)")
BtnClear.OnEvent("Click", ClearText)

BtnCopy := MyGui.Add("Button", "x345 y175 w90 h30", "复制结果")
BtnCopy.OnEvent("Click", CopyResult)

MyGui.Add("Text", "x15 y220", "输出:")
EditOutput := MyGui.Add("Edit", "x15 y245 w420 h120 vOutputText ReadOnly Multi")

MyGui.Show("w450 h385")

; ==========================================
; 核心逻辑函数
; ==========================================

; Base64 编码函数
EncodeBase64(*) {
    str := EditInput.Value
    if (str = "")
        return
    
    ; 获取转换为 UTF-8 后的字节大小（包含结尾的 null 终止符）
    size := StrPut(str, "UTF-8")
    buf := Buffer(size)
    StrPut(str, buf, "UTF-8")
    
    ; 实际需要编码的数据长度（去除 null 终止符，防止将 \0 也编码进去）
    dataSize := size - 1
    
    ; CRYPT_STRING_BASE64 := 0x00000001
    ; CRYPT_STRING_NOCRLF := 0x40000000 (避免输出自带换行符)
    flags := 0x40000001
    chars := 0
    
    ; 第一次调用获取所需的目标缓冲区大小（字符数）
    DllCall("Crypt32.dll\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", dataSize, "UInt", flags, "Ptr", 0, "UIntP", &chars)
    
    ; 分配目标缓冲区 (UTF-16 字符需乘以 2 字节)
    outBuf := Buffer(chars * 2)
    
    ; 第二次调用执行实际编码
    DllCall("Crypt32.dll\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", dataSize, "UInt", flags, "Ptr", outBuf.Ptr, "UIntP", &chars)
    
    ; 将结果写入输出框
    EditOutput.Value := StrGet(outBuf, "UTF-16")
}

; Base64 解码函数
DecodeBase64(*) {
    b64 := EditInput.Value
    if (b64 = "")
        return
    
    ; 移除输入中可能存在的多余空白字符或换行，防止解码报错
    b64 := RegExReplace(b64, "s)[ \t\r\n]+", "")
    
    flags := 0x00000001 ; CRYPT_STRING_BASE64
    bytes := 0
    
    ; 第一次调用获取解码所需的字节大小
    DllCall("Crypt32.dll\CryptStringToBinaryW", "Str", b64, "UInt", 0, "UInt", flags, "Ptr", 0, "UIntP", &bytes, "Ptr", 0, "Ptr", 0)
    
    if (bytes = 0) {
        MsgBox("解码失败！`n请检查输入的内容是否为有效的 Base64 字符串。", "解码错误", "IconX")
        return
    }
    
    ; 分配目标缓冲区（多分配一个字节并初始化为0，确保有 null 终止符截断）
    outBuf := Buffer(bytes + 1, 0)
    
    ; 第二次调用执行实际解码
    DllCall("Crypt32.dll\CryptStringToBinaryW", "Str", b64, "UInt", 0, "UInt", flags, "Ptr", outBuf.Ptr, "UIntP", &bytes, "Ptr", 0, "Ptr", 0)
    
    ; 按 UTF-8 读取解码后的字节为字符串，写入输出框
    EditOutput.Value := StrGet(outBuf, "UTF-8")
}

; 清空输入输出框
ClearText(*) {
    EditInput.Value := ""
    EditOutput.Value := ""
    EditInput.Focus() ; 将焦点重新设置到输入框
}

; 复制结果到剪贴板
CopyResult(*) {
    if (EditOutput.Value != "") {
        A_Clipboard := EditOutput.Value
        ToolTip("✅ 结果已复制到剪贴板！")
        SetTimer(() => ToolTip(), -2000) ; 2秒后自动隐藏提示
    }
}