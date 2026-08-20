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
Explorer Rescue v0.2.11

运行语义：
- 第一次双击：启动后台守护并立即进行第一次完整扫描，不重启 Explorer。
- 后续默认每 5 秒尝试一次完整扫描；扫描间隔与扫描超时完全独立。
- 单轮扫描失败或超时只丢弃这一轮，绝不覆盖最近 5 份有效历史；后台会继续按周期自动重试。
- 连续失败达到配置阈值后只通知一次；只要后续有一轮成功，连续失败计数立即清零。
- 如果某个 COM 扫描线程真的卡死，主进程不会继续堆叠扫描线程；后续自动重试临时切换为可超时终止的一次性同 EXE 子进程，直到原线程自行返回。
- 已在后台时再次运行同一个 EXE：执行“最终完整扫描 -> 重启 Explorer -> 恢复最近有效会话”。
- Win + Shift + E：与再次运行 EXE 完全相同。
- Explorer 已卡死时：不再强行做 COM 最终扫描，直接使用 sessions.dat 中最近一次完整有效扫描。
- 成功完成一次重启与完整恢复后自动恢复周期扫描。
- 托盘图标只用于管理本软件：双击或右键“重启 Explorer Rescue”会重启守护程序；Explorer 救援继续由 Win + Shift + E 或再次运行 EXE 触发。

持久文件严格只有：ExplorerRescue.exe / config.ini / sessions.dat。
*/

static const WCHAR kWindowClass[] = L"ExplorerRescue.HiddenWindow.v2";
static const WCHAR kMutexName[]   = L"Local\\ExplorerRescue.Daemon.v2";
// 所有进程写 sessions.dat 前都先取得这个命名互斥量，避免隔离扫描子进程与主守护同时覆盖会话文件。
static const WCHAR kSessionWriteMutexName[] = L"Local\\ExplorerRescue.SessionWrite.v1";
static const int kHotkeyId = 0x4552;
static const UINT kTrayIconId = 1;
static const UINT kIconResourceId = 101;

static const UINT WM_ER_RESCUE      = WM_APP + 11;
static const UINT WM_ER_SCAN_DONE   = WM_APP + 12;
static const UINT WM_ER_SCAN_FAIL   = WM_APP + 13;
static const UINT WM_ER_TRAY        = WM_APP + 15;
static const UINT WM_ER_TRAY_REFRESH= WM_APP + 16;

static const UINT_PTR TIMER_SCAN_INTERVAL = 1;
static const UINT_PTR TIMER_SCAN_INITIAL  = 2;
static const UINT_PTR TIMER_SCAN_WATCHDOG = 3;

static const UINT CMD_TRAY_RESTART = 1001;
static const UINT CMD_TRAY_SCAN    = 1002;
static const UINT CMD_TRAY_EXIT    = 1003;

static HWND g_hwnd=0;
static HANDLE g_mutex=0;
static HICON g_trayIcon=0;
static BOOL g_trayAdded=FALSE;
static UINT g_taskbarCreated=0;
static volatile LONG g_scanBusy=0;       // 0=空闲；非 0=当前扫描令牌
static volatile LONG g_scanSerial=0;     // 单调扫描令牌，防止超时旧线程清掉新扫描状态
// g_scanPaused 只用于“恢复结果本身失败”的保护性暂停。
// 普通后台扫描超时/失败不会再把它置 1，因为那种故障只应该影响当前这一轮。
static volatile LONG g_scanPaused=0;
static volatile LONG g_rescueBusy=0;
static volatile LONG g_scanEpoch=0;

// 连续扫描失败计数只负责通知节流。成功一轮后会立刻清零。
static volatile LONG g_scanFailureCount=0;
static volatile LONG g_scanFailureNotified=0;

// 如果正常 COM 扫描线程超过 watchdog，这里记录“被判超时的扫描令牌”。
// 线程本身不能被粗暴 TerminateThread，因此允许它自行返回；在此期间后续重试改用子进程隔离。
static volatile LONG g_scanTimedOutToken=0;
static volatile LONG g_fallbackScanBusy=0;

// 托盘请求“重启 Explorer Rescue”时只设置这个标志并正常退出消息循环。
// Main() 在释放单实例互斥量以后再启动新实例，避免新实例误认为旧守护仍存在。
static volatile LONG g_restartSelfRequested=0;
// 普通退出若恰逢隔离扫描子进程运行，也先等该子进程结束/超时回收，避免留下孤儿扫描进程。
static volatile LONG g_exitRequested=0;

