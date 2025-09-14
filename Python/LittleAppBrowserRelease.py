# -*- coding: utf-8 -*-
import sys
import argparse
import importlib.util
import logging
import shutil
import json
import traceback
from pathlib import Path
import tkinter as tk

HOTKEYS_PLIGIN_CONTENT='''\
def run(window, api):
    def inject():
        js = """
            setTimeout(() => {
                window.addEventListener('keydown', function(e) {
                    if (e.key === 'F11') window.pywebview.api.toggle_fullscreen();
                    if (e.key === 'F5') location.reload();
                });
            }, 100);
        """
        window.evaluate_js(js)
    window.events.loaded += inject
'''
RESIZE_NOTIFIER_PLIGIN_CONTENT='''\
def run(window, api):
    def inject():
        js = """
            new ResizeObserver(() => {
                window.dispatchEvent(new Event('resize'));
            }).observe(document.body);
    """
        window.evaluate_js(js)
    window.events.loaded += inject
'''
PIXEL_RATIO_PLIGIN_CONTENT='''\
def run(window, api):
    def inject():
        js = "document.body.style.zoom = window.devicePixelRatio || 1;"
        window.evaluate_js(js)
    window.events.loaded += inject
'''
TEMPLATE_NAME = "config_template.toml"
TEMPLATE_CONTENT = """\
# config_template.toml - WebView 应用配置模板
[App]
# 必填: 要加载的网页地址
URL             = "https://example.com/"
# 窗口标题，默认 "Web App"
Title           = "我的 Web 应用"
# 初始宽度和高度，单位 像素
Width           = 1024
Height          = 768
# 是否全屏，true/false
Fullscreen      = false
# 是否总在最上层，true/false
OnTop           = false
# 是否允许拖拽调整大小，true/false
Resizable       = true
# 是否去除系统边框和标题栏，true/false
Frameless       = false
# 是否记忆窗口大小，true/false
RememberSize    = false
# 关闭时是否清理缓存（不删除其他数据），true/false
ClearCache      = true

[Modules]
hotkeys         = true
resize_notifier = true
pixel_ratio     = true
"""
TEMPLATE_PLUGIN_NAME = "_template.py"
TEMPLATE_PLUGIN_CONTENT = '''\
# 插件模板：template.py
# 插件模板不可以放进【modules】目录内，否则会出问题
# 编写好的插件则需要放进【modules】目录内，否则不加载
# ----------------------------------------
# 每个插件必须定义一个名为 run(window, api) 的函数
# window: 当前 WebView 窗口对象，可用于注入 JS、绑定事件等
# api: 主脚本提供的 API 实例，可调用如 toggle_fullscreen() 等方法
# 插件运行在隔离作用域中，已注入以下变量供使用：
# - logging, json, Path, sys
# - config_data, app_cfg, mod_cfg
# - data_dir, window, api
# - atomic_write, enable_high_dpi

def run(window, api):
    # 示例：注入 JS（在页面加载完成后执行）
    def inject():
        js = """
            console.log("插件 template.py 已注入");
            // 可在此添加自定义 JS 逻辑
        """
        window.evaluate_js(js)

    window.events.loaded += inject
    logging.info("插件 template.py 已加载")

    # 示例：写入插件数据文件
    output = data_dir / "template_output.json"
    atomic_write(output, json.dumps({"status": "ok"}))

    # 示例：根据配置做出行为
    if app_cfg.get("Fullscreen"):
        api.toggle_fullscreen()

    # 示例：创建一个 Tk 弹窗（需启用 DPI 感知）
    # enable_high_dpi()
    # import tkinter as tk
    # root = tk.Tk(); root.title("插件弹窗")
    # tk.Label(root, text="Hello from plugin!").pack()
    # root.mainloop()
'''

EXE_DIR = (
    Path(sys.executable).parent if getattr(sys, "frozen", False)
    else Path(__file__).parent.resolve()
)

def dump_toml(data: dict) -> str:
    def format_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        elif isinstance(v, int):
            return str(v)
        elif isinstance(v, str):
            return f'"{v}"'
        else:
            raise TypeError(f"不支持的类型：{type(v)}")

    lines = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, val in values.items():
            lines.append(f"{key} = {format_value(val)}")
        lines.append("")  # 空行分隔
    return "\n".join(lines)

def atomic_write(path, content, mode="w", encoding="utf-8"):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open(mode, encoding=encoding) as f:
        f.write(content)
    try:
        tmp.replace(path)
    except Exception as e:
        logging.error(f"写入失败：{e}")
        tmp.unlink(missing_ok=True)
        raise

def enable_high_dpi():
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

def show_error(title, message):
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("500x500")
    win.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))

    txt = tk.Text(win, wrap="word")
    txt.insert("1.0", message)
    txt.config(state="disabled")
    txt.pack(fill="both", expand=True, padx=10, pady=10)

    btn = tk.Button(win, text="退出", command=lambda: sys.exit(1))
    btn.pack(pady=10)
    win.mainloop()

