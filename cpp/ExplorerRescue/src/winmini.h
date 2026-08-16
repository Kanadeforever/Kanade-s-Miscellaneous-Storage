#pragma once

// Explorer Rescue 使用的最小 Win32 / COM 声明。
// 目的：不依赖 Windows SDK 头文件和 CRT，也能在本构建环境中交叉编译原生 x64 PE。
// 在用户的 Visual Studio / Windows SDK 环境下，也可以改为包含 windows.h / oleauto.h 等官方头文件。

#ifdef _MSC_VER
#define WINAPI __stdcall
#define CALLBACK __stdcall
#define STDMETHODCALLTYPE __stdcall
#define DLLIMPORT __declspec(dllimport)
#else
#define WINAPI __attribute__((stdcall))
#define CALLBACK __attribute__((stdcall))
#define STDMETHODCALLTYPE __attribute__((stdcall))
#define DLLIMPORT __attribute__((dllimport))
#endif

#define NULLPTR ((void*)0)

typedef void VOID;
typedef int BOOL;
typedef unsigned char BYTE;
typedef unsigned short WORD;
typedef unsigned int UINT;
typedef unsigned long DWORD;
typedef long LONG;
typedef unsigned long ULONG;
typedef long HRESULT;
typedef unsigned long long ULONGLONG;
typedef long long LONGLONG;
typedef wchar_t WCHAR;
typedef WCHAR* LPWSTR;
typedef const WCHAR* LPCWSTR;
typedef const WCHAR* LPCWCH;
typedef void* LPVOID;
typedef const void* LPCVOID;
typedef unsigned long long ULONG_PTR;
typedef long long LONG_PTR;
typedef ULONG_PTR DWORD_PTR;
typedef ULONG_PTR WPARAM;
typedef LONG_PTR LPARAM;
typedef LONG_PTR LRESULT;
typedef ULONG_PTR SIZE_T;
typedef LONG_PTR INT_PTR;
typedef ULONG_PTR UINT_PTR;
typedef void* HANDLE;
typedef HANDLE HWND;
typedef HANDLE HINSTANCE;
typedef HANDLE HMODULE;
typedef HANDLE HICON;
typedef HANDLE HCURSOR;
typedef HANDLE HBRUSH;
typedef HANDLE HMENU;
typedef HANDLE HMONITOR;
typedef HANDLE HWINEVENTHOOK;
typedef unsigned short VARTYPE;
typedef LONG DISPID;
typedef DWORD LCID;
typedef WCHAR OLECHAR;
typedef OLECHAR* LPOLESTR;
typedef WCHAR* BSTR;
typedef short VARIANT_BOOL;

struct GUID {
    DWORD Data1;
    WORD Data2;
    WORD Data3;
    BYTE Data4[8];
};
typedef GUID IID;
typedef GUID CLSID;

struct POINT { LONG x; LONG y; };
struct RECT { LONG left; LONG top; LONG right; LONG bottom; };

struct WINDOWPLACEMENT {
    UINT length;
    UINT flags;
    UINT showCmd;
    POINT ptMinPosition;
    POINT ptMaxPosition;
    RECT rcNormalPosition;
    RECT rcDevice;
};

struct MONITORINFO {
    DWORD cbSize;
    RECT rcMonitor;
    RECT rcWork;
    DWORD dwFlags;
};

struct NOTIFYICONDATAW {
    DWORD cbSize;
    HWND hWnd;
    UINT uID;
    UINT uFlags;
    UINT uCallbackMessage;
    HICON hIcon;
    WCHAR szTip[128];
    DWORD dwState;
    DWORD dwStateMask;
    WCHAR szInfo[256];
    union { UINT uTimeout; UINT uVersion; };
    WCHAR szInfoTitle[64];
    DWORD dwInfoFlags;
    GUID guidItem;
    HICON hBalloonIcon;
};

struct FILETIME { DWORD dwLowDateTime; DWORD dwHighDateTime; };
struct LARGE_INTEGER { LONGLONG QuadPart; };

struct SECURITY_ATTRIBUTES {
    DWORD nLength;
    LPVOID lpSecurityDescriptor;
    BOOL bInheritHandle;
};

