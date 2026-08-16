#include <windows.h>
#include <shellapi.h>
#include <wincodec.h>
#include <commctrl.h>
#include <objidl.h>
#include "resource.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cwctype>
#include <exception>
#include <iterator>
#include <filesystem>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")

namespace fs = std::filesystem;

namespace {

constexpr wchar_t kWindowClass[] = L"IconMasterLiteWindow";
constexpr wchar_t kAppTitle[] = L"IconMasterLite - 图片 / ICO / EXE 图标转换";
constexpr int kListId = 1001;

HWND g_list = nullptr;
IWICImagingFactory* g_wic = nullptr;

#pragma pack(push, 1)
struct IconDir {
    std::uint16_t reserved;
    std::uint16_t type;
    std::uint16_t count;
};

struct IconDirEntry {
    std::uint8_t width;
    std::uint8_t height;
    std::uint8_t colorCount;
    std::uint8_t reserved;
    std::uint16_t planes;
    std::uint16_t bitCount;
    std::uint32_t bytesInRes;
    std::uint32_t imageOffset;
};

struct GroupIconDir {
    std::uint16_t reserved;
    std::uint16_t type;
    std::uint16_t count;
};

struct GroupIconDirEntry {
    std::uint8_t width;
    std::uint8_t height;
    std::uint8_t colorCount;
    std::uint8_t reserved;
    std::uint16_t planes;
    std::uint16_t bitCount;
    std::uint32_t bytesInRes;
    std::uint16_t id;
};
#pragma pack(pop)

struct ComReleaser {
    template <typename T>
    void operator()(T* p) const noexcept {
        if (p) p->Release();
    }
};

template <typename T>
using ComPtr = std::unique_ptr<T, ComReleaser>;

std::wstring HrText(HRESULT hr) {
    wchar_t* buffer = nullptr;
    const DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS;
    const DWORD len = FormatMessageW(flags, nullptr, static_cast<DWORD>(hr), 0,
                                     reinterpret_cast<LPWSTR>(&buffer), 0, nullptr);
    std::wstring result;
    if (len && buffer) {
        result.assign(buffer, len);
        while (!result.empty() && (result.back() == L'\r' || result.back() == L'\n')) result.pop_back();
        LocalFree(buffer);
    } else {
        std::wostringstream oss;
        oss << L"HRESULT 0x" << std::hex << static_cast<unsigned long>(hr);
        result = oss.str();
    }
    return result;
}

void LogLine(const std::wstring& line) {
    if (!g_list) return;
    SendMessageW(g_list, LB_ADDSTRING, 0, reinterpret_cast<LPARAM>(line.c_str()));
    const LRESULT count = SendMessageW(g_list, LB_GETCOUNT, 0, 0);
    if (count > 0) SendMessageW(g_list, LB_SETTOPINDEX, static_cast<WPARAM>(count - 1), 0);
}

std::wstring Lower(std::wstring s) {
    std::transform(s.begin(), s.end(), s.begin(), [](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });
    return s;
}

bool IsExe(const fs::path& p) {
    return Lower(p.extension().wstring()) == L".exe";
}

bool IsIco(const fs::path& p) {
    return Lower(p.extension().wstring()) == L".ico";
}

fs::path ReplaceExtension(const fs::path& input, const wchar_t* ext) {
    fs::path out = input;
    out.replace_extension(ext);
    return out;
}

bool WriteAllBytes(const fs::path& path, const std::vector<std::uint8_t>& data, std::wstring& err) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) {
        err = L"无法创建输出文件";
        return false;
    }
    out.write(reinterpret_cast<const char*>(data.data()), static_cast<std::streamsize>(data.size()));
    if (!out.good()) {
        err = L"写入输出文件失败";
        return false;
    }
    return true;
}

