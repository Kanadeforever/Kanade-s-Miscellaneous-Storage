# PortableLELauncher（最小改动版）

这个 launcher 的目标是：**不重新实现 Locale Emulator，不改 LEGUI 配置格式，不改 LEProc 的启动逻辑**，只把官方 Locale Emulator 包装成可随游戏目录携带的绿色启动方式。

## 目录结构

```text
GameFolder/
├─ PortableLELauncher.exe
└─ .le/
   ├─ PortableLELauncher.ini   # launcher 自己的目标 exe 记录，不是 LE profile
   ├─ LEProc.exe               # 官方 Locale Emulator 文件
   ├─ LEGUI.exe                # 官方 Locale Emulator 文件
   ├─ LoaderDll.dll            # 官方 Locale Emulator 文件
   ├─ LocaleEmulator.dll       # 官方 Locale Emulator 文件
   └─ ...                      # 其他官方 LE 文件按需放入
```

## 行为

### 第一次运行

1. 让用户选择目标 `exe`。
2. 打开官方 `LEGUI.exe`，并传入：

```bat
LEGUI.exe "目标.exe.le.config"
```

3. 用户在官方 LEGUI 的“程序设置”界面里配置区域、时区、命令行参数等。
4. LEGUI 按官方格式创建：

```text
目标.exe.le.config
```

5. launcher 调用：

```bat
.le\LEProc.exe -run "目标.exe"
```

### 以后运行

直接调用：

```bat
.le\LEProc.exe -run "目标.exe"
```

`<Parameter>`、`<Location>`、`<Timezone>` 等字段全部由官方 `目标.exe.le.config` 控制。

## 参数

```bat
PortableLELauncher.exe
```

正常启动。

```bat
PortableLELauncher.exe /config
```

打开官方 LEGUI 编辑当前目标的 `目标.exe.le.config`，不启动程序。

```bat
PortableLELauncher.exe /select
```

重新选择目标 exe，然后打开官方 LEGUI。

```bat
PortableLELauncher.exe -windowed -debug
```

会转发为：

```bat
.le\LEProc.exe -run "目标.exe" -windowed -debug
```

注意：这保持官方 LEProc 行为。如果 `目标.exe.le.config` 里的 `<Parameter>` 不为空，官方 LEProc 会优先使用 `<Parameter>`，后面的追加参数可能不会生效。

## 这个版本为什么“最小改动”

- 使用官方 `LEGUI.exe` 创建/编辑配置。
- 使用官方 `LEProc.exe -run` 启动。
- 配置文件仍然是官方 `目标.exe.le.config`。
- launcher 只记录目标路径，不保存 LE profile。
- 不做 ASI Loader，不做 DLL 代理，不自己 Hook API。

## 如果想让所有 LE 配置都待在 `.le` 目录里

官方零改动模式下，独立程序配置固定是：

```text
目标.exe.le.config
```

如果目标 exe 在 `GameFolder`，这个文件就会出现在 `GameFolder`。

想把每个程序的 app config 也放进 `.le\configs\`，同时继续复用官方 LEGUI 的“程序设置”界面，最小补丁方案是只给 `LEProc.exe` 加一个新命令，例如：

```bat
LEProc.exe -runconf ".le\configs\game.le.config" "目标.exe" [args]
```

LEGUI 本身不用改，因为它已经可以接受任意 `.le.config` 路径并打开 AppConfig 界面；需要补的只是 LEProc 从指定 config 文件读取 profile 后启动目标程序。
