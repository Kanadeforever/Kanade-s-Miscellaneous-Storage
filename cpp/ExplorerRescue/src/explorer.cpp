#include "explorer.h"

/*
Explorer 交互核心。

v0.2.7 扫描/恢复规则：
1) 捕获原语逐项等价 Mine_Exp：Shell.Application.Windows() -> Document.Folder.Self.Path + HWND。
   相同 HWND 直接归入同一个 Win11 Explorer 标签组。
2) 不使用 CabinetWClass、FullName、Count 前后一致性来过滤 ShellWindow。
3) 窗口位置额外保存 GetWindowRect 的真实屏幕坐标；WINDOWPLACEMENT 只保存显示状态。位置不可靠时按主显示器工作区居中常规窗口兜底。
4) 稳定性仅在两次 Mine_Exp 等价捕获结果之间比较窗口/标签/路径语义，不比较窗口位置。
5) Win11 Explorer 标签恢复严格回到 Mine_Exp 已验证的原语：创建前记录 (HWND, LocationURL)，
   发送 ShellTabWindowClass/0xA21B 后以同 HWND 的新增 ShellWindow 为新标签，再 Navigate2。
6) 最终校验只用于报告，不得因为校验失败关闭整个恢复窗口或销毁已经成功恢复的标签。
*/

static const UINT kNewTabCommand = 0xA21B;
static const UINT kCloseTabCommand = 0xA021;

static BOOL IsCabinetClass(HWND hwnd){
    WCHAR c[64]; c[0]=0;
    if(!hwnd||!GetClassNameW(hwnd,c,64))return FALSE;
    return WEqual(c,L"CabinetWClass")||WEqual(c,L"ExploreWClass");
}
BOOL IsExplorerWindowHwnd(HWND hwnd){HWND root=GetAncestor(hwnd,GA_ROOT);if(!root)root=hwnd;return IsCabinetClass(root);}

static IDispatch* GetShellWindows(IDispatch* shell){return OleCallDispatch0(shell,L"Windows");}
static BOOL GetBrowserHwnd(IDispatch* browser,HWND* out){LONGLONG n=0;if(out)*out=0;if(!OleGetLong64(browser,L"HWND",&n)||!n)return FALSE;if(out)*out=(HWND)(LONG_PTR)n;return TRUE;}

static BOOL GetBrowserLocation(IDispatch* browser,DWORD* kind,WCHAR** value){
    if(kind)*kind=LOCATION_SHELL_PATH;if(value)*value=0;if(!browser)return FALSE;
    IDispatch* doc=OleGetDispatch(browser,L"Document");
    if(doc){
        IDispatch* folder=OleGetDispatch(doc,L"Folder");
        if(folder){
            IDispatch* self=OleGetDispatch(folder,L"Self");
            if(self){
                WCHAR* path=OleGetStringAlloc(self,L"Path");
                OleRelease(self);
                if(path&&path[0]){OleRelease(folder);OleRelease(doc);if(kind)*kind=LOCATION_SHELL_PATH;if(value)*value=path;return TRUE;}
                HFree(path);
            }
            OleRelease(folder);
        }
        OleRelease(doc);
    }
    WCHAR* url=OleGetStringAlloc(browser,L"LocationURL");
    if(url&&url[0]){if(kind)*kind=LOCATION_URL;if(value)*value=url;return TRUE;}
    HFree(url);return FALSE;
}

static UINT TrimmedLen(const WCHAR* s){UINT n=WLen(s);while(n>1&&(s[n-1]==L'\\'||s[n-1]==L'/'))--n;return n;}
static BOOL LocationTextEqual(const WCHAR* a,const WCHAR* b){if(!a||!b)return FALSE;UINT la=TrimmedLen(a),lb=TrimmedLen(b);if(la!=lb)return FALSE;return CompareStringOrdinal(a,(int)la,b,(int)lb,TRUE)==CSTR_EQUAL;}
static BOOL BrowserLocationEquals(IDispatch* b,const TabState* expected){DWORD k=0;WCHAR* v=0;if(!GetBrowserLocation(b,&k,&v))return FALSE;BOOL ok=LocationTextEqual(v,expected->value);HFree(v);return ok;}


