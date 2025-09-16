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
JS_INJECTOR_NAME    = "_CORE_js_injector.py"
JS_INJECTOR_CONTENT = r'''
import logging
def run(window, api):
    logging.info("js_injector: run() called")
    def inject_js(js: str):
        """
        将 js 字符串注入到 window.loaded 事件中，
        并自动捕获 js 变量，避免 NameError。
        """
        window.events.loaded += lambda js=js: window.evaluate_js(js)
    api.inject_js = inject_js
    logging.info("js_injector: api.inject_js 已重定义")
'''.lstrip('\n')

PLUGIN_MANAGER_NAME    = "_CORE_plugin_manager.py"
PLUGIN_MANAGER_CONTENT = r'''
from pathlib import Path
import tkinter as tk
import textwrap, logging

def run(window, api):
    logging.info("plugin_manager: run() called")

    def open_plugin_manager():
        root = tk.Tk()
        root.title("Plugins Manager")
        root.geometry("600x300")
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Escape>", lambda e: root.destroy())  # ESC 关闭窗口

        desc = tk.Message(root, text="双击或按空格/回车切换插件状态，鼠标拖拽或Shift+↑/↓排序，ESC关闭窗口，插件生效需要重启程序。", font=("Segoe UI", 10), width=580)
        desc.pack(pady=(10, 0))

        mods = list(mod_cfg.keys())

        frame = tk.Frame(root)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        lb = tk.Listbox(frame, font=("Arial", 11), yscrollcommand=scrollbar.set)
        lb.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=lb.yview)

        status_label = tk.Label(root, text="", font=("Arial", 10), fg="#444")
        status_label.pack(pady=(0, 10))

        def refresh_listbox():
            lb.delete(0, "end")
            for name in mods:
                lb.insert("end", f"{name}: {'On' if mod_cfg[name] else 'Off'}")
            # 写入排序结果到 toml
            sorted_mods = {name: mod_cfg.get(name, False) for name in mods}
            cfg.data["Modules"] = sorted_mods
            cfg.save()

        refresh_listbox()
        lb.focus_set()

        def toggle(event=None):
            sel = lb.curselection()
            if not sel: return
            nm = mods[sel[0]]
            mod_cfg[nm] = not mod_cfg[nm]
            cfg.data["Modules"] = mod_cfg
            cfg.save()
            lb.delete(sel[0])
            lb.insert(sel[0], f"{nm}: {'On' if mod_cfg[nm] else 'Off'}")

        lb.bind("<Double-Button-1>", toggle)
        lb.bind("<Return>", toggle)       # 回车键触发
        lb.bind("<space>", toggle)        # 空格键触发
        lb.bind("<Left>", toggle)         # 左方向键触发
        lb.bind("<Right>", toggle)        # 右方向键触发

        lb.bind("<<ListboxSelect>>", lambda e: update_status())

        def update_status():
            sel = lb.curselection()
            if sel:
                nm = mods[sel[0]]
                status_label.config(text=f"{nm}: {'On' if mod_cfg[nm] else 'Off'}")

        drag_index = [None]

        def on_drag_start(event):
            sel = lb.curselection()
            if sel:
                drag_index[0] = sel[0]

        def on_drag_motion(event):
            if drag_index[0] is None: return
            target = lb.nearest(event.y)
            if target != drag_index[0]:
                mods.insert(target, mods.pop(drag_index[0]))
                drag_index[0] = target
                refresh_listbox()
                lb.selection_set(target)
                update_status()

        lb.bind("<Button-1>", on_drag_start)
        lb.bind("<B1-Motion>", on_drag_motion)

        def shift_move(event):
            sel = lb.curselection()
            if not sel or not event.state & 0x0001: return  # Shift 未按下
            i = sel[0]
            if event.keysym == "Up" and i > 0:
                mods[i - 1], mods[i] = mods[i], mods[i - 1]
                refresh_listbox()
                lb.selection_set(i - 1)
                update_status()
            elif event.keysym == "Down" and i < len(mods) - 1:
                mods[i + 1], mods[i] = mods[i], mods[i + 1]
                refresh_listbox()
                lb.selection_set(i + 1)
                update_status()

        lb.bind("<Up>", shift_move)
        lb.bind("<Down>", shift_move)

        root.mainloop()

    api._plugin_callbacks["open_plugin_manager"] = open_plugin_manager

    window.events.loaded += lambda: window.evaluate_js(textwrap.dedent("""
        console.log("plugin_manager: JS injected");
        window.addEventListener('keydown', e => {
            if (e.ctrlKey && e.shiftKey && e.key === 'P') {
                console.log("plugin_manager: shortcut triggered");
                if (window.pywebview?.api?.open_plugin_manager) {
                    window.pywebview.api.open_plugin_manager();
                }
            }
        });
    """))
'''.lstrip('\n')

