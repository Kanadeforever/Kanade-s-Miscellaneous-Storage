#include "EncodingEngine.h"

// windows.h 提供 MultiByteToWideChar、WideCharToMultiByte、IsValidCodePage 等系统函数。
// 这些 API 是原始 RenameFiles.exe 实际使用的同一类底层机制；
// 为适应任意长度的文章与多种代码页，所有转码过程均使用动态缓冲和可枚举编码。
#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <sstream>
#include <unordered_map>
#include <unordered_set>

namespace {

// “自动分析”不应该无脑枚举 Windows 支持的几百种历史代码页。
// 那样不仅速度慢，而且许多非常冷门的代码页会制造大量看似成功、实际无意义的候选。
// 这里保留现实中最常见的 UTF-8、简繁中文、日文、韩文、俄文和 Windows 西欧代码页。
const EncodingInfo kCommonEncodings[] = {
    {65001, L"UTF-8"},
    {936,   L"GBK / CP936（简体中文）"},
    {54936, L"GB18030（简体中文）"},
    {950,   L"Big5 / CP950（繁体中文）"},
    {932,   L"Shift-JIS / CP932（日文）"},
    {50221, L"ISO-2022-JP / CP50221（日文）"},
    {949,   L"CP949（韩文）"},
    {51949, L"EUC-KR / CP51949（韩文）"},
    {1252,  L"Windows-1252（西欧）"},
    {28591, L"ISO-8859-1 / Latin-1"},
    {1251,  L"Windows-1251（西里尔文）"},
    {1250,  L"Windows-1250（中欧）"},
    {1253,  L"Windows-1253（希腊文）"},
    {1254,  L"Windows-1254（土耳其文）"},
    {1255,  L"Windows-1255（希伯来文）"},
    {1256,  L"Windows-1256（阿拉伯文）"},
    {1257,  L"Windows-1257（波罗的海）"},
    {1258,  L"Windows-1258（越南文）"},
};

// 自动候选的“错误解码侧”主要选择最常见的错误解释代码页。
// CP50221、EUC-KR 这类状态型/较少见编码仍然保留在手动模式，
// 但不参与大规模自动组合，避免降低自动排序质量。
const unsigned int kAutomaticWrongDecoders[] = {
    1252, 28591, 936, 54936, 950, 932, 949,
    1251, 1250, 1253, 1254, 1255, 1256, 1257, 1258
};

// 自动候选的“真实编码侧”包含最常见的文件/网页编码。
const unsigned int kAutomaticTrueEncodings[] = {
    65001, 936, 54936, 950, 932, 949, 1251, 1250, 1252, 28591
};

// FindEncodingName 只负责把代码页数字翻译成显示名称。
// 如果遇到列表外但系统支持的代码页，也不会失败，而是直接显示“CP数字”。
std::wstring FindEncodingName(unsigned int codePage) {
    for (const auto& encoding : kCommonEncodings) {
        if (encoding.codePage == codePage) {
            return encoding.name;
        }
    }

    std::wstringstream stream;
    stream << L"CP" << codePage;
    return stream.str();
}

// DecodeBytesRaw 调用 Windows 的 MultiByteToWideChar，把某代码页字节转成 Unicode。
// 这里只做“原始解码”，不负责判断该解码是否真的可逆；可逆检查在后面的函数完成。
bool DecodeBytesRaw(
    const std::vector<unsigned char>& bytes,
    unsigned int codePage,
    std::wstring& result) {

    // 空字节流对应空字符串，这是合法情况，没必要调用 Windows API。
    if (bytes.empty()) {
        result.clear();
        return true;
    }

    // MultiByteToWideChar 的长度参数是 int，所以单次调用最多处理 INT_MAX 字节。
    // GUI 文本控件本身也使用 32 位字符计数；这里不设置“小字数限制”，
    // 只拒绝 Windows API 无法表达的 2GB 级单个文本块。
    if (bytes.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        return false;
    }

    // UTF-8 和 GB18030 在现代 Windows 上支持 MB_ERR_INVALID_CHARS。
    // 使用这个标志时，非法字节序列会直接失败，不会悄悄替换成“�”。
    DWORD flags = 0;
    if (codePage == CP_UTF8 || codePage == 54936) {
        flags = MB_ERR_INVALID_CHARS;
    }

    const int byteCount = static_cast<int>(bytes.size());

    // 第一次调用传入空输出缓冲，只询问“需要多少个 wchar_t”。
    // 这样就不需要原程序那种固定 260 字符数组，也就没有句子长度上限。
    int requiredCharacters = MultiByteToWideChar(
        codePage,
        flags,
        reinterpret_cast<const char*>(bytes.data()),
        byteCount,
        nullptr,
        0);

    // 某些老代码页不接受 MB_ERR_INVALID_CHARS。
    // 如果只是因为标志不兼容而失败，再用 flags=0 重试一次。
    if (requiredCharacters <= 0 && flags != 0) {
        flags = 0;
        requiredCharacters = MultiByteToWideChar(
            codePage,
            flags,
            reinterpret_cast<const char*>(bytes.data()),
            byteCount,
            nullptr,
            0);
    }

    if (requiredCharacters <= 0) {
        return false;
    }

    // std::wstring 会按实际需要动态分配内存。
    // 输入越长，字符串就越长；程序没有人为写死 260、1024 或 65535 之类的上限。
    result.resize(static_cast<std::size_t>(requiredCharacters));

    const int convertedCharacters = MultiByteToWideChar(
        codePage,
        flags,
        reinterpret_cast<const char*>(bytes.data()),
        byteCount,
        result.data(),
        requiredCharacters);

    if (convertedCharacters != requiredCharacters) {
        result.clear();
        return false;
    }

    return true;
}

// EncodeWideRaw 做上一个函数的反方向：Unicode → 指定代码页字节。
// 它同样先询问长度，再动态分配，因此不存在固定短缓冲区。
bool EncodeWideRaw(
    const std::wstring& text,
    unsigned int codePage,
    std::vector<unsigned char>& bytes) {

    if (text.empty()) {
        bytes.clear();
        return true;
    }

    if (text.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        return false;
    }

    const int characterCount = static_cast<int>(text.size());

    // 对 UTF-8/GB18030 可以使用 WC_ERR_INVALID_CHARS 检测非法 Unicode。
    // 传统单字节/双字节代码页则使用 WC_NO_BEST_FIT_CHARS，尽量阻止 Windows
    // 把“不存在的字符”偷偷替换成看起来相似的字符。
    DWORD flags = 0;
    if (codePage == CP_UTF8 || codePage == 54936) {
        flags = WC_ERR_INVALID_CHARS;
    } else if (codePage != 50221 && codePage != 51949) {
        flags = WC_NO_BEST_FIT_CHARS;
    }

    int requiredBytes = WideCharToMultiByte(
        codePage,
        flags,
        text.data(),
        characterCount,
        nullptr,
        0,
        nullptr,
        nullptr);

    // 某些历史代码页不支持上面的严格标志。
    // 这里允许退回 flags=0，但随后还会做完整 round-trip 校验，
    // 所以“最佳拟合”造成的信息变化最终仍然会被识别出来。
    if (requiredBytes <= 0 && flags != 0) {
        flags = 0;
        requiredBytes = WideCharToMultiByte(
            codePage,
            flags,
            text.data(),
            characterCount,
            nullptr,
            0,
            nullptr,
            nullptr);
    }

    if (requiredBytes <= 0) {
        return false;
    }

    bytes.resize(static_cast<std::size_t>(requiredBytes));

    const int convertedBytes = WideCharToMultiByte(
        codePage,
        flags,
        text.data(),
        characterCount,
        reinterpret_cast<char*>(bytes.data()),
        requiredBytes,
        nullptr,
        nullptr);

    if (convertedBytes != requiredBytes) {
        bytes.clear();
        return false;
    }

    return true;
}

// EncodeWideRoundTripStrict 是自动恢复可靠性的关键之一。
// 它不仅要求“能编码”，还要求“编码后再按同一个代码页解码，必须逐字符回到原文”。
// 这样可以排除“某字符无法表示，被 ? 或近似字符替换”的不可逆情况。
bool EncodeWideRoundTripStrict(
    const std::wstring& text,
    unsigned int codePage,
    std::vector<unsigned char>& bytes) {

    if (!EncodeWideRaw(text, codePage, bytes)) {
        return false;
    }

    std::wstring roundTrip;
    if (!DecodeBytesRaw(bytes, codePage, roundTrip)) {
        return false;
    }

    return roundTrip == text;
}

// DecodeBytesRoundTripStrict 与上面的函数方向相反。
// 它要求“字节 → Unicode → 同代码页字节”必须完整还原原字节。
// 因此自动候选只接受真正可逆的转码，不会把已经损坏的数据伪装成成功。
bool DecodeBytesRoundTripStrict(
    const std::vector<unsigned char>& bytes,
    unsigned int codePage,
    std::wstring& text) {

    if (!DecodeBytesRaw(bytes, codePage, text)) {
        return false;
    }

    std::vector<unsigned char> roundTripBytes;
    if (!EncodeWideRaw(text, codePage, roundTripBytes)) {
        return false;
    }

    return roundTripBytes == bytes;
}

// IsControlCharacter 判断“真正不适合正文的控制字符”。
// 回车、换行、Tab 是文章正常需要的，所以明确排除在外。
bool IsControlCharacter(wchar_t ch) {
    if (ch == L'\r' || ch == L'\n' || ch == L'\t') {
        return false;
    }
    return (ch >= 0x0000 && ch < 0x0020) || (ch >= 0x007F && ch <= 0x009F);
}

// IsPrivateUseCharacter 检测 Unicode 私用区。
// 很多“UTF-8 被当成 GBK/Big5 解读”的乱码会出现大量私用区字符，
// 正常文章出现大量这类字符的概率很低，所以这是很有价值的降分信号。
bool IsPrivateUseCharacter(wchar_t ch) {
    return ch >= 0xE000 && ch <= 0xF8FF;
}

// IsCommonTextCharacter 粗略识别正常文本常见的字母/文字区域。
// 它不试图判断“这句话语法对不对”，只判断字符是不是常见正文字符。
bool IsCommonTextCharacter(wchar_t ch) {
    // ASCII 可打印字符。
    if (ch >= 0x0020 && ch <= 0x007E) {
        return true;
    }

    // 拉丁扩展、希腊、西里尔、希伯来、阿拉伯等常见文字区。
    if ((ch >= 0x00A0 && ch <= 0x052F) ||
        (ch >= 0x0590 && ch <= 0x08FF)) {
        return true;
    }

    // 日文平假名、片假名、注音、韩文兼容字母。
    if (ch >= 0x3040 && ch <= 0x318F) {
        return true;
    }

    // CJK 常用汉字。
    if (ch >= 0x3400 && ch <= 0x9FFF) {
        return true;
    }

    // 韩文音节。
    if (ch >= 0xAC00 && ch <= 0xD7A3) {
        return true;
    }

    // 常用全角标点与全角字符。
    if (ch >= 0x2000 && ch <= 0x303F) {
        return true;
    }
    if (ch >= 0xFF01 && ch <= 0xFFEF) {
        return true;
    }

    return false;
}

// CountSubstring 统计某个“典型乱码片段”出现次数。
// 这里用片段而不是单个字符，是为了减少把正常法语/德语中的 Ã、Â 等误判成乱码。
std::size_t CountSubstring(const std::wstring& text, const std::wstring& needle) {
    if (needle.empty()) {
        return 0;
    }

    std::size_t count = 0;
    std::size_t position = 0;
    while ((position = text.find(needle, position)) != std::wstring::npos) {
        ++count;
        position += needle.size();
    }
    return count;
}

// ScoreText 给一段 Unicode 文本打“结构可读性分”。
// 这不是语言模型，不会宣称能理解所有语言；它只使用可解释的字符层特征。
// 特别注意：评分必须按“字符比例”归一化，而不能简单把每个正常字符一直累加。
// 因为同一组字节用不同代码页解码后字符数可能不同；如果按总分累加，
// 更长但更差的乱码反而可能因为“字符多”得到虚假的高分。
double ScoreText(const std::wstring& text) {
    if (text.empty()) {
        return -1000.0;
    }

    double characterScore = 0.0;
    std::size_t commonCharacters = 0;
    std::size_t suspiciousCharacters = 0;
    std::size_t kanaCharacters = 0;

    for (wchar_t ch : text) {
        if (ch == static_cast<wchar_t>(0xFFFD)) {
            characterScore -= 30.0;
            ++suspiciousCharacters;
            continue;
        }

        if (IsControlCharacter(ch)) {
            characterScore -= 12.0;
            ++suspiciousCharacters;
            continue;
        }

        if (IsPrivateUseCharacter(ch)) {
            characterScore -= 8.0;
            ++suspiciousCharacters;
            continue;
        }

        // 日文正常正文通常含有平假名/片假名。
        // UTF-8 被 GBK 等错误解读时，日文经常会退化成“一串全是汉字的假 CJK”，
        // 所以恢复后重新出现 Kana 是一个有价值、但不能绝对化的正面信号。
        //
        // 这里故意【不】对韩文 Hangul 数量额外加分：
        // 中文候选继续经过错误的 GBK→CP949 转码，也可能产生大量合法韩文音节；
        // 如果奖励 Hangul，那个错误的“两步候选”反而可能超过真正的一步恢复结果。
        // 韩文代码页仍然完整支持，只是不把“看起来像韩文”当作自动排序捷径。
        if (ch >= 0x3040 && ch <= 0x30FF) {
            ++kanaCharacters;
        }

        if (IsCommonTextCharacter(ch)) {
            characterScore += 0.55;
            ++commonCharacters;
        } else {
            // 不认识的 Unicode 字符不一定是坏字符，所以这里只做很小的惩罚。
            characterScore -= 0.08;
        }
    }

    const double length = static_cast<double>(text.size());

    // 把逐字符总分除以长度，再放大到 100 字符量级。
    // 这样 20 字短句和 20 万字文章在“文本质量相同”时得到近似可比较的分数。
    double score = (characterScore / length) * 100.0;

    // 典型“UTF-8 被 CP1252/Latin-1 解读”的片段。
    // 使用两字符组合能明显降低正常法语/德语中的 Ã、Â 等被误判的概率。
    const std::wstring mojibakePatterns[] = {
        L"Ã¤", L"Ã¥", L"Ã¦", L"Ã§", L"Ã¨", L"Ã©", L"Ãª", L"Ã«",
        L"Â·", L"Â ", L"â€™", L"â€œ", L"â€", L"ï»¿", L"ðŸ"
    };

    std::size_t mojibakePatternCount = 0;
    for (const auto& pattern : mojibakePatterns) {
        mojibakePatternCount += CountSubstring(text, pattern);
    }

    // 片段惩罚也按长度归一化。
    // 例如 100 字里出现 5 个典型乱码片段会明显降分；10 万字文章偶尔出现 1 个则影响很小。
    score -= (static_cast<double>(mojibakePatternCount) / length) * 700.0;

    const std::size_t replacementChinese = CountSubstring(text, L"锟斤拷");
    score -= (static_cast<double>(replacementChinese) / length) * 1200.0;

    // 正文字符比例越高越好；控制字符、私用区、U+FFFD 比例越高越差。
    score += (static_cast<double>(commonCharacters) / length) * 30.0;
    score -= (static_cast<double>(suspiciousCharacters) / length) * 100.0;

    // 只对日文 Kana 给一个有限且有上限的辅助奖励。
    // 它的作用不是“检测语言”，而是帮助区分“本来有假名的日文”和
    // “被错误代码页解释后退化成一串 CJK 汉字”的候选。
    // Hangul 的字符数量不能可靠推断语义正确性，因此不对它另行加分。
    const double kanaRatio = static_cast<double>(kanaCharacters) / length;
    score += std::min(18.0, kanaRatio * 45.0);

    return score;
}

// MakeSample 从超长全文中抽取“开头 + 中间 + 结尾”。
// 自动检测只对这个样本做大量组合测试，最终用户选中的方案才应用到全文。
// 这样一篇几十万甚至几百万字的文章也不会被重复转码上千次。
std::wstring MakeSample(const std::wstring& input) {
    constexpr std::size_t kPartLength = 4096;
    constexpr std::size_t kWholeSampleLimit = kPartLength * 3;

    if (input.size() <= kWholeSampleLimit) {
        return input;
    }

    std::wstring sample;
    sample.reserve(kWholeSampleLimit + 8);

    sample.append(input, 0, kPartLength);
    sample.append(L"\n");

    const std::size_t middleStart = (input.size() / 2) - (kPartLength / 2);
    sample.append(input, middleStart, kPartLength);
    sample.append(L"\n");

    sample.append(input, input.size() - kPartLength, kPartLength);
    return sample;
}

// CandidateWork 是自动分析过程中使用的内部结构。
// sampleText 只保存抽样结果，不保存整篇文章，避免候选很多时浪费内存。
struct CandidateWork {
    std::vector<RepairStep> steps;
    std::wstring sampleText;
    double score = -std::numeric_limits<double>::infinity();
};

// IsAutomaticCodePageAvailable 在真正尝试候选之前先检查系统是否支持代码页。
bool IsAutomaticCodePageAvailable(unsigned int codePage) {
    return IsValidCodePage(codePage) != FALSE;
}

} // namespace