static BOOL IsMinimizedShowCmd(UINT cmd){return cmd==SW_SHOWMINIMIZED||cmd==SW_MINIMIZE||cmd==SW_SHOWMINNOACTIVE||cmd==SW_FORCEMINIMIZE;}
static BOOL ScreenRectUsable(const RECT* r){
    if(!r)return FALSE;LONG w=r->right-r->left,h=r->bottom-r->top;if(w<480||h<320||w>20000||h>20000)return FALSE;
    HMONITOR m=MonitorFromRect(r,MONITOR_DEFAULTTONULL);if(!m)return FALSE;
    MONITORINFO mi;memset(&mi,0,sizeof(mi));mi.cbSize=sizeof(mi);if(!GetMonitorInfoW(m,&mi))return FALSE;
    LONG il=r->left>mi.rcWork.left?r->left:mi.rcWork.left,it=r->top>mi.rcWork.top?r->top:mi.rcWork.top;
    LONG ir=r->right<mi.rcWork.right?r->right:mi.rcWork.right,ib=r->bottom<mi.rcWork.bottom?r->bottom:mi.rcWork.bottom;
    return ir-il>=120&&ib-it>=80;
}
static BOOL CaptureReliableScreenRect(HWND hwnd,const WINDOWPLACEMENT* wp,BOOL havePlacement,RECT* out){
    if(out)memset(out,0,sizeof(*out));
    /* 最小化窗口的 GetWindowRect/COM 几何可能是最小化占位位置；按既定规则视为无可靠数据，恢复时居中常规大小。 */
    if(havePlacement&&IsMinimizedShowCmd(wp->showCmd))return FALSE;
    RECT r;if(!GetWindowRect(hwnd,&r)||!ScreenRectUsable(&r))return FALSE;if(out)*out=r;return TRUE;
}

BOOL CaptureExplorerSnapshot(Snapshot* out){
    /*
    v0.2.4：这里严格按 Mine_Exp 的 get_open_windows() 语义实现。
    事实来源只有 Shell.Application.Windows()：
      Document -> Folder -> Self -> Path
      HWND
    相同 HWND 直接归入同一个 Win11 Explorer 标签组。

    不再做 CabinetWClass / FullName / Count 前后一致性过滤；这些不是 Mine_Exp
    成功保存标签页所依赖的条件，在 Win11 上反而可能把正常 ShellWindow 误判为异常。

    WINDOWPLACEMENT 只是附加信息：读取不到时保留会话路径，恢复时使用默认居中常规窗口。
    */
    SnapshotInit(out);
    HRESULT ci=CoInitializeEx(0,COINIT_APARTMENTTHREADED);BOOL uninit=SUCCEEDED(ci);
    IDispatch* shell=OleCreateProgID(L"Shell.Application");
    if(!shell){if(uninit)CoUninitialize();return FALSE;}
    IDispatch* windows=GetShellWindows(shell);
    if(!windows){OleRelease(shell);if(uninit)CoUninitialize();return FALSE;}

    LONGLONG count64=0;
    if(!OleGetLong64(windows,L"Count",&count64)||count64<0||count64>4096){
        OleRelease(windows);OleRelease(shell);if(uninit)CoUninitialize();return FALSE;
    }

    BOOL hardFailure=FALSE;
    for(LONG i=0;i<(LONG)count64;++i){
        IDispatch* b=OleCallDispatchI4(windows,L"Item",i);
        if(!b){
            /* Mine_Exp 对单个不可读条目直接跳过，不让整轮会话失败。 */
            continue;
        }

        IDispatch* doc=OleGetDispatch(b,L"Document");
        if(!doc){OleRelease(b);continue;}
        IDispatch* folder=OleGetDispatch(doc,L"Folder");
        if(!folder){OleRelease(doc);OleRelease(b);continue;}
        IDispatch* self=OleGetDispatch(folder,L"Self");
        if(!self){OleRelease(folder);OleRelease(doc);OleRelease(b);continue;}
        WCHAR* path=OleGetStringAlloc(self,L"Path");
        OleRelease(self);OleRelease(folder);OleRelease(doc);
        if(!path||!path[0]){HFree(path);OleRelease(b);continue;}

        HWND hwnd=0;
        if(!GetBrowserHwnd(b,&hwnd)||!hwnd){HFree(path);OleRelease(b);continue;}

        WindowState* w=SnapshotFindWindowByHwnd(out,hwnd);
        if(!w){
            WINDOWPLACEMENT wp;memset(&wp,0,sizeof(wp));wp.length=sizeof(wp);
            BOOL havePlacement=GetWindowPlacement(hwnd,&wp);
            RECT screenRect;BOOL haveScreenRect=CaptureReliableScreenRect(hwnd,&wp,havePlacement,&screenRect);
            w=SnapshotAddWindow(out,hwnd,havePlacement?&wp:0,havePlacement,haveScreenRect?&screenRect:0,haveScreenRect);
            if(!w){HFree(path);OleRelease(b);hardFailure=TRUE;break;}
        }
        if(!WindowAddTab(w,LOCATION_SHELL_PATH,path)){
            HFree(path);OleRelease(b);hardFailure=TRUE;break;
        }
        HFree(path);OleRelease(b);
    }

    OleRelease(windows);OleRelease(shell);if(uninit)CoUninitialize();
    if(hardFailure){SnapshotFree(out);return FALSE;}
    return TRUE;
}