HOTKEYS_PLUGIN_NAME    = "hotkeys.py"
HOTKEYS_PLUGIN_CONTENT = r'''
import textwrap, logging

def run(window, api):
    logging.info("hotkeys: run() called")

    window.events.loaded += lambda: window.evaluate_js(textwrap.dedent("""
        console.log("hotkeys: JS injected");
        setTimeout(() => {
            window.addEventListener('keydown', e => {
                console.log("hotkeys keydown:", e.key);
                if (e.key === 'F11') {
                    if (window.pywebview?.api?.toggle_fullscreen) {
                        window.pywebview.api.toggle_fullscreen().then(() => {
                            console.log("hotkeys: fullscreen toggled");
                        }).catch(err => {
                            console.error("hotkeys: toggle failed", err);
                        });
                    }
                }
                if (e.key === 'F5') {
                    location.reload();
                }
            });
        }, 500);
    """))
'''.lstrip('\n')

RESIZE_NOTIFIER_PLUGIN_NAME    = "resize_notifier.py"
RESIZE_NOTIFIER_PLUGIN_CONTENT = r'''
def run(window, api):
    api.inject_js("""\
new ResizeObserver(() => {
    window.dispatchEvent(new Event('resize'));
}).observe(document.body);
""")
'''.lstrip('\n')

PIXEL_RATIO_PLUGIN_NAME    = "pixel_ratio.py"
PIXEL_RATIO_PLUGIN_CONTENT = r'''
def run(window, api):
    api.inject_js("""
document.body.style.zoom = window.devicePixelRatio || 1;
""")
'''.lstrip('\n')

CONTEXT_PLUGIN_NAME    = "context_menu.py"
CONTEXT_PLUGIN_CONTENT = r'''
import logging
import textwrap

def run(window, api):
    logging.info("context_menu: run() called")

    # 注入 HTML + CSS + JS 一体化上下文菜单
    window.events.loaded += lambda: window.evaluate_js(textwrap.dedent("""
        console.log("context_menu: injecting HTML menu");

        const menu = document.createElement('div');
        menu.id = 'custom-context-menu';
        menu.style.position = 'absolute';
        menu.style.display = 'none';
        menu.style.zIndex = '9999';
        menu.style.background = '#fff';
        menu.style.border = '1px solid #ccc';
        menu.style.boxShadow = '2px 2px 6px rgba(0,0,0,0.2)';
        menu.style.fontFamily = 'Arial, sans-serif';
        menu.style.fontSize = '14px';
        menu.style.minWidth = '160px';

        function addItem(label, callback) {
            const item = document.createElement('div');
            item.textContent = label;
            item.style.padding = '8px 12px';
            item.style.cursor = 'pointer';
            item.onmouseenter = () => item.style.background = '#f0f0f0';
            item.onmouseleave = () => item.style.background = '#fff';
            item.onclick = () => {
                hideMenu();
                callback();
            };
            menu.appendChild(item);
        }

        addItem('刷新页面', () => location.reload());
        addItem('切换全屏', () => {
            if (window.pywebview?.api?.toggle_fullscreen) {
                window.pywebview.api.toggle_fullscreen();
            }
        });
        addItem('关闭菜单', () => hideMenu());

        document.body.appendChild(menu);

        function showMenu(x, y) {
            menu.style.left = x + 'px';
            menu.style.top = y + 'px';
            menu.style.display = 'block';
        }

        function hideMenu() {
            menu.style.display = 'none';
        }

        document.addEventListener('contextmenu', e => {
            e.preventDefault();
            const scale = window.devicePixelRatio || 1;
            showMenu(e.clientX, e.clientY);
        });

        document.addEventListener('click', hideMenu);
    """))
'''.lstrip('\n')