HRESULT StreamToBytes(IStream* stream, std::vector<std::uint8_t>& bytes) {
    if (!stream) return E_POINTER;
    STATSTG st{};
    HRESULT hr = stream->Stat(&st, STATFLAG_NONAME);
    if (FAILED(hr)) return hr;
    if (st.cbSize.QuadPart > static_cast<ULONGLONG>(SIZE_MAX)) return E_OUTOFMEMORY;
    bytes.resize(static_cast<size_t>(st.cbSize.QuadPart));
    LARGE_INTEGER zero{};
    hr = stream->Seek(zero, STREAM_SEEK_SET, nullptr);
    if (FAILED(hr)) return hr;
    ULONG read = 0;
    if (!bytes.empty()) {
        hr = stream->Read(bytes.data(), static_cast<ULONG>(bytes.size()), &read);
        if (FAILED(hr)) return hr;
        if (read != bytes.size()) return E_FAIL;
    }
    return S_OK;
}

HRESULT EncodePngToMemory(UINT width, UINT height, const std::vector<std::uint8_t>& bgra,
                          std::vector<std::uint8_t>& png) {
    IStream* rawStream = nullptr;
    HRESULT hr = CreateStreamOnHGlobal(nullptr, TRUE, &rawStream);
    if (FAILED(hr)) return hr;
    ComPtr<IStream> stream(rawStream);

    IWICBitmapEncoder* rawEncoder = nullptr;
    hr = g_wic->CreateEncoder(GUID_ContainerFormatPng, nullptr, &rawEncoder);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapEncoder> encoder(rawEncoder);

    hr = encoder->Initialize(stream.get(), WICBitmapEncoderNoCache);
    if (FAILED(hr)) return hr;

    IWICBitmapFrameEncode* rawFrame = nullptr;
    IPropertyBag2* rawBag = nullptr;
    hr = encoder->CreateNewFrame(&rawFrame, &rawBag);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapFrameEncode> frame(rawFrame);
    ComPtr<IPropertyBag2> bag(rawBag);

    hr = frame->Initialize(bag.get());
    if (FAILED(hr)) return hr;
    hr = frame->SetSize(width, height);
    if (FAILED(hr)) return hr;

    WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
    hr = frame->SetPixelFormat(&format);
    if (FAILED(hr)) return hr;

    const UINT stride = width * 4;
    hr = frame->WritePixels(height, stride, static_cast<UINT>(bgra.size()),
                            const_cast<BYTE*>(reinterpret_cast<const BYTE*>(bgra.data())));
    if (FAILED(hr)) return hr;
    hr = frame->Commit();
    if (FAILED(hr)) return hr;
    hr = encoder->Commit();
    if (FAILED(hr)) return hr;

    return StreamToBytes(stream.get(), png);
}

HRESULT DecodeImageToBgra(const fs::path& path, UINT& width, UINT& height, std::vector<std::uint8_t>& bgra) {
    IWICBitmapDecoder* rawDecoder = nullptr;
    HRESULT hr = g_wic->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ,
                                                   WICDecodeMetadataCacheOnLoad, &rawDecoder);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapDecoder> decoder(rawDecoder);

    IWICBitmapFrameDecode* rawFrame = nullptr;
    hr = decoder->GetFrame(0, &rawFrame);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapFrameDecode> frame(rawFrame);

    hr = frame->GetSize(&width, &height);
    if (FAILED(hr) || width == 0 || height == 0) return E_FAIL;

    IWICFormatConverter* rawConverter = nullptr;
    hr = g_wic->CreateFormatConverter(&rawConverter);
    if (FAILED(hr)) return hr;
    ComPtr<IWICFormatConverter> converter(rawConverter);

    // 缩放前使用预乘 Alpha（PBGRA），可显著减少透明边缘缩放时的黑边/彩边。
    hr = converter->Initialize(frame.get(), GUID_WICPixelFormat32bppPBGRA,
                               WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) return hr;

    const UINT stride = width * 4;
    bgra.resize(static_cast<size_t>(stride) * height);
    return converter->CopyPixels(nullptr, stride, static_cast<UINT>(bgra.size()),
                                 reinterpret_cast<BYTE*>(bgra.data()));
}

