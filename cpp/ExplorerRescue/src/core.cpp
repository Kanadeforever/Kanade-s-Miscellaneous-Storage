#include "core.h"

/*
Explorer Rescue v0.2.6 便携运行时：
- 所有持久数据只放 EXE 同目录；目录不可写就明确失败，不回退 AppData，不写注册表。
- 首次运行只生成 config.ini 与 sessions.dat，因此运行目录始终只有三个运行时文件。
- config.ini 为 UTF-16LE，便于 Windows 记事本直接编辑；修改后重启程序生效。
*/

HANDLE g_heap = 0;
WCHAR g_exePath[32768];
WCHAR g_appDir[32768];
WCHAR g_configPath[32768];
WCHAR g_sessionPath[32768];

extern "C" void* memcpy(void* d, const void* s, unsigned long long n) { BYTE* dd=(BYTE*)d;const BYTE*ss=(const BYTE*)s;for(unsigned long long i=0;i<n;++i)dd[i]=ss[i];return d; }
extern "C" void* memset(void* d, int c, unsigned long long n) { BYTE*dd=(BYTE*)d;for(unsigned long long i=0;i<n;++i)dd[i]=(BYTE)c;return d; }
extern "C" void* memmove(void* d, const void* s, unsigned long long n) { BYTE*dd=(BYTE*)d;const BYTE*ss=(const BYTE*)s;if(dd<ss){for(unsigned long long i=0;i<n;++i)dd[i]=ss[i];}else if(dd>ss){for(unsigned long long i=n;i>0;--i)dd[i-1]=ss[i-1];}return d; }
extern "C" int memcmp(const void* a,const void* b,unsigned long long n){const BYTE*aa=(const BYTE*)a;const BYTE*bb=(const BYTE*)b;for(unsigned long long i=0;i<n;++i){if(aa[i]!=bb[i])return aa[i]<bb[i]?-1:1;}return 0;}

void RuntimeInit(){g_heap=GetProcessHeap();}
void* HAlloc(SIZE_T bytes,BOOL zero){if(!g_heap)RuntimeInit();return HeapAlloc(g_heap,zero?HEAP_ZERO_MEMORY:0,bytes?bytes:1);}
void* HReAlloc(void* p,SIZE_T bytes,BOOL zero){if(!p)return HAlloc(bytes,zero);return HeapReAlloc(g_heap,zero?HEAP_ZERO_MEMORY:0,p,bytes?bytes:1);}
void HFree(void* p){if(p&&g_heap)HeapFree(g_heap,0,p);}

UINT WLen(const WCHAR*s){UINT n=0;if(!s)return 0;while(s[n])++n;return n;}
void WCopy(WCHAR*dst,UINT cap,const WCHAR*src){if(!dst||!cap)return;UINT i=0;if(src)for(;i+1<cap&&src[i];++i)dst[i]=src[i];dst[i]=0;}
void WAppend(WCHAR*dst,UINT cap,const WCHAR*src){UINT n=WLen(dst);if(n<cap)WCopy(dst+n,cap-n,src);}
void WAppendU32(WCHAR*dst,UINT cap,DWORD value){WCHAR tmp[16];UINT n=0;if(!value)tmp[n++]=L'0';else{WCHAR rev[16];UINT r=0;while(value&&r<15){rev[r++]=(WCHAR)(L'0'+value%10);value/=10;}while(r)tmp[n++]=rev[--r];}tmp[n]=0;WAppend(dst,cap,tmp);}
BOOL WEqual(const WCHAR*a,const WCHAR*b){if(a==b)return TRUE;if(!a||!b)return FALSE;UINT la=WLen(a),lb=WLen(b);if(la!=lb)return FALSE;for(UINT i=0;i<la;++i)if(a[i]!=b[i])return FALSE;return TRUE;}
BOOL WEqualI(const WCHAR*a,const WCHAR*b){if(a==b)return TRUE;if(!a||!b)return FALSE;UINT la=WLen(a),lb=WLen(b);if(la!=lb)return FALSE;return CompareStringOrdinal(a,(int)la,b,(int)lb,TRUE)==CSTR_EQUAL;}
BOOL WStartsWithI(const WCHAR*s,const WCHAR*prefix){if(!s||!prefix)return FALSE;UINT ls=WLen(s),lp=WLen(prefix);if(lp>ls)return FALSE;return CompareStringOrdinal(s,(int)lp,prefix,(int)lp,TRUE)==CSTR_EQUAL;}
BOOL WContainsArg(const WCHAR*cmd,const WCHAR*arg){if(!cmd||!arg)return FALSE;UINT lc=WLen(cmd),la=WLen(arg);if(!la||la>lc)return FALSE;for(UINT i=0;i+la<=lc;++i){BOOL left=(i==0||cmd[i-1]==L' '||cmd[i-1]==L'\t'||cmd[i-1]==L'\"');if(!left)continue;if(CompareStringOrdinal(cmd+i,(int)la,arg,(int)la,TRUE)!=CSTR_EQUAL)continue;WCHAR r=(i+la<lc)?cmd[i+la]:0;if(!r||r==L' '||r==L'\t'||r==L'\"')return TRUE;}return FALSE;}
void PathDirname(const WCHAR*path,WCHAR*out,UINT cap){WCopy(out,cap,path);UINT n=WLen(out);while(n){WCHAR c=out[n-1];if(c==L'\\'||c==L'/'){out[n-1]=0;return;}--n;}WCopy(out,cap,L".");}
void PathJoin(const WCHAR*a,const WCHAR*b,WCHAR*out,UINT cap){WCopy(out,cap,a);UINT n=WLen(out);if(n&&out[n-1]!=L'\\'&&out[n-1]!=L'/')WAppend(out,cap,L"\\");WAppend(out,cap,b);}

