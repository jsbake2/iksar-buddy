#Requires AutoHotkey v2.0
#SingleInstance Force
; Event-mode key injection for EQ2 (deployed to C:\ib\key_ev.ahk, fired by the
; 'ibkey' scheduled task). Reads C:\ib\keys.txt: a comma-separated SEQUENCE of
; key specs, e.g. "F2,4" = target group member F2 then press 4. Modifier form is
; "Mod+Key" (Ctrl+1, Alt+=). A "pause_<seconds>" element waits (for cast times in
; pre-pull / buff combos). EQ2 only registers legacy Event-mode input.
SendMode "Event"
SetKeyDelay 40, 40
SetTitleMatchMode 2

; Send one spec ("Alt+7", "Ctrl+1", "F2", "Space", "4"). Modifiers are held
; EXPLICITLY with pauses -- the compact Send("!7") releases too fast for EQ2 to
; register the combo in Event mode, which is why modified keys weren't firing.
sendKey(spec) {
    parts := StrSplit(Trim(spec, " `t`r`n"), "+")
    key := parts[parts.Length]
    mods := []
    Loop parts.Length - 1 {
        m := StrLower(Trim(parts[A_Index]))
        if (m = "ctrl")
            mods.Push("Ctrl")
        else if (m = "alt")
            mods.Push("Alt")
        else if (m = "shift")
            mods.Push("Shift")
    }
    if (StrLen(key) > 1)          ; named keys (F2, Space, ...) need braces
        key := "{" key "}"
    for m in mods
        Send("{" m " down}")
    if (mods.Length)
        Sleep 60
    Send(key)
    if (mods.Length)
        Sleep 60
    for m in mods
        Send("{" m " up}")
}

if !WinExist("EverQuest II")
    ExitApp
WinActivate("EverQuest II")
Sleep 250
seq := Trim(FileRead("C:\ib\keys.txt"), " `t`r`n")
for part in StrSplit(seq, ",") {
    k := Trim(part, " `t`r`n")
    if (k = "" || k = "none")
        continue
    if (SubStr(k, 1, 6) = "pause_") {        ; "pause_2.5" -> wait 2.5s (cast time)
        Sleep(Round(Number(SubStr(k, 7)) * 1000))
        continue
    }
    if (SubStr(k, 1, 5) = "hold_") {         ; "hold_w_0.3" -> hold w for 0.3s (WASD nudge)
        p := StrSplit(SubStr(k, 6), "_")
        hk := p[1]
        dur := p.Length > 1 ? Number(p[2]) : 0.3
        Send("{" hk " down}")
        Sleep(Round(dur * 1000))
        Send("{" hk " up}")
        continue
    }
    sendKey(k)
    Sleep 110
}
FileAppend("ev-keys [" seq "] @" A_Now "`n", "C:\ib\key.logf")
