@echo off

title to HEVC

rem 注：原文件名不能有“)”、“^”、“&”、“=”、“;”、“,”符号

set ffmpegpath=%~dp0

for %%a in (%*) do (

  "%ffmpegpath%ffmpeg.exe" -i %%a -c:v libx265 -c:a copy "%ffmpegpath%%%~na_HEVC.mp4"
  
)

exit