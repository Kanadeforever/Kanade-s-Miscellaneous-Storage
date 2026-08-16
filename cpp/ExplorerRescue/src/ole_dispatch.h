#pragma once
#include "core.h"

// 轻量 IDispatch 自动化封装。只覆盖 Shell.Application / ShellWindows 所需能力。

extern const IID IID_NULL_VALUE;
extern const IID IID_IDISPATCH_VALUE;
extern const IID IID_IUNKNOWN_VALUE;

void VariantInitLocal(VARIANT* v);
VARIANT VariantI4(LONG v);
VARIANT VariantBstr(const WCHAR* s);
VARIANT VariantDispatch(IDispatch* d);
void VariantArgFree(VARIANT* v);

HRESULT OleInvoke(IDispatch* obj,const WCHAR* name,WORD flags,VARIANT* args,UINT argCount,VARIANT* result);
IDispatch* OleCreateProgID(const WCHAR* progId);
IDispatch* OleGetDispatch(IDispatch* obj,const WCHAR* name);
IDispatch* OleCallDispatch0(IDispatch* obj,const WCHAR* name);
IDispatch* OleCallDispatchI4(IDispatch* obj,const WCHAR* name,LONG value);
BOOL OleGetLong64(IDispatch* obj,const WCHAR* name,LONGLONG* out);
WCHAR* OleGetStringAlloc(IDispatch* obj,const WCHAR* name);
BOOL OleCall0(IDispatch* obj,const WCHAR* name);
BOOL OleCall1(IDispatch* obj,const WCHAR* name,VARIANT* arg);
ULONG_PTR OleIdentity(IDispatch* obj);
void OleRelease(IDispatch* obj);
