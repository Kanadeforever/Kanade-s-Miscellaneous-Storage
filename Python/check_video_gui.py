#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频完整性检测工具 GUI 版（美化版 + 进度条 + 剩余时间估算）
使用多个 ffmpeg 进程并发检测，每个进程可配置线程数，输出 CSV 报告。
点击“停止任务”或关闭窗口时自动终止所有任务。
"""

import os, subprocess, time, csv, multiprocessing as mp
from datetime import datetime
from tkinter import Tk, Label, Entry, Button, filedialog, StringVar, IntVar, Text, END, Scrollbar, W, Frame, font, ttk
from threading import Thread, Event

stop_flag = Event()

def classify_error(err_text):
    if 'Invalid' in err_text or 'corrupt' in err_text:
        return '格式错误'
    elif 'stream' in err_text:
        return '流错误'
    elif 'timeout' in err_text.lower():
        return '超时'
    elif err_text:
        return '解码错误'
    return ''

def find_videos(root, exts):
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(exts):
                yield os.path.join(dirpath, fn)

def check_file(args):
    file_path, timeout, threads = args
    cmd = [
        'ffmpeg',
        '-v', 'error',
        '-threads', str(threads),
        '-i', file_path,
        '-f', 'null', '-'
    ]
    start_time = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        outs, errs = proc.communicate(timeout=timeout)
        duration = round(time.time() - start_time, 2)
        err_text = errs.decode('utf-8', errors='ignore').strip()
        if proc.returncode != 0 or err_text:
            return {
                '文件路径': file_path,
                '状态': '损坏',
                '错误信息': err_text,
                '错误类型': classify_error(err_text),
                '耗时（秒）': duration
            }
        return {
            '文件路径': file_path,
            '状态': '完整',
            '错误信息': '',
            '错误类型': '',
            '耗时（秒）': duration
        }
    except subprocess.TimeoutExpired:
        proc.kill()
        duration = round(time.time() - start_time, 2)
        return {
            '文件路径': file_path,
            '状态': '超时',
            '错误信息': f'超时（超过 {timeout} 秒）',
            '错误类型': '超时',
            '耗时（秒）': duration
        }

def run_detection(params, log_widget, progress_var, eta_label):
    stop_flag.clear()

    directory, extensions, workers, threads, timeout = params
    exts = tuple('.' + e.strip().lower() for e in extensions.split(','))
    videos = list(find_videos(directory, exts))

    log_widget.insert(END, f'共发现 {len(videos)} 个视频文件\n')
    log_widget.insert(END, f'使用 {workers} 个并发进程，每个 ffmpeg 使用 {threads} 个线程\n')

    pool = mp.Pool(processes=workers)
    tasks = ((f, timeout, threads) for f in videos)

    results = []
    total = len(videos)
    start_global = time.time()

    for idx, result in enumerate(pool.imap_unordered(check_file, tasks), start=1):
        if stop_flag.is_set():
            break
        results.append(result)

        if result['状态'] == '完整':
            log_widget.insert(END, f'完整: {result["文件路径"]}\n')
        else:
            log_widget.insert(END, f'{result["状态"]}: {result["文件路径"]} | 错误信息: {result["错误信息"]}\n')
        log_widget.see(END)

        # 更新进度条
        progress = int((idx / total) * 100)
        progress_var.set(progress)

        # 估算剩余时间
        elapsed = time.time() - start_global
        avg_time = elapsed / idx
        remaining = avg_time * (total - idx)
        hrs, rem = divmod(int(remaining), 3600)
        mins, secs = divmod(rem, 60)
        eta_label.config(text=f'剩余时间估算: {hrs:02d}:{mins:02d}:{secs:02d}')

    pool.terminate()
    pool.join()

    progress_var.set(0)
    eta_label.config(text='剩余时间估算: --')

    if not stop_flag.is_set():
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = os.path.join(directory, f'decode_report_{timestamp}.csv')
        with open(report_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['文件路径', '状态', '错误信息', '错误类型', '耗时（秒）'])
            writer.writeheader()
            writer.writerows(results)
        log_widget.insert(END, f'检测完成，报告已保存至: {report_path}\n')
    else:
        log_widget.insert(END, '任务已中断，未生成报告\n')

    log_widget.see(END)

def stop_detection(log_widget):
    stop_flag.set()
    try:
        result = subprocess.run(
            ['taskkill', '/F', '/IM', 'ffmpeg.exe'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True
        )
        if result.returncode == 0:
            log_widget.insert(END, '成功终止任务！\n')
        else:
            log_widget.insert(END, '终止任务失败\n')
    except Exception as e:
        log_widget.insert(END, f'终止任务失败: {e}\n')

def launch_gui():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = Tk()
    root.title('视频完整性检测工具')
    root.geometry('820x860')
    root.resizable(False, False)

    default_font = font.nametofont("TkDefaultFont")
    default_font.configure(size=10)
    root.option_add("*Font", default_font)

    root.grid_rowconfigure(3, weight=1)
    root.grid_columnconfigure(0, weight=1)

    dir_var = StringVar()
    ext_var = StringVar(value='mp4,mkv,avi,flv,wmv,webm,mov,m4v,ts,mts,m2ts,3gp,3g2,f4v,asf,vob,mpg,mpeg,rm,rmvb,dv,ogv')
    workers_var = IntVar(value=max(1, mp.cpu_count() // 2))
    threads_var = IntVar(value=1)
    timeout_var = IntVar(value=300)

    progress_var = IntVar()

    def browse_dir():
        path = filedialog.askdirectory()
        if path:
            dir_var.set(path)

    def start_detection():
        if not dir_var.get():
            log.insert(END, '请先选择视频目录\n')
            return
        params = (
            dir_var.get(),
            ext_var.get(),
            max(1, min(workers_var.get(), mp.cpu_count())),
            max(1, threads_var.get()),
            max(1, timeout_var.get())
        )
        Thread(target=run_detection, args=(params, log, progress_var, eta_label), daemon=True).start()

    def on_close():
        stop_detection(log)
        root.destroy()

    top_frame = Frame(root, padx=10, pady=10)
    top_frame.grid(row=0, column=0, sticky='ew')
    top_frame.grid_columnconfigure(1, weight=1)

    param_frame = Frame(root, padx=10, pady=5)
    param_frame.grid(row=1, column=0, sticky='w')

    button_frame = Frame(root, padx=10, pady=10)
    button_frame.grid(row=2, column=0, sticky='w')

    log_frame = Frame(root, padx=10, pady=10)
    log_frame.grid(row=3, column=0, sticky='nsew')
    log_frame.grid_rowconfigure(0, weight=1)
    log_frame.grid_columnconfigure(0, weight=1)

    progress_frame = Frame(root, padx=10, pady=5)
    progress_frame.grid(row=4, column=0, sticky='ew')
    progress_frame.grid_columnconfigure(0, weight=1)

    scrollbar = Scrollbar(log_frame)
    scrollbar.grid(row=0, column=1, sticky='ns')

    log = Text(log_frame, height=12, wrap='none', yscrollcommand=scrollbar.set, bg='#f9f9f9', relief='solid', bd=1)
    log.grid(row=0, column=0, sticky='nsew')
    scrollbar.config(command=log.yview)

    progress_bar = ttk.Progressbar(progress_frame, variable=progress_var, maximum=100)
    progress_bar.grid(row=0, column=0, sticky='ew', padx=5)

    eta_label = Label(progress_frame, text='剩余时间估算: --')
    eta_label.grid(row=1, column=0, sticky='w', padx=5, pady=2)

    Label(top_frame, text='视频目录:').grid(row=0, column=0, sticky=W)
    Entry(top_frame, textvariable=dir_var).grid(row=0, column=1, sticky='ew')
    Button(top_frame, text='浏览', command=browse_dir).grid(row=0, column=2, padx=5)

    Label(param_frame, text='文件后缀:').grid(row=0, column=0, sticky=W)
    Entry(param_frame, textvariable=ext_var, width=40).grid(row=0, column=1, columnspan=2, sticky=W)

    Label(param_frame, text='并发进程数:').grid(row=1, column=0, sticky=W)
    Entry(param_frame, textvariable=workers_var, width=10).grid(row=1, column=1, sticky=W)

    Label(param_frame, text='每进程线程数:').grid(row=2, column=0, sticky=W)
    Entry(param_frame, textvariable=threads_var, width=10).grid(row=2, column=1, sticky=W)

    Label(param_frame, text='超时时间（秒）:').grid(row=3, column=0, sticky=W)
    Entry(param_frame, textvariable=timeout_var, width=10).grid(row=3, column=1, sticky=W)

    Button(button_frame, text='开始检测', width=15, command=start_detection).grid(row=0, column=0, padx=5)
    Button(button_frame, text='停止任务', width=15, command=lambda: stop_detection(log)).grid(row=0, column=1, padx=5)
    Button(button_frame, text='清空日志', width=15, command=lambda: log.delete(1.0, END)).grid(row=0, column=2, padx=5)
    Button(button_frame, text='打开报告文件夹', width=15, command=lambda: os.startfile(dir_var.get())).grid(row=0, column=3, padx=5)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == '__main__':
    launch_gui()