static AppConfig g_cfg;

static BOOL AtomicTrySet(volatile LONG*p){return _InterlockedCompareExchange(p,1,0)==0?TRUE:FALSE;}
static LONG AtomicExchange(volatile LONG*p,LONG v){return _InterlockedExchange(p,v);}
static LONG AtomicIncrement(volatile LONG*p){return _InterlockedIncrement(p);}

/*
跨进程写入锁。

为什么需要它：v0.2.11 在正常 COM 线程真正超时后，会临时启动同一个 EXE 的 /capture-once 子进程。
如果此时用户又触发 Explorer 救援，理论上可能出现“子进程准备写 sessions.dat”与“主进程开始重启 Explorer”重叠。
命名互斥量把所有会话文件写入串行化，并允许救援阶段在真正杀 Explorer 前暂时锁住写入通道。

这不是持久文件，也不会写注册表；所有持有者退出后 Windows 自动销毁内核对象。
*/
static HANDLE AcquireSessionWriteLock(DWORD timeoutMs){
    HANDLE m=CreateMutexW(0,FALSE,kSessionWriteMutexName);
    if(!m)return 0;
    DWORD wr=WaitForSingleObject(m,timeoutMs);
    if(wr==WAIT_OBJECT_0||wr==WAIT_ABANDONED)return m;
    CloseHandle(m);
    return 0;
}

static void ReleaseSessionWriteLock(HANDLE m){
    if(!m)return;
    ReleaseMutex(m);
    CloseHandle(m);
}

static BOOL SaveSnapshotHistoryLocked(const Snapshot* snap,BOOL* changed){
    /*
    普通扫描写入只短暂等待 1 秒。
    如果 Explorer 救援正持有这把锁，说明 Shell 已进入“重启/恢复事务”，旧扫描应该直接放弃，
    而不是一直等到恢复结束后再把已经过时的快照写回历史。
    */
    HANDLE m=AcquireSessionWriteLock(1000);
    if(!m)return FALSE;
    BOOL ok=SaveSnapshotHistory(snap,changed);
    ReleaseSessionWriteLock(m);
    return ok;
}

static void FillTrayIdentity(NOTIFYICONDATAW* nid){
    memset(nid,0,sizeof(*nid));nid->cbSize=sizeof(*nid);nid->hWnd=g_hwnd;nid->uID=kTrayIconId;
}

static void TrayTooltipText(WCHAR* out,UINT cap){
    if(g_rescueBusy)WCopy(out,cap,L"Explorer Rescue - 正在重启并恢复");
    else if(g_restartSelfRequested)WCopy(out,cap,L"Explorer Rescue - 正在等待扫描结束后重启本软件");
    else if(g_exitRequested)WCopy(out,cap,L"Explorer Rescue - 正在等待扫描结束后退出");
    else if(g_scanPaused)WCopy(out,cap,L"Explorer Rescue - 恢复失败，扫描保护性暂停");
    else if(g_scanTimedOutToken)WCopy(out,cap,L"Explorer Rescue - 扫描超时，正在隔离自动重试");
    else if(g_scanBusy)WCopy(out,cap,L"Explorer Rescue - 正在扫描 Explorer 会话");
    else if(g_scanFailureCount>0)WCopy(out,cap,L"Explorer Rescue - 最近扫描失败，正在自动重试");
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
    WCopy(nid.szInfo,256,detail);nid.dwInfoFlags=NIIF_WARNING;nid.uTimeout=g_cfg.notificationMs;
    Shell_NotifyIconW(NIM_MODIFY,&nid);
}

/*
普通扫描失败不再永久停扫。这里仅做两件事：
1. 递增“连续失败次数”；
2. 达到 config.ini 的阈值以后只通知一次。
后续任何一次成功扫描都会调用 ResetScanFailureState() 清零，所以新的故障周期仍然能再次通知。
*/
static void ResetScanFailureState(){
    AtomicExchange(&g_scanFailureCount,0);
    AtomicExchange(&g_scanFailureNotified,0);
}

