Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """C:\Program Files\Intel\Intel(R) Extreme Tuning Utility\Client\PerfTune.exe""", 0, False
WScript.Sleep 15000
Set colProcessList = GetObject("Winmgmts:").ExecQuery ("Select * from Win32_Process Where Name = 'PerfTune.exe'")
If colProcessList.Count = 0 Then
       WScript.Echo "PerfTune.exe Not Found."
       WScript.Quit
ElseIf colProcessList.Count = 1 Then
       WshShell.SendKeys "^+{HOME}"
       WScript.Quit
End If