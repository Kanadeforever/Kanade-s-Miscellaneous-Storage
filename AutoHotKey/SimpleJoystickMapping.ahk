#NoEnv                                          ; 为了性能和与未来AutoHotkey版本的兼容性，推荐使用。
#Persistent                                     ; 让脚本保持运行, 直至用户明确退出.
; #Warn                                         ; 启用警告，以协助检测常见错误。
SendMode Input                                  ; 由于其卓越的速度和可靠性，推荐给新的脚本。
SetWorkingDir %A_ScriptDir%                     ; 确保一个一致的起始目录。
SetTimer, WatchJoystick, 5
return

/*
一个简单的手柄映射脚本
来自于：https://www.gog.com/forum/yookalaylee/make_your_joystick_work_with_autohotkey
感谢原作者的付出
我仅仅是在原作者的基础上，针对XBOX手柄进行优化和增加了扳机与右摇杆的适配
现在使用XBOX手柄应该会很舒服
*/


WatchJoystick:

; 左右摇杆和LT & RT
JoyX := GetKeyState("JoyX")                     ; 获取左摇杆X轴的位置
JoyY := GetKeyState("JoyY")                     ; 获取左摇杆Y轴的位置
JoyU := GetKeyState("JoyU")                     ; 获取右摇杆X轴的位置
JoyR := GetKeyState("JoyR")                     ; 获取右摇杆Y轴的位置
JoyZ := GetKeyState("JoyZ")                     ; 获取左右扳机的位置
KeyXToHoldDownPrev := KeyXToHoldDown            ; Prev现在按住之前按下的键（如果有）
KeyYToHoldDownPrev := KeyYToHoldDown            ; Prev现在按住之前按下的键（如果有）
KeyUToHoldDownPrev := KeyUToHoldDown            ; Prev现在按住之前按下的键（如果有）
KeyRToHoldDownPrev := KeyRToHoldDown            ; Prev现在按住之前按下的键（如果有）
KeyZToHoldDownPrev := KeyZToHoldDown            ; Prev现在按住之前按下的键（如果有）

; 方向键
JoyPOV := GetKeyState("JoyPOV")                 ; 获取POV控制的位置
KeyPOVToHoldDownPrev := KeyPOVToHoldDown        ; Prev现在按住之前按下的键（如果有）

; A B X Y & LB RB & START & BACK
KJoy1 := GetKeyState("Joy1")
KJoy2 := GetKeyState("Joy2")
KJoy3 := GetKeyState("Joy3")
KJoy4 := GetKeyState("Joy4")
KJoy5 := GetKeyState("Joy5")
KJoy6 := GetKeyState("Joy6")
KJoy7 := GetKeyState("Joy7")
KJoy8 := GetKeyState("Joy8")
KJoy9 := GetKeyState("Joy9")
KJoy10 := GetKeyState("Joy10")
Key1ToHoldDownPrev := Key1ToHoldDown
Key2ToHoldDownPrev := Key2ToHoldDown
Key3ToHoldDownPrev := Key3ToHoldDown
Key4ToHoldDownPrev := Key4ToHoldDown
Key5ToHoldDownPrev := Key5ToHoldDown
Key6ToHoldDownPrev := Key6ToHoldDown
Key7ToHoldDownPrev := Key7ToHoldDown
Key8ToHoldDownPrev := Key8ToHoldDown
Key9ToHoldDownPrev := Key9ToHoldDown
Key10ToHoldDownPrev := Key10ToHoldDown


; 左摇杆
if  (JoyY < 30)
KeyYToHoldDown := "W"                           ; 定义左摇杆向上
else if(JoyY > 70)
KeyYToHoldDown := "S"                           ; 定义左摇杆向下
else
KeyYToHoldDown := ""

if (JoyX < 30)
KeyXToHoldDown := "A"                           ; 定义左摇杆向左
else if (JoyX > 70)
KeyXToHoldDown := "D"                           ; 定义左摇杆向右
else
KeyXToHoldDown = ""

