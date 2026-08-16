# Explorer Rescue v0.2.7

## 中文

Explorer Rescue 是一个面向 Windows 11 文件资源管理器的原生 C++ 会话保护/重启恢复工具。

### 实际运行目录严格只有 3 个文件

首次解压时只需要：

```text
ExplorerRescue.exe
```

第一次运行后，程序在 EXE 同目录自动生成：

```text
ExplorerRescue.exe
config.ini
sessions.dat
```

不会再创建其他持久文件：

- 不写注册表
- 不创建开机启动项
- 不创建 AppData 数据
- 不创建日志
- 不创建 session.tmp / session.bak
- 不联网、无遥测

如果 EXE 所在目录不可写，程序会直接报错退出，不会偷偷改写到其他目录。

### 使用方式

- **第一次双击 `ExplorerRescue.exe`**：静默进入后台守护，并开始周期扫描 Explorer 会话。
- **程序已经在后台时再次运行同一个 EXE**：执行“重启 Explorer 并恢复最近有效会话”。
- **Win + Shift + E**：执行同样的重启恢复。
- 如果后台进程意外没有运行，而 Explorer 已经卡死，再运行 EXE 时，只要 `sessions.dat` 中存在有效历史，会直接进入救援。

运行后提供托盘图标用于管理；双击托盘图标执行救援，右键可立即扫描、重启恢复或退出。没有设置 GUI。

### 扫描逻辑

首次运行后立即进行第一次扫描；之后默认每 5 秒扫描一次，可在 `config.ini` 修改。

自 v0.2.4 起，捕获方法按 Mine_Exp `get_open_windows()` 的核心语义实现；v0.2.7 保持该扫描路径不变：

1. `Shell.Application.Windows()` 作为唯一会话来源；
2. 对每个条目读取 `Document.Folder.Self.Path`；
3. 读取 `HWND`；
4. 相同 HWND 直接归为同一个 Windows 11 Explorer 标签组；
5. 单个没有 `Document/Folder/Self/Path/HWND` 的 ShellWindow 像 Mine_Exp 一样跳过，不让整个扫描器永久熔断；
6. 不检查 `CabinetWClass`、`FullName`，不要求 `Shell.Windows().Count` 前后完全一致；
7. 不再要求连续两次扫描结果相同；一次 Mine_Exp 等价枚举成功即形成一个采样；
8. 窗口状态仍记录 `GetWindowPlacement()`，同时对非最小化窗口额外记录 `GetWindowRect()` 的真实屏幕坐标；位置数据不可靠时使用主显示器工作区中央的常规窗口尺寸兜底。

只有 `Shell.Application` / `Shell.Windows()` 本身无法建立或枚举、COM 调用真正超时、内存分配失败、`sessions.dat` 写入失败等硬故障才停止后续扫描并通知。用户恰好在切换/创建/关闭标签造成的单条瞬态不可读不会再永久停扫。

另外，v0.2.7 继续保留 v0.2.4 已修正的原生 COM x64 ABI：旧版手写 `VARIANT` 只有 16 字节，而 Windows x64 `VARIANT` 必须是 24 字节。该错误可能导致 `IDispatch::Invoke` 返回值越界/错读，是旧版扫描失败的重要底层原因。源码现在通过 `static_assert(sizeof(VARIANT)==24)` 固定该约束。

### sessions.dat：单文件 5 历史槽

`sessions.dat` 内部固定保留最多 5 个完整有效的历史状态。

- 每个槽都有独立 header hash 与 payload hash；
- 写入顺序是 payload -> Flush -> header -> Flush；
- 写入中断最多损失当前被覆盖的一个槽，其余历史仍可读取；
- 完全相同的会话不会重复推进历史槽，降低 SSD 写入并保留更多不同的历史状态；
- 读取时自动选择 generation 最新的有效槽；最新槽损坏时自动回退到下一个有效槽。

因此不需要额外 `.tmp` / `.bak` 文件。

### 主动重启与卡死救援

#### Explorer 正常响应

触发重启时会先启动同一个 EXE 的一次性 `/capture-once` 子进程，进行最后一刻严格扫描。

- 最终扫描成功：写入 `sessions.dat`，随后重启 Explorer 并恢复该最新状态。
- 最终扫描失败但 Explorer 仍响应：取消重启，绝不冒险杀掉 Explorer。

子进程不会创建额外文件，超时后可以直接结束。

#### Explorer 已卡死

不再强行访问可能阻塞的 Shell COM，直接读取 `sessions.dat` 中最近一次完整有效扫描：

```text
最近有效扫描
    -> 重启 explorer.exe
    -> 恢复窗口组
    -> 在同一个窗口重新建立原标签页
    -> 逐标签导航
    -> 最终校验窗口/标签组合与路径
```

恢复失败的“此电脑”等错误标签不会被当作成功。

### Win11 标签页恢复

标签页方案沿用并强化 Mine_Exp 已验证的逻辑：

- `Shell.Application.Windows()` 中共享同一 Explorer HWND 的条目视为同一个窗口的标签组；
- 使用 `ShellTabWindowClass` 创建新标签；
- 对新标签执行 `Navigate2`；
- 新标签创建前记录 `(HWND, LocationURL)`，创建后以同 HWND 下的新增 ShellWindow 识别目标标签；
- 若新标签初始 URL 与已有 Home/“此电脑”标签重复，则以同 HWND 标签数量增长作为无破坏补强；
- 每个标签 `Navigate2` 后验证实际路径；最终整组校验只报告结果，绝不因校验失败关闭整个窗口。

