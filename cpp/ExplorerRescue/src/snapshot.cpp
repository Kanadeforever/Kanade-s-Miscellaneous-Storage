#include "snapshot.h"

/*
ERSESS2: 单文件五槽历史。

每个槽 512 KiB，前 64 字节为槽头，后面保存一个完整 ERSNAP2 快照。
写入顺序严格为“先 payload -> Flush -> 再 header -> Flush”。header 自身带哈希，payload 也带哈希。
因此若进程/系统在写入中途退出，只会让当前被覆盖的那个槽失效，其余四个历史槽仍可读取；
不需要 session.tmp/session.bak，也就能满足运行目录严格只有 EXE/config.ini/sessions.dat 三个文件。
*/

static const BYTE kSnapshotMagic[8] = {'E','R','S','N','A','P','2',0};
static const DWORD kSnapshotVersion = 3;
static const DWORD kSnapshotHeaderSize = 40;

static const BYTE kSlotMagic[8] = {'E','R','H','I','S','T','2',0};
static const DWORD kSlotVersion = 2;
static const DWORD kHistorySlots = 5;
static const DWORD kSlotHeaderSize = 64;
static const DWORD kSlotSize = 512u * 1024u;
static const DWORD kMaxPayload = kSlotSize - kSlotHeaderSize;

void SnapshotInit(Snapshot*s){if(s)memset(s,0,sizeof(*s));}
static void WindowFree(WindowState*w){if(!w)return;for(DWORD i=0;i<w->tabCount;++i)HFree(w->tabs[i].value);HFree(w->tabs);memset(w,0,sizeof(*w));}
void SnapshotFree(Snapshot*s){if(!s)return;for(DWORD i=0;i<s->windowCount;++i)WindowFree(&s->windows[i]);HFree(s->windows);SnapshotInit(s);}
WindowState* SnapshotFindWindowByHwnd(Snapshot*s,HWND hwnd){if(!s)return 0;for(DWORD i=0;i<s->windowCount;++i)if(s->windows[i].sourceHwnd==hwnd)return &s->windows[i];return 0;}
WindowState* SnapshotAddWindow(Snapshot*s,HWND hwnd,const WINDOWPLACEMENT*wp,BOOL hasPlacement,const RECT*screenRect,BOOL hasScreenRect){if(!s)return 0;if(s->windowCount>=s->windowCap){DWORD nc=s->windowCap?s->windowCap*2:8;WindowState*p=(WindowState*)HReAlloc(s->windows,nc*sizeof(WindowState),TRUE);if(!p)return 0;if(nc>s->windowCap)memset(p+s->windowCap,0,(nc-s->windowCap)*sizeof(WindowState));s->windows=p;s->windowCap=nc;}WindowState*w=&s->windows[s->windowCount++];memset(w,0,sizeof(*w));w->sourceHwnd=hwnd;w->hasPlacement=hasPlacement;if(wp)w->placement=*wp;else{memset(&w->placement,0,sizeof(w->placement));w->placement.length=sizeof(WINDOWPLACEMENT);}w->hasScreenRect=hasScreenRect;if(screenRect)w->screenRect=*screenRect;return w;}
BOOL WindowAddTab(WindowState*w,DWORD kind,const WCHAR*value){if(!w||!value||!value[0])return FALSE;if(w->tabCount>=w->tabCap){DWORD nc=w->tabCap?w->tabCap*2:4;TabState*p=(TabState*)HReAlloc(w->tabs,nc*sizeof(TabState),TRUE);if(!p)return FALSE;if(nc>w->tabCap)memset(p+w->tabCap,0,(nc-w->tabCap)*sizeof(TabState));w->tabs=p;w->tabCap=nc;}UINT n=WLen(value);WCHAR*copy=(WCHAR*)HAlloc((n+1)*sizeof(WCHAR),TRUE);if(!copy)return FALSE;memcpy(copy,value,n*sizeof(WCHAR));copy[n]=0;w->tabs[w->tabCount].kind=kind;w->tabs[w->tabCount].value=copy;++w->tabCount;return TRUE;}
DWORD SnapshotTotalTabs(const Snapshot*s){DWORD n=0;if(s)for(DWORD i=0;i<s->windowCount;++i)n+=s->windows[i].tabCount;return n;}

