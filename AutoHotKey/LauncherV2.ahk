#Requires AutoHotkey v2.0

#SingleInstance Force
#NoTrayIcon

SetWorkingDir(A_ScriptDir)

Launcher := Gui()
Launcher.OnEvent("Close", GuiEscape)
Launcher.OnEvent("Escape", GuiEscape)
Launcher.Opt("-MaximizeBox -MinimizeBox DPIScale")

Launcher.Title := "ArnosurgeDXLauncher"
Launcher.Show("w220 h155")

Launcher.SetFont("s8 Bold", "Arial")
btnArnosurgeDX := Launcher.Add("Button", "x50 y20 w120 h32", "&ArnosurgeDX")
btnArnosurgeDX.OnEvent("Click", ArnosurgeDX.Bind("Normal"))
btnArnosurgeDXEnv := Launcher.Add("Button", "x50 y75 w120 h32", "&ArnosurgeDX_Env")
btnArnosurgeDXEnv.OnEvent("Click", ArnosurgeDXEnv.Bind("Normal"))
btnReadme := Launcher.Add("Button", "x175 y125 w30 h20", "&!")
btnReadme.OnEvent("Click", Readme.Bind("Normal"))
Return


ArnosurgeDX(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        Launcher.Hide()

        ; 这里是使用Locale Remulator启动游戏的部分，结构上是【LRProc.exe LR里的内置区域GUID 你的目标程序】，下方的写法算是计算机基础，所以小白请自行询问ai或者自己研究
        ; GUID在LR目录里LRConfig.xml文件中，写法类似【<Profile Name="Run in Japanese" Guid="99e3cc35-3478-4f8a-ac52-42ffd838e37e">】这样
        ; 我们只需要Guid=后面的双引号里的内容，出问题一般情况下替换这个部分，其他地方不动就能修好

        ; This is the part where the game is started using Locale Remulator. The structure is [LRProc.exe LR_built-in_area_GUID your_target_program].
        ; The following writing is considered computer basics, so novices please ask AI or study it yourself.
        ; The GUID is in the LRConfig.xml file in the LR directory. The writing is similar to [<Profile Name="Run in Japanese" Guid="99e3cc35-3478-4f8a-ac52-42ffd838e37e">]
        ; We only need the content in the double quotes after Guid=. If there is a problem, generally replace this part and fix it without changing other places.

        RunWait('".\LocaleRemulator\LRProc.exe" 99e3cc35-3478-4f8a-ac52-42ffd838e37e "ArnosurgeDX.exe"')
        ExitApp()
    }

ArnosurgeDXEnv(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        Launcher.Hide()

        ; LE的话比较舒适，LEProc.exe会在运行的时候先搜索目标exe有没有同名的le.config设置文件，如果有则读取这个设置而不是像LR似的读取自己的设置
        ; 所以如果这里出现问题了，一般找exe的config文件打开看看，实在不行就换成LR，换了以后写法参考上面游戏本体的参数

        ; LE is more comfortable. LEProc.exe will first search the target exe for a le.config setting file with the same name when running. If there is one, it will read the setting instead of reading its own setting like LR.
        ; So if there is a problem here, generally find the config file of the exe and open it. If it doesn't work, switch to LR. After that, refer to the parameters of the game above.
        
        RunWait('".\LocaleEmulator\LEProc.exe" -run "ArnosurgeDX_Env.exe"')
        Launcher.Restore()
    }

Readme(A_GuiEvent, GuiCtrlObj, Info, *)
    {
        SecondGui := Gui()
        SecondGui.Opt("-MaximizeBox -MinimizeBox DPIScale")
        SecondGui.SetFont("s8")
        SecondGui.Show("w500 h400")
        SecondGui.Title := "关于 About"    
        SecondGui.Add("Text", , "`n此启动器使用 AutoHotKey2.0.18 制作以及编译，采用press压缩。`n`n游戏的设置GUI是32位程序，使用Locale Emulator修复区域问题。`n游戏程序是64位程序，使用Locale Remulator修复区域问题。`n`nahk源代码应该在你下载到的压缩包里，供研究使用。`n如果未来出现错误，请参考源码，对启动代码进行修复。")
        SecondGui.Add("Text", , "This launcher is made and compiled with AutoHotKey2.0.18 and compressed with press. `nThe game's settings GUI is a 32-bit program, and the Locale Emulator is used to fix `nthe regional problem. `nThe game program is a 64-bit program, and the Locale Remulator is used to fix it. `n`nThe ahk source code should be in the compressed file you downloaded `nfor research purposes.`nIf errors occur in the future, please refer to the source code `nand fix the startup code.")
        SecondGui.Add("Text", , "`nBy LuminousNox")    
        SecondGui.Add("Text", , "`n链接 Link：")
        SecondGui.Add("Link",, 'Locale Emulator:    <a href="https://github.com/xupefei/Locale-Emulator/">https://github.com/xupefei/Locale-Emulator/</a>')
        SecondGui.Add("Link",, 'Locale Remulator:    <a href="https://github.com/InWILL/Locale_Remulator/">https://github.com/InWILL/Locale_Remulator/</a>')
        SecondGui.Add("Link",, 'AutoHotKey V2:  <a href="https://www.autohotkey.com">https://www.autohotkey.com</a>')
        btnOK := SecondGui.Add("Button", "x420 y360 w70 h30", "&OK")
        btnOK.OnEvent("Click", OK.Bind("Normal"))
        return

        OK(A_GuiEvent, GuiCtrlObj, Info, *)
        {
            SecondGui.Destroy
        }
    }

GuiEscape(*)
    {
        ExitApp()
    }
