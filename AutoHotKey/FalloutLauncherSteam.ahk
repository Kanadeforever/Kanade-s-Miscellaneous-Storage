#SingleInstance Force   ; ���ű��Ѿ�����ʱ�Զ������ű���
                        ; #SingleInstance: Force - �Զ�����; Ignore - ������; Prompt - ѯ���Ƿ�������Ĭ��ѡ�; Off - ����ͬʱ���ж��ʵ��.
#NoEnv                  ; Ϊ�����ܺ���δ��AutoHotkey�汾�ļ����ԣ��Ƽ�ʹ�á�
#NoTrayIcon             ; ����ʾ����ͼ�ꡣ
; #Warn                 ; ���þ��棬��Э����ⳣ������


SetWorkingDir %A_ScriptDir%     ; ���ýű�����λ��Ϊ����Ŀ¼��
SetBatchLines -1                ; ���ýű������ٶȣ�
                                ; SetBatchLines: -1 - ȫ�����У�20ms - ÿ������ 20 ms ֮������ 10 ms��LineCount(��ֱ���������ֶ���ms��β) - ����ǰҪִ�нű�����������msģʽ���⡣

Gui -MaximizeBox +AlwaysOnTop -DPIScale

Gui Show, w900 h360, Fallout 3 Launcher

Gui Font, s15 Bold, Arial
Gui Add, Text, x56 y66 w220 h35 ,  A
Gui Add, Text, x56 y101 w220 h35 , Simple
Gui Add, Text, x56 y136 w220 h35 , Fallout 3
Gui Add, Text, x56 y171 w220 h35 , With
Gui Add, Text, x56 y206 w220 h35 , Mod Organizer
Gui Add, Text, x56 y241 w220 h35 , Launcher

Gui Font, s9 Bold, Arial
Gui Add, Button, gFOSE x336 y56 w200 h50, &Fallout 3 With MO2
Gui Add, Button, gFallout3 x336 y146 w200 h50, &Fallout 3
Gui Add, Button, gMO2 x336 y236 w200 h50, &Mod Organizer 2
Gui Add, Button, gFLauncherMO2 x600 y56 w200 h50, &Fallout 3 Launcher With MO2
Gui Add, Button, gFLauncher x600 y146 w200 h50, &Fallout 3 Launcher
Gui Add, Button, gLOOT x600 y236 w200 h50, &LOOT

Return


FOSE:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:FOSE",
ExitApp

Fallout3:
  WinMinimize
    Runwait, ".\Fallout3.exe"
ExitApp

MO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe"
  WinRestore
Return

FLauncherMO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Fallout Launcher",
ExitApp


FLauncher:
  WinMinimize
    Runwait, ".\OblivionLauncher.exe"
ExitApp

LOOT:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:LOOT",
  WinRestore
Return

GuiEscape:
GuiClose:
  ExitApp
