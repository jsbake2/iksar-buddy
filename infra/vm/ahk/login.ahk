#Requires AutoHotkey v2.0
#SingleInstance Force
; LaunchPad account login. Keyboard-driven (username autofocused -> Tab -> pass).
; SendText sends literal text so '!' in the password is not read as the Alt mod.
; Creds are passed in via env (IB_USER/IB_PASS) so they stay out of the repo.
SetTitleMatchMode 2
logf := "C:\ib\launch.logf"
FileAppend("login " A_Now "`n", logf)
if !WinWait("EverQuest", , 12) {
    FileAppend("FAIL: no LaunchPad window`n", logf)
    ExitApp
}
WinActivate("EverQuest")
WinWaitActive("EverQuest", , 3)
Sleep 1000
user := EnvGet("IB_USER")
pass := EnvGet("IB_PASS")
SendText(user)
Sleep 400
Send("{Tab}")
Sleep 400
SendText(pass)
Sleep 400
Send("{Enter}")
FileAppend("submitted login for " user "`n", logf)
