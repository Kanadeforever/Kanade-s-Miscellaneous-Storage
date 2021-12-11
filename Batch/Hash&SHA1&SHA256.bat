@echo off
echo 请拖入需要计算MD5、SHA1、 SHA256的文件，并按回车键继续
set /p fileNmae=
color 0A
echo 请检查文件路径，确定是否需要继续进行
pause
color 71
echo
echo 开始计算该文件SHA256
certutil -hashfile %fileNmae% SHA256
echo.
echo 开始计算文件SHA1
certutil -hashfile %fileNmae% SHA1
echo.
echo 开始计算该文件MD5
certutil -hashfile %fileNmae% MD5 
echo.
echo 计算完毕，按任意键退出
echo.
echo.
pause
