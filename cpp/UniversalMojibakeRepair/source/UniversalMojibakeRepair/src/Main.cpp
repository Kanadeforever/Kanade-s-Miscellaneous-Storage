// Universal Mojibake Repair 窗口与交互逻辑
// ------------------------------------------------------------
// 这是程序的 Win32 GUI 主文件。
// 它负责窗口、按钮、文本框、文件打开/保存和剪贴板；
// 真正的乱码恢复算法放在 EncodingEngine.cpp，二者故意分开，
// 这样以后增加编码或改评分规则时，不必把 GUI 代码一起搅乱。

#include "EncodingEngine.h"
#include "TransformEngine.h"

#include <windows.h>
#include <commdlg.h>
#include <richedit.h>
#include <shellapi.h>

#include "Resource.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace {

// 每个控件都有一个唯一数字 ID。
// 当用户点击按钮或切换下拉框时，Windows 会把这个 ID 通过 WM_COMMAND 发给主窗口。
enum ControlId : int {
    IDC_AUTO_REPAIR = 1001,
    IDC_OPEN_FILE = 1002,
    IDC_SAVE_FILE = 1003,
    IDC_COPY_OUTPUT = 1004,
    IDC_CLEAR_ALL = 1005,
    IDC_WRONG_ENCODING = 1006,
    IDC_TRUE_ENCODING = 1007,
    IDC_MANUAL_REPAIR = 1008,
    IDC_INPUT_EDIT = 1009,
    IDC_OUTPUT_EDIT = 1010,
    IDC_CANDIDATE_COMBO = 1011,
    IDC_STATUS = 1012,
    IDC_FORMAT_COMBO = 1013,
    IDC_FORMAT_DECODE = 1014,
    IDC_FORMAT_ENCODE = 1015,
};

// 全局窗口句柄保存主窗口和主要控件。
// 对这个小工具来说，用简单句柄比建立庞大 UI 类层次更容易维护。
HWND g_mainWindow = nullptr;
HWND g_autoButton = nullptr;
HWND g_openButton = nullptr;
HWND g_saveButton = nullptr;
HWND g_copyButton = nullptr;
HWND g_clearButton = nullptr;
HWND g_wrongCombo = nullptr;
HWND g_trueCombo = nullptr;
HWND g_manualButton = nullptr;
HWND g_wrongEncodingLabel = nullptr;
HWND g_trueEncodingLabel = nullptr;
HWND g_inputLabel = nullptr;
HWND g_inputEdit = nullptr;
HWND g_outputLabel = nullptr;
HWND g_candidateCombo = nullptr;
HWND g_outputEdit = nullptr;
HWND g_statusLabel = nullptr;
HWND g_formatLabel = nullptr;
HWND g_formatCombo = nullptr;
HWND g_formatDecodeButton = nullptr;
HWND g_formatEncodeButton = nullptr;
HFONT g_uiFont = nullptr;
HMODULE g_richEditModule = nullptr;

// 自动分析只保存“恢复步骤”，不永久保存每个候选的整篇输出。
// 用户切换候选时才把该路线应用到全文，可以明显减少长文章的内存占用。
std::vector<RepairCandidate> g_candidates;

// g_currentInput 保存最近一次自动分析时的完整输入。
// 这样候选下拉框切换时，不会因为用户刚好编辑了输入框而套用到错误版本。
std::wstring g_currentInput;

// 格式解码候选与“编码错配逆转码”候选分别存储。
// 格式候选固定放在下拉框第 0 位；其余候选根据偏移映射回原引擎的路线。
bool g_hasEscapeCandidate = false;
std::wstring g_escapeCandidateOutput;
std::wstring g_escapeCandidateDescription;

// DPI 缩放基准。96 DPI 表示 100%。
UINT g_dpi = 96;

// Scale 把“按 96 DPI 设计的像素”转换成当前 DPI 下的实际像素。
int Scale(int value) {
    return MulDiv(value, static_cast<int>(g_dpi), 96);
}

// GetWindowTextDynamic 从任意 Unicode 编辑框读取完整文本。
// 先查询实际长度，再动态创建 std::wstring，完全不使用 260 字固定数组。
std::wstring GetWindowTextDynamic(HWND window) {
    const int length = GetWindowTextLengthW(window);
    if (length <= 0) {
        return std::wstring();
    }

    // 多分配一个 wchar_t 给结尾的 \0；读取完成后再缩回实际字符数。
    std::wstring text(static_cast<std::size_t>(length) + 1, L'\0');
    const int copied = GetWindowTextW(window, text.data(), length + 1);

    if (copied < 0) {
        return std::wstring();
    }

    text.resize(static_cast<std::size_t>(copied));
    return text;
}

// SetStatus 统一修改窗口底部状态文字。
void SetStatus(const std::wstring& message) {
    SetWindowTextW(g_statusLabel, message.c_str());
}

// FormatScoreChange 把评分差值做成“+12.3”或“-4.5”的统一格式。
// 自动排序只是启发式，所以界面叫“评分变化”，不会把它包装成绝对置信度。
std::wstring FormatScoreChange(double value) {
    std::wstringstream stream;
    if (value >= 0.0) {
        stream << L'+';
    }
    stream.setf(std::ios::fixed);
    stream.precision(1);
    stream << value;
    return stream.str();
}

// ShowError 用统一标题显示错误，避免每个功能重复写 MessageBox 参数。
void ShowError(const std::wstring& message) {
    MessageBoxW(g_mainWindow, message.c_str(), L"万能乱码恢复 - 错误", MB_OK | MB_ICONERROR);
}

// GetSelectedCodePage 从编码下拉框取回用户选择的代码页数字。
// 代码页存放在 ComboBox 每一项的 item data 中，而不是从显示文字里硬解析。
unsigned int GetSelectedCodePage(HWND combo) {
    const LRESULT selectedIndex = SendMessageW(combo, CB_GETCURSEL, 0, 0);
    if (selectedIndex == CB_ERR) {
        return 0;
    }

    const LRESULT itemData = SendMessageW(combo, CB_GETITEMDATA, selectedIndex, 0);
    if (itemData == CB_ERR) {
        return 0;
    }

    return static_cast<unsigned int>(itemData);
}