struct STARTUPINFOW {
    DWORD cb;
    LPWSTR lpReserved;
    LPWSTR lpDesktop;
    LPWSTR lpTitle;
    DWORD dwX;
    DWORD dwY;
    DWORD dwXSize;
    DWORD dwYSize;
    DWORD dwXCountChars;
    DWORD dwYCountChars;
    DWORD dwFillAttribute;
    DWORD dwFlags;
    WORD wShowWindow;
    WORD cbReserved2;
    BYTE* lpReserved2;
    HANDLE hStdInput;
    HANDLE hStdOutput;
    HANDLE hStdError;
};

struct PROCESS_INFORMATION {
    HANDLE hProcess;
    HANDLE hThread;
    DWORD dwProcessId;
    DWORD dwThreadId;
};

#define MAX_PATH 260
struct PROCESSENTRY32W {
    DWORD dwSize;
    DWORD cntUsage;
    DWORD th32ProcessID;
    ULONG_PTR th32DefaultHeapID;
    DWORD th32ModuleID;
    DWORD cntThreads;
    DWORD th32ParentProcessID;
    LONG pcPriClassBase;
    DWORD dwFlags;
    WCHAR szExeFile[MAX_PATH];
};

struct MSG {
    HWND hwnd;
    UINT message;
    WPARAM wParam;
    LPARAM lParam;
    DWORD time;
    POINT pt;
    DWORD lPrivate;
};

typedef LRESULT (CALLBACK *WNDPROC)(HWND, UINT, WPARAM, LPARAM);
struct WNDCLASSEXW {
    UINT cbSize;
    UINT style;
    WNDPROC lpfnWndProc;
    int cbClsExtra;
    int cbWndExtra;
    HINSTANCE hInstance;
    HICON hIcon;
    HCURSOR hCursor;
    HBRUSH hbrBackground;
    LPCWSTR lpszMenuName;
    LPCWSTR lpszClassName;
    HICON hIconSm;
};

typedef VOID (CALLBACK *WINEVENTPROC)(HWINEVENTHOOK, DWORD, HWND, LONG, LONG, DWORD, DWORD);
typedef DWORD (WINAPI *LPTHREAD_START_ROUTINE)(LPVOID);

// OLE Automation
struct IDispatch;
struct IUnknown;

struct VARIANT {
    VARTYPE vt;
    WORD wReserved1;
    WORD wReserved2;
    WORD wReserved3;
    union {
        LONGLONG llVal;
        LONG lVal;
        BYTE bVal;
        short iVal;
        float fltVal;
        double dblVal;
        VARIANT_BOOL boolVal;
        LONGLONG scode64;
        BSTR bstrVal;
        IUnknown* punkVal;
        IDispatch* pdispVal;
        ULONG_PTR ullVal;
        LONG_PTR intVal;
        ULONG_PTR uintVal;
        LPVOID byref;
        /*
        Windows x64 VARIANT 的内部 union 还必须能容纳 BRECORD：
        { PVOID pvRecord; IRecordInfo* pRecInfo; }，共两个指针 = 16 字节。
        即使本项目不使用 VT_RECORD，也必须保留正确 ABI 尺寸，否则 IDispatch::Invoke
        写回 VARIANT 时会越界。
        */
        struct { LPVOID pvRecord; LPVOID pRecInfo; } brecord;
        BYTE _abiPad[16];
    } data;
};
typedef VARIANT VARIANTARG;
static_assert(sizeof(VARIANT)==24, "Windows x64 VARIANT ABI must be 24 bytes");

struct DISPPARAMS {
    VARIANTARG* rgvarg;
    DISPID* rgdispidNamedArgs;
    UINT cArgs;
    UINT cNamedArgs;
};

struct EXCEPINFO {
    WORD wCode;
    WORD wReserved;
    BSTR bstrSource;
    BSTR bstrDescription;
    BSTR bstrHelpFile;
    DWORD dwHelpContext;
    LPVOID pvReserved;
    HRESULT (STDMETHODCALLTYPE *pfnDeferredFillIn)(EXCEPINFO*);
    HRESULT scode;
};

struct IUnknownVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(IUnknown*, const IID*, void**);
    ULONG (STDMETHODCALLTYPE *AddRef)(IUnknown*);
    ULONG (STDMETHODCALLTYPE *Release)(IUnknown*);
};
struct IUnknown { IUnknownVtbl* lpVtbl; };

