@echo off
setlocal enabledelayedexpansion

:: ����Ƿ��Ѱ�װbencodepy��
python -c "import bencodepy" 2>NUL
if %errorlevel% neq 0 (
    echo ���ڰ�װbencodepy��...
    pip install bencodepy
) else (
    echo �Ѽ�⵽bencodepy�⣬������װ...
)

REM �趨Python�ű���·��
set PYTHON_SCRIPT=rename_torrent.py

REM ��ʾ�û�����Ŀ¼·��
echo.
echo.
set /p INPUT_DIR=��������� .torrent �ļ���Ŀ¼·�������س���ʹ��Ĭ�ϵ� temp Ŀ¼����
echo.
echo.
REM ����û��Ƿ�������·��
if "%INPUT_DIR%"=="" (
    REM ���û������·����ʹ��Ĭ�ϵ� temp Ŀ¼
    set INPUT_DIR=%~dp0temp
)

REM ��������.torrent�ļ�
for /r "%INPUT_DIR%" %%f in (*.torrent) do (
    echo �����ļ���%%f
    python "%PYTHON_SCRIPT%" "%%f"
)

endlocal
pause
