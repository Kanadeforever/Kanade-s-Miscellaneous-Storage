@echo off

title FFMpeg无损转MP4格式

rem 直接将文件拖拽至本Bat文件上即可，可批量进行

rem 注：原文件名不能有“)”、“^”、“&”、“=”、“;”、“,”符号

set ffmpegpath=%~dp0

for %%a in (%*) do (

  "%ffmpegpath%ffmpeg.exe" -i %%a -codec copy "%ffmpegpath%%%~na.mp4"

)

exit