// SelectCodePage 尝试让下拉框选中指定代码页。
// 如果当前系统没有这一代码页，就保持原选择不动。
void SelectCodePage(HWND combo, unsigned int codePage) {
    const LRESULT count = SendMessageW(combo, CB_GETCOUNT, 0, 0);
    for (LRESULT index = 0; index < count; ++index) {
        const LRESULT itemData = SendMessageW(combo, CB_GETITEMDATA, index, 0);
        if (static_cast<unsigned int>(itemData) == codePage) {
            SendMessageW(combo, CB_SETCURSEL, index, 0);
            return;
        }
    }
}

// FillEncodingCombo 把系统实际支持的常见代码页放进下拉框。
void FillEncodingCombo(HWND combo) {
    const std::vector<EncodingInfo> encodings = GetSupportedEncodings();

    for (const auto& encoding : encodings) {
        const LRESULT index = SendMessageW(
            combo,
            CB_ADDSTRING,
            0,
            reinterpret_cast<LPARAM>(encoding.name.c_str()));

        if (index != CB_ERR && index != CB_ERRSPACE) {
            SendMessageW(combo, CB_SETITEMDATA, index, static_cast<LPARAM>(encoding.codePage));
        }
    }
}

// PutTextOnClipboard 把 Unicode 结果放进 Windows 剪贴板。
// 使用 CF_UNICODETEXT，所以中文、日文、俄文等都不会经过系统 ANSI 代码页再损坏一次。
bool PutTextOnClipboard(const std::wstring& text) {
    if (!OpenClipboard(g_mainWindow)) {
        return false;
    }

    EmptyClipboard();

    const std::size_t bytesNeeded = (text.size() + 1) * sizeof(wchar_t);
    HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, bytesNeeded);
    if (memory == nullptr) {
        CloseClipboard();
        return false;
    }

    void* destination = GlobalLock(memory);
    if (destination == nullptr) {
        GlobalFree(memory);
        CloseClipboard();
        return false;
    }

    memcpy(destination, text.c_str(), bytesNeeded);
    GlobalUnlock(memory);

    // SetClipboardData 成功后，内存所有权交给系统，程序不能再 GlobalFree。
    if (SetClipboardData(CF_UNICODETEXT, memory) == nullptr) {
        GlobalFree(memory);
        CloseClipboard();
        return false;
    }

    CloseClipboard();
    return true;
}

// ReadWholeFile 读取整个文本文件为原始字节。
// 没有任何“字数”限制；真正上限来自可用内存和 Windows/GUI 的 32 位文本长度。
bool ReadWholeFile(
    const std::filesystem::path& path,
    std::vector<unsigned char>& bytes,
    std::wstring& errorMessage) {

    errorMessage.clear();

    std::ifstream file(path, std::ios::binary);
    if (!file) {
        errorMessage = L"无法打开所选文件。请检查权限或文件是否仍然存在。";
        return false;
    }

    file.seekg(0, std::ios::end);
    const std::streamoff size = file.tellg();
    if (size < 0) {
        errorMessage = L"无法取得文件大小。";
        return false;
    }

    file.seekg(0, std::ios::beg);

    // RichEdit 和 Windows 文本 API 最终仍使用 32 位长度。
    // 这里不设置人为的小上限，但明确拒绝大于约 2GB 的单个文本文件，避免整数溢出。
    if (static_cast<unsigned long long>(size) > 0x7FFFFFFEULL) {
        errorMessage = L"这个单个文本文件超过约 2GB，已经超出 Windows 文本控件可安全编辑的范围。";
        return false;
    }

    bytes.resize(static_cast<std::size_t>(size));
    if (!bytes.empty()) {
        file.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(size));
        if (!file) {
            errorMessage = L"读取文件时发生错误，文件内容没有完整读入。";
            bytes.clear();
            return false;
        }
    }

    return true;
}

// WriteWholeFile 把字节完整写到磁盘。
bool WriteWholeFile(
    const std::filesystem::path& path,
    const std::vector<unsigned char>& bytes,
    std::wstring& errorMessage) {

    errorMessage.clear();

    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file) {
        errorMessage = L"无法创建或覆盖目标文件。请检查目录写入权限。";
        return false;
    }

    if (!bytes.empty()) {
        file.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
        if (!file) {
            errorMessage = L"写入文件时发生错误，结果可能没有完整保存。";
            return false;
        }
    }

    return true;
}

// RunAutomaticRepair 的完整实现位于本文件后面。
// 这里先声明它，是因为“打开 TXT”成功后要立刻调用自动修复，而不是只把文字塞进输入框。
void RunAutomaticRepair();

// IsTxtPath 只检查扩展名是不是 .txt，比较时忽略大小写。
// 用户已经明确本工具的“文件模式”只支持 TXT，因此 .log/.md/.json 等不再暗中放行。
bool IsTxtPath(const std::filesystem::path& path) {
    const std::wstring extension = path.extension().wstring();

    // CompareStringOrdinal 是 Windows 自带的 Unicode 序数比较。
    // TRUE 表示忽略大小写，所以 .txt、.TXT、.Txt 都视为同一种扩展名。
    return CompareStringOrdinal(
               extension.c_str(),
               -1,
               L".txt",
               -1,
               TRUE) == CSTR_EQUAL;
}