struct IDispatchVtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(IDispatch*, const IID*, void**);
    ULONG (STDMETHODCALLTYPE *AddRef)(IDispatch*);
    ULONG (STDMETHODCALLTYPE *Release)(IDispatch*);
    HRESULT (STDMETHODCALLTYPE *GetTypeInfoCount)(IDispatch*, UINT*);
    HRESULT (STDMETHODCALLTYPE *GetTypeInfo)(IDispatch*, UINT, LCID, void**);
    HRESULT (STDMETHODCALLTYPE *GetIDsOfNames)(IDispatch*, const IID*, LPOLESTR*, UINT, LCID, DISPID*);
    HRESULT (STDMETHODCALLTYPE *Invoke)(IDispatch*, DISPID, const IID*, LCID, WORD, DISPPARAMS*, VARIANT*, EXCEPINFO*, UINT*);
};
struct IDispatch { IDispatchVtbl* lpVtbl; };

// 常量
#define TRUE 1
#define FALSE 0
#define S_OK ((HRESULT)0L)
#define S_FALSE ((HRESULT)1L)
#define FAILED(hr) (((HRESULT)(hr)) < 0)
#define SUCCEEDED(hr) (((HRESULT)(hr)) >= 0)

#define VT_EMPTY 0
#define VT_NULL 1
#define VT_I2 2
#define VT_I4 3
#define VT_BSTR 8
#define VT_DISPATCH 9
#define VT_BOOL 11
#define VT_UNKNOWN 13
#define VT_I8 20
#define VT_UI8 21
#define VT_INT 22
#define VT_UINT 23
#define VT_UI4 19

#define DISPATCH_METHOD 0x1
#define DISPATCH_PROPERTYGET 0x2

#define COINIT_APARTMENTTHREADED 0x2
#define CLSCTX_INPROC_SERVER 0x1
#define CLSCTX_LOCAL_SERVER 0x4

#define HEAP_ZERO_MEMORY 0x00000008
#define INVALID_HANDLE_VALUE ((HANDLE)(LONG_PTR)-1)
#define INVALID_FILE_ATTRIBUTES 0xFFFFFFFFUL
#define ERROR_ALREADY_EXISTS 183
#define ERROR_FILE_NOT_FOUND 2
#define ERROR_PATH_NOT_FOUND 3

#define GENERIC_READ 0x80000000UL
#define GENERIC_WRITE 0x40000000UL
#define FILE_SHARE_READ 0x00000001
#define FILE_SHARE_WRITE 0x00000002
#define CREATE_ALWAYS 2
#define OPEN_EXISTING 3
#define OPEN_ALWAYS 4
#define FILE_BEGIN 0
#define FILE_ATTRIBUTE_NORMAL 0x00000080
#define MOVEFILE_REPLACE_EXISTING 0x00000001
#define MOVEFILE_WRITE_THROUGH 0x00000008

#define TH32CS_SNAPPROCESS 0x00000002
#define PROCESS_TERMINATE 0x0001
#define PROCESS_SYNCHRONIZE 0x00100000
#define WAIT_OBJECT_0 0x00000000
#define WAIT_TIMEOUT 258
#define INFINITE 0xFFFFFFFFUL
#define STILL_ACTIVE 259
#define CREATE_NO_WINDOW 0x08000000
#define CREATE_UNICODE_ENVIRONMENT 0x00000400
#define STACK_SIZE_PARAM_IS_A_RESERVATION 0x00010000

#define WM_NULL 0x0000
#define WM_CLOSE 0x0010
#define WM_DESTROY 0x0002
#define WM_COMMAND 0x0111
#define WM_HOTKEY 0x0312
#define WM_TIMER 0x0113
#define WM_APP 0x8000
#define WM_USER 0x0400
#define WM_CONTEXTMENU 0x007B
#define WM_LBUTTONDBLCLK 0x0203

#define NIN_SELECT (WM_USER + 0)
#define NIN_KEYSELECT (WM_USER + 1)

