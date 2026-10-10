#include "TransformEngine.h"

#include <algorithm>
#include <cstdint>
#include <cwctype>
#include <limits>
#include <sstream>
#include <string_view>
#include <unordered_map>

namespace {

// 通过这一函数统一写入 Unicode 码点。
// Windows 的 wchar_t 是 16 位：超过 U+FFFF 的文字要拆成两个 UTF-16 代理项。
// 在测试使用的 32 位 wchar_t 系统上，则直接写入完整码点。
bool AppendCodePoint(std::wstring& out, std::uint32_t cp) {
    if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
    if (sizeof(wchar_t) == 2 && cp > 0xFFFF) {
        cp -= 0x10000;
        out.push_back(static_cast<wchar_t>(0xD800 + (cp >> 10)));
        out.push_back(static_cast<wchar_t>(0xDC00 + (cp & 0x3FF)));
    } else {
        out.push_back(static_cast<wchar_t>(cp));
    }
    return true;
}

// 从 UTF-16/UTF-32 字符串中读取一个完整码点，同时检查代理项配对是否正确。
bool NextCodePoint(const std::wstring& input, std::size_t& pos, std::uint32_t& cp) {
    if (pos >= input.size()) return false;
    cp = static_cast<std::uint32_t>(input[pos++]);
    if (cp >= 0xD800 && cp <= 0xDBFF) {
        if (pos >= input.size()) return false;
        const auto low = static_cast<std::uint32_t>(input[pos]);
        if (low < 0xDC00 || low > 0xDFFF) return false;
        ++pos;
        cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
    } else if (cp >= 0xDC00 && cp <= 0xDFFF) return false;
    return cp <= 0x10FFFF;
}

// 所有非 ASCII 字符都先通过 UTF-8 编码成字节；ASCII 在 UTF-8 中编码不变。
// 因此“ASCII 十进制互转”与“UTF-8 字节十进制互转”可以共用可靠实现。
bool ToUtf8(const std::wstring& input, std::string& bytes) {
    bytes.clear();
    for (std::size_t i = 0; i < input.size();) {
        std::uint32_t cp = 0;
        if (!NextCodePoint(input, i, cp)) return false;
        if (cp < 0x80) bytes.push_back(static_cast<char>(cp));
        else if (cp < 0x800) {
            bytes.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            bytes.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            bytes.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            bytes.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            bytes.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            bytes.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            bytes.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            bytes.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            bytes.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    return true;
}

// 严格 UTF-8 解码：禁止过长编码、非法续字节、代理项与超过 U+10FFFF 的码点。
// 不能把解码失败悄悄替换成问号，否则原始乱码信息会永久丢失。
bool FromUtf8(std::string_view bytes, std::wstring& output) {
    output.clear();
    for (std::size_t i = 0; i < bytes.size();) {
        const auto first = static_cast<unsigned char>(bytes[i++]);
        std::uint32_t cp = first;
        int remaining = 0;
        std::uint32_t minimum = 0;
        if (first < 0x80) {}
        else if (first >= 0xC2 && first <= 0xDF) { cp = first & 0x1F; remaining = 1; minimum = 0x80; }
        else if (first >= 0xE0 && first <= 0xEF) { cp = first & 0x0F; remaining = 2; minimum = 0x800; }
        else if (first >= 0xF0 && first <= 0xF4) { cp = first & 0x07; remaining = 3; minimum = 0x10000; }
        else return false;
        while (remaining--) {
            if (i >= bytes.size()) return false;
            const auto next = static_cast<unsigned char>(bytes[i++]);
            if ((next & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (next & 0x3F);
        }
        if (cp < minimum || !AppendCodePoint(output, cp)) return false;
    }
    return true;
}

int HexDigit(wchar_t ch) {
    if (ch >= L'0' && ch <= L'9') return ch - L'0';
    if (ch >= L'a' && ch <= L'f') return ch - L'a' + 10;
    if (ch >= L'A' && ch <= L'F') return ch - L'A' + 10;
    return -1;
}

std::wstring HexNumber(unsigned int number, int width) {
    const wchar_t* digits = L"0123456789ABCDEF";
    std::wstring result(static_cast<std::size_t>(width), L'0');
    for (int i = width - 1; i >= 0; --i) {
        result[static_cast<std::size_t>(i)] = digits[number & 15];
        number >>= 4;
    }
    return result;
}

bool ParseHex(std::wstring_view value, std::uint32_t& number) {
    if (value.empty() || value.size() > 8) return false;
    number = 0;
    for (wchar_t ch : value) {
        const int digit = HexDigit(ch);
        if (digit < 0 || number > (0x10FFFFu >> 4)) return false;
        number = (number << 4) | static_cast<unsigned int>(digit);
    }
    return true;
}

// URL 编码始终按 UTF-8 的字节进行，空格默认是 %20。
// 单独的“表单编码”模式才把 + 当作空格，避免误改正常文件名中的加号。
bool ConvertPercent(const std::wstring& input, bool encode, bool form,
                    std::wstring& output, std::wstring& error) {
    if (encode) {
        std::string bytes;
        if (!ToUtf8(input, bytes)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
        output.clear();
        for (unsigned char ch : bytes) {
            if ((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.' || ch == '~') {
                output.push_back(static_cast<wchar_t>(ch));
            } else if (form && ch == ' ') output.push_back(L'+');
            else { output.push_back(L'%'); output += HexNumber(ch, 2); }
        }
        return true;
    }
    output.clear();
    for (std::size_t i = 0; i < input.size();) {
        if (form && input[i] == L'+') { output.push_back(L' '); ++i; continue; }
        if (input[i] != L'%' || i + 2 >= input.size() ||
            HexDigit(input[i + 1]) < 0 || HexDigit(input[i + 2]) < 0) {
            output.push_back(input[i++]); continue;
        }
        const std::size_t start = i;
        std::string bytes;
        while (i + 2 < input.size() && input[i] == L'%' &&
               HexDigit(input[i + 1]) >= 0 && HexDigit(input[i + 2]) >= 0) {
            bytes.push_back(static_cast<char>((HexDigit(input[i + 1]) << 4) | HexDigit(input[i + 2])));
            i += 3;
        }
        std::wstring decoded;
        if (FromUtf8(bytes, decoded)) output += decoded;
        else {
            // 遇到不完整的旧代码页编码，不允许输出破损字形；保留原编码供用户检查。
            output.append(input, start, i - start);
        }
    }
    return true;
}

// 全量 HTML5 命名实体数据保存在一个独立文件中，便于逐项核查和维护。
// 只允许 ; 结尾的实体，保证普通英文中的 &name 不会被误认。
struct HtmlEntityPair { std::wstring_view name; std::wstring_view value; };
const HtmlEntityPair kHtmlEntityPairs[] = {
#include "HtmlEntities.inc"
};

const std::unordered_map<std::wstring_view, std::wstring_view>& HtmlMap() {
    static const auto map = [] {
        std::unordered_map<std::wstring_view, std::wstring_view> values;
        values.reserve(sizeof(kHtmlEntityPairs) / sizeof(kHtmlEntityPairs[0]));
        for (const auto& pair : kHtmlEntityPairs) values.emplace(pair.name, pair.value);
        return values;
    }();
    return map;
}

bool ConvertHtml(const std::wstring& input, bool encode,
                 std::wstring& output, std::wstring& error) {
    output.clear();
    if (encode) {
        for (std::size_t i = 0; i < input.size();) {
            std::uint32_t cp = 0;
            if (!NextCodePoint(input, i, cp)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
            if (cp == '&') output += L"&amp;";
            else if (cp == '<') output += L"&lt;";
            else if (cp == '>') output += L"&gt;";
            else if (cp == '"') output += L"&quot;";
            else if (cp == '\'') output += L"&#39;";
            else if (cp > 127) output += L"&#x" + HexNumber(cp, cp <= 0xFFFF ? 4 : 6) + L";";
            else output.push_back(static_cast<wchar_t>(cp));
        }
        return true;
    }
    for (std::size_t i = 0; i < input.size();) {
        if (input[i] != L'&') { output.push_back(input[i++]); continue; }
        // HTML5 实体名称最长只有几十个字符；限制扫描范围避免恶意长串拖慢整篇文章。
        const std::size_t end = input.find(L';', i + 1);
        if (end == std::wstring::npos || end - i > 40) { output.push_back(input[i++]); continue; }
        const auto name = std::wstring_view(input).substr(i + 1, end - i - 1);
        std::wstring decoded;
        if (name.size() >= 2 && name[0] == L'#') {
            unsigned int base = 10;
            std::size_t begin = 1;
            if (begin < name.size() && (name[begin] == L'x' || name[begin] == L'X')) { base = 16; ++begin; }
            std::uint32_t cp = 0;
            bool valid = begin < name.size();
            for (std::size_t k = begin; valid && k < name.size(); ++k) {
                const int digit = base == 16 ? HexDigit(name[k]) :
                    (name[k] >= L'0' && name[k] <= L'9' ? name[k] - L'0' : -1);
                if (digit < 0 || static_cast<unsigned int>(digit) >= base ||
                    cp > (0x10FFFFu - static_cast<unsigned int>(digit)) / base) valid = false;
                else cp = cp * base + static_cast<unsigned int>(digit);
            }
            if (valid) valid = AppendCodePoint(decoded, cp);
            if (!valid) decoded.clear();
        } else {
            const auto iter = HtmlMap().find(name);
            if (iter != HtmlMap().end()) decoded.assign(iter->second);
        }
        if (!decoded.empty()) { output += decoded; i = end + 1; }
        else output.push_back(input[i++]);
    }
    return true;
}

// \uXXXX 与 \xHH 是 JavaScript/JSON 里最常见的字符串转义。
// \uD83D\uDE00 必须按 UTF-16 代理项合并，不能分别写入两个坏字符。
bool ConvertUnicodeEscape(const std::wstring& input, bool encode,
                          std::wstring& output, std::wstring& error) {
    output.clear();
    if (encode) {
        for (std::size_t i = 0; i < input.size();) {
            std::uint32_t cp = 0;
            if (!NextCodePoint(input, i, cp)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
            if (cp < 128 && cp != '\\') output.push_back(static_cast<wchar_t>(cp));
            else if (cp == '\\') output += L"\\\\";
            else if (cp <= 0xFFFF) output += L"\\u" + HexNumber(cp, 4);
            else {
                cp -= 0x10000;
                output += L"\\u" + HexNumber(0xD800 + (cp >> 10), 4);
                output += L"\\u" + HexNumber(0xDC00 + (cp & 0x3FF), 4);
            }
        }
        return true;
    }
    for (std::size_t i = 0; i < input.size();) {
        if (input[i] != L'\\' || i + 1 >= input.size()) { output.push_back(input[i++]); continue; }
        if (input[i + 1] == L'\\') { output.push_back(L'\\'); i += 2; continue; }
        const wchar_t marker = input[i + 1];
        std::uint32_t cp = 0;
        std::size_t end = i;
        bool parsed = false;
        if (marker == L'u' && i + 2 < input.size() && input[i + 2] == L'{') {
            end = input.find(L'}', i + 3);
            if (end != std::wstring::npos) {
                parsed = ParseHex(std::wstring_view(input).substr(i + 3, end - i - 3), cp);
                ++end;
            }
        } else {
            const std::size_t digits = marker == L'x' ? 2 :
                marker == L'u' ? 4 : marker == L'U' ? 8 : 0;
            if (digits && i + 2 + digits <= input.size()) {
                parsed = ParseHex(std::wstring_view(input).substr(i + 2, digits), cp);
                end = i + 2 + digits;
            }
        }
        if (parsed && cp >= 0xD800 && cp <= 0xDBFF && marker == L'u' &&
            end + 6 <= input.size() && input.substr(end, 2) == L"\\u") {
            std::uint32_t low = 0;
            if (ParseHex(std::wstring_view(input).substr(end + 2, 4), low) && low >= 0xDC00 && low <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                end += 6;
            }
        }
        if (parsed && AppendCodePoint(output, cp)) i = end;
        else { output.push_back(input[i++]); }
    }
    return true;
}

bool ParseByteTokens(const std::wstring& input, bool hex,
                     std::string& bytes, std::wstring& error) {
    bytes.clear();
    std::size_t i = 0;
    while (i < input.size()) {
        while (i < input.size() && (iswspace(input[i]) || input[i] == L',' || input[i] == L';')) ++i;
        if (i >= input.size()) break;
        if (hex && i + 1 < input.size() && input[i] == L'0' &&
            (input[i + 1] == L'x' || input[i + 1] == L'X')) i += 2;
        const std::size_t begin = i;
        while (i < input.size() && (hex ? HexDigit(input[i]) >= 0 :
            (input[i] >= L'0' && input[i] <= L'9'))) ++i;
        if (begin == i) { error = L"字节列表中存在非法字符。"; return false; }
        const auto digits = std::wstring_view(input).substr(begin, i - begin);
        if (hex && digits.size() > 2) {
            // 连续十六进制串 E4B8AD 也允许，每两位表示一个 UTF-8 字节。
            if (digits.size() % 2 != 0) { error = L"连续十六进制字节数必须是偶数。"; return false; }
            for (std::size_t j = 0; j < digits.size(); j += 2)
                bytes.push_back(static_cast<char>((HexDigit(digits[j]) << 4) | HexDigit(digits[j + 1])));
        } else {
            std::uint32_t value = 0;
            for (wchar_t ch : digits) {
                const int d = hex ? HexDigit(ch) : ch - L'0';
                if (value > (255u - static_cast<unsigned int>(d)) / (hex ? 16u : 10u)) {
                    error = L"字节数值必须在 0～255 之间。"; return false;
                }
                value = value * (hex ? 16u : 10u) + static_cast<unsigned int>(d);
            }
            bytes.push_back(static_cast<char>(value));
        }
        if (i < input.size() && !iswspace(input[i]) && input[i] != L',' && input[i] != L';') {
            error = L"请用空格、逗号或分号分隔字节。"; return false;
        }
    }
    return true;
}

bool ConvertByteNumbers(const std::wstring& input, bool encode, bool hex,
                        std::wstring& output, std::wstring& error) {
    std::string bytes;
    if (encode) {
        if (!ToUtf8(input, bytes)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
        output.clear();
        for (std::size_t i = 0; i < bytes.size(); ++i) {
            if (i) output.push_back(L' ');
            const auto byte = static_cast<unsigned char>(bytes[i]);
            output += hex ? HexNumber(byte, 2) : std::to_wstring(byte);
        }
        return true;
    }
    if (!ParseByteTokens(input, hex, bytes, error)) return false;
    if (!FromUtf8(bytes, output)) { error = L"这些字节不是完整合法的 UTF-8；请检查编码来源。"; return false; }
    return true;
}

constexpr char kBase64Alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
int Base64Digit(wchar_t ch) {
    if (ch >= L'A' && ch <= L'Z') return ch - L'A';
    if (ch >= L'a' && ch <= L'z') return ch - L'a' + 26;
    if (ch >= L'0' && ch <= L'9') return ch - L'0' + 52;
    if (ch == L'+') return 62;
    if (ch == L'/') return 63;
    return -1;
}

bool ConvertBase64(const std::wstring& input, bool encode,
                   std::wstring& output, std::wstring& error) {
    if (encode) {
        std::string bytes;
        if (!ToUtf8(input, bytes)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
        output.clear();
        for (std::size_t i = 0; i < bytes.size(); i += 3) {
            const auto a = static_cast<unsigned char>(bytes[i]);
            const bool b = i + 1 < bytes.size(), c = i + 2 < bytes.size();
            const auto second = b ? static_cast<unsigned char>(bytes[i + 1]) : 0u;
            const auto third = c ? static_cast<unsigned char>(bytes[i + 2]) : 0u;
            output.push_back(kBase64Alphabet[a >> 2]);
            output.push_back(kBase64Alphabet[((a & 3) << 4) | (second >> 4)]);
            output.push_back(b ? kBase64Alphabet[((second & 15) << 2) | (third >> 6)] : '=');
            output.push_back(c ? kBase64Alphabet[third & 63] : '=');
        }
        return true;
    }
    std::wstring compact;
    for (wchar_t ch : input) if (!iswspace(ch)) compact.push_back(ch);
    if (compact.size() % 4) { error = L"Base64 长度必须是 4 的倍数（允许换行）。"; return false; }
    std::string bytes;
    for (std::size_t i = 0; i < compact.size(); i += 4) {
        const int a = Base64Digit(compact[i]), b = Base64Digit(compact[i + 1]);
        const bool pad2 = compact[i + 2] == L'=', pad3 = compact[i + 3] == L'=';
        const int c = pad2 ? 0 : Base64Digit(compact[i + 2]);
        const int d = pad3 ? 0 : Base64Digit(compact[i + 3]);
        if (a < 0 || b < 0 || c < 0 || d < 0 || (pad2 && !pad3) ||
            ((pad2 || pad3) && i + 4 != compact.size()) ||
            (pad2 && (b & 15) != 0) || (pad3 && !pad2 && (c & 3) != 0)) {
            error = L"Base64 存在非法字符、非规范补位或中间填充。"; return false;
        }
        bytes.push_back(static_cast<char>((a << 2) | (b >> 4)));
        if (!pad2) bytes.push_back(static_cast<char>(((b & 15) << 4) | (c >> 2)));
        if (!pad3) bytes.push_back(static_cast<char>(((c & 3) << 6) | d));
    }
    if (!FromUtf8(bytes, output)) { error = L"Base64 能解出字节，但内容不是 UTF-8 文本。"; return false; }
    return true;
}

// Quoted-Printable 常见于邮件正文，=E4=B8=AD 表示 UTF-8 的“中”。
// 软换行 =\r\n 或 =\n 只是传输时的折行，不属于原文。
bool ConvertQuotedPrintable(const std::wstring& input, bool encode,
                            std::wstring& output, std::wstring& error) {
    if (encode) {
        std::string bytes;
        if (!ToUtf8(input, bytes)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
        output.clear();
        for (unsigned char ch : bytes) {
            if ((ch >= 33 && ch <= 60) || (ch >= 62 && ch <= 126) ||
                ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r')
                output.push_back(static_cast<wchar_t>(ch));
            else { output.push_back(L'='); output += HexNumber(ch, 2); }
        }
        return true;
    }
    // 混合存在的普通 Unicode 正文先原样保留；连续 ASCII/QP 字节段严格解 UTF-8。
    output.clear();
    std::string chunk;
    auto flush = [&]() -> bool {
        if (chunk.empty()) return true;
        std::wstring decoded;
        if (!FromUtf8(chunk, decoded)) return false;
        output += decoded;
        chunk.clear();
        return true;
    };
    for (std::size_t i = 0; i < input.size();) {
        const wchar_t ch = input[i];
        if (ch > 127) {
            if (!flush()) { error = L"邮件编码包含无效 UTF-8 字节。"; return false; }
            output.push_back(ch); ++i; continue;
        }
        if (ch == L'=' && i + 1 < input.size() && input[i + 1] == L'\n') { i += 2; continue; }
        if (ch == L'=' && i + 2 < input.size() && input[i + 1] == L'\r' && input[i + 2] == L'\n') { i += 3; continue; }
        if (ch == L'=' && i + 2 < input.size() && HexDigit(input[i + 1]) >= 0 && HexDigit(input[i + 2]) >= 0) {
            chunk.push_back(static_cast<char>((HexDigit(input[i + 1]) << 4) | HexDigit(input[i + 2])));
            i += 3;
        } else { chunk.push_back(static_cast<char>(ch)); ++i; }
    }
    if (!flush()) { error = L"邮件编码包含无效 UTF-8 字节。"; return false; }
    return true;
}

bool ConvertCodePoints(const std::wstring& input, bool encode,
                       std::wstring& output, std::wstring& error) {
    output.clear();
    if (encode) {
        for (std::size_t i = 0; i < input.size();) {
            std::uint32_t cp = 0;
            if (!NextCodePoint(input, i, cp)) { error = L"文字包含无效 Unicode 代理项。"; return false; }
            if (!output.empty()) output.push_back(L' ');
            output += L"U+" + HexNumber(cp, cp <= 0xFFFF ? 4 : 6);
        }
        return true;
    }
    std::size_t pos = 0;
    while (pos < input.size()) {
        while (pos < input.size() && (iswspace(input[pos]) || input[pos] == L',' || input[pos] == L';')) ++pos;
        if (pos >= input.size()) break;
        if (pos + 2 <= input.size() && input.substr(pos, 2) == L"U+") pos += 2;
        const std::size_t begin = pos;
        while (pos < input.size() && HexDigit(input[pos]) >= 0) ++pos;
        std::uint32_t cp = 0;
        if (!ParseHex(std::wstring_view(input).substr(begin, pos - begin), cp) || !AppendCodePoint(output, cp) ||
            (pos < input.size() && !iswspace(input[pos]) && input[pos] != L',' && input[pos] != L';')) {
            error = L"码点必须是 U+XXXX 格式，码点值不可为代理项或超过 U+10FFFF。";
            return false;
        }
    }
    return true;
}

} // namespace

std::vector<TextFormatInfo> GetTextFormats() {
    return {
        {TextFormat::Percent, L"URL 百分号编码（%XX / UTF-8）"},
        {TextFormat::FormPercent, L"表单编码（%XX，+ 代表空格）"},
        {TextFormat::HtmlEntity, L"HTML 实体（&#...; / &name;）"},
        {TextFormat::UnicodeEscape, L"Unicode / JavaScript（\\u / \\x）"},
        {TextFormat::DecimalBytes, L"ASCII / UTF-8 十进制字节"},
        {TextFormat::HexBytes, L"ASCII / UTF-8 十六进制字节"},
        {TextFormat::UnicodeCodePoint, L"Unicode 码点（U+XXXX）"},
        {TextFormat::Base64, L"Base64（UTF-8 文本）"},
        {TextFormat::QuotedPrintable, L"邮件 Quoted-Printable"}
    };
}

bool ConvertTextFormat(const std::wstring& input, TextFormat format, bool encode,
                       std::wstring& output, std::wstring& error) {
    error.clear();
    std::wstring converted;
    bool success = false;
    switch (format) {
        case TextFormat::Percent: success = ConvertPercent(input, encode, false, converted, error); break;
        case TextFormat::FormPercent: success = ConvertPercent(input, encode, true, converted, error); break;
        case TextFormat::HtmlEntity: success = ConvertHtml(input, encode, converted, error); break;
        case TextFormat::UnicodeEscape: success = ConvertUnicodeEscape(input, encode, converted, error); break;
        case TextFormat::DecimalBytes: success = ConvertByteNumbers(input, encode, false, converted, error); break;
        case TextFormat::HexBytes: success = ConvertByteNumbers(input, encode, true, converted, error); break;
        case TextFormat::Base64: success = ConvertBase64(input, encode, converted, error); break;
        case TextFormat::QuotedPrintable: success = ConvertQuotedPrintable(input, encode, converted, error); break;
        case TextFormat::UnicodeCodePoint: success = ConvertCodePoints(input, encode, converted, error); break;
    }
    if (success) output.swap(converted);
    else if (error.empty()) error = L"文字格式转换失败。";
    return success;
}

bool DetectAndDecodeEscapes(const std::wstring& input, std::wstring& output,
                            std::wstring& description) {
    // 自动解码最多做三层。例如 %26%23x4E2D%3B → &#x4E2D; → 中。
    // 不默认尝试 Base64/纯数字串，因为普通短文本也可能刚好符合这些格式。
    std::wstring current = input;
    std::wstring steps;
    for (int depth = 0; depth < 3; ++depth) {
        bool changed = false;
        for (const auto format : {TextFormat::Percent, TextFormat::HtmlEntity, TextFormat::UnicodeEscape}) {
            // 只有输入中存在足够明确的格式标记才启动对应解码。
            bool plausible = false;
            if (format == TextFormat::Percent) {
                for (std::size_t i = 0; i + 2 < current.size(); ++i) {
                    if (current[i] == L'%' && HexDigit(current[i + 1]) >= 0 && HexDigit(current[i + 2]) >= 0) {
                        plausible = true; break;
                    }
                }
            } else if (format == TextFormat::HtmlEntity) plausible = current.find(L'&') != std::wstring::npos && current.find(L';') != std::wstring::npos;
            else plausible = current.find(L"\\u") != std::wstring::npos || current.find(L"\\U") != std::wstring::npos || current.find(L"\\x") != std::wstring::npos;
            if (!plausible) continue;
            std::wstring next, error;
            // 含 NUL 的字节序列无法安全显示在普通 Win32 字符串控件里。
            // 自动分析将其视作不适合直接预览的格式，而不是返回被截断的候选。
            if (ConvertTextFormat(current, format, false, next, error) &&
                next != current && next.find(L'\0') == std::wstring::npos) {
                current.swap(next);
                if (!steps.empty()) steps += L" → ";
                steps += format == TextFormat::Percent ? L"URL %XX" :
                    format == TextFormat::HtmlEntity ? L"HTML 实体" : L"Unicode 转义";
                changed = true;
            }
        }
        if (!changed) break;
    }
    if (current == input) return false;
    output.swap(current);
    description = steps;
    return true;
}
