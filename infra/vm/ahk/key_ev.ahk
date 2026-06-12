#Requires AutoHotkey v2.0
#SingleInstance Force
; Event-mode key injection for EQ2 (deployed to C:\ib\key_ev.ahk, fired by the
; 'ibkey' scheduled task). Reads C:\ib\keys.txt: a comma-separated SEQUENCE of
; key specs, e.g. "F2,4" = target group member F2 then press 4. Modifier form is
; "Mod+Key" (Ctrl+1, Alt+=). A "pause_<seconds>" element waits (for cast times in
; pre-pull / buff combos). EQ2 only registers legacy Event-mode input.
SendMode "Event"
SetKeyDelay 30, 30
SetTitleMatchMode 2

toAhk(spec) {
    parts := StrSplit(Trim(spec, " `t`r`n"), "+")
    key := parts[parts.Length]
    mods := ""
    Loop parts.Length - 1 {
        m := StrLower(Trim(parts[A_Index]))
        mods .= (m = "ctrl") ? "^" : (m = "alt") ? "!" : (m = "shift") ? "+" : ""
    }
    if (StrLen(key) > 1)          ; named keys (F2, Space, ...) need braces
        key := "{" key "}"
    return mods key
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
    Send(toAhk(k))
    Sleep 110
}
FileAppend("ev-keys [" seq "] @" A_Now "`n", "C:\ib\key.logf")
