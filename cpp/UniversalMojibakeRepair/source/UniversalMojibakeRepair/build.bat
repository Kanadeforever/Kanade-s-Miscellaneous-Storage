@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion

rem 编译入口位于 source\UniversalMojibakeRepair，临时文件进入根目录 .build，正式 EXE 进入 release。  
rem 脚本使用系统 PATH 和 VS Installer 查询工具链，不写死开发机的编译器或 SDK 安装位置。  
cd /d "%~dp0"
if errorlevel 1 goto failed
rem 先通过当前目录定位工程根目录，保留用户原本的中文项目文件夹。  
rem 这里只保存真实目录；稍后将通过临时盘符给编译工具提供不含中文的路径。  
pushd "..\.."
if errorlevel 1 goto failed
set "ROOT=%CD%"
popd
set "SUBST_DRIVE="
set "PROJECT="
set "BUILD_DIR="
set "OUTPUT_DIR="
set "VS_INSTALL="
set "CMAKE_EXE="
set "CTEST_EXE="

rem C++ 编译器、NMake、资源编译器和清单工具全部就绪，才算具备完整的 Win32 构建环境。  
rem 某些终端只有 cl.exe，而 rc.exe 和 mt.exe 尚未被加入 PATH，因此不能仅检测编译器。  
set "TOOLS_READY=1"
where cl.exe >nul 2>nul
if errorlevel 1 set "TOOLS_READY=0"
where nmake.exe >nul 2>nul
if errorlevel 1 set "TOOLS_READY=0"
where rc.exe >nul 2>nul
if errorlevel 1 set "TOOLS_READY=0"
where mt.exe >nul 2>nul
if errorlevel 1 set "TOOLS_READY=0"
if "%TOOLS_READY%"=="1" goto tools_ready

rem 有工具缺失时，尝试从 Visual Studio 安装器定位 vcvars64.bat 重新初始化完整环境。  
rem vcvars64.bat 在安装 Windows SDK 时会添加 SDK 的 bin 目录，里面提供 rc.exe 和 mt.exe。  
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto tools_ready
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_INSTALL=%%I"
if not defined VS_INSTALL goto tools_ready
if not exist "%VS_INSTALL%\VC\Auxiliary\Build\vcvars64.bat" goto tools_ready
call "%VS_INSTALL%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 goto failed

:tools_ready
rem 在启动 CMake 之前分别诊断 C++ 工具链和 Windows SDK，避免把组件缺失误报成源码错误。  
where cl.exe >nul 2>nul
if errorlevel 1 goto missing_compiler
where nmake.exe >nul 2>nul
if errorlevel 1 goto missing_compiler
where rc.exe >nul 2>nul
if errorlevel 1 goto missing_sdk
where mt.exe >nul 2>nul
if errorlevel 1 goto missing_sdk

rem 优先使用 PATH 中的 CMake，也支持当前 Visual Studio 安装目录附带的 CMake。  
for /f "delims=" %%I in ('where cmake.exe 2^>nul') do if not defined CMAKE_EXE set "CMAKE_EXE=%%I"
if not defined CMAKE_EXE if defined VS_INSTALL if exist "%VS_INSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" set "CMAKE_EXE=%VS_INSTALL%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not defined CMAKE_EXE goto missing_cmake
for %%C in ("%CMAKE_EXE%") do set "CTEST_EXE=%%~dpCctest.exe"
if not exist "%CTEST_EXE%" goto missing_cmake

rem NMake/MSVC 某些组件无法可靠处理路径中的非 ASCII 字符，尤其是链接器的 PDB 输出目录。  
rem 遍历未占用的盘符，临时把项目根目录映射为英文路径；这不会移动或复制原有文件。  
rem 映射必须成功才继续，避免构建工具悄悄退回中文真实路径后再次发生乱码链接失败。  
for %%D in (Z Y X W V U T S R Q P O N M L K J I H G F E D) do (
    if not defined SUBST_DRIVE (
        if not exist "%%D:\" (
            subst %%D: "%ROOT%" >nul 2>nul
            if not errorlevel 1 set "SUBST_DRIVE=%%D:"
        )
    )
)
if not defined SUBST_DRIVE goto mount_failed
rem 子目录和代码文件名称均为英文，编译器只能看到映射盘符下的 ASCII 路径。  
set "PROJECT=%SUBST_DRIVE%\source\UniversalMojibakeRepair"
set "BUILD_DIR=%SUBST_DRIVE%\.build\msvc-x64"
set "OUTPUT_DIR=%SUBST_DRIVE%\release"
if not exist "%PROJECT%\CMakeLists.txt" goto failed
rem 当前工作目录也进入映射位置，避免子进程从中文工作目录重新生成绝对路径。  
cd /d "%PROJECT%"
if errorlevel 1 goto failed

