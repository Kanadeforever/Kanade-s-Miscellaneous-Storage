# 导入标准库模块
import os               # 用于文件和目录路径操作
import sys              # 访问 Python 解释器本身和系统相关参数
import shutil           # 提供文件和文件夹的高级操作功能，如复制和删除
import subprocess       # 用于调用系统命令，例如运行 ffmpeg 工具
import uuid             # 用于生成唯一的临时文件夹名，防止重名
import configparser     # 用于读取和写入配置文件（.ini 格式）
import threading        # 导入线程模块
from datetime import datetime, timezone  # 时间处理模块，用于生成带时区的时间戳

# 第三方库（需要安装）
from PIL import Image, ImageDraw, ImageFont  # Pillow 图像处理库，用于处理截图与绘制文字

# GUI 库（内建的）
import tkinter as tk  # tkinter 是 Python 内建的 GUI 框架，适用于简单图形界面
from tkinter import filedialog, messagebox, ttk  # 导入常用子模块用于文件选择窗口、消息对话框、美化控件
from tkinter.scrolledtext import ScrolledText   # 导入日志相关控件

# 日志等级定义，用于控制输出信息的详细程度
LOG_NONE = 0       # 不显示任何日志
LOG_SIMPLE = 1     # 只显示简要信息
LOG_VERBOSE = 2    # 显示详细信息（用于调试）

# 功能：检测 ffmpeg 和 ffprobe 工具是否存在
def detect_ffmpeg_tools(use_custom=False, custom_dir=""):

    """
    检查系统中是否存在 ffmpeg 和 ffprobe 工具。
    - 支持用户提供的自定义路径。
    - 如果没找到则尝试从系统环境变量（PATH）中查找。
    返回检查报告的字符串。
    """

    script_dir = os.path.dirname(os.path.abspath(__file__))  # 当前脚本所在目录
    search_dirs = []

    if use_custom and custom_dir and os.path.isdir(custom_dir):
        search_dirs.append(custom_dir)  # 如果启用了自定义路径，优先搜索这个路径
    search_dirs.append(script_dir)  # 默认也会搜索脚本所在目录

    report = []  # 储存结果
    suffix = ".exe" if os.name == "nt" else ""  # Windows 下工具是 exe 文件，其他系统没有后缀

    for name in ["ffmpeg", "ffprobe"]:
        exec_name = name + suffix
        found = False
        checked = []  # 记录尝试检查过的路径

        for d in search_dirs:
            full = os.path.join(d, exec_name)
            checked.append(full)
            if os.path.isfile(full) and os.access(full, os.X_OK):  # 文件存在而且可执行
                report.append(f"✅ 找到 {exec_name} 于：{full}")
                found = True
                break

        if not found:
            # 试图从系统 PATH 中查找
            from_env = shutil.which(exec_name)
            if from_env:
                report.append(f"✅ 通过 PATH 找到 {exec_name} 于：{from_env}")
            else:
                report.append(f"❌ 未找到 {exec_name}（已检查：{', '.join(checked)}）")

    return "\n".join(report)

# 功能：用于获取某个工具（如 ffmpeg）的实际路径
def resolve_tool_path(tool, use_custom=False, custom_dir=""):

    """
    尝试获取指定命令的可执行路径。
    优先查找自定义目录，其次是脚本所在目录，最后查找系统环境变量 PATH。
    """

    if os.name == "nt" and not tool.endswith(".exe"):
        tool += ".exe"  # Windows 特有的 .exe 后缀

    search_dirs = []
    if use_custom and custom_dir and os.path.isdir(custom_dir):
        search_dirs.append(custom_dir)  # 优先查找自定义目录
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs.append(script_dir)

    for path in search_dirs:
        exe_path = os.path.join(path, tool)
        if os.path.isfile(exe_path) and os.access(exe_path, os.X_OK):
            return exe_path

    exe_path = shutil.which(tool)
    if exe_path:
        return exe_path

    raise FileNotFoundError(f"找不到 {tool}，请检查路径设置或确保已安装。")

