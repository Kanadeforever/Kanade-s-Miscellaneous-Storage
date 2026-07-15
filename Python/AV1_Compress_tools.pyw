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
- 队列状态恢复、无压缩收益持久记录、快速内容指纹、同目录滚动日志
- 无压缩收益时可将源文件移入“_无压缩收益”，并生成说明
- 成功且体积变小时可将原文件移入“_待删除原文件”
- FFmpeg 发生 0xC0000005 访问冲突时，在临时工作目录无损重封装后重试一次
- 编码或验证确认为失败时可将源文件移入“_压制失败”，并生成失败说明
- 首次运行自动生成同名 JSON 配置模板

依赖：Python 3.10+（通常自带 tkinter）、FFmpeg、ffprobe。

维护说明：
1. 本脚本只有一个主文件，故意不拆成多个模块，方便用户直接复制、移动或打包。
2. 同名 JSON 是“用户配置文件”，保存 FFmpeg 路径和软件设置；
   同名 .state.json 是“运行状态文件”，保存恢复队列、无收益记录等。
   这两个文件的职责不能混在一起，否则恢复任务和用户偏好会互相污染。
3. GUI 必须只在 Tk 主线程里更新；后台线程通过 ui_queue 发送消息。
   不要在工作线程中直接操作 Treeview、Label、Progressbar，否则在 Windows 上容易随机崩溃。
4. FFmpeg 命令必须始终使用 list[str] 传给 subprocess。
   不要拼接成一个字符串，也不要 shell=True；这样才能可靠支持中文、日文、空格、括号和特殊符号路径。
5. 所有正式输出都先写入 .__processing__.mkv 临时文件。
   只有 FFmpeg 返回成功、ffprobe 验证通过、且体积判断完成后，才会原子替换为正式文件。
6. “成功移动原文件”“失败移动原文件”“无收益移动原文件”移动的都是源视频，不移动有效成品。
   这样用户可以清晰看到：成功、失败、无收益三类源文件分别去了哪里。
7. 自动无损重封装和收益预估都会产生中间文件；这些文件必须放在 _压制临时文件 或用户自定义临时目录，
   不能放最终输出目录，避免用户误以为它们是成品。
8. CFR 时间戳规范化只作为失败回退，不作为默认编码路径；默认仍保持 passthrough，最大限度保留原时间轴。
9. 如果修改默认 CRF、音频策略、输出验证规则或会影响成品体积/结构的逻辑，应该同步提升 ENCODING_POLICY_VERSION，
   这样旧的“无收益记录”不会错误地阻止新策略重新处理。
