#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import argparse
import logging
import shutil
import json
import traceback
from pathlib import Path
from datetime import datetime

import tkinter as tk
import webview

# 常量
HOTKEYS_PLIGIN_CONTENT = '''\
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

RESIZE_NOTIFIER_PLIGIN_CONTENT = '''\
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

PIXEL_RATIO_PLIGIN_CONTENT = '''\
def run(window, api):
    def inject():
        js = """
document.body.style.zoom = window.devicePixelRatio || 1;
"""
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
# PLUGIN-DISABLE
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

# PLUGIN-DISABLE
'''

# 禁止加载插件的配置符
DISABLE_MARK = "# PLUGIN-DISABLE"

# -----------------------------------------------------------------------------
# 简易 TOML 解析器（支持一级节、字符串、整数、布尔）
# -----------------------------------------------------------------------------
def parse_toml(text: str) -> dict[str, dict]:
    data: dict[str, dict] = {}
    section: str = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            data[section] = {}
        elif "=" in line and section:
            key, val = map(str.strip, line.split("=", 1))
            if val.lower() in ("true", "false"):
                parsed = val.lower() == "true"
            elif val.isdigit():
                parsed = int(val)
            elif (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                parsed = val[1:-1]
            else:
                parsed = val
            data[section][key] = parsed
    return data

# -----------------------------------------------------------------------------
# 公共工具
# -----------------------------------------------------------------------------
def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入：先写 tmp，再覆盖目标文件。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)

def enable_high_dpi() -> None:
    """在 Windows 上启用高 DPI 支持。"""
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

def show_error(title: str, message: str) -> None:
    """弹窗显示错误并退出。"""
    enable_high_dpi()
    root = tk.Tk(); root.withdraw()
    win = tk.Toplevel(root)
    win.title(title); win.geometry("600x600")
    txt = tk.Text(win, wrap="word")
    txt.insert("1.0", message); txt.config(state="disabled")
    txt.pack(fill="both", expand=True, padx=10, pady=10)
    win.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))
    tk.Button(win, text="退出", command=lambda: sys.exit(1)).pack(pady=10)
    win.mainloop()

def dump_toml(data: dict) -> str:
    """简单序列化 dict 为 TOML 格式（仅支持一级 section 和基本类型）。"""
    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return f'"{v}"'
        raise TypeError(f"不支持的类型：{type(v)}")
    lines = []
    for sec, vals in data.items():
        lines.append(f"[{sec}]")
        for k, v in vals.items():
            lines.append(f"{k} = {fmt(v)}")
        lines.append("")  # section 之间空行
    return "\n".join(lines)

# -----------------------------------------------------------------------------
# 配置管理
# -----------------------------------------------------------------------------
class ConfigManager:
    allowed_fields = {
        "URL", "Title", "Width", "Height", "Fullscreen",
        "OnTop", "Resizable", "Frameless", "RememberSize", "ClearCache"
    }
    bool_fields = {
        "Fullscreen", "OnTop", "Resizable", "Frameless", "RememberSize", "ClearCache"
    }

    def __init__(self, path: Path):
        self.path = path
        self.data = self._load_toml()
        self.app = self.data.get("App", {})
        self.mods = self.data.get("Modules", {})

    def _load_toml(self) -> dict:
        text = self.path.read_text(encoding="utf-8")
        return parse_toml(text)

    def validate(self) -> None:
        errs = []

        # 未知字段
        for key in self.app:
            if key not in self.allowed_fields:
                errs.append(f"未知字段：{key}")

        # URL 检查
        url = self.app.get("URL", "")
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            errs.append("App.URL 必须以 http:// 或 https:// 开头")

        # Title 类型
        title = self.app.get("Title", "")
        if not isinstance(title, str):
            errs.append("App.Title 必须是字符串")

        # 宽高检查
        for dim in ("Width", "Height"):
            val = self.app.get(dim)
            if not (isinstance(val, int) and val > 0):
                errs.append(f"App.{dim} 必须是大于 0 的整数")

        # 布尔字段检查
        for fld in self.bool_fields:
            val = self.app.get(fld)
            if not isinstance(val, bool):
                errs.append(f"App.{fld} 必须是 true/false")

        if errs:
            raise ValueError(
                f"[{self.path.name}] 配置校验失败：\n" +
                "\n".join(f"• {e}" for e in errs)
            )

    def save(self) -> None:
        atomic_write(self.path, dump_toml(self.data))

