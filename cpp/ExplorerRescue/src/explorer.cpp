#include "explorer.h"

/*
Explorer 交互核心。

v0.2.10 扫描/恢复规则：
1) 捕获原语逐项等价 Mine_Exp：Shell.Application.Windows() -> Document.Folder.Self.Path + HWND。
   相同 HWND 直接归入同一个 Win11 Explorer 标签组。
2) 不使用 CabinetWClass、FullName、Count 前后一致性来过滤 ShellWindow。
3) 窗口位置额外保存 GetWindowRect 的真实屏幕坐标；WINDOWPLACEMENT 只保存显示状态。位置不可靠时按主显示器工作区居中常规窗口兜底。
4) 每次周期扫描只做一次 Mine_Exp 等价枚举；完整扫描超时由外层 watchdog 独立控制，不额外要求双采样一致。
5) Win11 Explorer 标签恢复严格回到 Mine_Exp 已验证的原语：创建前记录 (HWND, LocationURL)，
   发送 ShellTabWindowClass/0xA21B 后以同 HWND 的新增 ShellWindow 为新标签，再 Navigate2。
6) 每个标签组的首窗口必须先通过“新 HWND 绑定 + 首路径 + 标签宿主 + 窗口响应 + 稳定期”就绪闸门，之后才允许创建第二个标签；后续标签保持快速恢复。
7) 第一组按具体 Explorer 顶层 HWND 安全复用 Shell 重启后的单标签启动窗口；最终校验只用于报告，不得关闭失败恢复组。
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

/* ----- v0.2.10: Mine_Exp 等价恢复原语 + 首窗口就绪闸门 + Shell 启动窗口复用 ----- */

static WCHAR* GetBrowserLocationUrl(IDispatch* browser){
    WCHAR* u=OleGetStringAlloc(browser,L"LocationURL");
    if(!u){u=(WCHAR*)HAlloc(sizeof(WCHAR),TRUE);}
    return u;
}

struct HwndList { HWND* p; DWORD n; DWORD cap; };
static void HwndListFree(HwndList* l){if(!l)return;HFree(l->p);memset(l,0,sizeof(*l));}
static BOOL HwndListContains(const HwndList* l,HWND hwnd){if(!l)return FALSE;for(DWORD i=0;i<l->n;++i)if(l->p[i]==hwnd)return TRUE;return FALSE;}
static BOOL HwndListAddUnique(HwndList* l,HWND hwnd){
    if(!l||!hwnd)return TRUE;if(HwndListContains(l,hwnd))return TRUE;
    if(l->n>=l->cap){DWORD nc=l->cap?l->cap*2:8;HWND* p=(HWND*)HReAlloc(l->p,nc*sizeof(HWND),TRUE);if(!p)return FALSE;l->p=p;l->cap=nc;}
    l->p[l->n++]=hwnd;return TRUE;
}
static BOOL CaptureExplorerHwndList(IDispatch* shell,HwndList* out){
    memset(out,0,sizeof(*out));IDispatch* ws=GetShellWindows(shell);if(!ws)return FALSE;LONGLONG c=0;if(!OleGetLong64(ws,L"Count",&c)){OleRelease(ws);return FALSE;}
    for(LONG i=0;i<(LONG)c;++i){IDispatch* b=OleCallDispatchI4(ws,L"Item",i);if(!b)continue;HWND h=0;if(GetBrowserHwnd(b,&h)&&h&&!HwndListAddUnique(out,h)){OleRelease(b);OleRelease(ws);HwndListFree(out);return FALSE;}OleRelease(b);}
    OleRelease(ws);return TRUE;
}

