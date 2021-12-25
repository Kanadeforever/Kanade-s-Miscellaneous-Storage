#NoEnv                              ; Recommended for performance and compatibility with future AutoHotkey releases.
; #Warn                             ; Enable warnings to assist with detecting common errors.
SendMode Input                      ; Recommended for new scripts due to its superior speed and reliability.
SetWorkingDir %A_ScriptDir%         ; Ensures a consistent starting directory.

Q::
    Send, {Ctrl down}{c down}
        Sleep, 5
    Send, {c up}{Ctrl up}
Return

W::
    Send, {Ctrl down}{x down}
        Sleep, 5
    Send, {x up}{Ctrl up}
Return

E::
    Send, {Ctrl down}{a down}
        Sleep, 5
    Send, {a up}{Ctrl up}
    Send, {Ctrl down}{v down}
        Sleep, 5
    Send, {v up}{Ctrl up}
        Sleep, 5
    Send, {Enter}
Return
