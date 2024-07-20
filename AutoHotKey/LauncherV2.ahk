#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon

SetWorkingDir(A_ScriptDir)

Launcher := Gui()
Launcher.OnEvent("Close", GuiEscape)
Launcher.OnEvent("Escape", GuiEscape)
Launcher.Opt("-MaximizeBox -MinimizeBox +AlwaysOnTop -DPIScale")

Launcher.Title := "Grandia Launcher"
Launcher.Show("w273 h251")

; Launcher.SetFont("s9 Bold", "Arial")
btnGrandia := Launcher.Add("Button", "x50 y44 w172 h62", "&Grandia")
btnGrandia.OnEvent("Click", Grandia.Bind("Normal"))
btnGrandiaLauncher := Launcher.Add("Button", "x50 y144 w172 h62", "&Launcher")
btnGrandiaLauncher.OnEvent("Click", GrandiaLauncher.Bind("Normal"))

Return


Grandia(A_GuiEvent, GuiCtrlObj, Info, *)
{
    RunWait "grandia.exe"
    ExitApp()
}

GrandiaLauncher(A_GuiEvent, GuiCtrlObj, Info, *)
{
    RunWait "orglauncher.exe"
    ExitApp()
}

GuiEscape(*)
{
    ExitApp()
}
