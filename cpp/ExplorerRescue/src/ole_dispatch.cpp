#include "ole_dispatch.h"

/*
模块职责：不依赖 ATL/COM 智能指针的最小 IDispatch 自动化层。

Explorer 的公开 Shell 自动化接口大量以 IDispatch 暴露。为了保持无 CRT、低依赖，本文件
只实现项目实际使用到的 GetIDsOfNames/Invoke、VARIANT 生命周期、BSTR、Dispatch 属性/方法
调用和 canonical IUnknown 身份取得。所有调用方必须显式 Release，避免后台长期运行产生 COM
对象泄漏。
*/


const IID IID_NULL_VALUE      = {0,0,0,{0,0,0,0,0,0,0,0}};
const IID IID_IDISPATCH_VALUE = {0x00020400,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
const IID IID_IUNKNOWN_VALUE  = {0x00000000,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};

void VariantInitLocal(VARIANT* v){ if(v) memset(v,0,sizeof(VARIANT)); }
VARIANT VariantI4(LONG x){ VARIANT v; VariantInitLocal(&v); v.vt=VT_I4; v.data.lVal=x; return v; }
VARIANT VariantBstr(const WCHAR* s){ VARIANT v; VariantInitLocal(&v); v.vt=VT_BSTR; UINT n=WLen(s); v.data.bstrVal=SysAllocStringLen(s,n); if(!v.data.bstrVal) v.vt=VT_EMPTY; return v; }
VARIANT VariantDispatch(IDispatch* d){ VARIANT v; VariantInitLocal(&v); v.vt=VT_DISPATCH; v.data.pdispVal=d; if(d) d->lpVtbl->AddRef(d); return v; }
void VariantArgFree(VARIANT* v){ if(!v) return; VariantClear(v); VariantInitLocal(v); }

static void FreeExcep(EXCEPINFO* ex){
    if(!ex) return;
    if(ex->bstrSource) SysFreeString(ex->bstrSource);
    if(ex->bstrDescription) SysFreeString(ex->bstrDescription);
    if(ex->bstrHelpFile) SysFreeString(ex->bstrHelpFile);
    memset(ex,0,sizeof(*ex));
}

HRESULT OleInvoke(IDispatch* obj,const WCHAR* name,WORD flags,VARIANT* args,UINT argCount,VARIANT* result){
    if(!obj||!name) return (HRESULT)0x80004003L; // E_POINTER
    LPOLESTR n=(LPOLESTR)name; DISPID id=0;
    HRESULT hr=obj->lpVtbl->GetIDsOfNames(obj,&IID_NULL_VALUE,&n,1,0x0409,&id);
    if(FAILED(hr)) hr=obj->lpVtbl->GetIDsOfNames(obj,&IID_NULL_VALUE,&n,1,0,&id);
    if(FAILED(hr)) return hr;

    // IDispatch 要求参数逆序。当前程序最多使用 1 个参数；仍对少量参数做通用逆序。
    VARIANT stackArgs[8]; VARIANT* invokeArgs=0;
    if(argCount){
        if(argCount<=8) invokeArgs=stackArgs;
        else invokeArgs=(VARIANT*)HAlloc(sizeof(VARIANT)*argCount,FALSE);
        if(!invokeArgs) return (HRESULT)0x8007000EL;
        for(UINT i=0;i<argCount;++i) invokeArgs[i]=args[argCount-1-i];
    }
    DISPPARAMS dp; memset(&dp,0,sizeof(dp)); dp.rgvarg=invokeArgs; dp.cArgs=argCount;
    VARIANT local; if(!result){VariantInitLocal(&local);result=&local;} else VariantInitLocal(result);
    EXCEPINFO ex; memset(&ex,0,sizeof(ex)); UINT argErr=0;
    hr=obj->lpVtbl->Invoke(obj,id,&IID_NULL_VALUE,0,flags,&dp,result,&ex,&argErr);
    FreeExcep(&ex);
    if(invokeArgs && invokeArgs!=stackArgs) HFree(invokeArgs);
    if(result==&local) VariantClear(&local);
    return hr;
}

IDispatch* OleCreateProgID(const WCHAR* progId){
    CLSID clsid; memset(&clsid,0,sizeof(clsid)); if(FAILED(CLSIDFromProgID(progId,&clsid))) return 0;
    IDispatch* d=0; HRESULT hr=CoCreateInstance(&clsid,0,CLSCTX_INPROC_SERVER|CLSCTX_LOCAL_SERVER,&IID_IDISPATCH_VALUE,(void**)&d);
    return SUCCEEDED(hr)?d:0;
}

static IDispatch* DispatchFromVariantDetach(VARIANT* v){
    if(!v) return 0;
    if(v->vt==VT_DISPATCH && v->data.pdispVal){ IDispatch* d=v->data.pdispVal; v->vt=VT_EMPTY; v->data.pdispVal=0; return d; }
    if(v->vt==VT_UNKNOWN && v->data.punkVal){
        IDispatch* d=0; if(SUCCEEDED(v->data.punkVal->lpVtbl->QueryInterface(v->data.punkVal,&IID_IDISPATCH_VALUE,(void**)&d))) { VariantClear(v); VariantInitLocal(v); return d; }
    }
    return 0;
}
IDispatch* OleGetDispatch(IDispatch* obj,const WCHAR* name){ VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_PROPERTYGET,0,0,&r); if(FAILED(hr)) return 0; IDispatch* d=DispatchFromVariantDetach(&r); VariantClear(&r); return d; }
IDispatch* OleCallDispatch0(IDispatch* obj,const WCHAR* name){ VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_METHOD|DISPATCH_PROPERTYGET,0,0,&r); if(FAILED(hr)) return 0; IDispatch* d=DispatchFromVariantDetach(&r); VariantClear(&r); return d; }
IDispatch* OleCallDispatchI4(IDispatch* obj,const WCHAR* name,LONG value){ VARIANT a=VariantI4(value),r; HRESULT hr=OleInvoke(obj,name,DISPATCH_METHOD|DISPATCH_PROPERTYGET,&a,1,&r); if(FAILED(hr)) return 0; IDispatch* d=DispatchFromVariantDetach(&r); VariantClear(&r); return d; }

