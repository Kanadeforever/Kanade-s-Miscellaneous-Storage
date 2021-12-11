#SingleInstance Force   		; 当脚本已经运行时自动重启脚本。
                        		; #SingleInstance: Force - 自动重启; Ignore - 不重启; Prompt - 询问是否重启（默认选项）; Off - 允许同时运行多个实例。
#NoEnv                  		; 为了性能和与未来AutoHotkey版本的兼容性，推荐使用。
#NoTrayIcon             		; 不显示托盘图标。
; #Warn                			; 启用警告，以协助检测常见错误。


SetWorkingDir %A_ScriptDir%     ; 设置脚本所在位置为工作目录。
SetBatchLines -1                ; 设置脚本运行速度；
                                ; SetBatchLines: -1 - 全速运行；20ms - 每次运行 20 ms 之后休眠 10 ms；LineCount(即直接输入数字而无ms结尾) - 休眠前要执行脚本的行数，与ms模式互斥。


; GUI无最大化按钮、总在最前、无DPI缩放
Gui -MaximizeBox +AlwaysOnTop -DPIScale

; 窗口大小，窗口标题
Gui Show, w971 h415, 刀剑封魔录启动器

; 文字大小，粗细，字体
Gui Font, s20 Bold, Arial
; 文本类型，文本位置，字体种类，文本内容
Gui Add, Text, x321 y40 w328 h53 ,刀剑封魔录启动器

; 文字大小，粗细，字体
Gui Font, s9 Bold, Arial 
; 按钮触发变量，按钮位置与大小，按钮显示内容
Gui Add, Button, gbtnOriginal x126 y160 w167 h40, &原始分辨率
Gui Add, Button, gbtn480P x310 y160 w167 h40, &480P
Gui Add, Button, gbtn540P x494 y160 w167 h40, &540P
Gui Add, Button, gbtn720P x678 y160 w167 h40, &720P
Gui Add, Button, gbtn768P x126 y216 w167 h40, &768P
Gui Add, Button, gbtn900P x310 y216 w167 h40, &900P
Gui Add, Button, gbtn1080P x494 y216 w167 h40, &1080P
Gui Add, Button, gbtnINI x678 y216 w167 h40, &修改INI文件

; 文字大小，字体
Gui Font, s7, Arial
Gui Add, Button, gbtnConfig x840 y352 w100 h35, &兼容性设置

; 文字大小，粗细，字体
Gui Font, s8 Bold, Arial
; 文字颜色
Gui Font, c0xFF0000
Gui Add, Text, x160 y304 w651 h23 +0x200, 修改INI时将里面第一行等号后的数字改成【6】，然后保存，以启用自定义分辨率

Return

;变量具体代码
btnOriginal:

	WinMinimize									; 最小化窗口

		Runwait ".\ComeOn.exe"					; 运行程序（Run为直接运行，RunWait为运行后等待进程结束再进行下一步）

		SetTimer, RunMagpie, -1					; 设置计时器，跳转RunMagpie子进程，即时进行

		SetTimer, CheckComeOn, 5000				; 检测EXE程序运行状态并激活窗口

		SetTimer, MagpieScaling, 5000			; 设置计时器，跳转MagpieScaling子进程，5秒后执行

		Process, Exist, ComeOn.exe				; 检测是否存在ComeOn.exe进程

		if (ErrorLevel = 0)						; 如果ErrorLevel为0

		{
			Process, Close, Magpie.exe			; 结束Magpie.exe进程
		}

ExitApp											; 退出程序

RunMagpie:

	Run, ".\Magpie\Magpie.exe" -st,				; 最小化运行Magpie.exe；
												; 【-st】指令为Magpie作者提供的内部指令
												; 当前阶段Magpie存在BUG，需要设置工作目录才能缩放，已汇报给作者，等待解决

Return

CheckComeOn:

	if WinExist("ahk_exe ComeOn.exe")			; 如果检测到EXE程序正在运行
	   WinActivate, ComeOn.exe					; 则激活ComeOn.exe为活动窗口
/*
	DetectHiddenWindows, On
	   WinGetPos,,, Width, Height, ComeOn.exe
	   WinMove, ComeOn.exe,, (A_ScreenWidth/2)-(Width/2), (A_ScreenHeight/2)-(Height/2)
*/

Return

MagpieScaling:

	Send {ALT Down} 							; 按下A键
		Sleep, 20 								; 等待20毫秒
			Send F11 							; 按下F11键
	Send {ALT Up} 								; 抬起A键

Return

btn480P:

	WinMinimize
		RunWait ".\ComeOn480P.exe"

ExitApp

btn540P:

	WinMinimize
		RunWait ".\ComeOn540P.exe"

ExitApp

btn720P:

	WinMinimize
		RunWait ".\ComeOn720P.exe"

ExitApp

btn768P:

	WinMinimize
		RunWait ".\ComeOn768P.exe"

ExitApp

btn900P:

	WinMinimize
		RunWait ".\ComeOn900P.exe"

ExitApp

btn1080P:

	WinMinimize
		RunWait ".\ComeOn1080P.exe"

ExitApp

btnINI:

	WinMinimize
		RunWait ".\set.ini" 					; 打开ini文件
	WinRestore 									; 恢复启动器

Return

btnConfig:

	WinMinimize
		RunWait ".\cnc-ddraw config.exe"		; 运行cnc-ddraw设置程序
	WinRestore

Return


; 退出事件
GuiEscape:
GuiClose:

    ExitApp

/*

现在的思路是要检测到 DaojianServer 这个窗口后，
把它挪到屏幕中间，然后再进行缩放
现阶段需要解决强制将进入游戏后的 DaojianServer 这个窗口强制移动

*/