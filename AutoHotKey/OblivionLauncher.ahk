#SingleInstance Force   ; ���ű��Ѿ�����ʱ�Զ������ű���
                        ; #SingleInstance: Force - �Զ�����; Ignore - ������; Prompt - ѯ���Ƿ�������Ĭ��ѡ�; Off - ����ͬʱ���ж��ʵ��.
#NoEnv                  ; Ϊ�����ܺ���δ��AutoHotkey�汾�ļ����ԣ��Ƽ�ʹ�á�
#NoTrayIcon             ; ����ʾ����ͼ�ꡣ
; #Warn                 ; ���þ��棬��Э����ⳣ������


SetWorkingDir %A_ScriptDir%     ; ���ýű�����λ��Ϊ����Ŀ¼��
SetBatchLines -1                ; ���ýű������ٶȣ�
                                ; SetBatchLines: -1 - ȫ�����У�20ms - ÿ������ 20 ms ֮������ 10 ms��LineCount(��ֱ���������ֶ���ms��β) - ����ǰҪִ�нű�����������msģʽ���⡣

Gui -MaximizeBox +AlwaysOnTop -DPIScale

Gui Show, w900 h360, The Elden Scroll IV Oblivion Launcher

Gui Font, s15 Bold, Arial
Gui Add, Text, x56 y66 w220 h35 ,  A
Gui Add, Text, x56 y101 w220 h35 , Simple
Gui Add, Text, x56 y136 w220 h35 , Oblivion
Gui Add, Text, x56 y171 w220 h35 , With
Gui Add, Text, x56 y206 w220 h35 , Mod Organizer
Gui Add, Text, x56 y241 w220 h35 , Launcher

Gui Font, s9 Bold, Arial
Gui Add, Button, gOblivionMO2 x336 y56 w200 h50, &Oblivion With MO2
Gui Add, Button, gOblivion x336 y146 w200 h50, &Oblivion
Gui Add, Button, gMO2 x336 y236 w200 h50, &Mod Organizer 2
Gui Add, Button, gOLauncherMO2 x600 y56 w200 h50, &Oblivion Launcher With MO2
Gui Add, Button, gOLauncher x600 y146 w200 h50, &Oblivion Launcher
Gui Add, Button, gOMM x600 y236 w200 h50, &Oblivion Mod Manager With MO2

Return


OblivionMO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Oblivion",
ExitApp

Oblivion:
  WinMinimize
    Runwait, ".\Oblivion.exe"
ExitApp

MO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe"
  WinRestore
Return

OLauncherMO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Oblivion Launcher",
ExitApp


OLauncher:
  WinMinimize
    Runwait, ".\OblivionLauncher.exe"
ExitApp

OMM:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Oblivion Mod Manager",
  WinRestore
Return

GuiEscape:
GuiClose:
  ExitApp