HRESULT ResizeContainBgra(UINT srcW, UINT srcH, const std::vector<std::uint8_t>& src,
                          UINT dstSize, std::vector<std::uint8_t>& dst) {
    IWICBitmap* rawBitmap = nullptr;
    HRESULT hr = g_wic->CreateBitmapFromMemory(srcW, srcH, GUID_WICPixelFormat32bppPBGRA,
                                                srcW * 4, static_cast<UINT>(src.size()),
                                                const_cast<BYTE*>(reinterpret_cast<const BYTE*>(src.data())),
                                                &rawBitmap);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmap> bitmap(rawBitmap);

    const double scale = std::min(static_cast<double>(dstSize) / srcW,
                                  static_cast<double>(dstSize) / srcH);
    const UINT scaledW = std::max<UINT>(1, static_cast<UINT>(srcW * scale + 0.5));
    const UINT scaledH = std::max<UINT>(1, static_cast<UINT>(srcH * scale + 0.5));

    IWICBitmapScaler* rawScaler = nullptr;
    hr = g_wic->CreateBitmapScaler(&rawScaler);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapScaler> scaler(rawScaler);

    hr = scaler->Initialize(bitmap.get(), scaledW, scaledH, WICBitmapInterpolationModeFant);
    if (FAILED(hr)) return hr;

    IWICFormatConverter* rawConverter = nullptr;
    hr = g_wic->CreateFormatConverter(&rawConverter);
    if (FAILED(hr)) return hr;
    ComPtr<IWICFormatConverter> converter(rawConverter);

    hr = converter->Initialize(scaler.get(), GUID_WICPixelFormat32bppBGRA,
                               WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) return hr;

    std::vector<std::uint8_t> scaled(static_cast<size_t>(scaledW) * scaledH * 4);
    hr = converter->CopyPixels(nullptr, scaledW * 4, static_cast<UINT>(scaled.size()),
                               reinterpret_cast<BYTE*>(scaled.data()));
    if (FAILED(hr)) return hr;

    dst.assign(static_cast<size_t>(dstSize) * dstSize * 4, 0);
    const UINT x0 = (dstSize - scaledW) / 2;
    const UINT y0 = (dstSize - scaledH) / 2;
    for (UINT y = 0; y < scaledH; ++y) {
        auto* out = dst.data() + (static_cast<size_t>(y0 + y) * dstSize + x0) * 4;
        const auto* in = scaled.data() + static_cast<size_t>(y) * scaledW * 4;
        memcpy(out, in, static_cast<size_t>(scaledW) * 4);
    }
    return S_OK;
}

bool ImageToIco(const fs::path& input, const fs::path& output, std::wstring& err) {
    UINT srcW = 0, srcH = 0;
    std::vector<std::uint8_t> src;
    HRESULT hr = DecodeImageToBgra(input, srcW, srcH, src);
    if (FAILED(hr)) {
        err = L"图片解码失败：" + HrText(hr);
        return false;
    }

    // 常用 Windows 图标尺寸。256x256 帧以 PNG 形式写入 ICO，可保留完整透明度并控制体积。
    const UINT sizes[] = {16, 20, 24, 32, 40, 48, 64, 96, 128, 256};
    std::vector<std::vector<std::uint8_t>> frames;
    frames.reserve(std::size(sizes));

    for (UINT size : sizes) {
        std::vector<std::uint8_t> square;
        hr = ResizeContainBgra(srcW, srcH, src, size, square);
        if (FAILED(hr)) {
            err = L"缩放图片失败：" + HrText(hr);
            return false;
        }
        std::vector<std::uint8_t> png;
        hr = EncodePngToMemory(size, size, square, png);
        if (FAILED(hr)) {
            err = L"生成 ICO 内部 PNG 帧失败：" + HrText(hr);
            return false;
        }
        frames.emplace_back(std::move(png));
    }

    IconDir dir{0, 1, static_cast<std::uint16_t>(frames.size())};
    std::vector<IconDirEntry> entries(frames.size());
    std::uint32_t offset = static_cast<std::uint32_t>(sizeof(IconDir) + entries.size() * sizeof(IconDirEntry));
    for (size_t i = 0; i < frames.size(); ++i) {
        const UINT size = sizes[i];
        auto& e = entries[i];
        e.width = size == 256 ? 0 : static_cast<std::uint8_t>(size);
        e.height = size == 256 ? 0 : static_cast<std::uint8_t>(size);
        e.colorCount = 0;
        e.reserved = 0;
        e.planes = 1;
        e.bitCount = 32;
        e.bytesInRes = static_cast<std::uint32_t>(frames[i].size());
        e.imageOffset = offset;
        offset += e.bytesInRes;
    }

    std::vector<std::uint8_t> ico;
    ico.reserve(offset);
    const auto append = [&ico](const void* p, size_t n) {
        const auto* b = static_cast<const std::uint8_t*>(p);
        ico.insert(ico.end(), b, b + n);
    };
    append(&dir, sizeof(dir));
    append(entries.data(), entries.size() * sizeof(IconDirEntry));
    for (const auto& frame : frames) append(frame.data(), frame.size());
    return WriteAllBytes(output, ico, err);
}

