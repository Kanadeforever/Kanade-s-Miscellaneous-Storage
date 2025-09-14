# -*- coding: utf-8 -*-
import sys
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

def find_or_create_config() -> Path:
    tomls = sorted(SCRIPT_DIR.glob("*.toml"))
    # 未找到任何 toml，则生成模板并退出
    if not tomls:
        tpl = SCRIPT_DIR / TEMPLATE_NAME
        tpl.write_text(TEMPLATE_CONTENT, encoding="utf-8")
        print(f"未检测到 .toml 配置，已生成模板：{tpl.name}，请编辑后重启。")
        sys.exit()

    # 只有一个 toml 时直接返回
    if len(tomls) == 1:
        return tomls[0]

    # 多个时弹窗选择（双击 & “选择”按钮都可选中）
    # 高 DPI 感知（Windows）
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("选择配置文件")
    root.geometry("400x300")

    lst = tk.Listbox(root, font=("Arial", 12))
    for f in tomls:
        lst.insert("end", f.stem)
    lst.pack(fill="both", expand=True)

    # 列表框获取焦点，方向键自然就能移动选中
    lst.focus_set()

    chosen = {"stem": None}
    def on_ok(event=None):
		sel = lst.curselection()
		if sel:
			chosen["stem"] = lst.get(sel[0])
			root.destroy()

    # 双击和按钮都可选择
    lst.bind("<Double-Button-1>", on_ok)
    tk.Button(root, text="选择", command=on_ok).pack(pady=5)
    # 双击也可选择
    lst.bind("<Double-Button-1>", on_ok)
    # 回车（Enter）也触发选择
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
    config_path = find_or_create_config()
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