static void RecordScanFailure(const WCHAR* reason){
    LONG count=AtomicIncrement(&g_scanFailureCount);
    RefreshTrayIcon();
    if((DWORD)count<g_cfg.scanNotifyAfterFailures)return;
    if(AtomicExchange(&g_scanFailureNotified,1))return;

    WCHAR text[256];text[0]=0;
    WAppend(text,256,L"Explorer 会话已连续扫描失败 ");
    WAppendU32(text,256,(DWORD)count);
    WAppend(text,256,L" 次。程序会继续自动重试，最近 5 个有效历史不会被覆盖。\r\n");
    WAppend(text,256,reason);
    ShowSystemNotification(text);
}

static void RequestSelfRestart(){
    // 不在旧守护仍持有单实例互斥量时直接 CreateProcess。
    // 如果隔离扫描子进程还在运行，先让它按既定 timeout 正常结束/被回收，避免旧守护退出后留下孤儿子进程。
    if(g_rescueBusy)return;
    AtomicExchange(&g_restartSelfRequested,1);
    RefreshTrayIcon();
    if(g_fallbackScanBusy)return;
    if(g_hwnd)PostMessageW(g_hwnd,WM_CLOSE,0,0);
}

static void RequestExit(){
    if(g_rescueBusy)return;
    AtomicExchange(&g_exitRequested,1);
    RefreshTrayIcon();
    if(g_fallbackScanBusy)return;
    if(g_hwnd)PostMessageW(g_hwnd,WM_CLOSE,0,0);
}

static void ShowTrayMenu(){
    HMENU menu=CreatePopupMenu();if(!menu)return;
    AppendMenuW(menu,g_rescueBusy?(MF_STRING|MF_GRAYED):MF_STRING,CMD_TRAY_RESTART,L"重启 Explorer Rescue");
    AppendMenuW(menu,(g_scanPaused||g_rescueBusy)?(MF_STRING|MF_GRAYED):MF_STRING,CMD_TRAY_SCAN,L"立即扫描并保存会话");
    AppendMenuW(menu,MF_SEPARATOR,0,0);
    if(g_rescueBusy)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：正在重启并恢复 Explorer");
    else if(g_restartSelfRequested)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：等待隔离扫描结束后重启本软件");
    else if(g_exitRequested)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：等待隔离扫描结束后退出");
    else if(g_scanPaused)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：恢复失败，扫描保护性暂停");
    else if(g_scanTimedOutToken)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：扫描超时，正在隔离自动重试");
    else if(g_scanBusy)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：正在扫描");
    else if(g_scanFailureCount>0)AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：扫描异常，正在自动重试");
    else AppendMenuW(menu,MF_STRING|MF_GRAYED,0,L"状态：后台保护中");
    AppendMenuW(menu,MF_SEPARATOR,0,0);
    AppendMenuW(menu,g_rescueBusy?(MF_STRING|MF_GRAYED):MF_STRING,CMD_TRAY_EXIT,L"退出 Explorer Rescue");
    POINT pt;if(!GetCursorPos(&pt)){pt.x=0;pt.y=0;}
    SetForegroundWindow(g_hwnd);
    UINT cmd=TrackPopupMenuEx(menu,TPM_RIGHTBUTTON|TPM_RETURNCMD,(int)pt.x,(int)pt.y,g_hwnd,0);
    DestroyMenu(menu);PostMessageW(g_hwnd,WM_NULL,0,0);
    NOTIFYICONDATAW nid;FillTrayIdentity(&nid);Shell_NotifyIconW(NIM_SETFOCUS,&nid);
    if(cmd==CMD_TRAY_RESTART)RequestSelfRestart();
    else if(cmd==CMD_TRAY_SCAN){
        // 立即扫描仍走与周期扫描完全相同的安全路径，不会绕过历史保护规则。
        PostMessageW(g_hwnd,WM_TIMER,TIMER_SCAN_INTERVAL,0);
    }else if(cmd==CMD_TRAY_EXIT&&!g_rescueBusy)RequestExit();
}

// 后面两个线程函数需要调用位于文件稍后位置的同 EXE 扫描子进程帮助函数。
static DWORD RunCaptureHelperCode();