BOOL CaptureExplorerSnapshotStable(Snapshot* out,DWORD timeoutMs){
    /*
    v0.2.4 不再对 Mine_Exp 的一次枚举额外套“两次结果必须一致”的门槛。
    Mine_Exp 已经在用户环境验证：一次 shell.Windows() 枚举就是一份会话采样。
    timeoutMs 仍由外层工作线程 watchdog 用于防止 COM 真正挂死；这里不人为制造第二轮 COM 访问。
    */
    (void)timeoutMs;
    return CaptureExplorerSnapshot(out);
}

BOOL ExplorerResponsive(){
    HWND tray=FindWindowW(L"Shell_TrayWnd",0);if(!tray)return FALSE;DWORD_PTR result=0;
    if(!SendMessageTimeoutW(tray,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,120,&result))return FALSE;
    HWND after=0;for(;;){HWND w=FindWindowExW(0,after,L"CabinetWClass",0);if(!w)break;after=w;if(!SendMessageTimeoutW(w,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,120,&result))return FALSE;}
    return TRUE;
}

/* ----- v0.2.6: Mine_Exp 等价恢复原语 ----- */

static WCHAR* GetBrowserLocationUrl(IDispatch* browser){
    WCHAR* u=OleGetStringAlloc(browser,L"LocationURL");
    if(!u){u=(WCHAR*)HAlloc(sizeof(WCHAR),TRUE);}
    return u;
}

static HWND WaitForWindowByPath(IDispatch* shell,const TabState* target,DWORD timeoutMs){
    ULONGLONG start=GetTickCount64();
    while(GetTickCount64()-start<timeoutMs){
        IDispatch* ws=GetShellWindows(shell);
        if(ws){
            LONGLONG c=0;
            if(OleGetLong64(ws,L"Count",&c)){
                for(LONG i=0;i<(LONG)c;++i){
                    IDispatch* b=OleCallDispatchI4(ws,L"Item",i);
                    if(!b)continue;
                    HWND h=0;
                    if(GetBrowserHwnd(b,&h)&&h&&BrowserLocationEquals(b,target)){
                        OleRelease(b);OleRelease(ws);return h;
                    }
                    OleRelease(b);
                }
            }
            OleRelease(ws);
        }
        Sleep(60);
    }
    return 0;
}

static HWND WaitForTabHost(HWND hwnd,DWORD timeoutMs){
    ULONGLONG start=GetTickCount64();
    while(GetTickCount64()-start<timeoutMs){
        HWND host=FindWindowExW(hwnd,0,L"ShellTabWindowClass",0);
        if(host)return host;
        Sleep(60);
    }
    return 0;
}

