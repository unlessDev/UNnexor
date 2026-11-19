; =====================================================================
; Extdator — hover_scroll_helper.ahk  (OPCIONAL, AutoHotkey v2)
;
; El puente ya incorpora su propio hook de rueda de bajo nivel (WH_MOUSE_LL),
; por lo que este script normalmente NO es necesario. Déjalo activo solo si:
;   * desactivaste el ajuste de Windows "Desplazar ventanas inactivas al
;     pasar el ratón por encima de ellas", y
;   * arrancaste el puente con HOVER_SCROLL_HOOK=0.
;
; Función: cuando el cursor está sobre la ventana "Extdator", la rueda se
; reenvía a esa ventana sin activarla; el navegador conserva el foco.
;
; Requiere AutoHotkey v2:  https://www.autohotkey.com/
; =====================================================================
#Requires AutoHotkey v2.0
#SingleInstance Force
SetTitleMatchMode 2

global PANEL_TITLE := "Extdator"
global WM_MOUSEWHEEL := 0x20A

MouseIsOverExtdator(&hwnd) {
    hwnd := 0
    try {
        MouseGetPos ,, &win, , 2          ; coordenadas absolutas → HWND real
        hwnd := win
    } catch {
        return false
    }
    if !hwnd
        return false
    return WinExist(PANEL_TITLE " ahk_id " hwnd) ? true : false
}

#HotIf MouseIsOverExtdator(&hwnd)
WheelUp::   SendWheelToPanel(hwnd, 120)
WheelDown:: SendWheelToPanel(hwnd, -120)
#HotIf

SendWheelToPanel(hwnd, delta) {
    ; Empaqueta las coordenadas del cursor en lParam (MAKELPARAM).
    pt := 0
    DllCall("GetCursorPos", "int64*", &pt, "ptr")
    x := pt & 0xFFFF
    y := (pt >> 16) & 0xFFFF
    lparam := (y << 16) | x
    ; wParam de WM_MOUSEWHEEL: delta en la palabra alta.
    wparam := (delta << 16) & 0xFFFFFFFF
    PostMessage WM_MOUSEWHEEL, wparam, lparam, , "ahk_id " hwnd
}
