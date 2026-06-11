#Requires AutoHotkey v2.0
#SingleInstance Force
; Click "I ACCEPT" on the EQ2 LaunchPad EULA via native SendInput.
FileAppend("start " A_Now " session`n", "C:\ib\ahk.log")
CoordMode("Mouse", "Screen")
SetTitleMatchMode(2)
try {
    if WinExist("EverQuest") {
        WinActivate("EverQuest")
        WinWaitActive("EverQuest", , 2)
    }
}
title := WinActive("A") ? WinGetTitle("A") : "<none>"
FileAppend("active=" title "`n", "C:\ib\ahk.log")
Sleep(400)
MouseMove(620, 590)
Sleep(150)
Click()
FileAppend("clicked 620,590`n", "C:\ib\ahk.log")