static BOOL InvokeDispatchArgResult(IDispatch* obj,const WCHAR* name,VARIANT* arg,IDispatch** out){
    if(out)*out=0;VARIANT r;HRESULT hr=OleInvoke(obj,name,DISPATCH_METHOD|DISPATCH_PROPERTYGET,arg,1,&r);if(FAILED(hr))return FALSE;
    IDispatch*d=0;
    if(r.vt==VT_DISPATCH&&r.data.pdispVal){d=r.data.pdispVal;r.vt=VT_EMPTY;r.data.pdispVal=0;}
    else if(r.vt==VT_UNKNOWN&&r.data.punkVal){r.data.punkVal->lpVtbl->QueryInterface(r.data.punkVal,&IID_IDISPATCH_VALUE,(void**)&d);}
    VariantClear(&r);if(out)*out=d;return d!=0;
}

static BOOL ShellOpenLocation(IDispatch* shell,const TabState* tab){
    VARIANT a=VariantBstr(tab->value);if(a.vt!=VT_BSTR)return FALSE;
    BOOL ok=OleCall1(shell,L"Open",&a);VariantArgFree(&a);return ok;
}

static BOOL NavigateBrowserMineExp(IDispatch* shell,IDispatch* browser,const TabState* tab){
    /* Mine_Exp 的 _make_navigate_target：能取得 NameSpace Folder 时优先传 Folder，否则传字符串。 */
    BOOL ok=FALSE;
    VARIANT a=VariantBstr(tab->value);IDispatch* ns=0;
    if(a.vt==VT_BSTR&&InvokeDispatchArgResult(shell,L"NameSpace",&a,&ns)&&ns){
        VARIANT d=VariantDispatch(ns);ok=OleCall1(browser,L"Navigate2",&d);VariantArgFree(&d);OleRelease(ns);
    }
    VariantArgFree(&a);
    if(!ok){VARIANT b=VariantBstr(tab->value);if(b.vt==VT_BSTR)ok=OleCall1(browser,L"Navigate2",&b);VariantArgFree(&b);}
    return ok;
}

struct BrowserKey { HWND hwnd; WCHAR* url; };
struct BrowserKeyList { BrowserKey* p; DWORD n; DWORD cap; };
static void BrowserKeyListFree(BrowserKeyList* l){if(!l)return;for(DWORD i=0;i<l->n;++i)HFree(l->p[i].url);HFree(l->p);memset(l,0,sizeof(*l));}
static BOOL BrowserKeyListAdd(BrowserKeyList* l,HWND hwnd,const WCHAR* url){
    if(l->n>=l->cap){DWORD nc=l->cap?l->cap*2:8;BrowserKey* p=(BrowserKey*)HReAlloc(l->p,nc*sizeof(BrowserKey),TRUE);if(!p)return FALSE;l->p=p;l->cap=nc;}
    UINT n=WLen(url);WCHAR* copy=(WCHAR*)HAlloc((n+1)*sizeof(WCHAR),TRUE);if(!copy)return FALSE;if(n)memcpy(copy,url,n*sizeof(WCHAR));copy[n]=0;
    l->p[l->n].hwnd=hwnd;l->p[l->n].url=copy;++l->n;return TRUE;
}
static BOOL BrowserKeyExists(const BrowserKeyList* l,HWND hwnd,const WCHAR* url){
    for(DWORD i=0;i<l->n;++i)if(l->p[i].hwnd==hwnd&&WEqual(l->p[i].url,url))return TRUE;return FALSE;
}