def handle_exception(exc):
    msg = "".join(traceback.format_exception_only(type(exc), exc))
    show_error("错误", msg)

def check_dependencies():
    missing = []
    if sys.version_info < (3, 11):
        missing.append(f"Python ≥ 3.11   (当前 {sys.version_info.major}.{sys.version_info.minor})")
    if importlib.util.find_spec("tomllib") is None:
        missing.append("tomllib (Python 3.11+ 内置)")
    try:
        import tkinter
    except ImportError:
        missing.append("Tkinter (GUI 选择框)")
    if not importlib.util.find_spec("webview"):
        missing.append("pywebview (pip install pywebview)")
    if missing:
        show_error("缺少依赖组件", "检测到以下必要组件缺失或版本不符：\n\n" +
                   "\n".join(f"• {m}" for m in missing))
        sys.exit(1)

def parse_args():
    p = argparse.ArgumentParser(description="启动 WebView 应用")
    p.add_argument("-c", "--config", type=Path, help="指定 .toml 配置文件")
    return p.parse_args()

def find_or_create_config(explicit):
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(f"找不到指定配置文件：{explicit}")
        return explicit
    tomls = sorted(EXE_DIR.glob("*.toml"))
    if not tomls:
        tpl = EXE_DIR / TEMPLATE_NAME
        atomic_write(tpl, TEMPLATE_CONTENT)
        data_dir = prepare_data_dir(tpl)
        temp_path = data_dir / TEMPLATE_PLUGIN_NAME
        atomic_write(temp_path, TEMPLATE_PLUGIN_CONTENT)
        show_error("配置缺失", f"未检测到 .toml 配置，已生成模板：{tpl.name}\n\n请编辑后重新启动程序。")
        sys.exit(0)
    if len(tomls) == 1:
        return tomls[0]
    enable_high_dpi()
    root = tk.Tk(); root.title("选择配置文件"); root.geometry("500x500")
    lst = tk.Listbox(root, font=("Arial", 12))
    for f in tomls:
        # lst.insert("end", f.name)
        lst.insert("end", f.stem)
    lst.pack(fill="both", expand=True); lst.focus_set()
    chosen = {"file": None}
    def confirm(event=None):
        sel = lst.curselection()
        if sel:
            # chosen["file"] = EXE_DIR / lst.get(sel[0])
            chosen["file"] = EXE_DIR / (lst.get(sel[0]) + ".toml")
            root.destroy()
    lst.bind("<Double-Button-1>", confirm)
    lst.bind("<Return>", confirm)
    tk.Button(root, text="选择", command=confirm).pack(pady=5)
    root.mainloop()
    if not chosen["file"]:
        sys.exit("未选择配置，程序退出。")
    return chosen["file"]

def validate_config(app, path):
    allowed = {"URL", "Title", "Width", "Height", "Fullscreen",
               "OnTop", "Resizable", "Frameless", "RememberSize", "ClearCache"}
    errors = []
    for k in app:
        if k not in allowed:
            errors.append(f"未知字段：{k}")
    url = app.get("URL", "")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        errors.append("App.URL 必须是以 http:// 或 https:// 开头的字符串")
    title = app.get("Title", "Web App")
    if not isinstance(title, str):
        errors.append("App.Title 必须是字符串")
    for key in ("Width", "Height"):
        v = app.get(key, None)
        if not isinstance(v, int) or v <= 0:
            errors.append(f"App.{key} 必须是正整数")
    for key in ("Fullscreen", "OnTop", "Resizable", "Frameless", "RememberSize", "ClearCache"):
        v = app.get(key, None)
        if not isinstance(v, bool):
            errors.append(f"App.{key} 必须是 true/false")
    if errors:
        raise ValueError(f"[{path.name}] 配置校验失败：\n" + "\n".join(errors))
    return app

def load_config(path):
    import tomllib
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    app = data.get("App", {})
    mods = data.get("Modules", {})
    if "URL" not in app:
        raise KeyError(f"[{path.name}] 中缺少必填项 App.URL")
    validate_config(app, path)
    return app, mods, data

def prepare_data_dir(cfg_path):
    dname = cfg_path.with_suffix("").name + "_data"
    # p = cfg_path.parent / dname
    p = EXE_DIR / dname
    p.mkdir(parents=True, exist_ok=True)
    return p

from datetime import datetime

