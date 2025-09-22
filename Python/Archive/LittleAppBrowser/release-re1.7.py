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
import threading
import tkinter as tk
import webview
from urllib.parse import urlparse
from collections import deque
import tomllib  # Python 3.13 标准库

# 常量定义
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

        center_window(root)
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
import logging

def run(window, api):
    logging.info("hotkeys: run() called")

    js_code = """
        document.addEventListener("keydown", function(e) {
            const ctrl = e.ctrlKey || e.metaKey;
            const shift = e.shiftKey;

            // Ctrl+R or F5 → Reload
            if ((ctrl && e.key === "r") || e.key === "F5") {
                location.reload();
                e.preventDefault();
            }

            // Ctrl+Shift+R → Hard reload (simulate)
            if (ctrl && shift && e.key === "R") {
                location.href = location.href;
                e.preventDefault();
            }

            // F11 → Toggle fullscreen via Python API
            if (e.key === "F11") {
                if (window.pywebview && window.pywebview.api) {
                    window.pywebview.api.toggle_fullscreen();
                    e.preventDefault();
                }
            }
        });
    """
    api.inject_js(js_code)
    logging.info("hotkeys: JS 注入完成")

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

def run(window, api):
    logging.info("context_menu: run() called")

    js_code = r"""
        // 强制允许选中
        document.body.style.userSelect = "text";

        // 移除禁止选中的事件监听
        document.addEventListener("selectstart", e => e.stopPropagation(), true);
        document.addEventListener("mousedown", e => e.stopPropagation(), true);

        const menu = document.createElement("div");
        menu.id = "custom-context-menu";
        menu.style.position = "fixed";
        menu.style.zIndex = "9999";
        menu.style.background = "#fff";
        menu.style.border = "1px solid #ccc";
        menu.style.boxShadow = "2px 2px 6px rgba(0,0,0,0.2)";
        menu.style.display = "none";
        menu.style.padding = "8px 0";
        menu.style.fontFamily = "Segoe UI, sans-serif";
        menu.style.minWidth = "180px";

        const items = [
            { label: "🔙 后退", action: () => history.back() },
            { label: "🔜 前进", action: () => history.forward() },
            { label: "🏠 返回主页", action: () => location.href = window.location.origin },
            { label: "🔄 刷新页面", action: () => location.reload() },
            { label: "🖥️ 切换全屏", action: () => {
                if (window.pywebview && window.pywebview.api) {
                    window.pywebview.api.toggle_fullscreen();
                }
            }},
            { label: "🕵️ 查看页面信息", action: () => {
                const info = {
                    "📄 页面标题": document.title,
                    "🔗 页面地址": location.href,
                    "↩️ 页面来源": document.referrer || "无",
                    "🖥️ 分辨率": window.innerWidth + " × " + window.innerHeight,
                    "🌐 浏览器 UA": navigator.userAgent,
                    "🗣️ 语言": navigator.language,
                    "🖥️ 是否全屏": document.fullscreenElement ? "是" : "否",
                    "⏱️ 插件注入时间": new Date().toLocaleString()
                };

                const overlay = document.createElement("div");
                overlay.style.position = "fixed";
                overlay.style.top = "0"; overlay.style.left = "0";
                overlay.style.width = "100%"; overlay.style.height = "100%";
                overlay.style.background = "rgba(0,0,0,0.3)";
                overlay.style.zIndex = "9999";

                const box = document.createElement("div");
                box.style.background = "#fff";
                box.style.padding = "20px";
                box.style.margin = "5% auto";
                box.style.width = "80%";
                box.style.maxWidth = "600px";
                box.style.borderRadius = "8px";
                box.style.boxShadow = "0 4px 12px rgba(0,0,0,0.2)";
                box.style.fontFamily = "Segoe UI, sans-serif";
                box.style.whiteSpace = "pre-wrap";

                const title = document.createElement("h2");
                title.textContent = "🕵️ 页面信息";
                title.style.marginBottom = "10px";
                box.appendChild(title);

                const pre = document.createElement("pre");
                pre.style.userSelect = "text";
                pre.style.fontSize = "14px";
                pre.style.lineHeight = "1.6";
                pre.style.whiteSpace = "pre-wrap";
                pre.style.wordBreak = "break-word";  // ✅ 自动换行长行内容
                pre.textContent = Object.entries(info).map(function([k, v]) {
                    return k + ": " + v;
                }).join("\n");
                box.appendChild(pre);

                const closeBtn = document.createElement("button");
                closeBtn.textContent = "关闭";
                closeBtn.style.marginTop = "10px";
                closeBtn.onclick = () => document.body.removeChild(overlay);
                box.appendChild(closeBtn);

                overlay.appendChild(box);
                document.body.appendChild(overlay);
            }},
            { label: "❌ 关闭菜单", action: () => hideMenu() }
        ];

        items.forEach(item => {
            const btn = document.createElement("div");
            btn.textContent = item.label;
            btn.style.padding = "6px 16px";
            btn.style.cursor = "pointer";
            btn.onmouseover = () => btn.style.background = "#eee";
            btn.onmouseout = () => btn.style.background = "#fff";
            btn.onclick = () => {
                item.action();
                hideMenu();
            };
            menu.appendChild(btn);
        });

        document.body.appendChild(menu);

        document.addEventListener("contextmenu", function(e) {
            e.preventDefault();
            menu.style.left = e.pageX + "px";
            menu.style.top = e.pageY + "px";
            menu.style.display = "block";
        });

        document.addEventListener("click", hideMenu);
        function hideMenu() {
            menu.style.display = "none";
        }
    """
    api.inject_js(js_code)
    logging.info("context_menu: JS 注入完成")
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
LocalStorage = true  # 是否启用本地存储
DiskCache    = true  # 是否启用磁盘缓存
JavaScript   = true  # 是否启用 JavaScript
Zoomable     = true  # 是否允许页面缩放

