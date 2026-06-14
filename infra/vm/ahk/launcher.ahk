#Requires AutoHotkey v2.0
#SingleInstance Force
; Hands-off launcher (PROJECT.md §6.5): desktop -> LaunchPad -> PLAY ->
; char-select, then STOP. The HOST (brain) OCR-picks the ACTIVE PROFILE's character
; and clicks Play, so Launch respects the selected profile (Jenskin/Croolst/...)
; instead of a hardcoded list slot. The group invite is accepted host-side via the
; dashboard 'accept invite' button (invite_accept.py).
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
isMaroon(x, y) {         ; EQ2 stock dark-red button face (e.g. char-select Play)
    p := rgb(x, y)
    return (p[1] > 30 && p[1] < 95 && p[2] < 22 && p[3] < 22)
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
; STOP HERE. The host OCRs the char list, finds the active profile's character,
; clicks its row + Play (brain.charswitch.select_only via forge.sensors). No blind
; slot-click here — that loaded the wrong toon when the list order changed.
lg("=== launcher done: char-select (host picks profile character) ===")