"""

from __future__ import annotations

import hashlib
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
from dataclasses import asdict, dataclass, field, replace
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
CONFIG_SCHEMA_VERSION = 1
ENCODING_POLICY_VERSION = "av1-svt-p5-pixel-crf-opus-128-192-v2-old-output-scan"
QUICK_FINGERPRINT_VERSION = "sha256-sparse-v1"
QUICK_FINGERPRINT_CHUNK_SIZE = 256 * 1024
QUICK_FINGERPRINT_SAMPLE_COUNT = 5
PENDING_DELETE_DIRNAME = "_待删除原文件"
FAILED_DIRNAME = "_压制失败"
NO_GAIN_DIRNAME = "_无压缩收益"
TEMP_WORK_DIRNAME = "_压制临时文件"

# DEFAULT_SETTINGS 是“可长期保存的用户偏好”。
# 这些值会写入同名 JSON，例如 AV1_Compress_tool.json。
# 注意：不要把正在处理的文件列表、进度、无收益历史等写到这里；那些属于 STATE_PATH。
# 设计成两份文件的原因：
# - 配置文件：用户可以手动编辑，跨任务长期存在；
# - 状态文件：程序自动维护，用于恢复和跳过，内容随任务变化。
DEFAULT_SETTINGS = {
    # 软件设置保存在同名 JSON 中；state.json 只负责中断恢复和运行状态。
    "output_suffix": "_AV1",
    "recursive": True,
    "move_original_on_success": True,
    "move_failed_on_failure": True,
    "move_no_gain": True,
    "temp_dir": "",
    "precheck_enabled": False,
    "precheck_min_saving_percent": 8.0,
}

CONFIG_TEMPLATE = {
    "version": CONFIG_SCHEMA_VERSION,
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "settings": DEFAULT_SETTINGS,
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
    r"\b(error|invalid|corrupt|failed|non[- ]monoton(?:ous|ically)|decode_slice_header)\b",
    re.IGNORECASE,
)
BENIGN_TIMESTAMP_PATTERNS = (
    re.compile(r"application provided invalid,?\s*non[- ]monotonically increasing dts to muxer", re.IGNORECASE),
    re.compile(r"non[- ]monoton(?:ous|ically).*dts", re.IGNORECASE),
    re.compile(r"timestamps are unset in a packet", re.IGNORECASE),
    re.compile(r"invalid dts.*pts", re.IGNORECASE),
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------- 代码阅读索引 -----------------------------
#
# 这个脚本经历了多轮功能增强，已经不再只是“调用 FFmpeg 的小按钮”。
# 为了以后排查问题方便，这里先给出各部分的阅读顺序：
#
# 1. 常量区：
#    - CONFIG_PATH / STATE_PATH / LOG_PATH 决定三个持久文件的位置；
#    - ENCODING_POLICY_VERSION 决定无收益记录是否仍然可信；
#    - *_DIRNAME 决定源文件归档目录名称；
#    - DEFAULT_SETTINGS 决定同名 JSON 的默认软件设置。
#
# 2. 数据结构区：
#    - InputSource：用户添加的“文件”或“文件夹”；
#    - AudioPlan：某条音轨最终复制还是转 Opus；
#    - Task：一个待压制视频的完整分析结果，也是任务列表里的核心对象。
#
# 3. 通用辅助函数区：
#    - 路径、时间、字节数、帧率、返回码、错误摘要等都放在这里；
#    - 这些函数不依赖 GUI，方便以后单独测试。
#
# 4. AV1CompressorApp：
#    - __init__ 只初始化状态；
#    - _build_ui 创建主界面；
#    - _open_optional_features 创建“可选功能”窗口；
#    - _worker_main 是后台任务主循环；
#    - _scan_tasks 负责扫描、ffprobe 和生成 Task；
#    - _process_task 负责一个文件从“检查旧成品”到“最终归档”的完整生命周期。
#
# 5. 故障回退顺序：
#    - 首次正常编码：尽量保留原始时间戳 passthrough；
#    - 访问冲突 0xC0000005：先无损重封装，修复容器/PTS 问题；
#    - 仍失败且可判断固定帧率：尝试 CFR 时间戳规范化；
#    - 仍失败：写失败说明并按设置移入 _压制失败。
#
# 6. 收益预估顺序：
#    - 在多个位置截原始样本（-c copy）和 AV1 样本；
#    - 比较两者“每秒大小”，而不是只看 AV1 样本大小；
#    - 预计节省不足阈值时写无收益记录，按设置移入 _无压缩收益；
#    - 预估失败不会让任务失败，只会继续完整压制。
#
# 7. 不要轻易改动的地方：
#    - subprocess 必须用 list[str]，不要 shell=True；
#    - Tk 控件只能由主线程更新；
#    - 输出文件必须先写 .__processing__.mkv；
#    - 移动源文件前必须确保状态已经写入或成品已验证；
#    - 改编码策略要更新 ENCODING_POLICY_VERSION。


# ----------------------------- 数据结构 -----------------------------

# 用户在 GUI 里添加的入口。
# kind="file" 表示单个文件；kind="dir" 表示文件夹。
# 文件夹入口会作为相对路径根，用于保持目录结构和归档源文件。
@dataclass
class InputSource:
    path: str
    kind: str  # "file" 或 "dir"


# 每一条音频流都会生成一个 AudioPlan。
# 它把“探测到的源音频信息”和“最终怎么处理”放在一起，
# 后面生成 FFmpeg 命令、验证输出、写状态记录都会复用这份计划。
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


# Task 是本工具最核心的数据结构：一个 Task 对应一个源视频。
# 扫描阶段会把 ffprobe 的结果、自动 CRF、音频策略、字幕/附件列表、
# 源文件指纹、输出路径、恢复所需信息全部收集到这里。
# 后台编码线程只处理 Task，不再回头猜测源文件结构。
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
    cfr_fallback_rate: str = ""
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
    source_fingerprint: str = ""
    fingerprint_version: str = QUICK_FINGERPRINT_VERSION
    weight: float = 0.0
    status: str = "等待"
    message: str = ""
    output_size: int = 0
    failure_move_eligible: bool = False
    failure_stage: str = ""
    failure_return_code: Optional[int] = None
    failure_moved_to: str = ""
    failure_error: str = ""
    failure_ffmpeg_command: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# ----------------------------- 通用辅助函数 -----------------------------

def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def quick_content_fingerprint(path: Path, size: Optional[int] = None) -> str:
    """
    计算快速内容指纹。

    指纹包含文件字节数，并抽样读取开头、1/4、1/2、3/4、结尾附近的数据块。
    大文件通常只读取约 1.25 MiB；小文件会自动去重重叠采样位置。
    这不是完整文件哈希，但足以显著降低仅凭路径、大小和修改时间造成的误匹配。
    """
    file_size = path.stat().st_size if size is None else int(size)
    if file_size < 0:
        raise ValueError("文件大小无效")

    hasher = hashlib.sha256()
    hasher.update(QUICK_FINGERPRINT_VERSION.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(file_size.to_bytes(16, "big", signed=False))

    if file_size == 0:
        return hasher.hexdigest()

    chunk_size = min(QUICK_FINGERPRINT_CHUNK_SIZE, file_size)
    max_offset = max(file_size - chunk_size, 0)
    if QUICK_FINGERPRINT_SAMPLE_COUNT <= 1 or max_offset == 0:
        offsets = [0]
    else:
        offsets = sorted({
            (max_offset * index) // (QUICK_FINGERPRINT_SAMPLE_COUNT - 1)
            for index in range(QUICK_FINGERPRINT_SAMPLE_COUNT)
        })

    with path.open("rb") as handle:
        for offset in offsets:
            handle.seek(offset)
            block = handle.read(chunk_size)
            hasher.update(offset.to_bytes(16, "big", signed=False))
            hasher.update(len(block).to_bytes(8, "big", signed=False))
            hasher.update(block)
    return hasher.hexdigest()


def is_benign_timestamp_message(line: str) -> bool:
    return any(pattern.search(line) for pattern in BENIGN_TIMESTAMP_PATTERNS)


def clone_default_settings() -> dict[str, Any]:
    """返回一份默认软件设置副本，避免直接修改 DEFAULT_SETTINGS 常量。"""
    return json.loads(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False))


def normalize_settings(raw: Any) -> dict[str, Any]:
    """合并并校验同名 JSON 中的软件设置。

    这里的设置是用户偏好，例如后缀、是否移动源文件、是否启用收益预估。
    它们与 state.json 中的中断恢复记录分开管理；配置缺项时自动补默认值。
    """
    settings = clone_default_settings()
    if isinstance(raw, dict):
        for key in settings:
            if key in raw:
                settings[key] = raw[key]

    settings["output_suffix"] = str(settings.get("output_suffix", "_AV1"))
    settings["recursive"] = bool(settings.get("recursive", True))
    settings["move_original_on_success"] = bool(settings.get("move_original_on_success", True))
    settings["move_failed_on_failure"] = bool(settings.get("move_failed_on_failure", True))
    settings["move_no_gain"] = bool(settings.get("move_no_gain", True))
    settings["temp_dir"] = str(settings.get("temp_dir", ""))
    settings["precheck_enabled"] = bool(settings.get("precheck_enabled", False))

    try:
        threshold = float(settings.get("precheck_min_saving_percent", 8.0))
    except (TypeError, ValueError):
        threshold = 8.0
    settings["precheck_min_saving_percent"] = min(max(threshold, 1.0), 50.0)
    return settings


def save_config(settings: dict[str, Any], ffmpeg_path: str, ffprobe_path: str) -> None:
    """把用户偏好写回同名 JSON。

    只写软件配置，不写任务队列、无收益记录、失败记录等运行状态；那些属于 state.json。
    """
    atomic_write_json(CONFIG_PATH, {
        "version": CONFIG_SCHEMA_VERSION,
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "settings": normalize_settings(settings),
    })


def generate_config_template() -> None:
    atomic_write_json(CONFIG_PATH, CONFIG_TEMPLATE)


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取配置文件：{exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    ffmpeg_raw = data.get("ffmpeg_path", "")
    ffprobe_raw = data.get("ffprobe_path", "")
    if not isinstance(ffmpeg_raw, str) or not isinstance(ffprobe_raw, str):
        raise ValueError("ffmpeg_path 和 ffprobe_path 必须是字符串。")
    if not ffmpeg_raw.strip() or not ffprobe_raw.strip():
        raise ValueError("请先在同名 JSON 中填写 ffmpeg_path 和 ffprobe_path。")

    settings = normalize_settings(data.get("settings"))
    # 自动补齐新版本增加的配置键。补写失败不影响启动，只写日志前无法记录。
    normalized = {
        "version": CONFIG_SCHEMA_VERSION,
        "ffmpeg_path": ffmpeg_raw.strip(),
        "ffprobe_path": ffprobe_raw.strip(),
        "settings": settings,
    }
    try:
        if data != normalized:
            atomic_write_json(CONFIG_PATH, normalized)
    except OSError:
        pass

    return {
        "ffmpeg_path": str(resolve_dependency_path(ffmpeg_raw.strip())),
        "ffprobe_path": str(resolve_dependency_path(ffprobe_raw.strip())),
        "raw_ffmpeg_path": ffmpeg_raw.strip(),
        "raw_ffprobe_path": ffprobe_raw.strip(),
        "settings": settings,
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


def parse_rate_fraction(value: Any) -> Optional[Fraction]:
    """把 ffprobe 的 60/1、30000/1001 这类帧率解析为 Fraction。"""
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        fraction = Fraction(str(value))
        if fraction <= 0:
            return None
        return fraction
    except (ValueError, ZeroDivisionError):
        try:
            numeric = float(value)
            if numeric <= 0:
                return None
            return Fraction(numeric).limit_denominator(1001000)
        except (TypeError, ValueError, ZeroDivisionError):
            return None


def frame_rate_arg(rate: Fraction) -> str:
    """把 Fraction 转成 FFmpeg -r 参数；整数帧率用整数，小数帧率保留分数。"""
    if rate.denominator == 1:
        return str(rate.numerator)
    return f"{rate.numerator}/{rate.denominator}"


def detect_cfr_fallback_rate(video_stream: dict[str, Any]) -> str:
    """判断源视频是否适合在失败回退时做 CFR 时间戳规范化。

    正常路径仍使用 passthrough。只有编码/重封装重试失败后，才可能使用这里返回的帧率。
    """
    r_rate = parse_rate_fraction(video_stream.get("r_frame_rate"))
    avg_rate = parse_rate_fraction(video_stream.get("avg_frame_rate"))
    if r_rate is None or avg_rate is None:
        return ""
    r_value = float(r_rate)
    avg_value = float(avg_rate)
    if r_value <= 0 or avg_value <= 0 or avg_value > 240:
        return ""
    if abs(r_value - avg_value) / max(avg_value, 0.001) > 0.001:
        return ""

    # 常见固定帧率。非常规帧率不自动 CFR，避免误处理真正的 VFR 素材。
    common = [
        Fraction(24000, 1001), Fraction(24, 1), Fraction(25, 1),
        Fraction(30000, 1001), Fraction(30, 1), Fraction(50, 1),
        Fraction(60000, 1001), Fraction(60, 1), Fraction(120, 1),
    ]
    chosen = min(common, key=lambda item: abs(float(item) - avg_value))
    if abs(float(chosen) - avg_value) / avg_value <= 0.001:
        return frame_rate_arg(chosen)
    return ""


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


def format_process_return_code(return_code: int) -> str:
    unsigned = return_code & 0xFFFFFFFF
    if unsigned == 0xC0000005:
        return f"{return_code} / 0xC0000005（访问冲突）"
    return f"{return_code} / 0x{unsigned:08X}" if return_code != 0 else "0"


def summarize_ffmpeg_failure(return_code: int, stderr_lines: list[str]) -> str:
    unsigned = return_code & 0xFFFFFFFF
    if unsigned == 0xC0000005:
        return "FFmpeg 异常崩溃：0xC0000005（访问冲突）"

    meaningful: list[str] = []
    keywords = re.compile(
        r"(error|failed|invalid|cannot|could not|denied|no space|memory|malloc|abort|exception|corrupt)",
        re.IGNORECASE,
    )
    for line in stderr_lines:
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("svt[info]"):
            continue
        if keywords.search(stripped):
            meaningful.append(stripped)
    if meaningful:
        return f"FFmpeg 返回码 {format_process_return_code(return_code)}：" + " | ".join(meaningful[-8:])[-1500:]

    non_info = [
        line.strip() for line in stderr_lines
        if line.strip() and not line.strip().lower().startswith("svt[info]")
    ]
    if non_info:
        return f"FFmpeg 返回码 {format_process_return_code(return_code)}：" + " | ".join(non_info[-12:])[-1500:]
    return f"FFmpeg 异常退出，返回码 {format_process_return_code(return_code)}；未输出明确错误信息"


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
    def __init__(self, root: tk.Tk, config: dict[str, Any]) -> None:
        self.root = root
        self.ffmpeg = config["ffmpeg_path"]
        self.ffprobe = config["ffprobe_path"]
        self.raw_ffmpeg_path = config.get("raw_ffmpeg_path", config["ffmpeg_path"])
        self.raw_ffprobe_path = config.get("raw_ffprobe_path", config["ffprobe_path"])
        self.settings = normalize_settings(config.get("settings"))

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
        self.move_failed_enabled = True
        self.move_no_gain_enabled = True
        self.precheck_enabled = bool(self.settings.get("precheck_enabled", False))
        self.precheck_threshold = float(self.settings.get("precheck_min_saving_percent", 8.0))
        self.custom_temp_root: Optional[Path] = None
        self.options_window: Optional[tk.Toplevel] = None
        self.current_state_context: Optional[tuple[list[InputSource], Path, str, bool, bool, bool, bool, str, bool]] = None
        self.existing_output_files: list[Path] = []
        self.existing_output_name_index: dict[str, list[Path]] = {}
        self.existing_output_probe_cache: dict[str, dict[str, Any]] = {}
        self.existing_output_fingerprint_index: dict[str, list[Path]] = {}
        self.existing_output_fingerprint_index_built = False
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

    def _current_settings(self) -> dict[str, Any]:
        """从 GUI 变量收集当前软件设置。"""
        return normalize_settings({
            "output_suffix": self.suffix_var.get() if hasattr(self, "suffix_var") else self.settings.get("output_suffix", "_AV1"),
            "recursive": self.recursive_var.get() if hasattr(self, "recursive_var") else self.settings.get("recursive", True),
            "move_original_on_success": self.move_original_var.get() if hasattr(self, "move_original_var") else self.settings.get("move_original_on_success", True),
            "move_failed_on_failure": self.move_failed_var.get() if hasattr(self, "move_failed_var") else self.settings.get("move_failed_on_failure", True),
            "move_no_gain": self.move_no_gain_var.get() if hasattr(self, "move_no_gain_var") else self.settings.get("move_no_gain", True),
            "temp_dir": self.temp_dir_var.get() if hasattr(self, "temp_dir_var") else self.settings.get("temp_dir", ""),
            "precheck_enabled": self.precheck_var.get() if hasattr(self, "precheck_var") else self.settings.get("precheck_enabled", False),
            "precheck_min_saving_percent": self.settings.get("precheck_min_saving_percent", 8.0),
        })

    def _persist_settings(self) -> None:
        """把软件偏好保存到同名 JSON；失败只记录日志，不影响当前压制。"""
        self.settings = self._current_settings()
        try:
            save_config(self.settings, self.raw_ffmpeg_path, self.raw_ffprobe_path)
        except Exception:
            self.logger.exception("保存配置文件失败：%s", CONFIG_PATH)

    # ---------- GUI ----------

    def _build_ui(self) -> None:
        self.root.title(APP_STEM)
        self.root.geometry("780x920")
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

        # 次要设置保留原变量和状态格式，仅从主界面移入“可选功能”窗口。
        self.suffix_var = tk.StringVar(value=str(self.settings.get("output_suffix", "_AV1")))
        self.recursive_var = tk.BooleanVar(value=bool(self.settings.get("recursive", True)))
        self.move_original_var = tk.BooleanVar(value=bool(self.settings.get("move_original_on_success", True)))
        self.move_failed_var = tk.BooleanVar(value=bool(self.settings.get("move_failed_on_failure", True)))
        self.move_no_gain_var = tk.BooleanVar(value=bool(self.settings.get("move_no_gain", True)))
        self.temp_dir_var = tk.StringVar(value=str(self.settings.get("temp_dir", "")))
        self.precheck_var = tk.BooleanVar(value=bool(self.settings.get("precheck_enabled", False)))
        self.optional_button = ttk.Button(
            output_frame,
            text="可选功能",
            command=self._open_optional_features,
        )
        self.optional_button.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

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
        self.task_tree.column("#0", width=330, minwidth=180, stretch=False)
        self.task_tree.column("resolution", width=105, anchor=tk.CENTER, stretch=False)
        self.task_tree.column("quality", width=115, anchor=tk.CENTER, stretch=False)
        self.task_tree.column("audio", width=82, minwidth=70, anchor=tk.CENTER, stretch=False)
        self.task_tree.column("status", width=520, minwidth=220, anchor=tk.W, stretch=False)
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(task_frame, orient=tk.VERTICAL, command=self.task_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        tree_xscroll = ttk.Scrollbar(task_frame, orient=tk.HORIZONTAL, command=self.task_tree.xview)
        tree_xscroll.grid(row=1, column=0, sticky="ew")
        self.task_tree.configure(yscrollcommand=tree_scroll.set, xscrollcommand=tree_xscroll.set)

        progress_frame = ttk.LabelFrame(main, text="进度", padding=8)
        progress_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        progress_frame.columnconfigure(1, weight=1)

        ttk.Label(progress_frame, text="当前文件：").grid(row=0, column=0, sticky="nw")
        self.current_text = tk.StringVar(value="等待开始")
        self.current_label = ttk.Label(progress_frame, textvariable=self.current_text, wraplength=620, justify=tk.LEFT)
        self.current_label.grid(row=0, column=1, sticky="ew")
        ttk.Label(progress_frame, text="详细信息：").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.current_detail_text = tk.StringVar(value="")
        self.current_detail_label = ttk.Label(progress_frame, textvariable=self.current_detail_text, wraplength=620, justify=tk.LEFT)
        self.current_detail_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))
        self.current_progress = ttk.Progressbar(progress_frame, maximum=100)
        self.current_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 8))

        ttk.Label(progress_frame, text="全部任务：").grid(row=3, column=0, sticky="w")
        self.overall_text = tk.StringVar(value="0 / 0")
        ttk.Label(progress_frame, textvariable=self.overall_text).grid(row=3, column=1, sticky="w")
        self.overall_progress = ttk.Progressbar(progress_frame, maximum=100)
        self.overall_progress.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        def _sync_progress_wraplength(event: tk.Event) -> None:
            width = max(int(event.width) - 120, 260)
            self.current_label.configure(wraplength=width)
            self.current_detail_label.configure(wraplength=width)

        progress_frame.bind("<Configure>", _sync_progress_wraplength)

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

    def _open_optional_features(self) -> None:
        if self.options_window is not None and self.options_window.winfo_exists():
            self.options_window.deiconify()
            self.options_window.lift()
            self.options_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.options_window = window
        window.title("可选功能")
        window.transient(self.root)
        window.resizable(False, False)

        body = ttk.Frame(window, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="文件名后缀：").grid(row=0, column=0, sticky="w", padx=(0, 8))
        suffix_entry = ttk.Entry(body, textvariable=self.suffix_var, width=28)
        suffix_entry.grid(row=0, column=1, sticky="ew")

        ttk.Checkbutton(
            body,
            text=f"成功且体积变小后，将原视频移入输入根目录的“{PENDING_DELETE_DIRNAME}”",
            variable=self.move_original_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 0))

        ttk.Checkbutton(
            body,
            text=f"编码或验证失败后，将源视频移入输入根目录的“{FAILED_DIRNAME}”",
            variable=self.move_failed_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            body,
            text=f"无压缩收益后，将源视频移入输入根目录的“{NO_GAIN_DIRNAME}”",
            variable=self.move_no_gain_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            body,
            text="扫描子文件夹并保持目录结构",
            variable=self.recursive_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            body,
            text="压制前进行收益预估；预计节省不足配置阈值时归入“_无压缩收益”",
            variable=self.precheck_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        temp_frame = ttk.LabelFrame(body, text="临时工作目录", padding=8)
        temp_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        temp_frame.columnconfigure(0, weight=1)
        ttk.Entry(temp_frame, textvariable=self.temp_dir_var, width=54).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(temp_frame, text="选择", command=self._choose_temp_dir).grid(row=0, column=1)
        ttk.Label(
            temp_frame,
            text=f"留空=使用输入根目录下的 {TEMP_WORK_DIRNAME}；自动重封装中间文件会在完成后清理。",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        button_row = ttk.Frame(body)
        button_row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))

        def close_window() -> None:
            self._persist_settings()
            try:
                window.grab_release()
            except tk.TclError:
                pass
            self.options_window = None
            window.destroy()

        ttk.Button(button_row, text="完成", command=close_window).pack()
        window.protocol("WM_DELETE_WINDOW", close_window)
        window.update_idletasks()
        width = max(680, window.winfo_reqwidth())
        height = max(390, window.winfo_reqheight())
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - width) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - height) // 3, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.grab_set()
        suffix_entry.focus_set()

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

    def _choose_temp_dir(self) -> None:
        value = filedialog.askdirectory(title="选择临时工作目录")
        if value:
            self.temp_dir_var.set(str(Path(value).resolve()))

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
        self.optional_button.configure(state=tk.DISABLED)
        self.current_progress["value"] = 0
        self.overall_progress["value"] = 0
        self.current_text.set("正在检查环境……")
        self.current_detail_text.set("")
        self.overall_text.set("准备任务")
        self.task_tree.delete(*self.task_tree.get_children())
        self.tree_items.clear()

        self._persist_settings()
        temp_dir_text = self.temp_dir_var.get().strip()
        if temp_dir_text:
            try:
                temp_dir = self._resolve_temp_dir_text(temp_dir_text)
                temp_dir.mkdir(parents=True, exist_ok=True)
                test_file = temp_dir / f".__{APP_STEM}_temp_write_test_{os.getpid()}"
                test_file.write_bytes(b"ok")
                test_file.unlink()
            except OSError as exc:
                messagebox.showerror("临时目录错误", f"临时工作目录不可写：\n{temp_dir_text}\n\n{exc}")
                return

        sources = [InputSource(item.path, item.kind) for item in self.sources]
        recursive = bool(self.recursive_var.get())
        move_original = bool(self.move_original_var.get())
        move_failed = bool(self.move_failed_var.get())
        move_no_gain = bool(self.move_no_gain_var.get())
        precheck_enabled = bool(self.precheck_var.get())
        self.worker = threading.Thread(
            target=self._worker_main,
            args=(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled),
            daemon=True,
        )
        self.worker.start()

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.log("收到停止请求，正在结束当前 FFmpeg 进程……", logging.WARNING)
        self.current_text.set("正在停止……")
        self.current_detail_text.set("")
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
        move_failed: bool,
        move_no_gain: bool,
        temp_dir_text: str,
        precheck_enabled: bool,
    ) -> None:
        self.move_original_enabled = move_original
        self.move_failed_enabled = move_failed
        self.move_no_gain_enabled = move_no_gain
        self.precheck_enabled = precheck_enabled
        self.custom_temp_root = self._resolve_temp_dir_text(temp_dir_text) if temp_dir_text.strip() else None
        self.current_state_context = (sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled)
        try:
            self._startup_checks(output_dir)
            self._cleanup_temp_work_dirs(sources)
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
            self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True)

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
                self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True)

                result_status, message = self._process_task(task)
                task.status = result_status
                task.message = message

                if (
                    result_status == "失败"
                    and self.move_failed_enabled
                    and task.failure_move_eligible
                    and not self.stop_event.is_set()
                ):
                    # 先保存失败状态，再移动源文件；异常退出时仍有可追踪记录。
                    self._save_state(
                        sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True
                    )
                    moved, move_detail = self._move_original_to_failed(task)
                    if moved:
                        task.message = f"{task.message}；{move_detail}"
                    else:
                        self.log(f"失败已记录，但移动源文件失败：{move_detail}", logging.WARNING)

                self.completed_weight += max(task.weight, 1.0)
                self.completed_count += 1
                self._update_task_ui(task)
                self._update_overall_progress(0.0, 0.0, None)
                self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True)

            self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=False)
            success = sum(1 for t in tasks if t.status == "完成")
            skipped = sum(1 for t in tasks if t.status in {"跳过", "无收益"})
            failed = sum(1 for t in tasks if t.status == "失败")
            summary = f"全部结束：完成 {success}，跳过/无收益 {skipped}，失败 {failed}。"
            self.log(summary)
            self.ui_queue.put(("finished", summary))

        except InterruptedError:
            self.log("任务已停止；未完成队列会在下次启动时提供恢复。", logging.WARNING)
            try:
                self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True)
            except Exception:
                self.logger.exception("保存停止状态失败")
            self.ui_queue.put(("stopped", "任务已停止"))
        except Exception as exc:
            self.logger.exception("后台任务异常")
            self.log(f"任务中止：{exc}", logging.ERROR)
            try:
                self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active=True)
            except Exception:
                self.logger.exception("保存异常状态失败")
            self.ui_queue.put(("failed", str(exc)))

    # ---------- 临时工作目录 ----------

    def _resolve_temp_dir_text(self, value: str) -> Path:
        text = value.strip()
        if not text:
            raise ValueError("临时目录为空")
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        return path.resolve(strict=False)

    def _temp_root_for_source_path(self, source_root: Path) -> Path:
        if self.custom_temp_root is not None:
            return self.custom_temp_root
        return source_root.resolve(strict=False) / TEMP_WORK_DIRNAME

    def _cleanup_temp_work_dirs(self, sources: list[InputSource]) -> None:
        roots: set[Path] = set()
        if self.custom_temp_root is not None:
            roots.add(self.custom_temp_root)
        for source in sources:
            try:
                source_path = Path(source.path).resolve(strict=False)
                root = source_path.parent if source.kind == "file" else source_path
                roots.add(self._temp_root_for_source_path(root))
            except OSError:
                continue

        for root in roots:
            if not root.exists():
                continue
            try:
                temp_candidates = list(root.rglob("*.av1tooltmp.*")) + list(root.rglob("*.__remux_retry__.mkv"))
                for path in temp_candidates:
                    try:
                        path.unlink()
                        self.log(f"清理上次遗留临时文件：{path}", gui=False)
                    except OSError as exc:
                        self.log(f"无法清理重封装临时文件：{path}：{exc}", logging.WARNING)
                self._remove_empty_dirs_under(root, remove_root=(self.custom_temp_root is None and root.name == TEMP_WORK_DIRNAME))
            except OSError as exc:
                self.log(f"扫描临时工作目录失败：{root}：{exc}", logging.WARNING)

    def _remove_empty_dirs_under(self, root: Path, remove_root: bool = False) -> None:
        if not root.exists() or not root.is_dir():
            return
        try:
            dirs = [path for path in root.rglob("*") if path.is_dir()]
            for directory in sorted(dirs, key=lambda value: len(value.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if remove_root:
                try:
                    root.rmdir()
                except OSError:
                    pass
        except OSError:
            pass

    def _remux_retry_path_for_task(self, task: Task) -> Path:
        source_root = Path(task.source_root)
        temp_root = self._temp_root_for_source_path(source_root)
        try:
            relative = Path(task.source_relative)
        except Exception:
            relative = Path(Path(task.input_path).name)
        relative_parent = relative.parent if str(relative.parent) != "." else Path()
        stem = Path(task.input_path).stem
        fingerprint = (task.source_fingerprint or "nofp")[:12]
        name = f"{stem}.{fingerprint}.av1tooltmp.__remux_retry__.mkv"
        return temp_root / relative_parent / name

    def _prepare_remux_retry_path(self, task: Task, remux_path: Path) -> str:
        try:
            remux_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"无法创建临时工作目录：{exc}"

        try:
            free = shutil.disk_usage(remux_path.parent).free
        except OSError as exc:
            return f"无法检查临时工作目录剩余空间：{exc}"
        required = max(int(task.input_size * 1.10), task.input_size + 64 * 1024 * 1024)
        if free < required:
            return (
                f"临时工作目录空间不足：剩余 {format_bytes(free)}，"
                f"需要约 {format_bytes(required)}"
            )

        if remux_path.exists():
            try:
                remux_path.unlink()
            except OSError as exc:
                return f"无法删除遗留重封装临时文件：{exc}"
        return ""

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
                failed_root = source_resolved / FAILED_DIRNAME
                no_gain_root = source_resolved / NO_GAIN_DIRNAME
                temp_work_root = self._temp_root_for_source_path(source_resolved)
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
                if source.kind == "dir" and (
                    is_relative_to(resolved, pending_delete_root)
                    or is_relative_to(resolved, failed_root)
                    or is_relative_to(resolved, no_gain_root)
                    or is_relative_to(resolved, temp_work_root)
                ):
                    continue
                if ".__processing__" in resolved.name or ".__remux_retry__" in resolved.name or ".av1tooltmp." in resolved.name:
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
        self.existing_output_probe_cache.clear()
        self.existing_output_fingerprint_index.clear()
        self.existing_output_fingerprint_index_built = False
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
                dirnames[:] = [
                    name for name in dirnames
                    if name not in {PENDING_DELETE_DIRNAME, FAILED_DIRNAME, NO_GAIN_DIRNAME, TEMP_WORK_DIRNAME}
                ]
                root_path = Path(current_root)
                for filename in filenames:
                    if Path(filename).suffix.lower() != ".mkv":
                        continue
                    path = root_path / filename
                    try:
                        if not path.is_file():
                            continue
                        lower_name = filename.casefold()
                        if (
                            ".__processing__.mkv" in lower_name
                            or ".__remux_retry__.mkv" in lower_name
                            or ".invalid_" in lower_name
                        ):
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
        cfr_fallback_rate = detect_cfr_fallback_rate(video)

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
        source_fingerprint = quick_content_fingerprint(path, stat.st_size)
        self.log(
            f"快速内容指纹：{path.name} -> {source_fingerprint[:16]}… ({QUICK_FINGERPRINT_VERSION})",
            gui=False,
        )
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
            cfr_fallback_rate=cfr_fallback_rate,
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
            source_fingerprint=source_fingerprint,
            fingerprint_version=QUICK_FINGERPRINT_VERSION,
            weight=weight,
        )

        no_gain_record = self._matching_no_gain_record(task)
        if no_gain_record is not None:
            task.status = "无收益"
            task.message = "已有无压缩收益记录，源文件和编码策略均未变化"
            reserved_outputs.add(os.path.normcase(str(base_final_path.resolve(strict=False))))
            self._cleanup_recorded_no_gain_output(task, no_gain_record)
            if self.move_no_gain_enabled:
                moved, move_detail = self._move_original_to_no_gain(task, parse_int(no_gain_record.get("output_size"), 0), "命中无收益记录")
                if moved:
                    task.message += f"；{move_detail}"
                else:
                    self.log(f"命中无收益记录，但移动源文件失败：{move_detail}", logging.WARNING)
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

    # ---------- 压制前收益预估 ----------

    def _precheck_sample_specs(self, task: Task) -> list[tuple[float, float]]:
        """根据视频时长决定收益预估的抽样位置。

        为什么不是只抽开头：
        - 开头常常是黑场、片头、Logo 或静态画面，码率代表性很差；
        - 结尾可能是字幕，也不能单独代表整片；
        - 25% / 50% / 75% 位置通常更接近正片内容。

        返回值是若干 (开始秒数, 抽样时长) 元组。短视频不预估，直接完整压制，
        因为抽样本身已经接近完整压制时间，收益不大。
        """
        duration = max(float(task.duration), 0.0)
        if duration <= 120:
            return []
        if duration <= 8 * 60:
            sample_len = 15.0
            ratios = (0.12, 0.50, 0.88)
        else:
            sample_len = 20.0
            ratios = (0.02, 0.25, 0.50, 0.75, 0.96)

        specs: list[tuple[float, float]] = []
        latest_start = max(duration - sample_len - 0.5, 0.0)
        used: set[int] = set()
        for ratio in ratios:
            start = min(max(duration * ratio, 0.0), latest_start)
            # 以 0.5 秒为粒度去重，避免短片中多个比例落到同一段。
            bucket = int(start * 2)
            if bucket in used:
                continue
            used.add(bucket)
            specs.append((start, min(sample_len, duration - start)))
        return specs

    def _precheck_sample_path(self, task: Task, sample_index: int, kind: str) -> Path:
        """生成收益预估临时样本路径。

        kind 只用于文件名区分：
        - orig：源片段，使用 -c copy，无损截取；
        - av1：同位置 AV1/Opus 样本，使用正式压制参数。

        文件名中包含快速指纹前缀，能减少不同同名视频同时处理时的冲突。
        所有样本都放在 _压制临时文件 或用户指定的临时目录，完成后会清理。
        """
        source_root = Path(task.source_root)
        temp_root = self._temp_root_for_source_path(source_root)
        try:
            relative = Path(task.source_relative)
            relative_parent = relative.parent if str(relative.parent) != "." else Path()
        except Exception:
            relative_parent = Path()
        stem = Path(task.input_path).stem
        fingerprint = (task.source_fingerprint or "nofp")[:12]
        name = f"{stem}.{fingerprint}.av1tooltmp.precheck{sample_index:02d}.{kind}.mkv"
        return temp_root / relative_parent / name

    def _build_precheck_original_sample_command(self, task: Task, start: float, length: float, sample_path: Path) -> list[str]:
        """构造“原始样本”命令。

        原始样本只做 stream copy，不重新编码，因此不会损失质量，速度也非常快。
        这里主要为了得到“同一时间段在源文件中本来占多大空间”。
        与 AV1 样本比较时，用“每秒字节数”而不是裸文件大小，
        这样即使 -c copy 因关键帧对齐造成片段稍长，也不会严重影响判断。
        """
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel", "warning",
            "-ss", f"{start:.3f}",
            "-t", f"{length:.3f}",
            "-i", task.input_path,
            "-map", f"0:{task.video_index}",
        ]
        for plan in task.audio_plans:
            command += ["-map", f"0:{plan.input_index}"]
        command += [
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-c", "copy",
            "-max_muxing_queue_size", "4096",
            "-f", "matroska",
            str(sample_path),
        ]
        return command

    def _build_precheck_av1_sample_command(self, task: Task, start: float, length: float, sample_path: Path) -> list[str]:
        """构造“AV1 样本”命令。

        这条命令使用与正式压制一致的核心策略：SVT-AV1 Preset 5、自动 CRF、10-bit、Opus 音频。
        不写入来源标签，也不映射字幕/附件，因为收益预估只关心音视频主体体积。
        预估失败不会让任务失败，只会回退到完整压制。
        """
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel", "warning",
            "-ss", f"{start:.3f}",
            "-t", f"{length:.3f}",
            "-i", task.input_path,
            "-map", f"0:{task.video_index}",
        ]
        for plan in task.audio_plans:
            command += ["-map", f"0:{plan.input_index}"]
        command += [
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-c:v:0", "libsvtav1",
            "-preset", "5",
            "-crf", str(task.crf),
            "-svtav1-params", "tune=0",
            "-pix_fmt", "yuv420p10le",
            "-fps_mode:v:0", "passthrough",
        ]
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
        command += [
            "-max_muxing_queue_size", "4096",
            "-f", "matroska",
            str(sample_path),
        ]
        return command

    def _probe_duration_or_default(self, path: Path, fallback: float) -> float:
        """读取样本实际时长；失败时回退到计划时长。

        收益预估使用“每秒大小”比较，因此最好知道样本实际时长。
        ffprobe 失败不值得中断主流程，此时使用 fallback 即可。
        """
        try:
            probe = self._probe(path)
            duration = parse_float((probe.get("format") or {}).get("duration"), 0.0)
            if duration > 0:
                return duration
        except Exception:
            pass
        return max(fallback, 0.001)

    def _estimate_no_gain_before_encoding(self, task: Task) -> tuple[bool, str, int]:
        """压制前收益预估。

        核心思想：不要只看“AV1 样本有多大”，而要比较“同一时间段的源样本”和“同一时间段的 AV1 样本”。
        这样可以自动考虑原文件在不同位置的码率分布，比简单按样本时长放大更准确。

        返回：
        - should_skip：True 表示预计收益不足，应直接归类到“_无压缩收益”；
        - detail：给日志和 GUI 使用的人类可读说明；
        - estimated_output_size：估算完整输出体积，用于写入无收益记录。
        """
        specs = self._precheck_sample_specs(task)
        if not specs:
            return False, "视频较短，跳过收益预估，直接完整压制", 0

        # 样本文件可能接近源文件片段大小，因此先检查临时目录空间。
        # 估算需要空间：源片段 + AV1片段 + 余量。这里按源文件 20% 与 512MiB 取较小上限，避免误拦截。
        first_sample = self._precheck_sample_path(task, 0, "spacecheck")
        first_sample.parent.mkdir(parents=True, exist_ok=True)
        try:
            free = shutil.disk_usage(first_sample.parent).free
        except OSError as exc:
            raise RuntimeError(f"无法检查收益预估临时目录空间：{exc}")
        planned_seconds = sum(length for _, length in specs)
        rough_need = int(max(task.input_size * min(planned_seconds / max(task.duration, 1.0), 0.20) * 2.2, 256 * 1024 * 1024))
        if free < rough_need:
            raise RuntimeError(f"收益预估临时目录空间不足：剩余 {format_bytes(free)}，估计需要 {format_bytes(rough_need)}")

        sample_paths: list[Path] = []
        input_bytes = 0
        input_seconds = 0.0
        av1_bytes = 0
        av1_seconds = 0.0
        segment_ratios: list[float] = []

        self.ui_queue.put(("current_text", f"收益预估：{Path(task.input_path).name}"))
        try:
            for index, (start, length) in enumerate(specs, start=1):
                if self.stop_event.is_set():
                    raise InterruptedError
                orig_path = self._precheck_sample_path(task, index, "orig")
                av1_path = self._precheck_sample_path(task, index, "av1")
                sample_paths.extend([orig_path, av1_path])
                orig_path.parent.mkdir(parents=True, exist_ok=True)
                self._safe_unlink(orig_path)
                self._safe_unlink(av1_path)

                # 1) 源样本：-c copy，只用于得到源文件同位置的实际体积。
                orig_cmd = self._build_precheck_original_sample_command(task, start, length, orig_path)
                self.log(f"收益预估源样本命令：{display_command(orig_cmd)}", gui=False)
                rc, stderr, _ = self._run_ffmpeg_capture(orig_cmd, f"收益预估源样本 {index} {task.input_path}")
                if rc != 0 or not orig_path.exists() or orig_path.stat().st_size <= 0:
                    raise RuntimeError(f"源样本 {index} 生成失败：{summarize_ffmpeg_failure(rc, stderr)}")

                # 2) AV1样本：使用正式核心编码策略，比较是否真的有体积收益。
                av1_cmd = self._build_precheck_av1_sample_command(task, start, length, av1_path)
                self.log(f"收益预估AV1样本命令：{display_command(av1_cmd)}", gui=False)
                rc, stderr, _ = self._run_ffmpeg_capture(av1_cmd, f"收益预估AV1样本 {index} {task.input_path}")
                if rc != 0 or not av1_path.exists() or av1_path.stat().st_size <= 0:
                    raise RuntimeError(f"AV1样本 {index} 生成失败：{summarize_ffmpeg_failure(rc, stderr)}")

                orig_duration = self._probe_duration_or_default(orig_path, length)
                av1_duration = self._probe_duration_or_default(av1_path, length)
                orig_size = orig_path.stat().st_size
                av1_size = av1_path.stat().st_size
                input_bytes += orig_size
                input_seconds += orig_duration
                av1_bytes += av1_size
                av1_seconds += av1_duration
                orig_bps = orig_size / max(orig_duration, 0.001)
                av1_bps = av1_size / max(av1_duration, 0.001)
                segment_ratio = av1_bps / max(orig_bps, 1.0)
                segment_ratios.append(segment_ratio)
                self.log(
                    f"收益预估样本 {index}/{len(specs)}：start={start:.1f}s，"
                    f"源 {format_bytes(orig_size)} / {orig_duration:.2f}s，"
                    f"AV1 {format_bytes(av1_size)} / {av1_duration:.2f}s，"
                    f"比例 {segment_ratio:.3f}",
                    gui=False,
                )

            input_rate = input_bytes / max(input_seconds, 0.001)
            av1_rate = av1_bytes / max(av1_seconds, 0.001)
            ratio = av1_rate / max(input_rate, 1.0)
            estimated_output = int(task.input_size * ratio)
            saving_percent = max(0.0, (1.0 - ratio) * 100.0)
            beneficial_segments = sum(1 for value in segment_ratios if value <= 0.95)
            majority = (len(segment_ratios) + 1) // 2

            # 只在收益明显不足时跳过，避免误伤“可能只是抽样偏保守”的视频。
            # 默认阈值 8%，即预计小于 8% 的节省直接归入无收益。
            should_skip = saving_percent < self.precheck_threshold
            # 如果总收益刚过线但大多数样本都没明显变小，也认为风险偏高，跳过更符合“省时间”的目的。
            if not should_skip and beneficial_segments < majority and saving_percent < self.precheck_threshold + 3.0:
                should_skip = True

            ratio_text = ", ".join(f"{value:.2f}" for value in segment_ratios)
            detail = (
                f"样本 {len(segment_ratios)} 段，预计节省 {saving_percent:.1f}% "
                f"（阈值 {self.precheck_threshold:.1f}%），"
                f"估算输出 {format_bytes(estimated_output)}，"
                f"分段比例 [{ratio_text}]"
            )
            return should_skip, detail, estimated_output
        finally:
            for path in sample_paths:
                self._safe_unlink(path)
            # 尽量清理空目录；失败也不影响正式任务。
            try:
                self._remove_empty_dirs_under(first_sample.parent, remove_root=False)
            except Exception:
                pass

    # ---------- 单任务编码 ----------

    def _failure_result(
        self,
        task: Task,
        message: str,
        *,
        stage: str,
        move_eligible: bool,
        return_code: Optional[int] = None,
        ffmpeg_command: str = "",
    ) -> tuple[str, str]:
        task.failure_move_eligible = move_eligible
        task.failure_stage = stage
        task.failure_return_code = return_code
        task.failure_error = message
        task.failure_ffmpeg_command = ffmpeg_command
        return "失败", message

    def _run_encoding_command(
        self,
        progress_task: Task,
        command: list[str],
        log_source: Path,
    ) -> tuple[int, list[str], float]:
        stderr_lines: list[str] = []
        start_time = time.monotonic()
        current_seconds = 0.0
        current_speed = 0.0

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
                    current_seconds = min(parse_ffmpeg_time(value), progress_task.duration)
                elif key == "speed":
                    current_speed = parse_float(value.rstrip("x"), 0.0)
                elif key == "progress":
                    percent = min(max(current_seconds / progress_task.duration, 0.0), 1.0)
                    current_eta = (
                        (progress_task.duration - current_seconds) / current_speed
                        if current_speed > 0 else None
                    )
                    self._update_encoding_progress(progress_task, percent, current_speed, current_eta)
                    progress_data.clear()

            return_code = process.wait()
            stderr_thread.join(timeout=2)
        finally:
            with self.process_lock:
                self.current_process = None

        stderr_text = "\n".join(stderr_lines)
        if stderr_text:
            self.logger.debug("FFmpeg stderr for %s:\n%s", log_source, stderr_text)
        return return_code, stderr_lines, time.monotonic() - start_time

    def _run_ffmpeg_capture(
        self,
        command: list[str],
        log_label: str,
    ) -> tuple[int, list[str], float]:
        stderr_lines: list[str] = []
        start_time = time.monotonic()
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_options(),
        )
        with self.process_lock:
            self.current_process = process

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
            return_code = process.returncode if process.returncode is not None else -1
        finally:
            with self.process_lock:
                self.current_process = None

        stderr_text = "\n".join(stderr_lines)
        if stderr_text:
            self.logger.debug("FFmpeg stderr for %s:\n%s", log_label, stderr_text)
        return return_code, stderr_lines, time.monotonic() - start_time

    def _build_remux_retry_command(self, task: Task, remux_path: Path) -> list[str]:
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel", "warning",
            "-fflags", "+genpts",
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
            "-c:v", "copy",
            "-c:a", "copy",
        ]
        for subtitle_output_index, codec in enumerate(task.subtitle_codecs):
            if codec in TEXT_SUBTITLE_TO_SRT:
                command += [f"-c:s:{subtitle_output_index}", "srt"]
            else:
                command += [f"-c:s:{subtitle_output_index}", "copy"]
        if task.attachment_indexes:
            command += ["-c:t", "copy"]
        command += [
            "-max_muxing_queue_size", "4096",
            "-f", "matroska",
            str(remux_path),
        ]
        return command

    def _task_for_remuxed_input(self, task: Task, remux_path: Path) -> Task:
        probe = self._probe(remux_path)
        streams = probe.get("streams") or []
        videos = [
            stream for stream in streams
            if stream.get("codec_type") == "video"
            and parse_int((stream.get("disposition") or {}).get("attached_pic"), 0) == 0
        ]
        audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
        subtitles = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
        attachments = [stream for stream in streams if stream.get("codec_type") == "attachment"]

        if len(videos) != 1:
            raise RuntimeError(f"重封装后主视频流数量异常：{len(videos)}")
        if len(audios) != len(task.audio_plans):
            raise RuntimeError(f"重封装后音轨数量变化：{len(task.audio_plans)} -> {len(audios)}")
        if len(subtitles) != len(task.subtitle_indexes):
            raise RuntimeError(f"重封装后字幕数量变化：{len(task.subtitle_indexes)} -> {len(subtitles)}")
        if len(attachments) != len(task.attachment_indexes):
            raise RuntimeError(f"重封装后附件数量变化：{len(task.attachment_indexes)} -> {len(attachments)}")
        chapter_count = len(probe.get("chapters") or [])
        if chapter_count != task.chapter_count:
            raise RuntimeError(f"重封装后章节数量变化：{task.chapter_count} -> {chapter_count}")

        video = videos[0]
        raw_width = parse_int(video.get("width"), 0)
        raw_height = parse_int(video.get("height"), 0)
        expected_width, expected_height = (
            (task.height, task.width) if task.rotation in {90, 270} else (task.width, task.height)
        )
        if (raw_width, raw_height) != (expected_width, expected_height):
            raise RuntimeError(
                f"重封装后分辨率变化：{expected_width}x{expected_height} -> {raw_width}x{raw_height}"
            )
        remux_duration = get_duration(probe, video)
        tolerance = max(2.0, task.duration * 0.005)
        if remux_duration <= 0 or abs(remux_duration - task.duration) > tolerance:
            raise RuntimeError(
                f"重封装后时长异常：{task.duration:.3f}s -> {remux_duration:.3f}s"
            )

        remapped_audio = [
            replace(plan, input_index=parse_int(stream.get("index")))
            for plan, stream in zip(task.audio_plans, audios)
        ]
        return replace(
            task,
            input_path=str(remux_path),
            video_index=parse_int(video.get("index")),
            audio_plans=remapped_audio,
            subtitle_indexes=[parse_int(stream.get("index")) for stream in subtitles],
            subtitle_codecs=[str(stream.get("codec_name", "unknown")).lower() for stream in subtitles],
            attachment_indexes=[parse_int(stream.get("index")) for stream in attachments],
            cover_indexes=[],
        )

    def _ffmpeg_error_is_environmental(self, stderr_lines: list[str]) -> bool:
        text = "\n".join(stderr_lines).lower()
        return any(token in text for token in (
            "no space left",
            "disk full",
            "permission denied",
            "access is denied",
            "read-only file system",
            "not enough space",
        ))

    def _process_task(self, task: Task) -> tuple[str, str]:
        task.failure_move_eligible = False
        task.failure_stage = ""
        task.failure_return_code = None
        task.failure_moved_to = ""
        task.failure_error = ""
        task.failure_ffmpeg_command = ""
        input_path = Path(task.input_path)
        final_path = Path(task.output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = final_path.with_name(f"{final_path.stem}.__processing__.mkv")
        remux_path = self._remux_retry_path_for_task(task)

        if not input_path.exists():
            return self._failure_result(task, "源文件不存在", stage="源文件检查", move_eligible=False)
        stat = input_path.stat()
        if stat.st_size != task.input_size or stat.st_mtime_ns != task.input_mtime_ns:
            return self._failure_result(task, "源文件在扫描后发生变化", stage="源文件检查", move_eligible=False)

        for stale_path, label in ((temp_path, "编码临时文件"), (remux_path, "重封装临时文件")):
            if stale_path.exists():
                try:
                    stale_path.unlink()
                    self.log(f"清理上次遗留{label}：{stale_path}")
                except OSError as exc:
                    return self._failure_result(
                        task,
                        f"无法删除遗留{label}：{exc}",
                        stage="临时文件清理",
                        move_eligible=False,
                    )

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
                return self._failure_result(task, f"已有无效输出无法改名：{exc}", stage="已有输出处理", move_eligible=False)

        legacy_path = self._find_reusable_old_output(task, final_path)
        if legacy_path is not None:
            return self._handle_reusable_output(task, legacy_path, "扫描发现的旧成品")

        if self.precheck_enabled:
            try:
                should_skip, estimate_detail, estimated_output = self._estimate_no_gain_before_encoding(task)
                self.log(f"收益预估结果：{input_path.name}：{estimate_detail}")
                if should_skip:
                    if not self._record_no_gain(task, max(estimated_output, 0), record_kind="precheck", detail=estimate_detail):
                        return self._failure_result(
                            task,
                            "收益预估判定无收益，但状态记录写入失败；为安全起见未移动源文件",
                            stage="收益预估状态写入",
                            move_eligible=False,
                        )
                    move_note = ""
                    if self.move_no_gain_enabled:
                        moved, move_detail = self._move_original_to_no_gain(task, max(estimated_output, 0), f"收益预估无压缩收益：{estimate_detail}")
                        move_note = f"；{move_detail}"
                        if not moved:
                            self.log(f"收益预估无收益已记录，但移动源文件失败：{move_detail}", logging.WARNING)
                    return "无收益", f"收益预估跳过：{estimate_detail}{move_note}"
            except InterruptedError:
                raise
            except Exception as exc:
                self.log(f"收益预估失败，继续完整压制：{input_path.name}：{exc}", logging.WARNING)

        free = shutil.disk_usage(final_path.parent).free
        minimum_free = min(task.input_size + 512 * 1024 * 1024, 8 * 1024 * 1024 * 1024)
        if free < minimum_free:
            return self._failure_result(task, f"磁盘空间不足：剩余 {format_bytes(free)}", stage="磁盘空间检查", move_eligible=False)

        total_start_time = time.monotonic()
        command = self._build_ffmpeg_command(task, temp_path)
        command_history: list[tuple[str, list[str]]] = [("首次编码", command)]
        self.log(f"开始编码：{input_path}")
        self.log(f"FFmpeg 命令：{display_command(command)}", gui=False)

        used_remux_retry = False
        # retry_source_task 指向“当前实际输入”。
        # 正常情况下它就是原 task；若自动无损重封装成功，它会指向重封装后的临时 MKV。
        # 后续 CFR 回退要基于当前实际输入构造命令，不能继续错误地使用已经崩溃的旧命令。
        retry_source_task: Optional[Task] = None
        try:
            try:
                return_code, stderr_lines, _ = self._run_encoding_command(task, command, input_path)
            except OSError as exc:
                return self._failure_result(task, f"无法启动 FFmpeg：{exc}", stage="启动 FFmpeg", move_eligible=False)

            if self.stop_event.is_set():
                self._safe_unlink(temp_path)
                raise InterruptedError

            if return_code != 0 and (return_code & 0xFFFFFFFF) == 0xC0000005:
                # 已确认这类崩溃可能由 MP4 时间戳/封装结构稳定触发。
                # 先以 stream copy + genpts 无损重封装，再用相同编码参数重试一次。
                self._safe_unlink(temp_path)
                used_remux_retry = True
                task.message = "检测到 FFmpeg 访问冲突，正在自动无损重封装后重试"
                self._update_task_ui(task)
                self.ui_queue.put(("current_text", f"自动修复封装：{input_path.name}"))
                self.log(
                    f"首次编码发生 0xC0000005：{input_path.name}；"
                    "开始无损重封装并自动重试一次。",
                    logging.WARNING,
                )

                remux_prepare_error = self._prepare_remux_retry_path(task, remux_path)
                if remux_prepare_error:
                    return self._failure_result(
                        task,
                        f"首次编码访问冲突，但无法自动无损重封装：{remux_prepare_error}",
                        stage="自动无损重封装准备",
                        move_eligible=False,
                        return_code=return_code,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

                remux_command = self._build_remux_retry_command(task, remux_path)
                command_history.append(("自动无损重封装", remux_command))
                self.log(f"自动重封装命令：{display_command(remux_command)}", gui=False)
                try:
                    remux_return_code, remux_stderr, _ = self._run_ffmpeg_capture(
                        remux_command,
                        f"自动重封装 {input_path}",
                    )
                except OSError as exc:
                    return self._failure_result(
                        task,
                        f"访问冲突后无法启动自动重封装：{exc}",
                        stage="自动无损重封装",
                        move_eligible=False,
                        return_code=return_code,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

                if remux_return_code != 0:
                    summary = summarize_ffmpeg_failure(remux_return_code, remux_stderr)
                    self.log(
                        f"自动无损重封装失败：{input_path.name}：{summary} | "
                        f"返回码 {format_process_return_code(remux_return_code)}",
                        logging.ERROR,
                    )
                    return self._failure_result(
                        task,
                        f"首次编码访问冲突，自动无损重封装失败：{summary}",
                        stage="自动无损重封装",
                        move_eligible=not self._ffmpeg_error_is_environmental(remux_stderr),
                        return_code=remux_return_code,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )
                if not remux_path.exists() or remux_path.stat().st_size <= 0:
                    return self._failure_result(
                        task,
                        "首次编码访问冲突，自动无损重封装未生成有效文件",
                        stage="自动无损重封装",
                        move_eligible=True,
                        return_code=remux_return_code,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

                try:
                    retry_task = self._task_for_remuxed_input(task, remux_path)
                    retry_source_task = retry_task
                except Exception as exc:
                    return self._failure_result(
                        task,
                        f"自动无损重封装完成，但结构验证失败：{exc}",
                        stage="自动无损重封装验证",
                        move_eligible=True,
                        return_code=remux_return_code,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

                retry_command = self._build_ffmpeg_command(retry_task, temp_path)
                command_history.append(("重封装后重试编码", retry_command))
                task.message = "无损重封装完成，正在重新编码"
                self._update_task_ui(task)
                self.ui_queue.put(("current_text", f"重封装后重试：{input_path.name}"))
                self.log(f"重封装成功，重新编码：{input_path.name}")
                self.log(f"重试 FFmpeg 命令：{display_command(retry_command)}", gui=False)
                try:
                    return_code, stderr_lines, _ = self._run_encoding_command(
                        task,
                        retry_command,
                        remux_path,
                    )
                except OSError as exc:
                    return self._failure_result(
                        task,
                        f"重封装后无法启动 FFmpeg：{exc}",
                        stage="重封装后重试编码",
                        move_eligible=False,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

            if self.stop_event.is_set():
                self._safe_unlink(temp_path)
                raise InterruptedError

            # 第二级失败回退：CFR 时间戳规范化。
            #
            # 背景：有些 MP4/MKV 表面显示为固定 60fps，但实际时间戳有起始偏移、舍入或边界问题。
            # 默认 passthrough 会尽量保留这些时间戳，个别文件会让 FFmpeg/SVT 在第一批帧上崩溃或卡住。
            # 如果 ffprobe 判断它是稳定固定帧率，就可以在失败后让 FFmpeg 重新按固定帧率生成输出时间轴。
            # 这可能造成极少量帧复制/丢弃，所以只作为失败回退，不用于普通成功路径。
            if return_code != 0 and task.cfr_fallback_rate and not any(label == "CFR 时间戳规范化重试" for label, _ in command_history):
                self._safe_unlink(temp_path)
                cfr_task = retry_source_task if retry_source_task is not None else task
                # 如果重封装后的 task 因容器时间基变化没识别出帧率，沿用原始源文件的 CFR 判定结果。
                if not cfr_task.cfr_fallback_rate:
                    cfr_task = replace(cfr_task, cfr_fallback_rate=task.cfr_fallback_rate)
                cfr_command = self._build_ffmpeg_command(cfr_task, temp_path, fps_mode="cfr")
                command_history.append(("CFR 时间戳规范化重试", cfr_command))
                task.message = f"正在尝试 CFR 时间戳规范化回退（{task.cfr_fallback_rate} fps）"
                self._update_task_ui(task)
                self.ui_queue.put(("current_text", f"CFR回退：{input_path.name}"))
                self.log(
                    f"编码失败后尝试 CFR 时间戳规范化：{input_path.name}，帧率 {task.cfr_fallback_rate}",
                    logging.WARNING,
                )
                self.log(f"CFR 回退 FFmpeg 命令：{display_command(cfr_command)}", gui=False)
                try:
                    return_code, stderr_lines, _ = self._run_encoding_command(
                        task,
                        cfr_command,
                        Path(cfr_task.input_path),
                    )
                except OSError as exc:
                    return self._failure_result(
                        task,
                        f"CFR 时间戳规范化回退无法启动 FFmpeg：{exc}",
                        stage="CFR 时间戳规范化回退",
                        move_eligible=False,
                        ffmpeg_command="\n\n".join(
                            f"[{label}]\n{display_command(item)}" for label, item in command_history
                        ),
                    )

            command_text = "\n\n".join(
                f"[{label}]\n{display_command(item)}" for label, item in command_history
            )
            if return_code != 0:
                self._safe_unlink(temp_path)
                summary = summarize_ffmpeg_failure(return_code, stderr_lines)
                stage = "重封装后重试编码" if used_remux_retry else "编码"
                prefix = "重封装后重试仍失败" if used_remux_retry else "编码失败"
                self.log(
                    f"{prefix}：{input_path.name}：{summary} | "
                    f"返回码 {format_process_return_code(return_code)}",
                    logging.ERROR,
                )
                return self._failure_result(
                    task,
                    summary[:1500],
                    stage=stage,
                    move_eligible=not self._ffmpeg_error_is_environmental(stderr_lines),
                    return_code=return_code,
                    ffmpeg_command=command_text,
                )

            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                self._safe_unlink(temp_path)
                return self._failure_result(
                    task,
                    "FFmpeg 未生成有效临时文件",
                    stage="重封装后编码输出" if used_remux_retry else "编码输出",
                    move_eligible=True,
                    return_code=return_code,
                    ffmpeg_command=command_text,
                )

            self.log(f"编码结束，开始验证：{input_path.name}")
            suspicious = any(
                ERROR_WORDS.search(line)
                and "failed to set thread priority" not in line.lower()
                and not is_benign_timestamp_message(line)
                for line in stderr_lines
            )
            valid, details = self._validate_output(task, temp_path, suspicious)
            if not valid:
                self._safe_unlink(temp_path)
                self.log(f"验证失败：{input_path.name}：{details}", logging.ERROR)
                return self._failure_result(
                    task,
                    f"验证失败：{details}",
                    stage="完成验证",
                    move_eligible=True,
                    return_code=return_code,
                    ffmpeg_command=command_text,
                )

            output_size = temp_path.stat().st_size
            task.output_size = output_size
            if output_size >= task.input_size:
                if not self._record_no_gain(task, output_size):
                    preserved = final_path.with_name(f"{final_path.stem}.no_gain_unrecorded.mkv")
                    try:
                        os.replace(temp_path, preserved)
                        return self._failure_result(
                            task,
                            f"无收益状态写入失败；输出已保留为 {preserved.name}",
                            stage="状态写入",
                            move_eligible=False,
                        )
                    except OSError:
                        return self._failure_result(
                            task,
                            "无收益状态写入失败；临时输出未删除",
                            stage="状态写入",
                            move_eligible=False,
                        )
                self._safe_unlink(temp_path)
                move_note = ""
                if self.move_no_gain_enabled:
                    moved, move_detail = self._move_original_to_no_gain(task, output_size, "新输出无压缩收益")
                    move_note = f"；{move_detail}"
                    if not moved:
                        self.log(f"无收益已记录，但移动源文件失败：{move_detail}", logging.WARNING)
                self.log(
                    f"无压缩收益：{input_path.name}，源 {format_bytes(task.input_size)}，"
                    f"输出 {format_bytes(output_size)}；已记录并删除输出{move_note}。",
                    logging.WARNING,
                )
                return "无收益", f"输出不小于源文件，已记录{move_note}"

            try:
                os.replace(temp_path, final_path)
            except OSError as exc:
                self._safe_unlink(temp_path)
                return self._failure_result(task, f"临时文件改名失败：{exc}", stage="输出落盘", move_eligible=False)

            move_detail = ""
            if self.move_original_enabled:
                moved, move_detail = self._move_original_to_pending(task)
                if not moved:
                    self.log(f"输出已完成，但移动原文件失败：{move_detail}", logging.WARNING)

            elapsed = time.monotonic() - total_start_time
            saving = 100.0 * (1.0 - output_size / task.input_size)
            used_cfr_retry = any(label == "CFR 时间戳规范化重试" for label, _ in command_history)
            retry_note = " | 自动无损重封装后重试成功" if used_remux_retry else ""
            if used_cfr_retry:
                retry_note += " | CFR 时间戳规范化回退成功"
            self.log(
                f"完成：{input_path.name} -> {final_path.name} | "
                f"{format_bytes(task.input_size)} -> {format_bytes(output_size)} | "
                f"节省 {saving:.1f}% | 用时 {format_duration(elapsed)}{retry_note}"
            )
            message = f"节省 {saving:.1f}%"
            if used_remux_retry:
                message += "；自动重封装后成功"
            if used_cfr_retry:
                message += "；CFR回退成功"
            if move_detail:
                message += f"；{move_detail}"
            return "完成", message
        finally:
            self._safe_unlink(remux_path)

    def _build_ffmpeg_command(self, task: Task, temp_path: Path, fps_mode: str = "passthrough") -> list[str]:
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
        ]
        if fps_mode == "cfr" and task.cfr_fallback_rate:
            command += ["-r:v:0", task.cfr_fallback_rate, "-fps_mode:v:0", "cfr"]
        else:
            command += ["-fps_mode:v:0", "passthrough"]

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
            "-metadata", f"AV1TOOL_SOURCE_FINGERPRINT={task.source_fingerprint}",
            "-metadata", f"AV1TOOL_FINGERPRINT_VERSION={task.fingerprint_version}",
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

    def _format_tags(self, probe: dict[str, Any]) -> dict[str, str]:
        raw_tags = ((probe.get("format") or {}).get("tags") or {})
        return {str(key).casefold(): str(value) for key, value in raw_tags.items()}

    def _source_metadata_match(self, task: Task, probe: dict[str, Any]) -> Optional[bool]:
        tags = self._format_tags(probe)
        tagged_fingerprint = tags.get("av1tool_source_fingerprint", "").strip().lower()
        tagged_fingerprint_version = tags.get("av1tool_fingerprint_version", "").strip()

        # 新版快速内容指纹优先。指纹包含文件大小与多点内容采样，
        # 因此源文件改名、移动或仅修改文件时间后仍可识别。
        if tagged_fingerprint:
            if tagged_fingerprint_version and tagged_fingerprint_version != task.fingerprint_version:
                return False
            return tagged_fingerprint == task.source_fingerprint.lower()

        # 当前版本升级前生成的文件没有快速指纹，仍沿用原有来源标记，
        # 以保证正在进行的任务与已有成品不会因升级而全部重做。
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

    def _probe_existing_cached(self, path: Path) -> dict[str, Any]:
        key = self._existing_output_key(path)
        cached = self.existing_output_probe_cache.get(key)
        if cached is not None:
            return cached
        probe = self._probe(path)
        self.existing_output_probe_cache[key] = probe
        return probe

    def _ensure_existing_output_fingerprint_index(self) -> None:
        if self.existing_output_fingerprint_index_built:
            return
        self.existing_output_fingerprint_index_built = True
        if not self.existing_output_files:
            return

        self.log("正在建立旧成品快速指纹索引，用于识别改名或移动过的成品。")
        total = len(self.existing_output_files)
        for index, candidate in enumerate(self.existing_output_files, start=1):
            if self.stop_event.is_set():
                raise InterruptedError
            try:
                probe = self._probe_existing_cached(candidate)
            except Exception as exc:
                self.log(f"旧成品指纹索引无法读取，忽略：{candidate}：{exc}", logging.WARNING, gui=False)
                continue
            tags = self._format_tags(probe)
            fingerprint = tags.get("av1tool_source_fingerprint", "").strip().lower()
            version = tags.get("av1tool_fingerprint_version", "").strip()
            if fingerprint and (not version or version == QUICK_FINGERPRINT_VERSION):
                self.existing_output_fingerprint_index.setdefault(fingerprint, []).append(candidate)
            if index % 25 == 0 or index == total:
                self.ui_queue.put(("current_text", f"建立旧成品指纹索引：{index}/{total}"))

    def _find_reusable_old_output(self, task: Task, expected_path: Path) -> Optional[Path]:
        """
        在编码前查找旧成品。

        优先使用快速内容指纹。带指纹的新成品即使改名或移动，仍可识别；
        无指纹的现有成品继续使用文件名、原来源标记、媒体结构和唯一性判断。
        """
        expected_parent = expected_path.parent.resolve(strict=False)
        input_resolved = Path(task.input_path).resolve(strict=False)
        evaluated: list[tuple[Path, int, str]] = []
        seen_candidates: set[str] = set()

        source_key = Path(task.input_path).stem.casefold()
        candidate_pool = list(self.existing_output_name_index.get(source_key, []))

        # 只有文件名候选中没有高可信匹配时，才建立全目录指纹索引。
        # 索引只读取 MKV 元数据，不解码视频，并在本次扫描中缓存。
        if task.source_fingerprint:
            self._ensure_existing_output_fingerprint_index()
            candidate_pool.extend(
                self.existing_output_fingerprint_index.get(task.source_fingerprint.lower(), [])
            )

        for candidate in candidate_pool:
            try:
                candidate_resolved = candidate.resolve(strict=False)
            except OSError:
                continue
            candidate_key = self._existing_output_key(candidate_resolved)
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            if candidate_resolved == expected_path.resolve(strict=False):
                continue
            if candidate_resolved == input_resolved:
                continue
            if self._is_claimed_existing_output(candidate_resolved):
                continue
            if not candidate_resolved.exists():
                continue

            try:
                probe = self._probe_existing_cached(candidate_resolved)
            except Exception as exc:
                self.log(f"旧成品候选无法读取，忽略：{candidate_resolved}：{exc}", logging.WARNING, gui=False)
                continue

            metadata_match = self._source_metadata_match(task, probe)
            name_match = self._legacy_name_matches_source(task, candidate_resolved)
            if metadata_match is False:
                self.log(f"旧成品候选的来源指纹或来源标记不匹配，忽略：{candidate_resolved}", gui=False)
                continue
            if metadata_match is None and not name_match:
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

            same_parent = candidate_resolved.parent.resolve(strict=False) == expected_parent
            tags = self._format_tags(probe)
            has_quick_fingerprint = bool(tags.get("av1tool_source_fingerprint", "").strip())
            if metadata_match is True and has_quick_fingerprint:
                confidence = 4
                reason = "快速内容指纹匹配"
            elif metadata_match is True:
                confidence = 3
                reason = "原来源标记匹配"
            elif same_parent:
                confidence = 2
                reason = "同一预期目录中的无指纹旧文件"
            else:
                source_count = self.source_stem_counts.get(source_key, 1)
                if source_count != 1:
                    self.log(
                        f"旧成品候选缺少来源标记，且输入中有 {source_count} 个同名源文件，忽略：{candidate_resolved}",
                        gui=False,
                    )
                    continue
                confidence = 1
                reason = "输出目录内唯一的无指纹结构匹配"
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
            move_note = ""
            if self.move_no_gain_enabled:
                moved, move_detail = self._move_original_to_no_gain(task, task.output_size, f"{source_label}无压缩收益")
                move_note = f"；{move_detail}"
                if not moved:
                    self.log(f"{source_label}无收益已记录，但移动源文件失败：{move_detail}", logging.WARNING)
            self.log(
                f"{source_label}无压缩收益：{path.name}，已记录并删除输出{move_note}。",
                logging.WARNING,
            )
            return "无收益", f"已有输出不小于源文件，已记录并删除{move_note}"

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
            probe = self._probe_existing_cached(path)
            metadata_match = self._source_metadata_match(task, probe)
            if metadata_match is False:
                return False, "来源快速指纹或来源标记与当前源文件不匹配"
            return self._validate_output(
                task, path, suspicious=False, existing=True, probe_data=probe
            )
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
            benign_lines = [line for line in stderr_lines if is_benign_timestamp_message(line)]
            failure_lines = [line for line in stderr_lines if not is_benign_timestamp_message(line)]

            # null 封装器可能把重复 DTS 报成 error，甚至返回非零状态，
            # 但这只说明时间戳不严格单调，不代表 AV1 画面或 Opus 音频无法解码。
            # 只有出现其余解码错误，或无任何可解释信息却返回非零时，才判失败。
            if failure_lines:
                detail = "\n".join(failure_lines).strip()[-1000:]
                return False, f"完整解码验证失败：{detail}"
            if process.returncode != 0 and not benign_lines:
                return False, f"完整解码验证失败：FFmpeg 返回码 {process.returncode}"
            if benign_lines:
                self.log(
                    f"完整解码验证忽略 {len(benign_lines)} 条重复/非单调 DTS 时间戳信息：{path.name}",
                    logging.WARNING,
                )
                return True, "完整解码通过（忽略非单调 DTS 时间戳信息）"
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
        recorded_fingerprint = str(record.get("source_fingerprint", "")).strip().lower()
        if recorded_fingerprint:
            same_source = (
                record.get("fingerprint_version", QUICK_FINGERPRINT_VERSION) == task.fingerprint_version
                and recorded_fingerprint == task.source_fingerprint.lower()
            )
        else:
            # 当前版本升级前的中断状态没有指纹，继续按大小和纳秒修改时间恢复。
            same_source = (
                record.get("input_size") == task.input_size
                and record.get("input_mtime_ns") == task.input_mtime_ns
            )
        expected = (
            same_source
            and record.get("policy_version") == ENCODING_POLICY_VERSION
            and record.get("crf") == task.crf
            and record.get("audio_policy") == self._audio_policy_signature(task)
        )
        if expected and record.get("record_kind") == "precheck":
            expected = (
                self.precheck_enabled
                and parse_float(record.get("precheck_min_saving_percent"), -1.0) == self.precheck_threshold
            )
        if expected:
            return record
        self.no_gain_records.pop(key, None)
        self.log(f"无收益记录已失效，将重新处理：{Path(task.input_path).name}", gui=False)
        return None

    def _record_no_gain(self, task: Task, output_size: int, record_kind: str = "actual", detail: str = "") -> bool:
        key = self._state_record_key(task.input_path)
        self.no_gain_records[key] = {
            "input_path": str(Path(task.input_path).resolve(strict=False)),
            "input_size": task.input_size,
            "input_mtime_ns": task.input_mtime_ns,
            "source_fingerprint": task.source_fingerprint,
            "fingerprint_version": task.fingerprint_version,
            "policy_version": ENCODING_POLICY_VERSION,
            "crf": task.crf,
            "audio_policy": self._audio_policy_signature(task),
            "output_path": str(Path(task.output_path).resolve(strict=False)),
            "output_size": int(output_size),
            "record_kind": record_kind,
            "detail": detail,
            "precheck_min_saving_percent": self.precheck_threshold if record_kind == "precheck" else None,
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

    def _move_original_to_no_gain(self, task: Task, output_size: int, reason: str) -> tuple[bool, str]:
        source = Path(task.input_path)
        if not source.exists():
            return True, "源文件已不在原位置"
        root = Path(task.source_root)
        try:
            relative = source.relative_to(root)
        except ValueError:
            relative = Path(source.name)
        destination = root / NO_GAIN_DIRNAME / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate = destination
            counter = 2
            while candidate.exists() or candidate.with_name(candidate.name + ".no_gain.txt").exists():
                candidate = destination.with_name(f"{destination.stem} ({counter}){destination.suffix}")
                counter += 1

            shutil.move(str(source), str(candidate))
            report_path = candidate.with_name(candidate.name + ".no_gain.txt")
            saving = 100.0 * (1.0 - output_size / task.input_size) if task.input_size > 0 else 0.0
            report_lines = [
                "AV1 无压缩收益记录",
                "=" * 60,
                f"记录时间：{datetime.now().isoformat(timespec='seconds')}",
                f"原因：{reason}",
                f"原始路径：{source}",
                f"移动位置：{candidate}",
                f"源文件大小：{task.input_size} 字节",
                f"转码输出大小：{int(output_size)} 字节",
                f"节省比例：{saving:.2f}%",
                f"视频参数：{task.width}x{task.height} / {task.fps:.3f} fps / CRF {task.crf} / Preset 5",
                f"编码策略：{ENCODING_POLICY_VERSION}",
                f"快速指纹：{task.source_fingerprint}",
                f"完整日志：{LOG_PATH}",
            ]
            try:
                report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
            except OSError as exc:
                self.log(f"无收益源文件已移动，但无法写入说明文件：{report_path}：{exc}", logging.WARNING)
                self.log(f"无收益源文件已移入：{source} -> {candidate}")
                return True, f"源文件已移至 {candidate}（说明文件写入失败）"

            self.log(f"无收益源文件已移入：{source} -> {candidate}；说明：{report_path}")
            return True, f"源文件已移至 {candidate}"
        except Exception as exc:
            return False, f"移动无收益源文件失败：{exc}"

    def _move_original_to_failed(self, task: Task) -> tuple[bool, str]:
        source = Path(task.input_path)
        if not source.exists():
            return False, "源文件已不在原位置"
        root = Path(task.source_root)
        try:
            relative = source.relative_to(root)
        except ValueError:
            relative = Path(source.name)
        destination = root / FAILED_DIRNAME / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate = destination
            counter = 2
            while candidate.exists() or candidate.with_name(candidate.name + ".failure.txt").exists():
                candidate = destination.with_name(f"{destination.stem} ({counter}){destination.suffix}")
                counter += 1

            shutil.move(str(source), str(candidate))
            task.failure_moved_to = str(candidate)
            report_path = candidate.with_name(candidate.name + ".failure.txt")
            report_lines = [
                "AV1 压制失败记录",
                "=" * 60,
                f"失败时间：{datetime.now().isoformat(timespec='seconds')}",
                f"原始路径：{source}",
                f"移动位置：{candidate}",
                f"失败阶段：{task.failure_stage or '未知'}",
                f"FFmpeg 返回码：{format_process_return_code(task.failure_return_code) if task.failure_return_code is not None else '无'}",
                f"错误摘要：{task.failure_error or task.message or '未提供'}",
                f"视频参数：{task.width}x{task.height} / {task.fps:.3f} fps / CRF {task.crf} / Preset 5",
                f"编码策略：{ENCODING_POLICY_VERSION}",
                f"源文件大小：{task.input_size} 字节",
                f"快速指纹：{task.source_fingerprint}",
                f"完整日志：{LOG_PATH}",
            ]
            if task.failure_ffmpeg_command:
                report_lines += ["", "FFmpeg 命令：", task.failure_ffmpeg_command]
            try:
                report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
            except OSError as exc:
                self.log(f"失败视频已移动，但无法写入说明文件：{report_path}：{exc}", logging.WARNING)
                self.log(f"失败源文件已移入：{source} -> {candidate}")
                return True, f"源文件已移至 {candidate}（说明文件写入失败）"

            self.log(f"失败源文件已移入：{source} -> {candidate}；说明：{report_path}")
            return True, f"源文件已移至 {candidate}"
        except Exception as exc:
            return False, f"移动失败源文件失败：{exc}"

    def _save_current_state(self, active: bool) -> None:
        if self.current_state_context is None:
            raise RuntimeError("当前没有可保存的任务上下文")
        sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled = self.current_state_context
        self._save_state(sources, output_dir, suffix, recursive, move_original, move_failed, move_no_gain, temp_dir_text, precheck_enabled, active)

    def _save_state(
        self,
        sources: list[InputSource],
        output_dir: Path,
        suffix: str,
        recursive: bool,
        move_original: bool,
        move_failed: bool,
        move_no_gain: bool,
        temp_dir_text: str,
        precheck_enabled: bool,
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
            "move_failed": move_failed,
            "move_no_gain": move_no_gain,
            "temp_dir": temp_dir_text,
            "precheck_enabled": precheck_enabled,
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
        self.move_failed_var.set(bool(data.get("move_failed", True)))
        self.move_no_gain_var.set(bool(data.get("move_no_gain", True)))
        self.temp_dir_var.set(str(data.get("temp_dir", "")))
        self.precheck_var.set(bool(data.get("precheck_enabled", self.settings.get("precheck_enabled", False))))
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
                    self.current_detail_text.set("")
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
                    self.current_text.set(Path(task.input_path).name)
                    self.current_detail_text.set(
                        f"{percent * 100:.1f}% | 速度 {speed:.2f}x | 剩余 {format_duration(current_eta)}"
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
                    self.current_detail_text.set("")
                    messagebox.showinfo("完成", str(payload))
                elif kind == "stopped":
                    self._set_idle()
                    self.current_text.set(str(payload))
                    self.current_detail_text.set("")
                elif kind == "failed":
                    self._set_idle()
                    self.current_text.set("任务中止")
                    self.current_detail_text.set("")
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
        self.optional_button.configure(state=tk.NORMAL)
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
        self._persist_settings()
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
            "请填写 ffmpeg_path 和 ffprobe_path 后重新运行；后续软件偏好也会保存在这个 JSON 中。",
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
