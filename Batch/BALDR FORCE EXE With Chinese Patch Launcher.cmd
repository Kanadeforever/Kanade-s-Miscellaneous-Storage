@echo off
cd /d "%~dp0"
SET CurDir=%CD%
PowerShell -Command Mount-DiskImage -ImagePath '%CurDir%\BFEXE_Lite.iso'
start BaldrForce.exe
set taskname=BaldrForce.exe
:a
set a=
tasklist|find /i "%taskname%">nul 2>nul&&set b==||set a==
if "%a%%b%"=="==" goto:b
ping 127.1 -n 2 >nul
goto:a
:b
PowerShell -Command Dismount-DiskImage -ImagePath '%CurDir%\BFEXE_Lite.iso'
exit
@echo off