@echo off
rem 设置路径
cd /d "%~dp0"
SET CurDir=%CD%

rem 使用Powershell挂载ISO镜像
PowerShell -Command Mount-DiskImage -ImagePath '%CurDir%\Image.iso'

rem 执行程序
start YourAppName1.exe

rem 检查程序是否运行
:step_a
tasklist | find /i "YourAppName1.exe" > nul
set process1_exist=%errorlevel%

rem 如果进程存在，则3秒后继续循环检测
if %process1_exist% equ 0 (
    timeout /t 3
    goto step_a
) else (
    goto step_b
)

:step_b
PowerShell -Command Dismount-DiskImage -ImagePath '%CurDir%\Image.iso'
exit
@echo off
