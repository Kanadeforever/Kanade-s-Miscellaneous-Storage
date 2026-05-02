#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CBZ File Create Tool - 漫画转附带元数据cbz格式工具
版本: 1.2

功能:
  1. 生成CSV - 扫描input文件夹，根据规则集预提取信息生成__edit__.csv
  2. 处理漫画文件 - 读取CSV生成comicinfo.xml并打包为cbz
  3. 规则集文件导入 - CSV转JSON规则集
  4. 规则集文件导出 - JSON规则集转CSV

外置依赖(pip安装):
  pypinyin          - 中文转汉语拼音
  pykakasi          - 日文转罗马音
  korean-romanizer  - 韩文转罗马音
"""

import os
import sys
from pathlib import Path

# 判断是否在 PyInstaller 打包环境中运行
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent      # EXE 所在目录（可写）
    RESOURCE_DIR = Path(sys._MEIPASS)             # 打包资源目录（只读）
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = SCRIPT_DIR

TOOLS_DIR = SCRIPT_DIR / "__tools__"              # 始终可写

def clear_screen():
    """清屏"""
    os.system("cls" if sys.platform == "win32" else "clear")


def confirm(prompt: str = "确认？") -> bool:
    """
    确认：回车/Y = Yes，ESC/退格/N = No。
    支持按键（回车/ESC/退格）和打字（y/n），两者同时有效。
    Windows 用 msvcrt，Unix 用 termios。
    """
    print(f"  {prompt} [Y (Enter) / n (Esc & Backspace)]: ", end="", flush=True)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                ch = msvcrt.getch()
                if ch in (b"\r", b"\n", b"y", b"Y"):
                    print("Y")
                    return True
                elif ch in (b"n", b"N", b"\x1b", b"\x08"):
                    label = "N" if ch != b"\x1b" else "N(ESC)"
                    print(label)
                    return False
        else:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch.lower() == "y" or ch in ("\r", "\n"):
                        print("Y")
                        return True
                    elif ch.lower() == "n" or ch == "\x1b" or ch == "\x7f":
                        label = "N" if ch != "\x1b" else "N(ESC)"
                        print(label)
                        return False
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        ans = input().strip().lower()
        return ans != "n"

import csv
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
import urllib.request
import urllib.parse
from datetime import datetime

# ============================================================
# 路径配置
# ============================================================
INPUT_DIR = SCRIPT_DIR / "input"
OUTPUT_DIR = SCRIPT_DIR / "output"
LOGS_DIR = SCRIPT_DIR / "__logs__"
EDIT_CSV = SCRIPT_DIR / "__edit__.csv"
RULES_JSON = TOOLS_DIR / "rules.json"
RULES_CSV = TOOLS_DIR / "rules.csv"
SEVEN_Z_DIR = TOOLS_DIR / "7z"

# ============================================================
# 确保必要目录存在
# ============================================================
for d in [TOOLS_DIR, INPUT_DIR, OUTPUT_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# 日志系统
# ============================================================
def get_log_path():
    """获取带时间戳的日志文件路径（增量）"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return LOGS_DIR / f"log_{timestamp}.txt"

def write_log(message: str, log_path: Path = None):
    """写入日志文件并打印到控制台"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            print(f"  ⚠ 日志写入失败 ({log_path}): {e}", file=sys.stderr)

# ============================================================
# 7z 检测与封装
# ============================================================
_seven_zip_available = None
_seven_zip_exe = None

def check_7z():
    """检测7z是否可用。检查7za.exe、7za.dll、7zxa.dll"""
    global _seven_zip_available, _seven_zip_exe

    if _seven_zip_available is not None:
        return _seven_zip_available

    # 检查 __tools__/7z/ 下的文件
    candidates = []
    if sys.platform == "win32":
        exe_path = SEVEN_Z_DIR / "7za.exe"
        dll1 = SEVEN_Z_DIR / "7za.dll"
        dll2 = SEVEN_Z_DIR / "7zxa.dll"
        if exe_path.exists() and dll1.exists() and dll2.exists():
            candidates.append(str(exe_path))
    else:
        for name in ["7za", "7zz", "7z"]:
            exe_path = SEVEN_Z_DIR / name
            if exe_path.exists():
                candidates.append(str(exe_path))

    # 也检查系统PATH
    if not candidates:
        for name in ["7za", "7za.exe", "7zz", "7z"]:
            found = shutil.which(name)
            if found:
                candidates.append(found)
                break

    if candidates:
        _seven_zip_exe = candidates[0]
        _seven_zip_available = True
        return True
    else:
        _seven_zip_available = False
        return False

def run_7z(args, log_path=None):
    """运行7z命令"""
    if not check_7z():
        return None
    try:
        cmd = [_seven_zip_exe] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result
    except Exception as e:
        write_log(f"[7z] 执行失败: {e}", log_path)
        return None

def extract_with_7z(archive_path: Path, dest_dir: Path, log_path=None):
    """使用7z解压到目标目录"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = run_7z(["x", str(archive_path), f"-o{str(dest_dir)}", "-y"], log_path)
    if result and result.returncode == 0:
        return True
    return False

def compress_with_7z(source_dir: Path, output_path: Path, log_path=None):
    """使用7z压缩为zip（cbz）"""
    # 7z 创建zip: a -tzip output.zip source_dir/*
    result = run_7z(["a", "-tzip", str(output_path), str(source_dir / "*")], log_path)
    if result and result.returncode == 0:
        return True
    return False