HRESULT SaveWicSourceAsPng(IWICBitmapSource* source, const fs::path& output) {
    IWICStream* rawStream = nullptr;
    HRESULT hr = g_wic->CreateStream(&rawStream);
    if (FAILED(hr)) return hr;
    ComPtr<IWICStream> stream(rawStream);

    hr = stream->InitializeFromFilename(output.c_str(), GENERIC_WRITE);
    if (FAILED(hr)) return hr;

    IWICBitmapEncoder* rawEncoder = nullptr;
    hr = g_wic->CreateEncoder(GUID_ContainerFormatPng, nullptr, &rawEncoder);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapEncoder> encoder(rawEncoder);

    hr = encoder->Initialize(stream.get(), WICBitmapEncoderNoCache);
    if (FAILED(hr)) return hr;

    IWICBitmapFrameEncode* rawFrame = nullptr;
    IPropertyBag2* rawBag = nullptr;
    hr = encoder->CreateNewFrame(&rawFrame, &rawBag);
    if (FAILED(hr)) return hr;
    ComPtr<IWICBitmapFrameEncode> frame(rawFrame);
    ComPtr<IPropertyBag2> bag(rawBag);

    hr = frame->Initialize(bag.get());
    if (FAILED(hr)) return hr;

    UINT w = 0, h = 0;
    source->GetSize(&w, &h);
    hr = frame->SetSize(w, h);
    if (FAILED(hr)) return hr;

    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
    hr = frame->SetPixelFormat(&fmt);
    if (FAILED(hr)) return hr;

    IWICFormatConverter* rawConverter = nullptr;
    hr = g_wic->CreateFormatConverter(&rawConverter);
    if (FAILED(hr)) return hr;
    ComPtr<IWICFormatConverter> converter(rawConverter);

    hr = converter->Initialize(source, fmt, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) return hr;

    hr = frame->WriteSource(converter.get(), nullptr);
    if (FAILED(hr)) return hr;
    hr = frame->Commit();
    if (FAILED(hr)) return hr;
    return encoder->Commit();
}

bool IcoToPng(const fs::path& input, const fs::path& output, std::wstring& err) {
    IWICBitmapDecoder* rawDecoder = nullptr;
    HRESULT hr = g_wic->CreateDecoderFromFilename(input.c_str(), nullptr, GENERIC_READ,
                                                   WICDecodeMetadataCacheOnLoad, &rawDecoder);
    if (FAILED(hr)) {
        err = L"ICO 解码失败：" + HrText(hr);
        return false;
    }
    ComPtr<IWICBitmapDecoder> decoder(rawDecoder);

    UINT frameCount = 0;
    hr = decoder->GetFrameCount(&frameCount);
    if (FAILED(hr) || frameCount == 0) {
        err = L"ICO 中没有可用图像帧";
        return false;
    }

    UINT bestIndex = 0;
    std::uint64_t bestArea = 0;
    for (UINT i = 0; i < frameCount; ++i) {
        IWICBitmapFrameDecode* rawFrame = nullptr;
        if (SUCCEEDED(decoder->GetFrame(i, &rawFrame)) && rawFrame) {
            ComPtr<IWICBitmapFrameDecode> frame(rawFrame);
            UINT w = 0, h = 0;
            if (SUCCEEDED(frame->GetSize(&w, &h))) {
                const std::uint64_t area = static_cast<std::uint64_t>(w) * h;
                if (area > bestArea) {
                    bestArea = area;
                    bestIndex = i;
                }
            }
        }
    }

    IWICBitmapFrameDecode* rawBest = nullptr;
    hr = decoder->GetFrame(bestIndex, &rawBest);
    if (FAILED(hr)) {
        err = L"读取 ICO 最佳帧失败：" + HrText(hr);
        return false;
    }
    ComPtr<IWICBitmapFrameDecode> best(rawBest);

    // WIC 文件流在覆盖已存在 PNG 时可能返回访问错误，因此先删除旧目标。
    DeleteFileW(output.c_str());
    hr = SaveWicSourceAsPng(best.get(), output);
    if (FAILED(hr)) {
        err = L"保存 PNG 失败：" + HrText(hr);
        return false;
    }
    return true;
}