static DWORD WINAPI PeriodicScanThread(LPVOID param){
    LONG token=(LONG)(LONG_PTR)param;
    LONG epoch=g_scanEpoch;
    Snapshot snap;SnapshotInit(&snap);

    BOOL ok=CaptureExplorerSnapshotStable(&snap,g_cfg.scanTimeoutMs);
    BOOL writeOk=TRUE;

    /*
    watchdog 如果已经把这个 token 标成“超时”，这份扫描即使后来自己返回，也必须丢弃。
    原因很简单：它代表的是二十多秒前开始读取的旧现场，不能在更晚时间反过来覆盖新历史。
    */
    BOOL timedOut=(g_scanTimedOutToken==token);
    if(ok&&!timedOut&&snap.windowCount>0&&!g_rescueBusy&&!g_scanPaused&&epoch==g_scanEpoch){
        BOOL changed=FALSE;
        writeOk=SaveSnapshotHistoryLocked(&snap,&changed);
    }

    SnapshotFree(&snap);

    // 只有拥有相同 token 的线程才能清掉 busy；这样旧线程不会清掉后来扫描的状态。
    _InterlockedCompareExchange(&g_scanBusy,0,token);

    // 如果这正是曾经超时的旧线程，现在它终于自行返回，可以退出“子进程隔离重试”模式。
    if(timedOut)_InterlockedCompareExchange(&g_scanTimedOutToken,0,token);

    if(g_hwnd&&!timedOut&&epoch==g_scanEpoch&&!g_rescueBusy&&!g_scanPaused){
        if(ok&&writeOk)PostMessageW(g_hwnd,WM_ER_SCAN_DONE,0,0);
        else PostMessageW(g_hwnd,WM_ER_SCAN_FAIL,ok?2:1,0);
    }
    return 0;
}

/*
正常 COM 工作线程已经超过 watchdog 后，不能每 5 秒再创建一个新的 COM 线程：
如果 Explorer 真卡在某个 Shell 扩展里，那会不断堆积永久卡住的线程。

因此在旧线程自行返回之前，后续自动重试改用同一个 ExplorerRescue.exe 的 /capture-once 子进程。
子进程如果也卡住，可以由父进程 WaitForSingleObject 超时后安全 TerminateProcess；
这样主守护的线程数始终有上限，同时仍满足“失败后继续自动重试”。
*/
static DWORD WINAPI FallbackScanThread(LPVOID){
    DWORD code=RunCaptureHelperCode();
    AtomicExchange(&g_fallbackScanBusy,0);

    // 托盘“重启本软件/退出”如果正在等待这个隔离子进程，到这里再安全关闭守护。
    if(g_hwnd&&(g_restartSelfRequested||g_exitRequested)){PostMessageW(g_hwnd,WM_CLOSE,0,0);return 0;}
    if(!g_hwnd||g_rescueBusy||g_scanPaused)return 0;

    // /capture-once 返回 4 表示当前确实没有 Explorer 窗口；这不是扫描故障，也不会写空历史。
    if(code==0||code==4)PostMessageW(g_hwnd,WM_ER_SCAN_DONE,1,0);
    else PostMessageW(g_hwnd,WM_ER_SCAN_FAIL,code==3?4:3,0);
    return 0;
}

static void StartFallbackScan(){
    if(g_rescueBusy||g_scanPaused)return;
    if(!AtomicTrySet(&g_fallbackScanBusy))return;

    HANDLE t=CreateThread(0,64*1024,FallbackScanThread,0,STACK_SIZE_PARAM_IS_A_RESERVATION,0);
    if(t){
        CloseHandle(t);
        return;
    }

    AtomicExchange(&g_fallbackScanBusy,0);
    RecordScanFailure(L"无法创建隔离重试线程。下一周期仍会继续尝试。");
}

static void StartPeriodicScan(){
    if(g_rescueBusy||g_scanPaused)return;

    LONG busy=g_scanBusy;
    if(busy){
        // 正常扫描仍在规定时间内运行时，只跳过本周期，避免重叠扫描。
        if(g_scanTimedOutToken!=busy)return;

        // 这个正常扫描已经超时但线程尚未返回：使用可终止的子进程继续自动重试。
        StartFallbackScan();
        return;
    }

    LONG token=AtomicIncrement(&g_scanSerial);
    if(token==0)token=AtomicIncrement(&g_scanSerial);
    if(_InterlockedCompareExchange(&g_scanBusy,token,0)!=0)return;

    /*
    scanTimeoutMs 就是这一轮真正的 watchdog 时间，不再额外附加“永久停扫”语义。
    一旦超时，主线程只把该 token 标记为废弃；旧线程继续自行退出，下一周期走隔离重试。
    */
    if(g_hwnd){
        KillTimer(g_hwnd,TIMER_SCAN_WATCHDOG);
        SetTimer(g_hwnd,TIMER_SCAN_WATCHDOG,g_cfg.scanTimeoutMs,0);
        PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);
    }

    HANDLE t=CreateThread(0,64*1024,PeriodicScanThread,(LPVOID)(LONG_PTR)token,STACK_SIZE_PARAM_IS_A_RESERVATION,0);
    if(t){
        CloseHandle(t);
    }else{
        _InterlockedCompareExchange(&g_scanBusy,0,token);
        RecordScanFailure(L"无法创建会话扫描线程。下一周期仍会继续尝试。");
    }
}

