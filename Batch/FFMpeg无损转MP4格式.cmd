@echo off

title FFMpeg����תMP4��ʽ

rem ֱ�ӽ��ļ���ק����Bat�ļ��ϼ��ɣ�����������

rem ע��ԭ�ļ��������С�)������^������&������=������;������,������

set ffmpegpath=%~dp0

for %%a in (%*) do (

  "%ffmpegpath%ffmpeg.exe" -i %%a -codec copy "%ffmpegpath%%%~na.mp4"

)

exit