TEMPLATE_NAME = "config_template.toml"
TEMPLATE_CONTENT = """\
# config_template.toml - WebView 应用配置模板
[App]
URL          = "https://example.com/"   # 必填: 要加载的网页地址
Title        = "我的 Web 应用"          # 窗口标题，默认 "Web App"
Width        = 1024  # 初始宽度，单位 像素
Height       = 768   # 初始高度，单位 像素
Fullscreen   = false # 是否全屏，true/false
OnTop        = false # 是否总在最上层，true/false
Resizable    = true  # 是否允许拖拽调整大小，true/false
Frameless    = false # 是否去除系统边框和标题栏，true/false
RememberSize = false # 是否记忆窗口大小，true/false
ClearCache   = true  # 关闭时是否清理缓存（不删除其他数据），true/false

[Modules]
resize_notifier = true
pixel_ratio     = false
hotkeys         = true
context_menu    = true
"""
TEMPLATE_PLUGIN_NAME = "[DEMO]_template.py"
TEMPLATE_PLUGIN_CONTENT = r'''
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

    # window.events.loaded += lambda: window.evaluate_js(js)
# PLUGIN-DISABLE
'''.lstrip('\n')

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
            val = val.split("#", 1)[0].strip()
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
    win.title(title)
    win.geometry("500x300")
    win.minsize(300, 200)  # 设置最小尺寸，防止过小导致布局崩溃

    # 使用 grid 布局，确保文本框和按钮都能自适应
    win.grid_rowconfigure(0, weight=1)
    win.grid_columnconfigure(0, weight=1)

    txt = tk.Text(win, wrap="word")
    txt.insert("1.0", message)
    txt.config(state="disabled")
    txt.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))

    btn = tk.Button(win, text="退出", command=lambda: sys.exit(1))
    btn.grid(row=1, column=0, sticky="ew", padx=10, pady=10)

    win.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))
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
    def __init__(self, window: webview.Window | None):
        self._window = window
        self._plugin_callbacks = {}

    def toggle_fullscreen(self) -> None:
        logging.info("Api.toggle_fullscreen called")
        try:
            self._window.toggle_fullscreen()
        except Exception as e:
            logging.warning(f"切换全屏失败：{e}")

    def inject_js(self, js: str) -> None:
        self._window.events.loaded += lambda js=js: self._window.evaluate_js(js)

    def open_plugin_manager(self):
        cb = getattr(self, "_plugin_callbacks", {}).get("open_plugin_manager")
        if callable(cb):
            cb()
        else:
            logging.warning("找不到 open_plugin_manager 回调")

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
            JS_INJECTOR_NAME: JS_INJECTOR_CONTENT,
            PLUGIN_MANAGER_NAME: PLUGIN_MANAGER_CONTENT,
            TEMPLATE_PLUGIN_NAME: TEMPLATE_PLUGIN_CONTENT,
            HOTKEYS_PLUGIN_NAME: HOTKEYS_PLUGIN_CONTENT,
            RESIZE_NOTIFIER_PLUGIN_NAME: RESIZE_NOTIFIER_PLUGIN_CONTENT,
            PIXEL_RATIO_PLUGIN_NAME: PIXEL_RATIO_PLUGIN_CONTENT,
            CONTEXT_PLUGIN_NAME: CONTEXT_PLUGIN_CONTENT,
        }

        self.modules_dir.mkdir(parents=True, exist_ok=True)

        updated = False
        for name, content in samples.items():
            path = self.modules_dir / name

            # 模板
            if name in {JS_INJECTOR_NAME, PLUGIN_MANAGER_NAME, TEMPLATE_PLUGIN_NAME}:
                old = path.read_text(encoding="utf-8") if path.exists() else None
                if old != content:
                    atomic_write(path, content)
                    logging.info(f"已初始化内置插件：{name}")
                continue

            # 其余示例：仅在缺失时写入
            key = name.removesuffix(".py")
            if not path.exists():
                atomic_write(path, content)
                logging.info(f"已生成内置插件：{name}")
            if key not in self.cfg.mods:
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
        self.modules_dir.mkdir(parents=True, exist_ok=True)
        updated = False

        # 1. 核心插件：所有 _CORE_*.py
        for py in sorted(self.modules_dir.glob("_CORE_*.py"), key=lambda p: p.stem):
            self._exec_plugin(py, window, api)

        # 2. 用户插件：按 config.mods TOML 中声明的顺序
        for name, enabled in self.cfg.mods.items():
            if name.startswith("CORE_"):   # 跳过已经在第一阶段加载过的
                continue
            path = self.modules_dir / f"{name}.py"
            if not path.exists():
                if enabled:
                    logging.warning(f"自动禁用不存在的插件：{name}")
                    self.cfg.mods[name] = False
                    updated = True
                continue
            if enabled:
                self._exec_plugin(path, window, api)
            else:
                logging.info(f"插件已禁用：{name}.py")

        # 3. 同步任何自动禁用操作
        if updated:
            self.cfg.data["Modules"] = self.cfg.mods
            self.cfg.save()

    def _exec_plugin(self, py: Path, window: webview.Window, api: Api) -> None:
        name = py.stem
        lines = py.read_text(encoding="utf-8").splitlines()
        head = lines[0].strip() if lines else ""
        tail = lines[-1].strip() if lines else ""
        if DISABLE_MARK in head or DISABLE_MARK in tail:
            logging.info(f"插件标记停用，跳过加载：{py.name}")
            return

        logging.info(f"注入插件：{py.name}")
        scope = {
            "logging": logging,
            "json": json,
            "Path": Path,
            "sys": sys,
            "data_dir": self.data_dir,
            "app_cfg": self.cfg.app,
            "mod_cfg": self.cfg.mods,
            "cfg": self.cfg,
            "atomic_write": atomic_write,
            "enable_high_dpi": enable_high_dpi,
            "window": window,
            "api": api
        }
        try:
            exec(py.read_text(encoding="utf-8"), scope)
            runner = scope.get("run")
            if callable(runner):
                runner(window, api)
                logging.info(f"插件加载成功：{py.name}")
            else:
                logging.warning(f"{py.name} 缺少 run() 方法")
        except Exception as e:
            logging.error(f"插件加载失败：{py.name} - {e}")

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
    root.geometry("500x300")
    root.minsize(300, 200)
    root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))

    # 使用 grid 布局
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    frame = tk.Frame(root)
    frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))

    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    lst = tk.Listbox(frame, font=("Segoe UI", 12), yscrollcommand=scrollbar.set)
    for f in tomls:
        lst.insert("end", f.name)
    lst.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=lst.yview)

    lst.focus_set()

    chosen: dict[str, Path] = {"file": None}
    def confirm(event=None):
        sel = lst.curselection()
        if sel:
            chosen["file"] = tomls[sel[0]]
            root.destroy()

    lst.bind("<Double-Button-1>", confirm)
    lst.bind("<Return>", confirm)

    btn = tk.Button(root, text="选择", command=confirm)
    btn.grid(row=1, column=0, sticky="ew", padx=10, pady=10)

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

        # 创建 API 桥接器（行为由插件绑定）
        api = Api(None)

        # 创建窗口并绑定 API（必须在此处传入 js_api）
        window = webview.create_window(
            title=app["Title"], url=app["URL"],
            width=app["Width"], height=app["Height"],
            fullscreen=app["Fullscreen"],
            resizable=app["Resizable"],
            frameless=app["Frameless"],
            on_top=app["OnTop"],
            js_api=api
        )

        # 回填 window 引用到 API 实例
        api._window = window

        # 加载插件（插件负责所有行为逻辑）
        pm = PluginManager(modules_dir, cfg, data_dir)
        pm.generate_samples()
        pm.sync()
        pm.load_all(window, api)

        # 记忆窗口大小（插件可重写此行为）
        if app.get("RememberSize"):
            def save_size():
                w, h = window.get_size()
                atomic_write(size_file, json.dumps({"width": w, "height": h}))
            window.events.closing += save_size

        # webview.start(debug=True)
        webview.start()

        # 清理缓存（插件可重写此行为）
        if app.get("ClearCache"):
            shutil.rmtree(data_dir / "cache", ignore_errors=True)

    except Exception as e:
        show_error("启动失败", "".join(traceback.format_exception_only(type(e), e)))

if __name__ == "__main__":
    main()