# -----------------------------------------------------------------------------
# 日志管理
# -----------------------------------------------------------------------------
class LogManager:
    def __init__(self, data_dir: Path, keep: int = 10):
        self.data_dir = data_dir
        self.keep = keep

    def setup(self) -> None:
        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        logfile = self.data_dir / f"AppLog-{ts}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s: %(message)s",
            handlers=[logging.FileHandler(logfile, encoding="utf-8")]
        )
        self._cleanup()
        logging.info("应用启动")

    def _cleanup(self) -> None:
        logs = sorted(
            self.data_dir.glob("AppLog-*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for old in logs[self.keep:]:
            try:
                old.unlink()
                logging.info(f"删除旧日志：{old.name}")
            except Exception as e:
                logging.warning(f"删除日志失败：{old.name} - {e}")

# -----------------------------------------------------------------------------
# WebView API
# -----------------------------------------------------------------------------
class Api:
    def __init__(self, window: webview.Window):
        self._window = window

    def toggle_fullscreen(self) -> None:
        try:
            self._window.toggle_fullscreen()
        except Exception as e:
            logging.warning(f"切换全屏失败：{e}")

# -----------------------------------------------------------------------------
# 插件管理
# -----------------------------------------------------------------------------
class PluginManager:
    def __init__(self, modules_dir: Path, cfg: ConfigManager, data_dir: Path):
        self.modules_dir = modules_dir
        self.cfg = cfg
        self.data_dir = data_dir

    def generate_samples(self) -> None:
        samples = {
            TEMPLATE_PLUGIN_NAME: TEMPLATE_PLUGIN_CONTENT,
            "hotkeys.py": HOTKEYS_PLIGIN_CONTENT,
            "resize_notifier.py": RESIZE_NOTIFIER_PLIGIN_CONTENT,
            "pixel_ratio.py": PIXEL_RATIO_PLIGIN_CONTENT
        }

        self.modules_dir.mkdir(parents=True, exist_ok=True)

        updated = False
        for name, content in samples.items():
            path = self.modules_dir / name
            # 模板不进 toml 管理
            if name == TEMPLATE_PLUGIN_NAME:
                if not path.exists():
                    atomic_write(path, content)
                    logging.info(f"已生成插件模板：{name}")
                continue

            # 普通示例插件
            key = name.removesuffix(".py")
            if not path.exists():
                atomic_write(path, content)
                logging.info(f"已生成内置插件：{name}")
            if key not in self.cfg.mods:
                # 默认打开示例插件
                self.cfg.mods[key] = True
                updated = True

        if updated:
            self.cfg.data["Modules"] = self.cfg.mods
            self.cfg.save()
            logging.info("已同步插件启用状态到配置文件")

    def sync(self) -> None:
        existing = {p.stem for p in self.modules_dir.glob("*.py")}
        updated = False
        for name in list(self.cfg.mods):
            if name not in existing and self.cfg.mods[name]:
                logging.warning(f"自动禁用不存在的插件：{name}")
                self.cfg.mods[name] = False
                updated = True
        if updated:
            self.cfg.data["Modules"] = self.cfg.mods
            self.cfg.save()

    def load_all(self, window: webview.Window, api: Api) -> None:
        # 确保目录存在，防止意外删除导致 glob 卡住
        self.modules_dir.mkdir(parents=True, exist_ok=True)

        updated = False
        for py in sorted(self.modules_dir.glob("*.py")):
            name = py.stem
            enabled = self.cfg.mods.get(name, False)
            if not enabled:
                logging.info(f"插件已禁用：{py.name}")
                continue

            # 检测第一行／最后一行标识符
            lines = py.read_text(encoding="utf-8").splitlines()
            head = lines[0].strip() if lines else ""
            tail = lines[-1].strip() if lines else ""
            if DISABLE_MARK in head or DISABLE_MARK in tail:
                show_error("插件停用",
                           f"检测到 {py.name} 包含禁用标识，将自动禁用并跳过加载。")
                self.cfg.mods[name] = False
                updated = True
                continue

            # 正常加载流程
            logging.info(f"注入插件：{py.name}")
            try:
                scope = {
                    "logging": logging, "json": json, "Path": Path,
                    "sys": sys, "data_dir": self.data_dir,
                    "app_cfg": self.cfg.app, "mod_cfg": self.cfg.mods,
                    "atomic_write": atomic_write, "enable_high_dpi": enable_high_dpi,
                    "window": window, "api": api
                }
                exec(py.read_text(encoding="utf-8"), scope)
                runner = scope.get("run")
                if callable(runner):
                    runner(window, api)
                    logging.info(f"插件加载成功：{py.name}")
                else:
                    logging.warning(f"{py.name} 缺少 run() 方法")
            except Exception as e:
                logging.error(f"插件加载失败：{py.name} - {e}")

        if updated:
            # 有插件被标记停用时，保存回 toml
            self.cfg.data["Modules"] = self.cfg.mods
            self.cfg.save()
            logging.info("已同步插件停用状态到配置文件")

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
EXE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.resolve()

def find_or_create_config(explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            show_error("配置文件未找到",
                       f"找不到指定的配置文件：{explicit}")
        return explicit

    tomls = sorted(EXE_DIR.glob("*.toml"))
    if not tomls:
        tpl = EXE_DIR / TEMPLATE_NAME
        atomic_write(tpl, TEMPLATE_CONTENT)

        data_dir    = prepare_data_dir(tpl)
        modules_dir = data_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            modules_dir / TEMPLATE_PLUGIN_NAME,
            TEMPLATE_PLUGIN_CONTENT
        )

        show_error(
            "配置缺失",
            f"未检测到 .toml 配置，已生成：\n"
            f"  • {tpl.name}\n"
            f"  • {modules_dir.name}/{TEMPLATE_PLUGIN_NAME}\n\n"
            "请编辑后重新启动程序。"
        )

        sys.exit(1)

    if len(tomls) == 1:
        return tomls[0]

    enable_high_dpi()
    root = tk.Tk()
    root.title("选择配置文件")
    root.geometry("500x400")
    root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))

    lst = tk.Listbox(root, font=("Arial", 12))
    for f in tomls:
        lst.insert("end", f.name)
    lst.pack(fill="both", expand=True)
    lst.focus_set()

    chosen: dict[str, Path] = {"file": None}
    def confirm(event=None):
        sel = lst.curselection()
        if sel:
            chosen["file"] = tomls[sel[0]]
            root.destroy()

    lst.bind("<Double-Button-1>", confirm)
    lst.bind("<Return>", confirm)
    tk.Button(root, text="选择", command=confirm).pack(pady=5)
    root.mainloop()

    if not chosen["file"]:
        sys.exit("未选择配置，程序退出。")
    return chosen["file"]


