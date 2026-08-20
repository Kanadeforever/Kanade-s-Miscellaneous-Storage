#pragma once
#include "winmini.h"

// Explorer Rescue 最小运行时。正式 EXE 不链接 CRT/.NET/Qt。

extern "C" void* memcpy(void*, const void*, unsigned long long);
extern "C" void* memset(void*, int, unsigned long long);
extern "C" void* memmove(void*, const void*, unsigned long long);
extern "C" int memcmp(const void*, const void*, unsigned long long);

extern HANDLE g_heap;
extern WCHAR g_exePath[32768];
extern WCHAR g_appDir[32768];
extern WCHAR g_configPath[32768];
extern WCHAR g_sessionPath[32768];

struct AppConfig {
    // 后台两次自动扫描之间的间隔。这个值只决定“多久尝试一次”，不等于单轮扫描允许耗时。
    DWORD scanIntervalMs;

    // 单轮扫描的独立超时。超过这个时间只判定“这一轮无效”，不会永久停止后台保护。
    DWORD scanTimeoutMs;

    // 连续多少轮扫描失败以后才弹一次系统通知。成功一轮后，连续失败计数会自动清零。
    DWORD scanNotifyAfterFailures;

    // Windows 通知区域气泡/系统通知建议显示时长。
    DWORD notificationMs;
};

void RuntimeInit();
void* HAlloc(SIZE_T bytes, BOOL zero = TRUE);
void* HReAlloc(void* p, SIZE_T bytes, BOOL zero = FALSE);
void HFree(void* p);

UINT WLen(const WCHAR* s);
void WCopy(WCHAR* dst, UINT cap, const WCHAR* src);
void WAppend(WCHAR* dst, UINT cap, const WCHAR* src);
void WAppendU32(WCHAR* dst, UINT cap, DWORD value);
BOOL WEqual(const WCHAR* a, const WCHAR* b);
BOOL WEqualI(const WCHAR* a, const WCHAR* b);
BOOL WStartsWithI(const WCHAR* s, const WCHAR* prefix);
BOOL WContainsArg(const WCHAR* cmdLine, const WCHAR* arg);
void PathDirname(const WCHAR* path, WCHAR* out, UINT cap);
void PathJoin(const WCHAR* a, const WCHAR* b, WCHAR* out, UINT cap);
BOOL BuildAppPaths();
BOOL EnsurePortableFiles();
void LoadAppConfig(AppConfig* cfg);

struct ByteBuffer {
    BYTE* data;
    DWORD size;
    DWORD cap;
};
void BufferInit(ByteBuffer* b);
void BufferFree(ByteBuffer* b);
BOOL BufferReserve(ByteBuffer* b, DWORD extra);
BOOL BufferAppend(ByteBuffer* b, const void* data, DWORD bytes);
BOOL BufferAppendU32(ByteBuffer* b, DWORD v);
BOOL BufferAppendU64(ByteBuffer* b, ULONGLONG v);

ULONGLONG Hash64(const void* data, DWORD bytes);
BOOL WriteWholeFile(const WCHAR* path, const void* data, DWORD bytes, BOOL flush);
BOOL ReadWholeFile(const WCHAR* path, ByteBuffer* out, DWORD maxBytes);
BOOL FileExists(const WCHAR* path);
BOOL WriteFileIfChanged(const WCHAR* path, const void* data, DWORD bytes);

void ShowError(const WCHAR* text);
void ShowWarning(const WCHAR* text);
void ShowInfo(const WCHAR* text);
