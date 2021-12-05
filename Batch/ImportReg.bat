@ECHO OFF
title 注册表修复
SET game=%CD%
SET game=%game:\=\\%
> "%Temp%.\tempreg.reg" ECHO Windows Registry Editor Version 5.00
>>"%Temp%.\tempreg.reg" ECHO.
>>"%Temp%.\tempreg.reg" ECHO [注册表路径]
>>"%Temp%.\tempreg.reg" ECHO 这里填写注册表的具体内容
cls
"%windir%\regedit.exe" /s "%Temp%.\tempreg.reg"
"%windir%\SysWoW64\regedit.exe" /s "%Temp%.\tempreg.reg"
DEL "%Temp%.\tempreg.reg"
cls
echo.
echo 注册表导入成功，按任意键退出。
echo. 
pause >nul
cls