static BOOL WaitCurrentScanToFinish(DWORD timeoutMs){ULONGLONG start=GetTickCount64();while(g_scanBusy&&GetTickCount64()-start<timeoutMs)Sleep(30);return g_scanBusy?FALSE:TRUE;}

static BOOL HasAnyExplorerTopLevelWindow(){return FindWindowW(L"CabinetWClass",0)||FindWindowW(L"ExploreWClass",0);}

// 使用“同一个 EXE 的一次性子进程”完成可隔离、可超时终止的扫描。
// 这个帮助函数既服务于主动重启前的最终严格扫描，也服务于普通扫描线程超时后的自动隔离重试。
static DWORD RunCaptureHelperCode(){
    UINT cap=WLen(g_exePath)+80;
    WCHAR*cmd=(WCHAR*)HAlloc(cap*sizeof(WCHAR),TRUE);
    if(!cmd)return 100;

    WAppend(cmd,cap,L"\"");
    WAppend(cmd,cap,g_exePath);
    WAppend(cmd,cap,L"\" /capture-once");

    STARTUPINFOW si;PROCESS_INFORMATION pi;
    memset(&si,0,sizeof(si));memset(&pi,0,sizeof(pi));si.cb=sizeof(si);
    BOOL started=CreateProcessW(g_exePath,cmd,0,0,FALSE,CREATE_NO_WINDOW|CREATE_UNICODE_ENVIRONMENT,0,g_appDir,&si,&pi);
    HFree(cmd);
    if(!started)return 101;
    CloseHandle(pi.hThread);

    DWORD wr=WaitForSingleObject(pi.hProcess,g_cfg.scanTimeoutMs);
    if(wr==WAIT_TIMEOUT){
        // 子进程只负责一次扫描，没有任何长期状态，因此超时后终止整个子进程比杀线程安全得多。
        TerminateProcess(pi.hProcess,9);
        WaitForSingleObject(pi.hProcess,1000);
        CloseHandle(pi.hProcess);
        return 102;
    }

    DWORD code=9;
    GetExitCodeProcess(pi.hProcess,&code);
    CloseHandle(pi.hProcess);
    return code;
}

static BOOL RunFinalCaptureHelper(){return RunCaptureHelperCode()==0;}

static void AppendRestoreStats(WCHAR*msg,UINT cap,const RestoreStats*st){WAppend(msg,cap,L"\r\n\r\n窗口：");WAppendU32(msg,cap,st->restoredWindows);WAppend(msg,cap,L" / ");WAppendU32(msg,cap,st->expectedWindows);WAppend(msg,cap,L"\r\n标签页：");WAppendU32(msg,cap,st->restoredTabs);WAppend(msg,cap,L" / ");WAppendU32(msg,cap,st->expectedTabs);if(st->validationFailures){WAppend(msg,cap,L"\r\n组合/路径校验失败：");WAppendU32(msg,cap,st->validationFailures);}}