std::vector<EncodingInfo> GetSupportedEncodings() {
    std::vector<EncodingInfo> result;

    for (const auto& encoding : kCommonEncodings) {
        if (IsValidCodePage(encoding.codePage)) {
            result.push_back(encoding);
        }
    }

    return result;
}

bool RepairOnce(
    const std::wstring& input,
    unsigned int wrongDecoderCodePage,
    unsigned int trueEncodingCodePage,
    std::wstring& result,
    std::wstring& errorMessage) {

    result.clear();
    errorMessage.clear();

    if (input.empty()) {
        errorMessage = L"输入为空，没有需要转换的文字。";
        return false;
    }

    if (wrongDecoderCodePage == trueEncodingCodePage) {
        errorMessage = L"两个代码页相同；这种转换不会改变文字。";
        return false;
    }

    if (!IsValidCodePage(wrongDecoderCodePage)) {
        errorMessage = L"当前 Windows 不支持“乱码当前被解释成的代码页”。";
        return false;
    }

    if (!IsValidCodePage(trueEncodingCodePage)) {
        errorMessage = L"当前 Windows 不支持“原始真实编码”的代码页。";
        return false;
    }

    // 第一步：把“已经错误显示的 Unicode 文字”重新编码回当时的错误代码页字节。
    // 例如“æ—¥”按 CP1252 编回字节后，会重新得到原来的 UTF-8 字节序列。
    std::vector<unsigned char> recoveredBytes;
    if (!EncodeWideRoundTripStrict(input, wrongDecoderCodePage, recoveredBytes)) {
        errorMessage = L"这段文字无法无损地编码回所选的“错误代码页”。可能已经发生字符丢失。";
        return false;
    }

    // 第二步：把刚刚恢复出来的原始字节，按真正应该使用的编码重新解释。
    if (!DecodeBytesRoundTripStrict(recoveredBytes, trueEncodingCodePage, result)) {
        errorMessage = L"恢复出的字节无法按所选“真实编码”无损解码。请尝试其他编码组合。";
        result.clear();
        return false;
    }

    return true;
}