static BOOL AppendPlacement(ByteBuffer*p,const WindowState*w){const WINDOWPLACEMENT*q=&w->placement;if(!BufferAppendU32(p,w->hasPlacement?1:0))return FALSE;if(!BufferAppendU32(p,q->flags)||!BufferAppendU32(p,q->showCmd))return FALSE;LONG vals[12]={q->ptMinPosition.x,q->ptMinPosition.y,q->ptMaxPosition.x,q->ptMaxPosition.y,q->rcNormalPosition.left,q->rcNormalPosition.top,q->rcNormalPosition.right,q->rcNormalPosition.bottom,q->rcDevice.left,q->rcDevice.top,q->rcDevice.right,q->rcDevice.bottom};if(!BufferAppend(p,vals,sizeof(vals)))return FALSE;if(!BufferAppendU32(p,w->hasScreenRect?1:0))return FALSE;LONG sr[4]={w->screenRect.left,w->screenRect.top,w->screenRect.right,w->screenRect.bottom};if(!BufferAppend(p,sr,sizeof(sr)))return FALSE;return BufferAppendU32(p,w->tabCount);}

BOOL SerializeSnapshot(const Snapshot*s,ByteBuffer*out,ULONGLONG*outHash){if(outHash)*outHash=0;BufferInit(out);if(!s)return FALSE;ByteBuffer payload;BufferInit(&payload);BOOL ok=TRUE;for(DWORD i=0;i<s->windowCount&&ok;++i){const WindowState*w=&s->windows[i];ok=AppendPlacement(&payload,w);for(DWORD t=0;t<w->tabCount&&ok;++t){const TabState*tab=&w->tabs[t];UINT chars=WLen(tab->value);if(chars>32767){ok=FALSE;break;}ok=BufferAppendU32(&payload,tab->kind)&&BufferAppendU32(&payload,chars)&&BufferAppend(&payload,tab->value,chars*sizeof(WCHAR));}}if(!ok){BufferFree(&payload);return FALSE;}ULONGLONG hash=Hash64(payload.data,payload.size);FILETIME ft;GetSystemTimeAsFileTime(&ft);ULONGLONG ftime=((ULONGLONG)ft.dwHighDateTime<<32)|ft.dwLowDateTime;ok=BufferAppend(out,kSnapshotMagic,8)&&BufferAppendU32(out,kSnapshotVersion)&&BufferAppendU32(out,s->windowCount)&&BufferAppendU64(out,payload.size)&&BufferAppendU64(out,hash)&&BufferAppendU64(out,ftime)&&BufferAppend(out,payload.data,payload.size);BufferFree(&payload);if(!ok){BufferFree(out);return FALSE;}if(outHash)*outHash=hash;return TRUE;}

static BOOL ReadU32(const BYTE*&p,const BYTE*end,DWORD*out){if((SIZE_T)(end-p)<4)return FALSE;memcpy(out,p,4);p+=4;return TRUE;}
static BOOL ReadU64(const BYTE*&p,const BYTE*end,ULONGLONG*out){if((SIZE_T)(end-p)<8)return FALSE;memcpy(out,p,8);p+=8;return TRUE;}
static BOOL ReadLong(const BYTE*&p,const BYTE*end,LONG*out){if((SIZE_T)(end-p)<4)return FALSE;memcpy(out,p,4);p+=4;return TRUE;}

