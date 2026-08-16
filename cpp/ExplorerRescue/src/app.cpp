#include "explorer.h"

static_assert(sizeof(NOTIFYICONDATAW)==976, "NOTIFYICONDATAW x64 layout mismatch");

// x64 上 Interlocked* 属于编译器原子内在函数。
// 不从 KERNEL32 导入同名符号，避免某些环境在装载阶段解析到错误入口点。
extern "C" LONG _InterlockedCompareExchange(volatile LONG*, LONG, LONG);
extern "C" LONG _InterlockedExchange(volatile LONG*, LONG);
extern "C" LONG _InterlockedIncrement(volatile LONG*);
#pragma intrinsic(_InterlockedCompareExchange)
#pragma intrinsic(_InterlockedExchange)
#pragma intrinsic(_InterlockedIncrement)

/*
Explorer Rescue v0.2.6

运行语义：
- 第一次双击：启动后台守护并立即进行第一次完整扫描，不重启 Explorer。
- 后续默认每 5 秒尝试一次完整扫描；扫描间隔与扫描超时完全独立。
- 单轮瞬时读取失败只在本次扫描超时窗口内自动重试，不写入历史；只有持续无法形成稳定完整快照、真正超时或写盘失败，才暂停后续扫描并通知。
- 已在后台时再次运行同一个 EXE：执行“最终完整扫描 -> 重启 Explorer -> 恢复最近有效会话”。
- Win + Shift + E：与再次运行 EXE 完全相同。
- Explorer 已卡死时：不再强行做 COM 最终扫描，直接使用 sessions.dat 中最近一次完整有效扫描。
- 成功完成一次重启与完整恢复后自动恢复周期扫描。
- 托盘图标用于管理：双击执行救援；右键可立即扫描、重启恢复或退出。

持久文件严格只有：ExplorerRescue.exe / config.ini / sessions.dat。
*/

static const WCHAR kWindowClass[] = L"ExplorerRescue.HiddenWindow.v2";
static const WCHAR kMutexName[]   = L"Local\\ExplorerRescue.Daemon.v2";
static const int kHotkeyId = 0x4552;
static const UINT kTrayIconId = 1;
static const UINT kIconResourceId = 101;

static const UINT WM_ER_RESCUE      = WM_APP + 11;
static const UINT WM_ER_SCAN_DONE   = WM_APP + 12;
static const UINT WM_ER_SCAN_FAIL   = WM_APP + 13;
static const UINT WM_ER_NOTIFY      = WM_APP + 14;
static const UINT WM_ER_TRAY        = WM_APP + 15;
static const UINT WM_ER_TRAY_REFRESH= WM_APP + 16;

static const UINT_PTR TIMER_SCAN_INTERVAL = 1;
static const UINT_PTR TIMER_SCAN_INITIAL  = 2;
static const UINT_PTR TIMER_SCAN_WATCHDOG = 3;

static const UINT CMD_TRAY_RESCUE = 1001;
static const UINT CMD_TRAY_SCAN   = 1002;
static const UINT CMD_TRAY_EXIT   = 1003;

static HWND g_hwnd=0;
static HANDLE g_mutex=0;
static HICON g_trayIcon=0;
static BOOL g_trayAdded=FALSE;
static UINT g_taskbarCreated=0;
static volatile LONG g_scanBusy=0;       // 0=空闲；非 0=当前扫描令牌
static volatile LONG g_scanSerial=0;     // 单调扫描令牌，防止超时旧线程清掉新扫描状态
static volatile LONG g_scanPaused=0;
static volatile LONG g_rescueBusy=0;
static volatile LONG g_scanEpoch=0;
static AppConfig g_cfg;

static BOOL AtomicTrySet(volatile LONG*p){return _InterlockedCompareExchange(p,1,0)==0?TRUE:FALSE;}
static LONG AtomicExchange(volatile LONG*p,LONG v){return _InterlockedExchange(p,v);}
static LONG AtomicIncrement(volatile LONG*p){return _InterlockedIncrement(p);}

static void FillTrayIdentity(NOTIFYICONDATAW* nid){
    memset(nid,0,sizeof(*nid));nid->cbSize=sizeof(*nid);nid->hWnd=g_hwnd;nid->uID=kTrayIconId;
}

