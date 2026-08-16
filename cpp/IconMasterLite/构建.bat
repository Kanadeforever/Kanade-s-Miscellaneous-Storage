@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where cmake >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 CMake。请先安装 CMake，或从 Visual Studio Developer Command Prompt 运行。
    goto :end
)

if exist build rmdir /s /q build
cmake -S . -B build -A Win32
if errorlevel 1 goto :fail
cmake --build build --config Release
if errorlevel 1 goto :fail

echo.
echo [完成] 程序位于：build\Release\IconMasterLite.exe
echo [提示] EXE 已内置程序图标，无需额外复制 ICO 文件。
goto :end

:fail
echo.
echo [失败] 构建失败，请查看上面的编译器输出。

:end
echo.
pause
endlocal
