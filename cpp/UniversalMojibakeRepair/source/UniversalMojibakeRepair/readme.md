# 从零构建说明

本项目使用原生 Win32/C++17。打开 `source\UniversalMojibakeRepair\build.bat` 即可构建程序，无须手动指定 Visual Studio 的安装路径。

## 准备编译环境

安装支持 C++ 桌面开发的 Visual Studio / Build Tools、**Windows 10/11 SDK（必须包含 `rc.exe` 和 `mt.exe`）**及 CMake。即使 `cl.exe` 和 `nmake.exe` 已安装，缺少 Windows SDK 也无法链接使用图标资源与应用程序清单的 Win32 EXE。

可以提前在 MSVC x64 开发者命令提示符中运行 BAT。构建脚本通过 `vswhere.exe` 自动查找 Visual Studio，并在工具链不完整时尝试运行 `vcvars64.bat`，从而将已安装的 Windows SDK 工具加入 PATH；不依赖其他历史发行包，也不在源码中写死编译器绝对路径。

## 编译和验证

1. 双击 `build.bat`。
2. 脚本自动选择空闲盘符，用 Windows `subst` 将项目根目录临时映射为纯英文路径（例如 `Z:\`）。只改变工具访问文件时看到的路径，**不移动、复制、重命名项目文件，也不要求管理员权限**。
3. 当前工作目录与 CMake / NMake / MSVC 均使用映射盘符下的源码路径和 `.build\msvc-x64`；正式 EXE 仍生成到原始项目中的 `release\万能乱码恢复.exe`。脚本执行完毕或正常报错退出时，自动撤销本次创建的盘符。
4. 脚本配置 x64 Release，编译 GUI 和格式转换回归测试。只有自动化格式测试通过才会复制正式 EXE。
5. 构建前先检查 `cl.exe`、`nmake.exe`、`rc.exe`、`mt.exe`，任何工具缺失都会给出对应提示。检查通过后清理上次可能包含 `CMAKE_MT-NOTFOUND` 的 `.build\msvc-x64` 缓存，重新配置。编译失败时查看窗口输出及 `.build\msvc-x64` 中的 CMake 日志；成功后 BAT 会清理该临时目录。
6. EXE 使用随项目提供的 `config\AppIcon.ico` 资源，在编译时嵌入，无需另外复制图标。

## 非 Windows 算法验证

拥有 G++ 的环境可在工程目录执行：

```bash
g++ -std=c++17 -Wall -Wextra -Wpedantic tools/test_transform.cpp src/TransformEngine.cpp -O2 -o test_transform
./test_transform
```

这只验证跨平台格式算法，不等于在 Windows 上完成 EXE 编译或图形界面实机验收。

## Windows 路径含中文时

用户实际工程路径可包含中文或空格，例如 `C:\Project\Github\万能乱码恢复_v0.2.0_完整工程包`。已观察到：MSVC 测试源码编译完成、Windows SDK 的 `rc.exe`/`mt.exe` 被找到且资源编译成功，但 NMake 链接步骤中的 `/pdb:` 路径变成乱码，出现 `Unknown system error -1`。**当前优先怀疑 NMake/MSVC/CMake 在中文绝对路径之间的编码传递；尚不能仅凭一份日志证明唯一根因。**

修正版在调用 CMake 前执行 Windows `subst`，选择空闲盘符映射工程根目录。CMake 收到的参数会是类似：

```bat
cmake -S "Z:\source\UniversalMojibakeRepair" -B "Z:\.build\msvc-x64" -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release
```

上述只是映射成功后的路径示例，**并不固定要求 `Z:`**。原项目文件始终在用户的真实目录下。构建结束或错误退出时撤销映射；如果强制关闭窗口导致未自动撤销，可以先运行 `subst` 查看映射，再对确认由本次构建创建的盘符执行 `subst Z: /D`（盘符以实际输出为准）。不得在没有确认映射来源时随意删除其他盘符。

映射只能隔离中文绝对路径，不能解决工具缺失、代码语法错误、杀毒软件拦截等其他原因。如果新日志的 `-S`、`-B`、`/pdb:` 都是映射盘符下的英文路径但链接仍失败，应继续以该日志查找独立问题。

## 找到了 cl.exe，却显示 rc.exe 或 mt.exe 不可用

这通常不是项目的 C++ 源码错误，而是 Windows SDK 缺失或 SDK 的环境变量未正确加载。确认方式：从 **VS x64 开发者命令提示符**分别运行：

```bat
where cl.exe
where nmake.exe
where rc.exe
where mt.exe
```

如果前两个成功、后两个失败，打开 **Visual Studio Installer → 修改 → 单个组件**，安装与你的 Visual Studio 版本兼容的 **Windows 10/11 SDK**，再重新运行 `build.bat`。若 SDK 已安装但 `where` 仍找不到，请检查 VS 安装器中的 SDK 组件状态、Windows Kits 安装情况及 `vcvars64.bat` 执行结果，不要将 CMake 的 `CMAKE_MT-NOTFOUND` 当作程序逻辑错误。

出现 `rc /fo ... failed` 且 `CMAKE_MT-NOTFOUND` 时尤其应先检查这两项。项目脚本会在工具齐备后自动清理旧失败缓存，避免再次使用过期探测结果。工程可以位于包含中文和空格的目录，但若此后出现**明确的中文路径解析错误**，应另行按新日志定位；不能将所有后续错误都归因于 SDK。