static void TrayTooltipText(WCHAR* out,UINT cap){
    if(g_rescueBusy)WCopy(out,cap,L"Explorer Rescue - 正在重启并恢复");
    else if(g_scanPaused)WCopy(out,cap,L"Explorer Rescue - 会话扫描已停止");
    else if(g_scanBusy)WCopy(out,cap,L"Explorer Rescue - 正在扫描 Explorer 会话");
    else WCopy(out,cap,L"Explorer Rescue - 正在保护 Explorer 会话");
}

static BOOL AddTrayIcon(){
    if(!g_hwnd)return FALSE;
    if(!g_trayIcon)g_trayIcon=LoadIconW((HINSTANCE)GetModuleHandleW(0),(LPCWSTR)(ULONG_PTR)kIconResourceId);
    NOTIFYICONDATAW nid;FillTrayIdentity(&nid);
    nid.uFlags=NIF_MESSAGE|NIF_ICON|NIF_TIP|NIF_SHOWTIP;
    nid.uCallbackMessage=WM_ER_TRAY;nid.hIcon=g_trayIcon;TrayTooltipText(nid.szTip,128);
    if(!Shell_NotifyIconW(NIM_ADD,&nid)){g_trayAdded=FALSE;return FALSE;}
    nid.uVersion=NOTIFYICON_VERSION_4;Shell_NotifyIconW(NIM_SETVERSION,&nid);
    g_trayAdded=TRUE;return TRUE;
}

static void RemoveTrayIcon(){
    if(!g_trayAdded||!g_hwnd)return;NOTIFYICONDATAW nid;FillTrayIdentity(&nid);Shell_NotifyIconW(NIM_DELETE,&nid);g_trayAdded=FALSE;
}

static void RefreshTrayIcon(){
    if(!g_hwnd)return;
    NOTIFYICONDATAW nid;FillTrayIdentity(&nid);nid.uFlags=NIF_TIP|NIF_SHOWTIP;TrayTooltipText(nid.szTip,128);
    if(!g_trayAdded||!Shell_NotifyIconW(NIM_MODIFY,&nid)){g_trayAdded=FALSE;AddTrayIcon();}
}

static void ShowSystemNotification(const WCHAR* detail){
    if(!g_trayAdded&&!AddTrayIcon()){ShowWarning(detail);return;}
    NOTIFYICONDATAW nid;FillTrayIdentity(&nid);nid.uFlags=NIF_INFO|NIF_REALTIME;
    WCopy(nid.szInfoTitle,64,L"Explorer Rescue");
    WCHAR text[256];text[0]=0;WAppend(text,256,L"会话扫描已停止，最近 5 个有效历史不会被覆盖。\r\n");WAppend(text,256,detail);WAppend(text,256,L"\r\n成功重启恢复后会自动继续扫描。");
    WCopy(nid.szInfo,256,text);nid.dwInfoFlags=NIIF_WARNING;nid.uTimeout=g_cfg.notificationMs;
    Shell_NotifyIconW(NIM_MODIFY,&nid);
}

static void ShowTrayMenu(){
    HMENU menu=CreatePopupMenu();if(!menu)return;
    AppendMenuW(menu,MF_STRING,CMD_TRAY_RESCUE,L"重启并恢复资源管理器");
    AppendMenuW(menu,(g_scanPaused||g_rescueBusy)?(MF_STRING|MF_GRAYED):MF_STRING,CMD_TRAY_SCAN,L"立即扫描并保存会话");
    AppendMenuW(menu,MF_SEPARATOR,0,0);
    if(g_rescueBusy)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：正在重启并恢复");
    else if(g_scanPaused)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：会话扫描已停止");
    else if(g_scanBusy)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：正在扫描");
    else AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：后台保护中");
    AppendMenuW(menu,MF_SEPARATOR,0,0);
    AppendMenuW(menu,g_rescueBusy?(MF_STRING|MF_GRAYED):MF_STRING,CMD_TRAY_EXIT,L"退出 Explorer Rescue");
    POINT pt;if(!GetCursorPos(&pt)){pt.x=0;pt.y=0;}
    SetForegroundWindow(g_hwnd);
    UINT cmd=TrackPopupMenuEx(menu,TPM_RIGHTBUTTON|TPM_RETURNCMD,(int)pt.x,(int)pt.y,g_hwnd,0);
    DestroyMenu(menu);PostMessageW(g_hwnd,WM_NULL,0,0);
    NOTIFYICONDATAW nid;FillTrayIdentity(&nid);Shell_NotifyIconW(NIM_SETFOCUS,&nid);
    if(cmd==CMD_TRAY_RESCUE)PostMessageW(g_hwnd,WM_ER_RESCUE,0,0);
    else if(cmd==CMD_TRAY_SCAN){
        // 由窗口过程所在主线程直接触发，函数本身只创建短命扫描线程。
        PostMessageW(g_hwnd,WM_TIMER,TIMER_SCAN_INTERVAL,0);
    }else if(cmd==CMD_TRAY_EXIT&&!g_rescueBusy)PostMessageW(g_hwnd,WM_CLOSE,0,0);
}

