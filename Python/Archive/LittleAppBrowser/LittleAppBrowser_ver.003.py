# -*- coding: utf-8 -*-
import sys
import argparse
import tomllib
import webview
import logging
import shutil
import json
import tkinter as tk
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent.resolve()
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
    """
    在 Windows 上启用 DPI 感知，让 Tkinter 窗口按照系统设置缩放。
    对 Linux/macOS，Tk 自身会跟随系统缩放，通常不需要额外处理。
    """
    if sys.platform == "win32":
        try:
            from ctypes import windll
            # PROCESS_PER_MONITOR_DPI_AWARE = 1
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

def show_dependency_error(missing: list[str]):
    # 先开启 DPI 感知
    enable_high_dpi()
    # 然后创建 Tk 弹窗
    root = tk.Tk()
    root.withdraw()
    dlg = tk.Toplevel()
    dlg.title("缺少依赖组件")
    txt = tk.Text(dlg, width=50, height=10, wrap="word")
    txt.insert("1.0", "检测到以下必要组件缺失或版本不符：\n\n")
    for comp in missing:
        txt.insert("end", f"• {comp}\n")
    txt.config(state="normal")  # 文本框可选中复制
    txt.pack(padx=10, pady=10, fill="both", expand=True)
    btn = tk.Button(dlg, text="退出", command=sys.exit)
    btn.pack(pady=(0,10))
    dlg.mainloop()

def check_dependencies():
    missing = []
    # Python 版本
    if sys.version_info < (3,11):
        missing.append(f"Python ≥ 3.11   (当前 {sys.version.split()[0]})")
    # Tkinter
    try:
        import tkinter
    except ImportError:
        missing.append("Tkinter (GUI 选择框)")
    # pywebview
    try:
        import webview
    except ImportError:
        missing.append("pywebview (pip install pywebview)")
    if missing:
        show_dependency_error(missing)

def parse_args():
    parser = argparse.ArgumentParser(description="启动 WebView 应用")
    parser.add_argument(
        '-c','--config',
        type=Path,
        help="指定 .toml 配置文件"
    )
    return parser.parse_args()

def find_or_create_config(explicit: Path | None) -> Path:
    # 如果命令行指定了 config，直接用它
    if explicit:
        if not explicit.exists():
            sys.exit(f"Error: 找不到指定的配置文件：{explicit}")
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
    with path.open("rb") as fp:
        cfg = tomllib.load(fp)
    app = cfg.get("App", {})
    if not app.get("URL"):
        sys.exit(f"Error: [{path.name}] 中的 [App].URL 为必填项")
    return app

def prepare_data_dir(cfg: Path) -> Path:
    data_dir = cfg.with_suffix("").name + "_data"
    target   = cfg.parent / data_dir
    target.mkdir(parents=True, exist_ok=True)
    return target

def setup_logging(data_dir: Path):
    logfile = data_dir / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8")]
    )
    logging.info("应用启动")

def main():
    # 先检测依赖
    check_dependencies()

    # 解析命令行
    args        = parse_args()
    config_path = find_or_create_config(args.config)
    app         = load_config(config_path)
    data_dir    = prepare_data_dir(config_path)
    setup_logging(data_dir)

    # 读取配置
    url        = app["URL"]
    title      = app.get("Title", "Web App")
    width      = int(app.get("Width", 1024))
    height     = int(app.get("Height", 768))
    fullscreen = bool(app.get("Fullscreen", False))
    on_top     = bool(app.get("OnTop", False))
    resizable  = bool(app.get("Resizable", True))
    frameless  = bool(app.get("Frameless", False))

    # 窗口大小记忆
    remember  = bool(app.get("RememberSize", False))
    size_file = data_dir / "window_size.json"
    if remember and size_file.exists():
        try:
            d = json.loads(size_file.read_text("utf-8"))
            w, h = d["width"], d["height"]
            if isinstance(w, int) and isinstance(h, int):
                width, height = w, h
        except:
            pass

    # 准备 JS API，用于切换全屏和开发者工具
    window = None
    class Api:
        def toggle_fullscreen(self):
            try:
                window.toggle_fullscreen()
            except:
                pass

        def toggle_dev_tools(self):
            try:
                window.toggle_dev_tools()
            except:
                pass

    api = Api()

    # 创建窗口时传入 js_api
    window = webview.create_window(
        title=title,
        url=url,
        width=width,
        height=height,
        fullscreen=fullscreen,
        resizable=resizable,
        frameless=frameless,
        on_top=on_top,
        js_api=api
    )

    # 注入键盘监听脚本
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
            if (e.ctrlKey && (e.key==='+' || e.key==='=')) {
                document.body.style.zoom = zoom + 0.1;
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

    # 注册 loaded 事件
    window.events.loaded += inject_hotkeys

    # 启动 WebView
    webview.start(http_server=True)

    logging.info("窗口已关闭")

    # 保存窗口大小
    if remember:
        try:
            w, h = window.get_size()
            size_file.write_text(json.dumps({"width": w, "height": h}), "utf-8")
        except:
            pass

    # 清理缓存
    if bool(app.get("ClearCache", False)):
        shutil.rmtree(data_dir / "cache", ignore_errors=True)
        logging.info("缓存已清理")

if __name__ == "__main__":
    main()
