@echo off
rem 设置路径
cd /d "%~dp0"
SET CurDir=%CD%

rem 使用Powershell挂载ISO镜像
PowerShell -Command Mount-DiskImage -ImagePath '%CurDir%\Image.iso'

rem 执行程序
start YourAppName1.exe

timeout /t 5

rem 检查程序是否运行
:step_a
tasklist | find /i "YourAppName1.exe" > nul
if %errorlevel% equ 0 (
        timeout /t 3
        cls
) else (

tasklist | find /i "YourAppName2.exe" > nul
if %errorlevel% equ 0 (
        timeout /t 3
        cls
) else (

tasklist | find /i "YourAppName3.exe" > nul
if %errorlevel% equ 0 (
        timeout /t 3
        cls
) else (
    goto step_b
)

:step_b
PowerShell -Command Dismount-DiskImage -ImagePath '%CurDir%\Image.iso'
exit
@echo off
