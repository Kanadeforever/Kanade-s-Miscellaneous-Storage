#SingleInstance Force   ; ���ű��Ѿ�����ʱ�Զ������ű���
                        ; #SingleInstance: Force - �Զ�����; Ignore - ������; Prompt - ѯ���Ƿ�������Ĭ��ѡ�; Off - ����ͬʱ���ж��ʵ��.
#NoEnv                  ; Ϊ�����ܺ���δ��AutoHotkey�汾�ļ����ԣ��Ƽ�ʹ�á�
#NoTrayIcon             ; ����ʾ����ͼ�ꡣ
; #Warn                 ; ���þ��棬��Э����ⳣ������


SetWorkingDir %A_ScriptDir%     ; ���ýű�����λ��Ϊ����Ŀ¼��
SetBatchLines -1                ; ���ýű������ٶȣ�
                                ; SetBatchLines: -1 - ȫ�����У�20ms - ÿ������ 20 ms ֮������ 10 ms��LineCount(��ֱ���������ֶ���ms��β) - ����ǰҪִ�нű�����������msģʽ���⡣

Gui -MaximizeBox +AlwaysOnTop -DPIScale

Gui Show, w900 h360, The Elden Scroll V Skyrim Launcher

Gui Font, s15 Bold, Arial
Gui Add, Text, x56 y66 w220 h35 ,  A
Gui Add, Text, x56 y101 w220 h35 , Simple
Gui Add, Text, x56 y136 w220 h35 , Skyrim
Gui Add, Text, x56 y171 w220 h35 , With
Gui Add, Text, x56 y206 w220 h35 , Mod Organizer
Gui Add, Text, x56 y241 w220 h35 , Launcher

Gui Font, s9 Bold, Arial
Gui Add, Button, gSKSE x336 y56 w200 h50, &Skyrim With MO2
Gui Add, Button, gSkyrim x336 y146 w200 h50, &Skyrim
Gui Add, Button, gMO2 x336 y236 w200 h50, &Mod Organizer 2
Gui Add, Button, gSLauncherMO2 x600 y56 w200 h50, &Skyrim Launcher With MO2
Gui Add, Button, gSLauncher x600 y146 w200 h50, &Skyrim Launcher
Gui Add, Button, gLOOT x600 y236 w200 h50, &LOOT

Return


SKSE:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:SKSE",
ExitApp

Skyrim:
  WinMinimize
    Runwait, ".\Skyrim.exe"
ExitApp

MO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe"
  WinRestore
Return

SLauncherMO2:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:Skyrim Launcher",
ExitApp


SLauncher:
  WinMinimize
    Runwait, ".\SkyrimLauncher_o.exe"
ExitApp

LOOT:
  WinMinimize
    Runwait, ".\Tools\MO2\ModOrganizer.exe" "moshortcut://:LOOT",
  WinRestore
Return

GuiEscape:
GuiClose:
  ExitApp