// ValidateSingleTxtPath 在真正读文件以前先做最基本的文件类型检查。
// 这样把文件夹、快捷方式或其他格式拖进来时，用户会得到明确说明，而不是一个含糊的“读取失败”。
bool ValidateSingleTxtPath(
    const std::filesystem::path& path,
    std::wstring& errorMessage) {

    errorMessage.clear();

    if (!IsTxtPath(path)) {
        errorMessage = L"当前版本的文件拖放/打开功能只支持单个 .txt 文本文件。";
        return false;
    }

    const DWORD attributes = GetFileAttributesW(path.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        errorMessage = L"找不到这个 TXT 文件，或者当前用户没有权限访问它。";
        return false;
    }

    if ((attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        errorMessage = L"拖入的是文件夹；当前版本只接受单个 .txt 文件。";
        return false;
    }

    return true;
}

// LoadTxtFileAndAutoRepair 是所有“从文件进入程序”的共同入口。
// 无论用户点击“打开文件”、把 TXT 拖进窗口，还是把 TXT 直接拖到 EXE 图标上，最后都走这里，
// 从而保证三种入口的读取、清理旧候选和自动分析行为完全一致。
void LoadTxtFileAndAutoRepair(const std::filesystem::path& path) {
    std::wstring error;
    if (!ValidateSingleTxtPath(path, error)) {
        ShowError(error);
        return;
    }

    std::vector<unsigned char> bytes;
    if (!ReadWholeFile(path, bytes, error)) {
        ShowError(error);
        return;
    }

    std::wstring text;
    std::wstring detectedEncoding;
    if (!DecodeTextFileBytes(bytes, text, detectedEncoding, error)) {
        ShowError(error);
        return;
    }

    // 新文件必须完全替换上一轮状态。
    // 如果不清空 g_candidates/g_currentInput，用户后来切换候选时可能把旧路线错误地套到新文件上。
    SetWindowTextW(g_inputEdit, text.c_str());
    SetWindowTextW(g_outputEdit, L"");
    SendMessageW(g_candidateCombo, CB_RESETCONTENT, 0, 0);
    g_candidates.clear();
    g_currentInput.clear();
    g_hasEscapeCandidate = false;
    g_escapeCandidateOutput.clear();
    g_escapeCandidateDescription.clear();

    // 先把读取结果显示出来并刷新一次窗口，让大文件加载时用户能看到程序已经接收到了文件。
    SetStatus(L"已读取 TXT；文件编码：" + detectedEncoding + L"。正在自动分析乱码……");
    UpdateWindow(g_mainWindow);

    // 所有文件入口遵循同一交互规则：打开后自动分析并展示候选。
    // 如果算法置信度不足，RunAutomaticRepair 自己会保留原文并列出候选，不会擅自覆盖成低置信度结果。
    RunAutomaticRepair();
}

// OpenTextFile 弹出系统文件选择框。
// 文件模式严格只列出 TXT；选中后立即读取并自动分析，不再要求用户额外点击一次“自动分析并恢复”。
void OpenTextFile() {
    wchar_t fileName[32768] = {};

    OPENFILENAMEW dialog = {};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = g_mainWindow;
    dialog.lpstrFilter = L"TXT 文本文件\0*.txt\0\0";
    dialog.lpstrFile = fileName;
    dialog.nMaxFile = static_cast<DWORD>(sizeof(fileName) / sizeof(fileName[0]));
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_EXPLORER;
    dialog.lpstrTitle = L"打开并自动修复 TXT 文本";

    if (!GetOpenFileNameW(&dialog)) {
        return;
    }

    LoadTxtFileAndAutoRepair(std::filesystem::path(fileName));
}

// HandleDroppedTxt 接收用户拖到主窗口里的文件。
// Windows 的 HDROP 可能包含多个文件，所以这里先数数量；本版本按用户要求只接受一个 TXT。
void HandleDroppedTxt(HDROP dropHandle) {
    const UINT fileCount = DragQueryFileW(dropHandle, 0xFFFFFFFFu, nullptr, 0);

    if (fileCount != 1) {
        // DragFinish 必须调用，否则系统为本次拖放保存的内部资源不能及时释放。
        DragFinish(dropHandle);
        ShowError(L"一次只支持拖入一个 .txt 文件。请不要同时拖入多个文件。");
        return;
    }

    // 第一次调用只查询路径需要多少个 wchar_t；再按实际长度动态分配。
    // 这样不会重新引入 MAX_PATH/260 字符固定缓冲区问题。
    const UINT pathLength = DragQueryFileW(dropHandle, 0, nullptr, 0);
    std::wstring pathText(static_cast<std::size_t>(pathLength) + 1, L'\0');

    const UINT copied = DragQueryFileW(
        dropHandle,
        0,
        pathText.data(),
        static_cast<UINT>(pathText.size()));

    DragFinish(dropHandle);

    if (copied == 0) {
        ShowError(L"Windows 没有返回拖入文件的完整路径。请重新拖入一次。");
        return;
    }

    pathText.resize(static_cast<std::size_t>(copied));
    LoadTxtFileAndAutoRepair(std::filesystem::path(pathText));
}

// HandleStartupArguments 处理“把 TXT 文件直接拖到 EXE 或快捷方式图标上”的情况。
// Explorer 会启动本程序，并把被拖入文件的路径作为命令行参数传进来。
void HandleStartupArguments() {
    int argumentCount = 0;
    LPWSTR* arguments = CommandLineToArgvW(GetCommandLineW(), &argumentCount);

    if (arguments == nullptr) {
        // 命令行解析失败不影响普通启动，所以只保持空白界面，不把它升级成致命错误。
        return;
    }

    if (argumentCount == 2) {
        // arguments[0] 是 EXE 自己；arguments[1] 才是拖到图标上的 TXT 路径。
        LoadTxtFileAndAutoRepair(std::filesystem::path(arguments[1]));
    } else if (argumentCount > 2) {
        // 即使 Explorer 传进来多个文件，也严格遵守“一次一个 TXT”的规格，不偷偷只取第一个。
        ShowError(L"一次只支持通过 EXE/快捷方式打开一个 .txt 文件。");
    }

    LocalFree(arguments);
}

// SaveOutputFile 把输出区统一保存成 UTF-8。
void SaveOutputFile() {
    const std::wstring output = GetWindowTextDynamic(g_outputEdit);
    if (output.empty()) {
        ShowError(L"输出区为空，没有可以保存的结果。" );
        return;
    }

    wchar_t fileName[32768] = L"恢复结果.txt";

    OPENFILENAMEW dialog = {};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = g_mainWindow;
    dialog.lpstrFilter = L"UTF-8 文本文件\0*.txt\0所有文件\0*.*\0\0";
    dialog.lpstrFile = fileName;
    dialog.nMaxFile = static_cast<DWORD>(sizeof(fileName) / sizeof(fileName[0]));
    dialog.lpstrDefExt = L"txt";
    dialog.Flags = OFN_PATHMUSTEXIST | OFN_OVERWRITEPROMPT | OFN_EXPLORER;
    dialog.lpstrTitle = L"把恢复结果保存为 UTF-8";

    if (!GetSaveFileNameW(&dialog)) {
        return;
    }

    std::vector<unsigned char> bytes;
    std::wstring error;
    if (!EncodeUtf8(output, bytes, error)) {
        ShowError(error);
        return;
    }

    if (!WriteWholeFile(std::filesystem::path(fileName), bytes, error)) {
        ShowError(error);
        return;
    }

    SetStatus(L"结果已保存为 UTF-8：" + std::wstring(fileName));
}

// ApplyCandidateByIndex 在用户选择某个自动候选时，把那条路线真正应用到全文。
void ApplyCandidateByIndex(std::size_t index) {
    // 百分号、HTML 实体、Unicode 转义是独立的格式候选，不走代码页修复路线。
    if (g_hasEscapeCandidate && index == 0) {
        SetWindowTextW(g_outputEdit, g_escapeCandidateOutput.c_str());
        SetStatus(L"已预览自动格式解码：" + g_escapeCandidateDescription +
                  L"。可切换其他候选；保存前请核对结果。");
        return;
    }
    if (g_hasEscapeCandidate) --index;
    if (index >= g_candidates.size() || g_currentInput.empty()) {
        return;
    }

    std::wstring repaired;
    std::wstring error;
    if (!ApplyRepairSteps(g_currentInput, g_candidates[index].steps, repaired, error)) {
        ShowError(L"这个候选在应用到全文时失败：\n\n" + error);
        return;
    }

    SetWindowTextW(g_outputEdit, repaired.c_str());

    std::wstringstream status;
    status << L"候选 " << (index + 1 + (g_hasEscapeCandidate ? 1 : 0)) << L"："
           << DescribeCandidate(g_candidates[index])
           << L"；评分变化 " << FormatScoreChange(g_candidates[index].improvement);
    SetStatus(status.str());
}

// RunAutomaticRepair 执行自动候选搜索。
void RunAutomaticRepair() {
    const std::wstring input = GetWindowTextDynamic(g_inputEdit);
    if (input.empty()) {
        ShowError(L"请先在上方输入/粘贴乱码文字，或者打开一个文本文件。" );
        return;
    }

    SetStatus(L"正在分析常见可逆乱码组合……长文章只抽样分析，选中候选后才处理全文。" );
    UpdateWindow(g_mainWindow);

    g_currentInput = input;
    // 显式转义格式与代码页恢复分别寻找候选，避免格式已明确却被语言评分误判。
    // 例如文件名里的 %E6%8C%89 并不是 CP1252 的乱码，而是 UTF-8 的百分号编码。
    g_hasEscapeCandidate = DetectAndDecodeEscapes(input, g_escapeCandidateOutput,
                                                    g_escapeCandidateDescription);
    // RichEdit 不能按字节长度处理内嵌 NUL；遇到这种解码候选就不自动展示。
    if (g_hasEscapeCandidate && g_escapeCandidateOutput.find(L'\0') != std::wstring::npos) {
        g_hasEscapeCandidate = false;
        g_escapeCandidateOutput.clear();
        g_escapeCandidateDescription.clear();
    }
    g_candidates = FindRepairCandidates(input, 12);

    SendMessageW(g_candidateCombo, CB_RESETCONTENT, 0, 0);

    if (g_hasEscapeCandidate) {
        const std::wstring description = L"0. 格式解码：" + g_escapeCandidateDescription;
        SendMessageW(g_candidateCombo, CB_ADDSTRING, 0,
                     reinterpret_cast<LPARAM>(description.c_str()));
    }

    if (g_candidates.empty() && !g_hasEscapeCandidate) {
        SetWindowTextW(g_outputEdit, input.c_str());
        SetStatus(L"未发现可信自动候选。可手动选择代码页恢复，或在“表示形式”中选择格式转换。" );
        return;
    }

    for (std::size_t index = 0; index < g_candidates.size(); ++index) {
        std::wstringstream item;
        item << (index + 1 + (g_hasEscapeCandidate ? 1 : 0)) << L". " << DescribeCandidate(g_candidates[index])
             << L"  [评分变化 " << FormatScoreChange(g_candidates[index].improvement) << L"]";

        const std::wstring itemText = item.str();
        SendMessageW(
            g_candidateCombo,
            CB_ADDSTRING,
            0,
            reinterpret_cast<LPARAM>(itemText.c_str()));
    }

    // 明确符合 %XX、&#...;、\uXXXX 等转义语法时，优先预览格式解码结果。
    // 输入框始终保留原文，输出框只是候选结果，不修改源 TXT 文件。
    if (g_hasEscapeCandidate) {
        SendMessageW(g_candidateCombo, CB_SETCURSEL, 0, 0);
        ApplyCandidateByIndex(0);
        return;
    }

    // 如果第一名相对原文有明显改善，就直接预览第一名。
    // 如果改善很小或为负，说明字符层评分无法可靠判断（东亚编码互错时很常见），
    // 此时不擅自改全文，只把候选列出来让用户选择。
    if (g_candidates.front().improvement > 0.5) {
        SendMessageW(g_candidateCombo, CB_SETCURSEL, 0, 0);
        ApplyCandidateByIndex(0);
    } else {
        SendMessageW(g_candidateCombo, CB_SETCURSEL, static_cast<WPARAM>(-1), 0);
        SetWindowTextW(g_outputEdit, input.c_str());
        SetStatus(L"找到了可逆候选，但自动评分无法明显区分。请在“恢复结果 / 候选”下拉框逐项查看，或使用手动模式。" );
    }
}

// RunManualRepair 完全按用户指定的两个代码页做一次逆转码。
void RunManualRepair() {
    const std::wstring input = GetWindowTextDynamic(g_inputEdit);
    if (input.empty()) {
        ShowError(L"请先输入需要恢复的乱码文字。" );
        return;
    }

    const unsigned int wrongCodePage = GetSelectedCodePage(g_wrongCombo);
    const unsigned int trueCodePage = GetSelectedCodePage(g_trueCombo);

    if (wrongCodePage == 0 || trueCodePage == 0) {
        ShowError(L"请选择两端编码。" );
        return;
    }

    std::wstring repaired;
    std::wstring error;
    if (!RepairOnce(input, wrongCodePage, trueCodePage, repaired, error)) {
        ShowError(error);
        return;
    }

    SetWindowTextW(g_outputEdit, repaired.c_str());

    RepairCandidate manualCandidate;
    manualCandidate.steps.push_back({wrongCodePage, trueCodePage});
    SetStatus(L"已按手动组合恢复：" + DescribeCandidate(manualCandidate));
}

// RunFormatConversion 执行明确选定的表示形式转换。
// “编码”把原文变成 %XX / 数字字节等格式；“解码”反过来还原文字。
// 解码与编码都使用输入框内容，输出只写到结果框，防止覆盖未经核对的原文。
void RunFormatConversion(bool encode) {
    const std::wstring input = GetWindowTextDynamic(g_inputEdit);
    if (input.empty()) { ShowError(L"请先输入需要转换的文字。"); return; }
    const LRESULT selection = SendMessageW(g_formatCombo, CB_GETCURSEL, 0, 0);
    if (selection == CB_ERR) { ShowError(L"请先选择转换格式。"); return; }
    const auto format = static_cast<TextFormat>(SendMessageW(g_formatCombo, CB_GETITEMDATA, selection, 0));
    std::wstring output, error;
    if (!ConvertTextFormat(input, format, encode, output, error)) {
        ShowError(error);
        return;
    }
    // RichEdit 使用 NUL 作为字符串结尾；不能把携带内嵌 NUL 的结果截断后称为成功。
    if (output.find(L'\0') != std::wstring::npos) {
        ShowError(L"转换结果包含 NUL 控制字符，无法安全显示在编辑框中。");
        return;
    }
    SetWindowTextW(g_outputEdit, output.c_str());
    SetStatus(encode ? L"已将输入文字转换为所选表示形式（UTF-8 字节模式支持中文）。" :
                       L"已按所选格式严格解码；请核对结果后再保存。");
}

// CopyOutput 把输出区复制到剪贴板。
void CopyOutput() {
    const std::wstring output = GetWindowTextDynamic(g_outputEdit);
    if (output.empty()) {
        ShowError(L"输出区为空，没有可以复制的文字。" );
        return;
    }

    if (!PutTextOnClipboard(output)) {
        ShowError(L"无法访问 Windows 剪贴板。可能有其他程序暂时占用了剪贴板。" );
        return;
    }

    SetStatus(L"恢复结果已复制到剪贴板。" );
}

// ClearAll 清空当前输入、输出和候选状态。
void ClearAll() {
    SetWindowTextW(g_inputEdit, L"");
    SetWindowTextW(g_outputEdit, L"");
    SendMessageW(g_candidateCombo, CB_RESETCONTENT, 0, 0);
    g_candidates.clear();
    g_currentInput.clear();
    g_hasEscapeCandidate = false;
    g_escapeCandidateOutput.clear();
    g_escapeCandidateDescription.clear();
    SetStatus(L"已清空。" );
    SetFocus(g_inputEdit);
}

// ApplyFontToControl 把统一 UI 字体发给一个控件。
void ApplyFontToControl(HWND control) {
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(g_uiFont), TRUE);
}

