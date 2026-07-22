using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;

/// <summary>
/// Portable LE Launcher 的命名空间。
///
/// 本程序只是一个便携式前端：它不自行实现区域模拟，也不修改目标 EXE，
/// 而是调用放在启动器目录下“.le”子目录中的官方 Locale Emulator 组件。
/// </summary>
namespace PortableLELauncher
{
    /// <summary>
    /// 程序入口及全部启动逻辑。
    ///
    /// 主要职责：
    /// 1. 计算启动器目录和“.le”目录；
    /// 2. 检查官方 Locale Emulator 文件是否齐全；
    /// 3. 读取或保存当前绑定的目标 EXE；
    /// 4. 首次运行时调用官方 LEGUI.exe 创建目标程序的配置；
    /// 5. 正常运行时调用官方 LEProc.exe 启动目标程序；
    /// 6. 过滤启动器自己的管理开关，并转发其余命令行参数。
    /// </summary>
    internal static class Program
    {
        /// <summary>
        /// Locale Emulator 官方文件所在的固定子目录名。
        ///
        /// 预期目录结构：
        /// launcher.exe
        /// .le\LEProc.exe
        /// .le\LEGUI.exe
        /// .le\LoaderDll.dll
        /// .le\LocaleEmulator.dll
        /// </summary>
        private const string LeFolderName = ".le";
        /// <summary>
        /// 启动器自己的轻量配置文件名。
        ///
        /// 该 INI 只记录目标 EXE 路径；语言、代码页、时区等 LE 配置
        /// 仍由官方 LEGUI 保存到“目标程序.exe.le.config”中。
        /// </summary>
        private const string LauncherConfigName = "PortableLELauncher.ini";