def setup_logging(data_dir):
    # 生成时间戳文件名
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    logfile = data_dir / f"AppLog-{ts}.log"

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8")]
    )
    logging.info(f"日志已创建：{logfile.name}")

    # 清理旧日志（保留最近 10 个）
    logs = sorted(data_dir.glob("log-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_log in logs[10:]:
        try:
            old_log.unlink()
            logging.info(f"已删除旧日志：{old_log.name}")
        except Exception as e:
            logging.warning(f"删除日志失败：{old_log.name} - {e}")
    logging.info("应用启动")

def generate_sample_plugins(modules_dir, config_path, config_data):
    samples = {
        "hotkeys.py": HOTKEYS_PLIGIN_CONTENT,
        "resize_notifier.py": RESIZE_NOTIFIER_PLIGIN_CONTENT,
        "pixel_ratio.py": PIXEL_RATIO_PLIGIN_CONTENT
    }

    modules_dir.mkdir(parents=True, exist_ok=True)
    updated = False
    mod_cfg = config_data.get("Modules", {})
    for name, content in samples.items():
        path = modules_dir / name
        key = name.removesuffix(".py")
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            logging.info(f"已生成内置插件：{name}；可供编写插件时参考。")
        if key not in mod_cfg:
            mod_cfg[key] = True
            updated = True
    if updated:
        config_data["Modules"] = mod_cfg
        with config_path.open("wb") as fp:
            fp.write(dump_toml(config_data).encode("utf-8"))

        logging.info("已更新配置文件中的插件启用状态")

def sync_plugin_config(modules_dir, mod_cfg):
    existing = {f.stem for f in modules_dir.glob("*.py")}
    updated = False
    for name in list(mod_cfg):
        if name not in existing:
            logging.warning(f"配置中启用了不存在的插件：{name}，已自动禁用")
            mod_cfg[name] = False
            updated = True
    return updated

def load_plugins(modules_dir, window, api, mod_cfg, config_data, app_cfg, data_dir):
    for mod_file in sorted(modules_dir.glob("*.py")):
        name = mod_file.stem
        if mod_cfg.get(name, True):
            logging.info(f"注入插件：{mod_file.name}")
            try:
                code = mod_file.read_text(encoding="utf-8")
                scope = {
                    "logging": logging,
                    "json": json,
                    "Path": Path,
                    "sys": sys,
                    "config_data": config_data,
                    "app_cfg": app_cfg,
                    "mod_cfg": mod_cfg,
                    "data_dir": data_dir,
                    "window": window,
                    "api": api,
                    "atomic_write": atomic_write,
                    "enable_high_dpi": enable_high_dpi
                }
                exec(code, scope)
                if "run" in scope and callable(scope["run"]):
                    scope["run"](window, api)
                    logging.info(f"插件加载成功：{mod_file.name}")
                else:
                    logging.warning(f"插件缺少 run() 方法：{mod_file.name}")
            except Exception as e:
                logging.error(f"插件加载失败：{mod_file.name} - {e}")
        else:
            logging.info(f"插件已禁用：{mod_file.name}")

def main():
    try:
        check_dependencies()
        import webview

        args        = parse_args()
        cfg_path    = find_or_create_config(args.config)
        app_cfg, mod_cfg, config_data = load_config(cfg_path)
        data_dir    = prepare_data_dir(cfg_path)
        setup_logging(data_dir)

        title      = app_cfg["Title"]
        url        = app_cfg["URL"]
        width      = app_cfg["Width"]
        height     = app_cfg["Height"]
        fullscreen = app_cfg["Fullscreen"]
        on_top     = app_cfg["OnTop"]
        resizable  = app_cfg["Resizable"]
        frameless  = app_cfg["Frameless"]
        remember   = app_cfg["RememberSize"]
        clearcache = app_cfg["ClearCache"]
        size_file  = data_dir / "window_size.json"

        if remember and size_file.exists():
            try:
                j = json.loads(size_file.read_text("utf-8"))
                w, h = j.get("width"), j.get("height")
                if isinstance(w, int) and isinstance(h, int):
                    width, height = w, h
            except Exception:
                pass

        class Api:
            def __init__(self):
                self._window = None
            def toggle_fullscreen(self):
                try:
                    if self._window:
                        self._window.toggle_fullscreen()
                except Exception as e:
                    logging.warning(f"切换全屏失败：{e}")

        api = Api()

        window = webview.create_window(
            title=title, url=url,
            width=width, height=height,
            fullscreen=fullscreen,
            resizable=resizable,
            frameless=frameless,
            on_top=on_top,
            js_api=api
        )

        api._window = window

        modules_dir = data_dir / "modules"
        generate_sample_plugins(modules_dir, cfg_path, config_data)

        if sync_plugin_config(modules_dir, mod_cfg):
            config_data["Modules"] = mod_cfg
            with cfg_path.open("wb") as fp:
                fp.write(dump_toml(config_data).encode("utf-8"))
            logging.info("已同步插件配置状态")

        load_plugins(modules_dir, window, api, mod_cfg, config_data, app_cfg, data_dir)
        logging.info("应用初始化完毕，准备启动窗口......")

        if remember:
            def on_closing():
                try:
                    w, h = window.get_size()
                    atomic_write(size_file, json.dumps({"width": w, "height": h}))
                except Exception as e:
                    logging.warning(f"保存窗口尺寸失败：{e}")
            window.events.closing += on_closing

        webview.start()
        logging.info("窗口已关闭")

        if clearcache:
            cache_dir = data_dir / "cache"
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            logging.info("缓存已清理")

    except Exception as e:
        handle_exception(e)

if __name__ == "__main__":
    main()
