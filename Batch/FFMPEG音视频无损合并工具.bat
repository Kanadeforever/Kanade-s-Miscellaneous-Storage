@echo off
setlocal enabledelayedexpansion

:: 用户可修改部分 ==================================
:: 设置FFMPEG路径（如果不在系统PATH中）
set FFMPEG_PATH="E:\Software\FFmpeg\ffmpeg.exe"
:: ================================================

:: 设置窗口标题
title FFMPEG音视频无损合并工具

:: 获取脚本所在目录
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"  :: 去除末尾的反斜杠

:start
cls
echo ╔════════════════════════════════════╗
echo ║      FFMPEG音视频无损合并工具      ║
echo ╚════════════════════════════════════╝
echo.

:: FFmpeg路径检测逻辑 ==============================
set "FFMPEG_FOUND=0"
set "ACTUAL_FFMPEG_PATH="

:: 1. 首先检查用户设置的FFMPEG_PATH
if defined FFMPEG_PATH (
    set "TEST_PATH=!FFMPEG_PATH:"=!"
    if exist "!TEST_PATH!" (
        "!TEST_PATH!" -version >nul 2>&1
        if not errorlevel 1 (
            set "ACTUAL_FFMPEG_PATH=!TEST_PATH!"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=用户设置的FFMPEG_PATH"
        )
    )
)

:: 2. 检查脚本所在目录是否有ffmpeg.exe
if "!FFMPEG_FOUND!"=="0" (
    set "LOCAL_FFMPEG=!SCRIPT_DIR!\ffmpeg.exe"
    if exist "!LOCAL_FFMPEG!" (
        "!LOCAL_FFMPEG!" -version >nul 2>&1
        if not errorlevel 1 (
            set "ACTUAL_FFMPEG_PATH=!LOCAL_FFMPEG!"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=脚本所在目录"
        )
    )
)

:: 3. 检查系统PATH中的ffmpeg
if "!FFMPEG_FOUND!"=="0" (
    where ffmpeg >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%F in ('where ffmpeg') do (
            set "ACTUAL_FFMPEG_PATH=%%F"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=系统PATH"
        )
    )
)

:: 如果所有检测都失败
if "!FFMPEG_FOUND!"=="0" (
    echo 错误：未找到可用的FFmpeg！
    echo.
    echo.
    echo 已检查以下位置：
    echo.
    if defined FFMPEG_PATH echo 1. 用户设置路径: !FFMPEG_PATH!
    echo 2. 脚本所在目录: !SCRIPT_DIR!\ffmpeg.exe
    echo 3. 系统PATH环境变量
    echo.
    echo 解决方法（三选一）：
    echo.
    echo 1. 将ffmpeg.exe放在脚本同目录下；
    echo 2. 编辑本脚本修改 FFMPEG_PATH 变量；
    echo 3. 安装FFmpeg并添加到系统PATH。
    echo.
    echo.
    pause
    exit /b
)

:: 如果是默认路径但实际使用的是其他来源
if defined FFMPEG_PATH (
    set "DEFAULT_CHECK=!FFMPEG_PATH:"=!"
    if "!DEFAULT_CHECK!"=="E:\Software\FFMPEG\ffmpeg.exe" (
        if not "!ACTUAL_FFMPEG_PATH!"=="!DEFAULT_CHECK!" (
            echo 注意：您设置了默认FFmpeg路径，但实际使用的是!FFMPEG_SOURCE!的ffmpeg
            echo.
            echo 当前使用: !ACTUAL_FFMPEG_PATH!
            echo.
        )
    )
)

echo FFmpeg路径验证通过 [!FFMPEG_SOURCE!]:
echo.
echo !ACTUAL_FFMPEG_PATH!
echo.
echo.

:: 主程序部分 ======================================
echo 请按以下步骤操作：
echo.
echo 1. 先将视频文件拖到本窗口，然后按回车；
echo 2. 再将音频文件拖到本窗口，然后按回车；
echo 3. 程序将自动合并（不改变任何编码参数）。
echo.
echo.
echo 注意：请确保文件路径不含特殊字符。
echo.
echo.
echo.

:: 获取视频文件
set video_file=
set /p "video_file=请拖入视频文件然后按回车: "
if "!video_file!"=="" (
    echo 未输入视频文件，请重新操作！
    timeout /t 3 >nul
    goto start
)

:: 去除拖拽文件时自动添加的双引号
set "video_file=!video_file:"=!"

:: 检查视频文件是否存在
if not exist "!video_file!" (
    echo 视频文件不存在: !video_file!
    timeout /t 3 >nul
    goto start
)

echo.

:: 获取音频文件
set audio_file=
set /p "audio_file=请拖入音频文件然后按回车: "
if "!audio_file!"=="" (
    echo 未输入音频文件，请重新操作！
    timeout /t 3 >nul
    goto start
)

:: 去除拖拽文件时自动添加的双引号
set "audio_file=!audio_file:"=!"

:: 检查音频文件是否存在
if not exist "!audio_file!" (
    echo 音频文件不存在: !audio_file!
    timeout /t 3 >nul
    goto start
)

:: 生成输出文件名（保持原视频格式）
for %%i in ("!video_file!") do (
    set "output_file=%%~dpni_merged%%~xi"
)

:: echo.
:: echo 正在合并...
:: echo 视频文件: !video_file!
:: echo 音频文件: !audio_file!
:: echo 输出文件: !output_file!
:: echo.

:: 使用FFMPEG合并（不重新编码）
cls
"!ACTUAL_FFMPEG_PATH!" -i "!video_file!" -i "!audio_file!" -c:v copy -c:a copy -map 0:v:0 -map 1:a:0 -shortest "!output_file!"

if errorlevel 1 (
    echo.
    echo 合并失败，转换日志见上方。
    echo 以下步骤请检查:
    echo 1. 输入文件是否有效
    echo 2. 输出路径是否有写入权限
    echo 3. 文件路径是否包含特殊字符
    echo.
    pause
    goto start
)

cls
echo.
echo 合并成功！输出文件已保存为:
echo.
echo !output_file!
echo.
echo.
echo 按任意键返回主菜单...
pause >nul
goto start