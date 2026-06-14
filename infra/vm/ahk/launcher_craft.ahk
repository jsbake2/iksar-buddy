#Requires AutoHotkey v2.0
#SingleInstance Force
; Forge craft-mode launcher (FORGE.md §5.5). Same as the healer launcher.ahk up to
; char-select, then STOPS -- the HOST selects the character by OCR-and-click (each
; account holds 2 crafters, so no blind slot click) and there is NO group invite in
; craft mode. Deployed to the craft guest's C:\ib\launcher.ahk (fired by ibrun).
; Coords are for 1920x1080 fullscreen-windowed.

CoordMode "Mouse", "Screen"
CoordMode "Pixel", "Screen"
SendMode "Event"
SetMouseDelay 40
SetTitleMatchMode 2

EQDIR := "C:\Users\Public\Daybreak Game Company\Installed Games\EverQuest II"
LOGF := "C:\ib\launcher.log"
lg(m) {
    ; The HOST polls launcher.log while we write it -> a sharing collision used to
    ; throw a modal AHK error and halt the launcher. Retry briefly, then give up
    ; silently (a dropped log line must never stop the launcher).
    Loop 20 {
        try {
            FileAppend(m " @" A_Now "`n", LOGF)
            return
        } catch {
            Sleep 50
        }
    }
}
rgb(x, y) {
    c := PixelGetColor(x, y)
    return [(c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF]
}
isMaroon(x, y) {
    p := rgb(x, y)
    return (p[1] > 30 && p[1] < 95 && p[2] < 22 && p[3] < 22)
}
evclick(x, y, win := "") {
    if win
        WinActivate(win)
    Sleep 300
    MouseMove(x, y)
    Sleep 200
    Click()
}
gameWindow() {
    for w in WinGetList() {
        if InStr(WinGetTitle(w), "USER OPTIMIZED")
            return w
    }
    return 0
}

lg("=== craft launcher start ===")

Run('"' EQDIR '\LaunchPad.exe"', EQDIR)
if !WinWait("EverQuest II", , 90) {
    lg("FAIL: LaunchPad window never appeared")
    ExitApp
}
lg("LaunchPad up; letting it auto-login")
Sleep 12000

WinActivate("EverQuest II")
WinGetPos(&wx, &wy, &ww, &wh, "EverQuest II")
px := wx + Round(ww * 0.871), py := wy + Round(wh * 0.883)
lg("PLAY click @ " px "," py)
evclick(px, py)

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
Loop 150 {
    if isMaroon(1715, 895)
        break
    Sleep 2000
}
lg("char-select ready -- HOST selects character (craft mode: no auto-pick, no invite)")