# 功能：加载配置文件 config.ini，如果不存在则创建默认配置
def load_config(config_path="config.ini"):

    """
    使用 configparser 加载配置文件。
    返回一个包含默认设置和路径信息的配置对象。
    如果配置文件不存在，会自动创建默认模板。
    """

    config = configparser.ConfigParser()  # 创建配置对象

    # 默认参数组：缩略图行列数、字体大小、是否显示时间戳/序号、日志等级
    default_defaults = {
        "rows": "6",              # 网格的行数，6 行
        "cols": "6",              # 网格的列数，6 列
        "font_size": "64",        # 字体大小
        "show_timestamp": "true", # 是否显示截图时间
        "show_index": "true",     # 是否显示截图序号
        "log_level": "简单信息"    # 日志等级（无 / 简单信息 / 详细信息）
    }

    # 路径参数组：输入输出路径、是否使用自定义 ffmpeg、其路径
    default_paths = {
        "input_path": "",
        "output_path": "",
        "use_custom_ffmpeg": "false",
        "ffmpeg_path": ""
    }

    # 如果配置文件不存在，则写入默认配置
    if not os.path.exists(config_path):
        config["Defaults"] = default_defaults
        config["Paths"] = default_paths
        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)
    else:
        # 如果文件存在，则读取并确保 Defaults 和 Paths 两个分组存在
        config.read(config_path, encoding="utf-8")
        if "Defaults" not in config:
            config["Defaults"] = default_defaults
        if "Paths" not in config:
            config["Paths"] = default_paths

    return config  # 返回配置对象给主程序使用

# 功能：判断文件扩展名是否是视频格式（只支持这几种）
def is_video_file(filename):
    """
    简单判断一个文件名是否是视频类型。
    用于过滤不相关的文件（例如 .jpg、.txt）。
    """
    return filename.lower().endswith(('.mp4', '.mov', '.ts', '.avi', '.flv', '.wmv', '.asf', '.mpg', '.mpeg', '.3gp', '.mkv', '.rm', '.rmvb', '.qt'))

# 功能：日志打印函数，用于控制信息输出（无 / 简单 / 详细）
def log(msg, level, current_level):

    """
    打印日志信息（条件是：当前日志等级 >= 消息等级）。
    - level：这条消息属于哪个级别（简单信息 or 详细信息）
    - current_level：当前程序的日志输出等级
    """

    if current_level >= level:
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        print(f"{timestamp} {msg}")  # 满足条件才打印