static void PauseScanning(const WCHAR*reason){
    if(AtomicExchange(&g_scanPaused,1))return;
    // 当前扫描一旦被判定为障碍，就把其令牌作废。旧 COM 线程即使稍后才返回，
    // 也不能阻塞成功救援后的新扫描，更不能清掉新扫描的 busy 状态。
    AtomicExchange(&g_scanBusy,0);AtomicIncrement(&g_scanEpoch);
    if(g_hwnd)PostMessageW(g_hwnd,WM_ER_NOTIFY,(WPARAM)reason,0);
}

static DWORD WINAPI PeriodicScanThread(LPVOID param){
    LONG token=(LONG)(LONG_PTR)param;LONG epoch=g_scanEpoch;Snapshot snap;SnapshotInit(&snap);
    BOOL ok=CaptureExplorerSnapshotStable(&snap,g_cfg.scanTimeoutMs);BOOL writeOk=TRUE;
    /* 0 个 Explorer 窗口不作为新的“有效会话”推进五槽历史，避免异常恢复把空状态压到最近记录。 */
    if(ok&&snap.windowCount>0&&!g_rescueBusy&&!g_scanPaused&&epoch==g_scanEpoch){BOOL changed=FALSE;writeOk=SaveSnapshotHistory(&snap,&changed);}
    SnapshotFree(&snap);_InterlockedCompareExchange(&g_scanBusy,0,token);
    if(g_hwnd&&epoch==g_scanEpoch&&!g_rescueBusy&&!g_scanPaused){
        if(ok&&writeOk)PostMessageW(g_hwnd,WM_ER_SCAN_DONE,0,0);
        else PostMessageW(g_hwnd,WM_ER_SCAN_FAIL,ok?2:1,0);
    }
    return 0;
}

static void StartPeriodicScan(){
    if(g_rescueBusy||g_scanPaused)return;
    LONG token=AtomicIncrement(&g_scanSerial);if(token==0)token=AtomicIncrement(&g_scanSerial);
    if(_InterlockedCompareExchange(&g_scanBusy,token,0)!=0)return;
    // Watchdog 只防 COM 调用真正挂死；比 CaptureExplorerSnapshotStable 自身超时多留 5 秒收尾余量，
    // 避免“工作线程刚在超时边界完成、UI watchdog 却先判失败”的竞态。
    if(g_hwnd){KillTimer(g_hwnd,TIMER_SCAN_WATCHDOG);SetTimer(g_hwnd,TIMER_SCAN_WATCHDOG,g_cfg.scanTimeoutMs+5000,0);PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);}
    HANDLE t=CreateThread(0,64*1024,PeriodicScanThread,(LPVOID)(LONG_PTR)token,STACK_SIZE_PARAM_IS_A_RESERVATION,0);
    if(t)CloseHandle(t);else{_InterlockedCompareExchange(&g_scanBusy,0,token);PauseScanning(L"无法创建会话扫描线程。");}
}

static BOOL WaitCurrentScanToFinish(DWORD timeoutMs){ULONGLONG start=GetTickCount64();while(g_scanBusy&&GetTickCount64()-start<timeoutMs)Sleep(30);return g_scanBusy?FALSE:TRUE;}

static BOOL HasAnyExplorerTopLevelWindow(){return FindWindowW(L"CabinetWClass",0)||FindWindowW(L"ExploreWClass",0);}