#define NIM_ADD 0x00000000
#define NIM_MODIFY 0x00000001
#define NIM_DELETE 0x00000002
#define NIM_SETFOCUS 0x00000003
#define NIM_SETVERSION 0x00000004
#define NOTIFYICON_VERSION_4 4
#define NIF_MESSAGE 0x00000001
#define NIF_ICON 0x00000002
#define NIF_TIP 0x00000004
#define NIF_INFO 0x00000010
#define NIF_REALTIME 0x00000040
#define NIF_SHOWTIP 0x00000080
#define NIIF_INFO 0x00000001
#define NIIF_WARNING 0x00000002
#define NIIF_ERROR 0x00000003
#define NIIF_NOSOUND 0x00000010

#define MF_STRING 0x00000000
#define MF_SEPARATOR 0x00000800
#define MF_GRAYED 0x00000001
#define TPM_RIGHTBUTTON 0x0002
#define TPM_RETURNCMD 0x0100
#define IMAGE_ICON 1
#define LR_DEFAULTSIZE 0x00000040

#define MOD_SHIFT 0x0004
#define MOD_WIN 0x0008
#define MOD_NOREPEAT 0x4000

#define GA_ROOT 2
#define SMTO_ABORTIFHUNG 0x0002
#define SMTO_BLOCK 0x0001

#define WINEVENT_OUTOFCONTEXT 0x0000
#define WINEVENT_SKIPOWNPROCESS 0x0002
#define EVENT_OBJECT_CREATE 0x8000
#define EVENT_OBJECT_DESTROY 0x8001
#define EVENT_OBJECT_LOCATIONCHANGE 0x800B
#define EVENT_OBJECT_NAMECHANGE 0x800C
#define EVENT_SYSTEM_MINIMIZESTART 0x0016
#define EVENT_SYSTEM_MINIMIZEEND 0x0017

#define SW_SHOWNORMAL 1
#define SW_SHOWMINIMIZED 2
#define SW_SHOWMAXIMIZED 3
#define SW_MINIMIZE 6
#define SW_SHOWMINNOACTIVE 7
#define SW_FORCEMINIMIZE 11
#define SW_RESTORE 9

#define SWP_NOSIZE 0x0001
#define SWP_NOMOVE 0x0002
#define SWP_NOZORDER 0x0004
#define SWP_NOACTIVATE 0x0010

#define MONITOR_DEFAULTTONULL 0x00000000
#define SPI_GETWORKAREA 0x0030

#define MB_OK 0x00000000
#define MB_ICONERROR 0x00000010
#define MB_ICONWARNING 0x00000030
#define MB_ICONINFORMATION 0x00000040
#define MB_TOPMOST 0x00040000

#define WS_POPUP 0x80000000UL
#define WS_VISIBLE 0x10000000UL
#define WS_BORDER 0x00800000UL
#define SS_LEFT 0x00000000UL
#define SS_NOPREFIX 0x00000080UL
#define WS_EX_TOPMOST 0x00000008UL
#define WS_EX_TOOLWINDOW 0x00000080UL
#define WS_EX_NOACTIVATE 0x08000000UL

#define CSTR_EQUAL 2

#define HWND_MESSAGE ((HWND)(LONG_PTR)-3)