if (KeyYToHoldDown = KeyYToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyYToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%KeyYToHoldDownPrev% up}                 ; 释放它
if KeyYToHoldDown                               ; 有一个键可以按下去
Send, {%KeyYToHoldDown% down}                   ; 按下它
}

if (KeyXToHoldDown = KeyXToHoldDownPrev)        ; 正确的键已经按下（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyXToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%KeyXToHoldDownPrev% up}                 ; 释放它
if KeyXToHoldDown                               ; 有一个键可以按下去
Send, {%KeyXToHoldDown% down}                   ; 按下它
}


; 右摇杆
if (JoyR < 30)
KeyRToHoldDown := "W"                           ; 定义右摇杆向上
else if (JoyR > 70)
KeyRToHoldDown := "S"                           ; 定义右摇杆向下
else
KeyRToHoldDown := ""

if (JoyU < 30)
KeyUToHoldDown := "A"                           ; 定义右摇杆向左
else if (JoyU > 70)
KeyUToHoldDown := "D"                           ; 定义右摇杆向右
else
KeyUToHoldDown = ""

if (KeyRToHoldDown = KeyRToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyRToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%KeyRToHoldDownPrev% up}                 ; 释放它
if KeyRToHoldDown                               ; 有一个键可以按下去
Send, {%KeyRToHoldDown% down}                   ; 按下它
}

if (KeyUToHoldDown = KeyUToHoldDownPrev)        ; 正确的键已经按下（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyUToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%KeyUToHoldDownPrev% up}                 ; 释放它
if KeyUToHoldDown                               ; 有一个键可以按下去
Send, {%KeyUToHoldDown% down}                   ; 按下它
}


; 方向键
; 有些操纵杆可能有一个平滑/连续的POV，而不是一个固定增量的POV。
; 为了支持他们所有人，使用一个范围：
if (JoyPOV < 0) ; 没有角度可以报告
KeyPOVToHoldDown := ""
else if (JoyPOV > 31500)                        ; 315° ~ 360°：向上
KeyPOVToHoldDown := "U"                         ; 定义上键
else if JoyPOV between 0 and 4500               ; 0° ~ 45°：向上
KeyPOVToHoldDown := "U"                         ; 定义上键
else if JoyPOV between 4501 and 13500           ; 45° ~ 135°：向右
KeyPOVToHoldDown := "R"                         ; 定义右键
else if JoyPOV between 13501 and 22500          ; 135° ~ 225°：向下
KeyPOVToHoldDown := "D"                         ; 定义下键
else                                            ; 225° ~ 315°：向左
KeyPOVToHoldDown := "L"                         ; 定义左键

if (KeyPOVToHoldDown = KeyPOVToHoldDownPrev)    ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyPOVToHoldDownPrev                         ; 有一个以前的键可以释放
Send, {%KeyPOVToHoldDownPrev% up}               ; 释放它
if KeyPOVToHoldDown                             ; 有一个键可以按下去
Send, {%KeyPOVToHoldDown% down}                 ; 按下它
}


; 左右扳机
if (JoyZ > 70)
KeyZToHoldDown := "L"                           ; 定义LT键
else if (JoyZ < 30)
KeyZToHoldDown := "R"                           ; 定义RT键
else
KeyZToHoldDown = ""

if (KeyZToHoldDown = KeyZToHoldDownPrev)        ; 正确的键已经按下（或者不需要键）
{
                                                ; 什么都不做
}
else                                        	; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if KeyZToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%KeyZToHoldDownPrev% up}                 ; 释放它
if KeyXToHoldDown                               ; 有一个键可以按下去
Send, {%KeyZToHoldDown% down}                   ; 按下它
}


; Joy1
if (KJoy1 > 0)
Key1ToHoldDown := "1"                           ; 定义A键
else
Key1ToHoldDown := ""

if (Key1ToHoldDown = Key1ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key1ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key1ToHoldDownPrev% up}                 ; 释放它
if Key1ToHoldDown                               ; 有一个键可以按下去
Send, {%Key1ToHoldDown% down}                   ; 按下它
}