static DWORD WINAPI RescueThreadProc(LPVOID){
    AtomicIncrement(&g_scanEpoch);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);
    BOOL wasResponsive=ExplorerResponsive();BOOL pausedAtStart=g_scanPaused?TRUE:FALSE;

    // 扫描已经因障碍暂停时，用户触发救援就是明确要求使用“最近一次有效扫描”，
    // 不再让一个已经卡住/失败的扫描阻止救援。
    BOOL preserveLastBecauseNoWindows=(wasResponsive&&CountValidHistorySlots()>0&&!HasAnyExplorerTopLevelWindow());
    if(wasResponsive&&!pausedAtStart&&!preserveLastBecauseNoWindows){
        /*
        如果后台扫描已经被 watchdog 判超时，就不再等待那个可能卡在 COM 里的旧线程。
        主动救援直接使用可终止的 /capture-once 子进程做最后扫描；这样后台保护故障不会反过来阻止用户救援。
        */
        LONG busyToken=g_scanBusy;
        BOOL currentScanAlreadyTimedOut=(busyToken&&g_scanTimedOutToken==busyToken);
        if(!currentScanAlreadyTimedOut&&!WaitCurrentScanToFinish(g_cfg.scanTimeoutMs)){
            if(ExplorerResponsive()){
                /*
                主动重启必须取消，因为此刻拿不到可信的“最后现场”；
                但这仍然只是一次扫描故障，不再永久暂停后台保护。
                同时把当前 token 标记为超时，让后续周期自动切换到隔离重试。
                */
                LONG token=g_scanBusy;if(token)AtomicExchange(&g_scanTimedOutToken,token);
                if(g_hwnd)PostMessageW(g_hwnd,WM_ER_SCAN_FAIL,3,0);
                AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
            }
            wasResponsive=FALSE;
        }
        if(wasResponsive&&!RunFinalCaptureHelper()){
            if(ExplorerResponsive()){
                // 最终严格扫描失败：取消本次主动重启，但后台扫描仍会继续重试。
                if(g_hwnd)PostMessageW(g_hwnd,WM_ER_SCAN_FAIL,3,0);
                AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
            }
        }
    }

    Snapshot snap;if(!LoadLastSnapshot(&snap)){
        ShowError(L"没有可验证的有效会话记录。为避免丢失窗口，本次没有结束 explorer.exe。\r\n\r\n请先让程序在 Explorer 正常时完成至少一次扫描。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
    }

    /*
    从这一刻开始直到恢复结束，禁止任何 /capture-once 子进程写 sessions.dat。
    这样即使后台刚好处于隔离重试，也不可能把“Explorer 重启中/只恢复了一半”的瞬态现场写进历史。
    */
    HANDLE sessionWriteLock=AcquireSessionWriteLock(g_cfg.scanTimeoutMs);
    if(!sessionWriteLock){
        SnapshotFree(&snap);
        ShowError(L"无法取得会话文件写入保护锁。为避免历史与恢复过程竞争，本次没有结束 explorer.exe。");
        AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
    }

    if(!RestartExplorerShell()){
        ReleaseSessionWriteLock(sessionWriteLock);
        SnapshotFree(&snap);ShowError(L"Windows Explorer 没有在预期时间内重新启动。sessions.dat 中的有效历史没有被修改，可以再次执行救援。");AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
    }
    // Explorer 重启后旧通知区域图标已被 Shell 清空，主动要求主线程重新注册一次。
    if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,1,0);

    RestoreStats st;BOOL ok=RestoreExplorerSnapshot(&snap,&st);SnapshotFree(&snap);
    // 恢复已经结束；从现在开始允许新的完整扫描写入历史。
    ReleaseSessionWriteLock(sessionWriteLock);
    if(!ok){
        WCHAR msg[1024];msg[0]=0;WAppend(msg,1024,L"Explorer 已重新启动，但有窗口/标签组没有通过最终路径校验。\r\n失败标签不会被当作恢复成功，历史扫描也不会被新状态覆盖。");AppendRestoreStats(msg,1024,&st);ShowError(msg);AtomicExchange(&g_scanPaused,1);
    }else{
        AtomicExchange(&g_scanPaused,0);
        AtomicIncrement(&g_scanEpoch);
        ResetScanFailureState();
        if(g_hwnd){KillTimer(g_hwnd,TIMER_SCAN_INITIAL);SetTimer(g_hwnd,TIMER_SCAN_INITIAL,1800,0);}
    }
    AtomicExchange(&g_rescueBusy,0);if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);return 0;
}

static void StartRescue(){if(!AtomicTrySet(&g_rescueBusy))return;if(g_hwnd)PostMessageW(g_hwnd,WM_ER_TRAY_REFRESH,0,0);HANDLE t=CreateThread(0,128*1024,RescueThreadProc,0,STACK_SIZE_PARAM_IS_A_RESERVATION,0);if(t)CloseHandle(t);else{AtomicExchange(&g_rescueBusy,0);ShowError(L"无法启动 Explorer 救援线程。");}}