def prepare_data_dir(cfg_path: Path) -> Path:
    dname = cfg_path.with_suffix("").name + "_data"
    p = EXE_DIR / dname
    p.mkdir(parents=True, exist_ok=True)
    return p

def main():
    try:
        parser = argparse.ArgumentParser(description="启动 WebView 应用")
        parser.add_argument("-c", "--config", type=Path, help="指定 .toml 配置文件")
        args = parser.parse_args()

        cfg_path = find_or_create_config(args.config)
        cfg = ConfigManager(cfg_path)

        data_dir    = prepare_data_dir(cfg_path)
        modules_dir = data_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)

        cfg.validate()
        LogManager(data_dir).setup()

        app = cfg.app

        size_file = data_dir / "window_size.json"
        if app.get("RememberSize") and size_file.exists():
            try:
                j = json.loads(size_file.read_text(encoding="utf-8"))
                w, h = j.get("width"), j.get("height")
                if isinstance(w, int) and isinstance(h, int):
                    app["Width"], app["Height"] = w, h
            except Exception:
                pass

        window = webview.create_window(
            title=app["Title"], url=app["URL"],
            width=app["Width"], height=app["Height"],
            fullscreen=app["Fullscreen"],
            resizable=app["Resizable"],
            frameless=app["Frameless"],
            on_top=app["OnTop"],
            js_api=None
        )
        api = Api(window)
        window.js_api = api

        pm = PluginManager(data_dir / "modules", cfg, data_dir)
        pm.generate_samples()
        pm.sync()
        pm.load_all(window, api)

        if app.get("RememberSize"):
            def save_size():
                w, h = window.get_size()
                atomic_write(size_file, json.dumps({"width": w, "height": h}))
            window.events.closing += save_size

        webview.start()

        if app.get("ClearCache"):
            shutil.rmtree(data_dir / "cache", ignore_errors=True)

    except Exception as e:
        show_error("启动失败", "".join(traceback.format_exception_only(type(e), e)))

if __name__ == "__main__":
    main()