// 主动重启前使用“同一个 EXE 的一次性子进程”完成最终严格扫描。
// 子进程只会在扫描完整成功后写入 sessions.dat，不产生任何临时文件；超时可以安全终止子进程。
static BOOL RunFinalCaptureHelper(){
    UINT cap=WLen(g_exePath)+80;WCHAR*cmd=(WCHAR*)HAlloc(cap*sizeof(WCHAR),TRUE);if(!cmd)return FALSE;WAppend(cmd,cap,L"\"");WAppend(cmd,cap,g_exePath);WAppend(cmd,cap,L"\" /capture-once");
    STARTUPINFOW si;PROCESS_INFORMATION pi;memset(&si,0,sizeof(si));memset(&pi,0,sizeof(pi));si.cb=sizeof(si);
    BOOL started=CreateProcessW(g_exePath,cmd,0,0,FALSE,CREATE_NO_WINDOW|CREATE_UNICODE_ENVIRONMENT,0,g_appDir,&si,&pi);HFree(cmd);if(!started)return FALSE;CloseHandle(pi.hThread);
    DWORD waitMs=g_cfg.scanTimeoutMs+5000;DWORD wr=WaitForSingleObject(pi.hProcess,waitMs);if(wr==WAIT_TIMEOUT){TerminateProcess(pi.hProcess,9);WaitForSingleObject(pi.hProcess,1000);}DWORD code=9;GetExitCodeProcess(pi.hProcess,&code);CloseHandle(pi.hProcess);return wr!=WAIT_TIMEOUT&&code==0;
}

static void AppendRestoreStats(WCHAR*msg,UINT cap,const RestoreStats*st){WAppend(msg,cap,L"\r\n\r\n窗口：");WAppendU32(msg,cap,st->restoredWindows);WAppend(msg,cap,L" / ");WAppendU32(msg,cap,st->expectedWindows);WAppend(msg,cap,L"\r\n标签页：");WAppendU32(msg,cap,st->restoredTabs);WAppend(msg,cap,L" / ");WAppendU32(msg,cap,st->expectedTabs);if(st->validationFailures){WAppend(msg,cap,L"\r\n组合/路径校验失败：");WAppendU32(msg,cap,st->validationFailures);}}