bool ApplyRepairSteps(
    const std::wstring& input,
    const std::vector<RepairStep>& steps,
    std::wstring& result,
    std::wstring& errorMessage) {

    result = input;
    errorMessage.clear();

    for (const auto& step : steps) {
        std::wstring next;
        if (!RepairOnce(
                result,
                step.wrongDecoderCodePage,
                step.trueEncodingCodePage,
                next,
                errorMessage)) {
            result.clear();
            return false;
        }
        result.swap(next);
    }

    return true;
}

std::vector<RepairCandidate> FindRepairCandidates(
    const std::wstring& input,
    std::size_t maximumCandidates) {

    std::vector<RepairCandidate> finalCandidates;

    if (input.empty() || maximumCandidates == 0) {
        return finalCandidates;
    }

    const std::wstring sample = MakeSample(input);
    const double originalScore = ScoreText(sample);

    std::vector<CandidateWork> firstLevel;

    // 第一层：枚举“一次错误解码”的常见组合。
    for (unsigned int wrongCodePage : kAutomaticWrongDecoders) {
        if (!IsAutomaticCodePageAvailable(wrongCodePage)) {
            continue;
        }

        for (unsigned int trueCodePage : kAutomaticTrueEncodings) {
            if (wrongCodePage == trueCodePage || !IsAutomaticCodePageAvailable(trueCodePage)) {
                continue;
            }

            std::wstring repaired;
            std::wstring error;
            if (!RepairOnce(sample, wrongCodePage, trueCodePage, repaired, error)) {
                continue;
            }

            if (repaired == sample) {
                continue;
            }

            CandidateWork work;
            work.steps.push_back({wrongCodePage, trueCodePage});
            work.sampleText = std::move(repaired);
            work.score = ScoreText(work.sampleText) - 1.5; // 每多一步都要付一点“复杂度成本”。

            // UTF-8 是网页、现代文本最常见的真实编码，给非常小的先验加分。
            // 这个加分远小于乱码字符带来的惩罚，不会强行压过明显更好的其它结果。
            if (trueCodePage == CP_UTF8) {
                work.score += 0.75;
            }

            firstLevel.push_back(std::move(work));
        }
    }

    // 先按第一层评分排序。所有第一层候选都应该保留到最终排名，
    // 只有“继续做第二次恢复”这件昂贵操作才限制为前 10 名。
    std::sort(
        firstLevel.begin(),
        firstLevel.end(),
        [](const CandidateWork& left, const CandidateWork& right) {
            return left.score > right.score;
        });

    std::vector<CandidateWork> allWork = firstLevel;
    std::vector<CandidateWork> firstLevelForSecondPass = firstLevel;
    if (firstLevelForSecondPass.size() > 10) {
        firstLevelForSecondPass.resize(10);
    }

    // 第二层：只在最有希望的第一层候选上再尝试一次恢复。
    for (const CandidateWork& first : firstLevelForSecondPass) {
        for (unsigned int wrongCodePage : kAutomaticWrongDecoders) {
            if (!IsAutomaticCodePageAvailable(wrongCodePage)) {
                continue;
            }

            for (unsigned int trueCodePage : kAutomaticTrueEncodings) {
                if (wrongCodePage == trueCodePage || !IsAutomaticCodePageAvailable(trueCodePage)) {
                    continue;
                }

                // 不做“立刻完全反向”的第二步，因为那只会回到第一步之前。
                const RepairStep& previous = first.steps.back();
                if (wrongCodePage == previous.trueEncodingCodePage &&
                    trueCodePage == previous.wrongDecoderCodePage) {
                    continue;
                }

                std::wstring repairedAgain;
                std::wstring error;
                if (!RepairOnce(
                        first.sampleText,
                        wrongCodePage,
                        trueCodePage,
                        repairedAgain,
                        error)) {
                    continue;
                }

                if (repairedAgain == first.sampleText || repairedAgain == sample) {
                    continue;
                }

                CandidateWork work;
                work.steps = first.steps;
                work.steps.push_back({wrongCodePage, trueCodePage});
                work.sampleText = std::move(repairedAgain);
                work.score = ScoreText(work.sampleText) - 3.0; // 双重恢复比单步候选多扣一次复杂度分。

                if (trueCodePage == CP_UTF8) {
                    work.score += 0.75;
                }

                allWork.push_back(std::move(work));
            }
        }
    }

    // 多条不同路径可能得到完全相同的文本。
    // 这里只保留评分最高的一条，避免候选框出现大量重复结果。
    std::unordered_map<std::wstring, CandidateWork> bestByText;
    for (CandidateWork& work : allWork) {
        // 先复制 key，再移动 work。
        // C++17 中赋值表达式的求值顺序可能先移动右侧对象；如果直接写
        // bestByText[work.sampleText] = std::move(work)，sampleText 可能已经被搬空，
        // 最终所有候选错误地落到空字符串键上。这里显式分开，彻底消除该风险。
        const std::wstring key = work.sampleText;
        auto iterator = bestByText.find(key);
        if (iterator == bestByText.end()) {
            bestByText.emplace(key, std::move(work));
        } else if (iterator->second.score < work.score) {
            iterator->second = std::move(work);
        }
    }

    std::vector<CandidateWork> deduplicated;
    deduplicated.reserve(bestByText.size());
    for (auto& pair : bestByText) {
        deduplicated.push_back(std::move(pair.second));
    }

    std::sort(
        deduplicated.begin(),
        deduplicated.end(),
        [](const CandidateWork& left, const CandidateWork& right) {
            return left.score > right.score;
        });

    // 即使第一名没有明显“评分改善”，仍然把排名靠前的可逆候选返回给界面。
    // 原因是东亚编码互错可能出现“乱码和正确文字都落在 CJK 汉字区”的情况，
    // 仅靠字符层评分无法可靠区分；如果直接把这些候选全部过滤掉，用户反而看不到正确路线。
    // GUI 会在 improvement <= 0.5 时明确标记为低置信度，并且不会擅自覆盖原文。
    for (const CandidateWork& work : deduplicated) {
        RepairCandidate candidate;
        candidate.steps = work.steps;
        candidate.score = work.score;
        candidate.improvement = work.score - originalScore;
        finalCandidates.push_back(std::move(candidate));

        if (finalCandidates.size() >= maximumCandidates) {
            break;
        }
    }

    return finalCandidates;
}

