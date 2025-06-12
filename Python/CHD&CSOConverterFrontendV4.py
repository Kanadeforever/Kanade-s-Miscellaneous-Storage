import tkinter as tk
from tkinter import filedialog, messagebox
import os
import subprocess

# 选择文件函数
def select_files():
    # 打开文件选择对话框，允许多选文件，限制文件类型为ISO、CUE、GDI和CHD
    file_paths = filedialog.askopenfilenames(filetypes=[("ISO 文件", "*.iso"), ("CUE 文件", "*.cue"), ("GDI 文件", "*.gdi"), ("CHD 文件", "*.chd")])
    for file_path in file_paths:
        # 将选择的文件路径添加到列表框中
        file_listbox.insert(tk.END, file_path)

# 移除选定文件函数
def remove_selected_files():
    # 检查列表框中是否有内容
    if file_listbox.size() == 0:
        messagebox.showwarning("警告", "没有文件可移除")
        return
    # 获取选定的文件索引并移除
    selected_indices = file_listbox.curselection()
    for index in reversed(selected_indices):
        file_listbox.delete(index)

# 转换为 CHD 文件函数
def convert_chd():
    # 检查列表框中是否有内容
    if file_listbox.size() == 0:
        messagebox.showwarning("警告", "没有文件可转换")
        return
    errors = []
    for i in range(file_listbox.size()):
        file_path = file_listbox.get(i)
        output_path = os.path.splitext(file_path)[0] + ".chd"
        cmd = f'chdman\\chdman.exe createcd -i "{file_path}" -o "{output_path}"'
        try:
            # 执行转换命令
            subprocess.run(cmd, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            errors.append(f"转换为 CHD 失败: {file_path}\n{e}")
    
    if errors:
        messagebox.showerror("错误", "\n".join(errors))
    else:
        messagebox.showinfo("成功", "所有文件成功转换为 CHD！")

# # 转换为 CSO 文件函数
# def convert_cso():
#     # 检查列表框中是否有内容
#     if file_listbox.size() == 0:
#         messagebox.showwarning("警告", "没有文件可转换")
#         return
#     errors = []
#     for i in range(file_listbox.size()):
#         file_path = file_listbox.get(i)
#         if file_path.lower().endswith(".iso"):
#             cmd = f'maxcso\\maxcso.exe "{file_path}"'
#             try:
#                 # 执行转换命令
#                 subprocess.run(cmd, check=True, shell=True)
#             except subprocess.CalledProcessError as e:
#                 errors.append(f"转换为 CSO 失败: {file_path}\n{e}")
#         else:
#             errors.append(f"文件格式不支持 CSO 转换: {file_path}")
    
#     if errors:
#         messagebox.showerror("错误", "\n".join(errors))
#     else:
#         messagebox.showinfo("成功", "所有文件成功转换为 CSO！")

# 转换为 CSO 文件函数
def convert_cso():
    # 检查列表框中是否有内容
    if file_listbox.size() == 0:
        messagebox.showwarning("警告", "没有文件可转换")
        return
    errors = []
    for i in range(file_listbox.size()):
        file_path = file_listbox.get(i)
        output_path = os.path.splitext(file_path)[0] + ".cso"
        cmd = f'maxcso\\maxcso.exe "{file_path}"'
        try:
            # 执行转换命令
            subprocess.run(cmd, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            errors.append(f"转换为 CSO 失败: {file_path}\n{e}")
    
    if errors:
        messagebox.showerror("错误", "\n".join(errors))
    else:
        messagebox.showinfo("成功", "所有文件成功转换为 CSO！")

# 转换为 CUE 文件函数
def convert_cue():
    # 检查列表框中是否有内容
    if file_listbox.size() == 0:
        messagebox.showwarning("警告", "没有文件可转换")
        return
    errors = []
    for i in range(file_listbox.size()):
        file_path = file_listbox.get(i)
        output_path = os.path.splitext(file_path)[0] + ".cue"
        cmd = f'chdman\\chdman.exe extractcd -i "{file_path}" -o "{output_path}"'
        try:
            # 执行转换命令
            subprocess.run(cmd, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            errors.append(f"转换为 CUE 失败: {file_path}\n{e}")
    
    if errors:
        messagebox.showerror("错误", "\n".join(errors))
    else:
        messagebox.showinfo("成功", "所有文件成功转换为 CUE！")

# 转换为 ISO 文件函数
def convert_iso():
    # 检查列表框中是否有内容
    if file_listbox.size() == 0:
        messagebox.showwarning("警告", "没有文件可转换")
        return
    errors = []
    for i in range(file_listbox.size()):
        file_path = file_listbox.get(i)
        output_path = os.path.splitext(file_path)[0] + ".iso"
        cmd = f'maxcso\\maxcso.exe "{file_path}"'
        try:
            # 执行转换命令
            subprocess.run(cmd, check=True, shell=True)
        except subprocess.CalledProcessError as e:
            errors.append(f"转换为 ISO 失败: {file_path}\n{e}")
    
    if errors:
        messagebox.showerror("错误", "\n".join(errors))
    else:
        messagebox.showinfo("成功", "所有文件成功转换为 ISO！")

# 设置DPI感知
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except:
    pass


# 创建主窗口
app = tk.Tk()
app.title("CHD/CSO转换器V4      支持iso/cue/gdi转chd和iso转cso")

# 创建框架
frame = tk.Frame(app)
frame.pack(pady=10)

# 创建文件列表框
file_listbox = tk.Listbox(frame, width=75, height=10, selectmode=tk.EXTENDED)
file_listbox.pack(side=tk.LEFT, padx=10)

# 创建滚动条
scrollbar = tk.Scrollbar(frame, orient=tk.VERTICAL)
scrollbar.config(command=file_listbox.yview)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
file_listbox.config(yscrollcommand=scrollbar.set)

# 创建选择文件按钮
select_button = tk.Button(app, text="选择文件", command=select_files)
select_button.pack(side=tk.LEFT, padx=20, pady=20)

# 创建移除选定文件按钮
remove_button = tk.Button(app, text="移除选定文件", command=remove_selected_files)
remove_button.pack(side=tk.LEFT, padx=20, pady=20)

# 创建转换为 CHD 按钮
convert_chd_button = tk.Button(app, text="转换为 CHD", command=convert_chd)
convert_chd_button.pack(side=tk.LEFT, padx=20, pady=20)

# 创建转换为 CSO 按钮
convert_cso_button = tk.Button(app, text="转换为 CSO", command=convert_cso)
convert_cso_button.pack(side=tk.LEFT, padx=20, pady=20)

# 创建 转ISO 按钮
convert_iso_button = tk.Button(app, text="转换为 ISO", command=convert_iso)
convert_iso_button.pack(side=tk.LEFT, padx=20, pady=20)

# 创建 转CUE 按钮
convert_cue_button = tk.Button(app, text="转换为 CUE", command=convert_cue)
convert_cue_button.pack(side=tk.LEFT, padx=20, pady=20)
# 运行主循环
app.mainloop()