; Joy2
if (KJoy2 > 0)
Key2ToHoldDown := "2"                           ; 定义B键
else
Key2ToHoldDown := ""

if (Key2ToHoldDown = Key2ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key2ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key2ToHoldDownPrev% up}                 ; 释放它
if Key2ToHoldDown                               ; 有一个键可以按下去
Send, {%Key2ToHoldDown% down}                   ; 按下它
}


; Joy3
if (KJoy3 > 0)
Key3ToHoldDown := "3"                           ; 定义X键
else
Key3ToHoldDown := ""

if (Key3ToHoldDown = Key3ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key3ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key3ToHoldDownPrev% up}                 ; 释放它
if Key3ToHoldDown                               ; 有一个键可以按下去
Send, {%Key3ToHoldDown% down}                   ; 按下它
}


; Joy4
if (KJoy4 > 0)
Key4ToHoldDown := "4"                           ; 定义Y键
else
Key4ToHoldDown := ""

if (Key4ToHoldDown = Key4ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key4ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key4ToHoldDownPrev% up}                 ; 释放它
if Key4ToHoldDown                               ; 有一个键可以按下去
Send, {%Key4ToHoldDown% down}                   ; 按下它
}


; Joy5
if (KJoy5 > 0)
Key5ToHoldDown := "5"                           ; 定义LB键
else
Key5ToHoldDown := ""

if (Key5ToHoldDown = Key5ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key5ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key5ToHoldDownPrev% up}                 ; 释放它
if Key5ToHoldDown                               ; 有一个键可以按下去
Send, {%Key5ToHoldDown% down}                   ; 按下它
}


; Joy6
if (KJoy6 > 0)
Key6ToHoldDown := "6"                           ; 定义RB键
else
Key6ToHoldDown := ""

if (Key6ToHoldDown = Key6ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key6ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key6ToHoldDownPrev% up}                 ; 释放它
if Key6ToHoldDown                               ; 有一个键可以按下去
Send, {%Key6ToHoldDown% down}                   ; 按下它
}


; Joy7
if (KJoy7 > 0)
Key7ToHoldDown := "7"                           ; 定义BACK/View键
else
Key7ToHoldDown := ""

if (Key7ToHoldDown = Key7ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key7ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key7ToHoldDownPrev% up}                 ; 释放它
if Key7ToHoldDown                               ; 有一个键可以按下去
Send, {%Key7ToHoldDown% down}                   ; 按下它
}


; Joy8
if (KJoy8 > 0)
Key8ToHoldDown := "8"                           ; 定义START/Menu键
else
Key8ToHoldDown := ""

if (Key8ToHoldDown = Key8ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key8ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key8ToHoldDownPrev% up}                 ; 释放它
if Key8ToHoldDown                               ; 有一个键可以按下去
Send, {%Key8ToHoldDown% down}                   ; 按下它
}


; Joy9
if (KJoy9 > 0)
Key9ToHoldDown := "9"                           ; 定义LS键
else
Key9ToHoldDown := ""

if (Key9ToHoldDown = Key9ToHoldDownPrev)        ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key9ToHoldDownPrev                           ; 有一个以前的键可以释放
Send, {%Key9ToHoldDownPrev% up}                 ; 释放它
if Key9ToHoldDown                               ; 有一个键可以按下去
Send, {%Key9ToHoldDown% down}                   ; 按下它
}

; Joy10
if (KJoy10 > 0)
Key10ToHoldDown := "Z"                          ; 定义RS键
else
Key10ToHoldDown := ""

if (Key10ToHoldDown = Key10ToHoldDownPrev)      ; 正确的键已经按下了（或者不需要键）
{
                                                ; 什么都不做
}
else                                            ; 否则，松开前一个键并按下新的键
{
SetKeyDelay -1                                  ; 避免击键间的延迟
if Key10ToHoldDownPrev                          ; 有一个以前的键可以释放
Send, {%Key10ToHoldDownPrev% up}                ; 释放它
if Key10ToHoldDown                              ; 有一个键可以按下去
Send, {%Key10ToHoldDown% down}                  ; 按下它
}


return