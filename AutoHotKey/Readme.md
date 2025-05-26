# 这些是？

这里存放个人制作的一些AutoHotKey小工具。

# 项目说明

以下各个项目的简单说明。

## compile

- 存放编译好的EXE文件。

### 自动翻页GUI威力加强版V2.exe

- 该文件对应[AutoPageGUIV2.ahk](#autopagev2)。


## 1ClickChangeName.ahk

- 简化Ctrl+C【复制】/Ctrl+X【剪切】/Ctrl+V【粘贴】到【Q】【W】【E】输入；
- 初学时期写的的AHKv1脚本。

<a id="autopagev2"></a>
## AutoPageGUIV2.ahk

- 用于间隔一定时间输入某个按键，可能有部分BUG，但，it just works.
- 用来看韩漫整的，感谢Microsoft Copilot辅助。

<a id="customl1"></a>
## CustomLauncherV1.ahk

- 这是一个可以自定义的简单的启动器模板；
- 它的v2版[是这个](#customl2)（代码是ahkv2的）。

<a id="customl2"></a>
## CustomLauncherV2.ahk

- 这是一个可以自定义的简单的启动器模板；
- 它的v1版[是这个](#customl1)（代码是ahkv2的）。

<a id="ltemp"></a>
## LauncherTemplate.ahk

- 这是一个可以自定义的简单的游戏启动器模板；
- 用于让某些需要命令行加载MOD的游戏可以使用Steam一键启动；
- 这是AHKv1版代码，是这个游戏启动器的最终版；
- 已被我广泛用于[Ar Nosurge DX非日文系统转码(当然这是V2版)](#arnov2)、[上古卷轴5通过Steam启动MO2](#skyriml)等等用途，可以说久经考验，十分耐用；
- 感谢Microsoft Copilot部分辅助；
- AHKv2版参见[这里](#arnov2)。

## FalloutLauncherSteam.ahk

- [启动器模板](ltemp)的辐射3版本；
- 也曾用于新维加斯。

## OblivionLauncher.ahk

- [启动器模板](ltemp)的上古卷轴4版本；
- 当然，这是上古卷轴4原版的，做这个的时候4高清版八字连一撇都没。

<a id="skyriml"></a>
## SkyrimLauncher.ahk

- [启动器模板](ltemp)的上古卷轴5版本；
- 也曾用于上古卷轴5特别版也就是现在的十周年版。

## （参考）刀剑封魔录启动器源码.ahk

- [启动器模板](ltemp)的刀剑封魔录版本；
- 整个启动器代码的原型的最终版本；
- 可以说这套启动器就是为了这个游戏和上古卷轴4做的。

<a id="arnov2"></a>
## LauncherV2.ahk

- 用于[Ar Nosurge Delux](https://www.pcgamingwiki.com/wiki/Ar_Nosurge%3A_Ode_to_an_Unborn_Star_DX)在非日文系统下调用[Locale Emulator](https://github.com/xupefei/Locale-Emulator)和[Locale Remulator](https://github.com/InWILL/Locale_Remulator)解决游戏和设置乱码&无法打开的问题（是的，这几把游戏设置程序32位游戏64位，必须LE和LR一起用）
- 脱胎自[启动器模板](ltemp)，改写为了AHKv2版。
- AHKv1版参见[这里](#ltemp)。

## SimpleJoystickMapping.ahk

- 简单的手柄映射脚本，本是用于《天地劫·神魔至尊传》的便捷控制，但水平不够，输入手感感觉不够到位，未能继续优化。
- it just works.
- 可以的话希望有一天能优化的更好。

<a id="wmove2"></a>
## WindowMoveV2.ahk

- 用于将活动窗口快速移动到主显示器的正中央；
- 建议搭配微软的[PowerToys](https://github.com/microsoft/PowerToys)中的FancyZone功能使用；
- 感谢Microsoft Copilot部分辅助。


## 调整窗口大小_ResizeWindow.ahk

- 用于将活动窗口调整到指定大小；
- 建议搭配[WindowMoveV2](#wmove2)脚本使用，效果更好；
- 可能会存在窗口尺寸有个别像素的差异，这个暂时没有好方案解决；
- 感谢Microsoft Copilot部分辅助。