rem 旧 CMake 缓存可能记住 CMAKE_MT-NOTFOUND；SDK 恢复后必须重新检测，不能沿用失败缓存。  
rem .build 是专用的临时构建目录，因此可以删除其中属于本项目的旧构建结果。  
if exist "%BUILD_DIR%" (
    echo [准备] 正在清理上次构建的临时缓存...  
    rmdir /s /q "%BUILD_DIR%"
    if exist "%BUILD_DIR%" goto failed
)

rem 使用 NMake 生成器，不依赖某个固定的 Visual Studio 版本号。  
echo [路径] 已为工程建立临时 ASCII 路径：%SUBST_DRIVE%\  
echo [1/4] 正在生成 Release x64 工程...  
"%CMAKE_EXE%" -S "%PROJECT%" -B "%BUILD_DIR%" -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release
if errorlevel 1 goto failed

rem 源码改动后同时编译 GUI 和独立的格式转换回归测试。  
echo [2/4] 正在编译程序和回归测试...  
"%CMAKE_EXE%" --build "%BUILD_DIR%" --config Release
if errorlevel 1 goto failed

rem 测试未通过时不生成新的正式发行文件。  
echo [3/4] 正在运行格式转换回归测试...  
"%CTEST_EXE%" --test-dir "%BUILD_DIR%" --output-on-failure
if errorlevel 1 goto failed

rem 仅在编译和回归测试均通过后，把程序复制到固定的正式发行目录。  
echo [4/4] 正在输出正式 EXE...  
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
copy /y "%BUILD_DIR%\UniversalMojibakeRepair.exe" "%OUTPUT_DIR%\万能乱码恢复.exe" >nul
if errorlevel 1 goto failed

rem 成功后清除可再生的临时文件；失败时保留构建现场供排查。  
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
call :cleanup_mount
echo [完成] release\万能乱码恢复.exe 已生成。  
echo [说明] 程序本体不需要附带运行时脚本。  
pause
exit /b 0

:mount_failed
echo [错误] 无法建立空闲的临时盘符映射，已取消构建以防止中文路径在链接器中损坏。  
echo [检查] 请在 CMD 中运行 subst，确认是否已有其他软件占用了全部候选盘符。  
pause
exit /b 1

:cleanup_mount
rem 仅撤销当前脚本自己创建的映射；未创建时不碰其他已有盘符。  
rem 构建失败时同样撤销映射，但保留真实目录中的 .build 日志供后续排查。  
if defined SUBST_DRIVE (
    rem 必须先离开映射盘符，否则系统可能拒绝卸载当前正在使用的虚拟驱动器。  
    cd /d "%ROOT%"
    subst %SUBST_DRIVE% /D >nul 2>nul
    if errorlevel 1 echo [提醒] 临时盘符 %SUBST_DRIVE% 撤销失败，可在关闭程序后手动运行 subst %SUBST_DRIVE% /D。  
)
exit /b 0

:missing_compiler
echo [错误] 找不到 MSVC x64 编译器或 NMake。  
echo [解决] 在 Visual Studio Installer 中安装“使用 C++ 的桌面开发”及 MSVC x64 编译工具。  
pause
exit /b 1

:missing_sdk
echo [错误] Windows SDK 资源编译器 rc.exe 或清单工具 mt.exe 不存在于当前构建环境。  
echo [解决] 打开 Visual Studio Installer，点击“修改”，在“单个组件”中安装 Windows 10/11 SDK。  
echo [检查] 可以使用 where rc.exe 和 where mt.exe 确认两个工具是否已被找到。  
echo [说明] 只有 cl.exe 还不够；该原生 Win32 程序的图标资源与清单处理都需要 Windows SDK。  
pause
exit /b 1

:missing_cmake
echo [错误] 找不到 CMake 或 CTest，请安装 CMake 或启用 Visual Studio 附带的 CMake。  
pause
exit /b 1

:failed
call :cleanup_mount
echo [错误] 构建、测试或复制失败；请保存上方完整日志。  
pause
exit /b 1
