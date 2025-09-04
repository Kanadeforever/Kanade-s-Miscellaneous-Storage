import os
import platform
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Windows 下隐藏子进程控制台
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def build_gui():
    # 高 DPI 感知
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("CHD/CSO 转换器 V5")
    root.geometry("950x800")
    root.resizable(True, True)

    # 应用 ttk 主题
    style = ttk.Style(root)
    os_name = platform.system()
    if os_name == "Windows":
        style.theme_use("vista")
    elif os_name == "Linux":
        style.theme_use("clam")
    elif os_name == "Darwin":
        style.theme_use("aqua")

    # ─── 文件选择区 ────────────────────────────────
    file_frame = ttk.LabelFrame(root, text="待转换文件列表")
    file_frame.pack(fill="both", padx=10, pady=6, expand=True)

    file_listbox = tk.Listbox(
        file_frame, selectmode=tk.EXTENDED, width=80, height=8
    )
    file_listbox.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

    file_scroll = ttk.Scrollbar(file_frame, orient="vertical", command=file_listbox.yview)
    file_scroll.pack(side="right", fill="y", padx=(0, 10), pady=10)
    file_listbox.config(yscrollcommand=file_scroll.set)

    btn_frame = ttk.Frame(root)
    btn_frame.pack(fill="x", padx=10, pady=(0, 10))

    def select_files():
        paths = filedialog.askopenfilenames(
            filetypes=[
                ("ISO 文件", "*.iso"),
                ("CUE 文件", "*.cue"),
                ("GDI 文件", "*.gdi"),
                ("CHD 文件", "*.chd"),
                ("CSO 文件", "*.cso"),
            ]
        )
        for p in paths:
            file_listbox.insert(tk.END, p)

    def remove_files():
        sel = list(file_listbox.curselection())
        for idx in reversed(sel):
            file_listbox.delete(idx)

    ttk.Button(btn_frame, text="选择文件", command=select_files).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="移除选中", command=remove_files).pack(side="left", padx=5)

    # ─── 日志输出区 ────────────────────────────────
    log_frame = ttk.LabelFrame(root, text="日志输出")
    log_frame.pack(fill="both", padx=10, pady=6, expand=True)

    log_text = tk.Text(log_frame, height=10, state="disabled", wrap="none")
    log_text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

    log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
    log_scroll.pack(side="right", fill="y", padx=(0, 10), pady=10)
    log_text.config(yscrollcommand=log_scroll.set)

    def append_log(msg: str):
        log_text.config(state="normal")
        log_text.insert(tk.END, msg)
        log_text.see(tk.END)
        log_text.config(state="disabled")

    # ─── 终止任务 功能 ────────────────────────────────
    def terminate_tasks(log_enabled=True):
        procs = ["chdman.exe", "maxcso.exe"]
        for proc in procs:
            try:
                if os_name == "Windows":
                    cmd = ["taskkill", "/IM", proc, "/F"]
                    res = subprocess.run(cmd,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE,
                                         creationflags=CREATE_NO_WINDOW,
                                         text=True)
                else:
                    cmd = ["pkill", "-f", proc]
                    res = subprocess.run(cmd,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE,
                                         text=True)
                if log_enabled:
                    if res.returncode == 0:
                        append_log(f"已停止进程 {proc}\n")
                    else:
                        stderr = res.stderr.strip().replace("\n", " ")
                        append_log(f"未找到或停止失败 {proc}: {stderr}\n")
            except Exception as e:
                if log_enabled:
                    append_log(f"停止进程 {proc} 时出现异常: {e}\n")

    # 在“选择文件”、“移除选中”按钮旁添加“终止任务”按钮
    ttk.Button( btn_frame, text="终止任务", command=lambda: terminate_tasks(log_enabled=True)).pack(side="right", padx=5)

    # ─── 进度显示区 ────────────────────────────────
    progress_frame = ttk.Frame(root)
    progress_frame.pack(fill="x", padx=10, pady=(0, 10))

    progress_var = tk.IntVar(value=0)
    progress_label = ttk.Label(progress_frame, text="进度: 0/0")
    progress_label.pack(side="left")

    progress_bar = ttk.Progressbar(
        progress_frame, variable=progress_var, mode="determinate"
    )
    progress_bar.pack(side="left", fill="x", expand=True, padx=(10, 0))

    # ─── 启动转换线程 ───────────────────────────────
    def start_conversion(mode: str):
        # 清空日志
        log_text.config(state="normal")
        log_text.delete("1.0", tk.END)
        log_text.config(state="disabled")

        # 开始转换线程
        files = list(file_listbox.get(0, tk.END))
        if not files:
            messagebox.showwarning("警告", "没有文件可转换")
            return

        total = len(files)
        progress_var.set(0)
        progress_bar.config(maximum=total)
        progress_label.config(text=f"进度: 0/{total}")
        append_log(f"开始 {mode.upper()} 转换，共 {total} 个文件\n")

        threading.Thread(
            target=run_conversion, args=(files, mode), daemon=True
        ).start()

    def run_conversion(files, mode: str):
        errors = []
        total = len(files)
        cmd_map = {
            "chd": ("chdman\\chdman.exe createcd -i \"{in}\" -o \"{out}\"", ".chd"),
            "cso": ("maxcso\\maxcso.exe \"{in}\" -o \"{out}\"", ".cso"),
            "cue": ("chdman\\chdman.exe extractcd -i \"{in}\" -o \"{out}\"", ".cue"),
            "iso": ("maxcso\\maxcso.exe --decompress \"{in}\" -o \"{out}\"", ".iso"),
        }

        template, ext = cmd_map[mode]
        for idx, src in enumerate(files, start=1):
            out = os.path.splitext(src)[0] + ext
            cmd = template.format_map({"in": src, "out": out})

            append_log(f"[{idx}/{total}] {mode.upper()} -> {out}\n")
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            for line in proc.stdout:
                append_log(line)
            proc.wait()

            if proc.returncode != 0:
                errors.append(f"{os.path.basename(src)} 转换失败 (返回码 {proc.returncode})\n")

            progress_var.set(idx)
            root.after(
                0, lambda i=idx: progress_label.config(text=f"进度: {i}/{total}")
            )

        if errors:
            append_log("\n以下文件转换失败：\n" + "".join(errors))
            root.after(0, lambda: messagebox.showerror("错误", "部分文件转换失败，详情见日志"))
        else:
            append_log("\n全部转换成功！\n")
            root.after(0, lambda: messagebox.showinfo("完成", "所有文件已成功转换"))

    # ─── 操作按钮区 ────────────────────────────────
    ops_frame = ttk.LabelFrame(root, text="转换操作")
    ops_frame.pack(fill="x", padx=10, pady=(0, 10))

    modes = [
        (" [C] 转换成 CHD 格式 ", "chd"),
        (" [M] 转换成 CSO 格式 ", "cso"),
        (" [C] 转换成 CUE 格式 ", "cue"),
        (" [M] 转换成 ISO 格式 ", "iso"),
    ]
    for text, mode in modes:
        ttk.Button(
            ops_frame,
            text=text,
            command=lambda m=mode: start_conversion(m)
        ).pack(side="left", padx=5, pady=10)

    # ─── 说明/介绍 弹窗 ────────────────────────────
    def show_about():
        about_win = tk.Toplevel(root)
        about_win.title("说明与介绍")
        about_win.geometry("600x500")
        about_win.resizable(False, False)
        about_win.bind("<Escape>", lambda event: about_win.destroy())

        container = ttk.Frame(about_win, padding=10)
        container.pack(fill="both", expand=True)

        text_frame = ttk.Frame(container)
        text_frame.pack(fill="both", expand=True)

        about_text = tk.Text(
            text_frame,
            wrap="word",
            state="normal",
            width=60,
            height=15,
        )
        scrollbar = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=about_text.yview
        )
        about_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        about_text.pack(side="left", fill="both", expand=True)

        about_text.insert("1.0", (
            "CHD/CSO 转换器 V4 使用说明\n\n"
            "1. 支持格式：\n"
            "   • ISO ↔ CSO\n"
            "   • ISO/CUE/GDI → CHD\n\n"
            "   • CHD → CSO\n\n"
            "2. 使用步骤：\n"
            "   - 单击“选择文件”添加镜像文件。\n"
            "   - 选中文件后点击对应转换按钮。\n"
            "   - [C]对应调用CHDMAN [M]对应调用MAXCSO\n\n"
            "3. 界面说明：\n"
            "   • 日志区显示实时运行信息。[仅支持CHDMAN]\n"
            "   • 进度条根据文件总数自动更新。\n\n"
            "4. 错误处理：\n"
            "   • 转换失败会在日志末尾列出错误文件。\n"
            "   • 弹窗提示部分或全部转换结果。\n\n"
            "5. 其他：\n"
            "   按 ESC 或点击“关闭”退出本窗口。"
        ))
        about_text.config(state="disabled")

        ttk.Button(
            container,
            text="关闭",
            command=about_win.destroy
        ).pack(pady=(10, 0))

    ttk.Button(
        ops_frame,
        text="说明/介绍",
        command=show_about
    ).pack(side="left", padx=5, pady=10)

    # ─── 拦截关闭，静默终止进程 ───────────────────────
    def on_closing():
        terminate_tasks(log_enabled=False)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    build_gui()
