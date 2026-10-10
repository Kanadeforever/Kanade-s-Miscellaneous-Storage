#include "../src/TransformEngine.h"

// 本文件是可在 Linux 与 Windows 上独立运行的纯算法回归测试。
// 每个测试都有明确的输入、期望输出及失败原因，避免只凭 GUI 视觉效果判断是否正确。

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

void Require(bool ok, const char* explanation) {
    if (!ok) throw std::runtime_error(explanation);
}

std::wstring Convert(const std::wstring& input, TextFormat format, bool encode) {
    std::wstring output, error;
    const bool success = ConvertTextFormat(input, format, encode, output, error);
    if (!success) {
        std::wcerr << L"转换错误：" << error << L"\n";
        throw std::runtime_error("unexpected conversion failure");
    }
    return output;
}

void RoundTrip(const std::wstring& input, TextFormat format, const char* caseName) {
    const auto encoded = Convert(input, format, true);
    const auto decoded = Convert(encoded, format, false);
    Require(decoded == input, caseName);
}

int main() {
    try {
        // 真实使用场景：文件名中混杂普通 ASCII 名称、版本号、中文 UTF-8 百分号编码及扩展名。
        const std::wstring filename =
            L"NPCSubtitles_v0.1e-log1_%E6%8C%89%E9%9C%80%E8%AF%A6%E7%BB%86%E6%97%A5%E5%BF%97_"
            L"%E5%B7%B2%E7%BC%96%E8%AF%91%E5%AE%8C%E6%95%B4%E5%B7%A5%E7%A8%8B%E5%8C%85_20261011.zip";
        const std::wstring expected =
            L"NPCSubtitles_v0.1e-log1_按需详细日志_已编译完整工程包_20261011.zip";
        Require(Convert(filename, TextFormat::Percent, false) == expected,
                "百分号中文文件名恢复错误");
        std::wstring automatic, description;
        Require(DetectAndDecodeEscapes(filename, automatic, description) && automatic == expected,
                "自动检测未恢复实际文件名");
        Require(Convert(L"ABC", TextFormat::DecimalBytes, true) == L"65 66 67",
                "ASCII 十进制编码错误");
        Require(Convert(L"65 66 67", TextFormat::DecimalBytes, false) == L"ABC",
                "ASCII 十进制解码错误");
        Require(Convert(L"中文", TextFormat::DecimalBytes, true) == L"228 184 173 230 150 135",
                "中文 UTF-8 十进制字节编码错误");
        Require(Convert(L"E4 B8 AD E6 96 87", TextFormat::HexBytes, false) == L"中文",
                "UTF-8 十六进制字节解码错误");
        Require(Convert(L"E4B8ADE69687", TextFormat::HexBytes, false) == L"中文",
                "连续十六进制字节解码错误");
        Require(Convert(L"&#x4E2D;&amp;&NotEqualTilde;", TextFormat::HtmlEntity, false) ==
                L"中&\u2242\u0338", "HTML5 实体或组合实体错误");
        Require(Convert(L"中文\\uD83D\\uDE00\\x21", TextFormat::UnicodeEscape, false) ==
                L"中文\U0001F600!", "Unicode 代理项或 JS 转义解码错误");
        Require(Convert(L"U+4E2D U+6587 U+01F600", TextFormat::UnicodeCodePoint, false) ==
                L"中文\U0001F600", "Unicode 码点解码错误");
        Require(Convert(L"=E4=B8=AD=E6=96=87", TextFormat::QuotedPrintable, false) == L"中文",
                "邮件 Quoted-Printable 解码错误");
        Require(Convert(L"%E4%B8%AD%20A%2BB", TextFormat::Percent, false) == L"中 A+B",
                "百分号空格或加号语义错误");
        Require(Convert(L"A+B", TextFormat::FormPercent, false) == L"A B",
                "表单 + 字符未转换为空格");
        Require(Convert(L"A+B", TextFormat::Percent, false) == L"A+B",
                "普通 URL 模式错误地修改了加号");
        Require(Convert(L"%E6%8C", TextFormat::Percent, false) == L"%E6%8C",
                "不完整 UTF-8 错误地生成了替代字符");
        Require(DetectAndDecodeEscapes(L"%26%23x4E2D%3B", automatic, description) && automatic == L"中",
                "多层百分号/HTML 实体解码错误");
        Require(!DetectAndDecodeEscapes(L"normal_text_2026.zip", automatic, description),
                "普通文件名被误判为转义文字");
        Require(!DetectAndDecodeEscapes(L"%00", automatic, description),
                "NUL 百分号转义不应自动显示为候选");
        Require(Convert(L"prefix &copycat suffix", TextFormat::HtmlEntity, false) ==
                L"prefix &copycat suffix", "缺少分号的普通文本不应被自动当作 HTML 实体");

        // 特殊字符、日文、俄文、阿拉伯文、emoji 和中文混排必须每种模式都能无损回转。
        const std::wstring mixed = L"ABC 123 中文 日本語 Русский عربي \U0001F600 & <> \\";
        for (const auto& info : GetTextFormats())
            RoundTrip(mixed, info.kind, "多语言字符往返不一致");

        // 对 ASCII 数字列表和 Base64 的非法输入必须明确失败，不产生伪结果。
        std::wstring out, error;
        Require(!ConvertTextFormat(L"256", TextFormat::DecimalBytes, false, out, error),
                "超过 255 的 ASCII 字节未拒绝");
        Require(!ConvertTextFormat(L"E4 B8", TextFormat::HexBytes, false, out, error),
                "不完整 UTF-8 字节未拒绝");
        Require(!ConvertTextFormat(L"aaaa=aaa", TextFormat::Base64, false, out, error),
                "非法 Base64 未拒绝");

        // 使用固定伪随机序列组合多语字符、符号和反斜杠。
        // 种子固定后每次构建测试完全相同，避免无法复现偶发失败。
        const std::vector<std::wstring> fragments = {
            L"a", L"中", L"文", L"あ", L"ا", L"Б", L"&", L"<", L"\\", L"%", L"=", L" ",
            L"\U0001F600", L"\r", L"\n", L"\t", L"'", L"\"", L"9", L"+"
        };
        unsigned int seed = 0x12345678u;
        for (int sample = 0; sample < 128; ++sample) {
            std::wstring randomText;
            for (int index = 0; index < 80; ++index) {
                seed = seed * 1664525u + 1013904223u;
                randomText += fragments[seed % fragments.size()];
            }
            for (const auto& format : GetTextFormats())
                RoundTrip(randomText, format.kind, "混合多语言伪随机往返失败");
        }

        // 以百万字符长文执行完整往返，验证没有固定句长缓冲区。
        std::wstring longText;
        for (int i = 0; i < 100000; ++i) longText += L"中文English!";
        RoundTrip(longText, TextFormat::Percent, "超长文章 URL 往返不一致");
        RoundTrip(longText, TextFormat::HtmlEntity, "超长文章 HTML 往返不一致");
        RoundTrip(longText, TextFormat::DecimalBytes, "超长文章 ASCII 数字往返不一致");
        std::cout << "PASS: 真实文件名、多语言、ASCII、HTML5、URL、Unicode、Base64、邮件和百万字符长文。\n";
    } catch (const std::exception& ex) {
        std::cerr << "FAIL: " << ex.what() << '\n';
        return 1;
    }
    return 0;
}
