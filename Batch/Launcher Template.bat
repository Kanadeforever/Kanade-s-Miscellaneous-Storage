@echo off
title Fallout New Vegas MO Launcher
:main
cls
echo.
echo.
echo.
echo.
echo      1 - Fallout New Vegas (NVSE)
echo.
echo      2 - Fallout New Vegas Launcher (MOD Organizer Ver.)
echo.
echo      3 - Fallout New Vegas Launcher (Original Ver.)
echo.
echo      4 - Mod Organizer
echo.
echo      5 - Exit launcher
echo.
echo.
echo.
set choice=
choice /c 12345 /n /m "«Î ‰»Î[–Ú∫≈]:"

if %errorlevel% equ 1 goto main1
if %errorlevel% equ 2 goto main2
if %errorlevel% equ 3 goto main3
if %errorlevel% equ 4 goto main4
if %errorlevel% equ 5 goto main5


:main1
cls
echo.
echo.
echo.
echo.
.\Tools\MO2\ModOrganizer.exe "moshortcut://:NVSE"
echo.
echo.
echo.
:end
exit

:main2
cls
echo.
echo.
echo.
echo.
.\Tools\MO2\ModOrganizer.exe "moshortcut://:Fallout Launcher"
echo.
echo.
echo.
:end
goto main

:main3
cls
echo.
echo.
echo.
echo.
.\FalloutNVLauncher_original.exe
echo.
echo.
echo.
:end
goto main

:main4
cls
echo.
echo.
echo.
echo.
.\Tools\MO2\ModOrganizer.exe
echo.
echo.
echo.
:end
goto main

:main5
exit