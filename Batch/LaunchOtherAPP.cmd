@echo off
setlocal

:: 把模板复制到游戏exe所在目录，然后修改下面程序的名字

:: 启动 YourApp和游戏程序
:: cmd的启动方式
::start "" "YourApp.exe"
:: 调用powershell的启动方式，并且启动后最小化（YourApp会最小化or最小化到托盘） 
powershell Start-Process -FilePath "C:\YourAppFolderPath\YourApp.exe" -WindowStyle Minimized
start "" "YourGameName1.exe"

:: 等待 5 秒
timeout /t 5

:loop
cls

:: 检测 YourGameName1.exe 进程是否存在
tasklist | find /i "YourGameName1.exe" > nul
set process1_exist=%errorlevel%

:: 检测 YourGameName2.exe 进程是否存在
tasklist | find /i "YourGameName2.exe" > nul
set process2_exist=%errorlevel%

:: 检测 YourGameName3.exe 进程是否存在
tasklist | find /i "YourGameName3.exe" > nul
set process3_exist=%errorlevel%


:: 如果任何一个进程存在，则继续循环
if %process1_exist% equ 0 (
    timeout /t 3
    goto loop
) else if %process2_exist% equ 0 (
    timeout /t 3
    goto loop
) else if %process3_exist% equ 0 (
    timeout /t 3
    goto loop
)

:endloop
:: 如果所有进程都不存在，则结束你指定的进程 YourApp.exe

:: cmd的结束方式
taskkill /f /im YourApp.exe
:: 调用powershell的结束方式，可结束管理员权限打开的窗口
::powershell -Command "(Get-WmiObject -Class Win32_Process -Filter \"name = 'YourApp.exe'\").Terminate()"

endlocal