std::wstring DescribeRepairStep(const RepairStep& step) {
    return FindEncodingName(step.wrongDecoderCodePage) + L" → " +
           FindEncodingName(step.trueEncodingCodePage);
}

std::wstring DescribeCandidate(const RepairCandidate& candidate) {
    std::wstringstream stream;

    for (std::size_t index = 0; index < candidate.steps.size(); ++index) {
        if (index != 0) {
            stream << L"；";
        }
        stream << DescribeRepairStep(candidate.steps[index]);
    }

    return stream.str();
}

bool DecodeTextFileBytes(
    const std::vector<unsigned char>& bytes,
    std::wstring& text,
    std::wstring& detectedEncoding,
    std::wstring& errorMessage) {

    text.clear();
    detectedEncoding.clear();
    errorMessage.clear();

    if (bytes.empty()) {
        detectedEncoding = L"空文件";
        return true;
    }

    // UTF-8 BOM：EF BB BF。
    if (bytes.size() >= 3 &&
        bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) {

        std::vector<unsigned char> body(bytes.begin() + 3, bytes.end());
        if (DecodeBytesRoundTripStrict(body, CP_UTF8, text)) {
            detectedEncoding = L"UTF-8 BOM";
            return true;
        }
    }

    // UTF-16LE BOM：FF FE。Windows wchar_t 本身就是 UTF-16LE，
    // 但这里仍然手工逐两个字节组合，避免未对齐指针和文件长度为奇数的问题。
    if (bytes.size() >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE) {
        if (((bytes.size() - 2) % 2) != 0) {
            errorMessage = L"UTF-16LE 文件的字节数不是偶数，文件可能已损坏。";
            return false;
        }

        text.reserve((bytes.size() - 2) / 2);
        for (std::size_t index = 2; index + 1 < bytes.size(); index += 2) {
            const wchar_t ch = static_cast<wchar_t>(
                static_cast<unsigned int>(bytes[index]) |
                (static_cast<unsigned int>(bytes[index + 1]) << 8));
            text.push_back(ch);
        }

        detectedEncoding = L"UTF-16LE BOM";
        return true;
    }

    // UTF-16BE BOM：FE FF。读取时需要把高低字节交换成 Windows 使用的 UTF-16LE wchar_t。
    if (bytes.size() >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF) {
        if (((bytes.size() - 2) % 2) != 0) {
            errorMessage = L"UTF-16BE 文件的字节数不是偶数，文件可能已损坏。";
            return false;
        }

        text.reserve((bytes.size() - 2) / 2);
        for (std::size_t index = 2; index + 1 < bytes.size(); index += 2) {
            const wchar_t ch = static_cast<wchar_t>(
                (static_cast<unsigned int>(bytes[index]) << 8) |
                static_cast<unsigned int>(bytes[index + 1]));
            text.push_back(ch);
        }

        detectedEncoding = L"UTF-16BE BOM";
        return true;
    }

    // 无 BOM 时，现代文本最常见的情况就是 UTF-8。
    // 先做严格 round-trip；只要全部字节合法，就优先按 UTF-8 打开。
    if (DecodeBytesRoundTripStrict(bytes, CP_UTF8, text)) {
        detectedEncoding = L"UTF-8（无 BOM）";
        return true;
    }

    // 如果不是 UTF-8，再从常见传统编码中选“能完整 round-trip 且文本结构评分最高”的一个。
    // 这只是打开文件时的保守猜测，不会覆盖原文件；用户仍然可以在主界面继续做乱码恢复。
    const unsigned int legacyCandidates[] = {936, 54936, 950, 932, 949, 1252, 1251, 1250};

    double bestScore = -std::numeric_limits<double>::infinity();
    unsigned int bestCodePage = 0;
    std::wstring bestText;

    for (unsigned int codePage : legacyCandidates) {
        if (!IsValidCodePage(codePage)) {
            continue;
        }

        std::wstring decoded;
        if (!DecodeBytesRoundTripStrict(bytes, codePage, decoded)) {
            continue;
        }

        const double score = ScoreText(MakeSample(decoded));
        if (score > bestScore) {
            bestScore = score;
            bestCodePage = codePage;
            bestText.swap(decoded);
        }
    }

    if (bestCodePage == 0) {
        errorMessage = L"无法用常见 UTF-8/中文/日文/韩文/西欧代码页无损解码这个文件。";
        return false;
    }

    text.swap(bestText);
    detectedEncoding = FindEncodingName(bestCodePage) + L"（自动猜测）";
    return true;
}

bool EncodeUtf8(
    const std::wstring& text,
    std::vector<unsigned char>& bytes,
    std::wstring& errorMessage) {

    bytes.clear();
    errorMessage.clear();

    if (!EncodeWideRaw(text, CP_UTF8, bytes)) {
        errorMessage = L"无法把当前结果编码成 UTF-8。文本中可能包含非法 UTF-16 数据。";
        return false;
    }

    return true;
}
