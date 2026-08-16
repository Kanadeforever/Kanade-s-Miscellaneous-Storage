# IconMasterLite

一个类似软媒 PCMaster / IconMaster 使用逻辑的轻量 Windows 图标转换工具。

## 1.2.1

本版在 1.2.0 基础上加入统一窗口定位规则：程序窗口始终在目标显示器工作区中央打开。

- 启动时根据鼠标所在显示器选择目标显示器，并在窗口显示前完成居中定位，避免先闪到系统默认位置。
- 多显示器环境下不会强制固定到主屏；从哪个显示器启动/拖放到 EXE，优先在该显示器中央打开。
- DPI 跨屏变化时，应用系统建议的新尺寸后会在新显示器工作区重新居中。
- 用户仍可在启动后自由拖动窗口；“始终居中”指每次打开时默认居中，而不是把窗口锁死在中央。

### 1.2.0 基线

1.2.0 把程序图标正式纳入工程资源，并统一 Windows 构建源码的换行格式。

- 新增 `res/IconMasterLite.ico`：用于 EXE、窗口标题栏和任务栏的正式程序图标。
- 保留 `res/IconMasterLite.png` 1024×1024 PNG 母版，方便以后重新生成 ICO。
- 新增 `res/app.rc` / `res/resource.h`，CMake 构建时自动调用 Windows Resource Compiler，把图标嵌入 `IconMasterLite.exe`。
- RC 同时写入 1.2.0 版本资源。
- `CMakeLists.txt` 已启用 `RC` 语言并把资源文件加入目标。
- 源码、头文件、RC、BAT、Markdown、CMakeLists 等文本文件统一为 UTF-8 + CRLF。
- 新增 `.gitattributes`，避免日后 Git 检出时又把 Windows 工程文件改回 LF。
- 修复 `Layout()` 中 `LONG` / `int` 混用造成的 MSVC 19.51 `std::max` 编译错误。
- 真正加入“把文件直接拖到 IconMasterLite.exe 上启动”的处理逻辑；它与窗口内拖放共用 `ProcessPath()`。

## 功能

- 普通图片 → 在图片同目录生成同名 `.ico`
- `.ico` → 在同目录生成同名 `.png`
- `.exe` → 从 `RT_GROUP_ICON / RT_ICON` 资源提取原始图标组并生成同名 `.ico`
- 支持一次拖入多个文件
- 支持打开程序后拖到窗口
- 支持直接把文件拖到 `IconMasterLite.exe` 本体上
- 输出已存在时直接覆盖
- 原生 Win32 GUI，高 DPI 感知
- 窗口每次启动时默认在目标显示器工作区中央打开
- 不写注册表，不创建配置目录

## 图片转 ICO

生成尺寸：

`16, 20, 24, 32, 40, 48, 64, 96, 128, 256`

每一帧采用 32 位透明 PNG 数据封装进 ICO。非正方形图片会等比缩放并居中，空白区域保持透明，不会强制拉伸变形。

输入图片使用 Windows Imaging Component（WIC）解码，因此常见 PNG/JPEG/BMP/GIF/TIFF 可直接使用；其他格式取决于系统是否安装对应 WIC 编解码器。

## ICO 转 PNG

自动选择 ICO 中像素面积最大的帧并输出为 PNG。

## EXE 提取 ICO

直接解析：

- `RT_GROUP_ICON`
- `RT_ICON`

然后重新组成标准 ICO 文件，因此不是 Shell 截取单个 HICON，可保留第一个图标组中的多个尺寸与位深。

## 程序图标资源

工程内包含：

```text
res/
  IconMasterLite.png   1024×1024 母版
  IconMasterLite.ico   多尺寸 Windows ICO
  resource.h           资源 ID
  app.rc               ICON + VERSIONINFO
```

编译后图标已写入 `IconMasterLite.exe`，运行时窗口标题栏/任务栏也使用同一资源，不需要把 `.ico` 放在 EXE 旁边。

## 构建

推荐 Visual Studio + CMake：

1. 双击 `构建.bat`
2. 或在 Developer Command Prompt 中执行：

```bat
cmake -S . -B build -A Win32
cmake --build build --config Release
```

输出：

`build\Release\IconMasterLite.exe`

项目仅依赖 Windows 自带的 Win32 / WIC / COM API，无第三方运行库。MSVC Release 默认使用 `/MT` 静态 CRT。

## 换行与编码

Windows 构建文本统一使用：

- 编码：UTF-8
- 换行：CRLF

包内 `.gitattributes` 也固定 `.cpp/.h/.rc/.bat/.md/.txt/CMakeLists.txt` 为 CRLF，降低不同环境编辑后再次出现换行漂移的概率。
