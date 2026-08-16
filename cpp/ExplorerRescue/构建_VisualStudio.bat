@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo [错误] 未找到 vswhere.exe。请安装 Visual Studio C++ 工具链。
  pause
  exit /b 1
)

for /f "usebackq tokens=*" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%I"
if not defined VSROOT (
  echo [错误] 没有找到带 x64 C++ 工具链的 Visual Studio。
  pause
  exit /b 1
)

call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 (
  echo [错误] 初始化 Visual Studio x64 编译环境失败。
  pause
  exit /b 1
)

if not exist build mkdir build
pushd build

del /q *.obj ExplorerRescue.exe 2>nul

set "CFLAGS=/nologo /c /O1 /GS- /GR- /W4 /permissive- /DUNICODE /D_UNICODE"
cl %CFLAGS% /Fo:core.obj ..\src\core.cpp || goto :fail
cl %CFLAGS% /Fo:ole_dispatch.obj ..\src\ole_dispatch.cpp || goto :fail
cl %CFLAGS% /Fo:snapshot.obj ..\src\snapshot.cpp || goto :fail
cl %CFLAGS% /Fo:explorer.obj ..\src\explorer.cpp || goto :fail
cl %CFLAGS% /Fo:app.obj ..\src\app.cpp || goto :fail

link /nologo /SUBSYSTEM:WINDOWS /ENTRY:ExplorerRescueEntry /NODEFAULTLIB /MACHINE:X64 /OUT:ExplorerRescue.exe ^
  core.obj ole_dispatch.obj snapshot.obj explorer.obj app.obj ^
  ..\src\resources\ExplorerRescue.res kernel32.lib user32.lib ole32.lib oleaut32.lib shell32.lib || goto :fail

echo.
echo [完成] build\ExplorerRescue.exe
popd
pause
exit /b 0

:fail
echo.
echo [错误] 构建失败，请查看上方编译器输出。
popd
pause
exit /b 1
