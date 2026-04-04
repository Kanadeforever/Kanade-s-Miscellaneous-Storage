@echo off
setlocal

python -c "import pefile" 2>NUL
if %errorlevel% neq 0 (
    echo 正在安装pefile库...
    pip install pefile
) else (
    echo 已检测到pefile库，跳过安装...
)

cls

> "tmp.py" ECHO # -*- coding: gbk -*-
>>"tmp.py" ECHO import pefile
>>"tmp.py" ECHO import sys
>>"tmp.py" ECHO.
>>"tmp.py" ECHO def check_exe_bit(filename):
>>"tmp.py" ECHO     try:
>>"tmp.py" ECHO         pe = pefile.PE(filename)
>>"tmp.py" ECHO         if hex(pe.OPTIONAL_HEADER.Magic) == '0x20b':
>>"tmp.py" ECHO             print("这是一个64位的文件。")
>>"tmp.py" ECHO         elif hex(pe.OPTIONAL_HEADER.Magic) == '0x10b':
>>"tmp.py" ECHO             print("这是一个32位的文件。")
>>"tmp.py" ECHO         else:
>>"tmp.py" ECHO             print("未知的文件类型。")
>>"tmp.py" ECHO     except FileNotFoundError:
>>"tmp.py" ECHO         print(f"未找到文件: {filename}")
>>"tmp.py" ECHO     except pefile.PEFormatError:
>>"tmp.py" ECHO         print(f"不是有效的PE文件: {filename}")
>>"tmp.py" ECHO.
>>"tmp.py" ECHO if __name__ == "__main__":
>>"tmp.py" ECHO     if len(sys.argv) != 2:
>>"tmp.py" ECHO         print("用法: python check_exe_bit.py <exe/dll文件路径>")
>>"tmp.py" ECHO     else:
>>"tmp.py" ECHO         check_exe_bit(sys.argv[1])

cls

:begin
if "%~1"=="" (
    set /p exe_path=请输入需要检测的exe/dll文件的完整路径并按回车:
) else (
    set exe_path=%~dpnx1
)

set exe_path=%exe_path:"=%

cls

python tmp.py "%exe_path%"
echo.
echo.
pause

if exist "tmp.py" del /F /Q "tmp.py"

exit /b

endlocal