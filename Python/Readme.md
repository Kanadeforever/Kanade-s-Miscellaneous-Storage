# 这些是？

这里存放个人制作的一些Python制作的小工具，基本是与AI合作完成。

# 项目说明

以下各个项目的简单说明。

---

## LittleAppBrowserLite.py

- 使用 `pywebview` 调用系统浏览器内核的、支持多平台的简易浏览器GUI
- 用于懒得开浏览器、想把某些没有GUI只有webui的程序弄的像exe程序一样运行（本来想支持pwa但失败了，感觉还不如用ungoogled-chromium+命令行组合起来方便？）
- 非常简陋，超级简陋，巨TMD简陋，it just works!
- 所有的可配置项都在运行脚本后产生的同名TOML文件里，可自行配置。
- 支持命令行操作，支持用命令行启动不同网址、式样，具体由TOML文件控制
- 命令行用法： `python LittleAppBrowserLite.py -c "你的TOML文件地址"`
- 老样子，程序设计、DEBUG是我，代码编写是Microsoft Copilot。

## LittleAppBrowserRelease.py

- `LittleAppBrowserLite.py` 的功能扩展版，不如说写到最后好多东西不一样了；
- 对比 `LittleAppBrowserLite.py` ，最大的变化就是有GUI了，如果同目录有多个配置文件，打开的时候会弹出GUI让你选择用哪个文件
- 大部分报错都支持用GUI显示，但脚本最开始的缺少依赖报错实在是懒得搞了（最开始某个版本实现了但后面被Copilot吞了2333）。
  - 如果懒就用编译好的exe，没这个问题。
  - 选择界面的GUI支持键盘控制。
- 所有的可配置项都在运行脚本后产生的同名TOML文件里，可自行配置。
- 支持插件系统
  - 是的，有不满意的功能你可以自己复制到AI里让ai帮你写，反正都是python和js，ai强项。
  - 甚至这个项目的所有插件都是Copilot单独写的。
  - 内置插件包含右键菜单、热键、高DPI缩放和页面随窗口缩放（这个要网页支持）。
    - 这些插件会随着程序第一次启动时自动生成，除了提供基础的体验外，也可以作为编写新插件的参考。
    - 除了这些以外，还会生成一个参考用的插件模板，这个不会被加载。
  - 除了核心功能外其余所有功能都已经插件化了。
  - 插件加载使用外置（但是必需项）的js加载器。
  - 插件包含外置（但是必需项）的插件管理器，可按`Ctrl`+`Shift`+`P`唤出插件控制界面，可对插件进行开关（需要重启程序）和排序。
    - 排序不需要重启，实时更改，可以选中项目后按住shift+上下方向键移动，也可以鼠标拖拽插件来移动顺序。
  - 插件使用方法非常简单，按照规则写好丢到需要插件的配置目录里的modules目录内就行了，格式是py脚本
  - 插件的加载顺序可通过TOML文件里的Modules组的排序决定，最上面的最先加载。
- 支持命令行操作，支持用命令行启动不同网址、式样，具体由TOML文件控制
- 命令行用法： `python LittleAppBrowserLite.py -c "你的TOML文件地址"`
- 同上，依然非常简陋。能用就行。
- 老样子，程序设计、DEBUG是我，代码编写是Microsoft Copilot。
- 应该是迄今为止做过的规模最大的程序了……

## thumbnail_gui.py

- 视频预览图生成工具，带有GUI的版本，需要[FFmpeg](https://ffmpeg.org)；
- 用于生成视频的预览图，支持单个文件和文件夹批量使用（但基于个人考量没有支持子文件夹处理）；
- Python以外的依赖项为ffmpeg，除了可以放在脚本同目录外，也支持在GUI上勾选自定义路径；
- 支持为截图添加时间戳与帧序号，以及这两项的字号调整；
- 日志等级支持两种详细程度；
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码。

## CHD&CSOConverterFrontendV4.py

- 为[maxcso](https://github.com/unknownbrackets/maxcso)和[chdman(包含在mame发行版文件内)](https://docs.mamedev.org/tools/chdman.html)写的共用前端；
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码；
- 依然有些小问题；
- it just works!

## CHD&CSO_Converter_Frontend_V5_2.pyw

- 为[maxcso](https://github.com/unknownbrackets/maxcso)和[chdman(包含在mame发行版文件内)](https://docs.mamedev.org/tools/chdman.html)写的共用前端的新版本；
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码；
- V4的小问题基本解决了，不阻塞线程，也有日志窗口显示进度，虽然还有一些地方不满意但够用了；
- it just works!

## SimpleLauncher.py

- 简单的Windows用GUI启动器；
- 自动扫描所在文件夹下的exe文件，并记录在config.ini中，使用相对路径；
- 支持命令行参数，在ini文件里的args参数中填写；
- 有些小问题但懒得修了，it just works!
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码。

## check_video.py

- 简单的检测视频文件完整性的小工具；
- 需要[FFmpeg](https://ffmpeg.org)的 `ffmpeg.exe`；
- 使用方法用 `check_video.py -h` 查看；
- 有bug，搞不定终止子进程，但懒得修了，it just works!
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码。

## check_video_gui.py

- `check_video.py` 的GUI启动器，但功能大修版，依然需要[FFmpeg](https://ffmpeg.org)的 `ffmpeg.exe`；
- 基本功能和 `check_video.py` 一致，但用了奇技淫巧让其可以手动终止子进程了；
- 使用方法已经完全暴露在GUI里，另外只支持选择文件夹，选择单个文件这个懒得改了，有兴趣可以把代码丢给copilot自己提需求改；
- 支持进度条显示、支持剩余时间估算、高DPI缩放；
- 基本上我提需求与DEBUG，Microsoft Copilot完成代码。