        /// <summary>
        /// 程序主入口。
        /// </summary>
        /// <param name="args">
        /// 启动器收到的命令行参数。
        /// /select 与 --select 用于重新选择目标程序；
        /// /config、--config、/manage、--manage 用于只打开配置界面；
        /// 其他参数会继续传给 LEProc.exe。
        /// </param>
        // Windows Forms 的文件选择框和部分 OLE/COM 组件要求主线程使用 STA。
        [STAThread]
        private static void Main(string[] args)
        {
            // 启用 Windows 当前视觉样式，使对话框和控件使用系统主题。
            Application.EnableVisualStyles();
            // 使用 Windows Forms 的兼容文本渲染设置。
            Application.SetCompatibleTextRenderingDefault(false);

            // 将整个启动流程置于统一异常处理内，避免未处理异常直接结束程序。
            try
            {
                // 创建本次运行上下文：集中计算路径、保留原始参数，
                // 并生成过滤掉启动器管理开关后的参数列表。
                var ctx = LauncherContext.Create(args);
                // 启动前检查“.le”目录以及官方 LE 文件是否完整。
                // 这样可以给出明确的缺失文件提示，而不是稍后收到模糊的进程启动错误。
                ctx.EnsureLeFiles();

                // 强制重新选择目标程序。大小写差异由 HasSwitch 忽略。
                var forceSelect = ctx.HasSwitch("/select") || ctx.HasSwitch("--select");
                // “只编辑配置”模式。/manage 是 /config 的同义开关。
                var editOnly = ctx.HasSwitch("/config") || ctx.HasSwitch("--config") || ctx.HasSwitch("/manage") || ctx.HasSwitch("--manage");

                // 从“.le\PortableLELauncher.ini”读取上次绑定的目标 EXE。
                var settings = LauncherSettings.Load(ctx.LauncherConfigPath);
                // 没有保存目标、目标路径为空，或目标文件已被移动/删除时，
                // 都按首次运行处理。
                var firstRun = string.IsNullOrWhiteSpace(settings.TargetPath) || !File.Exists(settings.TargetPath);

                // 首次运行或显式使用 /select 时进入目标选择流程。
                if (firstRun || forceSelect)
                {
                    // 文件选择框优先从上次目标所在目录打开；
                    // 上次目录无效时则从启动器目录打开。
                    var selected = SelectExecutable(ctx.BaseDirectory, settings.TargetPath);
                    // 用户取消选择属于正常退出，不显示错误消息。
                    if (string.IsNullOrWhiteSpace(selected))
                        return;

                    // 更新并保存目标 EXE 的完整路径。
                    settings.TargetPath = selected;
                    settings.Save(ctx.LauncherConfigPath);

                    // 首次选择目标程序后，调用官方 LEGUI.exe。
                    // LEGUI 编辑的是“<目标程序>.le.config”，而不是启动器自己的 INI。
                    // 此处等待 LEGUI 退出，确保用户保存配置后才继续启动目标程序。
                    RunLeguiForApplication(ctx.LeGuiPath, settings.TargetPath, wait: true);
                }
                // 已存在有效目标，且用户只要求打开配置界面时进入此分支。
                else if (editOnly)
                {
                    // 此模式只启动 LEGUI，不等待其关闭，也不继续启动游戏。
                    RunLeguiForApplication(ctx.LeGuiPath, settings.TargetPath, wait: false);
                    return;
                }

                // 取得过滤后的参数。/select、/config 等启动器开关不会传给目标程序。
                var passthroughArgs = ctx.PassthroughArgs;
                // 调用官方 LEProc.exe，在 Locale Emulator 环境下启动目标 EXE。
                RunLeproc(ctx.LeProcPath, settings.TargetPath, passthroughArgs);
            }
            // 捕获路径、文件、权限和进程启动等异常，并向用户显示简洁错误信息。
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "Portable LE Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// 显示文件选择框，让用户选择需要通过 Locale Emulator 启动的程序。
        /// </summary>
        /// <param name="baseDirectory">启动器自身所在目录。</param>
        /// <param name="previousTarget">上次保存的目标 EXE 路径。</param>
        /// <returns>用户确认时返回完整路径；取消时返回 null。</returns>
        private static string SelectExecutable(string baseDirectory, string previousTarget)
        {
            // OpenFileDialog 持有原生窗口资源，使用 using 确保及时释放。
            using (var dialog = new OpenFileDialog())
            {
                // 对话框标题明确说明所选择程序的用途。
                dialog.Title = "选择要用 Locale Emulator 启动的程序";
                // 默认筛选 EXE，同时保留“所有文件”以兼容扩展名异常的可执行文件。
                dialog.Filter = "Executable files (*.exe)|*.exe|All files (*.*)|*.*";
                // 禁止确认不存在的文件。
                dialog.CheckFileExists = true;
                // 启动器一次只绑定一个目标程序。
                dialog.Multiselect = false;
                // 如果上次目标的父目录仍存在，则从该目录打开；
                // 否则退回启动器目录。previousTarget 为空时用空字符串避免异常。
                dialog.InitialDirectory = Directory.Exists(Path.GetDirectoryName(previousTarget ?? ""))
                    ? Path.GetDirectoryName(previousTarget)
                    : baseDirectory;

                // 仅在用户按下“打开/确定”时返回路径，其他结果统一视为取消。
                return dialog.ShowDialog() == DialogResult.OK ? dialog.FileName : null;
            }
        }

        /// <summary>
        /// 调用官方 LEGUI.exe，创建或编辑指定目标程序的独立 LE 配置。
        /// </summary>
        /// <param name="leGuiPath">LEGUI.exe 的完整路径。</param>
        /// <param name="targetPath">目标 EXE 的完整路径。</param>
        /// <param name="wait">是否等待 LEGUI 进程退出。</param>
        private static void RunLeguiForApplication(string leGuiPath, string targetPath, bool wait)
        {
            // Locale Emulator 按程序保存配置，文件名固定为：
            // “目标程序完整路径 + .le.config”。
            // 例如 D:\Game\Game.exe 对应 D:\Game\Game.exe.le.config。
            var configPath = targetPath + ".le.config";
            // 构造 LEGUI 的进程启动参数。
            var psi = new ProcessStartInfo
            {
                // 要运行的官方配置程序。
                FileName = leGuiPath,
                // 将 .le.config 路径作为单个参数传入；Quote 负责处理空格。
                Arguments = Quote(configPath),
                // 使用 LEGUI 所在目录作为工作目录，便于官方组件按相对路径加载资源。
                WorkingDirectory = Path.GetDirectoryName(leGuiPath),
                // 不通过 Windows Shell，直接创建进程，使参数行为更明确。
                UseShellExecute = false
            };

            // Process.Start 理论上可能返回 null，因此等待前同时检查进程对象。
            // using 会在完成后释放 Process 对象持有的系统资源。
            using (var process = Process.Start(psi))
            {
                // 首次配置时 wait=true；只编辑配置时 wait=false。
                if (wait && process != null)
                    process.WaitForExit();
            }
        }

        /// <summary>
        /// 调用官方 LEProc.exe，在 Locale Emulator 环境中启动目标程序。
        /// </summary>
        /// <param name="leProcPath">LEProc.exe 的完整路径。</param>
        /// <param name="targetPath">目标 EXE 的完整路径。</param>
        /// <param name="passthroughArgs">需要继续转发的目标程序参数。</param>
        private static void RunLeproc(string leProcPath, string targetPath, IReadOnlyList<string> passthroughArgs)
        {
            // 官方命令格式：
            // LEProc.exe -run "target.exe" [args]
            // 注意：如果“target.exe.le.config”的 <Parameter> 已保存参数，
            // 官方 LEProc 通常会优先使用配置文件中的参数。
            // 使用 StringBuilder 逐段构造完整参数，避免频繁创建中间字符串。
            var arguments = new StringBuilder();
            // -run 表示按目标程序对应的 LE 配置启动。
            arguments.Append("-run ");
            // 目标 EXE 路径作为 -run 后的第一个参数。
            arguments.Append(Quote(targetPath));

            // 逐个追加转发参数，并保持用户传入时的原始顺序。
            foreach (var arg in passthroughArgs)
            {
                // 参数之间使用单个空格分隔。
                arguments.Append(' ');
                // 每个参数单独加引号，避免参数中的空格被重新拆分。
                arguments.Append(Quote(arg));
            }

            // 构造 LEProc.exe 的启动信息。
            var psi = new ProcessStartInfo
            {
                // 官方区域模拟启动程序。
                FileName = leProcPath,
                // 最终形式为：-run "目标.exe" "参数1" "参数2"。
                Arguments = arguments.ToString(),
                // 使用 .le 目录作为工作目录，便于加载同目录官方 DLL。
                WorkingDirectory = Path.GetDirectoryName(leProcPath),
                // 直接创建进程，不借助 Shell 文件关联。
                UseShellExecute = false
            };

            // 不等待 LEProc 或游戏退出；目标启动后，本启动器即可结束。
            Process.Start(psi);
        }

        /// <summary>
        /// 将字符串包装成一个命令行参数。
        /// </summary>
        /// <param name="value">原始参数内容。</param>
        /// <returns>适合拼入 ProcessStartInfo.Arguments 的参数文本。</returns>
        private static string Quote(string value)
        {
            // 空参数必须表示为 ""；否则它会从命令行中消失。
            if (string.IsNullOrEmpty(value))
                return "\"\"";

            // 对普通启动器参数采用 Windows CreateProcess 可接受的引号形式。
            // 不含空白和双引号时保持原样，减少不必要的转义。
            // Any(char.IsWhiteSpace) 会识别空格、制表符等全部空白字符。
            if (!value.Any(char.IsWhiteSpace) && !value.Contains("\""))
                return value;

            // 有空白或双引号时：整体加双引号，并转义内部反斜杠与双引号。
            // 此函数保持项目原有行为，主要服务于普通路径和启动参数。
            return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }

        /// <summary>
        /// 保存一次启动所需的路径和参数状态。
        ///
        /// 将环境计算集中到此类，可避免 Main、配置调用和启动调用
        /// 分别重复拼接“.le”目录及官方程序路径。
        /// </summary>
        private sealed class LauncherContext
        {
            /// <summary>启动器 EXE 所在目录，不含末尾目录分隔符。</summary>
            public string BaseDirectory { get; private set; }
            /// <summary>启动器目录下“.le”子目录的完整路径。</summary>
            public string LeDirectory { get; private set; }
            /// <summary>官方 LEProc.exe 的完整路径。</summary>
            public string LeProcPath { get; private set; }
            /// <summary>官方 LEGUI.exe 的完整路径。</summary>
            public string LeGuiPath { get; private set; }
            /// <summary>启动器自身 INI 配置文件的完整路径。</summary>
            public string LauncherConfigPath { get; private set; }
            /// <summary>Main 收到的全部原始命令行参数。</summary>
            public string[] RawArgs { get; private set; }
            /// <summary>移除启动器管理开关后，需要转发的参数。</summary>
            public IReadOnlyList<string> PassthroughArgs { get; private set; }

            /// <summary>
            /// 根据启动器所在位置和命令行参数创建运行上下文。
            /// </summary>
            /// <param name="args">Main 收到的参数数组，可为 null。</param>
            /// <returns>初始化完成的 LauncherContext。</returns>
            public static LauncherContext Create(string[] args)
            {
                // BaseDirectory 通常带有末尾反斜杠；TrimEnd 后统一交给 Path.Combine。
                var baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                // 官方 Locale Emulator 文件固定从“.le”子目录读取。
                var leDir = Path.Combine(baseDir, LeFolderName);

                // 这些开关只控制启动器自身，不能继续传给目标程序。
                // OrdinalIgnoreCase 提供不受系统区域设置影响的大小写不敏感比较。
                var knownSwitches = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
                {
                    "/config", "--config", "/manage", "--manage", "/select", "--select"
                };

                // 一次性填充全部派生路径和参数集合。
                return new LauncherContext
                {
                    // 启动器根目录。
                    BaseDirectory = baseDir,
                    // Locale Emulator 便携目录。
                    LeDirectory = leDir,
                    // 官方命令行启动组件。
                    LeProcPath = Path.Combine(leDir, "LEProc.exe"),
                    // 官方图形配置组件。
                    LeGuiPath = Path.Combine(leDir, "LEGUI.exe"),
                    // 启动器元数据也保存在 .le 内，不写入用户目录。
                    LauncherConfigPath = Path.Combine(leDir, LauncherConfigName),
                    // 正常情况下 args 不为 null；仍以空数组兜底，避免空引用。
                    RawArgs = args ?? Array.Empty<string>(),
                    // 仅移除完整匹配的已知管理开关；其他参数保持顺序不变。
                    PassthroughArgs = (args ?? Array.Empty<string>()).Where(a => !knownSwitches.Contains(a)).ToArray()
                };
            }

            /// <summary>
            /// 检查原始参数中是否存在指定开关。
            /// </summary>
            /// <param name="name">要检查的开关名称。</param>
            /// <returns>存在时为 true。</returns>
            public bool HasSwitch(string name)
            {
                // 使用 OrdinalIgnoreCase，确保 /SELECT 与 /select 行为一致。
                return RawArgs.Any(a => string.Equals(a, name, StringComparison.OrdinalIgnoreCase));
            }

            /// <summary>
            /// 检查“.le”目录及运行所需的官方 Locale Emulator 文件。
            /// 缺少目录或文件时抛出带中文说明的异常，由 Main 统一显示。
            /// </summary>
            public void EnsureLeFiles()
            {
                // 目录本身不存在时直接报告正确的便携部署位置。
                if (!Directory.Exists(LeDirectory))
                    throw new DirectoryNotFoundException("缺少 .le 子目录。请把官方 Locale Emulator 文件放进 launcher 同目录下的 .le 文件夹。\r\n" + LeDirectory);

                // 收集全部缺失文件，一次性报告，避免用户逐个修复、逐次重启。
                var missing = new List<string>();
                // LEProc.exe：按指定配置启动目标程序。
                if (!File.Exists(LeProcPath)) missing.Add("LEProc.exe");
                // LEGUI.exe：创建和编辑“目标.exe.le.config”。
                if (!File.Exists(LeGuiPath)) missing.Add("LEGUI.exe");
                // LoaderDll.dll：Locale Emulator 官方加载组件。
                if (!File.Exists(Path.Combine(LeDirectory, "LoaderDll.dll"))) missing.Add("LoaderDll.dll");
                // LocaleEmulator.dll：Locale Emulator 官方核心组件。
                if (!File.Exists(Path.Combine(LeDirectory, "LocaleEmulator.dll"))) missing.Add("LocaleEmulator.dll");

                // 只要有任一必要文件缺失，就中止启动并列出完整清单。
                if (missing.Count != 0)
                    throw new FileNotFoundException(".le 子目录缺少官方 Locale Emulator 文件：\r\n" + string.Join("\r\n", missing));
            }
        }

        /// <summary>
        /// 启动器自己的配置模型。
        ///
        /// 当前只保存 TargetPath。真正的 Locale Emulator 配置仍保存在
        /// 每个目标程序旁边的“.le.config”中，以保持与官方工具兼容。
        /// </summary>
        private sealed class LauncherSettings
        {
            /// <summary>当前绑定的目标 EXE 完整路径。</summary>
            public string TargetPath { get; set; }

            /// <summary>
            /// 从简单的 UTF-8“键=值”配置文件读取启动器设置。
            /// 文件不存在、行格式无效或没有 TargetPath 时返回空设置对象。
            /// </summary>
            /// <param name="path">启动器 INI 的完整路径。</param>
            /// <returns>读取结果。</returns>
            public static LauncherSettings Load(string path)
            {
                // 始终先创建设置对象，使首次运行无需特殊的 null 判断。
                var settings = new LauncherSettings();
                // 配置不存在是首次运行的正常状态。
                if (!File.Exists(path))
                    return settings;

                // Save 同样使用 UTF-8，因此这里按 UTF-8 读取。
                foreach (var line in File.ReadAllLines(path, Encoding.UTF8))
                {
                    // 跳过空行和左侧允许缩进的 # 注释行。
                    if (string.IsNullOrWhiteSpace(line) || line.TrimStart().StartsWith("#"))
                        continue;

                    // 只查找第一个等号，等号后的剩余内容全部属于值。
                    var index = line.IndexOf('=');
                    // 没有等号或键名为空的行视为无效行并忽略。
                    if (index <= 0)
                        continue;

                    // 去掉键名两侧空白。
                    var key = line.Substring(0, index).Trim();
                    // 去掉值两侧空白；路径内部空格不会受影响。
                    var value = line.Substring(index + 1).Trim();
                    // 当前只识别 TargetPath；未知键被忽略，为未来扩展保留兼容性。
                    if (string.Equals(key, "TargetPath", StringComparison.OrdinalIgnoreCase))
                        settings.TargetPath = value;
                }

                // 返回解析后的设置，即使 TargetPath 最终仍为空。
                return settings;
            }

            /// <summary>
            /// 将当前目标路径写入启动器 INI。
            /// </summary>
            /// <param name="path">启动器 INI 的完整路径。</param>
            public void Save(string path)
            {
                // 确保父目录存在。正常部署下该目录就是已验证存在的“.le”。
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                // 完整重写轻量配置，避免残留重复 TargetPath。
                // 第一行强调此文件只是启动器元数据，不替代官方 LE 配置。
                File.WriteAllLines(path, new[]
                {
                    // 该英文注释会直接写入生成的 INI 文件。
                    "# Portable LE Launcher metadata. LE profiles remain official Locale Emulator configs.",
                    // TargetPath 为空时写入空字符串，避免字符串拼接得到异常内容。
                    "TargetPath=" + (TargetPath ?? string.Empty)
                }, Encoding.UTF8);
            }
        }
    }
}