/*
首窗口不能只按路径查找：如果两个标签组的首路径相同，或者新窗口注册尚未完成，
按路径可能误命中前面已经恢复好的 Explorer 窗口，随后把标签添加到错误的组。
因此创建窗口前记录全部现有 Explorer HWND，只接受“创建之后新出现 + 路径匹配”的 HWND。
*/
static HWND WaitForNewWindowByPath(IDispatch* shell,const TabState* target,const HwndList* before,DWORD timeoutMs){
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
                    if(GetBrowserHwnd(b,&h)&&h&&!HwndListContains(before,h)&&BrowserLocationEquals(b,target)){
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

static BOOL TryGetBrowserBusy(IDispatch* browser,BOOL* busy){
    if(busy)*busy=FALSE;VARIANT r;HRESULT hr=OleInvoke(browser,L"Busy",DISPATCH_PROPERTYGET,0,0,&r);if(FAILED(hr))return FALSE;BOOL ok=TRUE;BOOL v=FALSE;
    if(r.vt==VT_BOOL)v=r.data.boolVal!=0;
    else if(r.vt==VT_I2)v=r.data.iVal!=0;
    else if(r.vt==VT_I4||r.vt==VT_INT)v=r.data.lVal!=0;
    else ok=FALSE;
    VariantClear(&r);if(ok&&busy)*busy=v;return ok;
}

/*
首窗口就绪闸门。

“Shell.Windows 已能返回 HWND/Path”只能说明窗口对象已经注册，并不代表 Win11 Explorer 的
标签宿主和首个 ShellView 已经完成初始化。过早发送 0xA21B 会与 Explorer 自己的初始化竞争，
表现为标签被加到错误阶段、标签组被破坏或最终整组校验失败。

通过条件：
- 顶层窗口与 ShellTabWindowClass 均可响应；
- 同 HWND 当前只有首个标签，且实际路径已经到达目标；
- IWebBrowser2.Busy 若可读取则必须为 false；
- 以上状态连续稳定至少 1200 ms。

超时只表示本组首窗口未能安全进入“可加标签”状态。调用方必须保留已经打开的首窗口，
但不得继续向它创建标签，避免恢复器自身破坏会话结构。
*/
static BOOL WaitForFirstWindowReady(IDispatch* shell,HWND hwnd,const TabState* first,DWORD timeoutMs){
    ULONGLONG start=GetTickCount64(),stableSince=0;HWND stableHost=0;
    while(GetTickCount64()-start<timeoutMs){
        BOOL ready=TRUE;DWORD_PTR ignored=0;
        if(!SendMessageTimeoutW(hwnd,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,150,&ignored))ready=FALSE;
        HWND host=FindWindowExW(hwnd,0,L"ShellTabWindowClass",0);
        if(!host||!SendMessageTimeoutW(host,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,150,&ignored))ready=FALSE;
        BrowserRefList list;memset(&list,0,sizeof(list));
        if(ready&&!EnumerateBrowsersForHwnd(shell,hwnd,&list))ready=FALSE;
        if(ready&&list.n!=1)ready=FALSE;
        if(ready&&list.n==1){
            if(!BrowserLocationEquals(list.p[0],first))ready=FALSE;
            BOOL busy=FALSE;if(ready&&TryGetBrowserBusy(list.p[0],&busy)&&busy)ready=FALSE;
        }
        BrowserRefListFree(&list);
        ULONGLONG now=GetTickCount64();
        if(ready){
            if(!stableSince||stableHost!=host){stableSince=now;stableHost=host;}
            else if(now-stableSince>=1200)return TRUE;
        }else{stableSince=0;stableHost=0;}
        Sleep(80);
    }
    return FALSE;
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

/*
Windows 11 在重启 Shell 的竞态窗口中，可能已经出现一个系统启动产生的单标签 Explorer 窗口
（常见表现为 Home/此电脑）。如果恢复器无视它，再 Shell.Open() 第一个会话组，就会留下一个
多余的“此电脑”窗口。

这里不按标题/本地化名称猜测，而只接受一个足够保守的结构条件：
- 当前 Shell.Windows() 只对应 1 个唯一 Explorer HWND；
- 该 HWND 当前只对应 1 个 ShellWindow/标签；
- HWND 是普通 Explorer 顶层窗口；
- 窗口/标签宿主可在后续就绪闸门中正常响应。

满足时直接复用其唯一标签导航到第一组首路径。若导航失败，返回这个 HWND 且 outReady=FALSE，
调用方会保留它并报告本组失败，不再额外新建第二个窗口，从而避免“此电脑 + 恢复窗口”双窗口。
*/
static BOOL CaptureVisibleExplorerFrames(HwndList* out){
    memset(out,0,sizeof(*out));
    const WCHAR* classes[2]={L"CabinetWClass",L"ExploreWClass"};
    for(int ci=0;ci<2;++ci){
        HWND after=0;
        for(;;){
            HWND h=FindWindowExW(0,after,classes[ci],0);
            if(!h)break;
            after=h;
            if(!HwndListAddUnique(out,h)){HwndListFree(out);return FALSE;}
        }
    }
    return TRUE;
}

/*
Windows 11 重启 Shell 后可能先产生一个默认的单标签 Explorer frame（常见为 Home/此电脑）。
v0.2.9 错误地要求 Shell.Windows() 中“全局只有一个唯一 Explorer HWND”，这在实机上过严：
Shell.Windows() 可能同时包含其他 Shell 条目，于是明明已经可见的默认窗口不会被识别，随后又 Shell.Open()
创建第一组，最终留下一个独立的“此电脑”。

v0.2.10 改为以“具体顶层 Explorer frame”为候选：
- 直接枚举当前可见/已创建的 CabinetWClass / ExploreWClass 顶层 HWND；
- 对每个 HWND 单独检查 Shell.Windows() 是否恰好对应 1 个标签；
- 顶层窗口必须可响应；
- 一旦找到安全候选，就直接复用其唯一标签导航到第一组首路径；
- 不再要求全局唯一，也不受其他 Shell 条目影响。

此函数只在恢复第一组之前调用，因此此时尚不存在本程序创建的恢复窗口；这里看到的 Explorer frame
只能来自本轮新 Shell 的 bootstrap/默认启动阶段。若候选暂时只创建了 HWND、尚未注册进 Shell.Windows()，
会继续等待到 waitMs，而不是马上放弃并新建第二个窗口。
*/
static HWND TryReuseBootstrapWindow(IDispatch* shell,const TabState* first,BOOL* outReady,DWORD waitMs){
    if(outReady)*outReady=FALSE;
    ULONGLONG start=GetTickCount64();
    for(;;){
        HwndList frames;
        if(CaptureVisibleExplorerFrames(&frames)){
            for(DWORD fi=0;fi<frames.n;++fi){
                HWND h=frames.p[fi];DWORD_PTR ignored=0;
                if(!SendMessageTimeoutW(h,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,150,&ignored))continue;

                BrowserRefList tabs;memset(&tabs,0,sizeof(tabs));
                if(!EnumerateBrowsersForHwnd(shell,h,&tabs))continue;
                if(tabs.n!=1){BrowserRefListFree(&tabs);continue;}

                IDispatch* onlyBrowser=tabs.p[0];tabs.p[0]=0;BrowserRefListFree(&tabs);
                BOOL reached=BrowserLocationEquals(onlyBrowser,first);
                for(int attempt=0;attempt<3&&!reached;++attempt){
                    if(NavigateBrowserMineExp(shell,onlyBrowser,first))
                        reached=WaitBrowserAtTarget(onlyBrowser,first,4500);
                    if(!reached)Sleep(120);
                }
                OleRelease(onlyBrowser);
                if(!reached){HwndListFree(&frames);return h;}

                BOOL ready=WaitForFirstWindowReady(shell,h,first,10000);
                if(outReady)*outReady=ready;
                HwndListFree(&frames);
                return h;
            }
            HwndListFree(&frames);
        }
        if(GetTickCount64()-start>=waitMs)break;
        Sleep(50);
    }
    return 0;
}

static HWND OpenGroupWindow(IDispatch* shell,const TabState* first,BOOL allowBootstrap,BOOL* outReady){
    if(outReady)*outReady=FALSE;

    /*
    仅第一组允许复用 Shell 启动窗口。任务栏恢复后通常这个窗口已经注册；额外最多等 2200 ms，
    用来覆盖 explorer.exe/系统 Shell 双方初始化的轻微竞态。若不存在安全候选，则走原来的新建流程。
    */
    if(allowBootstrap){
        BOOL ready=FALSE;
        HWND bootstrap=TryReuseBootstrapWindow(shell,first,&ready,2200);
        if(bootstrap){
            if(outReady)*outReady=ready;
            return bootstrap;
        }
    }

    HwndList before;if(!CaptureExplorerHwndList(shell,&before))return 0;
    if(!ShellOpenLocation(shell,first)){HwndListFree(&before);return 0;}
    HWND h=WaitForNewWindowByPath(shell,first,&before,8000);HwndListFree(&before);
    if(!h)return 0;
    /* 第一个窗口必须完全完成初始化后才允许创建第二个标签。这里宁可把本组报告失败，也不抢跑破坏标签组。 */
    BOOL ready=WaitForFirstWindowReady(shell,h,first,10000);
    if(outReady)*outReady=ready;
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

static BOOL RestoreOneGroup(IDispatch* shell,const WindowState* w,BOOL allowBootstrap,HWND* outHwnd){
    if(outHwnd)*outHwnd=0;if(!w||!w->tabCount)return FALSE;
    BOOL firstReady=FALSE;HWND hwnd=OpenGroupWindow(shell,&w->tabs[0],allowBootstrap,&firstReady);if(!hwnd)return FALSE;if(outHwnd)*outHwnd=hwnd;
    /*
    首窗口是每个标签组的硬闸门。未完全就绪时绝不继续发送新建标签命令；
    保留已经正确打开的首窗口并恢复其几何，历史会话也不会被失败状态覆盖。
    */
    if(!firstReady){ApplyWindowGeometry(hwnd,w);return FALSE;}
    /* 恢复标签期间保持窗口可操作；首窗口闸门通过后，后续标签继续使用现有快速创建/导航流程。 */
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
    /* 记录恢复开始前已经由新 Shell 产生的顶层 Explorer frame。旧 Explorer 已在此前被结束，
       因此这些 HWND 只可能属于本轮 bootstrap/默认启动阶段。正常情况会复用其中一个作为第一组；
       如果由于注册竞态没有复用，但全部会话最终恢复成功，则只清理这些“恢复前就存在且未被使用”的残留 frame。 */
    HwndList bootstrapFrames;memset(&bootstrapFrames,0,sizeof(bootstrapFrames));CaptureVisibleExplorerFrames(&bootstrapFrames);
    HWND* restored=(HWND*)HAlloc((s->windowCount?s->windowCount:1)*sizeof(HWND),TRUE);BOOL all=restored!=0;
    BOOL allowBootstrap=TRUE;
    for(DWORD wi=0;wi<s->windowCount&&restored;++wi){
        const WindowState* w=&s->windows[wi];if(!w->tabCount)continue;HWND hwnd=0;
        BOOL valid=RestoreOneGroup(shell,w,allowBootstrap,&hwnd);allowBootstrap=FALSE;restored[wi]=hwnd;
        if(stats){if(valid){++stats->restoredWindows;stats->restoredTabs+=w->tabCount;}else{++stats->failedWindows;stats->failedTabs+=w->tabCount;++stats->validationFailures;}}
        if(!valid)all=FALSE;
    }
    /* Win11 Explorer 在窗口/标签初始化完成后仍可能异步调整 frame。所有组完成后再次写回位置，避免 Shell 覆盖第一次定位。 */
    if(restored){
        Sleep(650);for(DWORD wi=0;wi<s->windowCount;++wi)if(restored[wi])ApplyWindowGeometry(restored[wi],&s->windows[wi]);
        /* 仅在全部窗口/标签都校验成功时清理未被复用的 bootstrap frame。失败恢复绝不做破坏性清理。
           这些 HWND 是 RestoreExplorerSnapshot() 入口时就存在、且不属于任何 restored[] 的新 Shell 启动窗口，
           因而不会误删本次恢复出的真实会话窗口。 */
        if(all){
            for(DWORD bi=0;bi<bootstrapFrames.n;++bi){
                HWND h=bootstrapFrames.p[bi];BOOL used=FALSE;
                for(DWORD wi=0;wi<s->windowCount;++wi)if(restored[wi]==h){used=TRUE;break;}
                if(!used){
                    BrowserRefList tabs;memset(&tabs,0,sizeof(tabs));
                    if(EnumerateBrowsersForHwnd(shell,h,&tabs)&&tabs.n==1)PostMessageW(h,WM_CLOSE,0,0);
                    BrowserRefListFree(&tabs);
                }
            }
        }
        HFree(restored);
    }
    HwndListFree(&bootstrapFrames);
    OleRelease(shell);if(uninit)CoUninitialize();return all;
}

static BOOL SameExeName(const WCHAR* a,const WCHAR* b){return WEqualI(a,b);}
static BOOL KillExplorerProcesses(){DWORD mySession=0;ProcessIdToSessionId(GetCurrentProcessId(),&mySession);HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);if(snap==INVALID_HANDLE_VALUE)return FALSE;HANDLE waits[128];DWORD waitCount=0;PROCESSENTRY32W pe;memset(&pe,0,sizeof(pe));pe.dwSize=sizeof(pe);BOOL any=FALSE;if(Process32FirstW(snap,&pe)){do{if(SameExeName(pe.szExeFile,L"explorer.exe")){DWORD sid=0;if(ProcessIdToSessionId(pe.th32ProcessID,&sid)&&sid==mySession){HANDLE p=OpenProcess(PROCESS_TERMINATE|PROCESS_SYNCHRONIZE,FALSE,pe.th32ProcessID);if(p){if(TerminateProcess(p,0)){any=TRUE;if(waitCount<128)waits[waitCount++]=p;else CloseHandle(p);}else CloseHandle(p);}}}}while(Process32NextW(snap,&pe));}CloseHandle(snap);for(DWORD i=0;i<waitCount;++i){WaitForSingleObject(waits[i],3000);CloseHandle(waits[i]);}return any;}
static BOOL SessionHasExplorerProcess(){
    DWORD mySession=0;if(!ProcessIdToSessionId(GetCurrentProcessId(),&mySession))return FALSE;
    HANDLE snap=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);if(snap==INVALID_HANDLE_VALUE)return FALSE;
    PROCESSENTRY32W pe;memset(&pe,0,sizeof(pe));pe.dwSize=sizeof(pe);BOOL found=FALSE;
    if(Process32FirstW(snap,&pe)){
        do{
            if(SameExeName(pe.szExeFile,L"explorer.exe")){
                DWORD sid=0;
                if(ProcessIdToSessionId(pe.th32ProcessID,&sid)&&sid==mySession){found=TRUE;break;}
            }
        }while(Process32NextW(snap,&pe));
    }
    CloseHandle(snap);return found;
}

static BOOL StartExplorerImmediately(){
    /*
    v0.2.10：v0.2.7 为了加快任务栏恢复，旧 Explorer 退出后立刻 CreateProcess(explorer.exe)。
    但 Windows 自己也可能在崩溃/终止后极快地重新拉起 Shell；两者发生竞态时，第二次 explorer.exe
    调用可能被解释为“再打开一个文件资源管理器窗口”，表现为恢复前先冒出一个 Home/此电脑窗口。

    这里仅给系统自动重拉一个非常短的 250 ms 进程级宽限：
    - 只要同一 Session 已经出现新的 explorer.exe 进程，就不再二次启动；
    - 250 ms 内没有出现，立即由本程序启动。
    相比旧 v0.2.6 的 1.2 秒固定等待，任务栏恢复仍然快得多；同时显著降低双启动竞态。
    */
    HWND tray=FindWindowW(L"Shell_TrayWnd",0);
    if(tray){
        DWORD_PTR r=0;
        if(SendMessageTimeoutW(tray,WM_NULL,0,0,SMTO_ABORTIFHUNG|SMTO_BLOCK,80,&r))return TRUE;
    }
    ULONGLONG graceStart=GetTickCount64();
    while(GetTickCount64()-graceStart<250){
        if(SessionHasExplorerProcess())return TRUE;
        Sleep(25);
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