BOOL OleGetLong64(IDispatch* obj,const WCHAR* name,LONGLONG* out){
    if(out)*out=0; VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_PROPERTYGET,0,0,&r); if(FAILED(hr)) return FALSE; BOOL ok=TRUE; LONGLONG v=0;
    switch(r.vt){
        case VT_I2: v=r.data.iVal; break;
        case VT_I4: case VT_INT: v=r.data.lVal; break;
        case VT_UI4: case VT_UINT: v=(DWORD)r.data.ullVal; break;
        case VT_I8: v=r.data.llVal; break;
        case VT_UI8: v=(LONGLONG)r.data.ullVal; break;
        default: ok=FALSE; break;
    }
    VariantClear(&r); if(ok&&out)*out=v; return ok;
}

WCHAR* OleGetStringAlloc(IDispatch* obj,const WCHAR* name){
    VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_PROPERTYGET,0,0,&r); if(FAILED(hr)) return 0;
    WCHAR* out=0; if(r.vt==VT_BSTR && r.data.bstrVal){ UINT n=SysStringLen(r.data.bstrVal); out=(WCHAR*)HAlloc((n+1)*sizeof(WCHAR),TRUE); if(out){memcpy(out,r.data.bstrVal,n*sizeof(WCHAR));out[n]=0;} }
    VariantClear(&r); return out;
}

BOOL OleCall0(IDispatch* obj,const WCHAR* name){ VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_METHOD,0,0,&r); VariantClear(&r); return SUCCEEDED(hr); }
BOOL OleCall1(IDispatch* obj,const WCHAR* name,VARIANT* arg){ VARIANT r; HRESULT hr=OleInvoke(obj,name,DISPATCH_METHOD,arg,1,&r); VariantClear(&r); return SUCCEEDED(hr); }

ULONG_PTR OleIdentity(IDispatch* obj){ if(!obj)return 0; IUnknown* u=0; HRESULT hr=obj->lpVtbl->QueryInterface(obj,&IID_IUNKNOWN_VALUE,(void**)&u); if(FAILED(hr)||!u)return 0; ULONG_PTR p=(ULONG_PTR)u; u->lpVtbl->Release(u); return p; }
void OleRelease(IDispatch* obj){ if(obj)obj->lpVtbl->Release(obj); }