[Modules]
resize_notifier = true
pixel_ratio     = false
hotkeys         = true
context_menu    = true
"""

ENHANCED_TEMPLATE_PLUGIN_NAME = "[DEMO]_enhanced_template.py"
ENHANCED_TEMPLATE_PLUGIN_CONTENT = r'''
# PLUGIN-DISABLE
"""
enhanced_template.py - 增强版插件模板
版本: 1.1.0
描述: 展示如何使用增强的插件API开发功能丰富的插件
"""

# 插件元数据（可选）
__version__ = "1.1.0"
__author__ = "插件开发者"
__depends__ = []  # 依赖的其他插件名称列表
__description__ = "增强版插件模板"

def setup(window, api):
    """插件初始化函数（可选）"""
    logging.info(f"🔄 初始化插件: {__plugin_name__}")

def run(window, api):
    """插件主函数（必需）"""
    logging.info(f"🚀 启动插件: {__plugin_name__}")
    
    # 1. 使用增强的API功能
    js_code = """
    console.log("🎯 增强插件已加载!");
    
    // 添加自定义样式
    const style = document.createElement('style');
    style.textContent = `
        .plugin-indicator {{
            position: fixed;
            top: 10px;
            right: 10px;
            background: #4CAF50;
            color: white;
            padding: 5px 10px;
            border-radius: 15px;
            font-size: 12px;
            z-index: 10000;
        }}
    `;
    document.head.appendChild(style);
    
    // 添加插件指示器
    const indicator = document.createElement('div');
    indicator.className = 'plugin-indicator';
    indicator.textContent = '🔧 插件运行中';
    document.body.appendChild(indicator);
    """
    
    api.inject_js(js_code)
    
    # 2. 访问Web数据目录
    plugin_storage = plugin_data_dir / "storage.json"
    atomic_write(plugin_storage, json.dumps({
        "last_run": __import__("datetime").datetime.now().isoformat(),
        "run_count": 1
    }))
    
    # 3. 创建Tkinter界面（可选）
    def create_plugin_panel():
        root = tk.Tk()
        root.title(f"插件面板 - {__plugin_name__}")
        root.geometry("300x200")
        
        label = tk.Label(root, text=f"欢迎使用 {__plugin_name__}!")
        label.pack(pady=20)
        
        # 添加插件功能按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=10)
        
        tk.Button(btn_frame, text="刷新页面", 
                 command=lambda: window.evaluate_js("location.reload()")).pack(side=tk.LEFT, padx=5)
        
        tk.Button(btn_frame, text="页面信息", 
                 command=lambda: print(api.get_page_info())).pack(side=tk.LEFT, padx=5)
        
        center_window(root)
        return root
    
    # 4. 注册快捷键打开插件面板
    api._plugin_callbacks[f"open_{__plugin_name__}_panel"] = create_plugin_panel
    
    window.events.loaded += lambda: window.evaluate_js(f"""
        console.log("注册插件快捷键: {__plugin_name__}");
        window.addEventListener('keydown', e => {{
            if (e.ctrlKey && e.altKey && e.key === '{__plugin_name__[0].upper()}') {{
                if (window.pywebview?.api?.open_{__plugin_name__}_panel) {{
                    window.pywebview.api.open_{__plugin_name__}_panel();
                }}
            }}
        }});
    """)
    
    # 5. 使用配置系统
    if plugin_cfg.get("auto_refresh", False):
        interval = plugin_cfg.get("refresh_interval", 60)
        window.events.loaded += lambda: window.evaluate_js(f"""
            setInterval(() => location.reload(), {interval * 1000});
        """)

def teardown():
    """插件清理函数（可选）"""
    logging.info(f"🧹 清理插件: {__plugin_name__}")
    # 清理临时文件等资源
'''

# 禁止加载插件的配置符
DISABLE_MARK = "# PLUGIN-DISABLE"

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

def center_window(win: tk.Toplevel | tk.Tk):
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")

_active_errors: list[tk.Toplevel] = []

def show_error(title: str, message: str) -> None:
    """弹窗显示错误并退出。"""
    def popup():
        if len(_active_errors) >= 3:
            return

        enable_high_dpi()
        root = tk.Tk()
        root.withdraw()

        win = tk.Toplevel(root)
        win.title(title)
        win.geometry("500x300")
        win.minsize(300, 200)

        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

        txt = tk.Text(win, wrap="word")
        txt.insert("1.0", message)
        txt.config(state="disabled")
        txt.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))

        btn = tk.Button(win, text="关闭", command=win.destroy)
        btn.grid(row=1, column=0, sticky="ew", padx=10, pady=10)

        def on_close():
            if win in _active_errors:
                _active_errors.remove(win)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        _active_errors.append(win)
        center_window(win)
        win.mainloop()

    threading.Thread(target=popup, daemon=True).start()

# -----------------------------------------------------------------------------
# 配置管理 - 使用 tomllib
# -----------------------------------------------------------------------------
class ConfigManager:
    allowed_fields = {
        "URL", "Title", "Width", "Height", "Fullscreen",
        "OnTop", "Resizable", "Frameless", "RememberSize", "ClearCache",
        "LocalStorage", "DiskCache", "JavaScript", "Zoomable"
    }
    bool_fields = {
        "Fullscreen", "OnTop", "Resizable", "Frameless", 
        "RememberSize", "ClearCache", "LocalStorage", "DiskCache", 
        "JavaScript", "Zoomable"
    }

    def __init__(self, path: Path):
        self.path = path
        self.data = self._load_toml()
        self.app = self.data.get("App", {})
        self.mods = self.data.get("Modules", {})

    def _load_toml(self) -> dict:
        """使用标准 tomllib 解析配置"""
        try:
            with open(self.path, 'rb') as f:
                return tomllib.load(f)
        except Exception as e:
            raise ValueError(f"TOML 解析失败: {e}")

    def validate(self) -> None:
        errs = []

        # 设置默认值
        defaults = {
            "Title": "Web App",
            "Width": 1024,
            "Height": 768,
            "Fullscreen": False,
            "OnTop": False,
            "Resizable": True,
            "Frameless": False,
            "RememberSize": False,
            "ClearCache": True,
            "LocalStorage": True,
            "DiskCache": True,
            "JavaScript": True,
            "Zoomable": True
        }
        
        for key, default in defaults.items():
            if key not in self.app:
                self.app[key] = default

        # 验证字段
        for key in self.app:
            if key not in self.allowed_fields:
                errs.append(f"未知字段：{key}")

        # URL 检查 - 支持更多协议和格式
        # 支持的URL格式如下：
        # https://example.com （HTTP）
        # index.html （本地文件，从data目录加载）
        # data:text/html,<h1>Hello World</h1> （Data URL）
        # file:///absolute/path/to/file.html （绝对路径，但注意file协议需要绝对路径）
        url = self.app.get("URL", "")
        if not isinstance(url, str) or not url.strip():
            errs.append("App.URL 必须是非空字符串")

        # 增强URL验证
        parsed = urlparse(url)
        if not parsed.scheme and not Path(url).exists():
            # 相对路径检查
            if not (Path(__file__).parent / url).exists():
                errs.append(f"URL 路径不存在: {url}")

        for dim in ("Width", "Height"):
            val = self.app.get(dim, 0)
            if not (isinstance(val, int) and val > 0):
                errs.append(f"App.{dim} 必须是大于 0 的整数")

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
        """保存配置到 TOML 文件"""
        def _serialize_toml(data, level=0):
            lines = []
            for key, value in data.items():
                if isinstance(value, dict):
                    lines.append(f"[{key}]")
                    for k, v in value.items():
                        if isinstance(v, bool):
                            v = "true" if v else "false"
                        elif isinstance(v, str):
                            v = f'"{v}"'
                        lines.append(f"{k} = {v}")
                    lines.append("")
                else:
                    if isinstance(value, bool):
                        value = "true" if value else "false"
                    elif isinstance(value, str):
                        value = f'"{value}"'
                    lines.append(f"{key} = {value}")
            return lines

        content = "\n".join(_serialize_toml(self.data))
        atomic_write(self.path, content)

# -----------------------------------------------------------------------------
# WebView 数据目录管理
# -----------------------------------------------------------------------------
class WebDataManager:
    """管理 WebView 所有数据的存储位置"""
    
    def __init__(self, base_data_dir: Path):
        self.base_dir = base_data_dir
        self.webdata_dir = base_data_dir / "webdata"
        self._setup_directories()
    
    def _setup_directories(self):
        """创建必要的目录结构"""
        directories = [
            self.webdata_dir,
            self.webdata_dir / "localstorage",
            self.webdata_dir / "cache",
            self.webdata_dir / "cookies",
            self.webdata_dir / "indexeddb",
            self.webdata_dir / "websql"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def get_storage_path(self, storage_type: str) -> Path:
        """获取特定类型数据的存储路径"""
        path_map = {
            "local_storage": self.webdata_dir / "localstorage",
            "disk_cache": self.webdata_dir / "cache", 
            "cookies": self.webdata_dir / "cookies",
            "indexed_db": self.webdata_dir / "indexeddb",
            "web_sql": self.webdata_dir / "websql"
        }
        return path_map.get(storage_type, self.webdata_dir)
    
    def clear_cache(self):
        """清理缓存数据，保留重要数据"""
        cache_dirs = ["cache"]
        for dir_name in cache_dirs:
            dir_path = self.webdata_dir / dir_name
            if dir_path.exists():
                shutil.rmtree(dir_path)
                dir_path.mkdir()
    
    def clear_all(self):
        """清理所有 Web 数据"""
        if self.webdata_dir.exists():
            shutil.rmtree(self.webdata_dir)
        self._setup_directories()

# -----------------------------------------------------------------------------
# 增强的 WebView API
# -----------------------------------------------------------------------------
class EnhancedApi:
    """增强的 API，支持更多浏览器功能"""
    
    def __init__(self, window: webview.Window | None, webdata_manager: WebDataManager):
        self._window = window
        self._webdata = webdata_manager
        self._plugin_callbacks = {}
        self._teardown_functions = []
        self._application_start_callbacks = []

    def toggle_fullscreen(self) -> None:
        try:
            self._window.toggle_fullscreen()
        except Exception as e:
            logging.warning(f"切换全屏失败：{e}")

    def inject_js(self, js: str) -> None:
        self._window.events.loaded += lambda js=js: self._window.evaluate_js(js)

    def open_dev_tools(self):
        """打开开发者工具"""
        try:
            self._window.evaluate_js("""
                if (window.chrome && window.chrome.devtools) {
                    window.chrome.devtools.inspectedWindow.reload();
                }
            """)
        except Exception as e:
            logging.warning(f"打开开发者工具失败：{e}")

    def set_zoom(self, zoom_level: float):
        """设置页面缩放级别"""
        try:
            self._window.evaluate_js(f"document.body.style.zoom = '{zoom_level}'")
        except Exception as e:
            logging.warning(f"设置缩放失败：{e}")

    def get_page_info(self) -> dict:
        """获取页面信息"""
        try:
            return self._window.evaluate_js("""
                {
                    title: document.title,
                    url: location.href,
                    referrer: document.referrer,
                    width: window.innerWidth,
                    height: window.innerHeight,
                    userAgent: navigator.userAgent,
                    language: navigator.language
                }
            """)
        except Exception as e:
            logging.warning(f"获取页面信息失败：{e}")
            return {}

    def open_plugin_manager(self):
        cb = getattr(self, "_plugin_callbacks", {}).get("open_plugin_manager")
        if callable(cb):
            cb()
        else:
            logging.warning("找不到 open_plugin_manager 回调")

    def on_application_start(self, callback):
        if callable(callback):
            self._application_start_callbacks.append(callback)
        else:
            logging.warning("on_application_start 回调必须是可调用对象")

    def run_teardown(self):
        for func in self._teardown_functions:
            try:
                func()
            except Exception as e:
                logging.error(f"teardown 函数执行失败: {e}")

# -----------------------------------------------------------------------------
# 增强的插件管理器
# -----------------------------------------------------------------------------
class EnhancedPluginManager:
    def __init__(self, modules_dir: Path, cfg: ConfigManager, data_dir: Path, webdata_manager: WebDataManager):
        self.modules_dir = modules_dir
        self.cfg = cfg
        self.data_dir = data_dir
        self.webdata = webdata_manager
        self.plugins_metadata = {}

    def generate_samples(self) -> None:
        samples = {
            JS_INJECTOR_NAME: JS_INJECTOR_CONTENT,
            PLUGIN_MANAGER_NAME: PLUGIN_MANAGER_CONTENT,
            ENHANCED_TEMPLATE_PLUGIN_NAME: ENHANCED_TEMPLATE_PLUGIN_CONTENT,
            HOTKEYS_PLUGIN_NAME: HOTKEYS_PLUGIN_CONTENT,
            RESIZE_NOTIFIER_PLUGIN_NAME: RESIZE_NOTIFIER_PLUGIN_CONTENT,
            PIXEL_RATIO_PLUGIN_NAME: PIXEL_RATIO_PLUGIN_CONTENT,
            CONTEXT_PLUGIN_NAME: CONTEXT_PLUGIN_CONTENT,
        }

        self.modules_dir.mkdir(parents=True, exist_ok=True)

        updated = False
        for name, content in samples.items():
            path = self.modules_dir / name

            # 核心插件和模板：总是更新到最新版本
            if name in {JS_INJECTOR_NAME, PLUGIN_MANAGER_NAME, ENHANCED_TEMPLATE_PLUGIN_NAME}:
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
            logging.info("已同步插件禁用状态到配置文件")

    def load_all(self, window: webview.Window, api: EnhancedApi) -> None:
        """加载所有启用的插件"""
        plugins_info = self._collect_plugins_info()
        
        # 分离核心插件和用户插件
        core_plugins = [p for p in plugins_info if p['path'].stem.startswith('_CORE_')]
        user_plugins = [p for p in plugins_info if not p['path'].stem.startswith('_CORE_')]
        
        # 先加载核心插件
        for info in sorted(core_plugins, key=lambda x: x['path'].stem):
            if info['enabled']:
                self._load_single_plugin(info, window, api)
        
        # 加载用户插件（按依赖顺序）
        sorted_plugins = self._resolve_dependencies(user_plugins)
        for info in sorted_plugins:
            if info['enabled']:
                self._load_single_plugin(info, window, api)

    def _collect_plugins_info(self) -> list[dict]:
        """收集所有插件的信息，包括依赖"""
        plugins_info = []
        for py in self.modules_dir.glob("*.py"):
            info = self._get_plugin_metadata(py)
            plugins_info.append(info)
        return plugins_info

    def _get_plugin_metadata(self, py: Path) -> dict:
        """获取插件的元数据，如 __depends__"""
        name = py.stem
        metadata = {
            'path': py,
            'depends': [],
            'enabled': self.cfg.mods.get(name, False) if not name.startswith('_CORE_') else True
        }
        try:
            code = py.read_text(encoding="utf-8")
            scope = {}
            exec(code, scope)
            if "__depends__" in scope:
                metadata['depends'] = scope["__depends__"]
        except Exception as e:
            logging.error(f"获取插件元数据失败：{py.name} - {e}")
        return metadata

    def _resolve_dependencies(self, plugins_info: list) -> list:
        """解析插件依赖关系"""
        graph = {}
        plugin_map = {}
        
        for info in plugins_info:
            name = info['path'].stem
            plugin_map[name] = info
            graph[name] = info.get('depends', [])
        
        visited = set()
        result = []
        
        def visit(plugin_name):
            if plugin_name in visited:
                return
            if plugin_name not in plugin_map:
                logging.warning(f"依赖的插件不存在: {plugin_name}")
                return
            visited.add(plugin_name)
            for dep in graph[plugin_name]:
                visit(dep)
            result.append(plugin_name)
        
        for name in list(graph.keys()):
            visit(name)
        
        return [plugin_map[name] for name in result if name in plugin_map]

    def _load_single_plugin(self, info: dict, window: webview.Window, api: EnhancedApi) -> None:
        py = info['path']
        name = py.stem
        
        # 检查禁用标记
        lines = py.read_text(encoding="utf-8").splitlines()
        head = lines[0].strip() if lines else ""
        tail = lines[-1].strip() if lines else ""
        if DISABLE_MARK in head or DISABLE_MARK in tail:
            logging.info(f"插件标记停用，跳过加载：{py.name}")
            return

        if not info['enabled']:
            logging.info(f"插件已禁用，跳过加载：{py.name}")
            return

        # 创建插件专用的数据目录
        plugin_data_dir = self.data_dir / "plugins" / name
        plugin_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 增强的插件执行环境
        plugin_scope = {
            "logging": logging,
            "json": json,
            "Path": Path,
            "tkinter": tk,
            "threading": threading,
            "data_dir": self.data_dir,
            "webdata_dir": self.webdata.webdata_dir,
            "plugin_data_dir": plugin_data_dir,
            "app_cfg": self.cfg.app,
            "mod_cfg": self.cfg.mods,
            "cfg": self.cfg,
            "plugin_cfg": self.cfg.data.get(name, {}),
            "window": window,
            "api": api,
            "webview": webview,
            "atomic_write": atomic_write,
            "center_window": center_window,
            "enable_high_dpi": enable_high_dpi,
            "show_error": show_error,
            "__plugin_name__": name,
        }
        
        try:
            code = py.read_text(encoding="utf-8")
            compiled = compile(code, f"plugin:{name}", "exec")
            exec(compiled, plugin_scope)
            
            # 执行插件入口函数
            if 'setup' in plugin_scope and callable(plugin_scope['setup']):
                plugin_scope['setup'](window, api)
                
            if 'run' in plugin_scope and callable(plugin_scope['run']):
                plugin_scope['run'](window, api)
                
            # 注册清理函数
            if 'teardown' in plugin_scope and callable(plugin_scope['teardown']):
                api._teardown_functions.append(plugin_scope['teardown'])
                
            logging.info(f"✅ 插件加载成功: {name}")
            
        except Exception as e:
            logging.error(f"❌ 插件加载失败: {name} - {e}")

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
            modules_dir / ENHANCED_TEMPLATE_PLUGIN_NAME,
            ENHANCED_TEMPLATE_PLUGIN_CONTENT
        )

        show_error(
            "配置缺失",
            f"未检测到 .toml 配置，已生成：\n"
            f"  • {tpl.name}\n"
            f"  • {modules_dir.name}/{ENHANCED_TEMPLATE_PLUGIN_NAME}\n\n"
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

    center_window(root)
    root.mainloop()

    if not chosen["file"]:
        sys.exit("未选择配置，程序退出。")
    return chosen["file"]

def prepare_data_dir(cfg_path: Path) -> Path:
    dname = cfg_path.with_suffix("").name + "_data"
    p = EXE_DIR / dname
    p.mkdir(parents=True, exist_ok=True)
    return p

def create_enhanced_window(config: dict, webdata_manager: WebDataManager) -> tuple[webview.Window, EnhancedApi]:
    """创建功能完整的 WebView 窗口"""
    
    # 处理 URL
    url = config["URL"]
    parsed = urlparse(url)
    if not parsed.scheme:
        local_path = webdata_manager.base_dir / url
        if local_path.exists():
            url = f"file://{local_path.absolute()}"
        else:
            exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
            local_path = exe_dir / url
            if local_path.exists():
                url = f"file://{local_path.absolute()}"
    
    # 创建窗口配置
    window_args = {
        "title": config["Title"],
        "url": url,
        "width": config["Width"],
        "height": config["Height"],
        "fullscreen": config["Fullscreen"],
        "resizable": config["Resizable"],
        "frameless": config["Frameless"],
        "on_top": config["OnTop"],
        "min_size": (400, 300),
    }
    
    # 添加数据存储配置
    if config.get("LocalStorage", True):
        window_args["local_storage_path"] = str(webdata_manager.get_storage_path("local_storage"))
    
    if config.get("DiskCache", True):
        window_args["disk_cache_path"] = str(webdata_manager.get_storage_path("disk_cache"))
    
    window_args.update({
        "text_select": True,
        "zoomable": config.get("Zoomable", True),
        "confirm_close": False,
    })
    
    # 创建 API 实例
    api = EnhancedApi(None, webdata_manager)
    window_args["js_api"] = api
    
    window = webview.create_window(**window_args)
    api._window = window
    
    return window, api

def main():
    try:
        parser = argparse.ArgumentParser(description="启动增强版 WebView 应用")
        parser.add_argument("-c", "--config", type=Path, help="指定 .toml 配置文件")
        args = parser.parse_args()

        cfg_path = find_or_create_config(args.config)
        cfg = ConfigManager(cfg_path)
        cfg.validate()

        data_dir = prepare_data_dir(cfg_path)
        
        # 设置 Web 数据管理
        webdata_manager = WebDataManager(data_dir)
        
        # 清理缓存（如果配置要求）
        if cfg.app.get("ClearCache", True):
            webdata_manager.clear_cache()

        # 设置日志
        LogManager(data_dir).setup()

        # 创建增强的 WebView 窗口
        window, api = create_enhanced_window(cfg.app, webdata_manager)

        # 加载插件
        modules_dir = data_dir / "modules"
        pm = EnhancedPluginManager(modules_dir, cfg, data_dir, webdata_manager)
        pm.generate_samples()
        pm.sync()
        pm.load_all(window, api)

        # 触发应用启动事件
        for callback in api._application_start_callbacks:
            try:
                callback()
            except Exception as e:
                logging.error(f"应用启动事件回调执行失败: {e}")

        # 窗口大小记忆
        size_file = data_dir / "window_size.json"
        if cfg.app.get("RememberSize"):
            def save_size():
                w, h = window.get_size()
                atomic_write(size_file, json.dumps({"width": w, "height": h}))
            window.events.closing += save_size

            # 恢复大小
            if size_file.exists():
                try:
                    j = json.loads(size_file.read_text(encoding="utf-8"))
                    w, h = j.get("width"), j.get("height")
                    if isinstance(w, int) and isinstance(h, int):
                        window.resize(w, h)
                except Exception:
                    pass

        # 注册应用关闭时的 teardown 调用
        window.events.closing += api.run_teardown

        # 启动 WebView
        webview.start(debug=False, http_server=False)

    except Exception as e:
        show_error("启动失败", "".join(traceback.format_exception_only(type(e), e)))

if __name__ == "__main__":
    main()