static DWORD WINAPI RescueThreadProc(LPVOID){
    AtomicIncrement(&g_scanEpoch);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);
    BOOL wasResponsive=ExplorerResponsive();BOOL pausedAtStart=g_scanPaused?TRUE:FALSE;

    // 扫描已经因障碍暂停时，用户触发救援就是明确要求使用“最近一次有效扫描”，
    // 不再让一个已经卡住/失败的扫描阻止救援。
    BOOL preserveLastBecauseNoWindows=(wasResponsive&&CountValidHistorySlots()>0&&!HasAnyExplorerTopLevelWindow());
    if(wasResponsive&&!pausedAtStart&&!preserveLastBecauseNoWindows){
        if(!WaitCurrentScanToFinish(g_cfg.scanTimeoutMs+5000)){
            if(ExplorerResponsive()){PauseScanning(L"已有会话扫描超过允许时间，主动重启已取消。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;}
            wasResponsive=FALSE;
        }
        if(wasResponsive&&!RunFinalCaptureHelper()){
            if(ExplorerResponsive()){
                PauseScanning(L"无法完整取得重启前的最终窗口/标签信息，主动重启已取消。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
            }
        }
    }

    Snapshot snap;if(!LoadLastSnapshot(&snap)){
        ShowError(L"没有可验证的有效会话记录。为避免丢失窗口，本次没有结束 explorer.exe。\r\n\r\n请先让程序在 Explorer 正常时完成至少一次扫描。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
    }

    if(!RestartExplorerShell()){
        SnapshotFree(&snap);ShowError(L"Windows Explorer 没有在预期时间内重新启动。sessions.dat 中的有效历史没有被修改，可以再次执行救援。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
    }
    // Explorer 重启后旧通知区域图标已被 Shell 清空，主动要求主线程重新注册一次。
    if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,1,0);

    RestoreStats st;BOOL ok=RestoreExplorerSnapshot(&snap,&st);SnapshotFree(&snap);
    if(!ok){
        WCHAR msg[1024];msg[0]=0;WAppend(msg,1024,L"Explorer 已重新启动，但有窗口/标签组没有通过最终路径校验。\r\n失败标签不会被当作恢复成功，历史扫描也不会被新状态覆盖。");AppendRestoreStats(msg,1024,&st);ShowError(msg);AtomicExchange(&g_scanPaused,1);
    }else{
        AtomicExchange(&g_scanPaused,0);AtomicIncrement(&g_scanEpoch);
        if(g_hwnd){KillTimer(g_hwnd,TIMER_SCAN_INITIAL);SetTimer(g_hwnd,TIMER_SCAN_INITIAL,1800,0);}
    }
    AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
}

static void StartRescue(){if(!AtomicTrySet(&g_rescueBusy))return;if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);HANDLE t=CreateThread(0,128*1024,RescueThreadProc,0,STACK_SIZE_PARAM_IS_A_RESERVATION,0);if(t)CloseHandle(t);else{AtomicExchange(&g_rescueBusy,0);ShowError(L"无法启动 Explorer 救援线程。");}}

static LRESULT CALLBACK HiddenWndProc(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    if(g_taskbarCreated&&msg==g_taskbarCreated){g_trayAdded=FALSE;AddTrayIcon();return 0;}
    if(msg==WM_HOTKEY&&wp==(WPARAM)kHotkeyId){StartRescue();return 0;}
    if(msg==WM_ER_RESCUE){StartRescue();return 0;}
    if(msg==WM_ER_SCAN_DONE){KillTimer(hwnd,TIMER_SCAN_WATCHDOG);RefreshTrayIcon();return 0;}
    if(msg==WM_ER_SCAN_FAIL){
        KillTimer(hwnd,TIMER_SCAN_WATCHDOG);
        if(wp==2)PauseScanning(L"无法把完整会话写入 sessions.dat；历史记录未被覆盖。");
        else PauseScanning(L"本次 Explorer 会话枚举未能在独立超时内正常完成。最近有效历史未被覆盖。");
        return 0;
    }
    if(msg==WM_ER_NOTIFY){RefreshTrayIcon();ShowSystemNotification((const WCHAR*)wp);return 0;}
    if(msg==WM_ER_TRAY_REFRESH){
        if(wp){g_trayAdded=FALSE;AddTrayIcon();}else RefreshTrayIcon();return 0;
    }
    if(msg==WM_ER_TRAY){
        UINT ev=(UINT)((ULONG_PTR)lp&0xFFFFu);
        if(ev==WM_CONTEXTMENU){ShowTrayMenu();return 0;}
        if(ev==WM_LBUTTONDBLCLK){StartRescue();return 0;}
        return 0;
    }
    if(msg==WM_TIMER){
        if(wp==TIMER_SCAN_INTERVAL){StartPeriodicScan();return 0;}
        if(wp==TIMER_SCAN_INITIAL){KillTimer(hwnd,TIMER_SCAN_INITIAL);StartPeriodicScan();return 0;}
        if(wp==TIMER_SCAN_WATCHDOG){KillTimer(hwnd,TIMER_SCAN_WATCHDOG);if(g_scanBusy&&!g_rescueBusy)PauseScanning(L"本次完整扫描超过独立扫描超时时间，Explorer 可能已经部分无响应。");return 0;}
    }
    if(msg==WM_CLOSE){DestroyWindow(hwnd);return 0;}
    if(msg==WM_DESTROY){UnregisterHotKey(hwnd,kHotkeyId);KillTimer(hwnd,TIMER_SCAN_INTERVAL);KillTimer(hwnd,TIMER_SCAN_INITIAL);KillTimer(hwnd,TIMER_SCAN_WATCHDOG);RemoveTrayIcon();g_hwnd=0;PostQuitMessage(0);return 0;}
    return DefWindowProcW(hwnd,msg,wp,lp);
}

static HWND FindDaemon(){return FindWindowW(kWindowClass,L"ExplorerRescueDaemon");}
static BOOL SendDaemon(UINT msg){for(int i=0;i<30;++i){HWND w=FindDaemon();if(w)return PostMessageW(w,msg,0,0);Sleep(100);}return FALSE;}
static void CloseLegacyDaemon(){HWND old=FindWindowW(L"ExplorerRescue.HiddenWindow.v1",L"ExplorerRescueDaemon");if(!old)return;PostMessageW(old,WM_CLOSE,0,0);for(int i=0;i<20&&FindWindowW(L"ExplorerRescue.HiddenWindow.v1",L"ExplorerRescueDaemon");++i)Sleep(50);}

static int CaptureOnceMode(){
    AppConfig cfg;LoadAppConfig(&cfg);Snapshot s;SnapshotInit(&s);
    if(!CaptureExplorerSnapshotStable(&s,cfg.scanTimeoutMs))return 2;
    if(s.windowCount==0){SnapshotFree(&s);return 4;}
    BOOL changed=FALSE;BOOL saved=SaveSnapshotHistory(&s,&changed);SnapshotFree(&s);return saved?0:3;
}

static int RunDaemon(BOOL rescueImmediately){
    HINSTANCE inst=(HINSTANCE)GetModuleHandleW(0);WNDCLASSEXW wc;memset(&wc,0,sizeof(wc));wc.cbSize=sizeof(wc);wc.lpfnWndProc=HiddenWndProc;wc.hInstance=inst;wc.lpszClassName=kWindowClass;wc.hIcon=LoadIconW(inst,(LPCWSTR)(ULONG_PTR)kIconResourceId);wc.hIconSm=wc.hIcon;
    if(!RegisterClassExW(&wc)&&GetLastError()!=ERROR_ALREADY_EXISTS){ShowError(L"无法注册 Explorer Rescue 后台窗口类。");return 2;}
    g_hwnd=CreateWindowExW(0,kWindowClass,L"ExplorerRescueDaemon",0,0,0,0,0,0,0,inst,0);if(!g_hwnd){ShowError(L"无法创建 Explorer Rescue 后台消息窗口。");return 3;}
    g_taskbarCreated=RegisterWindowMessageW(L"TaskbarCreated");
    AddTrayIcon();
    if(!RegisterHotKey(g_hwnd,kHotkeyId,MOD_WIN|MOD_SHIFT|MOD_NOREPEAT,L'E'))ShowSystemNotification(L"Win + Shift + E 注册失败；仍可再次运行 ExplorerRescue.exe 或使用托盘菜单执行救援。");
    SetTimer(g_hwnd,TIMER_SCAN_INTERVAL,g_cfg.scanIntervalMs,0);
    // 首次运行立即扫描，不再先空等一轮；5 秒只是后续扫描频率，不是首次扫描延迟。
    StartPeriodicScan();
    if(rescueImmediately)PostMessageW(g_hwnd,WM_ER_RESCUE,0,0);
    MSG m;while(GetMessageW(&m,0,0,0)>0){TranslateMessage(&m);DispatchMessageW(&m);}return 0;
}

static int Main(){
    RuntimeInit();SetProcessDpiAwarenessContext((HANDLE)(LONG_PTR)-4);if(!BuildAppPaths()){ShowError(L"无法确定 ExplorerRescue.exe 所在目录。");return 10;}
    LPCWSTR cmd=GetCommandLineW();
    if(WContainsArg(cmd,L"/capture-once")){if(!FileExists(g_configPath)||!FileExists(g_sessionPath))return 11;return CaptureOnceMode();}
    if(!EnsurePortableFiles()){ShowError(L"Explorer Rescue 无法在 EXE 同目录创建 config.ini / sessions.dat。\r\n\r\n程序不会改用 AppData 或注册表，请把 EXE 放到当前用户可写目录后重新运行。");return 12;}
    LoadAppConfig(&g_cfg);
    if(WContainsArg(cmd,L"/exit")){SendDaemon(WM_CLOSE);return 0;}

    CloseLegacyDaemon();
    g_mutex=CreateMutexW(0,FALSE,kMutexName);BOOL exists=(g_mutex&&GetLastError()==ERROR_ALREADY_EXISTS);
    if(exists){SendDaemon(WM_ER_RESCUE);if(g_mutex)CloseHandle(g_mutex);return 0;}

    BOOL rescueImmediately=FALSE;if(CountValidHistorySlots()>0&&!ExplorerResponsive())rescueImmediately=TRUE;
    int r=RunDaemon(rescueImmediately);if(g_mutex)CloseHandle(g_mutex);return r;
}

extern "C" VOID WINAPI ExplorerRescueEntry(){int code=Main();ExitProcess((UINT)code);}