struct ResourceName {
    bool integer = true;
    WORD id = 0;
    std::wstring text;
};

BOOL CALLBACK FirstGroupIconName(HMODULE, LPCWSTR, LPWSTR name, LONG_PTR param) {
    auto* out = reinterpret_cast<ResourceName*>(param);
    if (IS_INTRESOURCE(name)) {
        out->integer = true;
        out->id = static_cast<WORD>(reinterpret_cast<ULONG_PTR>(name));
    } else {
        out->integer = false;
        out->text = name;
    }
    return FALSE; // 只需要第一个图标组；一个图标组通常已经包含全部分辨率。
}

bool ExeToIco(const fs::path& input, const fs::path& output, std::wstring& err) {
    HMODULE module = LoadLibraryExW(input.c_str(), nullptr, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE);
    if (!module) {
        err = L"无法以资源方式打开 EXE，Win32 错误：" + std::to_wstring(GetLastError());
        return false;
    }

    ResourceName groupName;
    SetLastError(ERROR_SUCCESS);
    EnumResourceNamesW(module, RT_GROUP_ICON, FirstGroupIconName, reinterpret_cast<LONG_PTR>(&groupName));
    if (GetLastError() != ERROR_SUCCESS && GetLastError() != ERROR_RESOURCE_TYPE_NOT_FOUND) {
        // Enum 回调返回 FALSE 会使函数返回 FALSE，但这不是错误；只在未填充名称时处理。
    }

    LPCWSTR groupPtr = nullptr;
    if (groupName.integer && groupName.id != 0) {
        groupPtr = MAKEINTRESOURCEW(groupName.id);
    } else if (!groupName.integer && !groupName.text.empty()) {
        groupPtr = groupName.text.c_str();
    } else {
        FreeLibrary(module);
        err = L"EXE 中没有找到 RT_GROUP_ICON 图标资源";
        return false;
    }

    HRSRC groupRes = FindResourceW(module, groupPtr, RT_GROUP_ICON);
    if (!groupRes) {
        FreeLibrary(module);
        err = L"读取 EXE 图标组资源失败";
        return false;
    }
    HGLOBAL groupDataHandle = LoadResource(module, groupRes);
    const DWORD groupSize = SizeofResource(module, groupRes);
    const auto* groupBytes = static_cast<const std::uint8_t*>(LockResource(groupDataHandle));
    if (!groupBytes || groupSize < sizeof(GroupIconDir)) {
        FreeLibrary(module);
        err = L"EXE 图标组资源损坏";
        return false;
    }

    const auto* group = reinterpret_cast<const GroupIconDir*>(groupBytes);
    if (group->type != 1 || group->count == 0) {
        FreeLibrary(module);
        err = L"EXE 图标组格式无效";
        return false;
    }
    const size_t needed = sizeof(GroupIconDir) + static_cast<size_t>(group->count) * sizeof(GroupIconDirEntry);
    if (needed > groupSize) {
        FreeLibrary(module);
        err = L"EXE 图标组目录长度异常";
        return false;
    }

    const auto* groupEntries = reinterpret_cast<const GroupIconDirEntry*>(groupBytes + sizeof(GroupIconDir));
    const WORD iconCount = group->count;
    std::vector<IconDirEntry> entries(iconCount);
    std::vector<std::vector<std::uint8_t>> images(iconCount);

    std::uint32_t offset = static_cast<std::uint32_t>(sizeof(IconDir) + entries.size() * sizeof(IconDirEntry));
    for (WORD i = 0; i < iconCount; ++i) {
        HRSRC iconRes = FindResourceW(module, MAKEINTRESOURCEW(groupEntries[i].id), RT_ICON);
        if (!iconRes) {
            FreeLibrary(module);
            err = L"EXE 图标组引用了缺失的 RT_ICON 资源";
            return false;
        }
        HGLOBAL iconHandle = LoadResource(module, iconRes);
        const DWORD iconSize = SizeofResource(module, iconRes);
        const auto* iconBytes = static_cast<const std::uint8_t*>(LockResource(iconHandle));
        if (!iconBytes || iconSize == 0) {
            FreeLibrary(module);
            err = L"读取 EXE 图标图像资源失败";
            return false;
        }
        images[i].assign(iconBytes, iconBytes + iconSize);

        entries[i].width = groupEntries[i].width;
        entries[i].height = groupEntries[i].height;
        entries[i].colorCount = groupEntries[i].colorCount;
        entries[i].reserved = 0;
        entries[i].planes = groupEntries[i].planes;
        entries[i].bitCount = groupEntries[i].bitCount;
        entries[i].bytesInRes = iconSize;
        entries[i].imageOffset = offset;
        offset += iconSize;
    }

    FreeLibrary(module);

    IconDir dir{0, 1, iconCount};
    std::vector<std::uint8_t> ico;
    ico.reserve(offset);
    const auto append = [&ico](const void* p, size_t n) {
        const auto* b = static_cast<const std::uint8_t*>(p);
        ico.insert(ico.end(), b, b + n);
    };
    append(&dir, sizeof(dir));
    append(entries.data(), entries.size() * sizeof(IconDirEntry));
    for (const auto& image : images) append(image.data(), image.size());

    return WriteAllBytes(output, ico, err);
}