# 功能：使用 ffmpeg 提取视频的多帧画面（用于拼图）
def extract_frames_ffmpeg(video_path, out_dir, total_frames, start_offset,
                          ffmpeg, ffprobe, log_level):

    """
    从视频文件中按照间隔提取多张静态帧图像（jpg格式）。
    参数解释：
    - video_path：视频文件的完整路径
    - out_dir：保存帧图像的临时文件夹
    - total_frames：总共要提取的帧数
    - start_offset：从视频开头的第几秒开始取样（避免黑屏等）
    - ffmpeg / ffprobe：工具路径
    - log_level：控制是否打印日志信息
    返回值：列表，包含每帧的（图像路径，对应的时间戳）
    """
    os.makedirs(out_dir, exist_ok=True)  # 创建输出目录，若已存在则忽略

    # 使用 ffprobe 获取视频时长
    result = subprocess.run([
        ffprobe, '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    try:
        duration = float(result.stdout)  # 得到视频总时长（单位：秒）
        # log(f"[INFO] 视频时长: {duration:.2f}s", LOG_VERBOSE, log_level)
        log(f"[DETAIL] 视频总时长为 {duration:.2f} 秒，将提取 {total_frames} 帧", LOG_VERBOSE, log_level)
        log(f"[DETAIL] 每帧间隔约为 {(duration - start_offset) / total_frames:.2f} 秒，起始偏移 {start_offset}s", LOG_VERBOSE, log_level)
    except ValueError:
        raise RuntimeError("无法解析视频时长。")

    # 计算每帧的提取时间点（平均分布）
    interval = max(0.1, (duration - start_offset) / total_frames)
    timestamps = []
    for i in range(total_frames):
        ts = start_offset + i * interval  # 当前帧对应的时间戳（秒数）
        out_file = os.path.join(out_dir, f"frame_{i:02d}.jpg")
        cmd = [ffmpeg, '-ss', str(ts), '-i', video_path, '-frames:v', '1', '-q:v', '2', out_file, '-y']
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        timestamps.append((out_file, ts))

    return timestamps

# 功能：将多张视频帧拼接成一张缩略图（可加标注）
def create_thumbnail(frame_data, rows, cols, max_width, log_level,
                     show_timestamp=True, show_index=False, font_size=64):

    """
    将提取出的若干图像拼接成一个大缩略图，支持添加时间戳和帧序号。
    - frame_data：帧图像和时间戳的列表
    - rows / cols：拼图行列数
    - max_width：缩略图最大宽度，超出后会等比例缩放
    - show_timestamp / show_index：是否标注截图时间 / 编号
    - font_size：标注字体大小
    返回值：拼接后生成的 Pillow 图像对象
    """

    images = []
    # 输出日志
    log(f"[DETAIL] 开始拼接 {len(frame_data)} 张图像为 {rows}×{cols} 网格", LOG_VERBOSE, log_level)

    for idx, (path, ts) in enumerate(frame_data):
        log(f"[DETAIL] 处理第 {idx+1:02d} 帧：{path}", LOG_VERBOSE, log_level)
        img = Image.open(path).convert("RGBA")  # 打开图像并转换为带透明通道
        draw = ImageDraw.Draw(img)

        # 加载字体，如果加载失败就用默认字体
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except:
            font = ImageFont.load_default()

        labels = []
        if show_index:
            labels.append(("left-top", f"{idx+1:02d}"))  # 格式化为两位数编号
            log(f"[DETAIL] 添加序号标签：{idx+1:02d}", LOG_VERBOSE, log_level)  # 输出日志
        if show_timestamp:
            ts_text = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S.%f')[:-3]
            labels.append(("right-bottom", ts_text))  # 精确到毫秒的时间戳
            log(f"[DETAIL] 添加时间戳标签：{ts_text}", LOG_VERBOSE, log_level)  # 输出日志

        for pos, text in labels:
            # 尝试获取文字宽高（兼容不同 Pillow 版本）
            try:
                bbox = font.getbbox(text)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except:
                tw, th = font.getsize(text)

            pad = 10      # 内边距
            margin = 20   # 文字到图像边缘的边距

            if pos == "left-top":
                bg_x1, bg_y1 = margin, margin
            elif pos == "right-bottom":
                bg_x1 = img.width - tw - 2 * pad - margin
                bg_y1 = img.height - th - 2 * pad - margin
            else:
                continue

            bg_x2 = bg_x1 + tw + 2 * pad
            bg_y2 = bg_y1 + th + 2 * pad

            # 对字体进行垂直修正，使其居中美观
            try:
                ascent, descent = font.getmetrics()
                v_shift = ((ascent + descent) - th) // 2
            except:
                v_shift = 0

            x = bg_x1 + (bg_x2 - bg_x1 - tw) // 2
            y = bg_y1 + (bg_y2 - bg_y1 - th) // 2 - v_shift

            # 叠加半透明背景框
            overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
            odraw = ImageDraw.Draw(overlay)
            odraw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(255, 255, 255, 200))  # 白色半透明底
            odraw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], outline="black", width=2)   # 黑边框
            img = Image.alpha_composite(img, overlay)

            # 添加白色文字并描边（黑色描边模拟阴影）
            draw = ImageDraw.Draw(img)
            for dx in [-1, 1]:
                for dy in [-1, 1]:
                    draw.text((x + dx, y + dy), text, font=font, fill="black")
            draw.text((x, y), text, font=font, fill="white")

        images.append(img.convert("RGB"))

    if not images:
        raise FileNotFoundError("未找到任何帧图像。")

    # 获取单张图像大小，创建合成图像画布
    w, h = images[0].size
    thumb = Image.new("RGB", (cols * w, rows * h))

    # 将每张帧图像粘贴到对应的位置
    for idx, frame in enumerate(images):
        x = (idx % cols) * w
        y = (idx // cols) * h
        thumb.paste(frame, (x, y))

    # 如果拼图太宽，就等比缩放
    if thumb.width > max_width:
        ratio = max_width / thumb.width
        thumb = thumb.resize((max_width, int(thumb.height * ratio)),
                             getattr(Image, 'Resampling', Image).LANCZOS)
        log(f"[INFO] 缩略图已缩放至宽度 {max_width}px", LOG_VERBOSE, log_level)
    log(f"[DETAIL] 最终缩略图尺寸：{thumb.width}x{thumb.height}px", LOG_VERBOSE, log_level)

    return thumb

# 功能：对单个视频生成缩略图（包含截图提取 + 拼图 + 保存）
def generate_thumbnail(video_path, out_dir, rows, cols,
                       show_timestamp, show_index, font_size,
                       ffmpeg_path, ffprobe_path, log_level):

    """
    综合调用截图提取函数和拼图函数，为单个视频生成一个预览缩略图。
    参数说明：
    - video_path：输入的视频路径
    - out_dir：输出图像目录
    - rows / cols：缩略图排布行列数
    - show_timestamp / show_index：是否标注时间戳或帧序号
    - font_size：文字大小
    - ffmpeg_path / ffprobe_path：工具路径
    - log_level：日志等级
    """

    # 使用 UUID 生成一个临时文件夹名，用于保存中间帧图像
    temp_dir = f"thumb_frames_{uuid.uuid4().hex[:8]}"
    log(f"[DETAIL] 生成临时帧图像目录：{temp_dir}", LOG_VERBOSE, log_level)
    log(f"[INFO] 正在处理：{video_path}", LOG_SIMPLE, log_level)
    try:
        frame_data = extract_frames_ffmpeg(video_path, temp_dir, rows * cols, 5.0,
                                           ffmpeg_path, ffprobe_path, log_level)
        thumb = create_thumbnail(frame_data, rows, cols, 4096, log_level,
                                 show_timestamp, show_index, font_size)
        # 生成输出文件名，例如 video1_preview.jpg
        name = os.path.splitext(os.path.basename(video_path))[0] + "_preview.jpg"
        out_path = os.path.join(out_dir, name)
        thumb.save(out_path)
        log(f"[INFO] 已完成：{video_path}", LOG_SIMPLE, log_level)  # ✅ 成功提示
        log(f"[DETAIL] 已保存缩略图至：{out_path}", LOG_VERBOSE, log_level)
    except Exception as e:
        log(f"[ERROR] 处理失败：{video_path}，原因：{str(e)}", LOG_SIMPLE, log_level)  # ✅ 错误输出
        raise  # 保持原有异常逻辑
    finally:
        # 清理临时目录，释放空间
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log(f"[DETAIL] 已清理临时目录：{temp_dir}", LOG_VERBOSE, log_level)

# 功能：批量处理输入路径，可以是单个文件或整个文件夹
def handle_batch(input_path, out_dir, rows, cols,
                 show_timestamp, show_index, font_size,
                 ffmpeg_path, ffprobe_path, log_level):

    """
    判断输入路径是文件还是文件夹：
    - 如果是视频文件就直接处理；
    - 如果是文件夹，则批量处理其中所有视频文件；
    - 其他情况抛出错误。
    """

    log(f"[DETAIL] 扫描输入路径：{input_path}", LOG_VERBOSE, log_level)

    success_count = 0
    skip_nonvideo = 0
    skip_failed = 0

    def process_file(filepath, filename=None):
        nonlocal success_count, skip_failed, skip_nonvideo
        try:
            if is_video_file(filepath):
                generate_thumbnail(filepath, out_dir, rows, cols,
                                   show_timestamp, show_index, font_size,
                                   ffmpeg_path, ffprobe_path, log_level)
                success_count += 1
            else:
                log(f"[DETAIL] 跳过非视频文件：{filename or filepath}", LOG_VERBOSE, log_level)
                skip_nonvideo += 1
        except Exception as e:
            log(f"[DETAIL] 跳过异常视频：{filename or filepath}，原因：{str(e)}", LOG_VERBOSE, log_level)
            skip_failed += 1

    if os.path.isfile(input_path):
        process_file(input_path)
    elif os.path.isdir(input_path):
        for file in os.listdir(input_path):
            full_path = os.path.join(input_path, file)
            if os.path.isfile(full_path):
                process_file(full_path, file)
    else:
        raise FileNotFoundError("输入路径无效。")

    # 输出统计摘要
    total = success_count + skip_failed + skip_nonvideo
    log(f"[INFO] 批量处理完成：", LOG_SIMPLE, log_level)
    log(f"- 成功生成：{success_count} 个视频的预览图", LOG_SIMPLE, log_level)
    if skip_nonvideo > 0:
        log(f"- 跳过非视频：{skip_nonvideo} 个", LOG_SIMPLE, log_level)
    if skip_failed > 0:
        log(f"- 跳过异常视频：{skip_failed} 个", LOG_SIMPLE, log_level)
    if total == 0:
        log(f"- 未处理任何文件。", LOG_SIMPLE, log_level)

# === 重定向控制台输出到 GUI 文本框 ===
class TextRedirector:
    def __init__(self, widget):
        self.widget = widget

    def write(self, msg):
        self.widget.configure(state="normal")
        self.widget.insert("end", msg.rstrip() + "\n")
        self.widget.see("end")
        self.widget.configure(state="disabled")

    def flush(self):
        pass # 与标准输出接口兼容

# 程序主入口，构建图形界面
def start_gui():

    """
    初始化图形界面（GUI）窗口，绑定配置、控件、按钮逻辑等。
    """

    try:
        # 对于高 DPI 显示屏，提升窗口清晰度（仅在 Windows 下有效）
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass  # 若系统不支持，则忽略

    # 加载配置文件（默认是 config.ini）
    config = load_config()
    defaults = config["Defaults"]
    paths = config["Paths"]

    # 创建主窗口
    root = tk.Tk()
    root.title("视频预览图生成器")      # 窗口标题
    # root.geometry("850x520")                # 初始窗口大小
    root.geometry("850x750")                # 初始窗口大小
    root.minsize(600, 480)                  # 最小窗口大小
    root.resizable(False, False)             # 允许左右拉伸，禁止上下拉伸

    # 配置窗口栅格，使内容自动扩展填充
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # 创建主框架（Frame），加内边距用于放控件
    frm = ttk.Frame(root, padding=10)
    frm.grid(row=0, column=0, sticky="nsew")
    for i in range(2):
        frm.grid_columnconfigure(i, weight=1)  # 两列等比分布

    # 定义绑定变量（与界面控件同步）
    input_var = tk.StringVar(value=paths.get("input_path", ""))             # 输入路径
    output_var = tk.StringVar(value=paths.get("output_path", ""))           # 输出目录
    ffmpeg_dir_var = tk.StringVar(value=paths.get("ffmpeg_path", ""))       # 自定义 ffmpeg 目录
    use_custom_ffmpeg = tk.BooleanVar(value=paths.get("use_custom_ffmpeg", "false").lower() == "true")  # 是否启用

    timestamp_var = tk.BooleanVar(value=defaults.get("show_timestamp", "true").lower() == "true")  # 显示时间戳
    index_var = tk.BooleanVar(value=defaults.get("show_index", "true").lower() == "true")          # 显示编号
    font_size_var = tk.StringVar(value=defaults.get("font_size", "64"))                            # 字号
    log_level_var = tk.StringVar(value=defaults.get("log_level", "简单信息"))                      # 日志等级
    row_var = tk.StringVar(value=defaults.get("rows", "6"))                                        # 行数
    col_var = tk.StringVar(value=defaults.get("cols", "6"))                                        # 列数
    
    # 选择路径（文件或文件夹），并将选择结果写入对应的变量
    def browse_path(var, folder=False):
    # 弹出文件夹选择或文件选择对话框
        path = filedialog.askdirectory() if folder else filedialog.askopenfilename()
        if path:
        # 将选择结果写入绑定的变量（StringVar 类型）
            var.set(path)

    # 打开输出目录（在资源管理器 / Finder / 文件管理器中打开）
    def open_output_dir(path):
        path = path.strip()  # 去除首尾空白字符
        if not path or not os.path.exists(path):
            # 如果路径为空或不存在，弹出错误提示框
            messagebox.showerror("错误", "输出目录无效或不存在。")
            return
        try:
            if sys.platform.startswith('darwin'):
                subprocess.run(['open', path])  # macOS 系统
            elif os.name == 'nt':
                os.startfile(path)  # Windows 系统
            elif os.name == 'posix':
                subprocess.run(['xdg-open', path])  # Linux 系统
            else:
            # 若不属于以上系统，提示不支持
                messagebox.showwarning("提示", "不支持此平台的目录打开操作。")
        except Exception as e:
            # 弹出错误对话框，显示异常信息
            messagebox.showerror("打开失败", str(e))

    # 保存当前界面上所有配置项到 config.ini 文件
    def save_current_config():
        # 保存缩略图设置（行列、字体大小、是否显示时间戳与序号、日志等级）
        config["Defaults"]["rows"] = row_var.get()
        config["Defaults"]["cols"] = col_var.get()
        config["Defaults"]["font_size"] = font_size_var.get()
        config["Defaults"]["show_timestamp"] = str(timestamp_var.get()).lower()
        config["Defaults"]["show_index"] = str(index_var.get()).lower()
        config["Defaults"]["log_level"] = log_level_var.get()

        # 保存路径设置
        config["Paths"]["input_path"] = input_var.get()
        config["Paths"]["output_path"] = output_var.get()
        config["Paths"]["ffmpeg_path"] = ffmpeg_dir_var.get()
        config["Paths"]["use_custom_ffmpeg"] = str(use_custom_ffmpeg.get()).lower()

        # 写入到 config.ini 文件中
        with open("config.ini", "w", encoding="utf-8") as f:
            config.write(f)

    # 控制“自定义 ffmpeg 路径”输入框的启用/禁用状态
    def toggle_ffmpeg_fields():
        if use_custom_ffmpeg.get():
            # 启用：显示路径输入框和浏览按钮
            ffmpeg_path_frame.grid()
            ffmpeg_entry.config(state="normal")
            ffmpeg_browse_btn.config(state="normal")
        else:
            # 禁用：锁定用户编辑防止误操作
            ffmpeg_entry.config(state="disabled")
            ffmpeg_browse_btn.config(state="disabled")

    # 当点击“生成缩略图”按钮时执行的主逻辑
    def run():
        try:
            # 获取输入路径并检查其有效性
            input_path = input_var.get().strip()
            if not input_path or not os.path.exists(input_path):
                messagebox.showerror("错误", "请提供有效的输入路径。")
                return

            # 推断默认输出路径（与输入路径相同或其父目录）
            if os.path.isfile(input_path):
                default_output = os.path.dirname(input_path)
            elif os.path.isdir(input_path):
                default_output = input_path
            else:
                messagebox.showerror("错误", "输入路径无法识别为文件或文件夹。")
                return

            # 如果未手动设定输出路径，则使用默认值
            if not output_var.get().strip():
                output_var.set(default_output)
            output_path = output_var.get().strip()

            # 获取行列数（拼图结构）
            rows = int(row_var.get())
            cols = int(col_var.get())
            # 字号转为整数，失败时使用默认值 64
            try:
                font_size = int(font_size_var.get())
            except:
                font_size = 64
            # 获取选项值
            show_ts = timestamp_var.get()
            show_idx = index_var.get()
            # 将日志等级的中文转换为对应数字常量
            log_map = {"无": LOG_NONE, "简单信息": LOG_SIMPLE, "详细信息": LOG_VERBOSE}
            log_level = log_map.get(log_level_var.get(), LOG_SIMPLE)

            # 获取 ffmpeg 路径，如果找不到则提示错误
            try:
                ffmpeg_path = resolve_tool_path("ffmpeg", use_custom_ffmpeg.get(), ffmpeg_dir_var.get())
                ffprobe_path = resolve_tool_path("ffprobe", use_custom_ffmpeg.get(), ffmpeg_dir_var.get())
            except FileNotFoundError as e:
                messagebox.showerror("FFmpeg 错误", str(e))
                return

            # 调用主处理函数处理单个或批量视频
            handle_batch(input_path, output_path, rows, cols,
                         show_ts, show_idx, font_size,
                         ffmpeg_path, ffprobe_path, log_level)

            # 提示成功
            messagebox.showinfo("完成", "处理完成！请查看输出目录。")
        except Exception as e:
            # 捕获运行期间的任何异常并提示
            messagebox.showerror("错误", str(e))

    # 让用户选择路径并赋值给 input 路径变量
    def select_path(mode):
        path = filedialog.askopenfilename() if mode == "file" else filedialog.askdirectory()
        if path:
            input_var.set(path)

    # 创建主 Frame 组件用于放置控件
    frm = ttk.Frame(root, padding=10)
    frm.grid(row=0, column=0, sticky="nsew")

    # 设置根窗口和主框架的布局自适应（随窗口缩放）
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)
    frm.grid_columnconfigure(0, weight=1)
    frm.grid_columnconfigure(1, weight=1)

    # 输入路径区域包含文本框 + 按钮组
    input_frame = ttk.Frame(frm)
    input_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 5))
    input_frame.grid_columnconfigure(0, weight=1)  # 输入框占据主要宽度

    # 用户可以手动输入，也可以通过按钮选择路径
    ttk.Entry(input_frame, textvariable=input_var).grid(row=0, column=0, sticky="ew")

    # 右侧按钮：选择视频文件 / 文件夹
    button_group = ttk.Frame(input_frame)
    button_group.grid(row=0, column=1, padx=(5, 0))
    ttk.Button(button_group, text="选择文件", width=10, command=lambda: select_path("file")).grid(row=0, column=0, padx=(0, 5))
    ttk.Button(button_group, text="选择文件夹", width=10, command=lambda: select_path("folder")).grid(row=0, column=1)

    # 输出路径设置
    ttk.Label(frm, text="输出目录：").grid(row=2, column=0, sticky="w", pady=(10, 2))

    out_frame = ttk.Frame(frm)
    out_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 5))
    out_frame.grid_columnconfigure(0, weight=1)

    # 输出目录输入框
    ttk.Entry(out_frame, textvariable=output_var).grid(row=0, column=0, sticky="ew")

    # 输出目录相关按钮：选择目录 / 打开目录
    ttk.Button(out_frame, text="选择目录", width=10, command=lambda: browse_path(output_var, folder=True)).grid(row=0, column=1, padx=(5, 0))
    ttk.Button(out_frame, text="打开目录", width=10, command=lambda: open_output_dir(output_var.get())).grid(row=0, column=2, padx=(5, 0))

    # 缩略图行列数设置（拼图大小）
    grid = ttk.Frame(frm)
    grid.grid(row=4, column=0, pady=(10, 0), sticky="w", columnspan=2)
    ttk.Label(grid, text="行：").grid(row=0, column=0)
    ttk.Entry(grid, textvariable=row_var, width=5).grid(row=0, column=1, padx=(0, 10))
    ttk.Label(grid, text="列：").grid(row=0, column=2)
    ttk.Entry(grid, textvariable=col_var, width=5).grid(row=0, column=3)

    # 显示选项：是否显示时间戳、编号、字号与日志等级
    options_frame = ttk.Frame(frm)
    options_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    for i in range(6):
        options_frame.grid_columnconfigure(i, weight=1)  # 自动调整六列宽度

    # 控制是否显示时间和编号
    ttk.Checkbutton(options_frame, text="显示截图时间戳", variable=timestamp_var).grid(
        row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Checkbutton(options_frame, text="显示截图序号", variable=index_var).grid(
        row=0, column=1, sticky="w", padx=(0, 20))

    # 控制字体大小
    ttk.Label(options_frame, text="字号：").grid(row=0, column=2, sticky="e")
    ttk.Entry(options_frame, textvariable=font_size_var, width=6).grid(
        row=0, column=3, padx=(5, 20), sticky="w")

    # 控制日志信息等级
    ttk.Label(options_frame, text="日志等级：").grid(row=0, column=4, sticky="e")
    ttk.Combobox(options_frame, textvariable=log_level_var,
                values=["无", "简单信息", "详细信息"], state="readonly", width=10).grid(
        row=0, column=5, sticky="w")

    # 用户可勾选是否启用自定义 ffmpeg 目录
    ttk.Checkbutton(frm, text="使用自定义 ffmpeg 路径", variable=use_custom_ffmpeg,
                    command=lambda: toggle_ffmpeg_fields()).grid(row=9, column=0, sticky="w", pady=(10, 0))

    # 当启用时，显示 ffmpeg 路径输入框 + 选择按钮
    ffmpeg_path_frame = ttk.Frame(frm)
    ffmpeg_path_frame.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(5, 0))
    ffmpeg_path_frame.grid_columnconfigure(0, weight=1)

    # ffmpeg 路径文本框
    ffmpeg_entry = ttk.Entry(ffmpeg_path_frame, textvariable=ffmpeg_dir_var)
    ffmpeg_entry.grid(row=0, column=0, sticky="ew")

    # 浏览目录按钮
    ffmpeg_browse_btn = ttk.Button(ffmpeg_path_frame, text="选择目录", command=lambda: browse_path(ffmpeg_dir_var, folder=True))
    ffmpeg_browse_btn.grid(row=0, column=1, padx=(5, 0))

    # 功能按钮区
    # 创建用于放置底部功能按钮的 Frame 容器（称为 button_frame）
    button_frame = ttk.Frame(frm)
    # 将该按钮容器放在主界面的第 11 行，占据两列，垂直方向加上下边距 20 像素
    button_frame.grid(row=11, column=0, columnspan=2, pady=20)

    # 为按钮容器设置三行的行间距（每一行下方留出适当空隙）
    for i in range(3):
        button_frame.grid_rowconfigure(i, pad=5)

    # 设置按钮区域的列为可扩展（即当窗口变宽时，这一列会跟着拉伸）
    button_frame.grid_columnconfigure(0, weight=1)

    # 定义按钮的统一样式参数（目前仅设置了宽度和边距）
    btn_style = {"width": 20, "padding": 5}     # 所有按钮宽度设置为 20 个字符单位
    						                    # 注：这个 padding 未被实际用于 ttk.Button，可忽略或用于自定义风格

    ttk.Button(button_frame, text="生成缩略图",
               command=lambda: threading.Thread(target=run, daemon=True).start(),
               width=btn_style["width"]).grid(row=0, column=0, pady=(0, 5))

    # 检查 ffmpeg 工具状态
    ttk.Button(button_frame, text="检测 FFmpeg 工具",
               command=lambda: messagebox.showinfo("检测结果", detect_ffmpeg_tools(use_custom_ffmpeg.get(), ffmpeg_dir_var.get())),
               width=btn_style["width"]).grid(row=1, column=0, pady=5)

    # 保存当前设置
    ttk.Button(button_frame, text="保存配置", command=save_current_config,
               width=btn_style["width"]).grid(row=2, column=0, pady=5)

    # 退出程序前保存设置
    ttk.Button(button_frame, text="退出", command=lambda: (save_current_config(), root.destroy()),
               width=btn_style["width"]).grid(row=3, column=0, pady=(5, 0))

    # 创建日志文本框控件
    log_box = ScrolledText(frm, height=8, state="disabled", wrap="word", font=("Consolas", 10))
    log_box.grid(row=99, column=0, columnspan=2, sticky="nsew", pady=(15, 0))

    # 重定向 stdout/stderr 到日志框中
    sys.stdout = TextRedirector(log_box)
    sys.stderr = TextRedirector(log_box)  # 可选：stderr 也输出到日志框

    # 初始化 ffmpeg 选项框显示状态（控制可编辑性）
    toggle_ffmpeg_fields()

    # 启动主窗口事件循环（GUI 正式运行）
    root.mainloop()

if __name__ == "__main__":
    start_gui()