// Kernel32
extern "C" {
DLLIMPORT VOID WINAPI ExitProcess(UINT);
DLLIMPORT HANDLE WINAPI GetProcessHeap();
DLLIMPORT LPVOID WINAPI HeapAlloc(HANDLE,DWORD,SIZE_T);
DLLIMPORT LPVOID WINAPI HeapReAlloc(HANDLE,DWORD,LPVOID,SIZE_T);
DLLIMPORT BOOL WINAPI HeapFree(HANDLE,DWORD,LPVOID);
DLLIMPORT DWORD WINAPI GetLastError();
DLLIMPORT HANDLE WINAPI CreateMutexW(SECURITY_ATTRIBUTES*,BOOL,LPCWSTR);
DLLIMPORT BOOL WINAPI CloseHandle(HANDLE);
DLLIMPORT DWORD WINAPI GetModuleFileNameW(HMODULE,LPWSTR,DWORD);
DLLIMPORT LPWSTR WINAPI GetCommandLineW();
DLLIMPORT DWORD WINAPI GetEnvironmentVariableW(LPCWSTR,LPWSTR,DWORD);
DLLIMPORT BOOL WINAPI CreateDirectoryW(LPCWSTR,SECURITY_ATTRIBUTES*);
DLLIMPORT DWORD WINAPI GetFileAttributesW(LPCWSTR);
DLLIMPORT HANDLE WINAPI CreateFileW(LPCWSTR,DWORD,DWORD,SECURITY_ATTRIBUTES*,DWORD,DWORD,HANDLE);
DLLIMPORT BOOL WINAPI ReadFile(HANDLE,LPVOID,DWORD,DWORD*,LPVOID);
DLLIMPORT BOOL WINAPI WriteFile(HANDLE,LPCVOID,DWORD,DWORD*,LPVOID);
DLLIMPORT BOOL WINAPI FlushFileBuffers(HANDLE);
DLLIMPORT BOOL WINAPI GetFileSizeEx(HANDLE,LARGE_INTEGER*);
DLLIMPORT BOOL WINAPI SetFilePointerEx(HANDLE,LARGE_INTEGER,LARGE_INTEGER*,DWORD);
DLLIMPORT UINT WINAPI GetPrivateProfileIntW(LPCWSTR,LPCWSTR,int,LPCWSTR);
DLLIMPORT BOOL WINAPI MoveFileExW(LPCWSTR,LPCWSTR,DWORD);
DLLIMPORT BOOL WINAPI DeleteFileW(LPCWSTR);
DLLIMPORT HANDLE WINAPI CreateThread(SECURITY_ATTRIBUTES*,SIZE_T,LPTHREAD_START_ROUTINE,LPVOID,DWORD,DWORD*);
DLLIMPORT DWORD WINAPI WaitForSingleObject(HANDLE,DWORD);
DLLIMPORT VOID WINAPI Sleep(DWORD);
DLLIMPORT ULONGLONG WINAPI GetTickCount64();
DLLIMPORT DWORD WINAPI GetCurrentProcessId();
DLLIMPORT BOOL WINAPI ProcessIdToSessionId(DWORD,DWORD*);
DLLIMPORT HANDLE WINAPI CreateToolhelp32Snapshot(DWORD,DWORD);
DLLIMPORT BOOL WINAPI Process32FirstW(HANDLE,PROCESSENTRY32W*);
DLLIMPORT BOOL WINAPI Process32NextW(HANDLE,PROCESSENTRY32W*);
DLLIMPORT HANDLE WINAPI OpenProcess(DWORD,BOOL,DWORD);
DLLIMPORT BOOL WINAPI TerminateProcess(HANDLE,UINT);
DLLIMPORT BOOL WINAPI CreateProcessW(LPCWSTR,LPWSTR,SECURITY_ATTRIBUTES*,SECURITY_ATTRIBUTES*,BOOL,DWORD,LPVOID,LPCWSTR,STARTUPINFOW*,PROCESS_INFORMATION*);
DLLIMPORT BOOL WINAPI GetExitCodeProcess(HANDLE,DWORD*);
DLLIMPORT UINT WINAPI GetWindowsDirectoryW(LPWSTR,UINT);
DLLIMPORT VOID WINAPI GetSystemTimeAsFileTime(FILETIME*);
DLLIMPORT int WINAPI CompareStringOrdinal(LPCWCH,int,LPCWCH,int,BOOL);
DLLIMPORT HMODULE WINAPI GetModuleHandleW(LPCWSTR);
}

