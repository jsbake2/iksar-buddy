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

; 4) select Jenskin (fixed slot) then click Play
evclick(100, 884, gw)             ; Jenskin in the list
Sleep 600
evclick(1715, 890, gw)            ; Play
lg("entered world as Jenskin; loading")

Sleep 50000                       ; world load (software render)
lg("in-world; host OCR (invite_accept.py) handles the group invite")
lg("=== launcher done: in-world ===")

; NOTE: the group invite is accepted HOST-SIDE by invite_accept.py, which OCRs
; the screen, gates on "invited ... group", and clicks the located "Accept"
; word box (same self-locating approach as quest_accept.py). The guest has no
; tesseract, so blind pixel-sampling here was fragile and is intentionally gone.
