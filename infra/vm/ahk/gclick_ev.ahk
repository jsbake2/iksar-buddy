#Requires AutoHotkey v2.0
; Guest-side click: read C:\ib\click.txt ("x y" screen coords), activate EQ2, click.
; Fired by the 'ibgclick' scheduled task. Used by Forge (char-select / craft-window
; clicks) and the healer accept helpers.
CoordMode "Mouse", "Screen"
SendMode "Event"
SetMouseDelay 40
SetTitleMatchMode 2
; Guard: if EQ2 isn't running, exit cleanly instead of throwing on WinActivate
; (AHK v2 errors on a missing target window — the "both got AHK errors when I
; pressed Start with the game closed" popup). key_ev.ahk already guards this way.
if !WinExist("EverQuest II") {
    FileAppend("gclick: no EQ2 window @" A_Now "`n", "C:\ib\pos.logf")
    ExitApp
}
WinActivate("EverQuest II")
Sleep 400
txt := Trim(FileRead("C:\ib\click.txt"), " `t`r`n")
p := StrSplit(txt, " ")
MouseMove(p[1]+0, p[2]+0)
Sleep 200
Click()
FileAppend("ev-click " txt " @" A_Now "`n", "C:\ib\pos.logf")
