@echo off
setlocal

::����FFMPEG·��
set "ffmpeg=E:\Software\FFmpeg\ffmpeg.exe"
::ɾ����ͷXX��
set "second=11"

if "%~1"=="" (
    echo �뽫�ļ��Ϸŵ��˽ű���
    pause
    exit /b
)

set "input=%~1"
set "output=%~dpn1.output%~x1"

"%ffmpeg%" -i "%input%" -ss %second% -c copy -avoid_negative_ts 1 "%output%"

echo.
pause