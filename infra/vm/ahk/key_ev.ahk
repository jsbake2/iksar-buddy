#Requires AutoHotkey v2.0
; Ignore (not Force): if an injection is already running, a new one exits instead
; of KILLING the running one mid-combo. Force aborted in-flight presses whenever
; the auto-loop and a manual button (or rapid mashing) overlapped, so neither
; landed -- e.g. mashed Ctrl+7 never completed. The agent also time-locks injects
; so they don't overlap in the first place; this is the safety net.
#SingleInstance Ignore
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
    ; Release any modifier we DON'T want for this key, right before pressing it.
    ; A stuck Alt (e.g. latched in the guest by the SPICE console swallowing an
    ; Alt-up) would otherwise turn a bare "1" into "Alt+1". Only needed when we're
    ; pressing a MODIFIED key (to drop the other mods) -- a bare key was already
    ; fully cleared by the pre-sequence clear below, so skip this ~160ms loop for it.
    if (mods.Length) {
        for mm in ["Alt", "Ctrl", "Shift", "LWin"] {
            keep := false
            for m in mods
                if (m = mm)
                    keep := true
            if (!keep)
                SendEvent("{" mm " up}")
        }
    }
    ; named keys (F2, Space) need braces; single chars (incl. "=", "+") are sent
    ; as-is -- the "{= down}" key-event form is invalid and would crash AHK.
    keySpec := (StrLen(key) > 1) ? "{" key "}" : key
    ; Hold the wanted modifiers explicitly with a settle gap (EQ2 Event mode drops
    ; a modifier if the key arrives too soon -- the "Ctrl+7 fired as just 7" bug),
    ; tap the key, then release. SendEvent = Event mode regardless of SendMode.
    for m in mods
        SendEvent("{" m " down}")
    if (mods.Length)
        Sleep 90
    SendEvent(keySpec)
    if (mods.Length)
        Sleep 90
    for m in mods
        SendEvent("{" m " up}")
}

if !WinExist("EverQuest II")
    ExitApp
WinActivate("EverQuest II")
Sleep 40   ; the agent only injects when the game is already foreground (chat guard),
           ; so this is just a tiny settle, not a wait for the window to come up
; Clear any modifier stranded by a prior instance that #SingleInstance Force
; killed mid-combo (between {Mod down} and {Mod up}). Without this, an interrupted
; Alt+= / Ctrl+N injection leaves Alt/Ctrl "held" in the guest -- which shows up
; as stuck-Alt in the SPICE console and breaks in-game input until cleared.
Send("{Alt up}{Ctrl up}{Shift up}{LWin up}")
Sleep 30   ; let the clear settle so its {Ctrl up} can't bleed into a Ctrl+ combo
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
    Sleep 30
}
FileAppend("ev-keys [" seq "] @" A_Now "`n", "C:\ib\key.logf")
