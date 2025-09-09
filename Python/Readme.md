# 这些是？

这里存放个人制作的一些Python制作的小工具，基本是与AI合作完成。

# 项目说明

以下各个项目的简单说明。

## thumbnail_gui.py

- 视频预览图生成工具，带有GUI的版本；
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
