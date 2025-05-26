@echo off
setlocal enabledelayedexpansion

:: �û����޸Ĳ��� ==================================
:: ����FFMPEG·�����������ϵͳPATH�У�
set FFMPEG_PATH="E:\Software\FFmpeg\ffmpeg.exe"
:: ================================================

:: ���ô��ڱ���
title FFMPEG����Ƶ����ϲ�����

:: ��ȡ�ű�����Ŀ¼
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"  :: ȥ��ĩβ�ķ�б��

:start
cls
echo �X�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�[
echo �U      FFMPEG����Ƶ����ϲ�����      �U
echo �^�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�T�a
echo.

:: FFmpeg·������߼� ==============================
set "FFMPEG_FOUND=0"
set "ACTUAL_FFMPEG_PATH="

:: 1. ���ȼ���û����õ�FFMPEG_PATH
if defined FFMPEG_PATH (
    set "TEST_PATH=!FFMPEG_PATH:"=!"
    if exist "!TEST_PATH!" (
        "!TEST_PATH!" -version >nul 2>&1
        if not errorlevel 1 (
            set "ACTUAL_FFMPEG_PATH=!TEST_PATH!"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=�û����õ�FFMPEG_PATH"
        )
    )
)

:: 2. ���ű�����Ŀ¼�Ƿ���ffmpeg.exe
if "!FFMPEG_FOUND!"=="0" (
    set "LOCAL_FFMPEG=!SCRIPT_DIR!\ffmpeg.exe"
    if exist "!LOCAL_FFMPEG!" (
        "!LOCAL_FFMPEG!" -version >nul 2>&1
        if not errorlevel 1 (
            set "ACTUAL_FFMPEG_PATH=!LOCAL_FFMPEG!"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=�ű�����Ŀ¼"
        )
    )
)

:: 3. ���ϵͳPATH�е�ffmpeg
if "!FFMPEG_FOUND!"=="0" (
    where ffmpeg >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%F in ('where ffmpeg') do (
            set "ACTUAL_FFMPEG_PATH=%%F"
            set "FFMPEG_FOUND=1"
            set "FFMPEG_SOURCE=ϵͳPATH"
        )
    )
)

:: ������м�ⶼʧ��
if "!FFMPEG_FOUND!"=="0" (
    echo ����δ�ҵ����õ�FFmpeg��
    echo.
    echo.
    echo �Ѽ������λ�ã�
    echo.
    if defined FFMPEG_PATH echo 1. �û�����·��: !FFMPEG_PATH!
    echo 2. �ű�����Ŀ¼: !SCRIPT_DIR!\ffmpeg.exe
    echo 3. ϵͳPATH��������
    echo.
    echo �����������ѡһ����
    echo.
    echo 1. ��ffmpeg.exe���ڽű�ͬĿ¼�£�
    echo 2. �༭���ű��޸� FFMPEG_PATH ������
    echo 3. ��װFFmpeg����ӵ�ϵͳPATH��
    echo.
    echo.
    pause
    exit /b
)

:: �����Ĭ��·����ʵ��ʹ�õ���������Դ
if defined FFMPEG_PATH (
    set "DEFAULT_CHECK=!FFMPEG_PATH:"=!"
    if "!DEFAULT_CHECK!"=="E:\Software\FFMPEG\ffmpeg.exe" (
        if not "!ACTUAL_FFMPEG_PATH!"=="!DEFAULT_CHECK!" (
            echo ע�⣺��������Ĭ��FFmpeg·������ʵ��ʹ�õ���!FFMPEG_SOURCE!��ffmpeg
            echo.
            echo ��ǰʹ��: !ACTUAL_FFMPEG_PATH!
            echo.
        )
    )
)

echo FFmpeg·����֤ͨ�� [!FFMPEG_SOURCE!]:
echo.
echo !ACTUAL_FFMPEG_PATH!
echo.
echo.

:: �����򲿷� ======================================
echo �밴���²��������
echo.
echo 1. �Ƚ���Ƶ�ļ��ϵ������ڣ�Ȼ�󰴻س���
echo 2. �ٽ���Ƶ�ļ��ϵ������ڣ�Ȼ�󰴻س���
echo 3. �����Զ��ϲ������ı��κα����������
echo.
echo.
echo ע�⣺��ȷ���ļ�·�����������ַ���
echo.
echo.
echo.

:: ��ȡ��Ƶ�ļ�
set video_file=
set /p "video_file=��������Ƶ�ļ�Ȼ�󰴻س�: "
if "!video_file!"=="" (
    echo δ������Ƶ�ļ��������²�����
    timeout /t 3 >nul
    goto start
)

:: ȥ����ק�ļ�ʱ�Զ���ӵ�˫����
set "video_file=!video_file:"=!"

:: �����Ƶ�ļ��Ƿ����
if not exist "!video_file!" (
    echo ��Ƶ�ļ�������: !video_file!
    timeout /t 3 >nul
    goto start
)

echo.

:: ��ȡ��Ƶ�ļ�
set audio_file=
set /p "audio_file=��������Ƶ�ļ�Ȼ�󰴻س�: "
if "!audio_file!"=="" (
    echo δ������Ƶ�ļ��������²�����
    timeout /t 3 >nul
    goto start
)

:: ȥ����ק�ļ�ʱ�Զ���ӵ�˫����
set "audio_file=!audio_file:"=!"

:: �����Ƶ�ļ��Ƿ����
if not exist "!audio_file!" (
    echo ��Ƶ�ļ�������: !audio_file!
    timeout /t 3 >nul
    goto start
)

:: ��������ļ���������ԭ��Ƶ��ʽ��
for %%i in ("!video_file!") do (
    set "output_file=%%~dpni_merged%%~xi"
)

:: echo.
:: echo ���ںϲ�...
:: echo ��Ƶ�ļ�: !video_file!
:: echo ��Ƶ�ļ�: !audio_file!
:: echo ����ļ�: !output_file!
:: echo.

:: ʹ��FFMPEG�ϲ��������±��룩
cls
"!ACTUAL_FFMPEG_PATH!" -i "!video_file!" -i "!audio_file!" -c:v copy -c:a copy -map 0:v:0 -map 1:a:0 -shortest "!output_file!"

if errorlevel 1 (
    echo.
    echo �ϲ�ʧ�ܣ�ת����־���Ϸ���
    echo ���²�������:
    echo 1. �����ļ��Ƿ���Ч
    echo 2. ���·���Ƿ���д��Ȩ��
    echo 3. �ļ�·���Ƿ���������ַ�
    echo.
    pause
    goto start
)

cls
echo.
echo �ϲ��ɹ�������ļ��ѱ���Ϊ:
echo.
echo !output_file!
echo.
echo.
echo ��������������˵�...
pause >nul
goto start