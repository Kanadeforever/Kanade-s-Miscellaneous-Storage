@echo off
setlocal disableDelayedExpansion

set "ffmpeg=%~dp0ffmpeg.exe"

:loop
if "%~1"=="" exit /b
set "input=%~1"
set "output=%~n1.mp4"

setlocal enableDelayedExpansion
"%ffmpeg%" -i "!input!" -codec copy "!ffmpegpath!!output!"
endlocal

shift
goto loop