BOOL DeserializeSnapshot(const void*data,DWORD bytes,Snapshot*out,ULONGLONG*outHash){
    if(outHash)*outHash=0;SnapshotInit(out);if(!data||bytes<kSnapshotHeaderSize)return FALSE;
    const BYTE*base=(const BYTE*)data;if(memcmp(base,kSnapshotMagic,8)!=0)return FALSE;
    const BYTE*p=base+8;const BYTE*end=base+bytes;DWORD version=0,wcount=0;ULONGLONG psz=0,hash=0,ftime=0;
    if(!ReadU32(p,end,&version)||!ReadU32(p,end,&wcount)||!ReadU64(p,end,&psz)||!ReadU64(p,end,&hash)||!ReadU64(p,end,&ftime))return FALSE;
    if((version!=2&&version!=kSnapshotVersion)||wcount>512||psz!=(ULONGLONG)(end-p)||Hash64(p,(DWORD)psz)!=hash)return FALSE;
    for(DWORD wi=0;wi<wcount;++wi){
        DWORD has=0,flags=0,show=0,tabs=0;WINDOWPLACEMENT wp;memset(&wp,0,sizeof(wp));wp.length=sizeof(wp);
        if(!ReadU32(p,end,&has)||!ReadU32(p,end,&flags)||!ReadU32(p,end,&show)){SnapshotFree(out);return FALSE;}
        wp.flags=flags;wp.showCmd=show;
        LONG*vals[12]={&wp.ptMinPosition.x,&wp.ptMinPosition.y,&wp.ptMaxPosition.x,&wp.ptMaxPosition.y,&wp.rcNormalPosition.left,&wp.rcNormalPosition.top,&wp.rcNormalPosition.right,&wp.rcNormalPosition.bottom,&wp.rcDevice.left,&wp.rcDevice.top,&wp.rcDevice.right,&wp.rcDevice.bottom};
        for(int j=0;j<12;++j)if(!ReadLong(p,end,vals[j])){SnapshotFree(out);return FALSE;}
        DWORD hasScreen=0;RECT sr;memset(&sr,0,sizeof(sr));
        if(version>=3){
            if(!ReadU32(p,end,&hasScreen)){SnapshotFree(out);return FALSE;}
            LONG*rv[4]={&sr.left,&sr.top,&sr.right,&sr.bottom};
            for(int j=0;j<4;++j)if(!ReadLong(p,end,rv[j])){SnapshotFree(out);return FALSE;}
        }
        if(!ReadU32(p,end,&tabs)||tabs>2048){SnapshotFree(out);return FALSE;}
        WindowState*w=SnapshotAddWindow(out,0,&wp,has?TRUE:FALSE,version>=3?&sr:0,(version>=3&&hasScreen)?TRUE:FALSE);
        if(!w){SnapshotFree(out);return FALSE;}
        for(DWORD ti=0;ti<tabs;++ti){
            DWORD kind=0,chars=0;if(!ReadU32(p,end,&kind)||!ReadU32(p,end,&chars)||chars>32767||(SIZE_T)(end-p)<(SIZE_T)chars*sizeof(WCHAR)){SnapshotFree(out);return FALSE;}
            WCHAR*tmp=(WCHAR*)HAlloc((chars+1)*sizeof(WCHAR),TRUE);if(!tmp){SnapshotFree(out);return FALSE;}
            if(chars)memcpy(tmp,p,chars*sizeof(WCHAR));p+=chars*sizeof(WCHAR);tmp[chars]=0;
            BOOL a=WindowAddTab(w,kind,tmp);HFree(tmp);if(!a){SnapshotFree(out);return FALSE;}
        }
    }
    if(p!=end){SnapshotFree(out);return FALSE;}out->contentHash=hash;if(outHash)*outHash=hash;return TRUE;
}

struct SlotInfo {
    BOOL valid;
    DWORD index;
    ULONGLONG generation;
    ULONGLONG capturedTime;
    DWORD payloadBytes;
    DWORD windowCount;
    DWORD tabCount;
    ULONGLONG payloadHash;
    ULONGLONG contentHash;
};