### 窗口位置

v0.2.7 将“显示状态”和“屏幕位置”拆开保存：

- `GetWindowPlacement()` 只负责记录普通/最大化/最小化等显示状态；
- `GetWindowRect()` 记录非最小化 Explorer 窗口的真实屏幕坐标矩形；
- 恢复时用 `SetWindowPos()` 按屏幕坐标明确写回位置；
- 所有窗口/标签组恢复完成后再做一次最终位置回写，避免 Win11 Explorer 异步初始化覆盖第一次定位。

最小化窗口的几何数据不可信时不会使用最小化占位坐标，而是按既定规则恢复为主显示器工作区中央的常规大小窗口。旧版 `sessions.dat` 没有真实屏幕矩形，仍可兼容读取，但其位置一律视为“不确定”，按同样的居中规则处理。

### config.ini 默认内容

首次运行自动生成。修改后重新运行程序生效。

```ini
[Scan]
IntervalSeconds=5
TimeoutMilliseconds=20000

[Notification]
DurationSeconds=8
```

历史数量固定为 5；救援热键固定为 `Win + Shift + E`。`TimeoutMilliseconds` 允许 10000~120000 ms；旧版留下的 5000 会自动回退到 20000。

### 资源占用设计

- 原生 Win32/C++ x64
- 不依赖 .NET / Qt / WebView2
- 正式 EXE 不链接通用 CRT 动态运行库
- 空闲时只有隐藏消息窗口、托盘图标、定时器和热键
- Shell COM 只在扫描或恢复时短暂使用
- 扫描工作线程使用较小栈保留
- 会话没有变化时不重复写盘
- 无持续日志 I/O

## English

Explorer Rescue is a native Windows 11 File Explorer session guardian and restart/restore utility.

Runtime footprint is intentionally limited to three files in the executable directory:

```text
ExplorerRescue.exe
config.ini
sessions.dat
```

The first launch starts the background guardian. Launching the same EXE again, or pressing **Win + Shift + E**, restarts Explorer and restores the newest verified session, preserving Explorer window/tab grouping.


### v0.2.4 扫描器修复（v0.2.7 继承）

- 捕获代码改为 Mine_Exp `get_open_windows()` 的原生 C++ 等价实现。
- 删除 CabinetWClass / FullName / Count 前后一致性 / 双重稳定扫描等额外门槛。
- `WINDOWPLACEMENT` 降为可选附加数据，缺失时走居中常规窗口兜底。
- 修正自写 x64 `VARIANT` ABI：16 字节 -> 正确 24 字节，并加入编译期 static_assert。
- `sessions.dat` 格式未变化，旧历史继续兼容。


## v0.2.5 恢复修复（v0.2.7 继承）

- 标签恢复重新对齐 Mine_Exp 已验证机制：新建前记录 `(HWND, LocationURL)`，发送 `ShellTabWindowClass / 0xA21B` 后识别同 HWND 下新增 ShellWindow，再执行 `Navigate2`。
- 删除 COM IUnknown identity 新标签识别。
- 删除“最终校验失败就关闭整个窗口并重建”的破坏性逻辑。
- 最终路径校验失败只报告并暂停扫描保护旧历史，已恢复窗口/标签保持在桌面上。
- 单个新建标签导航失败时只关闭本次新建的垃圾标签，不关闭整组窗口。

- 安全保护：周期扫描发现当前为 0 个 Explorer 窗口时不推进历史；若已有有效历史且当前没有 Explorer 窗口，救援直接使用最近有效历史，不先写入空快照。


## v0.2.7 窗口位置修复

- 新增真实屏幕坐标 `screenRect`，捕获自 `GetWindowRect()`；不再仅依赖 `WINDOWPLACEMENT.rcNormalPosition`。
- 快照 payload 升级为 version 3；读取端同时兼容 version 2，旧 `sessions.dat` 无需删除。
- 普通/贴靠窗口按保存的屏幕坐标用 `SetWindowPos()` 精确恢复。
- 最大化窗口按保存屏幕矩形确定原显示器，再恢复最大化状态。
- 最小化或坐标不可验证的窗口不采用异常几何，统一在主显示器工作区中央恢复常规尺寸。
- 所有 Explorer 窗口/标签恢复结束后延迟 650 ms 再进行第二次位置回写，抵消 Win11 Explorer 初始化阶段的异步布局覆盖。


## v0.2.7 任务栏恢复加速

- 删除重启后启动新 Explorer 前固定等待 1.2 秒的逻辑；旧 Explorer 退出后立即启动新的 `explorer.exe`。
- `Shell_TrayWnd` 一旦创建且可响应，任务栏即已实际回来；随后仍保留 900 ms 的 Shell 稳定期供标签恢复使用，但它不再推迟 explorer.exe 的启动。
- 标签/窗口恢复仍使用各自的宿主与导航等待，不依赖任务栏固定延时。
- 不改变 v0.2.6 已验证的标签组恢复、路径恢复和窗口位置恢复逻辑。
