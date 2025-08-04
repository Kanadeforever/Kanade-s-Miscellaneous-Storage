# ——— 重构后的主脚本（模块式单文件结构） ———
import os
import shlex
import subprocess
import configparser
import tkinter as tk
from tkinter import ttk, messagebox

CONFIG_PATH = 'config.ini'

# ---------------- Config 处理 ----------------
def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding='utf-8')

    # 确保 Settings 节存在
    if not config.has_section("Settings"):
        config.add_section("Settings")
    # config["Settings"].pop("UseRelativePath", None)
    config["Settings"].setdefault("WaitForExeExit", "False")

    # 确保 AutoScan 节存在
    if not config.has_section("AutoScan"):
        config.add_section("AutoScan")
    config["AutoScan"].setdefault("Enabled", "True")
    config["AutoScan"].pop("Args", None)

    save_config(config)
    return config

def save_config(config):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        config.write(f)

def check_and_clean_paths(config):
    changed = False
    for section in config.sections():
        cfg = config[section]
        if "Path" in cfg:
            path = cfg.get("Path", "")
            abs_path = os.path.join(os.getcwd(), path)
            if not os.path.isfile(abs_path):
                config.remove_section(section)
                changed = True
    if changed:
        save_config(config)

# ---------------- EXE 执行 ----------------
def run_exe(path, args, wait_after, root=None):
    try:
        proc = subprocess.Popen([path] + shlex.split(args))
        if wait_after and root:
            root.withdraw() # 隐藏窗口
            root.after(200, lambda: check_proc(proc, root))
        elif root:
            root.quit()
            root.destroy()
    except Exception as e:
        messagebox.showerror("执行错误", f"无法运行：{path}\n{str(e)}")

def check_proc(proc, root):
    if proc.poll() is None:
        root.after(200, lambda: check_proc(proc, root))
    else:
        root.quit()
        root.destroy()

def get_all_exe_files(root_dir):
    return [os.path.join(dirpath, f)
            for dirpath, _, files in os.walk(root_dir)
            for f in files if f.lower().endswith('.exe')]

# ---------------- 配置提取 ----------------
def extract_buttons_info_from_config(config):
    return [
        {"label": cfg.get("btnName", "").strip(),
         "path": cfg.get("Path", "").strip(),
         "args": cfg.get("Args", "").strip()}
        for section, cfg in config.items()
        if cfg.get("btnName", "").strip() and cfg.get("Path", "").strip()
    ]

def persist_auto_exes(config, exe_list):
    # 删除旧式 section：以 AutoExe 或 Exe 开头的
    for section in list(config.sections()):
        if section.startswith("AutoExe") or section.startswith("Exe"):
            config.remove_section(section)

    # 收集已存在的标准化路径集合
    existing_paths = {
        os.path.normpath(cfg.get("Path", "")).lower()
        for cfg in config.values() if "Path" in cfg
    }

    # 用于统计 exe_name 的出现次数（避免重复）
    name_counter = {}

    for path in exe_list:
        rel_path = os.path.relpath(path, os.getcwd())
        norm_path = os.path.normpath(rel_path).lower()
        if norm_path in existing_paths:
            continue  # 跳过已存在路径

        exe_name = os.path.basename(path)
        section_name = exe_name

        # 检查是否已存在同名 section
        count = name_counter.get(exe_name, 0)
        if count:
            section_name = f"{exe_name}_{count:03d}"
        name_counter[exe_name] = count + 1

        config[section_name] = {
            "Path": rel_path,
            "btnName": exe_name,
            "Args": ""  # 添加默认空参数字段，避免后续提取失败
        }

    save_config(config)

# ---------------- GUI 构建 ----------------
def build_gui(buttons, config, use_chinese=True):
    root = tk.Tk()
    root.title("简易启动器")
    root.geometry("360x290")
    root.resizable(False, False)

    wait_exit_var = tk.BooleanVar(value=config["Settings"].getboolean("WaitForExeExit"))
    auto_scan_var = tk.BooleanVar(value=config["AutoScan"].getboolean("Enabled"))

    wait_exit_var.trace_add("write", lambda *_: update_flag(config, "WaitForExeExit", wait_exit_var.get()))
    auto_scan_var.trace_add("write", lambda *_: update_flag(config, "Enabled", auto_scan_var.get(), "AutoScan"))

    page_frame = ttk.Frame(root)
    page_frame.pack(pady=12, fill=tk.BOTH, expand=True)

    nav_frame = ttk.Frame(root)
    nav_frame.pack(side="bottom", pady=6)

    options_frame = ttk.Frame(root)
    options_frame.pack(side="bottom", pady=6)

    ttk.Checkbutton(options_frame, text="等待 EXE 结束", variable=wait_exit_var).grid(row=0, column=0, padx=10)
    ttk.Checkbutton(options_frame, text="启用自动扫描", variable=auto_scan_var).grid(row=0, column=2, padx=10)

    ttk.Button(nav_frame, text="<< 上一页" if use_chinese else "<< Prev",
               command=lambda: show_page(max(current_page.get() - 1, 0))) \
        .pack(side="left", padx=6)
    page_label = ttk.Label(nav_frame, text="")
    page_label.pack(side="left", padx=4)
    ttk.Button(nav_frame, text="下一页 >>" if use_chinese else "Next >>",
               command=lambda: show_page(min(current_page.get() + 1, total_pages - 1))) \
        .pack(side="left", padx=6)

    max_per_page = 10
    total_pages = (len(buttons) + max_per_page - 1) // max_per_page
    current_page = tk.IntVar(value=0)

    def center_window(window):
        window.update_idletasks()  # 更新当前大小
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = window.winfo_width()
        height = window.winfo_height()
        x = int((screen_width / 2) - (width / 2))
        y = int((screen_height / 2) - (height / 2))
        window.geometry(f"{width}x{height}+{x}+{y}")

    def show_page(page_idx):
        for widget in page_frame.winfo_children():
            widget.destroy()
        start, end = page_idx * max_per_page, (page_idx + 1) * max_per_page
        for i, item in enumerate(buttons[start:end]):
            row, col = i % 5, i // 5
            exe_path = os.path.join(os.getcwd(), item["path"])
            ttk.Button(page_frame, text=item["label"],
                       command=lambda p=exe_path, a=item["args"]:
                       run_exe(p, a, wait_exit_var.get(), root)) \
                .grid(row=row, column=col, padx=10, pady=6, sticky="w")

        label_text = f"第 {page_idx + 1} 页" if use_chinese else f"Page {page_idx + 1}"
        total_text = f"共 {total_pages} 页" if use_chinese else f"of {total_pages}"
        page_label.config(text=f"【{label_text} / {total_text}】")
        current_page.set(page_idx)

    root.protocol("WM_DELETE_WINDOW", lambda: [save_config(config), root.destroy()])
    show_page(0)
    center_window(root)
    root.mainloop()

def update_flag(config, key, value, section="Settings"):
    config[section][key] = str(value)
    save_config(config)

# ---------------- 主入口 ----------------
if __name__ == "__main__":
    config = load_config()
    check_and_clean_paths(config)

    if config["AutoScan"].getboolean("Enabled"):
        exe_list = get_all_exe_files(os.getcwd())
        persist_auto_exes(config, exe_list)

    buttons = extract_buttons_info_from_config(config)
    build_gui(buttons, config, use_chinese=True)