struct BrowserRefList { IDispatch** p; DWORD n; DWORD cap; };
static void BrowserRefListFree(BrowserRefList* l){if(!l)return;for(DWORD i=0;i<l->n;++i)OleRelease(l->p[i]);HFree(l->p);memset(l,0,sizeof(*l));}
static BOOL BrowserRefListAdd(BrowserRefList* l,IDispatch* b){
    if(l->n>=l->cap){DWORD nc=l->cap?l->cap*2:8;IDispatch** p=(IDispatch**)HReAlloc(l->p,nc*sizeof(IDispatch*),TRUE);if(!p)return FALSE;l->p=p;l->cap=nc;}
    l->p[l->n++]=b;return TRUE;
}
static BOOL EnumerateBrowsersForHwnd(IDispatch* shell,HWND hwnd,BrowserRefList* out){
    memset(out,0,sizeof(*out));IDispatch* ws=GetShellWindows(shell);if(!ws)return FALSE;LONGLONG c=0;if(!OleGetLong64(ws,L"Count",&c)){OleRelease(ws);return FALSE;}
    for(LONG i=0;i<(LONG)c;++i){
        IDispatch* b=OleCallDispatchI4(ws,L"Item",i);if(!b)continue;
        HWND h=0;if(GetBrowserHwnd(b,&h)&&h==hwnd){if(!BrowserRefListAdd(out,b)){OleRelease(b);OleRelease(ws);BrowserRefListFree(out);return FALSE;}}
        else OleRelease(b);
    }
    OleRelease(ws);return TRUE;
}
static BOOL CaptureBrowserKeys(IDispatch* shell,BrowserKeyList* out){
    memset(out,0,sizeof(*out));IDispatch* ws=GetShellWindows(shell);if(!ws)return FALSE;LONGLONG c=0;if(!OleGetLong64(ws,L"Count",&c)){OleRelease(ws);return FALSE;}
    for(LONG i=0;i<(LONG)c;++i){
        IDispatch* b=OleCallDispatchI4(ws,L"Item",i);if(!b)continue;HWND h=0;
        if(GetBrowserHwnd(b,&h)&&h){WCHAR* u=GetBrowserLocationUrl(b);if(u){BOOL a=BrowserKeyListAdd(out,h,u);HFree(u);if(!a){OleRelease(b);OleRelease(ws);BrowserKeyListFree(out);return FALSE;}}}
        OleRelease(b);
    }
    OleRelease(ws);return TRUE;
}

static BOOL WaitBrowserAtTarget(IDispatch* b,const TabState* tab,DWORD timeoutMs){
    ULONGLONG start=GetTickCount64();while(GetTickCount64()-start<timeoutMs){if(BrowserLocationEquals(b,tab))return TRUE;Sleep(60);}return FALSE;
}

static IDispatch* FindNewTabMineExp(IDispatch* shell,HWND targetHwnd,const BrowserKeyList* before,DWORD oldCount,DWORD timeoutMs){
    ULONGLONG start=GetTickCount64();
    while(GetTickCount64()-start<timeoutMs){
        Sleep(50);BrowserRefList now;if(!EnumerateBrowsersForHwnd(shell,targetHwnd,&now))continue;
        if(now.n>oldCount){
            /* 第一选择：完全照 Mine_Exp，用 (HWND, LocationURL) 前后差集识别。 */
            for(DWORD i=0;i<now.n;++i){
                WCHAR* u=GetBrowserLocationUrl(now.p[i]);
                BOOL fresh=u&&!BrowserKeyExists(before,targetHwnd,u);HFree(u);
                if(fresh){IDispatch* r=now.p[i];now.p[i]=0;BrowserRefListFree(&now);return r;}
            }
            /* 新建标签初始 URL 与已有 Home/此电脑重复时，Mine_Exp 的 set 差集无法识别。
               此处只做一个无破坏补强：既然同 HWND 数量已增加，取最后枚举项作为新标签候选。 */
            if(now.n){IDispatch* r=now.p[now.n-1];now.p[now.n-1]=0;BrowserRefListFree(&now);return r;}
        }
        BrowserRefListFree(&now);
    }
    return 0;
}

