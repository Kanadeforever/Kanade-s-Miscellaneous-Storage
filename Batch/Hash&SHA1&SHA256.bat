@echo off
echo ��������Ҫ����MD5��SHA1�� SHA256���ļ��������س�������
set /p fileNmae=
color 0A
echo �����ļ�·����ȷ���Ƿ���Ҫ��������
pause
color 71
echo
echo ��ʼ������ļ�SHA256
certutil -hashfile %fileNmae% SHA256
echo.
echo ��ʼ�����ļ�SHA1
certutil -hashfile %fileNmae% SHA1
echo.
echo ��ʼ������ļ�MD5
certutil -hashfile %fileNmae% MD5 
echo.
echo ������ϣ���������˳�
echo.
echo.
pause