// CreateUiFont 创建随当前 DPI 缩放的“Segoe UI”界面字体。
void CreateUiFont() {
    if (g_uiFont != nullptr) {
        DeleteObject(g_uiFont);
        g_uiFont = nullptr;
    }

    // CreateFontW 的负高度表示“按字符高度”创建字体。
    // 10pt 在 96 DPI 下约 13 像素；用点数换算比直接 -10 像素更符合 Windows 字体习惯。
    g_uiFont = CreateFontW(
        -MulDiv(10, static_cast<int>(g_dpi), 72),
        0,
        0,
        0,
        FW_NORMAL,
        FALSE,
        FALSE,
        FALSE,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE,
        L"Segoe UI");
}

// LayoutControls 根据窗口当前大小重新摆放所有控件。
// 所以窗口可以自由放大来查看长文章，而不是像旧工具一样固定小对话框。
void LayoutControls(int clientWidth, int clientHeight) {
    const int margin = Scale(12);
    const int gap = Scale(8);
    const int buttonHeight = Scale(30);
    const int comboHeight = Scale(300); // ComboBox 的高度包含下拉列表高度，所以故意给较大值。
    const int rowHeight = Scale(30);
    const int labelHeight = Scale(22);
    const int statusHeight = Scale(24);

    int x = margin;
    int y = margin;

    const int autoWidth = Scale(142);
    const int smallButtonWidth = Scale(92);

    MoveWindow(g_autoButton, x, y, autoWidth, buttonHeight, TRUE);
    x += autoWidth + gap;
    MoveWindow(g_openButton, x, y, smallButtonWidth, buttonHeight, TRUE);
    x += smallButtonWidth + gap;
    MoveWindow(g_saveButton, x, y, smallButtonWidth, buttonHeight, TRUE);
    x += smallButtonWidth + gap;
    MoveWindow(g_copyButton, x, y, smallButtonWidth, buttonHeight, TRUE);
    x += smallButtonWidth + gap;
    MoveWindow(g_clearButton, x, y, smallButtonWidth, buttonHeight, TRUE);

    y += buttonHeight + gap;

    // 第二行：手动转换的两个代码页。
    // 两端编码说明使用独立静态标签，避免固定宽度组合标签在高 DPI 下截断。
    // 每个标签紧贴自己的下拉框，使初次使用的人也能看懂恢复方向。
    x = margin;
    const int encodingLabelWidth = Scale(126);
    const int comboWidth = Scale(168);
    const int manualButtonWidth = Scale(110);

    MoveWindow(g_wrongEncodingLabel, x, y, encodingLabelWidth, rowHeight, TRUE);
    x += encodingLabelWidth;
    MoveWindow(g_wrongCombo, x, y, comboWidth, comboHeight, TRUE);
    x += comboWidth + gap;

    MoveWindow(g_trueEncodingLabel, x, y, encodingLabelWidth, rowHeight, TRUE);
    x += encodingLabelWidth;
    MoveWindow(g_trueCombo, x, y, comboWidth, comboHeight, TRUE);
    x += comboWidth + gap;

    MoveWindow(g_manualButton, x, y, manualButtonWidth, buttonHeight, TRUE);

    y += rowHeight + gap;

    // 第三行是独立的通用格式转换器，按窗口宽度伸缩下拉框。
    // ASCII 只覆盖 0~127；超过范围的文字使用 UTF-8 字节表示，并在菜单注明。
    x = margin;
    const int formatLabelWidth = Scale(92);
    const int formatActionWidth = Scale(106);
    MoveWindow(g_formatLabel, x, y, formatLabelWidth, rowHeight, TRUE);
    x += formatLabelWidth;
    const int formatComboWidth = std::max(Scale(175), clientWidth - margin * 2 -
        formatLabelWidth - (formatActionWidth + gap) * 2);
    MoveWindow(g_formatCombo, x, y, formatComboWidth, comboHeight, TRUE);
    x += formatComboWidth + gap;
    MoveWindow(g_formatDecodeButton, x, y, formatActionWidth, buttonHeight, TRUE);
    x += formatActionWidth + gap;
    MoveWindow(g_formatEncodeButton, x, y, formatActionWidth, buttonHeight, TRUE);
    y += rowHeight + gap;

    const int bottomY = clientHeight - margin - statusHeight;
    const int textAreaBottom = bottomY - gap;
    const int availableTextHeight = std::max(Scale(180), textAreaBottom - y);
    const int halfTextHeight = (availableTextHeight - gap - labelHeight * 2 - rowHeight) / 2;

    // “输入乱码文字”标签使用自己独立的静态控件。
    MoveWindow(g_inputLabel, margin, y, Scale(300), labelHeight, TRUE);
    y += labelHeight;

    MoveWindow(
        g_inputEdit,
        margin,
        y,
        std::max(Scale(200), clientWidth - margin * 2),
        std::max(Scale(80), halfTextHeight),
        TRUE);
    y += std::max(Scale(80), halfTextHeight) + gap;

    // 输出标题和候选下拉框放同一行。
    MoveWindow(g_outputLabel, margin, y + Scale(5), Scale(160), labelHeight, TRUE);
    MoveWindow(
        g_candidateCombo,
        margin + Scale(160),
        y,
        std::max(Scale(220), clientWidth - margin * 2 - Scale(160)),
        comboHeight,
        TRUE);
    y += rowHeight;

    MoveWindow(
        g_outputEdit,
        margin,
        y,
        std::max(Scale(200), clientWidth - margin * 2),
        std::max(Scale(80), textAreaBottom - y),
        TRUE);

    MoveWindow(
        g_statusLabel,
        margin,
        bottomY,
        std::max(Scale(200), clientWidth - margin * 2),
        statusHeight,
        TRUE);
}