static BOOL RestoreTabIntoWindowMineExp(IDispatch* shell,HWND hwnd,const TabState* tab){
    HWND host=WaitForTabHost(hwnd,6000);if(!host)return FALSE;
    BrowserKeyList before;if(!CaptureBrowserKeys(shell,&before))return FALSE;
    BrowserRefList current;if(!EnumerateBrowsersForHwnd(shell,hwnd,&current)){BrowserKeyListFree(&before);return FALSE;}DWORD oldCount=current.n;BrowserRefListFree(&current);
    if(!PostMessageW(host,WM_COMMAND,kNewTabCommand,0)){BrowserKeyListFree(&before);return FALSE;}
    IDispatch* b=FindNewTabMineExp(shell,hwnd,&before,oldCount,3000);BrowserKeyListFree(&before);
    if(!b)return FALSE;
    BOOL ok=FALSE;
    for(int attempt=0;attempt<3&&!ok;++attempt){if(NavigateBrowserMineExp(shell,b,tab))ok=WaitBrowserAtTarget(b,tab,3500);if(!ok)Sleep(120);}
    OleRelease(b);
    if(!ok){
        /* 只关闭本次失败创建的垃圾标签，绝不关闭整个 Explorer 窗口。 */
        PostMessageW(host,WM_COMMAND,kCloseTabCommand,1);Sleep(180);
    }
    return ok;
}

static HWND OpenGroupWindow(IDispatch* shell,const TabState* first){
    if(!ShellOpenLocation(shell,first))return 0;
    HWND h=WaitForWindowByPath(shell,first,8000);
    if(h)WaitForTabHost(h,6000);
    return h;
}

static void MakeCenteredRectInWork(const RECT* work,LONG preferredW,LONG preferredH,RECT* out){
    LONG ww=work->right-work->left,wh=work->bottom-work->top;UINT dpi=GetDpiForSystem();if(!dpi)dpi=96;
    LONG w=preferredW>0?preferredW:(LONG)(1200ULL*dpi/96),h=preferredH>0?preferredH:(LONG)(800ULL*dpi/96);
    LONG maxw=ww*80/100,maxh=wh*80/100;if(w>maxw)w=maxw;if(h>maxh)h=maxh;if(w<640)w=ww>640?640:ww;if(h<480)h=wh>480?480:wh;
    LONG l=work->left+(ww-w)/2,t=work->top+(wh-h)/2;out->left=l;out->top=t;out->right=l+w;out->bottom=t+h;
}
static void MakeDefaultScreenRect(RECT* out){RECT work={0,0,1200,800};SystemParametersInfoW(SPI_GETWORKAREA,0,&work,0);MakeCenteredRectInWork(&work,0,0,out);}
static UINT DesiredShowCmd(const WindowState* w,BOOL usingSavedRect){
    if(!usingSavedRect||!w->hasPlacement)return SW_SHOWNORMAL;
    if(w->placement.showCmd==SW_SHOWMAXIMIZED)return SW_SHOWMAXIMIZED;
    return SW_SHOWNORMAL;
}
static void ApplyWindowGeometry(HWND hwnd,const WindowState* w){
    RECT r;BOOL saved=w&&w->hasScreenRect&&ScreenRectUsable(&w->screenRect);UINT show=DesiredShowCmd(w,saved);
    if(saved&&show==SW_SHOWMAXIMIZED){
        /* 最大化状态下 GetWindowRect 主要用于确定原显示器。先放一个合理普通矩形到该显示器，再最大化，避免以后取消最大化时出现整屏大小的“普通窗口”。 */
        HMONITOR m=MonitorFromRect(&w->screenRect,MONITOR_DEFAULTTONULL);MONITORINFO mi;memset(&mi,0,sizeof(mi));mi.cbSize=sizeof(mi);
        LONG pw=0,ph=0;if(w->hasPlacement){pw=w->placement.rcNormalPosition.right-w->placement.rcNormalPosition.left;ph=w->placement.rcNormalPosition.bottom-w->placement.rcNormalPosition.top;}
        if(m&&GetMonitorInfoW(m,&mi))MakeCenteredRectInWork(&mi.rcWork,pw,ph,&r);else MakeDefaultScreenRect(&r);
    }else if(saved)r=w->screenRect;else MakeDefaultScreenRect(&r);
    LONG width=r.right-r.left,height=r.bottom-r.top;
    /* 先还原成普通态，再用真正的 screen coordinates 定位。GetWindowRect/SetWindowPos 是同一坐标系。 */
    ShowWindow(hwnd,SW_RESTORE);
    SetWindowPos(hwnd,0,r.left,r.top,width,height,SWP_NOZORDER|SWP_NOACTIVATE);
    if(show==SW_SHOWMAXIMIZED)ShowWindow(hwnd,SW_SHOWMAXIMIZED);else ShowWindow(hwnd,SW_SHOWNORMAL);
}


