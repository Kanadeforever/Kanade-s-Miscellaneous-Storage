#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AV1 压制工具

功能：
- 简单 Tkinter GUI
- 批量添加文件 / 文件夹，选择输出目录，自定义文件名后缀
- 根据每帧像素总数、HDR 和高帧率自动选择 SVT-AV1 CRF
- 视频统一编码为 AV1 10-bit；音频按源质量自动使用 Opus 128/192 kbps
- 保留字幕、章节、元数据与 Matroska 附件
- 当前文件进度、总体加权进度、速度和预计剩余时间
- 启动检查、安全临时输出、完成验证、可疑文件完整解码验证
- 队列状态恢复、无压缩收益持久记录、同目录滚动日志
- 成功且体积变小时可将原文件移入“_待删除原文件”
- 首次运行自动生成同名 JSON 配置模板

依赖：Python 3.10+（通常自带 tkinter）、FFmpeg、ffprobe。
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Any, Iterable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


# ----------------------------- 基本路径与常量 -----------------------------

SCRIPT_PATH = Path(sys.argv[0]).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
APP_STEM = SCRIPT_PATH.stem
CONFIG_PATH = SCRIPT_DIR / f"{APP_STEM}.json"
LOG_PATH = SCRIPT_DIR / f"{APP_STEM}.log"
STATE_PATH = SCRIPT_DIR / f"{APP_STEM}.state.json"

STATE_SCHEMA_VERSION = 2
ENCODING_POLICY_VERSION = "av1-svt-p5-pixel-crf-opus-128-192-v2-old-output-scan"
PENDING_DELETE_DIRNAME = "_待删除原文件"

CONFIG_TEMPLATE = {
    "ffmpeg_path": "",
    "ffprobe_path": ""
}

VIDEO_EXTENSIONS = {
    ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".mts", ".ogm", ".rm", ".rmvb", ".ts",
    ".vob", ".webm", ".wmv"
}

LOSSLESS_AUDIO_CODECS = {
    "alac", "ape", "flac", "mlp", "pcm_alaw", "pcm_bluray", "pcm_dvd",
    "pcm_f16le", "pcm_f24le", "pcm_f32be", "pcm_f32le", "pcm_f64be",
    "pcm_f64le", "pcm_mulaw", "pcm_s16be", "pcm_s16be_planar", "pcm_s16le",
    "pcm_s16le_planar", "pcm_s24be", "pcm_s24daud", "pcm_s24le",
    "pcm_s24le_planar", "pcm_s32be", "pcm_s32le", "pcm_s32le_planar",
    "pcm_s64be", "pcm_s64le", "pcm_s8", "pcm_s8_planar", "pcm_u16be",
    "pcm_u16le", "pcm_u24be", "pcm_u24le", "pcm_u32be", "pcm_u32le",
    "pcm_u8", "shorten", "tak", "truehd", "tta", "wavpack"
}

TEXT_SUBTITLE_TO_SRT = {"mov_text", "text", "tx3g", "webvtt"}