void ProcessPath(const fs::path& path) {
    if (!fs::exists(path) || !fs::is_regular_file(path)) {
        LogLine(L"✗ 不是有效文件：" + path.wstring());
        return;
    }

    fs::path output;
    std::wstring err;
    bool ok = false;

    if (IsExe(path)) {
        output = ReplaceExtension(path, L".ico");
        ok = ExeToIco(path, output, err);
    } else if (IsIco(path)) {
        output = ReplaceExtension(path, L".png");
        ok = IcoToPng(path, output, err);
    } else {
        output = ReplaceExtension(path, L".ico");
        ok = ImageToIco(path, output, err);
    }

    if (ok) {
        LogLine(L"✓ " + path.filename().wstring() + L"  →  " + output.filename().wstring());
    } else {
        LogLine(L"✗ " + path.filename().wstring() + L"：" + err);
    }
}


void ProcessCommandLineInputs() {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return;

    for (int i = 1; i < argc; ++i) {
        const std::wstring path = argv[i] ? argv[i] : L"";
        if (path.empty()) continue;
        try {
            ProcessPath(fs::path(path));
        } catch (const std::exception&) {
            LogLine(L"✗ 处理启动参数文件时发生未预期异常：" + path);
        }
    }

    LocalFree(argv);
}

UINT GetWindowDpiCompat(HWND hwnd) {
    using GetDpiForWindowFn = UINT(WINAPI*)(HWND);
    static auto fn = reinterpret_cast<GetDpiForWindowFn>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForWindow"));
    return fn ? fn(hwnd) : 96;
}

int ScalePx(int value, UINT dpi) {
    return MulDiv(value, static_cast<int>(dpi), 96);
}

HMONITOR GetPreferredStartupMonitor() {
    POINT cursor{};
    if (GetCursorPos(&cursor)) {
        return MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST);
    }
    return MonitorFromPoint(POINT{0, 0}, MONITOR_DEFAULTTOPRIMARY);
}

