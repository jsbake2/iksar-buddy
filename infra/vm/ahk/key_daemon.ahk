#Requires AutoHotkey v2.0
; Persistent key-inject daemon for EQ2 (C:\ib\key_daemon.ahk, run once by the 'ibkeyd'
; scheduled task in the INTERACTIVE session so its input reaches the game). Watches
; C:\ib\keycmd.txt for a new "<token>|<seq>" line and injects immediately — no per-press
; process spawn or Task Scheduler hop. The agent gates chat-safety BEFORE writing the
; file, so the daemon is a dumb, FAST executor. Heartbeat file lets the host detect a
; dead daemon and restart it.
#SingleInstance Ignore
SendMode "Event"
; FAST timing: 12ms key-hold (enough for EQ2 Event mode to register) + 6ms gap. The old
; 40/40 held+waited 80ms PER key event, so a 4-key modifier clear alone cost ~320ms —
; that was the whole latency problem, not the trigger. Modified keys still get an explicit
; Sleep 90 around the held modifier (EQ2 drops it if the key lands too soon).
SetKeyDelay 6, 12
SetTitleMatchMode 2

CMD := "C:\ib\keycmd.txt"
HB  := "C:\ib\keydaemon.hb"
lastToken := ""
beat := 0

OnExit(ReleaseMods)
ReleaseMods(*) {
    Send("{Alt up}{Ctrl up}{Shift up}{LWin up}")
}

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
    keySpec := (StrLen(key) > 1) ? "{" key "}" : key
    for m in mods
        SendEvent("{" m " down}")
    if (mods.Length)
        Sleep 90                     ; EQ2 drops a modifier if the key lands too soon
    SendEvent(keySpec)
    if (mods.Length)
        Sleep 90
    for m in mods
        SendEvent("{" m " up}")
}

runSeq(seq) {
    if !WinActive("EverQuest II") {          ; agent only writes when game is foreground,
        if !WinExist("EverQuest II")         ; so this is usually already true (no settle cost)
            return
        WinActivate("EverQuest II")
        Sleep 20
    }
    Send("{Alt up}{Ctrl up}{Shift up}{LWin up}")   ; clear a stray/stuck modifier (fast now)
    parts := StrSplit(seq, ",")
    for i, part in parts {
        k := Trim(part, " `t`r`n")
        if (k = "" || k = "none")
            continue
        if (SubStr(k, 1, 6) = "pause_") {           ; "pause_2.5" -> wait (cast time)
            Sleep(Round(Number(SubStr(k, 7)) * 1000))
            continue
        }
        if (SubStr(k, 1, 5) = "hold_") {            ; "hold_w_0.3" -> hold w for 0.3s
            p := StrSplit(SubStr(k, 6), "_")
            hk := p[1]
            dur := p.Length > 1 ? Number(p[2]) : 0.3
            Send("{" hk " down}")
            Sleep(Round(dur * 1000))
            Send("{" hk " up}")
            continue
        }
        sendKey(k)
        if (i < parts.Length)
            Sleep 15                              ; small gap so a target F-key registers
    }                                             ; before the ability key in a sequence
    try FileAppend("d [" seq "] @" A_Now "`n", "C:\ib\key.logf")
}

readAll(path) {
    s := ""
    try s := Trim(FileRead(path), " `t`r`n")
    return s
}

Loop {
    Sleep 12
    if (A_TickCount - beat > 1000) {              ; heartbeat ~1/s for host health-check
        beat := A_TickCount
        try FileAppend("", HB)
        try FileSetTime(, HB)
    }
    line := readAll(CMD)
    if (line = "")
        continue
    bar := InStr(line, "|")
    if (!bar)
        continue
    token := SubStr(line, 1, bar - 1)
    if (token = lastToken)
        continue
    Sleep 3                                        ; confirm a stable read (no partial write)
    if (readAll(CMD) != line)
        continue
    lastToken := token
    seq := SubStr(line, bar + 1)
    if (seq != "")
        runSeq(seq)
}