BOOL BuildAppPaths(){DWORD n=GetModuleFileNameW(0,g_exePath,32768);if(!n||n>=32767)return FALSE;PathDirname(g_exePath,g_appDir,32768);PathJoin(g_appDir,L"config.ini",g_configPath,32768);PathJoin(g_appDir,L"sessions.dat",g_sessionPath,32768);return TRUE;}

static const WCHAR kDefaultConfig[] =
    L"\xFEFF; Explorer Rescue 配置文件\r\n"
    L"; 修改后请退出并重新运行 ExplorerRescue.exe。\r\n"
    L"; 程序固定保留最近 5 个不同的完整有效会话；完全相同的扫描不会重复占用历史槽。\r\n"
    L"; 扫描一旦连续重试仍无法完整读取全部窗口/标签，会自动暂停，防止残缺数据覆盖历史。\r\n"
    L"\r\n[Scan]\r\n"
    L"; 后台完整扫描间隔。建议 5~60 秒。\r\n"
    L"IntervalSeconds=5\r\n"
    L"; 完整扫描独立超时，与 IntervalSeconds 无关。建议保持默认 20000。\r\n"
    L"TimeoutMilliseconds=20000\r\n"
    L"\r\n[Notification]\r\n"
    L"; 扫描暂停通知显示时间。\r\n"
    L"DurationSeconds=8\r\n";

BOOL EnsurePortableFiles(){
    if(!FileExists(g_configPath)){
        DWORD bytes=WLen(kDefaultConfig)*sizeof(WCHAR);
        if(!WriteWholeFile(g_configPath,kDefaultConfig,bytes,TRUE))return FALSE;
    }
    if(!FileExists(g_sessionPath)){
        if(!WriteWholeFile(g_sessionPath,0,0,TRUE))return FALSE;
    }
    return TRUE;
}

static DWORD Clamp(DWORD v,DWORD lo,DWORD hi,DWORD defv){if(v<lo||v>hi)return defv;return v;}
void LoadAppConfig(AppConfig*cfg){if(!cfg)return;DWORD sec=GetPrivateProfileIntW(L"Scan",L"IntervalSeconds",5,g_configPath);DWORD timeout=GetPrivateProfileIntW(L"Scan",L"TimeoutMilliseconds",20000,g_configPath);DWORD notify=GetPrivateProfileIntW(L"Notification",L"DurationSeconds",8,g_configPath);sec=Clamp(sec,5,600,5);timeout=Clamp(timeout,10000,120000,20000);notify=Clamp(notify,3,30,8);cfg->scanIntervalMs=sec*1000;cfg->scanTimeoutMs=timeout;cfg->notificationMs=notify*1000;}

