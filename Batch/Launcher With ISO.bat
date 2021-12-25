@echo off
rem 设置路径
cd /d "%~dp0"
SET CurDir=%CD%

rem 使用Powershell挂载ISO镜像
PowerShell -Command Mount-DiskImage -ImagePath '%CurDir%\Image.iso'

rem 执行程序
start Program.exe

rem 检查程序是否运行
set taskname=Program.exe
:a
set a=
tasklist|find /i "%taskname%">nul 2>nul&&set b==||set a==

rem 如果程序没有运行，则卸载镜像
if "%a%%b%"=="==" goto:b
ping 127.1 -n 2 >nul
goto:a
:b
PowerShell -Command Dismount-DiskImage -ImagePath '%CurDir%\Image.iso'
exit
@echo off