static BOOL ValidateGroup(IDispatch* shell,HWND hwnd,const WindowState* expected){
    BrowserRefList list;if(!EnumerateBrowsersForHwnd(shell,hwnd,&list))return FALSE;
    if(list.n!=expected->tabCount){BrowserRefListFree(&list);return FALSE;}
    BYTE* used=(BYTE*)HAlloc(list.n?list.n:1,TRUE);BOOL ok=used!=0;
    for(DWORD e=0;e<expected->tabCount&&ok;++e){
        BOOL hit=FALSE;
        for(DWORD i=0;i<list.n;++i){if(used[i])continue;if(BrowserLocationEquals(list.p[i],&expected->tabs[e])){used[i]=1;hit=TRUE;break;}}
        if(!hit)ok=FALSE;
    }
    HFree(used);BrowserRefListFree(&list);return ok;
}

static BOOL RestoreOneGroup(IDispatch* shell,const WindowState* w,HWND* outHwnd){
    if(outHwnd)*outHwnd=0;if(!w||!w->tabCount)return FALSE;
    HWND hwnd=OpenGroupWindow(shell,&w->tabs[0]);if(!hwnd)return FALSE;if(outHwnd)*outHwnd=hwnd;
    /* 恢复标签期间保持窗口可操作；最终再恢复最小化/最大化/普通位置，避免最小化状态干扰标签宿主命令。 */
    BOOL createdAll=TRUE;
    for(DWORD ti=1;ti<w->tabCount;++ti){
        BOOL tabOk=FALSE;for(int attempt=0;attempt<2&&!tabOk;++attempt)tabOk=RestoreTabIntoWindowMineExp(shell,hwnd,&w->tabs[ti]);
        if(!tabOk)createdAll=FALSE;
    }
    /* Explorer 的导航完成通知存在滞后；给最终只读校验一点收敛时间。 */
    Sleep(500);
    BOOL validated=ValidateGroup(shell,hwnd,w);
    ApplyWindowGeometry(hwnd,w);
    return createdAll&&validated;
}

BOOL RestoreExplorerSnapshot(const Snapshot* s,RestoreStats* stats){
    if(stats)memset(stats,0,sizeof(*stats));if(!s)return FALSE;if(stats){stats->expectedWindows=s->windowCount;stats->expectedTabs=SnapshotTotalTabs(s);}
    HRESULT ci=CoInitializeEx(0,COINIT_APARTMENTTHREADED);BOOL uninit=SUCCEEDED(ci);IDispatch* shell=OleCreateProgID(L"Shell.Application");if(!shell){if(uninit)CoUninitialize();return FALSE;}
    HWND* restored=(HWND*)HAlloc((s->windowCount?s->windowCount:1)*sizeof(HWND),TRUE);BOOL all=restored!=0;
    for(DWORD wi=0;wi<s->windowCount&&restored;++wi){
        const WindowState* w=&s->windows[wi];if(!w->tabCount)continue;HWND hwnd=0;
        BOOL valid=RestoreOneGroup(shell,w,&hwnd);restored[wi]=hwnd;
        if(stats){if(valid){++stats->restoredWindows;stats->restoredTabs+=w->tabCount;}else{++stats->failedWindows;stats->failedTabs+=w->tabCount;++stats->validationFailures;}}
        if(!valid)all=FALSE;
    }
    /* Win11 Explorer 在窗口/标签初始化完成后仍可能异步调整 frame。所有组完成后再次写回位置，避免 Shell 覆盖第一次定位。 */
    if(restored){Sleep(650);for(DWORD wi=0;wi<s->windowCount;++wi)if(restored[wi])ApplyWindowGeometry(restored[wi],&s->windows[wi]);HFree(restored);}
    OleRelease(shell);if(uninit)CoUninitialize();return all;
}

