@echo off
setlocal enabledelayedexpansion

:: 检查是否已安装bencodepy库
python -c "import bencodepy" 2>NUL
if %errorlevel% neq 0 (
    echo 正在安装bencodepy库...
    pip install bencodepy
) else (
    echo 已检测到bencodepy库，跳过安装...
)

REM 设定Python脚本的路径
set PYTHON_SCRIPT=rename_torrent.py

REM 提示用户输入目录路径
echo.
echo.
set /p INPUT_DIR=请输入包含 .torrent 文件的目录路径（按回车以使用默认的 temp 目录）：
echo.
echo.
REM 检查用户是否输入了路径
if "%INPUT_DIR%"=="" (
    REM 如果没有输入路径，使用默认的 temp 目录
    set INPUT_DIR=%~dp0temp
)

REM 遍历所有.torrent文件
for /r "%INPUT_DIR%" %%f in (*.torrent) do (
    echo 处理文件：%%f
    python "%PYTHON_SCRIPT%" "%%f"
)

endlocal
pause