static LRESULT CALLBACK HiddenWndProc(HWND hwnd,UINT msg,WPARAM wp,LPARAM lp){
    if(g_taskbarCreated&&msg==g_taskbarCreated){g_trayAdded=FALSE;AddTrayIcon();return 0;}
    if(msg==WM_HOTKEY&&wp==(WPARAM)kHotkeyId){StartRescue();return 0;}
    if(msg==WM_ER_RESCUE){StartRescue();return 0;}
    if(msg==WM_ER_SCAN_DONE){
        // wp==0 来自正常线程；wp==1 来自超时后的隔离子进程。只有正常线程拥有 watchdog。
        if(wp==0)KillTimer(hwnd,TIMER_SCAN_WATCHDOG);
        ResetScanFailureState();
        RefreshTrayIcon();
        return 0;
    }
    if(msg==WM_ER_SCAN_FAIL){
        if(wp==1||wp==2)KillTimer(hwnd,TIMER_SCAN_WATCHDOG);

        // 普通扫描失败只记一次失败并继续自动重试，绝不把 g_scanPaused 置 1。
        if(wp==2)RecordScanFailure(L"本轮无法写入 sessions.dat；本轮已丢弃，下一周期会继续尝试。");
        else if(wp==4)RecordScanFailure(L"隔离重试无法写入 sessions.dat；有效历史仍保持不变。");
        else if(wp==3)RecordScanFailure(L"隔离扫描未能完成；下一周期仍会自动重试。");
        else RecordScanFailure(L"本轮 Explorer 会话枚举失败；下一周期仍会自动重试。");
        return 0;
    }
    if(msg==WM_ER_TRAY_REFRESH){
        if(wp){g_trayAdded=FALSE;AddTrayIcon();}else RefreshTrayIcon();return 0;
    }
    if(msg==WM_ER_TRAY){
        UINT ev=(UINT)((ULONG_PTR)lp&0xFFFFu);
        if(ev==WM_CONTEXTMENU){ShowTrayMenu();return 0;}
        // 托盘不再承担 Explorer 救援入口。双击托盘统一解释为“重启本软件”。
        if(ev==WM_LBUTTONDBLCLK){RequestSelfRestart();return 0;}
        return 0;
    }
    if(msg==WM_TIMER){
        if(wp==TIMER_SCAN_INTERVAL){StartPeriodicScan();return 0;}
        if(wp==TIMER_SCAN_INITIAL){KillTimer(hwnd,TIMER_SCAN_INITIAL);StartPeriodicScan();return 0;}
        if(wp==TIMER_SCAN_WATCHDOG){
            KillTimer(hwnd,TIMER_SCAN_WATCHDOG);
            LONG token=g_scanBusy;
            if(token&&!g_rescueBusy&&g_scanTimedOutToken!=token){
                /*
                这里只把当前 token 标成“废弃”，不终止线程、不暂停守护。
                下一周期看到这个标记后会自动改走 /capture-once 子进程，直到旧线程自行返回。
                */
                AtomicExchange(&g_scanTimedOutToken,token);
                RecordScanFailure(L"本轮扫描超过独立超时，已丢弃本轮并切换为隔离自动重试。");
            }
            return 0;
        }
    }
    if(msg==WM_CLOSE){
        // 隔离扫描子进程由当前守护负责超时回收；先等它结束，避免守护先退出留下孤儿 /capture-once。
        if(g_fallbackScanBusy){if(!g_restartSelfRequested)AtomicExchange(&g_exitRequested,1);RefreshTrayIcon();return 0;}
        DestroyWindow(hwnd);return 0;
    }
    if(msg==WM_DESTROY){UnregisterHotKey(hwnd,kHotkeyId);KillTimer(hwnd,TIMER_SCAN_INTERVAL);KillTimer(hwnd,TIMER_SCAN_INITIAL);KillTimer(hwnd,TIMER_SCAN_WATCHDOG);RemoveTrayIcon();g_hwnd=0;PostQuitMessage(0);return 0;}
    return DefWindowProcW(hwnd,msg,wp,lp);
}

static HWND FindDaemon(){return FindWindowW(kWindowClass,L"ExplorerRescueDaemon");}
static BOOL SendDaemon(UINT msg){for(int i=0;i<30;++i){HWND w=FindDaemon();if(w)return PostMessageW(w,msg,0,0);Sleep(100);}return FALSE;}
static void CloseLegacyDaemon(){HWND old=FindWindowW(L"ExplorerRescue.HiddenWindow.v1",L"ExplorerRescueDaemon");if(!old)return;PostMessageW(old,WM_CLOSE,0,0);for(int i=0;i<20&&FindWindowW(L"ExplorerRescue.HiddenWindow.v1",L"ExplorerRescueDaemon");++i)Sleep(50);}