INVALID_SUFFIX_CHARS = re.compile(r'[<>:"/\\|?*]')
ERROR_WORDS = re.compile(
    r"\b(error|invalid|corrupt|failed|non[- ]monotonous|decode_slice_header)\b",
    re.IGNORECASE,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------- 数据结构 -----------------------------

@dataclass
class InputSource:
    path: str
    kind: str  # "file" 或 "dir"


@dataclass
class AudioPlan:
    input_index: int
    codec: str
    channels: int
    sample_rate: int
    source_bitrate: int
    action: str  # "copy" 或 "opus"
    target_kbps: int
    reason: str


@dataclass
class Task:
    input_path: str
    output_path: str
    source_root: str
    output_root: str
    relative_parent: str
    source_relative: str
    duration: float
    width: int
    height: int
    fps: float
    pixels: int
    crf: int
    hdr_type: str
    video_index: int
    video_codec: str
    pix_fmt: str
    rotation: int = 0
    color_primaries: str = ""
    color_transfer: str = ""
    color_space: str = ""
    color_range: str = ""
    chapter_count: int = 0
    required_hdr_side_data: list[str] = field(default_factory=list)
    audio_plans: list[AudioPlan] = field(default_factory=list)
    subtitle_indexes: list[int] = field(default_factory=list)
    subtitle_codecs: list[str] = field(default_factory=list)
    attachment_indexes: list[int] = field(default_factory=list)
    cover_indexes: list[int] = field(default_factory=list)
    input_size: int = 0
    input_mtime_ns: int = 0
    weight: float = 0.0
    status: str = "等待"
    message: str = ""
    output_size: int = 0

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# ----------------------------- 通用辅助函数 -----------------------------

def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def generate_config_template() -> None:
    atomic_write_json(CONFIG_PATH, CONFIG_TEMPLATE)


def load_config() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取配置文件：{exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    ffmpeg = data.get("ffmpeg_path", "")
    ffprobe = data.get("ffprobe_path", "")
    if not isinstance(ffmpeg, str) or not isinstance(ffprobe, str):
        raise ValueError("ffmpeg_path 和 ffprobe_path 必须是字符串。")
    if not ffmpeg.strip() or not ffprobe.strip():
        raise ValueError("请先在同名 JSON 中填写 ffmpeg_path 和 ffprobe_path。")

    return {
        "ffmpeg_path": str(resolve_dependency_path(ffmpeg.strip())),
        "ffprobe_path": str(resolve_dependency_path(ffprobe.strip())),
    }


def resolve_dependency_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = SCRIPT_DIR / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise ValueError(f"依赖文件不存在：{candidate}")
    return candidate


def subprocess_options() -> dict[str, Any]:
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = CREATE_NO_WINDOW
    return options


def format_bytes(value: int) -> str:
    size = float(max(value, 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.2f} TB"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--:--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_fraction(value: Any) -> float:
    if value in (None, "", "0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "N/A"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_ffmpeg_time(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return 0.0


def display_command(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def unique_path(path: Path, reserved: set[str]) -> Path:
    candidate = path
    counter = 2
    key = os.path.normcase(str(candidate.resolve(strict=False)))
    while key in reserved:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1
        key = os.path.normcase(str(candidate.resolve(strict=False)))
    reserved.add(key)
    return candidate


def choose_crf(pixels: int, hdr_type: str, fps: float) -> int:
    if pixels <= 600_000:
        crf = 24
    elif pixels <= 1_300_000:
        crf = 25
    elif pixels <= 2_600_000:
        crf = 27
    elif pixels <= 5_500_000:
        crf = 28
    elif pixels <= 10_000_000:
        crf = 29
    else:
        crf = 30

    # HDR 和高帧率内容稍微多留一点质量余量。
    if hdr_type in {"HDR10", "HLG"}:
        crf -= 1
    if fps >= 50.0:
        crf -= 1
    return max(crf, 20)


def infer_bit_depth(stream: dict[str, Any]) -> int:
    raw = parse_int(stream.get("bits_per_raw_sample"), 0)
    if raw:
        return raw
    pix_fmt = str(stream.get("pix_fmt", "")).lower()
    match = re.search(r"p(9|10|12|14|16)(?:le|be)?$", pix_fmt)
    if match:
        return int(match.group(1))
    if any(token in pix_fmt for token in ("p010", "x2rgb10", "x2bgr10")):
        return 10
    return 8


def detect_hdr_type(video: dict[str, Any]) -> str:
    transfer = str(video.get("color_transfer", "")).lower()
    if transfer == "smpte2084":
        return "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return "SDR"


def has_dolby_vision(video: dict[str, Any]) -> bool:
    text = json.dumps(video, ensure_ascii=False).lower()
    codec_tag = str(video.get("codec_tag_string", "")).lower()
    return (
        "dovi" in text
        or "dolby vision" in text
        or codec_tag in {"dvhe", "dvh1"}
    )


def has_stereo_3d(video: dict[str, Any]) -> bool:
    tags = video.get("tags") or {}
    stereo_mode = str(tags.get("stereo_mode", "")).strip().lower()
    if stereo_mode and stereo_mode not in {"mono", "2d"}:
        return True
    for side_data in video.get("side_data_list") or []:
        if "stereo 3d" in str(side_data.get("side_data_type", "")).lower():
            return True
    return False


def get_rotation(video: dict[str, Any]) -> int:
    values: list[Any] = []
    tags = video.get("tags") or {}
    values.append(tags.get("rotate"))
    for side_data in video.get("side_data_list") or []:
        values.append(side_data.get("rotation"))
    for value in values:
        try:
            angle = int(round(float(value))) % 360
        except (TypeError, ValueError):
            continue
        # 只接受常见直角旋转；其他角度交给 FFmpeg，但不据此交换宽高。
        if angle in {0, 90, 180, 270}:
            return angle
    return 0


def audio_plan_for_stream(stream: dict[str, Any]) -> AudioPlan:
    codec = str(stream.get("codec_name", "unknown")).lower()
    channels = parse_int(stream.get("channels"), 0)
    sample_rate = parse_int(stream.get("sample_rate"), 0)
    source_bitrate = parse_int(stream.get("bit_rate"), 0)
    bits_per_sample = max(
        parse_int(stream.get("bits_per_sample"), 0),
        parse_int(stream.get("bits_per_raw_sample"), 0),
    )

    high_quality_reasons: list[str] = []
    if codec in LOSSLESS_AUDIO_CODECS:
        high_quality_reasons.append("无损源")
    if channels >= 3:
        high_quality_reasons.append(f"{channels} 声道")
    if source_bitrate > 160_000:
        high_quality_reasons.append(f"源码率 {source_bitrate // 1000} kbps")
    if bits_per_sample >= 24:
        high_quality_reasons.append(f"{bits_per_sample}-bit")
    if source_bitrate <= 0:
        high_quality_reasons.append("源码率未知，保守处理")

    target = 192 if high_quality_reasons else 128
    reason = "、".join(high_quality_reasons) if high_quality_reasons else "普通质量音轨"

    if codec == "opus":
        if source_bitrate <= 0 or source_bitrate <= target * 1000:
            return AudioPlan(
                input_index=parse_int(stream.get("index")),
                codec=codec,
                channels=channels,
                sample_rate=sample_rate,
                source_bitrate=source_bitrate,
                action="copy",
                target_kbps=0,
                reason="已有 Opus，避免二次有损编码",
            )

    return AudioPlan(
        input_index=parse_int(stream.get("index")),
        codec=codec,
        channels=channels,
        sample_rate=sample_rate,
        source_bitrate=source_bitrate,
        action="opus",
        target_kbps=target,
        reason=reason,
    )


def get_duration(probe: dict[str, Any], main_video: dict[str, Any]) -> float:
    duration = parse_float((probe.get("format") or {}).get("duration"), 0.0)
    if duration > 0:
        return duration
    duration = parse_float(main_video.get("duration"), 0.0)
    if duration > 0:
        return duration
    values = [parse_float(s.get("duration"), 0.0) for s in probe.get("streams") or []]
    return max(values, default=0.0)


# ----------------------------- 主程序 -----------------------------

class AV1CompressorApp:
    def __init__(self, root: tk.Tk, config: dict[str, str]) -> None:
        self.root = root
        self.ffmpeg = config["ffmpeg_path"]
        self.ffprobe = config["ffprobe_path"]

        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.current_process: Optional[subprocess.Popen[str]] = None
        self.process_lock = threading.Lock()
        self.sources: list[InputSource] = []
        self.tasks: list[Task] = []
        self.tree_items: dict[str, str] = {}
        self.batch_started_at = 0.0
        self.completed_weight = 0.0
        self.total_weight = 0.0
        self.completed_count = 0
        self.running = False
        self.move_original_enabled = True
        self.current_state_context: Optional[tuple[list[InputSource], Path, str, bool, bool]] = None
        self.existing_output_files: list[Path] = []
        self.existing_output_name_index: dict[str, list[Path]] = {}
        self.claimed_existing_outputs: set[str] = set()
        self.source_stem_counts: dict[str, int] = {}

        self.logger = self._create_logger()
        self.no_gain_records = self._load_no_gain_records()
        self._build_ui()
        self.root.after(100, self._drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._offer_recovery)

    # ---------- 日志 ----------

    def _create_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"{APP_STEM}-{id(self)}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=10 * 1024 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.info("=" * 70)
        logger.info("程序启动：%s", SCRIPT_PATH)
        return logger

    def log(self, text: str, level: int = logging.INFO, gui: bool = True) -> None:
        self.logger.log(level, text)
        if gui:
            self.ui_queue.put(("log", text))

    # ---------- GUI ----------

    def _build_ui(self) -> None:
        self.root.title(APP_STEM)
        self.root.geometry("940x720")
        self.root.minsize(780, 620)

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        main.rowconfigure(5, weight=1)

        source_frame = ttk.LabelFrame(main, text="输入", padding=8)
        source_frame.grid(row=0, column=0, sticky="nsew")
        source_frame.columnconfigure(0, weight=1)
        source_frame.rowconfigure(0, weight=1)

        self.source_list = tk.Listbox(source_frame, height=5, selectmode=tk.EXTENDED)
        self.source_list.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=(0, 8))
        source_scroll = ttk.Scrollbar(source_frame, orient=tk.VERTICAL, command=self.source_list.yview)
        source_scroll.grid(row=0, column=1, rowspan=4, sticky="ns", padx=(0, 8))
        self.source_list.configure(yscrollcommand=source_scroll.set)

        ttk.Button(source_frame, text="添加视频", command=self._add_files).grid(row=0, column=2, sticky="ew", pady=(0, 4))
        ttk.Button(source_frame, text="添加文件夹", command=self._add_folder).grid(row=1, column=2, sticky="ew", pady=4)
        ttk.Button(source_frame, text="移除选中", command=self._remove_sources).grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Button(source_frame, text="清空", command=self._clear_sources).grid(row=3, column=2, sticky="ew", pady=(4, 0))

        output_frame = ttk.LabelFrame(main, text="输出", padding=8)
        output_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="输出文件夹：").grid(row=0, column=0, sticky="w")
        self.output_var = tk.StringVar()
        ttk.Entry(output_frame, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(output_frame, text="选择", command=self._choose_output).grid(row=0, column=2)

        ttk.Label(output_frame, text="文件名后缀：").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.suffix_var = tk.StringVar(value="_AV1")
        ttk.Entry(output_frame, textvariable=self.suffix_var, width=22).grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(output_frame, text="扫描子文件夹并保持目录结构", variable=self.recursive_var).grid(
            row=1, column=2, sticky="e", pady=(8, 0)
        )
        self.move_original_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            output_frame,
            text=f"成功且体积变小后，将原视频移入输入根目录的“{PENDING_DELETE_DIRNAME}”",
            variable=self.move_original_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        task_frame = ttk.LabelFrame(main, text="任务", padding=6)
        task_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        task_frame.columnconfigure(0, weight=1)
        task_frame.rowconfigure(0, weight=1)

        columns = ("resolution", "quality", "audio", "status")
        self.task_tree = ttk.Treeview(task_frame, columns=columns, show="tree headings", height=9)
        self.task_tree.heading("#0", text="文件")
        self.task_tree.heading("resolution", text="分辨率")
        self.task_tree.heading("quality", text="视频参数")
        self.task_tree.heading("audio", text="音频")
        self.task_tree.heading("status", text="状态")
        self.task_tree.column("#0", width=330, minwidth=180)
        self.task_tree.column("resolution", width=105, anchor=tk.CENTER)
        self.task_tree.column("quality", width=115, anchor=tk.CENTER)
        self.task_tree.column("audio", width=160, anchor=tk.CENTER)
        self.task_tree.column("status", width=150, anchor=tk.CENTER)
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(task_frame, orient=tk.VERTICAL, command=self.task_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=tree_scroll.set)

        progress_frame = ttk.LabelFrame(main, text="进度", padding=8)
        progress_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        progress_frame.columnconfigure(1, weight=1)

        ttk.Label(progress_frame, text="当前文件：").grid(row=0, column=0, sticky="w")
        self.current_text = tk.StringVar(value="等待开始")
        ttk.Label(progress_frame, textvariable=self.current_text).grid(row=0, column=1, sticky="w")
        self.current_progress = ttk.Progressbar(progress_frame, maximum=100)
        self.current_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        ttk.Label(progress_frame, text="全部任务：").grid(row=2, column=0, sticky="w")
        self.overall_text = tk.StringVar(value="0 / 0")
        ttk.Label(progress_frame, textvariable=self.overall_text).grid(row=2, column=1, sticky="w")
        self.overall_progress = ttk.Progressbar(progress_frame, maximum=100)
        self.overall_progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        button_frame = ttk.Frame(main)
        button_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        button_frame.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(button_frame, text="开始压制", command=self._start)
        self.start_button.grid(row=0, column=1, padx=(0, 6))
        self.stop_button = ttk.Button(button_frame, text="停止", command=self._stop, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=2)

        log_frame = ttk.LabelFrame(main, text=f"日志（完整日志：{LOG_PATH.name}）", padding=6)
        log_frame.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(log_frame, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[("视频文件", " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))), ("所有文件", "*.*")],
        )
        for value in paths:
            self._add_source(InputSource(str(Path(value).resolve()), "file"))

    def _add_folder(self) -> None:
        value = filedialog.askdirectory(title="选择输入文件夹")
        if value:
            self._add_source(InputSource(str(Path(value).resolve()), "dir"))

    def _add_source(self, source: InputSource) -> None:
        key = os.path.normcase(source.path)
        if any(os.path.normcase(item.path) == key for item in self.sources):
            return
        self.sources.append(source)
        prefix = "[文件] " if source.kind == "file" else "[文件夹] "
        self.source_list.insert(tk.END, prefix + source.path)

    def _remove_sources(self) -> None:
        indexes = list(self.source_list.curselection())
        for index in reversed(indexes):
            self.source_list.delete(index)
            del self.sources[index]

    def _clear_sources(self) -> None:
        self.sources.clear()
        self.source_list.delete(0, tk.END)

    def _choose_output(self) -> None:
        value = filedialog.askdirectory(title="选择输出文件夹")
        if value:
            self.output_var.set(str(Path(value).resolve()))

    # ---------- 开始 / 停止 ----------

    def _start(self) -> None:
        if self.running:
            return
        if not self.sources:
            messagebox.showwarning("缺少输入", "请先添加视频文件或输入文件夹。")
            return
        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showwarning("缺少输出", "请选择输出文件夹。")
            return

        suffix = self.suffix_var.get()
        if INVALID_SUFFIX_CHARS.search(suffix) or suffix.endswith((".", " ")):
            messagebox.showerror("后缀无效", "文件名后缀不能包含 < > : \" / \\ | ? *，也不能以点或空格结尾。")
            return

        output_dir = Path(output_text).expanduser().resolve()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("输出目录错误", str(exc))
            return

        self.running = True
        self.stop_event.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.current_progress["value"] = 0
        self.overall_progress["value"] = 0
        self.current_text.set("正在检查环境……")
        self.overall_text.set("准备任务")
        self.task_tree.delete(*self.task_tree.get_children())
        self.tree_items.clear()

        sources = [InputSource(item.path, item.kind) for item in self.sources]
        recursive = bool(self.recursive_var.get())
        move_original = bool(self.move_original_var.get())
        self.worker = threading.Thread(
            target=self._worker_main,
            args=(sources, output_dir, suffix, recursive, move_original),
            daemon=True,
        )
        self.worker.start()

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.log("收到停止请求，正在结束当前 FFmpeg 进程……", logging.WARNING)
        self.current_text.set("正在停止……")
        threading.Thread(target=self._terminate_current_process, daemon=True).start()

    def _terminate_current_process(self) -> None:
        with self.process_lock:
            process = self.current_process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        except OSError:
            pass

    # ---------- 后台总流程 ----------

    def _worker_main(
        self,
        sources: list[InputSource],
        output_dir: Path,
        suffix: str,
        recursive: bool,
        move_original: bool,
    ) -> None:
        self.move_original_enabled = move_original
        self.current_state_context = (sources, output_dir, suffix, recursive, move_original)
        try:
            self._startup_checks(output_dir)
            if self.stop_event.is_set():
                raise InterruptedError("用户停止")

            self.ui_queue.put(("current_text", "正在扫描和分析视频……"))
            tasks = self._scan_tasks(sources, output_dir, suffix, recursive)
            self.tasks = tasks
            if not tasks:
                raise RuntimeError("没有找到可处理的视频。具体跳过原因请查看日志。")

            self.total_weight = sum(max(task.weight, 1.0) for task in tasks)
            self.completed_weight = 0.0
            self.completed_count = 0
            self.batch_started_at = time.monotonic()
            self._save_state(sources, output_dir, suffix, recursive, move_original, active=True)

            for task in tasks:
                if self.stop_event.is_set():
                    raise InterruptedError("用户停止")

                if task.status in {"跳过", "无收益"}:
                    self.completed_weight += max(task.weight, 1.0)
                    self.completed_count += 1
                    self._update_task_ui(task)
                    self._update_overall_progress(0.0, 0.0, None)
                    continue

                task.status = "处理中"
                task.message = ""
                self._update_task_ui(task)
                self._save_state(sources, output_dir, suffix, recursive, move_original, active=True)

                result_status, message = self._process_task(task)
                task.status = result_status
                task.message = message
                self.completed_weight += max(task.weight, 1.0)
                self.completed_count += 1
                self._update_task_ui(task)
                self._update_overall_progress(0.0, 0.0, None)
                self._save_state(sources, output_dir, suffix, recursive, move_original, active=True)

            self._save_state(sources, output_dir, suffix, recursive, move_original, active=False)
            success = sum(1 for t in tasks if t.status == "完成")
            skipped = sum(1 for t in tasks if t.status in {"跳过", "无收益"})
            failed = sum(1 for t in tasks if t.status == "失败")
            summary = f"全部结束：完成 {success}，跳过/无收益 {skipped}，失败 {failed}。"
            self.log(summary)
            self.ui_queue.put(("finished", summary))

        except InterruptedError:
            self.log("任务已停止；未完成队列会在下次启动时提供恢复。", logging.WARNING)
            try:
                self._save_state(sources, output_dir, suffix, recursive, move_original, active=True)
            except Exception:
                self.logger.exception("保存停止状态失败")
            self.ui_queue.put(("stopped", "任务已停止"))
        except Exception as exc:
            self.logger.exception("后台任务异常")
            self.log(f"任务中止：{exc}", logging.ERROR)
            try:
                self._save_state(sources, output_dir, suffix, recursive, move_original, active=True)
            except Exception:
                self.logger.exception("保存异常状态失败")
            self.ui_queue.put(("failed", str(exc)))

    # ---------- 环境检查 ----------

    def _startup_checks(self, output_dir: Path) -> None:
        self.log("开始启动前检查。")
        for executable, name in ((self.ffmpeg, "FFmpeg"), (self.ffprobe, "ffprobe")):
            result = subprocess.run(
                [executable, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                **subprocess_options(),
            )
            if result.returncode != 0:
                raise RuntimeError(f"{name} 无法运行：{result.stdout.strip()}")
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            self.log(f"{name}：{first_line}")

        encoder_result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **subprocess_options(),
        )
        encoder_text = encoder_result.stdout.lower()
        if "libsvtav1" not in encoder_text:
            raise RuntimeError("当前 FFmpeg 不包含 libsvtav1 编码器。")
        if "libopus" not in encoder_text:
            raise RuntimeError("当前 FFmpeg 不包含 libopus 编码器。")

        svt_help = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-h", "encoder=libsvtav1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **subprocess_options(),
        ).stdout.lower()
        if "svtav1-params" not in svt_help:
            raise RuntimeError("当前 FFmpeg 的 libsvtav1 不支持 svtav1-params，请更换较新的完整构建。")

        muxer_result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-muxers"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **subprocess_options(),
        )
        if "matroska" not in muxer_result.stdout.lower():
            raise RuntimeError("当前 FFmpeg 不包含 Matroska（MKV）封装器。")

        for directory, label in ((SCRIPT_DIR, "工具目录"), (output_dir, "输出目录")):
            test_file = directory / f".__{APP_STEM}_write_test_{os.getpid()}"
            try:
                test_file.write_bytes(b"ok")
                test_file.unlink()
            except OSError as exc:
                raise RuntimeError(f"{label}不可写：{directory}\n{exc}") from exc

        free = shutil.disk_usage(output_dir).free
        if free < 1024 * 1024 * 1024:
            raise RuntimeError(f"输出磁盘剩余空间不足 1 GB：{format_bytes(free)}")

        self.log("启动前检查通过。")

    # ---------- 扫描与探测 ----------

    def _scan_tasks(
        self,
        sources: list[InputSource],
        output_dir: Path,
        suffix: str,
        recursive: bool,
    ) -> list[Task]:
        candidates: list[tuple[Path, Path]] = []  # (文件, 来源根目录)
        seen: set[str] = set()
        output_resolved = output_dir.resolve()

        for source in sources:
            source_path = Path(source.path)
            exclude_output_subtree = False
            if source.kind == "file":
                values = [source_path]
                root = source_path.parent
            else:
                root = source_path
                source_resolved = source_path.resolve()
                pending_delete_root = source_resolved / PENDING_DELETE_DIRNAME
                exclude_output_subtree = (
                    output_resolved != source_resolved
                    and is_relative_to(output_resolved, source_resolved)
                )
                iterator: Iterable[Path] = source_path.rglob("*") if recursive else source_path.glob("*")
                values = iterator

            for path in values:
                if self.stop_event.is_set():
                    raise InterruptedError
                try:
                    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    resolved = path.resolve()
                except OSError:
                    continue

                if exclude_output_subtree and is_relative_to(resolved, output_resolved):
                    continue
                if source.kind == "dir" and is_relative_to(resolved, pending_delete_root):
                    continue
                if ".__processing__" in resolved.name:
                    continue
                key = os.path.normcase(str(resolved))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((resolved, root.resolve()))

        self.log(f"发现 {len(candidates)} 个候选视频，开始 ffprobe 分析。")
        tasks: list[Task] = []
        reserved_outputs: set[str] = set()
        self.existing_output_files = self._index_existing_outputs(output_dir)
        self.existing_output_name_index = self._build_existing_output_name_index(self.existing_output_files)
        self.claimed_existing_outputs.clear()
        if self.existing_output_files:
            self.log(f"输出目录中发现 {len(self.existing_output_files)} 个可供旧成品验证的 MKV。")

        for index, (path, source_root) in enumerate(candidates, start=1):
            if self.stop_event.is_set():
                raise InterruptedError
            self.ui_queue.put(("current_text", f"分析 {index}/{len(candidates)}：{path.name}"))
            try:
                probe = self._probe(path)
                task = self._task_from_probe(path, source_root, output_dir, suffix, probe, reserved_outputs)
                if task is None:
                    continue
                tasks.append(task)
                self._insert_task_ui(task)
            except Exception as exc:
                self.log(f"跳过 {path}：分析失败：{exc}", logging.WARNING)

        self.source_stem_counts = {}
        for task in tasks:
            key = Path(task.input_path).stem.casefold()
            self.source_stem_counts[key] = self.source_stem_counts.get(key, 0) + 1
        return tasks

    def _index_existing_outputs(self, output_dir: Path) -> list[Path]:
        """一次性索引输出目录中的 MKV，避免每个任务都递归扫描磁盘。"""
        results: list[Path] = []
        if not output_dir.exists():
            return results
        try:
            for current_root, dirnames, filenames in os.walk(output_dir):
                dirnames[:] = [name for name in dirnames if name != PENDING_DELETE_DIRNAME]
                root_path = Path(current_root)
                for filename in filenames:
                    if Path(filename).suffix.lower() != ".mkv":
                        continue
                    path = root_path / filename
                    try:
                        if not path.is_file():
                            continue
                        lower_name = filename.casefold()
                        if ".__processing__.mkv" in lower_name or ".invalid_" in lower_name:
                            continue
                        if lower_name.endswith(".no_gain_unrecorded.mkv"):
                            continue
                        results.append(path.resolve())
                    except OSError:
                        continue
        except OSError as exc:
            self.log(f"扫描输出目录中的旧成品失败：{exc}", logging.WARNING)
        return results

    def _build_existing_output_name_index(self, paths: list[Path]) -> dict[str, list[Path]]:
        """按可能的原文件名建立索引，避免大量视频时逐任务遍历全部 MKV。"""
        index: dict[str, list[Path]] = {}
        separators = set("_- .(（[【{〔")
        for path in paths:
            stem = path.stem.casefold()
            keys = {stem}
            for position, char in enumerate(stem):
                if position > 0 and char in separators:
                    keys.add(stem[:position])
            for key in keys:
                index.setdefault(key, []).append(path)
        return index

    def _probe(self, path: Path) -> dict[str, Any]:
        command = [
            self.ffprobe,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **subprocess_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffprobe 返回失败")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ffprobe JSON 无法解析：{exc}") from exc

    def _task_from_probe(
        self,
        path: Path,
        source_root: Path,
        output_dir: Path,
        suffix: str,
        probe: dict[str, Any],
        reserved_outputs: set[str],
    ) -> Optional[Task]:
        streams = probe.get("streams") or []
        main_videos = [
            s for s in streams
            if s.get("codec_type") == "video"
            and parse_int((s.get("disposition") or {}).get("attached_pic"), 0) == 0
        ]
        if not main_videos:
            self.log(f"跳过 {path.name}：没有主视频流。", logging.WARNING)
            return None
        if len(main_videos) > 1:
            self.log(f"跳过 {path.name}：包含多个主视频流，避免选错画面。", logging.WARNING)
            return None

        video = main_videos[0]
        attached_pictures = [
            s for s in streams
            if s.get("codec_type") == "video"
            and parse_int((s.get("disposition") or {}).get("attached_pic"), 0) == 1
        ]
        if attached_pictures:
            self.log(
                f"跳过 {path.name}：包含 attached_pic 封面流；为避免封面在 MKV 中被错误处理，不自动压制。",
                logging.WARNING,
            )
            return None
        video_codec = str(video.get("codec_name", "unknown")).lower()
        if video_codec == "av1":
            self.log(f"跳过 {path.name}：源视频已经是 AV1，避免再次有损压制。")
            return None
        if has_dolby_vision(video):
            self.log(f"跳过 {path.name}：检测到 Dolby Vision。", logging.WARNING)
            return None
        if has_stereo_3d(video):
            self.log(f"跳过 {path.name}：检测到立体/3D 视频。", logging.WARNING)
            return None

        pix_fmt = str(video.get("pix_fmt", "")).lower()
        bit_depth = infer_bit_depth(video)
        if any(token in pix_fmt for token in ("yuva", "gbr", "rgb", "bgr", "444", "422")):
            self.log(f"跳过 {path.name}：特殊像素格式 {pix_fmt}，不自动转换。", logging.WARNING)
            return None
        if bit_depth > 10:
            self.log(f"跳过 {path.name}：{bit_depth}-bit 视频，不自动降为 10-bit。", logging.WARNING)
            return None

        field_order = str(video.get("field_order", "unknown")).lower()
        if field_order not in {"", "unknown", "progressive"}:
            self.log(f"跳过 {path.name}：检测到隔行视频（{field_order}）。", logging.WARNING)
            return None

        raw_width = parse_int(video.get("width"), 0)
        raw_height = parse_int(video.get("height"), 0)
        if raw_width <= 0 or raw_height <= 0:
            self.log(f"跳过 {path.name}：无法取得有效分辨率。", logging.WARNING)
            return None

        duration = get_duration(probe, video)
        if duration <= 0:
            self.log(f"跳过 {path.name}：无法取得有效时长。", logging.WARNING)
            return None

        fps = parse_fraction(video.get("avg_frame_rate")) or parse_fraction(video.get("r_frame_rate"))
        hdr_type = detect_hdr_type(video)
        side_data_types = [
            str(item.get("side_data_type", "")).strip()
            for item in (video.get("side_data_list") or [])
        ]
        side_data_text = " ".join(side_data_types).lower()
        if "dynamic hdr" in side_data_text or "hdr10+" in side_data_text:
            self.log(f"跳过 {path.name}：检测到 HDR10+ 动态元数据。", logging.WARNING)
            return None
        required_hdr_side_data = [
            value for value in side_data_types
            if any(token in value.lower() for token in ("mastering display", "content light level"))
        ]
        rotation = get_rotation(video)
        if rotation in {90, 270}:
            width, height = raw_height, raw_width
        else:
            width, height = raw_width, raw_height
        pixels = raw_width * raw_height
        crf = choose_crf(pixels, hdr_type, fps)

        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        audio_plans = [audio_plan_for_stream(s) for s in audio_streams]
        if any(plan.channels > 8 for plan in audio_plans):
            self.log(f"跳过 {path.name}：包含超过 8 声道的特殊音频。", logging.WARNING)
            return None

        subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
        attachment_streams = [s for s in streams if s.get("codec_type") == "attachment"]

        try:
            relative_parent = path.parent.relative_to(source_root)
        except ValueError:
            relative_parent = Path()
        base_final_path = output_dir / relative_parent / f"{path.stem}{suffix}.mkv"
        if base_final_path.resolve(strict=False) == path.resolve(strict=False):
            base_final_path = output_dir / relative_parent / f"{path.stem}_AV1.mkv"
            self.log(f"输出名与源文件相同，自动改为：{base_final_path.name}", logging.WARNING)

        stat = path.stat()
        fps_factor = max(1.0, fps / 30.0) ** 0.5 if fps > 0 else 1.0
        weight = duration * max(pixels, 1) * fps_factor

        try:
            source_relative = path.relative_to(source_root)
        except ValueError:
            source_relative = Path(path.name)

        task = Task(
            input_path=str(path),
            output_path=str(base_final_path),
            source_root=str(source_root),
            output_root=str(output_dir.resolve(strict=False)),
            relative_parent=str(relative_parent),
            source_relative=str(source_relative),
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            pixels=pixels,
            crf=crf,
            hdr_type=hdr_type,
            video_index=parse_int(video.get("index")),
            video_codec=video_codec,
            pix_fmt=pix_fmt,
            rotation=rotation,
            color_primaries=str(video.get("color_primaries", "")),
            color_transfer=str(video.get("color_transfer", "")),
            color_space=str(video.get("color_space", "")),
            color_range=str(video.get("color_range", "")),
            chapter_count=len(probe.get("chapters") or []),
            required_hdr_side_data=required_hdr_side_data,
            audio_plans=audio_plans,
            subtitle_indexes=[parse_int(s.get("index")) for s in subtitle_streams],
            subtitle_codecs=[str(s.get("codec_name", "unknown")).lower() for s in subtitle_streams],
            attachment_indexes=[parse_int(s.get("index")) for s in attachment_streams],
            cover_indexes=[parse_int(s.get("index")) for s in attached_pictures],
            input_size=stat.st_size,
            input_mtime_ns=stat.st_mtime_ns,
            weight=weight,
        )

        no_gain_record = self._matching_no_gain_record(task)
        if no_gain_record is not None:
            task.status = "无收益"
            task.message = "已有无压缩收益记录，源文件和编码策略均未变化"
            reserved_outputs.add(os.path.normcase(str(base_final_path.resolve(strict=False))))
            self._cleanup_recorded_no_gain_output(task, no_gain_record)
            self.log(f"跳过 {path.name}：命中无压缩收益记录。")
        else:
            task.output_path = str(unique_path(base_final_path, reserved_outputs))

        audio_summary = []
        for plan in audio_plans:
            if plan.action == "copy":
                audio_summary.append("Opus复制")
            else:
                audio_summary.append(f"{plan.target_kbps}k")
            self.log(
                f"音频判断 {path.name} / 流 {plan.input_index}: {plan.codec}, "
                f"{plan.channels}ch, {plan.source_bitrate or '未知'} bps -> "
                f"{'复制' if plan.action == 'copy' else f'Opus {plan.target_kbps} kbps'}；原因：{plan.reason}",
                gui=False,
            )
        self.log(
            f"分析完成：{path.name} | {width}x{height} | {fps:.3f} fps | "
            f"{hdr_type} | 旋转 {rotation}° | CRF {crf} | 音频：{', '.join(audio_summary) or '无'}"
        )
        return task

    # ---------- 单任务编码 ----------

    def _process_task(self, task: Task) -> tuple[str, str]:
        input_path = Path(task.input_path)
        final_path = Path(task.output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = final_path.with_name(f"{final_path.stem}.__processing__.mkv")

        if not input_path.exists():
            return "失败", "源文件不存在"
        stat = input_path.stat()
        if stat.st_size != task.input_size or stat.st_mtime_ns != task.input_mtime_ns:
            return "失败", "源文件在扫描后发生变化"

        if temp_path.exists():
            try:
                temp_path.unlink()
                self.log(f"清理上次遗留临时文件：{temp_path}")
            except OSError as exc:
                return "失败", f"无法删除遗留临时文件：{exc}"

        if final_path.exists():
            valid, details = self._validate_existing_output(task, final_path)
            if valid:
                self._claim_existing_output(final_path)
                return self._handle_reusable_output(task, final_path, "预期路径中的已有输出")
            renamed = final_path.with_name(
                f"{final_path.stem}.invalid_{datetime.now():%Y%m%d_%H%M%S}{final_path.suffix}"
            )
            try:
                final_path.rename(renamed)
                self.log(f"已有输出验证失败（{details}），已保留为：{renamed}", logging.WARNING)
            except OSError as exc:
                return "失败", f"已有无效输出无法改名：{exc}"

        legacy_path = self._find_reusable_old_output(task, final_path)
        if legacy_path is not None:
            return self._handle_reusable_output(task, legacy_path, "扫描发现的旧成品")

        free = shutil.disk_usage(final_path.parent).free
        minimum_free = min(task.input_size + 512 * 1024 * 1024, 8 * 1024 * 1024 * 1024)
        if free < minimum_free:
            return "失败", f"磁盘空间不足：剩余 {format_bytes(free)}"

        command = self._build_ffmpeg_command(task, temp_path)
        self.log(f"开始编码：{input_path}")
        self.log(f"FFmpeg 命令：{display_command(command)}", gui=False)

        stderr_lines: list[str] = []
        start_time = time.monotonic()
        current_seconds = 0.0
        current_speed = 0.0

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **subprocess_options(),
            )
        except OSError as exc:
            return "失败", f"无法启动 FFmpeg：{exc}"

        with self.process_lock:
            self.current_process = process

        stderr_thread = threading.Thread(
            target=self._collect_stderr,
            args=(process, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        try:
            assert process.stdout is not None
            progress_data: dict[str, str] = {}
            for raw_line in process.stdout:
                if self.stop_event.is_set():
                    self._terminate_current_process()
                    raise InterruptedError
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                progress_data[key] = value
                if key == "out_time":
                    current_seconds = min(parse_ffmpeg_time(value), task.duration)
                elif key == "speed":
                    current_speed = parse_float(value.rstrip("x"), 0.0)
                elif key == "progress":
                    percent = min(max(current_seconds / task.duration, 0.0), 1.0)
                    current_eta = (task.duration - current_seconds) / current_speed if current_speed > 0 else None
                    self._update_encoding_progress(task, percent, current_speed, current_eta)
                    progress_data.clear()

            return_code = process.wait()
            stderr_thread.join(timeout=2)
        finally:
            with self.process_lock:
                self.current_process = None

        elapsed = time.monotonic() - start_time
        stderr_text = "\n".join(stderr_lines)
        if stderr_text:
            self.logger.debug("FFmpeg stderr for %s:\n%s", input_path, stderr_text)

        if self.stop_event.is_set():
            self._safe_unlink(temp_path)
            raise InterruptedError

        if return_code != 0:
            self._safe_unlink(temp_path)
            tail = " | ".join(stderr_lines[-5:]) or f"返回码 {return_code}"
            self.log(f"编码失败：{input_path.name}：{tail}", logging.ERROR)
            return "失败", tail[:300]

        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            self._safe_unlink(temp_path)
            return "失败", "FFmpeg 未生成有效临时文件"

        self.log(f"编码结束，开始验证：{input_path.name}")
        suspicious = any(
            ERROR_WORDS.search(line)
            and "failed to set thread priority" not in line.lower()
            for line in stderr_lines
        )
        valid, details = self._validate_output(task, temp_path, suspicious)
        if not valid:
            self._safe_unlink(temp_path)
            self.log(f"验证失败：{input_path.name}：{details}", logging.ERROR)
            return "失败", f"验证失败：{details}"

        output_size = temp_path.stat().st_size
        task.output_size = output_size
        if output_size >= task.input_size:
            if not self._record_no_gain(task, output_size):
                preserved = final_path.with_name(f"{final_path.stem}.no_gain_unrecorded.mkv")
                try:
                    os.replace(temp_path, preserved)
                    return "失败", f"无收益状态写入失败；输出已保留为 {preserved.name}"
                except OSError:
                    return "失败", "无收益状态写入失败；临时输出未删除"
            self._safe_unlink(temp_path)
            self.log(
                f"无压缩收益：{input_path.name}，源 {format_bytes(task.input_size)}，"
                f"输出 {format_bytes(output_size)}；已记录并删除输出。",
                logging.WARNING,
            )
            return "无收益", "输出不小于源文件，已记录"

        try:
            os.replace(temp_path, final_path)
        except OSError as exc:
            self._safe_unlink(temp_path)
            return "失败", f"临时文件改名失败：{exc}"

        move_detail = ""
        if self.move_original_enabled:
            moved, move_detail = self._move_original_to_pending(task)
            if not moved:
                self.log(f"输出已完成，但移动原文件失败：{move_detail}", logging.WARNING)

        saving = 100.0 * (1.0 - output_size / task.input_size)
        self.log(
            f"完成：{input_path.name} -> {final_path.name} | "
            f"{format_bytes(task.input_size)} -> {format_bytes(output_size)} | "
            f"节省 {saving:.1f}% | 用时 {format_duration(elapsed)}"
        )
        message = f"节省 {saving:.1f}%"
        if move_detail:
            message += f"；{move_detail}"
        return "完成", message

    def _build_ffmpeg_command(self, task: Task, temp_path: Path) -> list[str]:
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel", "warning",
            "-stats_period", "0.5",
            "-progress", "pipe:1",
            "-i", task.input_path,
            "-map", f"0:{task.video_index}",
        ]

        for plan in task.audio_plans:
            command += ["-map", f"0:{plan.input_index}"]
        for index in task.subtitle_indexes:
            command += ["-map", f"0:{index}"]
        for index in task.attachment_indexes:
            command += ["-map", f"0:{index}"]
        for index in task.cover_indexes:
            command += ["-map", f"0:{index}"]

        command += [
            "-map_metadata", "0",
            "-map_chapters", "0",
            "-c:v:0", "libsvtav1",
            "-preset", "5",
            "-crf", str(task.crf),
            "-svtav1-params", "tune=0",
            "-pix_fmt", "yuv420p10le",
            "-fps_mode:v:0", "passthrough",
        ]

        # 显式传递常见色彩标记；找不到时不乱填。
        color_values = (
            (task.color_primaries, "-color_primaries:v:0"),
            (task.color_transfer, "-color_trc:v:0"),
            (task.color_space, "-colorspace:v:0"),
            (task.color_range, "-color_range:v:0"),
        )
        for value, option in color_values:
            clean = str(value).strip()
            if clean and clean not in {"unknown", "reserved"}:
                command += [option, clean]

        for audio_output_index, plan in enumerate(task.audio_plans):
            if plan.action == "copy":
                command += [f"-c:a:{audio_output_index}", "copy"]
            else:
                command += [
                    f"-c:a:{audio_output_index}", "libopus",
                    f"-b:a:{audio_output_index}", f"{plan.target_kbps}k",
                    f"-vbr:a:{audio_output_index}", "on",
                    f"-compression_level:a:{audio_output_index}", "10",
                ]

        for subtitle_output_index, codec in enumerate(task.subtitle_codecs):
            if codec in TEXT_SUBTITLE_TO_SRT:
                command += [f"-c:s:{subtitle_output_index}", "srt"]
            else:
                command += [f"-c:s:{subtitle_output_index}", "copy"]

        if task.attachment_indexes:
            command += ["-c:t", "copy"]
        for cover_output_index, _ in enumerate(task.cover_indexes, start=1):
            command += [f"-c:v:{cover_output_index}", "copy"]

        command += [
            "-metadata", f"AV1TOOL_SOURCE_SIZE={task.input_size}",
            "-metadata", f"AV1TOOL_SOURCE_MTIME_NS={task.input_mtime_ns}",
            "-metadata", f"AV1TOOL_SOURCE_RELATIVE={task.source_relative}",
            "-metadata", f"AV1TOOL_POLICY_VERSION={ENCODING_POLICY_VERSION}",
            "-max_muxing_queue_size", "4096",
            "-f", "matroska",
            str(temp_path),
        ]
        return command

    def _collect_stderr(self, process: subprocess.Popen[str], target: list[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            clean = line.rstrip()
            if clean:
                target.append(clean)
                if len(target) > 4000:
                    del target[:1000]

    # ---------- 已有/旧成品发现与复用 ----------

    def _existing_output_key(self, path: Path) -> str:
        return os.path.normcase(str(path.resolve(strict=False)))

    def _claim_existing_output(self, path: Path) -> None:
        self.claimed_existing_outputs.add(self._existing_output_key(path))

    def _is_claimed_existing_output(self, path: Path) -> bool:
        return self._existing_output_key(path) in self.claimed_existing_outputs

    def _legacy_name_matches_source(self, task: Task, candidate: Path) -> bool:
        source_stem = Path(task.input_path).stem.casefold()
        candidate_stem = candidate.stem.casefold()
        if candidate_stem == source_stem:
            return True
        separators = ("_", "-", " ", ".", "(", "（", "[", "【", "{", "〔")
        return any(candidate_stem.startswith(source_stem + sep) for sep in separators)

    def _source_metadata_match(self, task: Task, probe: dict[str, Any]) -> Optional[bool]:
        raw_tags = ((probe.get("format") or {}).get("tags") or {})
        tags = {str(key).casefold(): str(value) for key, value in raw_tags.items()}
        marker_keys = {
            "av1tool_source_size",
            "av1tool_source_mtime_ns",
            "av1tool_source_relative",
        }
        if not marker_keys.intersection(tags):
            return None
        try:
            if int(str(tags.get("av1tool_source_size", "-1")).strip()) != task.input_size:
                return False
            if int(str(tags.get("av1tool_source_mtime_ns", "-1")).strip()) != task.input_mtime_ns:
                return False
        except (TypeError, ValueError):
            return False
        tagged_relative = tags.get("av1tool_source_relative")
        if tagged_relative is not None:
            left = os.path.normcase(os.path.normpath(tagged_relative))
            right = os.path.normcase(os.path.normpath(task.source_relative))
            if left != right:
                return False
        return True

    def _find_reusable_old_output(self, task: Task, expected_path: Path) -> Optional[Path]:
        """
        在编码前查找旧成品。

        有本工具来源标记的文件可跨目录匹配；没有标记的旧版文件只依赖
        文件名、媒体结构和唯一性，避免把同名但无关的视频误认成成品。
        """
        expected_parent = expected_path.parent.resolve(strict=False)
        input_resolved = Path(task.input_path).resolve(strict=False)
        evaluated: list[tuple[Path, int, str]] = []

        source_key = Path(task.input_path).stem.casefold()
        for candidate in self.existing_output_name_index.get(source_key, []):
            try:
                candidate_resolved = candidate.resolve(strict=False)
            except OSError:
                continue
            if candidate_resolved == expected_path.resolve(strict=False):
                continue
            if candidate_resolved == input_resolved:
                continue
            if self._is_claimed_existing_output(candidate_resolved):
                continue
            if not candidate_resolved.exists():
                continue
            if not self._legacy_name_matches_source(task, candidate_resolved):
                continue

            try:
                probe = self._probe(candidate_resolved)
            except Exception as exc:
                self.log(f"旧成品候选无法读取，忽略：{candidate_resolved}：{exc}", logging.WARNING, gui=False)
                continue

            valid, details = self._validate_output(
                task,
                candidate_resolved,
                suspicious=False,
                existing=True,
                probe_data=probe,
            )
            if not valid:
                self.log(f"旧成品候选验证未通过，忽略：{candidate_resolved}：{details}", gui=False)
                continue

            metadata_match = self._source_metadata_match(task, probe)
            if metadata_match is False:
                self.log(f"旧成品候选的来源标记不匹配，忽略：{candidate_resolved}", gui=False)
                continue

            same_parent = candidate_resolved.parent.resolve(strict=False) == expected_parent
            if metadata_match is True:
                confidence = 3
                reason = "来源指纹匹配"
            elif same_parent:
                confidence = 2
                reason = "同一预期目录中的旧版文件"
            else:
                source_count = self.source_stem_counts.get(source_key, 1)
                if source_count != 1:
                    self.log(
                        f"旧成品候选缺少来源标记，且输入中有 {source_count} 个同名源文件，忽略：{candidate_resolved}",
                        gui=False,
                    )
                    continue
                confidence = 1
                reason = "输出目录内唯一的旧版结构匹配"
            evaluated.append((candidate_resolved, confidence, reason))

        if not evaluated:
            return None

        best_confidence = max(item[1] for item in evaluated)
        best = [item for item in evaluated if item[1] == best_confidence]
        if len(best) != 1:
            paths = "；".join(str(item[0]) for item in best[:5])
            self.log(
                f"发现多个同等可信的旧成品候选，无法安全自动选择，将重新编码：{Path(task.input_path).name}；{paths}",
                logging.WARNING,
            )
            return None

        chosen, _, reason = best[0]
        self._claim_existing_output(chosen)
        self.log(f"编码前发现可复用旧成品：{chosen}（{reason}），将验证后跳过编码。")
        return chosen

    def _handle_reusable_output(self, task: Task, path: Path, source_label: str) -> tuple[str, str]:
        original_output_path = task.output_path
        task.output_path = str(path)
        try:
            task.output_size = path.stat().st_size
        except OSError as exc:
            task.output_path = original_output_path
            return "失败", f"无法读取已有输出大小：{exc}"

        if task.output_size >= task.input_size:
            if not self._record_no_gain(task, task.output_size):
                return "失败", f"{source_label}无压缩收益，但状态记录写入失败；为安全起见未删除输出"
            try:
                path.unlink()
            except OSError as exc:
                return "失败", f"无收益已记录，但无法删除已有输出：{exc}"
            self.log(
                f"{source_label}无压缩收益：{path.name}，已记录并删除输出；保留源文件。",
                logging.WARNING,
            )
            return "无收益", "已有输出不小于源文件，已记录并删除"

        moved_note = ""
        if self.move_original_enabled:
            moved, move_detail = self._move_original_to_pending(task)
            moved_note = f"；{move_detail}"
            if not moved:
                self.log(f"{source_label}有效，但移动原文件失败：{move_detail}", logging.WARNING)
        self.log(f"跳过编码，复用{source_label}：{path}{moved_note}")
        return "跳过", f"复用已有有效输出{moved_note}"

    # ---------- 验证 ----------

    def _validate_existing_output(self, task: Task, path: Path) -> tuple[bool, str]:
        try:
            if path.stat().st_size <= 0:
                return False, "空文件"
            return self._validate_output(task, path, suspicious=False, existing=True)
        except Exception as exc:
            return False, str(exc)

    def _validate_output(
        self,
        task: Task,
        path: Path,
        suspicious: bool,
        existing: bool = False,
        probe_data: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        if probe_data is None:
            try:
                probe = self._probe(path)
            except Exception as exc:
                return False, f"ffprobe 无法读取：{exc}"
        else:
            probe = probe_data

        streams = probe.get("streams") or []
        videos = [
            s for s in streams
            if s.get("codec_type") == "video"
            and parse_int((s.get("disposition") or {}).get("attached_pic"), 0) == 0
        ]
        if len(videos) != 1:
            return False, f"主视频流数量异常：{len(videos)}"
        video = videos[0]
        if str(video.get("codec_name", "")).lower() != "av1":
            return False, "输出视频不是 AV1"
        if infer_bit_depth(video) < 10:
            return False, f"输出不是 10-bit：{video.get('pix_fmt', '未知')}"

        out_width = parse_int(video.get("width"))
        out_height = parse_int(video.get("height"))
        if (out_width, out_height) != (task.width, task.height):
            return False, f"分辨率变化：{task.width}x{task.height} -> {out_width}x{out_height}"

        output_duration = get_duration(probe, video)
        tolerance = max(2.0, task.duration * 0.005)
        if abs(output_duration - task.duration) > tolerance:
            return False, f"时长差异过大：{task.duration:.3f}s -> {output_duration:.3f}s"

        output_fps = parse_fraction(video.get("avg_frame_rate")) or parse_fraction(video.get("r_frame_rate"))
        if task.fps > 0 and output_fps > 0:
            if abs(output_fps - task.fps) / task.fps > 0.02:
                return False, f"帧率变化：{task.fps:.3f} -> {output_fps:.3f}"

        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if len(audio_streams) != len(task.audio_plans):
            return False, f"音轨数量变化：{len(task.audio_plans)} -> {len(audio_streams)}"
        for stream in audio_streams:
            if str(stream.get("codec_name", "")).lower() != "opus":
                return False, f"发现非 Opus 输出音轨：{stream.get('codec_name')}"

        subtitle_count = sum(1 for s in streams if s.get("codec_type") == "subtitle")
        if subtitle_count != len(task.subtitle_indexes):
            return False, f"字幕数量变化：{len(task.subtitle_indexes)} -> {subtitle_count}"

        attachment_count = sum(1 for s in streams if s.get("codec_type") == "attachment")
        if attachment_count != len(task.attachment_indexes):
            return False, f"附件数量变化：{len(task.attachment_indexes)} -> {attachment_count}"

        cover_count = sum(
            1 for s in streams
            if s.get("codec_type") == "video"
            and parse_int((s.get("disposition") or {}).get("attached_pic"), 0) == 1
        )
        if cover_count != len(task.cover_indexes):
            return False, f"封面流数量变化：{len(task.cover_indexes)} -> {cover_count}"

        output_chapter_count = len(probe.get("chapters") or [])
        if task.chapter_count != output_chapter_count:
            return False, f"章节数量变化：{task.chapter_count} -> {output_chapter_count}"

        if task.hdr_type in {"HDR10", "HLG"}:
            detected = detect_hdr_type(video)
            if detected != task.hdr_type:
                return False, f"HDR 标记未保持：{task.hdr_type} -> {detected}"
            output_side_data = [
                str(item.get("side_data_type", "")).lower()
                for item in (video.get("side_data_list") or [])
            ]
            for required in task.required_hdr_side_data:
                if not any(required.lower() == value for value in output_side_data):
                    return False, f"HDR 静态元数据未保持：{required}"

        # 已有输出验证不进行完整解码；本次新生成且日志可疑时进行全片解码。
        if suspicious and not existing:
            self.log(f"检测到可疑 FFmpeg 警告，对 {path.name} 执行完整解码验证。", logging.WARNING)
            ok, detail = self._full_decode_verify(path)
            if not ok:
                return False, detail

        return True, "验证通过"

    def _full_decode_verify(self, path: Path) -> tuple[bool, str]:
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v", "error",
            "-i", str(path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-f", "null",
            "-",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **subprocess_options(),
            )
        except OSError as exc:
            return False, f"无法启动完整解码验证：{exc}"
        with self.process_lock:
            self.current_process = process
        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=self._collect_stderr,
            args=(process, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()
        try:
            while process.poll() is None:
                if self.stop_event.is_set():
                    self._terminate_current_process()
                    raise InterruptedError
                time.sleep(0.2)
            stderr_thread.join(timeout=2)
            stderr = "\n".join(stderr_lines)
            if process.returncode != 0 or stderr.strip():
                return False, f"完整解码验证失败：{stderr.strip()[-500:]}"
            return True, "完整解码通过"
        finally:
            with self.process_lock:
                self.current_process = None

    # ---------- 原文件移动、无收益记录与状态恢复 ----------

    def _state_record_key(self, input_path: str | Path) -> str:
        return os.path.normcase(str(Path(input_path).resolve(strict=False)))

    def _audio_policy_signature(self, task: Task) -> list[dict[str, Any]]:
        return [
            {
                "codec": plan.codec,
                "channels": plan.channels,
                "action": plan.action,
                "target_kbps": plan.target_kbps,
            }
            for plan in task.audio_plans
        ]

    def _load_no_gain_records(self) -> dict[str, dict[str, Any]]:
        if not STATE_PATH.exists():
            return {}
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict) or data.get("version") != STATE_SCHEMA_VERSION:
            return {}
        raw = data.get("no_gain_records")
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _matching_no_gain_record(self, task: Task) -> Optional[dict[str, Any]]:
        key = self._state_record_key(task.input_path)
        record = self.no_gain_records.get(key)
        if record is None:
            return None
        expected = (
            record.get("input_size") == task.input_size
            and record.get("input_mtime_ns") == task.input_mtime_ns
            and record.get("policy_version") == ENCODING_POLICY_VERSION
            and record.get("crf") == task.crf
            and record.get("audio_policy") == self._audio_policy_signature(task)
        )
        if expected:
            return record
        self.no_gain_records.pop(key, None)
        self.log(f"无收益记录已失效，将重新处理：{Path(task.input_path).name}", gui=False)
        return None

    def _record_no_gain(self, task: Task, output_size: int) -> bool:
        key = self._state_record_key(task.input_path)
        self.no_gain_records[key] = {
            "input_path": str(Path(task.input_path).resolve(strict=False)),
            "input_size": task.input_size,
            "input_mtime_ns": task.input_mtime_ns,
            "policy_version": ENCODING_POLICY_VERSION,
            "crf": task.crf,
            "audio_policy": self._audio_policy_signature(task),
            "output_path": str(Path(task.output_path).resolve(strict=False)),
            "output_size": int(output_size),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._save_current_state(active=True)
            return True
        except Exception:
            self.logger.exception("写入无压缩收益记录失败")
            self.no_gain_records.pop(key, None)
            return False

    def _cleanup_recorded_no_gain_output(self, task: Task, record: dict[str, Any]) -> None:
        recorded_path = Path(str(record.get("output_path", task.output_path)))
        processing_path = recorded_path.with_name(f"{recorded_path.stem}.__processing__.mkv")
        expected_size = parse_int(record.get("output_size"), -1)

        for candidate in dict.fromkeys((recorded_path, processing_path)):
            if not candidate.exists():
                continue
            try:
                actual_size = candidate.stat().st_size
            except OSError as exc:
                self.log(f"无法检查无收益残留输出：{candidate}：{exc}", logging.WARNING)
                continue
            if expected_size < 0 or actual_size != expected_size:
                self.log(
                    f"发现与无收益记录相关但尺寸不同的文件，未自动删除：{candidate}",
                    logging.WARNING,
                )
                continue
            valid, details = self._validate_existing_output(task, candidate)
            if not valid:
                self.log(f"无收益残留输出验证失败，未自动删除：{candidate}：{details}", logging.WARNING)
                continue
            try:
                candidate.unlink()
                self.log(f"已删除状态记录对应的无收益残留输出：{candidate}")
            except OSError as exc:
                self.log(f"无法删除无收益残留输出：{candidate}：{exc}", logging.WARNING)

    def _move_original_to_pending(self, task: Task) -> tuple[bool, str]:
        source = Path(task.input_path)
        if not source.exists():
            return True, "原文件已不在原位置"
        root = Path(task.source_root)
        try:
            relative = source.relative_to(root)
        except ValueError:
            relative = Path(source.name)
        destination = root / PENDING_DELETE_DIRNAME / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate = destination
            counter = 2
            while candidate.exists():
                candidate = destination.with_name(f"{destination.stem} ({counter}){destination.suffix}")
                counter += 1
            shutil.move(str(source), str(candidate))
            self.log(f"原文件已移入待删除区：{source} -> {candidate}")
            return True, f"原文件已移至 {candidate}"
        except Exception as exc:
            return False, f"移动原文件失败：{exc}"

    def _save_current_state(self, active: bool) -> None:
        if self.current_state_context is None:
            raise RuntimeError("当前没有可保存的任务上下文")
        sources, output_dir, suffix, recursive, move_original = self.current_state_context
        self._save_state(sources, output_dir, suffix, recursive, move_original, active)

    def _save_state(
        self,
        sources: list[InputSource],
        output_dir: Path,
        suffix: str,
        recursive: bool,
        move_original: bool,
        active: bool,
    ) -> None:
        data = {
            "version": STATE_SCHEMA_VERSION,
            "active": active,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "sources": [asdict(item) for item in sources],
            "output_dir": str(output_dir),
            "suffix": suffix,
            "recursive": recursive,
            "move_original": move_original,
            "policy_version": ENCODING_POLICY_VERSION,
            "no_gain_records": self.no_gain_records,
            "tasks": [task.to_json() for task in self.tasks],
        }
        atomic_write_json(STATE_PATH, data)

    def _offer_recovery(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            self.logger.exception("读取恢复状态失败")
            return
        if (
            not isinstance(data, dict)
            or data.get("version") != STATE_SCHEMA_VERSION
            or not data.get("active")
        ):
            return

        if not messagebox.askyesno(
            "恢复上次任务",
            "检测到上次未完成的任务。是否恢复输入、输出和设置？\n\n恢复后请点击“开始压制”继续。",
        ):
            data["active"] = False
            try:
                atomic_write_json(STATE_PATH, data)
            except OSError:
                self.logger.exception("关闭恢复标记失败")
            return

        self._clear_sources()
        for raw in data.get("sources") or []:
            try:
                source = InputSource(path=str(raw["path"]), kind=str(raw["kind"]))
                if Path(source.path).exists():
                    self._add_source(source)
            except Exception:
                continue
        self.output_var.set(str(data.get("output_dir", "")))
        self.suffix_var.set(str(data.get("suffix", "_AV1")))
        self.recursive_var.set(bool(data.get("recursive", True)))
        self.move_original_var.set(bool(data.get("move_original", True)))
        self.log("已恢复上次输入、输出和原文件处理设置；点击“开始压制”即可重新扫描并继续。")

    # ---------- GUI 事件队列 ----------

    def _insert_task_ui(self, task: Task) -> None:
        self.ui_queue.put(("insert_task", task))

    def _update_task_ui(self, task: Task) -> None:
        self.ui_queue.put(("update_task", task))

    def _update_encoding_progress(
        self,
        task: Task,
        percent: float,
        speed: float,
        current_eta: Optional[float],
    ) -> None:
        self.ui_queue.put(("encoding_progress", (task, percent, speed, current_eta)))

    def _update_overall_progress(
        self,
        current_fraction: float,
        speed: float,
        current_eta: Optional[float],
    ) -> None:
        current_weight = 0.0
        current_task = next((t for t in self.tasks if t.status == "处理中"), None)
        if current_task:
            current_weight = max(current_task.weight, 1.0) * current_fraction
        total_fraction = (self.completed_weight + current_weight) / max(self.total_weight, 1.0)
        elapsed = time.monotonic() - self.batch_started_at if self.batch_started_at else 0.0
        overall_eta = elapsed * (1.0 - total_fraction) / total_fraction if total_fraction > 0 else None
        self.ui_queue.put((
            "overall_progress",
            (total_fraction, self.completed_count, len(self.tasks), overall_eta, speed, current_eta),
        ))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log_text.configure(state=tk.NORMAL)
                    self.log_text.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {payload}\n")
                    self.log_text.see(tk.END)
                    self.log_text.configure(state=tk.DISABLED)
                elif kind == "current_text":
                    self.current_text.set(str(payload))
                elif kind == "insert_task":
                    task: Task = payload
                    audio_text = self._audio_summary(task)
                    item = self.task_tree.insert(
                        "",
                        tk.END,
                        text=Path(task.input_path).name,
                        values=(
                            f"{task.width}×{task.height}",
                            f"CRF {task.crf} / P5",
                            audio_text,
                            task.status,
                        ),
                    )
                    self.tree_items[task.input_path] = item
                elif kind == "update_task":
                    task = payload
                    item = self.tree_items.get(task.input_path)
                    if item:
                        status = task.status
                        if task.message:
                            status = f"{status}：{task.message}"
                        self.task_tree.set(item, "status", status)
                        self.task_tree.see(item)
                elif kind == "encoding_progress":
                    task, percent, speed, current_eta = payload
                    self.current_progress["value"] = percent * 100
                    self.current_text.set(
                        f"{Path(task.input_path).name} | {percent * 100:.1f}% | "
                        f"速度 {speed:.2f}x | 剩余 {format_duration(current_eta)}"
                    )
                    self._update_overall_progress(percent, speed, current_eta)
                elif kind == "overall_progress":
                    total_fraction, completed, total, overall_eta, speed, current_eta = payload
                    self.overall_progress["value"] = total_fraction * 100
                    self.overall_text.set(
                        f"{completed} / {total} | {total_fraction * 100:.1f}% | "
                        f"预计剩余 {format_duration(overall_eta)}"
                    )
                elif kind == "finished":
                    self._set_idle()
                    self.current_progress["value"] = 100
                    self.overall_progress["value"] = 100
                    self.current_text.set(str(payload))
                    messagebox.showinfo("完成", str(payload))
                elif kind == "stopped":
                    self._set_idle()
                    self.current_text.set(str(payload))
                elif kind == "failed":
                    self._set_idle()
                    self.current_text.set("任务中止")
                    messagebox.showerror("任务中止", str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    def _audio_summary(self, task: Task) -> str:
        if not task.audio_plans:
            return "无音频"
        values = []
        for plan in task.audio_plans:
            values.append("复制" if plan.action == "copy" else f"{plan.target_kbps}k")
        return "/".join(values)

    def _set_idle(self) -> None:
        self.running = False
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.worker = None

    def _safe_unlink(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            self.logger.exception("删除文件失败：%s", path)

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("退出", "当前任务仍在运行。确定停止并退出吗？"):
                return
            self.stop_event.set()
            self._terminate_current_process()
        self.logger.info("程序关闭")
        self.root.destroy()


# ----------------------------- 启动入口 -----------------------------

def show_startup_message(kind: str, title: str, text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    if kind == "info":
        messagebox.showinfo(title, text, parent=root)
    else:
        messagebox.showerror(title, text, parent=root)
    root.destroy()


def main() -> int:
    if not CONFIG_PATH.exists():
        try:
            generate_config_template()
        except OSError as exc:
            show_startup_message("error", "配置生成失败", f"无法在脚本目录生成配置文件：\n{exc}")
            return 1
        show_startup_message(
            "info",
            "首次运行",
            f"已在脚本目录生成配置模板：\n{CONFIG_PATH}\n\n"
            "请填写 ffmpeg_path 和 ffprobe_path 后重新运行。",
        )
        return 0

    try:
        config = load_config()
    except ValueError as exc:
        show_startup_message("error", "配置错误", f"{exc}\n\n配置文件：\n{CONFIG_PATH}")
        return 1

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names() and os.name == "nt":
            style.theme_use("vista")
    except tk.TclError:
        pass
    try:
        AV1CompressorApp(root, config)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("启动失败", f"程序初始化失败：\n{exc}", parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