def extract_archive_fallback(archive_path: Path, dest_dir: Path, log_path=None):
    """Python原生解压（仅支持zip）"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = archive_path.suffix.lower()
    try:
        if ext == ".zip" or ext == ".cbz":
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(dest_dir)
            return True
        else:
            write_log(f"[解压] 不支持的文件格式(无7z回退): {archive_path.name}", log_path)
            return False
    except Exception as e:
        write_log(f"[解压] 回退解压失败: {e}", log_path)
        return False

def compress_folder_fallback(source_dir: Path, output_path: Path, log_path=None):
    """Python原生压缩为cbz（zip格式）"""
    try:
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(source_dir.rglob("*")):
                if f.is_file():
                    arcname = f.relative_to(source_dir)
                    zf.write(f, arcname)
        return True
    except Exception as e:
        write_log(f"[压缩] 回退压缩失败: {e}", log_path)
        return False

# ============================================================
# 规则集操作
# ============================================================
def load_rules(log_path=None):
    """加载规则集"""
    if not RULES_JSON.exists():
        write_log("[规则集] rules.json 不存在，将使用空规则集", log_path)
        return {"categories": {}, "exhibitions": []}
    try:
        with open(RULES_JSON, "r", encoding="utf-8") as f:
            rules = json.load(f)
        return rules
    except Exception as e:
        write_log(f"[规则集] 加载失败: {e}", log_path)
        return {"categories": {}, "exhibitions": []}

def save_rules(rules, log_path=None):
    """保存规则集"""
    try:
        with open(RULES_JSON, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
        write_log("[规则集] rules.json 保存成功", log_path)
        return True
    except Exception as e:
        write_log(f"[规则集] 保存失败: {e}", log_path)
        return False

def export_rules_to_csv(log_path=None):
    """将规则集JSON导出为CSV"""
    rules = load_rules(log_path)
    rows = []

    # 导出 categories
    for cat_name, cat_info in rules.get("categories", {}).items():
        cat_type = cat_info.get("type", "")
        for field, value in cat_info.get("mappings", {}).items():
            rows.append({
                "section": "categories",
                "key": cat_name,
                "type": cat_type,
                "mapping_field": field,
                "mapping_value": value
            })

    # 导出 exhibitions
    for exh in rules.get("exhibitions", []):
        pattern = exh.get("pattern", "")
        exh_type = exh.get("type", "")
        for field, value in exh.get("mappings", {}).items():
            rows.append({
                "section": "exhibitions",
                "key": pattern,
                "type": exh_type,
                "mapping_field": field,
                "mapping_value": value
            })

    if not rows:
        write_log("[规则集] ⚠ 规则集为空，导出的CSV将只有表头没有数据行", log_path)

    try:
        with open(RULES_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["section", "key", "type", "mapping_field", "mapping_value"])
            writer.writeheader()
            writer.writerows(rows)
        write_log(f"[规则集] 导出成功 -> {RULES_CSV} ({len(rows)} 条记录)", log_path)
        return True
    except Exception as e:
        write_log(f"[规则集] 导出CSV失败: {e}", log_path)
        return False

def import_rules_from_csv(log_path=None):
    """从CSV导入规则集JSON（合并完全相同的条目）"""
    if not RULES_CSV.exists():
        write_log(f"[规则集] {RULES_CSV} 不存在，无法导入", log_path)
        return False

    try:
        with open(RULES_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            headers = reader.fieldnames or []
    except Exception as e:
        write_log(f"[规则集] 读取CSV失败: {e}", log_path)
        return False

    write_log(f"[规则集] 读取到 {len(rows)} 行数据，列名: {headers}", log_path)

    # 列名校验
    expected = {"section", "key", "type", "mapping_field", "mapping_value"}
    actual = set(h.strip() if h else "" for h in headers)
    missing = expected - actual
    if missing:
        write_log(f"[规则集] ⚠ CSV缺少必要列: {missing}，请检查文件", log_path)
        return False

    # 构建规则集，合并相同条目
    categories = {}
    exhibitions_map = {}  # key: (pattern, type) -> {field: value}
    skipped = 0
    unknown_sections = set()

    for row in rows:
        section = row.get("section", "").strip()
        key = row.get("key", "").strip()
        rtype = row.get("type", "").strip()
        field = row.get("mapping_field", "").strip()
        value = row.get("mapping_value", "").strip()

        if not section or not key:
            skipped += 1
            continue

        # 归一化：兼容单数/复数写法（category/categories, exhibition/exhibitions）
        if section in ("category", "categories"):
            section = "categories"
        elif section in ("exhibition", "exhibitions"):
            section = "exhibitions"
        else:
            unknown_sections.add(section)
            skipped += 1
            continue

        if section == "categories":
            if key not in categories:
                categories[key] = {"type": rtype, "mappings": {}}
            if field:
                categories[key]["mappings"][field] = value

        elif section == "exhibitions":
            exh_key = (key, rtype)
            if exh_key not in exhibitions_map:
                exhibitions_map[exh_key] = {}
            if field:
                exhibitions_map[exh_key][field] = value

    if unknown_sections:
        write_log(f"[规则集] ⚠ 发现未知section值: {unknown_sections}，这些行已跳过", log_path)

    # 转换 exhibitions_map 为列表
    exhibitions = []
    for (pattern, rtype), mappings in exhibitions_map.items():
        exhibitions.append({
            "pattern": pattern,
            "type": rtype,
            "mappings": mappings
        })

    rules = {
        "categories": categories,
        "exhibitions": exhibitions
    }

    if not categories and not exhibitions:
        write_log(f"[规则集] ⚠ 未提取到任何规则（有效行: {len(rows) - skipped}，跳过: {skipped}），请检查CSV内容", log_path)

    write_log(f"[规则集] 导入结果: {len(categories)}个类别, {len(exhibitions)}个展别规则", log_path)
    return save_rules(rules, log_path)

# ============================================================
# 文件名解析器
# ============================================================
def match_exhibition(category_name: str, rules: dict, log_path=None):
    """检查类别名是否匹配展别规则，返回对应的规则信息"""
    for exh in rules.get("exhibitions", []):
        pattern = exh.get("pattern", "")
        try:
            m = re.fullmatch(pattern, category_name)
            if m:
                return {
                    "type": exh.get("type", "doujinshi"),
                    "mappings": _expand_mappings(exh.get("mappings", {}), m),
                    "is_exhibition": True
                }
        except re.error:
            continue
    return None

def _expand_mappings(mappings: dict, match):
    """展开映射中的 $1, $2 等占位符"""
    result = {}
    for field, value in mappings.items():
        try:
            result[field] = match.expand(value)
        except Exception:
            result[field] = value
    return result

def parse_filename(name: str, rules: dict, log_path=None):
    """
    解析文件名/文件夹名，提取信息。

    返回 dict 或 None（解析失败）
    """
    # 去除扩展名
    name_no_ext = name
    known_exts = [".zip", ".rar", ".7z", ".cbz"]
    for ext in known_exts:
        if name_no_ext.lower().endswith(ext):
            name_no_ext = name_no_ext[:-len(ext)]
            break

    name_no_ext = name_no_ext.strip()
    if not name_no_ext:
        return None

    # 第一步：匹配开头的 (文件类别/展别)
    m = re.match(r'\(([^)]+)\)\s*(.*)', name_no_ext)
    if not m:
        write_log(f"[解析] 文件名格式不规范(缺少类别括号): {name}", log_path)
        return None

    category = m.group(1).strip()
    rest = m.group(2).strip()

    # 第二步：判断类别类型
    # 先检查 categories
    cat_info = rules.get("categories", {}).get(category)
    # 再检查 exhibitions
    exh_info = match_exhibition(category, rules, log_path)

    is_doujinshi = False
    mappings_from_rules = {}

    if exh_info:
        is_doujinshi = True
        mappings_from_rules = exh_info.get("mappings", {})
    elif cat_info:
        is_doujinshi = (cat_info.get("type") == "doujinshi")
        mappings_from_rules = cat_info.get("mappings", {})
    else:
        # 未知类别，记录错误但仍然尝试解析
        write_log(f"[解析] 未知文件类别: {category}，文件: {name}", log_path)

    # 第三步：匹配 [发行/社团 (作者/作画/原作)] 或 [作者]
    m_bracket = re.match(r'\[([^\]]*)\]\s*(.*)', rest)
    if not m_bracket:
        write_log(f"[解析] 文件名格式不规范(缺少作者方括号): {name}", log_path)
        return None

    bracket_content = m_bracket.group(1).strip()
    rest = m_bracket.group(2).strip()

    # 解析 bracket 内容
    circle = ""
    artists = []

    if is_doujinshi:
        # 同人志: [发行/社团 (作画、原作)] 或 [发行/社团 (作者)] 或 [名字]
        m_inner = re.match(r'(.+?)\s*\(([^)]+)\)\s*$', bracket_content)
        if m_inner:
            circle = m_inner.group(1).strip()
            artists_str = m_inner.group(2).strip()
            # 分割作者（用逗号、顿号等）
            artists = [a.strip() for a in re.split(r'[,，、&＆]', artists_str) if a.strip()]
        else:
            # 没有括号，整个作为 circle（同时也是作者）
            circle = bracket_content
    else:
        # 发行版漫画: [作者]
        artists = [bracket_content]

    # 第四步：解析剩余部分
    # 同人志: 书名 (作品形式) [文件载体]
    # 发行版: 书名 + 附加内容 [文件载体]
    # 解析顺序：从后往前提取 [文件载体], 然后 (作品形式)/(附加内容)

    title = rest
    fmt = ""
    works_form = ""
    extra = ""

    if is_doujinshi:
        # 提取末尾的 [文件载体]
        m_fmt = re.search(r'\s*\[([^\]]+)\]\s*$', rest)
        if m_fmt:
            fmt = m_fmt.group(1).strip()
            rest = rest[:m_fmt.start()]

        # 提取末尾的 (作品形式)
        m_wf = re.search(r'\s*\(([^)]+)\)\s*$', rest)
        if m_wf:
            # 如果括号内容像数字/卷号，则认为是书名的一部分
            wf_candidate = m_wf.group(1).strip()
            if not re.match(r'^[\d.]+$', wf_candidate):
                works_form = wf_candidate
                rest = rest[:m_wf.start()]

        title = rest.strip()
    else:
        # 发行版漫画
        # 提取末尾的 [文件载体]
        m_fmt = re.search(r'\s*\[([^\]]+)\]\s*$', rest)
        if m_fmt:
            fmt = m_fmt.group(1).strip()
            rest = rest[:m_fmt.start()]

        # 提取末尾的 + 附加内容
        m_extra = re.search(r'\s*[＋+]\s*(.+?)\s*$', rest)
        if m_extra:
            extra = m_extra.group(1).strip()
            rest = rest[:m_extra.start()]

        title = rest.strip()

    # 清理 title 中可能残留的尾部标点
    title = title.rstrip(" 　\t")

    if not title:
        write_log(f"[解析] 无法提取书名: {name}", log_path)
        return None

    # 第五步：构建结果
    result = {
        "category": category,
        "is_doujinshi": is_doujinshi,
        "circle": circle,
        "artists": artists,
        "title": title,
        "works_form": works_form,
        "extra": extra,
        "format": fmt,
        "mappings_from_rules": mappings_from_rules,
    }

    return result

# ============================================================
# CSV 操作
# ============================================================
CSV_FIELDNAMES = [
    "folder_name",
    "Title",
    "Series",
    "Number",
    "Count",
    "Volume",
    "AlternateSeries",
    "Summary",
    "Notes",
    "Year",
    "Month",
    "Day",
    "Writer",
    "Penciller",
    "CoverArtist",
    "Translator",
    "Publisher",
    "Imprint",
    "Genre",
    "LanguageISO",
    "Format",
    "Manga",
    "AgeRating",
    "SeriesGroup",
    "searchurl",
    "bookurl",
]

def generate_csv(log_path=None):
    """
    扫描input文件夹，生成__edit__.csv
    """
    if EDIT_CSV.exists():
        write_log(f"[CSV] {EDIT_CSV.name} 已存在，请先处理或删除后再生成", log_path)
        return False

    rules = load_rules(log_path)

    # 收集待处理项
    items = []  # [(folder_name, source_type, source_path)]
    # source_type: "folder" 或 "archive"

    if not INPUT_DIR.exists():
        write_log("[CSV] input文件夹不存在，已创建", log_path)
        INPUT_DIR.mkdir(parents=True, exist_ok=True)

    archive_exts = {".zip", ".rar", ".7z", ".cbz"}

    for entry in sorted(INPUT_DIR.iterdir()):
        if entry.is_dir():
            items.append((entry.name, "folder", entry))
        elif entry.is_file() and entry.suffix.lower() in archive_exts:
            items.append((entry.name, "archive", entry))

    if not items:
        write_log("[CSV] input文件夹中没有可处理的文件或文件夹", log_path)
        return False

    write_log(f"[CSV] 发现 {len(items)} 个待处理项", log_path)

    csv_rows = []
    temp_dirs = []  # 用于清理临时目录

    for folder_name, src_type, src_path in items:
        write_log(f"[CSV] 处理: {folder_name}", log_path)

        # 如果是压缩文件，先解压
        actual_name = folder_name
        if src_type == "archive":
            ext = src_path.suffix.lower()
            archive_name_no_ext = folder_name[:-len(ext)] if folder_name.lower().endswith(ext) else folder_name
            # 去掉扩展名后的名字作为解析依据
            temp_dir = Path(tempfile.mkdtemp(prefix="manga_extract_"))
            temp_dirs.append(temp_dir)

            # 使用7z或回退方式解压
            if check_7z():
                success = extract_with_7z(src_path, temp_dir, log_path)
            else:
                success = extract_archive_fallback(src_path, temp_dir, log_path)

            if not success:
                write_log(f"[CSV] 解压失败: {folder_name}，跳过", log_path)
                continue

            actual_name = archive_name_no_ext
        else:
            actual_name = folder_name

        # 解析文件名
        parsed = parse_filename(actual_name, rules, log_path)
        if parsed is None:
            write_log(f"[CSV] 解析失败，跳过: {actual_name}", log_path)
            continue

        # 构建CSV行
        row = {field: "" for field in CSV_FIELDNAMES}
        row["folder_name"] = folder_name  # 保留原始名称（含扩展名）

        # 书名 → Series
        row["Series"] = parsed["title"]

        # 作者处理
        artists = parsed["artists"]
        if parsed["is_doujinshi"]:
            # 同人志
            if parsed["circle"]:
                row["Publisher"] = parsed["circle"]
            if len(artists) == 1:
                row["Writer"] = artists[0]
                row["Penciller"] = artists[0]
            elif len(artists) >= 2:
                # 第一个作为作画(Penciller)，其余的作为原作(Writer)
                row["Penciller"] = artists[0]
                row["Writer"] = ", ".join(artists[1:])
            elif not artists and parsed["circle"]:
                # 只有circle没有作者时，circle也作为作者
                row["Writer"] = parsed["circle"]
                row["Penciller"] = parsed["circle"]
        else:
            # 发行版漫画
            if len(artists) == 1:
                row["Writer"] = artists[0]
                row["Penciller"] = artists[0]
            elif len(artists) >= 2:
                row["Penciller"] = artists[0]
                row["Writer"] = ", ".join(artists[1:])

        # 作品形式 → Imprint (仅同人志)
        if parsed["works_form"]:
            row["Imprint"] = parsed["works_form"]

        # 文件载体 → Format
        if parsed["format"]:
            if "DL版" in parsed["format"] or parsed["format"].lower() == "dl":
                row["Format"] = "Digital"
            else:
                row["Format"] = parsed["format"]

        # 从规则集映射
        for field, value in parsed.get("mappings_from_rules", {}).items():
            if field in row:
                row[field] = value

        # 设置默认 Manga 值
        if not row["Manga"]:
            row["Manga"] = "YesAndRightToLeft"

        # 搜索链接：用 Series 作为 keyword 构建 DLsite 搜索链接
        row["searchurl"] = "https://www.dlsite.com/maniax/fsr/=/keyword/" + urllib.parse.quote(row["Series"]) + "/from/fs.header/"
        # 标签链接留空，由用户后续手动填写
        row["bookurl"] = ""

        csv_rows.append(row)
        write_log(f"[CSV] ✓ 解析成功: {actual_name} -> Series={row['Series']}", log_path)

    # 清理临时目录
    for td in temp_dirs:
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass

    if not csv_rows:
        write_log("[CSV] 没有成功解析任何项目，CSV未生成", log_path)
        return False

    # 写入CSV
    try:
        with open(EDIT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(csv_rows)
        write_log(f"[CSV] 成功生成 {EDIT_CSV.name}，共 {len(csv_rows)} 条记录", log_path)
        return True
    except Exception as e:
        write_log(f"[CSV] 写入失败: {e}", log_path)
        return False

def read_csv(log_path=None):
    """读取__edit__.csv"""
    if not EDIT_CSV.exists():
        write_log(f"[CSV] {EDIT_CSV.name} 不存在", log_path)
        return None
    try:
        with open(EDIT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows
    except Exception as e:
        write_log(f"[CSV] 读取失败: {e}", log_path)
        return None

# ============================================================
# 语言转换（中文→拼音 / 日文→罗马音 / 韩文→罗马音）
# ============================================================
def convert_to_latin(text: str, lang_iso: str, log_path=None) -> str:
    """
    将文本转换为英文字母。
    - zh: 汉语拼音（不带声调）
    - ja: 罗马音
    - ko: 罗马音（同日语处理规则）
    - 其他/空: 返回原文
    """
    if not text or not text.strip():
        return text

    lang = (lang_iso or "").strip().lower()

    if lang == "zh":
        return _chinese_to_pinyin(text, log_path)
    elif lang == "ja":
        return _japanese_to_romaji(text, log_path)
    elif lang == "ko":
        return _korean_to_romanize(text, log_path)
    else:
        # 没有填LanguageISO，返回原文本
        return text

def _chinese_to_pinyin(text: str, log_path=None) -> str:
    """中文转拼音（不带声调），特殊符号替换为空格"""
    try:
        from pypinyin import lazy_pinyin, Style
        result = lazy_pinyin(text, style=Style.NORMAL, errors="ignore")
        out = []
        for item in result:
            # 每个拼音片段只保留字母数字，其余用空格替代
            clean = re.sub(r'[^a-zA-Z0-9]+', ' ', item).strip()
            if clean:
                out.append(clean)
            elif item:
                # 纯特殊字符 → 单空格占位
                out.append(" ")
        return " ".join(out)
    except ImportError:
        write_log("[语言] pypinyin 未安装，无法转换中文拼音。请执行: pip install pypinyin", log_path)
        return text
    except FileNotFoundError as e:
        write_log(
            f"[语言] 中文拼音转换失败(pypinyin数据文件缺失): {e}\n"
            f"  PyInstaller打包时请在spec文件中添加: from PyInstaller.utils.hooks import collect_data_files; datas += collect_data_files('pypinyin')",
            log_path
        )
        return text
    except Exception as e:
        write_log(f"[语言] 中文拼音转换失败: {e}", log_path)
        return text

def _japanese_to_romaji(text: str, log_path=None) -> str:
    """日文转罗马音，特殊符号替换为空格"""
    try:
        from pykakasi import kakasi

        kks = kakasi()
        if hasattr(kks, "convert"):
            # pykakasi v3+ 新 API
            result = kks.convert(text)
            out = []
            for item in result:
                hepburn = item.get("hepburn", "")
                if hepburn:
                    clean = re.sub(r'[^a-zA-Z0-9]+', ' ', hepburn).strip()
                    if clean:
                        out.append(clean)
                    else:
                        out.append(" ")
            return " ".join(out) if out else text
        else:
            # pykakasi v2 旧 API 回退
            import warnings
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                kks.setMode("H", "a")
                kks.setMode("K", "a")
                kks.setMode("J", "a")
                kks.setMode("r", "Hepburn")
                conv = kks.getConverter()
                raw = conv.do(text)
            # 非 ASCII 字母 → 空格
            raw = re.sub(r'[^a-zA-Z\s]', ' ', raw)
            return " ".join(raw.split())
    except ImportError:
        write_log("[语言] pykakasi 未安装，无法转换日文罗马音。请执行: pip install pykakasi", log_path)
        return text
    except FileNotFoundError as e:
        write_log(
            f"[语言] 日文罗马音转换失败(pykakasi数据文件缺失): {e}\n"
            f"  PyInstaller打包时请在spec文件中添加: from PyInstaller.utils.hooks import collect_data_files; datas += collect_data_files('pykakasi')",
            log_path
        )
        return text
    except Exception as e:
        write_log(f"[语言] 日文罗马音转换失败: {e}", log_path)
        return text

def _korean_to_romanize(text: str, log_path=None) -> str:
    """韩文转罗马音，特殊符号替换为空格"""
    try:
        from korean_romanizer import Romanizer
        r = Romanizer(text)
        result = r.romanize()
        # 非 ASCII 字母 → 空格
        result = re.sub(r'[^a-zA-Z\s]', ' ', result)
        return " ".join(result.split())
    except ImportError:
        write_log("[语言] korean-romanizer 未安装，无法转换韩文罗马音。请执行: pip install korean-romanizer", log_path)
        return text
    except FileNotFoundError as e:
        write_log(
            f"[语言] 韩文罗马音转换失败(korean-romanizer数据文件缺失): {e}\n"
            f"  PyInstaller打包时请在spec文件中添加: from PyInstaller.utils.hooks import collect_data_files; datas += collect_data_files('korean-romanizer')",
            log_path
        )
        return text
    except Exception as e:
        write_log(f"[语言] 韩文罗马音转换失败: {e}", log_path)
        return text

def generate_comicinfo_xml(csv_row: dict, log_path=None):
    """
    根据CSV行数据生成干净的comicinfo.xml内容。
    只输出CSV中有值的字段，不产生任何注释和空标签。
    """
    field_to_tag = {
        "Title":        "Title",
        "Series":       "Series",
        "Number":       "Number",
        "Count":        "Count",
        "Volume":       "Volume",
        "AlternateSeries": "AlternateSeries",
        "Summary":      "Summary",
        "Notes":        "Notes",
        "Year":         "Year",
        "Month":        "Month",
        "Day":          "Day",
        "Writer":       "Writer",
        "Penciller":    "Penciller",
        "CoverArtist":  "CoverArtist",
        "Translator":   "Translator",
        "Publisher":    "Publisher",
        "Imprint":      "Imprint",
        "Genre":        "Genre",
        "LanguageISO":  "LanguageISO",
        "Format":       "Format",
        "Manga":        "Manga",
        "AgeRating":    "AgeRating",
        "SeriesGroup":  "SeriesGroup",
    }

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
    ]

    # SortBy 计算
    series_value = csv_row.get("Series", "").strip()
    lang_iso = csv_row.get("LanguageISO", "").strip()
    sort_by_value = ""
    if series_value:
        is_non_english = bool(re.search(r'[^\x00-\x7F]', series_value))
        if is_non_english and lang_iso in ("zh", "ja", "ko"):
            sort_by_value = convert_to_latin(series_value, lang_iso, log_path)
        else:
            sort_by_value = series_value

    # 只写有值的字段
    for _, tag in field_to_tag.items():
        val = csv_row.get(tag, "").strip()
        if val:
            lines.append(f"  <{tag}>{_xml_escape(val)}</{tag}>")

    if sort_by_value:
        lines.append(f"  <SortBy>{_xml_escape(sort_by_value)}</SortBy>")

    lines.append("</ComicInfo>")
    return "\n".join(lines) + "\n"

def _xml_escape(text: str) -> str:
    """转义XML特殊字符"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text

