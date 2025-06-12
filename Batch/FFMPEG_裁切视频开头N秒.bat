@echo off
setlocal

::设置FFMPEG路径
set "ffmpeg=E:\Software\FFmpeg\ffmpeg.exe"
::删除开头XX秒
set "second=11"

if "%~1"=="" (
    echo 请将文件拖放到此脚本上
    pause
    exit /b
)

set "input=%~1"
set "output=%~dpn1.output%~x1"

"%ffmpeg%" -i "%input%" -ss %second% -c copy -avoid_negative_ts 1 "%output%"

echo.
pause