void BufferInit(ByteBuffer*b){b->data=0;b->size=0;b->cap=0;}
void BufferFree(ByteBuffer*b){if(b&&b->data)HFree(b->data);if(b){b->data=0;b->size=b->cap=0;}}
BOOL BufferReserve(ByteBuffer*b,DWORD extra){if(!b)return FALSE;ULONGLONG need=(ULONGLONG)b->size+extra;if(need>0x7fffffffULL)return FALSE;if(need<=b->cap)return TRUE;DWORD cap=b->cap?b->cap:1024;while(cap<need){DWORD next=cap<0x40000000?cap*2:(DWORD)need;if(next<cap){cap=(DWORD)need;break;}cap=next;}BYTE*p=(BYTE*)HReAlloc(b->data,cap,FALSE);if(!p)return FALSE;b->data=p;b->cap=cap;return TRUE;}
BOOL BufferAppend(ByteBuffer*b,const void*data,DWORD bytes){if(!BufferReserve(b,bytes))return FALSE;if(bytes)memcpy(b->data+b->size,data,bytes);b->size+=bytes;return TRUE;}
BOOL BufferAppendU32(ByteBuffer*b,DWORD v){return BufferAppend(b,&v,4);}
BOOL BufferAppendU64(ByteBuffer*b,ULONGLONG v){return BufferAppend(b,&v,8);}
ULONGLONG Hash64(const void*data,DWORD bytes){const BYTE*p=(const BYTE*)data;ULONGLONG h=1469598103934665603ULL;for(DWORD i=0;i<bytes;++i){h^=p[i];h*=1099511628211ULL;}return h;}

BOOL WriteWholeFile(const WCHAR*path,const void*data,DWORD bytes,BOOL flush){HANDLE h=CreateFileW(path,GENERIC_WRITE,FILE_SHARE_READ,0,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,0);if(h==INVALID_HANDLE_VALUE)return FALSE;const BYTE*p=(const BYTE*)data;DWORD done=0;BOOL ok=TRUE;while(done<bytes){DWORD wrote=0;if(!WriteFile(h,p+done,bytes-done,&wrote,0)||!wrote){ok=FALSE;break;}done+=wrote;}if(ok&&flush)ok=FlushFileBuffers(h);CloseHandle(h);return ok;}
BOOL ReadWholeFile(const WCHAR*path,ByteBuffer*out,DWORD maxBytes){BufferInit(out);HANDLE h=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,0,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,0);if(h==INVALID_HANDLE_VALUE)return FALSE;LARGE_INTEGER sz;if(!GetFileSizeEx(h,&sz)||sz.QuadPart<0||(ULONGLONG)sz.QuadPart>maxBytes){CloseHandle(h);return FALSE;}DWORD n=(DWORD)sz.QuadPart;if(!BufferReserve(out,n)){CloseHandle(h);return FALSE;}DWORD done=0;BOOL ok=TRUE;while(done<n){DWORD got=0;if(!ReadFile(h,out->data+done,n-done,&got,0)){ok=FALSE;break;}if(!got)break;done+=got;}CloseHandle(h);if(!ok||done!=n){BufferFree(out);return FALSE;}out->size=n;return TRUE;}
BOOL FileExists(const WCHAR*path){DWORD a=GetFileAttributesW(path);return a!=INVALID_FILE_ATTRIBUTES;}
BOOL WriteFileIfChanged(const WCHAR*path,const void*data,DWORD bytes){ByteBuffer old;if(ReadWholeFile(path,&old,1024*1024)){BOOL same=old.size==bytes&&(!bytes||memcmp(old.data,data,bytes)==0);BufferFree(&old);if(same)return TRUE;}return WriteWholeFile(path,data,bytes,TRUE);}

void ShowError(const WCHAR*text){MessageBoxW(0,text,L"Explorer Rescue",MB_OK|MB_ICONERROR|MB_TOPMOST);}
void ShowWarning(const WCHAR*text){MessageBoxW(0,text,L"Explorer Rescue",MB_OK|MB_ICONWARNING|MB_TOPMOST);}
void ShowInfo(const WCHAR*text){MessageBoxW(0,text,L"Explorer Rescue",MB_OK|MB_ICONINFORMATION|MB_TOPMOST);}