static BOOL SeekAbs(HANDLE h,ULONGLONG off){LARGE_INTEGER q;q.QuadPart=(LONGLONG)off;return SetFilePointerEx(h,q,0,FILE_BEGIN);}
static BOOL ReadExact(HANDLE h,void*data,DWORD bytes){BYTE*p=(BYTE*)data;DWORD done=0;while(done<bytes){DWORD got=0;if(!ReadFile(h,p+done,bytes-done,&got,0)||!got)return FALSE;done+=got;}return TRUE;}
static BOOL WriteExact(HANDLE h,const void*data,DWORD bytes){const BYTE*p=(const BYTE*)data;DWORD done=0;while(done<bytes){DWORD wrote=0;if(!WriteFile(h,p+done,bytes-done,&wrote,0)||!wrote)return FALSE;done+=wrote;}return TRUE;}

static void BuildSlotHeader(BYTE*h,ULONGLONG generation,ULONGLONG capturedTime,DWORD payloadBytes,DWORD windows,DWORD tabs,ULONGLONG payloadHash){memset(h,0,kSlotHeaderSize);memcpy(h,kSlotMagic,8);DWORD v=kSlotVersion,hs=kSlotHeaderSize;memcpy(h+8,&v,4);memcpy(h+12,&hs,4);memcpy(h+16,&generation,8);memcpy(h+24,&capturedTime,8);memcpy(h+32,&payloadBytes,4);memcpy(h+36,&windows,4);memcpy(h+40,&tabs,4);DWORD reserved=0;memcpy(h+44,&reserved,4);memcpy(h+48,&payloadHash,8);ULONGLONG hh=Hash64(h,56);memcpy(h+56,&hh,8);}
static BOOL ParseSlotHeader(const BYTE*h,SlotInfo*out){memset(out,0,sizeof(*out));if(memcmp(h,kSlotMagic,8)!=0)return FALSE;DWORD v=0,hs=0;memcpy(&v,h+8,4);memcpy(&hs,h+12,4);if(v!=kSlotVersion||hs!=kSlotHeaderSize)return FALSE;ULONGLONG stored=0;memcpy(&stored,h+56,8);if(Hash64(h,56)!=stored)return FALSE;memcpy(&out->generation,h+16,8);memcpy(&out->capturedTime,h+24,8);memcpy(&out->payloadBytes,h+32,4);memcpy(&out->windowCount,h+36,4);memcpy(&out->tabCount,h+40,4);memcpy(&out->payloadHash,h+48,8);if(!out->generation||!out->payloadBytes||out->payloadBytes>kMaxPayload)return FALSE;return TRUE;}

static BOOL ReadSlot(HANDLE h,DWORD index,SlotInfo*out,Snapshot*optionalSnapshot){BYTE head[kSlotHeaderSize];ULONGLONG base=(ULONGLONG)index*kSlotSize;if(!SeekAbs(h,base)||!ReadExact(h,head,kSlotHeaderSize))return FALSE;SlotInfo si;if(!ParseSlotHeader(head,&si))return FALSE;ByteBuffer b;BufferInit(&b);if(!BufferReserve(&b,si.payloadBytes))return FALSE;if(!SeekAbs(h,base+kSlotHeaderSize)||!ReadExact(h,b.data,si.payloadBytes)){BufferFree(&b);return FALSE;}b.size=si.payloadBytes;if(Hash64(b.data,b.size)!=si.payloadHash){BufferFree(&b);return FALSE;}Snapshot tmp;ULONGLONG content=0;if(!DeserializeSnapshot(b.data,b.size,&tmp,&content)){BufferFree(&b);return FALSE;}BufferFree(&b);if(tmp.windowCount!=si.windowCount||SnapshotTotalTabs(&tmp)!=si.tabCount){SnapshotFree(&tmp);return FALSE;}si.valid=TRUE;si.index=index;si.contentHash=content;if(out)*out=si;if(optionalSnapshot)*optionalSnapshot=tmp;else SnapshotFree(&tmp);return TRUE;}

