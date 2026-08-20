# Explorer Rescue v0.2.11

Explorer Rescue 是一个面向 Windows 11 文件资源管理器的原生 C++ 会话保护与重启恢复工具。

## 使用方式

首次双击 `ExplorerRescue.exe`：

- 程序静默常驻后台；
- 显示托盘图标；
- 立即进行第一次 Explorer 会话扫描；
- 不重启 Explorer。

程序已经运行时：

- 再次双击同一个 EXE：重启 Explorer 并恢复最近一次有效会话；
- `Win + Shift + E`：执行同样的 Explorer 重启恢复；
- 双击托盘图标：**重启 Explorer Rescue 本软件**；
- 托盘右键：可“重启 Explorer Rescue”“立即扫描并保存会话”“状态”“退出 Explorer Rescue”。

如果重启/退出本软件时恰好有一个隔离 `/capture-once` 子进程在工作，守护会先等它完成或按超时规则回收，再退出/重启，避免留下孤儿扫描进程。

运行目录长期只有：

```text
ExplorerRescue.exe
config.ini
sessions.dat
```

`config.ini` 与 `sessions.dat` 首次运行自动生成。程序不写注册表、不创建自启动、不写日志、不联网、不使用 AppData。

## 默认配置

```ini
[Scan]
IntervalSeconds=5
TimeoutMilliseconds=20000
NotifyAfterConsecutiveFailures=3

[Notification]
DurationSeconds=8
```

旧版已经生成的 `config.ini` 没有 `NotifyAfterConsecutiveFailures` 也不需要删除或迁移；程序会自动使用默认值 `3`。如需手动调整，可直接补上这一项并重启 Explorer Rescue。

程序固定保留最近 5 份不同的有效会话。

## v0.2.11 扫描容错变化

v0.2.10 长期实机使用发现：偶发 Shell COM 扫描超过独立超时时间时，旧版会永久停止后续扫描。这个策略过于保守。

v0.2.11 改为“每轮扫描都是独立事务”：

```text
本轮扫描成功
→ 保存完整有效会话（有变化时才写盘）
→ 连续失败计数清零

本轮枚举失败 / 写入失败
→ 丢弃本轮
→ 不覆盖最近 5 份历史
→ 下一周期继续自动尝试

连续失败达到阈值
→ 只通知一次
→ 仍继续自动重试

之后任意一轮成功
→ 失败计数和通知状态全部清零
```

如果正常扫描线程真的卡在 Explorer/Shell COM 调用里并超过 watchdog：

1. 该线程的扫描结果立即标记为废弃；
2. 不用 `TerminateThread` 粗暴杀线程；
3. 旧线程自行等待 COM 返回；
4. 在旧线程返回之前，后续自动重试改用同一个 EXE 的 `/capture-once` 一次性子进程；
5. 子进程若同样卡住，可以由父进程超时后整体结束，因此不会每 5 秒堆一个永久卡死线程；
6. 旧正常线程一旦自行返回，自动恢复低开销线程扫描。

只有 **Explorer 已经重启、但恢复结果本身失败** 时，程序才继续保留“保护性暂停”。原因是这时桌面上可能已经是残缺恢复状态，继续保存可能污染最近 5 份有效历史。

## v0.2.10 稳定恢复基线

用户已经实机确认 v0.2.10 “这次没问题了”。v0.2.11 不修改以下已通过主链：

- Win11 Explorer 按原窗口标签页组恢复；
- 标签实际路径恢复；
- 原窗口位置恢复；
- 位置不确定时按主显示器工作区中央常规尺寸兜底；
- 任务栏快速恢复；
- 每组首窗口完整加载并连续稳定后才创建后续标签；
- Shell bootstrap 的“此电脑/Home”窗口复用与安全清理；
- 托盘常驻与 `TaskbarCreated` 后自动恢复托盘图标；
- `Win + Shift + E` / 再次运行 EXE 的 Explorer 救援入口。
