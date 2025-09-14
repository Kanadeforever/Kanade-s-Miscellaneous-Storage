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


TEMPLATE_NAME = "config_template.toml"
TEMPLATE_CONTENT = """\
# config_template.toml - WebView 应用配置模板

[App]
# 必填: 要加载的网页地址
URL           = "https://"
# 窗口标题，默认 "Web App"
Title         = "示例应用"
# 初始宽度和高度，单位 像素
Width         = 1024
Height        = 768
# 是否全屏，true/false
Fullscreen    = false
# 是否总在最上层，true/false
OnTop         = false
# 是否允许拖拽调整大小，true/false
Resizable     = true
# 是否去除系统边框和标题栏，true/false
Frameless     = false
# 是否记忆窗口大小，true/false
RememberSize  = false
# 关闭时是否清理缓存（不删除其他数据），true/false
ClearCache    = false
"""

SCRIPT_DIR = (  # 资源目录：如果打包后使用 _MEIPASS，否则脚本所在目录
    Path(sys._MEIPASS) if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else Path(__file__).parent.resolve()
)
EXE_DIR = (  # 可执行文件所在目录
    Path(sys.executable).parent if getattr(sys, "frozen", False)
    else Path(__file__).parent.resolve()
)

def atomic_write(path, content, mode="w", encoding="utf-8"):
    """使用临时文件写入后替换，保证原子性。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open(mode, encoding=encoding) as f:
        f.write(content)
    # tmp.replace(path)
    try:
        tmp.replace(path)
    except Exception as e:
        logging.error(f"写入失败：{e}")
        tmp.unlink(missing_ok=True)
        raise

def enable_high_dpi():
    """Windows 下启动 DPI 感知，Tk 窗口才能正确缩放。"""
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

def show_error(title, message):
    """统一错误弹窗，使用 Text 可复制，关闭或按钮退出。"""
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("500x400")
    # 当用户点击窗口 X 时也直接退出
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
    tomls = sorted(SCRIPT_DIR.glob("*.toml"))
    if not tomls:
        tpl = SCRIPT_DIR / TEMPLATE_NAME
        atomic_write(tpl, TEMPLATE_CONTENT)
        print(f"未检测到 .toml 配置，已生成模板：{tpl.name}，请编辑后重启。")
        sys.exit(0)
    if len(tomls) == 1:
        return tomls[0]
    # 多个时弹框选择
    enable_high_dpi()
    root = tk.Tk(); root.title("选择配置文件"); root.geometry("400x300")
    lst = tk.Listbox(root, font=("Arial", 12))
    for f in tomls:
        lst.insert("end", f.name)
    lst.pack(fill="both", expand=True); lst.focus_set()
    chosen = {"file": None}
    def confirm(event=None):
        sel = lst.curselection()
        if sel:
            chosen["file"] = SCRIPT_DIR / lst.get(sel[0])
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

    # 未知字段
    for k in app:
        if k not in allowed:
            errors.append(f"未知字段：{k}")

    # URL
    url = app.get("URL", "")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        errors.append("App.URL 必须是以 http:// 或 https:// 开头的字符串")

    # Title
    title = app.get("Title", "Web App")
    if not isinstance(title, str):
        errors.append("App.Title 必须是字符串")

    # 宽高
    for key in ("Width", "Height"):
        v = app.get(key, None)
        if not isinstance(v, int) or v <= 0:
            errors.append(f"App.{key} 必须是正整数")

    # 布尔项
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
    if "URL" not in app:
        raise KeyError(f"[{path.name}] 中缺少必填项 App.URL")
    return validate_config(app, path)

def prepare_data_dir(cfg):
    dname = cfg.with_suffix("").name + "_data"
    p = cfg.parent / dname
    p.mkdir(parents=True, exist_ok=True)
    return p

def setup_logging(data_dir):
    logfile = data_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8")]
    )
    logging.info("应用启动")

def main():
    try:
        check_dependencies()
        import webview

        args        = parse_args()
        cfg_path    = find_or_create_config(args.config)
        app_cfg     = load_config(cfg_path)
        data_dir    = prepare_data_dir(cfg_path)
        setup_logging(data_dir)

        # 取值
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

        # 恢复尺寸
        if remember and size_file.exists():
            try:
                j = json.loads(size_file.read_text("utf-8"))
                w,h = j.get("width"), j.get("height")
                if isinstance(w,int) and isinstance(h,int):
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

        # 注入热键
        def inject_hotkeys():
            js = """
                window.addEventListener('keydown', function(e) {
                    // 全屏 F11
                    if (e.key === 'F11') {
                        window.pywebview.api.toggle_fullscreen();
                        e.preventDefault();
                    }
                    // 刷新 F5 / Ctrl+R
                    if (e.key === 'F5' || (e.ctrlKey && e.key.toLowerCase()==='r')) {
                        location.reload();
                        e.preventDefault();
                    }
                    // 缩放 Ctrl + + / - / 0
                    let zoom = parseFloat(document.body.style.zoom) || 1;
                    if (e.ctrlKey && (e.key==='+'||e.key==='=')) {
                        document.body.style.zoom = zoom+0.1;
                        e.preventDefault();
                    }
                    if (e.ctrlKey && e.key==='-') {
                        document.body.style.zoom = Math.max(zoom - 0.1, 0.1);
                        e.preventDefault();
                    }
                    if (e.ctrlKey && e.key==='0') {
                        document.body.style.zoom = 1;
                        e.preventDefault();
                    }
                    // 查找 Ctrl+F
                    if (e.ctrlKey && e.key.toLowerCase()==='f') {
                        let term = prompt('查找：');
                        if (term) window.find(term);
                        e.preventDefault();
                    }
                });

                // 监听窗口尺寸变化
                new ResizeObserver(triggerResize).observe(document.body);
                """
            try: window.evaluate_js(js)
            except Exception as e: logging.warning(f"注入热键失败：{e}")

        window.events.loaded += inject_hotkeys

        # 在关闭前记忆尺寸
        if remember:
            def on_closing():
                try:
                    w,h = window.get_size()
                    atomic_write(size_file, json.dumps({"width":w,"height":h}))
                except: pass
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
