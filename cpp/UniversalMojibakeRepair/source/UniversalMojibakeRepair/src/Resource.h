#pragma once

// Resource.h 只保存 Win32 资源的数字 ID。
// RC 资源脚本和 C++ 主程序都 include 这个文件，
// 这样图标 ID 只定义一次，避免两边手写数字后改了一边却忘记同步另一边。

// 100 是应用程序主图标的资源编号。
// 数值本身没有特殊意义，只要在本 EXE 的资源表里保持唯一即可。
#define IDI_APP_ICON 100