// CenterWindowOnCursorMonitor 把窗口放在鼠标所在显示器工作区中央。
// 多显示器用户通常在准备操作的那块屏幕上有鼠标，因此这比永远居中主显示器更符合直觉。
void CenterWindowOnCursorMonitor(HWND window) {
    RECT rectangle = {};
    GetWindowRect(window, &rectangle);

    POINT cursor = {};
    GetCursorPos(&cursor);

    HMONITOR monitor = MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST);
    MONITORINFO monitorInfo = {};
    monitorInfo.cbSize = sizeof(monitorInfo);
    GetMonitorInfoW(monitor, &monitorInfo);

    const int width = rectangle.right - rectangle.left;
    const int height = rectangle.bottom - rectangle.top;
    const RECT& work = monitorInfo.rcWork;

    const int x = work.left + ((work.right - work.left) - width) / 2;
    const int y = work.top + ((work.bottom - work.top) - height) / 2;

    SetWindowPos(window, nullptr, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE);
}

// CreateControls 创建所有子控件。
void CreateControls(HWND window) {
    const DWORD buttonStyle = WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON;
    const DWORD comboStyle = WS_CHILD | WS_VISIBLE | WS_TABSTOP | CBS_DROPDOWNLIST | WS_VSCROLL;
    const DWORD editStyle = WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER |
                            ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_VSCROLL;

    g_autoButton = CreateWindowExW(0, L"BUTTON", L"自动分析并恢复", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_AUTO_REPAIR)), nullptr, nullptr);
    g_openButton = CreateWindowExW(0, L"BUTTON", L"打开 TXT", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_OPEN_FILE)), nullptr, nullptr);
    g_saveButton = CreateWindowExW(0, L"BUTTON", L"保存结果", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_SAVE_FILE)), nullptr, nullptr);
    g_copyButton = CreateWindowExW(0, L"BUTTON", L"复制结果", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_COPY_OUTPUT)), nullptr, nullptr);
    g_clearButton = CreateWindowExW(0, L"BUTTON", L"清空", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_CLEAR_ALL)), nullptr, nullptr);

    // 两个编码标签分别说明“现在这串乱码当初被错误按什么编码解释”以及“原字节真实是什么编码”。
    // SS_CENTERIMAGE 只负责在固定行高里垂直居中文字；真正的背景由 WM_CTLCOLORSTATIC 统一改成窗口背景，
    // 因此不会再出现传统 Win32 STATIC 控件那种突兀灰底。
    const DWORD staticLabelStyle = WS_CHILD | WS_VISIBLE | SS_LEFT | SS_CENTERIMAGE;

    g_wrongEncodingLabel = CreateWindowExW(0, L"STATIC", L"乱码当前被当作：", staticLabelStyle,
        0, 0, 0, 0, window, nullptr, nullptr, nullptr);

    g_trueEncodingLabel = CreateWindowExW(0, L"STATIC", L"原字节实际编码：", staticLabelStyle,
        0, 0, 0, 0, window, nullptr, nullptr, nullptr);

    g_inputLabel = CreateWindowExW(0, L"STATIC", L"输入乱码文字（无固定字数上限）", staticLabelStyle,
        0, 0, 0, 0, window, nullptr, nullptr, nullptr);

    g_wrongCombo = CreateWindowExW(0, L"COMBOBOX", L"", comboStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_WRONG_ENCODING)), nullptr, nullptr);
    g_trueCombo = CreateWindowExW(0, L"COMBOBOX", L"", comboStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_TRUE_ENCODING)), nullptr, nullptr);
    g_manualButton = CreateWindowExW(0, L"BUTTON", L"手动恢复", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_MANUAL_REPAIR)), nullptr, nullptr);

    // 新增独立格式行：不限制转换方向，只需从菜单中选定格式，再点解码或编码。
    g_formatLabel = CreateWindowExW(0, L"STATIC", L"表示形式：", staticLabelStyle,
        0, 0, 0, 0, window, nullptr, nullptr, nullptr);
    g_formatCombo = CreateWindowExW(0, L"COMBOBOX", L"", comboStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_FORMAT_COMBO)), nullptr, nullptr);
    g_formatDecodeButton = CreateWindowExW(0, L"BUTTON", L"格式解码", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_FORMAT_DECODE)), nullptr, nullptr);
    g_formatEncodeButton = CreateWindowExW(0, L"BUTTON", L"格式编码", buttonStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_FORMAT_ENCODE)), nullptr, nullptr);

    // RichEdit 5.0 比最老的 EDIT 控件更适合大段 Unicode 文本。
    // ES_NOHIDESEL 让用户切换窗口后仍能看见选择范围。
    g_inputEdit = CreateWindowExW(
        WS_EX_CLIENTEDGE,
        MSFTEDIT_CLASS,
        L"",
        editStyle | ES_NOHIDESEL,
        0, 0, 0, 0,
        window,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_INPUT_EDIT)),
        nullptr,
        nullptr);

    g_outputLabel = CreateWindowExW(0, L"STATIC", L"恢复结果 / 候选：", staticLabelStyle,
        0, 0, 0, 0, window, nullptr, nullptr, nullptr);

    g_candidateCombo = CreateWindowExW(0, L"COMBOBOX", L"", comboStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_CANDIDATE_COMBO)), nullptr, nullptr);

    g_outputEdit = CreateWindowExW(
        WS_EX_CLIENTEDGE,
        MSFTEDIT_CLASS,
        L"",
        editStyle | ES_NOHIDESEL,
        0, 0, 0, 0,
        window,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_OUTPUT_EDIT)),
        nullptr,
        nullptr);

    g_statusLabel = CreateWindowExW(0, L"STATIC",
        L"粘贴乱码后可自动分析；也可拖入一个 TXT，或把 TXT 拖到 EXE 上自动修复。",
        staticLabelStyle,
        0, 0, 0, 0, window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(IDC_STATUS)), nullptr, nullptr);

    // 把 RichEdit 自身的文本长度上限提高到接近 32 位接口能表达的最大值。
    // 这就是“无程序人为字数上限”的关键设置之一。
    SendMessageW(g_inputEdit, EM_EXLIMITTEXT, 0, 0x7FFFFFFE);
    SendMessageW(g_outputEdit, EM_EXLIMITTEXT, 0, 0x7FFFFFFE);

    // 给文本左右留一点内边距，长文章阅读起来不会紧贴边框。
    SendMessageW(g_inputEdit, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(Scale(8), Scale(8)));
    SendMessageW(g_outputEdit, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, MAKELPARAM(Scale(8), Scale(8)));

    CreateUiFont();

    HWND controls[] = {
        g_autoButton, g_openButton, g_saveButton, g_copyButton, g_clearButton,
        g_wrongEncodingLabel, g_trueEncodingLabel, g_inputLabel, g_wrongCombo, g_trueCombo, g_manualButton,
        g_formatLabel, g_formatCombo, g_formatDecodeButton, g_formatEncodeButton,
        g_inputEdit, g_outputLabel, g_candidateCombo, g_outputEdit, g_statusLabel
    };

    for (HWND control : controls) {
        ApplyFontToControl(control);
    }

    FillEncodingCombo(g_wrongCombo);
    FillEncodingCombo(g_trueCombo);
    // 编码格式不用 Windows 代码页；按自定义枚举值保存到 item data。
    for (const auto& format : GetTextFormats()) {
        const LRESULT item = SendMessageW(g_formatCombo, CB_ADDSTRING, 0,
            reinterpret_cast<LPARAM>(format.name.c_str()));
        if (item != CB_ERR && item != CB_ERRSPACE)
            SendMessageW(g_formatCombo, CB_SETITEMDATA, item,
                static_cast<LPARAM>(format.kind));
    }
    SendMessageW(g_formatCombo, CB_SETCURSEL, 0, 0);

    // 默认手动组合直接对应原程序已确认的一条典型恢复链：CP1252 → UTF-8。
    SelectCodePage(g_wrongCombo, 1252);
    SelectCodePage(g_trueCombo, 65001);

    // 告诉 Shell：这个主窗口愿意接收 Explorer 的文件拖放。
    // 后续 WM_DROPFILES 仍会再次验证“只能一个文件、而且必须是 .txt”，所以这里开启拖放并不会放宽格式限制。
    DragAcceptFiles(window, TRUE);
}

