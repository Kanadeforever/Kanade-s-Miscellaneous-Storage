# -*- coding: utf-8 -*-
import sys
import importlib
import pkgutil
import argparse
import tkinter as tk
import logging
import shutil
import json
from pathlib import Path

SCRIPT_DIR = (  # 资源目录：如果打包后使用 _MEIPASS，否则脚本所在目录
    Path(sys._MEIPASS) if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else Path(__file__).parent.resolve()
)
EXE_DIR = (  # 可执行文件所在目录
    Path(sys.executable).parent if getattr(sys, "frozen", False)
    else Path(__file__).parent.resolve()
)
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

def enable_high_dpi():
    """Windows 下启动 DPI 感知，Tk 窗口才能正确缩放。"""
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

def show_dependency_error(missing: list[str]):
    """用 Tk 弹窗列出缺失依赖，文字可选中复制。"""
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.title("缺少依赖组件")
    win.geometry("500x500")

    # 当用户点击窗口 X 时也直接退出
    win.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))

    txt = tk.Text(win, wrap="word")
    txt.insert("1.0", "检测到以下必要组件缺失或版本不符：\n\n")
    for item in missing:
        txt.insert("end", f"• {item}\n")
    txt.config(state="normal")  # 可复制
    txt.pack(fill="both", expand=True, padx=10, pady=10)

    btn = tk.Button(win, text="退出", command=lambda: sys.exit(1))
    btn.pack(pady=10)
    win.mainloop()


def check_dependencies():
    """
    检查 Python 版本、Tomllib、Tkinter、pywebview。
    弹窗提示后退出，避免后续直接 ImportError 崩溃。
    """
    missing = []

    # Python 版本
    if sys.version_info < (3,11):
        missing.append(f"Python ≥ 3.11   (当前 {sys.version_info.major}.{sys.version_info.minor})")

    # tomllib（Python 3.11+ 内置）
    import importlib.util
    if importlib.util.find_spec("tomllib") is None:
        missing.append("tomllib (Python 3.11+ 内置)")

    # tkinter
    try:
        import tkinter
    except ImportError:
        missing.append("Tkinter (GUI 选择框)")

    # pywebview
    if not importlib.util.find_spec("webview"):
        missing.append("pywebview (pip install pywebview)")

    if missing:
        show_dependency_error(missing)
        sys.exit(1)

def parse_args():
    parser = argparse.ArgumentParser(description="启动 WebView 应用")
    parser.add_argument(
        '-c','--config',
        type=Path,
        help="指定 .toml 配置文件"
    )
    return parser.parse_args()

def find_or_create_config(explicit: Path | None) -> Path:
    """
    - 如果命令行指定 --config，直接用之并验证存在
    - 否则扫描当前目录 *.toml
      • 0 个 → 生成模板后退出
      • 1 个 → 直接返回
      • 多个 → 弹 Tk 列表让用户用方向键/双击/回车/按钮选
    """
    if explicit:
        if not explicit.exists():
            sys.exit(f"Error: 找不到指定配置文件：{explicit}")
        return explicit

    tomls = sorted(SCRIPT_DIR.glob("*.toml"))
    if not tomls:
        tpl = SCRIPT_DIR / TEMPLATE_NAME
        tpl.write_text(TEMPLATE_CONTENT, encoding="utf-8")
        print(f"未检测到 .toml 配置，已生成模板：{tpl.name}，请编辑后重启。")
        sys.exit()

    if len(tomls) == 1:
        return tomls[0]

    # 多文件时弹框选择（方向键/回车/双击）
    # 高 DPI 感知（Windows）
    enable_high_dpi()
    # 然后创建 Tk 弹窗
    root = tk.Tk()
    root.title("选择配置文件")
    root.geometry("400x300")

    lst = tk.Listbox(root, font=("Arial", 12))
    for f in tomls:
        lst.insert("end", f.stem)
    lst.pack(fill="both", expand=True)
    lst.focus_set()

    chosen = {"stem": None}
    def on_ok(event=None):
        sel = lst.curselection()
        if sel:
            chosen["stem"] = lst.get(sel[0])
            root.destroy()

    lst.bind("<Double-Button-1>", on_ok)
    lst.bind("<Return>", on_ok)
    tk.Button(root, text="选择", command=on_ok).pack(pady=5)

    root.mainloop()

    if not chosen["stem"]:
        sys.exit("未选择配置，程序退出。")
    return SCRIPT_DIR / f"{chosen['stem']}.toml"

def load_config(path: Path) -> dict:
    # 这里才真正导入 tomllib，保证前面版本检查通过
    import tomllib
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    app = data.get("App", {})
    if not app.get("URL"):
        sys.exit(f"Error: [{path.name}] 中 [App].URL 为必填项")
    return app

def prepare_data_dir(cfg: Path) -> Path:
    d = cfg.with_suffix("").name + "_data"
    p = cfg.parent / d
    p.mkdir(parents=True, exist_ok=True)
    return p

def setup_logging(data_dir: Path):
    logfile = data_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8")]
    )
    logging.info("应用启动")

def main():
    # 1. 检查版本 & 模块依赖
    check_dependencies()

    # 2. 延后导入 pywebview
    import webview

    # 3. 命令行参数 & 配置文件选择/生成
    args        = parse_args()
    config_path = find_or_create_config(args.config)

    # 4. 读取配置，准备数据目录、日志
    app      = load_config(config_path)
    data_dir = prepare_data_dir(config_path)
    setup_logging(data_dir)

    # 5. 从配置里取值
    url        = app["URL"]
    title      = app.get("Title", "Web App")
    width      = int(app.get("Width", 1024))
    height     = int(app.get("Height", 768))
    fullscreen = bool(app.get("Fullscreen", False))
    on_top     = bool(app.get("OnTop", False))
    resizable  = bool(app.get("Resizable", True))
    frameless  = bool(app.get("Frameless", False))

    # 窗口大小记忆
    remember   = bool(app.get("RememberSize", False))
    size_file  = data_dir / "window_size.json"
    if remember and size_file.exists():
        try:
            d = json.loads(size_file.read_text("utf-8"))
            w,h = d.get("width"), d.get("height")
            if isinstance(w,int) and isinstance(h,int):
                width, height = w,h
        except:
            pass

    # 6. 创建窗口
    window = webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        fullscreen=fullscreen,
        resizable=resizable,
        frameless=frameless,
        on_top=on_top
    )

    class Api:
        def toggle_fullscreen(self):
            try: window.toggle_fullscreen()
            except: pass

    api = Api()

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
        """
        window.evaluate_js(js)

    window.events.loaded += inject_hotkeys
    webview.start()

    logging.info("窗口已关闭")

    # 7. 记忆窗口大小
    if remember:
        try:
            w,h = window.get_size()
            size_file.write_text(json.dumps({"width":w,"height":h}),"utf-8")
        except:
            pass

    # 8. 清理缓存
    if bool(app.get("ClearCache", False)):
        shutil.rmtree(data_dir / "cache", ignore_errors=True)
        logging.info("缓存已清理")

if __name__ == "__main__":
    main()