// User32
extern "C" {
DLLIMPORT unsigned short WINAPI RegisterClassExW(const WNDCLASSEXW*);
DLLIMPORT HWND WINAPI CreateWindowExW(DWORD,LPCWSTR,LPCWSTR,DWORD,int,int,int,int,HWND,HMENU,HINSTANCE,LPVOID);
DLLIMPORT LRESULT WINAPI DefWindowProcW(HWND,UINT,WPARAM,LPARAM);
DLLIMPORT BOOL WINAPI DestroyWindow(HWND);
DLLIMPORT VOID WINAPI PostQuitMessage(int);
DLLIMPORT BOOL WINAPI GetMessageW(MSG*,HWND,UINT,UINT);
DLLIMPORT BOOL WINAPI TranslateMessage(const MSG*);
DLLIMPORT LRESULT WINAPI DispatchMessageW(const MSG*);
DLLIMPORT BOOL WINAPI PostMessageW(HWND,UINT,WPARAM,LPARAM);
DLLIMPORT BOOL WINAPI RegisterHotKey(HWND,int,UINT,UINT);
DLLIMPORT BOOL WINAPI UnregisterHotKey(HWND,int);
DLLIMPORT UINT_PTR WINAPI SetTimer(HWND,UINT_PTR,UINT,LPVOID);
DLLIMPORT BOOL WINAPI KillTimer(HWND,UINT_PTR);
DLLIMPORT HWINEVENTHOOK WINAPI SetWinEventHook(DWORD,DWORD,HMODULE,WINEVENTPROC,DWORD,DWORD,DWORD);
DLLIMPORT BOOL WINAPI UnhookWinEvent(HWINEVENTHOOK);
DLLIMPORT HWND WINAPI GetAncestor(HWND,UINT);
DLLIMPORT int WINAPI GetClassNameW(HWND,LPWSTR,int);
DLLIMPORT HWND WINAPI FindWindowW(LPCWSTR,LPCWSTR);
DLLIMPORT HWND WINAPI FindWindowExW(HWND,HWND,LPCWSTR,LPCWSTR);
DLLIMPORT LRESULT WINAPI SendMessageTimeoutW(HWND,UINT,WPARAM,LPARAM,UINT,UINT,DWORD_PTR*);
DLLIMPORT BOOL WINAPI GetWindowPlacement(HWND,WINDOWPLACEMENT*);
DLLIMPORT BOOL WINAPI SetWindowPlacement(HWND,const WINDOWPLACEMENT*);
DLLIMPORT BOOL WINAPI GetWindowRect(HWND,RECT*);
DLLIMPORT BOOL WINAPI SetWindowPos(HWND,HWND,int,int,int,int,UINT);
DLLIMPORT BOOL WINAPI ShowWindow(HWND,int);
DLLIMPORT int WINAPI MessageBoxW(HWND,LPCWSTR,LPCWSTR,UINT);
DLLIMPORT BOOL WINAPI SetProcessDpiAwarenessContext(HANDLE);
DLLIMPORT BOOL WINAPI SystemParametersInfoW(UINT,UINT,LPVOID,UINT);
DLLIMPORT UINT WINAPI GetDpiForSystem();
DLLIMPORT HMONITOR WINAPI MonitorFromRect(const RECT*,DWORD);
DLLIMPORT BOOL WINAPI GetMonitorInfoW(HMONITOR,MONITORINFO*);
DLLIMPORT HICON WINAPI LoadIconW(HINSTANCE,LPCWSTR);
DLLIMPORT UINT WINAPI RegisterWindowMessageW(LPCWSTR);
DLLIMPORT HMENU WINAPI CreatePopupMenu();
DLLIMPORT BOOL WINAPI AppendMenuW(HMENU,UINT,UINT_PTR,LPCWSTR);
DLLIMPORT BOOL WINAPI DestroyMenu(HMENU);
DLLIMPORT BOOL WINAPI GetCursorPos(POINT*);
DLLIMPORT BOOL WINAPI SetForegroundWindow(HWND);
DLLIMPORT UINT WINAPI TrackPopupMenuEx(HMENU,UINT,int,int,HWND,LPVOID);
}

// Shell32
extern "C" {
DLLIMPORT HRESULT WINAPI SHGetKnownFolderPath(const GUID*,DWORD,HANDLE,LPWSTR*);
DLLIMPORT BOOL WINAPI Shell_NotifyIconW(DWORD,NOTIFYICONDATAW*);
}

// Ole32 / OleAut32
extern "C" {
DLLIMPORT HRESULT WINAPI CoInitializeEx(LPVOID,DWORD);
DLLIMPORT VOID WINAPI CoUninitialize();
DLLIMPORT HRESULT WINAPI CLSIDFromProgID(LPCWSTR,CLSID*);
DLLIMPORT HRESULT WINAPI CoCreateInstance(const CLSID*,IUnknown*,DWORD,const IID*,void**);
DLLIMPORT VOID WINAPI CoTaskMemFree(LPVOID);
DLLIMPORT BSTR WINAPI SysAllocStringLen(const WCHAR*,UINT);
DLLIMPORT VOID WINAPI SysFreeString(BSTR);
DLLIMPORT UINT WINAPI SysStringLen(BSTR);
DLLIMPORT HRESULT WINAPI VariantClear(VARIANT*);
}