// WindowProcedure 是 Win32 主窗口的消息入口。
// Windows 把“窗口创建、大小变化、按钮点击、关闭”等事件都送到这里。
LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case WM_CREATE: {
        g_mainWindow = window;

        // 优先使用 Win10+ 的 GetDpiForWindow；为了兼容旧系统，通过 GetProcAddress 动态查找，
        // 避免程序因为旧 Windows 没有这个导出而直接无法启动。
        using GetDpiForWindowFunction = UINT(WINAPI*)(HWND);
        HMODULE user32 = GetModuleHandleW(L"user32.dll");
        auto getDpiForWindow = reinterpret_cast<GetDpiForWindowFunction>(
            GetProcAddress(user32, "GetDpiForWindow"));

        if (getDpiForWindow != nullptr) {
            g_dpi = getDpiForWindow(window);
        } else {
            HDC screen = GetDC(nullptr);
            if (screen != nullptr) {
                g_dpi = static_cast<UINT>(GetDeviceCaps(screen, LOGPIXELSX));
                ReleaseDC(nullptr, screen);
            }
        }

        CreateControls(window);
        return 0;
    }

    case WM_SIZE: {
        const int width = LOWORD(lParam);
        const int height = HIWORD(lParam);
        LayoutControls(width, height);
        return 0;
    }

    case WM_DROPFILES:
        // Explorer 把一个或多个文件拖进窗口时会发送 WM_DROPFILES。
        // 真正的数量/扩展名检查集中在 HandleDroppedTxt，避免这里复制一套规则。
        HandleDroppedTxt(reinterpret_cast<HDROP>(wParam));
        return 0;

    case WM_CTLCOLORSTATIC: {
        // Win32 STATIC 默认常用 COLOR_3DFACE（灰色）做文字背景。
        // 主窗口本身是 COLOR_WINDOW（通常为白色），两者混在一起就会形成用户看到的“字后面一块灰底”。
        // 把静态文字绘制模式改成透明，并返回与主窗口相同的系统画刷，文字就自然融入背景。
        HDC deviceContext = reinterpret_cast<HDC>(wParam);
        SetBkMode(deviceContext, TRANSPARENT);
        SetTextColor(deviceContext, GetSysColor(COLOR_WINDOWTEXT));
        return reinterpret_cast<LRESULT>(GetSysColorBrush(COLOR_WINDOW));
    }

    case WM_COMMAND: {
        const int controlId = LOWORD(wParam);
        const int notification = HIWORD(wParam);

        if (controlId == IDC_AUTO_REPAIR && notification == BN_CLICKED) {
            RunAutomaticRepair();
            return 0;
        }
        if (controlId == IDC_OPEN_FILE && notification == BN_CLICKED) {
            OpenTextFile();
            return 0;
        }
        if (controlId == IDC_SAVE_FILE && notification == BN_CLICKED) {
            SaveOutputFile();
            return 0;
        }
        if (controlId == IDC_COPY_OUTPUT && notification == BN_CLICKED) {
            CopyOutput();
            return 0;
        }
        if (controlId == IDC_CLEAR_ALL && notification == BN_CLICKED) {
            ClearAll();
            return 0;
        }
        if (controlId == IDC_MANUAL_REPAIR && notification == BN_CLICKED) {
            RunManualRepair();
            return 0;
        }
        if (controlId == IDC_FORMAT_DECODE && notification == BN_CLICKED) {
            RunFormatConversion(false);
            return 0;
        }
        if (controlId == IDC_FORMAT_ENCODE && notification == BN_CLICKED) {
            RunFormatConversion(true);
            return 0;
        }
        if (controlId == IDC_CANDIDATE_COMBO && notification == CBN_SELCHANGE) {
            const LRESULT selected = SendMessageW(g_candidateCombo, CB_GETCURSEL, 0, 0);
            if (selected != CB_ERR) {
                ApplyCandidateByIndex(static_cast<std::size_t>(selected));
            }
            return 0;
        }
        break;
    }

    case WM_GETMINMAXINFO: {
        // 防止窗口被缩得太小，导致两块大文本编辑区完全无法使用。
        auto* info = reinterpret_cast<MINMAXINFO*>(lParam);
        info->ptMinTrackSize.x = Scale(760);
        info->ptMinTrackSize.y = Scale(620);
        return 0;
    }

    case WM_DESTROY:
        if (g_uiFont != nullptr) {
            DeleteObject(g_uiFont);
            g_uiFont = nullptr;
        }
        PostQuitMessage(0);
        return 0;
    }

    return DefWindowProcW(window, message, wParam, lParam);
}

} // namespace