static BOOL ScanSlots(HANDLE h,SlotInfo slots[kHistorySlots],DWORD*validCount){if(validCount)*validCount=0;for(DWORD i=0;i<kHistorySlots;++i){memset(&slots[i],0,sizeof(SlotInfo));slots[i].index=i;SlotInfo si;if(ReadSlot(h,i,&si,0)){slots[i]=si;if(validCount)++*validCount;}}return TRUE;}

BOOL SaveSnapshotHistory(const Snapshot*s,BOOL*changed){if(changed)*changed=FALSE;if(!s)return FALSE;ByteBuffer b;ULONGLONG contentHash=0;if(!SerializeSnapshot(s,&b,&contentHash))return FALSE;if(!b.size||b.size>kMaxPayload){BufferFree(&b);return FALSE;}HANDLE h=CreateFileW(g_sessionPath,GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ,0,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,0);if(h==INVALID_HANDLE_VALUE){BufferFree(&b);return FALSE;}SlotInfo slots[kHistorySlots];DWORD valid=0;ScanSlots(h,slots,&valid);ULONGLONG maxGen=0,minGen=~0ULL;DWORD target=0;BOOL haveInvalid=FALSE;ULONGLONG latestHash=0;for(DWORD i=0;i<kHistorySlots;++i){if(slots[i].valid){if(slots[i].generation>maxGen){maxGen=slots[i].generation;latestHash=slots[i].contentHash;}if(slots[i].generation<minGen){minGen=slots[i].generation;target=i;}}else if(!haveInvalid){target=i;haveInvalid=TRUE;}}if(maxGen&&latestHash==contentHash){CloseHandle(h);BufferFree(&b);return TRUE;}if(!haveInvalid&&minGen==~0ULL)target=0;ULONGLONG gen=maxGen+1;if(!gen)gen=1;FILETIME ft;GetSystemTimeAsFileTime(&ft);ULONGLONG time=((ULONGLONG)ft.dwHighDateTime<<32)|ft.dwLowDateTime;ULONGLONG base=(ULONGLONG)target*kSlotSize;BOOL ok=SeekAbs(h,base+kSlotHeaderSize)&&WriteExact(h,b.data,b.size)&&FlushFileBuffers(h);if(ok){BYTE head[kSlotHeaderSize];BuildSlotHeader(head,gen,time,b.size,s->windowCount,SnapshotTotalTabs(s),Hash64(b.data,b.size));ok=SeekAbs(h,base)&&WriteExact(h,head,kSlotHeaderSize)&&FlushFileBuffers(h);}CloseHandle(h);BufferFree(&b);if(ok&&changed)*changed=TRUE;return ok;}

BOOL LoadLastSnapshot(Snapshot*out){SnapshotInit(out);HANDLE h=CreateFileW(g_sessionPath,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,0,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,0);if(h==INVALID_HANDLE_VALUE)return FALSE;SlotInfo best;memset(&best,0,sizeof(best));for(DWORD i=0;i<kHistorySlots;++i){SlotInfo si;if(ReadSlot(h,i,&si,0)&&(!best.valid||si.generation>best.generation))best=si;}if(!best.valid){CloseHandle(h);return FALSE;}BOOL ok=ReadSlot(h,best.index,&best,out);CloseHandle(h);return ok;}

DWORD CountValidHistorySlots(){HANDLE h=CreateFileW(g_sessionPath,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,0,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,0);if(h==INVALID_HANDLE_VALUE)return 0;DWORD count=0;for(DWORD i=0;i<kHistorySlots;++i){SlotInfo si;if(ReadSlot(h,i,&si,0))++count;}CloseHandle(h);return count;}
