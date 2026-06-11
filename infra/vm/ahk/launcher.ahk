#Requires AutoHotkey v2.0
#SingleInstance Force
; Hands-off launcher (PROJECT.md §6.5): desktop -> LaunchPad -> PLAY ->
; select Jenskin -> char-select Play -> load world -> wait for group invite
; (indefinitely) -> accept.
;
; EQ2 only registers legacy mouse_event-style clicks -> SendMode "Event".
; Gating uses gold-button fingerprints (no blind sleeps where avoidable).
; Coords are for 1920x1080 fullscreen-windowed.

CoordMode "Mouse", "Screen"
CoordMode "Pixel", "Screen"
SendMode "Event"
SetMouseDelay 40
SetTitleMatchMode 2

EQDIR := "C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest II"
LOGF := "C:\ib\launcher.log"
lg(m) {
    FileAppend(m " @" A_Now "`n", LOGF)
}

rgb(x, y) {
    c := PixelGetColor(x, y)
    return [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF]
}
isGold(x, y) {           ; EQ2 gold/tan button face
    p := rgb(x, y)
    return (p[1] > 150 && p[1] - p[2] > 25 && p[2] - p[3] > 20)
}
isMaroon(x, y) {         ; EQ2 stock dark-red button face (e.g. char-select Play)
    p := rgb(x, y)
    return (p[1] > 30 && p[1] < 95 && p[2] < 22 && p[3] < 22)
}
isButton(x, y) {         ; a button face, either styling (gold or maroon)
    return isGold(x, y) || isMaroon(x, y)
}
evclick(x, y, win := "") {
    if win
        WinActivate(win)
    Sleep 300
    MouseMove(x, y)
    Sleep 200
    Click()                       ; press+release in place (no drag)
}
gameWindow() {
    for w in WinGetList() {
        if InStr(WinGetTitle(w), "USER OPTIMIZED")
            return w
    }
    return 0
}

lg("=== launcher start ===")

; 1) launch LaunchPad from desktop
Run('"' EQDIR '\LaunchPad.exe"', EQDIR)
if !WinWait("EverQuest II", , 90) {
    lg("FAIL: LaunchPad window never appeared")
    ExitApp
}
lg("LaunchPad up; letting it auto-login")
Sleep 12000                       ; auto-login -> PLAY ready

; 2) click PLAY (window-relative, robust to launcher position)
WinActivate("EverQuest II")
WinGetPos(&wx, &wy, &ww, &wh, "EverQuest II")
px := wx + Round(ww * 0.871), py := wy + Round(wh * 0.883)
lg("PLAY click @ " px "," py)
evclick(px, py)

; 3) wait for the game window, then for char-select (Play button turns gold)
gw := 0
Loop 90 {
    gw := gameWindow()
    if gw
        break
    Sleep 2000
}
if !gw {
    lg("FAIL: game window never appeared")
    ExitApp
}
lg("game window up; waiting for char-select")
Loop 150 {                        ; up to ~5 min for software-render load
    if isMaroon(1715, 895)        ; char-select Play button (dark-red face)
        break
    Sleep 2000
}
lg("char-select ready")

; 4) select Jenskin (fixed slot) then click Play
evclick(100, 884, gw)             ; Jenskin in the list
Sleep 600
evclick(1715, 890, gw)            ; Play
lg("entered world as Jenskin; loading")

Sleep 50000                       ; world load (software render)
lg("in-world; waiting for group invite (indefinite)")

; 5) wait for the invite dialog (both Accept+Decline gold), accept it
Loop {
    a := rgb(820, 595), d := rgb(1000, 595)
    if (isButton(820, 595) && isButton(1000, 595)) {
        lg("invite detected A=" a[1] "," a[2] "," a[3] " D=" d[1] "," d[2] "," d[3])
        evclick(820, 595, gw)     ; Accept
        Sleep 2500
        if !(isButton(820, 595) && isButton(1000, 595)) {
            lg("invite accepted")
            break
        }
        lg("dialog still present; retrying")
    } else {
        ; heartbeat every ~30s so we can see it's alive + calibrate colors
        if (Mod(A_TickCount // 1000, 30) = 0)
            lg("waiting... A=" a[1] "," a[2] "," a[3] " D=" d[1] "," d[2] "," d[3])
    }
    Sleep 1000
}
lg("=== launcher done: in group ===")
