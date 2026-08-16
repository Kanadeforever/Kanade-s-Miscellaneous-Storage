#pragma once
#include "core.h"

// 会话是“窗口 -> 有序标签页”层级结构；HWND 只在当前捕获阶段使用，不持久化。

enum LocationKind : DWORD {
    LOCATION_SHELL_PATH = 0,
    LOCATION_URL = 1,
};

struct TabState {
    DWORD kind;
    WCHAR* value;
};

struct WindowState {
    HWND sourceHwnd;
    BOOL hasPlacement;
    WINDOWPLACEMENT placement;
    BOOL hasScreenRect;
    RECT screenRect;
    TabState* tabs;
    DWORD tabCount;
    DWORD tabCap;
};

struct Snapshot {
    WindowState* windows;
    DWORD windowCount;
    DWORD windowCap;
    ULONGLONG contentHash;
};

void SnapshotInit(Snapshot* s);
void SnapshotFree(Snapshot* s);
WindowState* SnapshotFindWindowByHwnd(Snapshot* s, HWND hwnd);
WindowState* SnapshotAddWindow(Snapshot* s, HWND hwnd, const WINDOWPLACEMENT* wp, BOOL hasPlacement, const RECT* screenRect, BOOL hasScreenRect);
BOOL WindowAddTab(WindowState* w, DWORD kind, const WCHAR* value);
DWORD SnapshotTotalTabs(const Snapshot* s);

BOOL SerializeSnapshot(const Snapshot* s, ByteBuffer* out, ULONGLONG* outHash);
BOOL DeserializeSnapshot(const void* data, DWORD bytes, Snapshot* out, ULONGLONG* outHash);

// sessions.dat 单文件滚动历史：固定 5 个槽位，只保留完整有效记录。
// 完全相同的会话不会推进历史槽，减少磁盘写入并保留更多有意义的历史状态。
BOOL SaveSnapshotHistory(const Snapshot* s, BOOL* changed);
BOOL LoadLastSnapshot(Snapshot* out);
DWORD CountValidHistorySlots();
