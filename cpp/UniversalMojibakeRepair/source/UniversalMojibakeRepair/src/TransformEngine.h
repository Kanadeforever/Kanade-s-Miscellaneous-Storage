#pragma once

// 这一模块负责把“文字的表示形式”来回转换，而不是判断某种语言。
// 它独立于 Win32 窗口，也不依赖 Windows 代码页，因此可以在其他系统上进行自动化测试。
// 例如：中文 ↔ %E4%B8%AD%E6%96%87，或中文 ↔ 228 184 173 230 150 135。
// 注意：后两种都表示 UTF-8 字节，不是“中文属于 ASCII 字符集”。

#include <string>
#include <vector>

enum class TextFormat {
    Percent,
    FormPercent,
    HtmlEntity,
    UnicodeEscape,
    DecimalBytes,
    HexBytes,
    Base64,
    QuotedPrintable,
    UnicodeCodePoint
};

struct TextFormatInfo {
    TextFormat kind;
    std::wstring name;
};

// 返回可以供 Win32 下拉框直接显示的固定格式列表。
std::vector<TextFormatInfo> GetTextFormats();

// encode=true 是从正常文字转换成指定表示形式；false 是恢复成可阅读文字。
// 两个方向都必须严格检测无效序列：出错时不修改输出，错误说明写入 error。
bool ConvertTextFormat(const std::wstring& input, TextFormat format, bool encode,
                       std::wstring& output, std::wstring& error);

// 自动检查明显的 %HH、&#...;、&name;、\uXXXX、\xHH 等结构。
// 返回 true 只表示找到了确实发生变化的合法表示形式，不代表语义判断百分百正确。
// 为避免破坏原文，主窗口将结果作为“自动候选”，用户可以选回其他候选。
bool DetectAndDecodeEscapes(const std::wstring& input, std::wstring& output,
                            std::wstring& description);