static BOOL SameExeName(const WCHAR* a,const WCHAR* b){return WEqualI(a,b);}
static BOOL KillExplorerProcesses(){DWORD mySession=0;ProcessIdToSessionId(GetCurrentProcessId(),&mySession);HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);if(snap==INVALID_HANDLE_VALUE)return FALSE;HANDLE waits[128];DWORD waitCount=0;PROCESSENTRY32W pe;memset(&pe,0,sizeof(pe));pe.dwSize=sizeof(pe);BOOL any=FALSE;if(Process32FirstW(snap,&pe)){do{if(SameExeName(pe.szExeFile,L"explorer.exe")){DWORD sid=0;if(ProcessIdToSessionId(pe.th32ProcessID,&sid)&&sid==mySession){HANDLE p=OpenProcess(PROCESS_TERMINATE|PROCESS_SYNCHRONIZE,FALSE,pe.th32ProcessID);if(p){if(TerminateProcess(p,0)){any=TRUE;if(waitCount<128)waits[waitCount++]=p;else CloseHandle(p);}else CloseHandle(p);}}}}while(Process32NextW(snap,&pe));}CloseHandle(snap);for(DWORD i=0;i<waitCount;++i){WaitForSingleObject(waits[i],3000);CloseHandle(waits[i]);}return any;}
static BOOL StartExplorerImmediately(){
    /*
    v0.2.7：旧版会先等待 1.2 秒，赌 Windows 自己重启 Shell；如果系统没有立刻自动拉起 explorer.exe，
    这段等待会直接表现为任务栏迟迟不回来。既然 KillExplorerProcesses() 已经等待旧 Explorer 退出，
    这里应立即启动新的 Shell。Explorer 自身具有单实例/壳层接管语义，即使 Windows 同时尝试自动重启，
    也不需要我们人为空等。
    */
    HWND tray=FindWindowW(L"Shell_TrayWnd",0);
    if(tray){
        DWORD_PTR r=0;
        if(SendMessageTimeoutW(tray,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,80,&r))return TRUE;
    }
    WCHAR* win=(WCHAR*)HAlloc(1024*sizeof(WCHAR),TRUE);
    WCHAR* cmd=(WCHAR*)HAlloc(1200*sizeof(WCHAR),TRUE);
    if(!win||!cmd){HFree(win);HFree(cmd);return FALSE;}
    UINT n=GetWindowsDirectoryW(win,1024);
    if(!n||n>1000){HFree(win);HFree(cmd);return FALSE;}
    WAppend(win,1024,L"\\explorer.exe");
    WAppend(cmd,1200,L"\"");WAppend(cmd,1200,win);WAppend(cmd,1200,L"\"");
    STARTUPINFOW si;PROCESS_INFORMATION pi;memset(&si,0,sizeof(si));memset(&pi,0,sizeof(pi));si.cb=sizeof(si);
    BOOL ok=CreateProcessW(win,cmd,0,0,FALSE,CREATE_UNICODE_ENVIRONMENT,0,0,&si,&pi);
    if(ok){CloseHandle(pi.hThread);CloseHandle(pi.hProcess);}
    HFree(win);HFree(cmd);return ok;
}
BOOL RestartExplorerShell(){
    KillExplorerProcesses();
    if(!StartExplorerImmediately())return FALSE;
    ULONGLONG start=GetTickCount64();
    while(GetTickCount64()-start<15000){
        HWND tray=FindWindowW(L"Shell_TrayWnd",0);
        if(tray){
            DWORD_PTR r=0;
            if(SendMessageTimeoutW(tray,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,120,&r)){
                /* 任务栏已经创建且可响应。这里保留短暂 Shell 稳定期只用于后续标签恢复；
                   它发生在任务栏已经出现之后，不再影响“任务栏多久重新出现”。
                   v0.2.7 的提速点是：旧 Explorer 退出后立即拉起新 Shell，不再先白等 1.2 秒。 */
                Sleep(900);
                return TRUE;
            }
        }
        Sleep(40);
    }
    return FALSE;
}
