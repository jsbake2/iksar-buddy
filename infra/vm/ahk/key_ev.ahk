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

; Belt-and-suspenders: on any clean exit, release modifiers so we never leave one
; held in the guest (pairs with the start-of-run clear below for the kill case).
OnExit(ReleaseMods)
ReleaseMods(*) {
    Send("{Alt up}{Ctrl up}{Shift up}{LWin up}")
}

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
    ; Press with explicit key down/up and deliberate gaps. EQ2 (Event mode)
    ; intermittently drops a modifier if the key arrives too soon after the
    ; modifier-down (the "Ctrl+7 fired as just 7" bug), so we hold the modifier,
    ; let it settle, tap the key, then release -- using SendEvent so this is
    ; independent of SendMode. Works for single ("7") and named ("F2") keys alike.
    for m in mods
        SendEvent("{" m " down}")
    if (mods.Length)
        Sleep 90
    SendEvent("{" key " down}")
    Sleep 50
    SendEvent("{" key " up}")
    if (mods.Length)
        Sleep 90
    for m in mods
        SendEvent("{" m " up}")
}

if !WinExist("EverQuest II")
    ExitApp
WinActivate("EverQuest II")
Sleep 250
; Clear any modifier stranded by a prior instance that #SingleInstance Force
; killed mid-combo (between {Mod down} and {Mod up}). Without this, an interrupted
; Alt+= / Ctrl+N injection leaves Alt/Ctrl "held" in the guest -- which shows up
; as stuck-Alt in the SPICE console and breaks in-game input until cleared.
Send("{Alt up}{Ctrl up}{Shift up}{LWin up}")
Sleep 80   ; let the clear settle so its {Ctrl up} can't bleed into a Ctrl+ combo
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