def fetch_genre_tags(url: str, log_path=None) -> str:
    """
    从 DLsite 作品页面抓取 main_genre 中的标签。
    返回英文逗号分隔的标签字符串，失败返回空字符串。
    """
    if not url or not url.strip():
        return ""

    try:
        req = urllib.request.Request(
            url.strip(),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # 提取 <div class="main_genre"> ... </div>
        div_pattern = re.compile(
            r'<div\s+class="main_genre"[^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE
        )
        div_match = div_pattern.search(html)
        if not div_match:
            write_log(f"[标签] 未找到 main_genre div: {url}", log_path)
            return ""

        div_content = div_match.group(1)

        # 提取所有 <a href="...">标签名</a>
        a_pattern = re.compile(
            r'<a\s+[^>]*href="[^"]*"[^>]*>([^<]+)</a>',
            re.IGNORECASE
        )
        tags = a_pattern.findall(div_content)
        tags = [t.strip() for t in tags if t.strip()]

        result = ", ".join(tags)
        if result:
            write_log(f"[标签] ✓ 获取到 {len(tags)} 个标签: {result}", log_path)
        else:
            write_log(f"[标签] ⚠ main_genre 中未解析到标签链接", log_path)
        return result

    except Exception as e:
        write_log(f"[标签] 获取标签失败 ({url}): {e}", log_path)
        return ""

# ============================================================
# 处理漫画文件（生成xml + 打包cbz）
# ============================================================
def process_manga_files(log_path=None):
    """
    读取CSV，为每个条目生成comicinfo.xml并打包为cbz
    """
    csv_rows = read_csv(log_path)
    if csv_rows is None or len(csv_rows) == 0:
        write_log("[处理] CSV为空或不存在，无法处理", log_path)
        return False

    write_log(f"[处理] 开始处理 {len(csv_rows)} 条记录", log_path)

    success_count = 0
    fail_count = 0

    for row in csv_rows:
        folder_name = row.get("folder_name", "").strip()
        if not folder_name:
            write_log("[处理] 跳过空 folder_name 的行", log_path)
            fail_count += 1
            continue

        write_log(f"[处理] 处理: {folder_name}", log_path)

        # 查找对应的源（input中的文件夹或需要解压的文件）
        source_path = INPUT_DIR / folder_name
        is_archive = False

        # 如果直接找不到，尝试去掉已知扩展名后按不同形式查找
        if not source_path.exists():
            name_stem = folder_name
            archive_exts = [".zip", ".rar", ".7z", ".cbz"]
            for ext in archive_exts:
                if name_stem.lower().endswith(ext):
                    name_stem = name_stem[:-len(ext)]
                    break

            # 1) 同名文件夹（去掉扩展名后）
            stem_path = INPUT_DIR / name_stem
            if stem_path.is_dir():
                source_path = stem_path
            else:
                # 2) 尝试各种扩展名的压缩文件
                for ext in archive_exts:
                    candidate = INPUT_DIR / (name_stem + ext)
                    if candidate.is_file():
                        source_path = candidate
                        is_archive = True
                        break

        if not source_path.exists():
            write_log(f"[处理] 找不到源: {folder_name}，跳过", log_path)
            fail_count += 1
            continue

        # 确定工作目录
        work_dir = None
        temp_used = False

        if is_archive or source_path.is_file():
            # 解压到临时目录
            work_dir = Path(tempfile.mkdtemp(prefix="manga_process_"))
            temp_used = True
            if check_7z():
                success = extract_with_7z(source_path, work_dir, log_path)
            else:
                success = extract_archive_fallback(source_path, work_dir, log_path)
            if not success:
                write_log(f"[处理] 解压失败: {folder_name}", log_path)
                shutil.rmtree(work_dir, ignore_errors=True)
                fail_count += 1
                continue
        else:
            # 是文件夹，直接使用（但复制到临时目录以避免污染源）
            work_dir = Path(tempfile.mkdtemp(prefix="manga_process_"))
            temp_used = True
            try:
                # 复制文件夹内容
                shutil.copytree(source_path, work_dir, dirs_exist_ok=True)
            except Exception as e:
                write_log(f"[处理] 复制文件夹失败: {e}", log_path)
                shutil.rmtree(work_dir, ignore_errors=True)
                fail_count += 1
                continue

        # 如果标签链接不为空，抓取标签填入 Genre
        tag_link = row.get("bookurl", "").strip()
        if tag_link:
            write_log(f"[处理] 从标签链接获取标签: {tag_link}", log_path)
            genres = fetch_genre_tags(tag_link, log_path)
            if genres:
                row["Genre"] = genres

        # 生成 comicinfo.xml
        xml_content = generate_comicinfo_xml(row, log_path)
        if xml_content:
            xml_path = work_dir / "ComicInfo.xml"
            try:
                with open(xml_path, "w", encoding="utf-8") as f:
                    f.write(xml_content)
                write_log(f"[处理] ✓ ComicInfo.xml 已生成", log_path)
            except Exception as e:
                write_log(f"[处理] XML写入失败: {e}", log_path)
                shutil.rmtree(work_dir, ignore_errors=True)
                fail_count += 1
                continue

        # 打包为cbz
        # 确定输出文件名：使用 folder_name（去掉扩展名）+ .cbz
        output_name = folder_name
        for ext in [".zip", ".rar", ".7z", ".cbz"]:
            if output_name.lower().endswith(ext):
                output_name = output_name[:-len(ext)]
                break
        output_name += ".cbz"
        output_path = OUTPUT_DIR / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if check_7z():
            compress_success = compress_with_7z(work_dir, output_path, log_path)
        else:
            compress_success = compress_folder_fallback(work_dir, output_path, log_path)

        if compress_success:
            write_log(f"[处理] ✓ 打包成功: {output_name}", log_path)
            success_count += 1
        else:
            write_log(f"[处理] ✗ 打包失败: {folder_name}", log_path)
            fail_count += 1

        # 清理临时目录
        if temp_used:
            shutil.rmtree(work_dir, ignore_errors=True)

    write_log(f"[处理] 完成！成功: {success_count}, 失败: {fail_count}", log_path)
    return True

# ============================================================
# 辅助功能
# ============================================================
def open_folder(path: Path):
    """在文件管理器中打开目录"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception as e:
        print(f"无法打开文件夹: {e}")

# ============================================================
# 主菜单
# ============================================================
def print_banner():
    """打印横幅"""
    print("=" * 60)
    print("    CBZ File Create Tool v2.1")
    print("    漫画转标准CBZ格式工具")
    print("=" * 60)

def _init_tools(log_path=None):
    """EXE 环境下从资源目录复制 7z/模板 到可写 TOOLS_DIR"""
    resource_tools = RESOURCE_DIR / "__tools__"
    if resource_tools.exists() and resource_tools != TOOLS_DIR:
        for item in resource_tools.iterdir():
            dest = TOOLS_DIR / item.name
            if not dest.exists():
                try:
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                    write_log(f"[环境] 已复制资源: {item.name}", log_path)
                except Exception as e:
                    write_log(f"[环境] 复制资源失败 {item.name}: {e}", log_path)

def check_environment(log_path=None):
    """检查运行环境"""
    _init_tools(log_path)

    # 检查 __tools__ 目录
    if not TOOLS_DIR.exists():
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        write_log("[环境] 已创建 __tools__ 目录", log_path)

    # 检查 rules.json
    if not RULES_JSON.exists():
        write_log("[环境] ⚠ rules.json 不存在，将使用空规则集", log_path)

    # 检查 7z
    if check_7z():
        write_log(f"[环境] ✓ 7z 可用: {_seven_zip_exe}", log_path)
    else:
        write_log("[环境] ⚠ 7z 不可用，将使用 Python 原生回退方案（仅支持 zip/cbz）", log_path)

def main():
    """主函数"""
    log_path = get_log_path()
    write_log("=== Manga ComicInfo Tool 启动 ===", log_path)

    check_environment(log_path)
    print()

    while True:
        clear_screen()
        print_banner()
        print()
        print("  请选择操作：")
        print("    1. 生成CSV")
        print("    2. 处理漫画文件")
        print("    3. 规则集文件导出 (JSON → CSV)")
        print("    4. 规则集文件导入 (CSV → JSON)")
        print("    0. 退出")
        print()

        choice = input("  请输入选项 [0-4]: ").strip()

        if choice == "1":
            clear_screen()
            print()
            write_log(">>> 开始: 生成CSV", log_path)

            # 检查是否有已存在的CSV
            if EDIT_CSV.exists():
                print(f"  ⚠ {EDIT_CSV.name} 已存在！")
                if confirm("是否覆盖？"):
                    try:
                        EDIT_CSV.unlink()
                        write_log("[CSV] 已删除旧的 __edit__.csv", log_path)
                    except Exception as e:
                        write_log(f"[CSV] 删除失败: {e}", log_path)
                else:
                    print("  已取消。")
                    write_log("[CSV] 用户取消覆盖", log_path)
                    continue

            result = generate_csv(log_path)

            if result:
                print()
                print(f"  ✓ CSV 已生成: {EDIT_CSV}")
                print()
                if confirm("是否打开CSV所在文件夹？"):
                    open_folder(SCRIPT_DIR)
                write_log("<<< 完成: 生成CSV", log_path)
            else:
                print()
                print("  ✗ CSV 生成失败，请查看日志。")
                write_log("<<< 失败: 生成CSV", log_path)
            input("\n  按回车键继续...")

        elif choice == "2":
            clear_screen()
            print()
            write_log(">>> 开始: 处理漫画文件", log_path)

            if not EDIT_CSV.exists():
                print(f"  ⚠ {EDIT_CSV.name} 不存在，请先生成CSV！")
                write_log("[处理] CSV不存在", log_path)
                input("\n  按回车键继续...")
                continue

            print("  即将处理以下文件：")
            rows = read_csv(log_path)
            if rows:
                for r in rows:
                    print(f"    - {r.get('folder_name', '?')}  →  {r.get('Series', '?')}")
            print()
            if not confirm("确认处理？"):
                print("  已取消。")
                write_log("[处理] 用户取消", log_path)
                continue

            result = process_manga_files(log_path)

            if result:
                print()
                print(f"  ✓ 处理完成！输出目录: {OUTPUT_DIR}")
                print()
                # 处理完成后，让用户确认后删除CSV
                if confirm("是否确认完成并删除临时CSV文件？"):
                    try:
                        EDIT_CSV.unlink()
                        write_log("[CSV] 已删除 __edit__.csv", log_path)
                        print("  ✓ CSV已删除。")
                    except Exception as e:
                        write_log(f"[CSV] 删除失败: {e}", log_path)
                        print(f"  ✗ CSV删除失败: {e}")
                write_log("<<< 完成: 处理漫画文件", log_path)
            else:
                print("  ✗ 处理过程中出现问题，请查看日志。")
                write_log("<<< 失败: 处理漫画文件", log_path)
            input("\n  按回车键继续...")

        elif choice == "3":
            clear_screen()
            print()
            write_log(">>> 开始: 规则集文件导出", log_path)

            if not RULES_JSON.exists():
                print(f"  ⚠ {RULES_JSON.name} 不存在！")
                write_log("[规则集] JSON文件不存在", log_path)
                input("\n  按回车键继续...")
                continue

            print(f"  将导出: {RULES_JSON}")
            print(f"  目标:   {RULES_CSV}")
            if not confirm("确认导出？"):
                print("  已取消。")
                continue

            result = export_rules_to_csv(log_path)
            if result:
                print(f"  ✓ 规则集已导出到: {RULES_CSV}")
                write_log("<<< 完成: 规则集文件导出", log_path)
            else:
                print("  ✗ 导出失败，请查看日志。")
                write_log("<<< 失败: 规则集文件导出", log_path)
            input("\n  按回车键继续...")

        elif choice == "4":
            clear_screen()
            print()
            write_log(">>> 开始: 规则集文件导入", log_path)

            if not RULES_CSV.exists():
                print(f"  ⚠ {RULES_CSV.name} 不存在！")
                print(f"  请将规则集CSV文件放在: {RULES_CSV}")
                write_log("[规则集] CSV文件不存在", log_path)
                input("\n  按回车键继续...")
                continue

            print(f"  将导入: {RULES_CSV}")
            print(f"  目标:   {RULES_JSON}")
            if not confirm("确认导入？"):
                print("  已取消。")
                continue

            result = import_rules_from_csv(log_path)
            if result:
                print(f"  ✓ 规则集已导入到: {RULES_JSON}")
                write_log("<<< 完成: 规则集文件导入", log_path)
            else:
                print("  ✗ 导入失败，请查看日志。")
                write_log("<<< 失败: 规则集文件导入", log_path)
            input("\n  按回车键继续...")

        elif choice == "0":
            clear_screen()
            print()
            print("  再见！")
            write_log("=== 程序退出 ===", log_path)
            break

        else:
            clear_screen()
            print()
            print("  ⚠ 无效选项，请重新选择。")
            input("\n  按回车键继续...")

        print("\n" * 2)


if __name__ == "__main__":
    main()
