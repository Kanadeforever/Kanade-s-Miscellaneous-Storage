@ECHO OFF
title ע����޸�
SET game=%CD%
SET game=%game:\=\\%
> "%Temp%.\tempreg.reg" ECHO Windows Registry Editor Version 5.00
>>"%Temp%.\tempreg.reg" ECHO.
>>"%Temp%.\tempreg.reg" ECHO [ע���·��]
>>"%Temp%.\tempreg.reg" ECHO ������дע���ľ�������
cls
"%windir%\regedit.exe" /s "%Temp%.\tempreg.reg"
"%windir%\SysWoW64\regedit.exe" /s "%Temp%.\tempreg.reg"
DEL "%Temp%.\tempreg.reg"
cls
echo.
echo ע�����ɹ�����������˳���
echo. 
pause >nul
cls