#pragma once
#include "snapshot.h"
#include "ole_dispatch.h"

struct RestoreStats {
    DWORD expectedWindows;
    DWORD restoredWindows;
    DWORD expectedTabs;
    DWORD restoredTabs;
    DWORD failedWindows;
    DWORD failedTabs;
    DWORD validationFailures;
};

BOOL CaptureExplorerSnapshot(Snapshot* out);
BOOL CaptureExplorerSnapshotStable(Snapshot* out,DWORD timeoutMs);
BOOL ExplorerResponsive();
BOOL RestartExplorerShell();
BOOL RestoreExplorerSnapshot(const Snapshot* s,RestoreStats* stats);
BOOL IsExplorerWindowHwnd(HWND hwnd);