static int CaptureOnceMode(){
    AppConfig cfg;LoadAppConfig(&cfg);
    // /capture-once 走在 Main() 常规 LoadAppConfig(&g_cfg) 之前，因此这里同步全局配置，
    // 让跨进程 sessions.dat 写锁也能使用正确的 TimeoutMilliseconds。
    g_cfg=cfg;
    Snapshot s;SnapshotInit(&s);
    if(!CaptureExplorerSnapshotStable(&s,cfg.scanTimeoutMs))return 2;
    if(s.windowCount==0){SnapshotFree(&s);return 4;}
    BOOL changed=FALSE;BOOL saved=SaveSnapshotHistoryLocked(&s,&changed);SnapshotFree(&s);return saved?0:3;
}

static int RunDaemon(BOOL rescueImmediately){
    HINSTANCE inst=(HINSTANCE)GetModuleHandleW(0);WNDCLASSEXW wc;memset(&wc,0,sizeof(wc));wc.cbSize=sizeof(wc);wc.lpfnWndProc=HiddenWndProc;wc.hInstance=inst;wc.lpszClassName=kWindowClass;wc.hIcon=LoadIconW(inst,(LPCWSTR)(ULONG_PTR)kIconResourceId);wc.hIconSm=wc.hIcon;
    if(!RegisterClassExW(&wc)&&GetLastError()!=ERROR_ALREADY_EXISTS){ShowError(L"无法注册 Explorer Rescue 后台窗口类。");return 2;}
    g_hwnd=CreateWindowExW(0,kWindowClass,L"ExplorerRescueDaemon",0,0,0,0,0,0,0,inst,0);if(!g_hwnd){ShowError(L"无法创建 Explorer Rescue 后台消息窗口。");return 3;}
    g_taskbarCreated=RegisterWindowMessageW(L"TaskbarCreated");
    AddTrayIcon();
    if(!RegisterHotKey(g_hwnd,kHotkeyId,MOD_WIN|MOD_SHIFT|MOD_NOREPEAT,L'E'))ShowSystemNotification(L"Win + Shift + E 注册失败；仍可再次运行 ExplorerRescue.exe 执行 Explorer 救援。");
    SetTimer(g_hwnd,TIMER_SCAN_INTERVAL,g_cfg.scanIntervalMs,0);
    // 首次运行立即扫描，不再先空等一轮；5 秒只是后续扫描频率，不是首次扫描延迟。
    StartPeriodicScan();
    if(rescueImmediately)PostMessageW(g_hwnd,WM_ER_RESCUE,0,0);
    MSG m;while(GetMessageW(&m,0,0,0)>0){TranslateMessage(&m);DispatchMessageW(&m);}return 0;
}


static BOOL LaunchFreshSelf(){
    /*
    Main() 只会在旧守护的消息循环已经退出、单实例互斥量已经 CloseHandle 之后调用这里。
    因此新进程看到的是“没有旧守护”的正常首次启动状态，不会触发“第二实例 = Explorer 救援”。
    */
    UINT cap=WLen(g_exePath)+8;
    WCHAR* cmd=(WCHAR*)HAlloc(cap*sizeof(WCHAR),TRUE);
    if(!cmd)return FALSE;
    WAppend(cmd,cap,L"\"");
    WAppend(cmd,cap,g_exePath);
    WAppend(cmd,cap,L"\"");

    STARTUPINFOW si;PROCESS_INFORMATION pi;
    memset(&si,0,sizeof(si));memset(&pi,0,sizeof(pi));si.cb=sizeof(si);
    BOOL ok=CreateProcessW(g_exePath,cmd,0,0,FALSE,CREATE_UNICODE_ENVIRONMENT,0,g_appDir,&si,&pi);
    HFree(cmd);
    if(!ok)return FALSE;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return TRUE;
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
    int r=RunDaemon(rescueImmediately);

    // 先释放单实例互斥量，再启动新守护。这是托盘“重启 Explorer Rescue”不会误触发救援的关键。
    if(g_mutex){CloseHandle(g_mutex);g_mutex=0;}
    if(g_restartSelfRequested&&!LaunchFreshSelf()){
        ShowError(L"Explorer Rescue 已退出，但重新启动新实例失败。请手动再次运行 ExplorerRescue.exe。");
        return 13;
    }
    return r;
}

extern "C" VOID WINAPI ExplorerRescueEntry(){int code=Main();ExitProcess((UINT)code);}
