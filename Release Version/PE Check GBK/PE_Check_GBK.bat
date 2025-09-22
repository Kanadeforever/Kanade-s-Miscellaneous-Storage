@echo off
setlocal

python -c "import pefile" 2>NUL
if %errorlevel% neq 0 (
    echo ���ڰ�װpefile��...
    pip install pefile
) else (
    echo �Ѽ�⵽pefile�⣬������װ...
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
>>"tmp.py" ECHO             print("����һ��64λ���ļ���")
>>"tmp.py" ECHO         elif hex(pe.OPTIONAL_HEADER.Magic) == '0x10b':
>>"tmp.py" ECHO             print("����һ��32λ���ļ���")
>>"tmp.py" ECHO         else:
>>"tmp.py" ECHO             print("δ֪���ļ����͡�")
>>"tmp.py" ECHO     except FileNotFoundError:
>>"tmp.py" ECHO         print(f"δ�ҵ��ļ�: {filename}")
>>"tmp.py" ECHO     except pefile.PEFormatError:
>>"tmp.py" ECHO         print(f"������Ч��PE�ļ�: {filename}")
>>"tmp.py" ECHO.
>>"tmp.py" ECHO if __name__ == "__main__":
>>"tmp.py" ECHO     if len(sys.argv) != 2:
>>"tmp.py" ECHO         print("�÷�: python check_exe_bit.py <exe/dll�ļ�·��>")
>>"tmp.py" ECHO     else:
>>"tmp.py" ECHO         check_exe_bit(sys.argv[1])

cls

:begin
if "%~1"=="" (
    set /p exe_path=��������Ҫ����exe/dll�ļ�������·�������س�:
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