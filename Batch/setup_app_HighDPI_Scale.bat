@echo off
setlocal EnableExtensions EnableDelayedExpansion

:import
rem ==== 获取目标路径（拖拽或输入） ====
if "%~1"=="" (
    set /p "exePath=请输入要设置的 EXE 文件完整路径: "
) else (
    set "exePath=%~1"
)
rem 去除可能存在的双引号
set "exePath=%exePath:"=%"

rem ==== 验证文件存在 ====
if not exist "%exePath%" (
    echo [错误] 找不到文件 "%exePath%"
    pause
    exit /b 1
)

rem ==== 验证扩展名为 .exe ====
for %%I in ("%exePath%") do (
    if /i not "%%~xI"==".exe" (
        echo [错误] 仅支持 .exe 文件
        pause
        exit /b 1
    )
)

:apply
cls
echo 正在为 "%exePath%" 设置高 DPI 缩放为“应用程序”模式…

rem ==== 准备临时 .reg 文件 ====
set "tmpReg=%temp%\SetDpiAware.reg"
rem 将路径里的单个反斜杠转换为双反斜杠
set "regPath=%exePath:\=\\%"

(
    echo Windows Registry Editor Version 5.00
    echo.
    echo [HKEY_CURRENT_USER\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers]
    echo "%regPath%"="~ HIGHDPIAWARE"
) >"%tmpReg%"

rem ==== 导入 .reg 文件 ====
"%windir%\regedit.exe" /s "%tmpReg%"
"%windir%\SysWoW64\regedit.exe" /s "%tmpReg%"


if errorlevel 1 (
    cls
    echo [错误] 导入注册表失败，请检查权限或以管理员身份运行脚本
    del "%tmpReg%" >nul 2>&1
    pause
    exit /b 1
)

rem ==== 完成提示与清理 ====
cls
echo.
echo [完成] 已为以下程序设置高 DPI 缩放：
echo     "%exePath%"
echo.
del "%tmpReg%" >nul 2>&1

if "%~1"=="" (
    echo 按任意键继续设置其他程序…
    pause
    cls
    goto import
) else (
    echo 按任意键退出…
    pause
    endlocal
    exit /b
)