// wWinMain 是 Unicode GUI 程序入口。
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int showCommand) {
    // 告诉旧版 Windows：程序会自己按系统 DPI 缩放控件，避免系统把整个窗口做模糊位图拉伸。
    // SetProcessDPIAware 从 Windows Vista 就存在，作为兼容性很好的基础方案。
    SetProcessDPIAware();

    // 在创建主窗口之前先取得系统 DPI，用它缩放“初始窗口尺寸”。
    // 如果只缩放控件而不缩放窗口，在 200% DPI 下 1100×780 物理像素会显得过小，
    // 第一帧就可能塞不下已经放大的控件。WM_CREATE 后还会用窗口实际 DPI 再校正一次。
    HDC startupScreen = GetDC(nullptr);
    if (startupScreen != nullptr) {
        g_dpi = static_cast<UINT>(GetDeviceCaps(startupScreen, LOGPIXELSX));
        ReleaseDC(nullptr, startupScreen);
    }

    // RichEdit 5.0 控件在 Msftedit.dll 中。
    // 如果这个系统 DLL 都无法加载，主文本框无法创建，因此直接显示明确错误并退出。
    g_richEditModule = LoadLibraryW(L"Msftedit.dll");
    if (g_richEditModule == nullptr) {
        MessageBoxW(nullptr,
            L"无法加载系统组件 Msftedit.dll，程序无法创建大文本编辑框。",
            L"万能乱码恢复 - 启动失败",
            MB_OK | MB_ICONERROR);
        return 1;
    }

    const wchar_t kWindowClassName[] = L"UniversalMojibakeRepairWindow";

    WNDCLASSEXW windowClass = {};
    windowClass.cbSize = sizeof(windowClass);
    windowClass.style = CS_HREDRAW | CS_VREDRAW;
    windowClass.lpfnWndProc = WindowProcedure;
    windowClass.hInstance = instance;
    windowClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);

    // 使用工程自身的多尺寸 ICO，而不是 Windows 默认“白纸应用程序”图标。
    // 同一个 ICO 内含 16~256 像素多个尺寸；大图标与标题栏小图标分别让系统选择合适尺寸。
    windowClass.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(IDI_APP_ICON));
    windowClass.hIconSm = static_cast<HICON>(LoadImageW(
        instance,
        MAKEINTRESOURCEW(IDI_APP_ICON),
        IMAGE_ICON,
        GetSystemMetrics(SM_CXSMICON),
        GetSystemMetrics(SM_CYSMICON),
        LR_DEFAULTCOLOR));
    windowClass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    windowClass.lpszClassName = kWindowClassName;

    if (!RegisterClassExW(&windowClass)) {
        MessageBoxW(nullptr, L"无法注册主窗口类。", L"万能乱码恢复 - 启动失败", MB_OK | MB_ICONERROR);
        FreeLibrary(g_richEditModule);
        return 1;
    }

    // 初始尺寸按 100% DPI 写成 1100×780；Windows 创建后会再按当前系统 DPI/工作区显示。
    g_mainWindow = CreateWindowExW(
        0,
        kWindowClassName,
        L"万能乱码恢复 v0.2.0",
        WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        Scale(1100),
        Scale(780),
        nullptr,
        nullptr,
        instance,
        nullptr);

    if (g_mainWindow == nullptr) {
        MessageBoxW(nullptr, L"无法创建主窗口。", L"万能乱码恢复 - 启动失败", MB_OK | MB_ICONERROR);
        FreeLibrary(g_richEditModule);
        return 1;
    }

    // 按用户长期的软件交互规格：窗口启动时始终在目标显示器工作区中央。
    CenterWindowOnCursorMonitor(g_mainWindow);

    ShowWindow(g_mainWindow, showCommand);
    UpdateWindow(g_mainWindow);

    // 普通双击启动时 argumentCount==1，这里什么都不会发生；
    // 如果用户把一个 TXT 拖到 EXE/快捷方式图标上，则窗口显示后立即读入并自动修复。
    HandleStartupArguments();
    SetFocus(g_inputEdit);

    MSG message = {};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }

    FreeLibrary(g_richEditModule);
    g_richEditModule = nullptr;

    return static_cast<int>(message.wParam);
}
