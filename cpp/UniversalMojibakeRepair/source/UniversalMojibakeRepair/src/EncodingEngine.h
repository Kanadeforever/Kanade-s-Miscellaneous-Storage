#pragma once

// 这个头文件只放“乱码恢复引擎”对外公开的数据结构和函数声明。
// 可以把它理解成一本目录：Main.cpp 只需要看这里，就知道引擎能提供哪些功能，
// 不需要知道 EncodingEngine.cpp 里面每一步具体是怎样实现的。

#include <string>
#include <vector>

// EncodingInfo 表示一种 Windows 代码页。
// codePage 是 Windows API 使用的数字编号，例如 UTF-8 是 65001、Shift-JIS 是 932。
// name 是显示给用户看的简体中文名称。
struct EncodingInfo {
    unsigned int codePage;
    std::wstring name;
};

// RepairStep 表示“一次可逆乱码恢复”。
// 例如：文字“æ—¥æœ¬èªž”本来是 UTF-8 字节，却曾经被当成 CP1252 解读；
// 那么 wrongDecoderCodePage=1252，trueEncodingCodePage=65001。
struct RepairStep {
    unsigned int wrongDecoderCodePage;
    unsigned int trueEncodingCodePage;
};

// RepairCandidate 表示自动分析得到的一条候选恢复路线。
// steps 最多保存两步，用来处理“乱码又被错误转码了一次”的双重乱码。
// score 是候选文本本身的可读性评分。
// improvement 是相对于原始输入提高了多少分；数值越大，通常越值得优先尝试。
struct RepairCandidate {
    std::vector<RepairStep> steps;
    double score;
    double improvement;
};

// 返回当前系统能够使用的常见文本编码。
// Windows 某些精简系统可能没有安装全部代码页，所以这里会主动过滤无效代码页。
std::vector<EncodingInfo> GetSupportedEncodings();

// 把一段已经显示成 Unicode 的乱码，按照“一次逆转码”规则尝试恢复。
// 成功时 result 得到恢复后的文字并返回 true；失败时 errorMessage 会解释失败原因。
bool RepairOnce(
    const std::wstring& input,
    unsigned int wrongDecoderCodePage,
    unsigned int trueEncodingCodePage,
    std::wstring& result,
    std::wstring& errorMessage);

// 按顺序执行多步恢复。
// 这主要给自动候选使用，例如先 CP1252→UTF-8，再 GBK→UTF-8。
bool ApplyRepairSteps(
    const std::wstring& input,
    const std::vector<RepairStep>& steps,
    std::wstring& result,
    std::wstring& errorMessage);

// 自动寻找最可能的乱码恢复方案。
// 函数不会对全文做成百上千次转码，而是先从全文抽样分析；
// 这样即使输入是一篇很长的文章，也不会因为候选组合太多而明显卡住。
std::vector<RepairCandidate> FindRepairCandidates(
    const std::wstring& input,
    std::size_t maximumCandidates = 12);

// 把一个恢复步骤转换成人能看懂的名称，例如“CP1252 → UTF-8”。
std::wstring DescribeRepairStep(const RepairStep& step);

// 把完整候选路线转换成说明文字。
// 双重乱码时会显示成“CP1252 → UTF-8；GBK → UTF-8”。
std::wstring DescribeCandidate(const RepairCandidate& candidate);

// 对任意字节流做一个“文本文件打开时的保守解码”。
// 优先识别 UTF-8/UTF-16 BOM；无 BOM 时先严格尝试 UTF-8，
// 然后再从常见东亚/西欧代码页里选择一个可逆且评分较好的结果。
bool DecodeTextFileBytes(
    const std::vector<unsigned char>& bytes,
    std::wstring& text,
    std::wstring& detectedEncoding,
    std::wstring& errorMessage);

// 把 Unicode 文本编码成 UTF-8 字节，供“保存结果”功能使用。
// 保存统一使用 UTF-8，不再把新文件写回容易混淆的本地 ANSI 代码页。
bool EncodeUtf8(
    const std::wstring& text,
    std::vector<unsigned char>& bytes,
    std::wstring& errorMessage);