void CenterWindowOnMonitor(HWND hwnd, HMONITOR monitor = nullptr) {
    if (!hwnd) return;
    if (!monitor) {
        monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    }

    MONITORINFO mi{sizeof(mi)};
    if (!monitor || !GetMonitorInfoW(monitor, &mi)) return;

    RECT wr{};
    if (!GetWindowRect(hwnd, &wr)) return;

    const int windowWidth = static_cast<int>(wr.right - wr.left);
    const int windowHeight = static_cast<int>(wr.bottom - wr.top);
    const int workWidth = static_cast<int>(mi.rcWork.right - mi.rcWork.left);
    const int workHeight = static_cast<int>(mi.rcWork.bottom - mi.rcWork.top);

    const int x = static_cast<int>(mi.rcWork.left) + std::max(0, (workWidth - windowWidth) / 2);
    const int y = static_cast<int>(mi.rcWork.top) + std::max(0, (workHeight - windowHeight) / 2);

    SetWindowPos(hwnd, nullptr, x, y, 0, 0,
                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
}

void Layout(HWND hwnd) {
    RECT rc{};
    GetClientRect(hwnd, &rc);
    const UINT dpi = GetWindowDpiCompat(hwnd);
    const int clientWidth = static_cast<int>(rc.right - rc.left);
    const int clientHeight = static_cast<int>(rc.bottom - rc.top);
    const int pad = ScalePx(18, dpi);
    const int top = ScalePx(112, dpi);
    const int listWidth = std::max(1, clientWidth - pad * 2);
    const int listHeight = std::max(1, clientHeight - top - pad);
    if (g_list) {
        MoveWindow(g_list, pad, top, listWidth, listHeight, TRUE);
    }
}

void PaintHeader(HWND hwnd) {
    PAINTSTRUCT ps{};
    HDC hdc = BeginPaint(hwnd, &ps);
    RECT rc{};
    GetClientRect(hwnd, &rc);
    FillRect(hdc, &rc, reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1));

    const UINT dpi = GetWindowDpiCompat(hwnd);
    const int pad = ScalePx(18, dpi);
    RECT titleRc{pad, ScalePx(18, dpi), rc.right - pad, ScalePx(52, dpi)};
    RECT subRc{pad, ScalePx(55, dpi), rc.right - pad, ScalePx(104, dpi)};

    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, GetSysColor(COLOR_WINDOWTEXT));

    NONCLIENTMETRICSW ncm{sizeof(ncm)};
    SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, sizeof(ncm), &ncm, 0);
    LOGFONTW titleLf = ncm.lfMessageFont;
    titleLf.lfHeight = -ScalePx(22, dpi);
    titleLf.lfWeight = FW_SEMIBOLD;
    HFONT titleFont = CreateFontIndirectW(&titleLf);
    HFONT old = static_cast<HFONT>(SelectObject(hdc, titleFont));
    DrawTextW(hdc, L"把文件拖到这个窗口", -1, &titleRc, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
    SelectObject(hdc, old);
    DeleteObject(titleFont);

    LOGFONTW bodyLf = ncm.lfMessageFont;
    bodyLf.lfHeight = -ScalePx(13, dpi);
    HFONT bodyFont = CreateFontIndirectW(&bodyLf);
    old = static_cast<HFONT>(SelectObject(hdc, bodyFont));
    DrawTextW(hdc,
              L"图片 → 同目录同名 .ico（多尺寸）    |    .ico → 同目录同名 .png（最大帧）    |    .exe → 提取原始 .ico",
              -1, &subRc, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX);
    SelectObject(hdc, old);
    DeleteObject(bodyFont);

    EndPaint(hwnd, &ps);
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    switch (msg) {
    case WM_CREATE: {
        g_list = CreateWindowExW(WS_EX_CLIENTEDGE, L"LISTBOX", nullptr,
                                 WS_CHILD | WS_VISIBLE | WS_VSCROLL | LBS_NOINTEGRALHEIGHT | LBS_USETABSTOPS,
                                 0, 0, 100, 100, hwnd, reinterpret_cast<HMENU>(kListId),
                                 reinterpret_cast<LPCREATESTRUCTW>(lParam)->hInstance, nullptr);
        SendMessageW(g_list, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
        DragAcceptFiles(hwnd, TRUE);
        LogLine(L"就绪。可一次拖入多个文件。目标文件若已存在会直接覆盖。 ");
        Layout(hwnd);
        return 0;
    }
    case WM_DROPFILES: {
        HDROP drop = reinterpret_cast<HDROP>(wParam);
        const UINT count = DragQueryFileW(drop, 0xFFFFFFFF, nullptr, 0);
        for (UINT i = 0; i < count; ++i) {
            const UINT len = DragQueryFileW(drop, i, nullptr, 0);
            std::wstring path(len + 1, L'\0');
            DragQueryFileW(drop, i, path.data(), len + 1);
            path.resize(len);
            try {
                ProcessPath(fs::path(path));
            } catch (const std::exception&) {
                LogLine(L"✗ 处理文件时发生未预期异常：" + path);
            }
        }
        DragFinish(drop);
        return 0;
    }
    case WM_SIZE:
        Layout(hwnd);
        return 0;
    case WM_DPICHANGED: {
        const RECT* suggested = reinterpret_cast<const RECT*>(lParam);
        const HMONITOR monitor = MonitorFromRect(suggested, MONITOR_DEFAULTTONEAREST);
        SetWindowPos(hwnd, nullptr, suggested->left, suggested->top,
                     suggested->right - suggested->left, suggested->bottom - suggested->top,
                     SWP_NOZORDER | SWP_NOACTIVATE);
        CenterWindowOnMonitor(hwnd, monitor);
        Layout(hwnd);
        InvalidateRect(hwnd, nullptr, TRUE);
        return 0;
    }
    case WM_PAINT:
        PaintHeader(hwnd);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(hwnd, msg, wParam, lParam);
    }
}

void EnableBestDpiAwareness() {
    using SetProcessDpiAwarenessContextFn = BOOL(WINAPI*)(HANDLE);
    if (auto fn = reinterpret_cast<SetProcessDpiAwarenessContextFn>(
            GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetProcessDpiAwarenessContext"))) {
        // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4，动态调用避免旧版 Windows 缺少导出。
        if (fn(reinterpret_cast<HANDLE>(static_cast<INT_PTR>(-4)))) return;
    }
    SetProcessDPIAware();
}

} // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    EnableBestDpiAwareness();

    const HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(co)) {
        MessageBoxW(nullptr, L"COM 初始化失败，程序无法启动。", kAppTitle, MB_ICONERROR);
        return 1;
    }

    HRESULT hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                  IID_PPV_ARGS(&g_wic));
    if (FAILED(hr)) {
        MessageBoxW(nullptr, (L"Windows Imaging Component 初始化失败：\n" + HrText(hr)).c_str(),
                    kAppTitle, MB_ICONERROR);
        CoUninitialize();
        return 1;
    }

    INITCOMMONCONTROLSEX icc{sizeof(icc), ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);

    WNDCLASSEXW wc{sizeof(wc)};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hIcon = static_cast<HICON>(LoadImageW(
        instance, MAKEINTRESOURCEW(IDI_APP_ICON), IMAGE_ICON,
        GetSystemMetrics(SM_CXICON), GetSystemMetrics(SM_CYICON),
        LR_DEFAULTCOLOR | LR_SHARED));
    wc.hIconSm = static_cast<HICON>(LoadImageW(
        instance, MAKEINTRESOURCEW(IDI_APP_ICON), IMAGE_ICON,
        GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON),
        LR_DEFAULTCOLOR | LR_SHARED));
    if (!wc.hIcon) wc.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    if (!wc.hIconSm) wc.hIconSm = wc.hIcon;
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    wc.lpszClassName = kWindowClass;
    if (!RegisterClassExW(&wc)) {
        g_wic->Release();
        g_wic = nullptr;
        CoUninitialize();
        return 1;
    }

    const HMONITOR startupMonitor = GetPreferredStartupMonitor();

    HWND hwnd = CreateWindowExW(0, kWindowClass, kAppTitle,
                                WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                                CW_USEDEFAULT, CW_USEDEFAULT, 760, 460,
                                nullptr, nullptr, instance, nullptr);
    if (!hwnd) {
        g_wic->Release();
        g_wic = nullptr;
        CoUninitialize();
        return 1;
    }

    // 窗口在显示前先移动到目标显示器工作区中央，避免启动时先闪到默认位置。
    // 文件直接拖到 EXE 启动时同样经过这里，因此也始终居中打开。
    CenterWindowOnMonitor(hwnd, startupMonitor);
    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    // Windows Explorer 将文件拖到 IconMasterLite.exe 上时，会把文件路径作为启动参数传入。
    // 与窗口内 WM_DROPFILES 共用 ProcessPath()，保证转换规则完全一致。
    ProcessCommandLineInputs();

    MSG msg{};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    if (g_wic) {
        g_wic->Release();
        g_wic = nullptr;
    }
    CoUninitialize();
    return static_cast<int>(msg.wParam);
}
