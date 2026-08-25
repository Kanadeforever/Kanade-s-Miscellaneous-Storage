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
- 默认启用“两阶段队列”：第一阶段优先压制能顺利通过的视频，失败项先标记为“待修复”并继续下一个；第二阶段再集中做失败点定位与局部回退测试
- 单视频内部异常隔离：一个文件或验证器异常不会终止整个无人值守队列
- 编码与完整解码验证均带无进度看门狗，真正卡死时可自动终止并记录位置
- 中断恢复会保留“待修复”阶段，避免重启后重复一次已知失败的标准整片压制
- Windows 下可统一限制本程序启动的 FFmpeg/ffprobe 优先级和整数 CPU 硬上限；普通编码不加入 lp，既定压制与验证标准不变
- 设置窗口采用双列布局，低分辨率屏幕也能完整显示主要配置
- “额外功能”提供源视频与 AV1 成品的单文件/文件夹严格验证；严重问题与时间戳警告分级报告
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
9. 失败点定位不是“断点续压”。它只是在失败位置附近快速试验回退方案，确认能过后仍从头生成一个完整、可验证的成品。
10. 如果修改默认 CRF、音频策略、输出验证规则或会影响成品体积/结构的逻辑，应该同步提升 ENCODING_POLICY_VERSION，
   这样旧的“无收益记录”不会错误地阻止新策略重新处理。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
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
CONFIG_SCHEMA_VERSION = 2
ENCODING_POLICY_VERSION = "av1-svt-p5-pixel-crf-opus-128-192-v3-ignore-attached-pic"
QUICK_FINGERPRINT_VERSION = "sha256-sparse-v1"
QUICK_FINGERPRINT_CHUNK_SIZE = 256 * 1024
QUICK_FINGERPRINT_SAMPLE_COUNT = 5
PENDING_DELETE_DIRNAME = "_待删除原文件"
FAILED_DIRNAME = "_压制失败"
NO_GAIN_DIRNAME = "_无压缩收益"
TEMP_WORK_DIRNAME = "_压制临时文件"

# 无人值守看门狗：只在“媒体时间完全不前进”时触发，不限制正常慢速编码的总时长。
# 编码阶段 5 分钟无任何 out_time 变化，视为 FFmpeg/SVT 卡死；
# 完整解码验证阶段放宽到 10 分钟，避免大型文件或磁盘较慢时误杀。
ENCODING_STALL_TIMEOUT_SECONDS = 300.0
VERIFY_STALL_TIMEOUT_SECONDS = 600.0

# 额外功能中的“严格验证”会先让 ffprobe 完整遍历容器数据，再让 FFmpeg
# 严格解码全部动态视频流和音频流。这里仅限制“媒体时间完全不前进”的情况，
# 不限制正常的大文件验证总时长。
STRICT_VERIFY_STALL_TIMEOUT_SECONDS = 600.0
STRICT_PROBE_TIMEOUT_SECONDS = 6 * 60 * 60

# 严格验证中，FFmpeg 的 null 输出器可能报告“DTS 非严格递增”。这类信息来自
# 验证输出端的时间戳检查，不能单独证明 AV1 码流损坏。它仍然值得提示用户，
# 但必须与真正的解码错误、截断和提前结束分开统计。
STRICT_TIMESTAMP_WARNING_PATTERNS = (
    "application provided invalid, non monotonically increasing dts to muxer",
    "non-monotonically increasing dts",
    "non monotonically increasing dts",
    "non-monotonous dts",
)

# 当 null 输出器因 DTS 问题报告错误时，FFmpeg 可能继续打印下面这些“后果性”信息。
# 如果本次 stderr 中存在明确的 DTS 警告，并且除这些后果性信息外没有其他错误，
# 则整体仍归类为“时间戳警告”，而不是误报为码流损坏。
STRICT_TIMESTAMP_DERIVATIVE_PATTERNS = (
    "error muxing a packet",
    "error writing trailer",
    "error closing file",
    "conversion failed",
    "task finished with error code",
    "terminating thread with return code",
)

STRICT_TIMESTAMP_SAMPLE_LIMIT = 6

# DEFAULT_SETTINGS 是“可长期保存的用户偏好”。
# 这些值会写入同名 JSON，例如 AV1_Compress_tool.json。
# 注意：不要把正在处理的文件列表、进度、无收益历史等写到这里；那些属于 STATE_PATH。
# 设计成两份文件的原因：
# - 配置文件：用户可以手动编辑，跨任务长期存在；
# - 状态文件：程序自动维护，用于恢复和跳过，内容随任务变化。
# 内部使用的扁平设置。
#
# 注意：从这个版本开始，同名 JSON 对用户展示为“分组式配置文件”，例如：
# {
#   "tools": {"ffmpeg_path": "...", "ffprobe_path": "..."},
#   "settings": {
#     "output": {...},
#     "classification": {...},
#     "precheck": {...},
#     "repair": {...},
#     "temp": {...}
#   }
# }
#
# 程序内部仍然使用下面这种扁平结构，是为了不大规模改动编码、恢复和 UI 逻辑。
# 读写配置时由 flatten/save 函数负责在“分组 JSON”和“内部扁平设置”之间转换。
# 旧版扁平 JSON 不再兼容；如果保留旧 JSON，缺失的新分组会按默认值处理。
DEFAULT_SETTINGS = {
    "output_suffix": "_AV1",
    "recursive": True,
    "move_original_on_success": True,
    "move_failed_on_failure": True,
    "move_no_gain": True,
    "temp_dir": "",
    "precheck_enabled": False,
    "precheck_min_saving_percent": 8.0,
    # 默认启用：失败后先定位失败时间点，在该位置附近测试回退方案；确认能过后再整片重跑。
    "failure_point_probe_enabled": True,
    # 默认关闭：更激进的兼容性回退。开启后局部测试会额外尝试 lp=4、CRF-1、Preset6。
    "compatibility_fallback_enabled": False,
    # 两阶段队列默认开启：先处理正常视频，失败项最后集中修复。
    "two_stage_queue_enabled": True,
    # 自动失败点局部测试的默认范围：失败点前 30 秒、后 90 秒。
    "failure_probe_pre_seconds": 30.0,
    "failure_probe_post_seconds": 90.0,
    # 单个文件最多定位几个新的失败点，避免极端文件无限循环。
    "failure_probe_max_points": 3,
    # 日常资源控制只作用于 Windows 调度层，不修改 SVT-AV1 编码参数。
    # 普通编码路径明确不自动加入 lp；lp=4 只保留给失败后的兼容性回退。
    "process_priority": "below_normal",
    "cpu_limit_enabled": True,
    "cpu_limit_percent": 70,
}


def grouped_settings_from_flat(settings: dict[str, Any]) -> dict[str, Any]:
    """把内部扁平设置转换成写入 JSON 的分组结构。

    这个函数只用于写配置文件；state.json 仍然保留它自己的运行状态结构。
    """
    normalized = normalize_settings(settings)
    return {
        "output": {
            "filename_suffix": normalized["output_suffix"],
            "recursive": normalized["recursive"],
        },
        "classification": {
            "move_success_original": normalized["move_original_on_success"],
            "move_no_gain_original": normalized["move_no_gain"],
            "move_failed_original": normalized["move_failed_on_failure"],
        },
        "precheck": {
            "enabled": normalized["precheck_enabled"],
            "min_saving_percent": normalized["precheck_min_saving_percent"],
        },
        "repair": {
            "two_stage_queue": normalized["two_stage_queue_enabled"],
            "failure_point_probe": normalized["failure_point_probe_enabled"],
            "compatibility_fallback": normalized["compatibility_fallback_enabled"],
            "failure_probe_pre_seconds": normalized["failure_probe_pre_seconds"],
            "failure_probe_post_seconds": normalized["failure_probe_post_seconds"],
            "failure_probe_max_points": normalized["failure_probe_max_points"],
        },
        "resources": {
            "process_priority": normalized["process_priority"],
            "cpu_limit_enabled": normalized["cpu_limit_enabled"],
            "cpu_limit_percent": normalized["cpu_limit_percent"],
        },
        "temp": {
            "work_dir": normalized["temp_dir"],
        },
    }


def flat_settings_from_grouped(raw: Any) -> dict[str, Any]:
    """读取新分组式 JSON，转换成内部扁平设置。

    不做旧配置迁移：只识别新的分组字段。旧版 JSON 中的扁平 settings 字段会被忽略。
    """
    settings = clone_default_settings()
    if not isinstance(raw, dict):
        return settings

    output = raw.get("output") if isinstance(raw.get("output"), dict) else {}
    classification = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    precheck = raw.get("precheck") if isinstance(raw.get("precheck"), dict) else {}
    repair = raw.get("repair") if isinstance(raw.get("repair"), dict) else {}
    resources = raw.get("resources") if isinstance(raw.get("resources"), dict) else {}
    temp = raw.get("temp") if isinstance(raw.get("temp"), dict) else {}

    if "filename_suffix" in output:
        settings["output_suffix"] = output["filename_suffix"]
    if "recursive" in output:
        settings["recursive"] = output["recursive"]

    if "move_success_original" in classification:
        settings["move_original_on_success"] = classification["move_success_original"]
    if "move_failed_original" in classification:
        settings["move_failed_on_failure"] = classification["move_failed_original"]
    if "move_no_gain_original" in classification:
        settings["move_no_gain"] = classification["move_no_gain_original"]

    if "enabled" in precheck:
        settings["precheck_enabled"] = precheck["enabled"]
    if "min_saving_percent" in precheck:
        settings["precheck_min_saving_percent"] = precheck["min_saving_percent"]

    if "two_stage_queue" in repair:
        settings["two_stage_queue_enabled"] = repair["two_stage_queue"]
    if "failure_point_probe" in repair:
        settings["failure_point_probe_enabled"] = repair["failure_point_probe"]
    if "compatibility_fallback" in repair:
        settings["compatibility_fallback_enabled"] = repair["compatibility_fallback"]
    if "failure_probe_pre_seconds" in repair:
        settings["failure_probe_pre_seconds"] = repair["failure_probe_pre_seconds"]
    if "failure_probe_post_seconds" in repair:
        settings["failure_probe_post_seconds"] = repair["failure_probe_post_seconds"]
    if "failure_probe_max_points" in repair:
        settings["failure_probe_max_points"] = repair["failure_probe_max_points"]

    if "process_priority" in resources:
        settings["process_priority"] = resources["process_priority"]
    if "cpu_limit_enabled" in resources:
        settings["cpu_limit_enabled"] = resources["cpu_limit_enabled"]
    if "cpu_limit_percent" in resources:
        settings["cpu_limit_percent"] = resources["cpu_limit_percent"]

    if "work_dir" in temp:
        settings["temp_dir"] = temp["work_dir"]

    return normalize_settings(settings)


CONFIG_TEMPLATE = {
    "version": CONFIG_SCHEMA_VERSION,
    "tools": {
        "ffmpeg_path": "",
        "ffprobe_path": "",
    },
    "settings": {
        "output": {
            "filename_suffix": "_AV1",
            "recursive": True,
        },
        "classification": {
            "move_success_original": True,
            "move_no_gain_original": True,
            "move_failed_original": True,
        },
        "precheck": {
            "enabled": False,
            "min_saving_percent": 8.0,
        },
        "repair": {
            "two_stage_queue": True,
            "failure_point_probe": True,
            "compatibility_fallback": False,
            "failure_probe_pre_seconds": 30.0,
            "failure_probe_post_seconds": 90.0,
            "failure_probe_max_points": 3,
        },
        "resources": {
            "process_priority": "below_normal",
            "cpu_limit_enabled": True,
            "cpu_limit_percent": 70,
        },
        "temp": {
            "work_dir": "",
        },
    },
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

# Windows 进程优先级只提供适合后台编码的三个等级。
# 不提供“高”和“实时”，避免无人值守时反过来抢占系统关键线程。
PROCESS_PRIORITY_VALUES = {"idle", "below_normal", "normal"}
PROCESS_PRIORITY_LABELS = {
    "idle": "空闲",
    "below_normal": "低于正常",
    "normal": "正常",
}
PROCESS_PRIORITY_FROM_LABEL = {label: value for value, label in PROCESS_PRIORITY_LABELS.items()}
WINDOWS_PRIORITY_CLASSES = {
    "idle": 0x00000040,
    "below_normal": 0x00004000,
    "normal": 0x00000020,
}

# Job Object CPU rate control 常量。CpuRate 使用“百分比 × 100”，
# 例如 70% 写为 7000。该限制属于 Windows 调度层，不改变 FFmpeg/SVT 参数。
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS = 15


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    """ctypes 对应的 Windows JOBOBJECT_CPU_RATE_CONTROL_INFORMATION。

    原生结构第二个成员是 union；硬上限模式只使用其中的 DWORD CpuRate，
    因此 Python 侧按两个连续 DWORD 声明即可，大小和内存布局保持一致。
    """

    _fields_ = [
        ("ControlFlags", wintypes.DWORD),
        ("CpuRate", wintypes.DWORD),
    ]


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
#    - _open_optional_features 创建“设置”窗口；
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
    # 最近一次可定位失败的大致媒体时间/帧号。验证阶段也会更新这两个值，
    # 使第二阶段能直接围绕验证失败点测试，而不是再次盲目完整压制。
    failure_seconds: float = 0.0
    failure_frame: int = 0
    # 记录失败点定位过程，写入失败说明和 state.json，方便用户知道工具具体在哪里、用哪些方案试过。
    failure_probe_history: list[str] = field(default_factory=list)

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
    settings["failure_point_probe_enabled"] = bool(settings.get("failure_point_probe_enabled", True))
    settings["compatibility_fallback_enabled"] = bool(settings.get("compatibility_fallback_enabled", False))
    settings["two_stage_queue_enabled"] = bool(settings.get("two_stage_queue_enabled", True))
    priority = str(settings.get("process_priority", "below_normal")).strip().lower()
    settings["process_priority"] = priority if priority in PROCESS_PRIORITY_VALUES else "below_normal"
    settings["cpu_limit_enabled"] = bool(settings.get("cpu_limit_enabled", True))

    # GUI 和新配置只接受整数百分比。Windows Job Object 的 CpuRate 本身是整数，
    # 单位为“百分比 × 100”；虽然底层能够表达小数百分比，本工具没有必要暴露
    # 这种精度。这里允许旧的 70.0 这类“数值上为整数”的值自动规范为 70，
    # 但 70.5、字符串小数、布尔值等均回退到默认值。
    raw_cpu_limit = settings.get("cpu_limit_percent", 70)
    cpu_limit_percent = 70
    if isinstance(raw_cpu_limit, bool):
        cpu_limit_percent = 70
    elif isinstance(raw_cpu_limit, int):
        cpu_limit_percent = raw_cpu_limit
    elif isinstance(raw_cpu_limit, float) and math.isfinite(raw_cpu_limit) and raw_cpu_limit.is_integer():
        cpu_limit_percent = int(raw_cpu_limit)
    elif isinstance(raw_cpu_limit, str) and raw_cpu_limit.strip().isdigit():
        cpu_limit_percent = int(raw_cpu_limit.strip())
    settings["cpu_limit_percent"] = min(max(cpu_limit_percent, 10), 100)
    for _key, _default, _min, _max in (
        ("failure_probe_pre_seconds", 30.0, 0.0, 600.0),
        ("failure_probe_post_seconds", 90.0, 15.0, 1200.0),
    ):
        try:
            _value = float(settings.get(_key, _default))
        except (TypeError, ValueError):
            _value = _default
        settings[_key] = min(max(_value, _min), _max)
    try:
        _max_points = int(settings.get("failure_probe_max_points", 3))
    except (TypeError, ValueError):
        _max_points = 3
    settings["failure_probe_max_points"] = min(max(_max_points, 1), 10)

    try:
        threshold = float(settings.get("precheck_min_saving_percent", 8.0))
    except (TypeError, ValueError):
        threshold = 8.0
    settings["precheck_min_saving_percent"] = min(max(threshold, 1.0), 50.0)
    return settings


def save_config(settings: dict[str, Any], ffmpeg_path: str, ffprobe_path: str) -> None:
    """把软件配置写回同名 JSON。

    这里写入的是新的分组式配置文件：tools 保存 FFmpeg/ffprobe 路径，settings 按用途分组。
    不写任务队列、失败点、无收益历史等运行状态；那些仍然只属于 state.json。
    """
    atomic_write_json(CONFIG_PATH, {
        "version": CONFIG_SCHEMA_VERSION,
        "tools": {
            "ffmpeg_path": str(ffmpeg_path or "").strip(),
            "ffprobe_path": str(ffprobe_path or "").strip(),
        },
        "settings": grouped_settings_from_flat(settings),
    })


def generate_config_template() -> None:
    atomic_write_json(CONFIG_PATH, CONFIG_TEMPLATE)


def _resolve_dependency_path_or_empty(value: str) -> str:
    """解析工具路径；空路径或无效路径都允许启动主界面。

    真正开始压制时仍会执行完整环境检查；这里不阻止启动，
    是为了让用户可以进入“设置”窗口修正 FFmpeg/ffprobe 路径。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(resolve_dependency_path(text))
    except ValueError:
        return ""


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取配置文件：{exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    # 新配置只读取 tools 分组；不做旧版 ffmpeg_path/ffprobe_path 顶层字段兼容。
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    ffmpeg_raw = tools.get("ffmpeg_path", "")
    ffprobe_raw = tools.get("ffprobe_path", "")
    if not isinstance(ffmpeg_raw, str) or not isinstance(ffprobe_raw, str):
        raise ValueError("tools.ffmpeg_path 和 tools.ffprobe_path 必须是字符串。")

    settings = flat_settings_from_grouped(data.get("settings"))

    # 不再在启动阶段要求路径已经填写。用户可以打开主界面后在“设置”里选择路径。
    # 如果路径为空，点击“开始压制”时会给出明确提示。
    config = {
        "ffmpeg_path": _resolve_dependency_path_or_empty(ffmpeg_raw),
        "ffprobe_path": _resolve_dependency_path_or_empty(ffprobe_raw),
        "raw_ffmpeg_path": ffmpeg_raw.strip(),
        "raw_ffprobe_path": ffprobe_raw.strip(),
        "settings": settings,
    }

    # 配置读取成功后按新结构回写一次，确保 JSON 文件始终是当前版本格式。
    try:
        save_config(settings, config["raw_ffmpeg_path"], config["raw_ffprobe_path"])
    except OSError:
        pass
    return config


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


def is_strict_timestamp_warning(line: str) -> bool:
    """判断一行 FFmpeg stderr 是否只是 null 输出端的 DTS 时间戳警告。"""
    lowered = line.casefold()
    return any(pattern in lowered for pattern in STRICT_TIMESTAMP_WARNING_PATTERNS)


def split_strict_decode_messages(lines: Iterable[str]) -> tuple[list[str], list[str]]:
    """把严格解码日志拆分为“严重错误”和“时间戳警告”。

    ``Application provided invalid, non monotonically increasing dts`` 来自验证命令的
    null 输出器。它说明时间戳不够规整，但不能单独证明视频帧无法正确解码。

    如果同一轮日志中还存在其他错误，则其他错误仍按严重问题处理；只有纯 DTS
    警告及其后果性 muxer 信息才会降级。
    """
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    timestamp_lines = [line for line in cleaned if is_strict_timestamp_warning(line)]
    other_lines = [line for line in cleaned if not is_strict_timestamp_warning(line)]

    if timestamp_lines:
        filtered_other: list[str] = []
        for line in other_lines:
            lowered = line.casefold()
            if any(pattern in lowered for pattern in STRICT_TIMESTAMP_DERIVATIVE_PATTERNS):
                continue
            filtered_other.append(line)
        other_lines = filtered_other

    return other_lines, timestamp_lines


def format_timestamp_warning(
    timestamp_count: int,
    timestamp_samples: list[str],
    last_seconds: float,
) -> str:
    """把大量重复 DTS 日志压缩成一条可读警告，避免报告文件被刷满。"""
    samples = timestamp_samples[:STRICT_TIMESTAMP_SAMPLE_LIMIT]
    sample_text = "\n".join(f"    {line}" for line in samples)
    omitted = max(timestamp_count - len(samples), 0)
    suffix = f"\n    ……另有 {omitted} 条同类信息" if omitted > 0 else ""
    return (
        f"时间戳警告（约 {format_duration(last_seconds)}）：发现 {timestamp_count} 条重复或"
        "非严格递增 DTS。该信息来自严格验证的 null 输出端，未单独证明 AV1 码流损坏；"
        "已继续检查到文件末尾。\n"
        f"{sample_text}{suffix}"
    )


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
        # 当前 FFmpeg 或 ffprobe 对应的 Windows Job Object 句柄必须保持到进程退出，
        # 否则 CPU 硬上限可能提前失效。非 Windows 或未启用硬上限时为 None。
        self.current_job_handle: Optional[int] = None
        self.process_lock = threading.Lock()
        self.resource_limit_warning_keys: set[str] = set()
        # 成功读回资源限制后也只在每种工具/配置首次记录一次，避免批量 ffprobe 时刷屏。
        self.resource_limit_success_keys: set[str] = set()
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
        self.failure_point_probe_enabled = bool(self.settings.get("failure_point_probe_enabled", True))
        self.compatibility_fallback_enabled = bool(self.settings.get("compatibility_fallback_enabled", False))
        self.failure_probe_pre_seconds = float(self.settings.get("failure_probe_pre_seconds", 30.0))
        self.failure_probe_post_seconds = float(self.settings.get("failure_probe_post_seconds", 90.0))
        self.failure_probe_max_points = int(self.settings.get("failure_probe_max_points", 3))
        # 由 _run_encoding_command 实时刷新，用于 FFmpeg 崩溃/卡住后定位大致故障点。
        self.last_encoding_seconds = 0.0
        self.last_encoding_frame = 0
        self.last_encoding_progress_at = 0.0
        self.custom_temp_root: Optional[Path] = None
        self.options_window: Optional[tk.Toplevel] = None
        self.extra_features_window: Optional[tk.Toplevel] = None
        self.extra_validation_worker: Optional[threading.Thread] = None
        self.extra_validation_stop_event = threading.Event()
        self.extra_validation_process: Optional[subprocess.Popen[str]] = None
        self.extra_validation_job_handle: Optional[int] = None
        self.extra_validation_process_lock = threading.Lock()
        self.current_state_context: Optional[tuple[list[InputSource], Path, str, bool, bool, bool, bool, str, bool]] = None
        self.existing_output_files: list[Path] = []
        self.existing_output_name_index: dict[str, list[Path]] = {}
        self.existing_output_probe_cache: dict[str, dict[str, Any]] = {}
        self.existing_output_fingerprint_index: dict[str, list[Path]] = {}
        self.existing_output_fingerprint_index_built = False
        self.claimed_existing_outputs: set[str] = set()
        self.source_stem_counts: dict[str, int] = {}
        # 仅在用户确认恢复上次任务后填充。用于让“待修复”任务直接进入第二阶段，
        # 避免程序重启后再次从头重复第一阶段标准压制。
        self.recovery_task_records: dict[str, dict[str, Any]] = {}

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
            "failure_point_probe_enabled": self.failure_point_var.get() if hasattr(self, "failure_point_var") else self.settings.get("failure_point_probe_enabled", True),
            "compatibility_fallback_enabled": self.compatibility_var.get() if hasattr(self, "compatibility_var") else self.settings.get("compatibility_fallback_enabled", False),
            "two_stage_queue_enabled": self.two_stage_var.get() if hasattr(self, "two_stage_var") else self.settings.get("two_stage_queue_enabled", True),
            "failure_probe_pre_seconds": self.settings.get("failure_probe_pre_seconds", 30.0),
            "failure_probe_post_seconds": self.settings.get("failure_probe_post_seconds", 90.0),
            "failure_probe_max_points": self.settings.get("failure_probe_max_points", 3),
            "process_priority": PROCESS_PRIORITY_FROM_LABEL.get(
                self.process_priority_var.get(),
                self.settings.get("process_priority", "below_normal"),
            ) if hasattr(self, "process_priority_var") else self.settings.get("process_priority", "below_normal"),
            "cpu_limit_enabled": self.cpu_limit_var.get() if hasattr(self, "cpu_limit_var") else self.settings.get("cpu_limit_enabled", True),
            "cpu_limit_percent": self.cpu_limit_percent_var.get() if hasattr(self, "cpu_limit_percent_var") else self.settings.get("cpu_limit_percent", 70),
        })

    def _persist_settings(self) -> None:
        """把软件偏好保存到同名 JSON；失败只记录日志，不影响当前压制。"""
        self.settings = self._current_settings()
        # 运行时判断收益预估使用的是 self.precheck_threshold；保存设置时必须
        # 同步刷新，否则用户在本次启动中修改阈值后，实际仍会沿用启动时旧值。
        self.precheck_threshold = float(
            self.settings.get("precheck_min_saving_percent", 8.0)
        )
        try:
            self.raw_ffmpeg_path = self.ffmpeg_path_var.get().strip() if hasattr(self, "ffmpeg_path_var") else self.raw_ffmpeg_path
            self.raw_ffprobe_path = self.ffprobe_path_var.get().strip() if hasattr(self, "ffprobe_path_var") else self.raw_ffprobe_path
            try:
                self.ffmpeg = _resolve_dependency_path_or_empty(self.raw_ffmpeg_path)
                self.ffprobe = _resolve_dependency_path_or_empty(self.raw_ffprobe_path)
            except ValueError as exc:
                self.logger.warning("工具路径尚不可用：%s", exc)
            save_config(self.settings, self.raw_ffmpeg_path, self.raw_ffprobe_path)
        except Exception:
            self.logger.exception("保存配置文件失败：%s", CONFIG_PATH)

    # ---------- GUI ----------

    def _build_ui(self) -> None:
        self.root.title(APP_STEM)
        self.root.geometry("780x920")
        self.root.minsize(780, 620)
        # 主窗口保留 780 像素最小宽度，但允许横向、纵向拉伸和最大化。
        # Treeview 本身带横向滚动条，因此放大窗口不会破坏窄屏下的基本布局。
        self.root.resizable(True, True)

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

        # 软件设置变量集中在这里创建；主界面只露出“设置”按钮，详细设置放入独立窗口。
        self.ffmpeg_path_var = tk.StringVar(value=self.raw_ffmpeg_path)
        self.ffprobe_path_var = tk.StringVar(value=self.raw_ffprobe_path)
        self.suffix_var = tk.StringVar(value=str(self.settings.get("output_suffix", "_AV1")))
        self.recursive_var = tk.BooleanVar(value=bool(self.settings.get("recursive", True)))
        self.move_original_var = tk.BooleanVar(value=bool(self.settings.get("move_original_on_success", True)))
        self.move_failed_var = tk.BooleanVar(value=bool(self.settings.get("move_failed_on_failure", True)))
        self.move_no_gain_var = tk.BooleanVar(value=bool(self.settings.get("move_no_gain", True)))
        self.temp_dir_var = tk.StringVar(value=str(self.settings.get("temp_dir", "")))
        self.precheck_var = tk.BooleanVar(value=bool(self.settings.get("precheck_enabled", False)))
        self.failure_point_var = tk.BooleanVar(value=bool(self.settings.get("failure_point_probe_enabled", True)))
        self.compatibility_var = tk.BooleanVar(value=bool(self.settings.get("compatibility_fallback_enabled", False)))
        self.two_stage_var = tk.BooleanVar(value=bool(self.settings.get("two_stage_queue_enabled", True)))
        self.process_priority_var = tk.StringVar(
            value=PROCESS_PRIORITY_LABELS.get(
                str(self.settings.get("process_priority", "below_normal")),
                "低于正常",
            )
        )
        self.cpu_limit_var = tk.BooleanVar(value=bool(self.settings.get("cpu_limit_enabled", True)))
        self.cpu_limit_percent_var = tk.StringVar(value=str(int(self.settings.get("cpu_limit_percent", 70))))
        option_buttons = ttk.Frame(output_frame)
        option_buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.optional_button = ttk.Button(
            option_buttons,
            text="设置",
            command=self._open_optional_features,
        )
        self.optional_button.pack(side=tk.LEFT)
        self.extra_features_button = ttk.Button(
            option_buttons,
            text="额外功能",
            command=self._open_extra_features,
        )
        self.extra_features_button.pack(side=tk.LEFT, padx=(6, 0))

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
        """打开双列“设置”窗口。

        设置项增多后继续单列堆叠会让 768p 等低分辨率屏幕无法看到底部按钮。
        当前窗口把设置按职责分到左右两列，并限制初始尺寸不超过当前屏幕可用区域。
        所有长期配置仍写入同名 JSON；任务恢复和历史记录仍写入 state.json。
        """
        if self.options_window is not None and self.options_window.winfo_exists():
            self.options_window.deiconify()
            self.options_window.lift()
            self.options_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.options_window = window
        window.title("设置")
        window.transient(self.root)
        window.resizable(True, True)
        window.minsize(760, 500)

        body = ttk.Frame(window, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1, uniform="settings_columns")
        body.columnconfigure(1, weight=1, uniform="settings_columns")
        body.rowconfigure(0, weight=1)

        left_column = ttk.Frame(body)
        right_column = ttk.Frame(body)
        left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right_column.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        left_column.columnconfigure(0, weight=1)
        right_column.columnconfigure(0, weight=1)

        def add_group(parent: ttk.Frame, title: str, row: int) -> ttk.LabelFrame:
            group = ttk.LabelFrame(parent, text=title, padding=8)
            group.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            group.columnconfigure(1, weight=1)
            return group

        # ---------- 左列：工具、输出、临时目录 ----------
        tools_group = add_group(left_column, "工具路径", 0)
        ttk.Label(tools_group, text="FFmpeg：").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 6))
        ttk.Entry(tools_group, textvariable=self.ffmpeg_path_var, width=36).grid(row=0, column=1, sticky="ew", pady=(0, 6))
        ttk.Button(tools_group, text="选择", command=self._choose_ffmpeg_path).grid(row=0, column=2, padx=(6, 0), pady=(0, 6))
        ttk.Label(tools_group, text="ffprobe：").grid(row=1, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(tools_group, textvariable=self.ffprobe_path_var, width=36).grid(row=1, column=1, sticky="ew")
        ttk.Button(tools_group, text="选择", command=self._choose_ffprobe_path).grid(row=1, column=2, padx=(6, 0))
        ttk.Label(
            tools_group,
            text="路径保存在同名 JSON；压制和严格验证前都会检查工具是否可用。",
            foreground="#666666",
            wraplength=390,
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        output_group = add_group(left_column, "输出与扫描", 1)
        ttk.Label(output_group, text="文件名后缀：").grid(row=0, column=0, sticky="w", padx=(0, 8))
        suffix_entry = ttk.Entry(output_group, textvariable=self.suffix_var, width=22)
        suffix_entry.grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(
            output_group,
            text="扫描子文件夹并保持目录结构",
            variable=self.recursive_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        temp_group = add_group(left_column, "临时文件", 2)
        ttk.Entry(temp_group, textvariable=self.temp_dir_var, width=36).grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 6))
        ttk.Button(temp_group, text="选择", command=self._choose_temp_dir).grid(row=0, column=2)
        ttk.Label(
            temp_group,
            text=f"留空=输入根目录下的 {TEMP_WORK_DIRNAME}；重封装、收益预估和局部测试完成后自动清理。",
            foreground="#666666",
            wraplength=390,
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # ---------- 右列：归类、预估、修复、资源 ----------
        classify_group = add_group(right_column, "结果归类", 0)
        move_success_text: str = f"成功且体积变小后，将原视频移入“{PENDING_DELETE_DIRNAME}”"
        move_no_gain_text: str = f"无压缩收益后，将源视频移入“{NO_GAIN_DIRNAME}”"
        move_failed_text: str = f"最终失败后，将源视频移入“{FAILED_DIRNAME}”"
        ttk.Checkbutton(classify_group, text=move_success_text, variable=self.move_original_var).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(classify_group, text=move_no_gain_text, variable=self.move_no_gain_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Checkbutton(classify_group, text=move_failed_text, variable=self.move_failed_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(
            classify_group,
            text="以上只移动源文件，不直接删除源文件。",
            foreground="#666666",
            wraplength=390,
            justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        precheck_group = add_group(right_column, "压制前判断", 1)
        # add_group 默认让第 1 列扩展，适合路径输入框；这里的数值输入框应保持
        # 固定宽度，单位文字紧随其后，额外空白交给第 3 列吸收。
        precheck_group.columnconfigure(1, weight=0)
        precheck_group.columnconfigure(2, weight=0)
        precheck_group.columnconfigure(3, weight=1)
        ttk.Checkbutton(
            precheck_group,
            text="压制前进行收益预估",
            variable=self.precheck_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(precheck_group, text="预计节省不足：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.precheck_threshold_var = tk.StringVar(value=str(self.settings.get("precheck_min_saving_percent", 8.0)))
        precheck_value_row = ttk.Frame(precheck_group)
        precheck_value_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Entry(precheck_value_row, textvariable=self.precheck_threshold_var, width=7).pack(side=tk.LEFT)
        ttk.Label(precheck_value_row, text="% 时归为无收益").pack(side=tk.LEFT, padx=(4, 0))

        repair_group = add_group(right_column, "错误处理与修复", 2)
        ttk.Checkbutton(
            repair_group,
            text="优先完成正常视频，失败文件最后集中修复",
            variable=self.two_stage_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            repair_group,
            text="失败后定位问题片段并局部测试回退方案",
            variable=self.failure_point_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Checkbutton(
            repair_group,
            text="兼容性回退：允许 lp=4、CRF-1、Preset6",
            variable=self.compatibility_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(
            repair_group,
            text="修复阶段先局部确认方案，能通过错误点后才整片重跑。",
            foreground="#666666",
            wraplength=390,
            justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        resource_group = add_group(right_column, "资源占用", 3)
        resource_group.columnconfigure(1, weight=0)
        resource_group.columnconfigure(2, weight=0)
        resource_group.columnconfigure(3, weight=1)
        ttk.Label(resource_group, text="FFmpeg / ffprobe 优先级：").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        priority_combo = ttk.Combobox(
            resource_group,
            textvariable=self.process_priority_var,
            values=("空闲", "低于正常", "正常"),
            state="readonly",
            width=12,
        )
        priority_combo.grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(
            resource_group,
            text="FFmpeg / ffprobe 最大 CPU 使用率",
            variable=self.cpu_limit_var,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        # 输入阶段只允许空字符串或最多三位数字；保存时还会严格检查 10～100。
        cpu_validate = (window.register(lambda value: value == "" or (value.isdigit() and len(value) <= 3)), "%P")
        cpu_value_row = ttk.Frame(resource_group)
        cpu_value_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        cpu_spinbox = ttk.Spinbox(
            cpu_value_row,
            from_=10,
            to=100,
            increment=1,
            textvariable=self.cpu_limit_percent_var,
            width=6,
            validate="key",
            validatecommand=cpu_validate,
        )
        cpu_spinbox.pack(side=tk.LEFT)
        ttk.Label(cpu_value_row, text="%（整数）").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            resource_group,
            text=(
                "应用于本程序启动的全部 FFmpeg 和 ffprobe 进程；仅限制 Windows "
                "CPU 调度资源，不修改压制参数或验证标准。"
            ),
            foreground="#666666",
            wraplength=390,
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        button_row = ttk.Frame(body)
        button_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        def close_window() -> None:
            # 收益阈值继续允许小数；CPU 上限则只接受 10～100 的整数。
            try:
                threshold = float(self.precheck_threshold_var.get().strip())
                if not math.isfinite(threshold):
                    raise ValueError
            except (TypeError, ValueError):
                messagebox.showerror("设置无效", "收益预估阈值必须是有效数字。", parent=window)
                return

            cpu_text = self.cpu_limit_percent_var.get().strip()
            if not cpu_text.isdigit():
                messagebox.showerror("设置无效", "CPU 最大使用率必须是 10～100 的整数。", parent=window)
                return
            cpu_percent = int(cpu_text)
            if not 10 <= cpu_percent <= 100:
                messagebox.showerror("设置无效", "CPU 最大使用率必须位于 10～100。", parent=window)
                return

            self.settings["precheck_min_saving_percent"] = threshold
            self.settings["cpu_limit_percent"] = cpu_percent
            self.cpu_limit_percent_var.set(str(cpu_percent))
            self._persist_settings()
            try:
                window.grab_release()
            except tk.TclError:
                pass
            self.options_window = None
            window.destroy()

        ttk.Button(button_row, text="保存并关闭", command=close_window).pack(side=tk.RIGHT)
        window.protocol("WM_DELETE_WINDOW", close_window)
        window.update_idletasks()

        screen_width = max(window.winfo_screenwidth(), 800)
        screen_height = max(window.winfo_screenheight(), 600)
        requested_width = max(900, window.winfo_reqwidth())
        requested_height = max(540, window.winfo_reqheight())
        width = min(requested_width, max(screen_width - 40, 760))
        height = min(requested_height, max(screen_height - 80, 500))
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 3, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.grab_set()
        suffix_entry.focus_set()

    def _open_extra_features(self) -> None:
        """打开额外功能托管窗口。

        这里仅放不适合进入主压制流程、但对无人值守归档具有明确价值的独立工具。
        首批功能分开呈现，避免用户把“检查任意源视频”和“检查本工具 AV1 成品”混为一谈。
        """
        if self.extra_features_window is not None and self.extra_features_window.winfo_exists():
            self.extra_features_window.deiconify()
            self.extra_features_window.lift()
            self.extra_features_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.extra_features_window = window
        window.title("额外功能")
        window.transient(self.root)
        window.resizable(True, True)
        window.minsize(700, 500)

        body = ttk.Frame(window, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        source_group = ttk.LabelFrame(body, text="严格验证源视频", padding=10)
        source_group.grid(row=0, column=0, sticky="ew")
        source_group.columnconfigure(0, weight=1)
        ttk.Label(
            source_group,
            text=(
                "对单个文件或递归文件夹执行严格检查。先由 ffprobe 完整遍历容器和数据包，"
                "再由 FFmpeg 完整解码动态视频流和音频流；真实解码错误与时间戳警告会分开报告。"
            ),
            wraplength=600,
            justify=tk.LEFT,
        ).grid(row=0, column=0, sticky="w")
        source_buttons = ttk.Frame(source_group)
        source_buttons.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.extra_source_file_button = ttk.Button(
            source_buttons,
            text="选择单个文件",
            command=lambda: self._start_extra_validation("source", "file"),
        )
        self.extra_source_file_button.pack(side=tk.LEFT)
        self.extra_source_folder_button = ttk.Button(
            source_buttons,
            text="选择文件夹",
            command=lambda: self._start_extra_validation("source", "folder"),
        )
        self.extra_source_folder_button.pack(side=tk.LEFT, padx=(6, 0))

        av1_group = ttk.LabelFrame(body, text="严格验证 AV1 转码成品", padding=10)
        av1_group.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        av1_group.columnconfigure(0, weight=1)
        ttk.Label(
            av1_group,
            text=(
                "对单个文件或递归文件夹中的 MKV 成品执行检查：要求 Matroska、唯一 AV1 10-bit 主视频、"
                "全部音轨为 Opus、存在本工具来源标记，并严格完整解码视频和音频。"
            ),
            wraplength=600,
            justify=tk.LEFT,
        ).grid(row=0, column=0, sticky="w")
        av1_buttons = ttk.Frame(av1_group)
        av1_buttons.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.extra_av1_file_button = ttk.Button(
            av1_buttons,
            text="选择单个文件",
            command=lambda: self._start_extra_validation("av1", "file"),
        )
        self.extra_av1_file_button.pack(side=tk.LEFT)
        self.extra_av1_folder_button = ttk.Button(
            av1_buttons,
            text="选择文件夹",
            command=lambda: self._start_extra_validation("av1", "folder"),
        )
        self.extra_av1_folder_button.pack(side=tk.LEFT, padx=(6, 0))

        status_group = ttk.LabelFrame(body, text="验证进度", padding=8)
        status_group.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status_group.columnconfigure(0, weight=1)
        self.extra_validation_status_var = tk.StringVar(value="等待选择验证功能")
        self.extra_validation_current_var = tk.StringVar(value="")
        ttk.Label(status_group, textvariable=self.extra_validation_status_var, justify=tk.LEFT).grid(row=0, column=0, sticky="w")
        ttk.Label(
            status_group,
            textvariable=self.extra_validation_current_var,
            justify=tk.LEFT,
            wraplength=650,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.extra_validation_progress = ttk.Progressbar(status_group, maximum=100)
        self.extra_validation_progress.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.extra_validation_stop_button = ttk.Button(
            status_group,
            text="停止验证",
            command=self._stop_extra_validation,
            state=tk.DISABLED,
        )
        self.extra_validation_stop_button.grid(row=0, column=1, rowspan=3, sticky="e", padx=(8, 0))

        log_group = ttk.LabelFrame(body, text="本次验证日志", padding=6)
        log_group.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        log_group.columnconfigure(0, weight=1)
        log_group.rowconfigure(0, weight=1)
        self.extra_validation_log = ScrolledText(log_group, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.extra_validation_log.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            body,
            text=(
                "发现严重问题时生成问题清单；仅发现重复/非严格递增 DTS 时生成警告报告；"
                "完全通过时不创建文件。"
            ),
            foreground="#666666",
        ).grid(row=4, column=0, sticky="w", pady=(6, 0))

        def close_window() -> None:
            if self._extra_validation_is_running():
                messagebox.showwarning("验证进行中", "请先停止或等待当前严格验证完成。", parent=window)
                return
            self.extra_features_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_window)
        window.update_idletasks()
        width = min(max(820, window.winfo_reqwidth()), max(window.winfo_screenwidth() - 40, 700))
        height = min(max(560, window.winfo_reqheight()), max(window.winfo_screenheight() - 80, 500))
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - width) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - height) // 4, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _extra_validation_is_running(self) -> bool:
        worker = self.extra_validation_worker
        return worker is not None and worker.is_alive()

    def _set_extra_validation_running(self, running: bool) -> None:
        """同步主窗口和额外功能窗口的按钮状态，防止转码与严格验证抢占同一 FFmpeg 状态。"""
        if hasattr(self, "extra_source_file_button"):
            state = tk.DISABLED if running else tk.NORMAL
            for button in (
                self.extra_source_file_button,
                self.extra_source_folder_button,
                self.extra_av1_file_button,
                self.extra_av1_folder_button,
            ):
                button.configure(state=state)
            self.extra_validation_stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            self.start_button.configure(state=tk.DISABLED)
            self.optional_button.configure(state=tk.DISABLED)
            self.extra_features_button.configure(state=tk.DISABLED)
        elif not self.running:
            self.start_button.configure(state=tk.NORMAL)
            self.optional_button.configure(state=tk.NORMAL)
            self.extra_features_button.configure(state=tk.NORMAL)

    def _append_extra_validation_log(self, text: str) -> None:
        if not hasattr(self, "extra_validation_log"):
            return
        try:
            self.extra_validation_log.configure(state=tk.NORMAL)
            self.extra_validation_log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {text}\n")
            self.extra_validation_log.see(tk.END)
            self.extra_validation_log.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _start_extra_validation(self, mode: str, target_kind: str) -> None:
        """启动单文件或文件夹严格验证。

        两种入口共用完全相同的结构检查、完整解码和报告生成代码，区别仅在于
        候选集合与报告命名，避免单文件验证和批量验证逐渐出现不同判定标准。
        """
        if self.running:
            messagebox.showwarning("主任务正在运行", "请先结束当前压制任务，再执行严格验证。", parent=self.extra_features_window)
            return
        if self._extra_validation_is_running():
            messagebox.showwarning("验证正在运行", "当前已有严格验证任务。", parent=self.extra_features_window)
            return
        if target_kind not in {"file", "folder"}:
            raise ValueError(f"未知严格验证目标类型：{target_kind}")

        # 用户可能刚在设置窗口修改工具路径；先持久化并重新解析，再启动独立验证。
        self._persist_settings()
        if not self.ffmpeg or not self.ffprobe:
            messagebox.showerror("缺少工具路径", "请先在“设置”中选择 FFmpeg 和 ffprobe。", parent=self.extra_features_window)
            return
        for executable, label in ((self.ffmpeg, "FFmpeg"), (self.ffprobe, "ffprobe")):
            path = Path(executable)
            if not path.is_file():
                messagebox.showerror("工具路径无效", f"{label} 不存在：\n{path}", parent=self.extra_features_window)
                return

        if target_kind == "file":
            if mode == "source":
                filetypes = [
                    ("视频文件", " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))),
                    ("所有文件", "*.*"),
                ]
                title = "选择需要严格验证的源视频"
            else:
                filetypes = [("Matroska 视频", "*.mkv"), ("所有文件", "*.*")]
                title = "选择需要严格验证的 AV1 成品"
            selected = filedialog.askopenfilename(
                title=title,
                filetypes=filetypes,
                parent=self.extra_features_window,
            )
            if not selected:
                return
            selected_target = Path(selected).resolve()
            try:
                if not selected_target.is_file():
                    raise OSError("所选路径不是普通文件")
            except OSError as exc:
                messagebox.showerror("文件无效", f"无法读取所选文件：\n{selected_target}\n\n{exc}", parent=self.extra_features_window)
                return
            root = selected_target.parent
            candidates = [selected_target]
        else:
            title = "选择需要严格验证的源视频文件夹" if mode == "source" else "选择需要严格验证的 AV1 成品文件夹"
            selected = filedialog.askdirectory(title=title, parent=self.extra_features_window)
            if not selected:
                return
            selected_target = Path(selected).resolve()
            root = selected_target
            candidates = self._collect_strict_validation_candidates(root, mode)
            if not candidates:
                label = "受支持视频" if mode == "source" else "MKV 成品"
                messagebox.showinfo("没有文件", f"所选文件夹内没有找到{label}。", parent=self.extra_features_window)
                return

        self.extra_validation_stop_event.clear()
        self.extra_validation_progress["value"] = 0
        self.extra_validation_status_var.set(f"准备验证 {len(candidates)} 个文件")
        self.extra_validation_current_var.set(str(selected_target))
        self.extra_validation_log.configure(state=tk.NORMAL)
        self.extra_validation_log.delete("1.0", tk.END)
        self.extra_validation_log.configure(state=tk.DISABLED)
        self._set_extra_validation_running(True)
        scope_label = "单个文件" if target_kind == "file" else "文件夹"
        self._append_extra_validation_log(
            f"开始{'源视频' if mode == 'source' else 'AV1 成品'}严格验证（{scope_label}）："
            f"{selected_target}；共 {len(candidates)} 个文件。"
        )

        self.extra_validation_worker = threading.Thread(
            target=self._strict_validation_worker,
            args=(root, mode, candidates, target_kind, selected_target),
            daemon=True,
        )
        self.extra_validation_worker.start()

    def _stop_extra_validation(self) -> None:
        if not self._extra_validation_is_running():
            return
        self.extra_validation_stop_event.set()
        self._append_extra_validation_log("收到停止请求，正在终止当前验证进程……")
        threading.Thread(target=self._terminate_extra_validation_process, daemon=True).start()

    def _collect_strict_validation_candidates(self, root: Path, mode: str) -> list[Path]:
        results: list[Path] = []
        excluded_dirs = {TEMP_WORK_DIRNAME.casefold()}
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name.casefold() not in excluded_dirs]
            base = Path(current_root)
            for filename in filenames:
                path = base / filename
                lower = filename.casefold()
                if any(token in lower for token in (".__processing__.", ".__remux_retry__.", ".invalid_")):
                    continue
                if mode == "source":
                    if path.suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                else:
                    if path.suffix.lower() != ".mkv":
                        continue
                try:
                    if path.is_file():
                        results.append(path.resolve())
                except OSError:
                    continue
        return sorted(results, key=lambda item: os.path.normcase(str(item)))

    def _strict_validation_worker(
        self,
        root: Path,
        mode: str,
        candidates: list[Path],
        target_kind: str,
        selected_target: Path,
    ) -> None:
        findings: list[dict[str, str]] = []
        issue_file_count = 0
        warning_file_count = 0
        stopped = False
        checked_count = 0
        try:
            # 严格验证需要 ffprobe 和 FFmpeg 两个工具都可运行；这里独立检查，避免主压制环境检查的输出目录要求。
            for executable, label in ((self.ffprobe, "ffprobe"), (self.ffmpeg, "FFmpeg")):
                return_code, stdout_text, stderr_text, timed_out = self._run_extra_capture(
                    [executable, "-version"],
                    timeout_seconds=30,
                )
                if timed_out:
                    raise RuntimeError(f"{label} 启动检查超时。")
                if return_code != 0:
                    detail = (stdout_text + "\n" + stderr_text).strip()
                    raise RuntimeError(f"{label} 无法运行：{detail}")

            total = len(candidates)
            for index, path in enumerate(candidates, start=1):
                if self.extra_validation_stop_event.is_set():
                    stopped = True
                    break
                self.ui_queue.put(("extra_validation_progress", (index - 1, total, path)))
                try:
                    file_issues, file_warnings = self._strict_validate_single_file(path, mode)
                except InterruptedError:
                    stopped = True
                    break
                checked_count = index
                if file_issues or file_warnings:
                    if file_issues:
                        issue_file_count += 1
                    if file_warnings:
                        warning_file_count += 1
                    findings.append({
                        "path": str(path),
                        "relative": self._safe_relative_text(path, root),
                        "issues": "\n".join(file_issues),
                        "warnings": "\n".join(file_warnings),
                    })
                    if file_issues:
                        self.ui_queue.put(("extra_validation_log", f"发现严重问题：{self._safe_relative_text(path, root)}"))
                        for detail in file_issues:
                            self.ui_queue.put(("extra_validation_log", f"  - {detail}"))
                    if file_warnings:
                        self.ui_queue.put(("extra_validation_log", f"发现警告：{self._safe_relative_text(path, root)}"))
                        for detail in file_warnings:
                            self.ui_queue.put(("extra_validation_log", f"  - {detail}"))
                else:
                    self.ui_queue.put(("extra_validation_log", f"通过：{self._safe_relative_text(path, root)}"))
                self.ui_queue.put(("extra_validation_progress", (index, total, path)))

            report_path: Optional[Path] = None
            if findings:
                report_path = self._write_strict_validation_report(
                    root,
                    mode,
                    candidates,
                    findings,
                    stopped,
                    target_kind,
                    selected_target,
                    issue_file_count,
                    warning_file_count,
                )
            self.ui_queue.put(("extra_validation_done", {
                "mode": mode,
                "total": len(candidates),
                "checked": checked_count,
                "issues": issue_file_count,
                "warnings": warning_file_count,
                "stopped": stopped,
                "report": str(report_path) if report_path else "",
            }))
        except InterruptedError:
            report_path: Optional[Path] = None
            if findings:
                report_path = self._write_strict_validation_report(
                    root,
                    mode,
                    candidates,
                    findings,
                    True,
                    target_kind,
                    selected_target,
                    issue_file_count,
                    warning_file_count,
                )
            self.ui_queue.put(("extra_validation_done", {
                "mode": mode,
                "total": len(candidates),
                "checked": checked_count,
                "issues": issue_file_count,
                "warnings": warning_file_count,
                "stopped": True,
                "report": str(report_path) if report_path else "",
            }))
        except Exception as exc:
            self.logger.exception("额外功能严格验证异常")
            self.ui_queue.put(("extra_validation_failed", str(exc)))

    @staticmethod
    def _safe_relative_text(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    def _strict_validate_single_file(self, path: Path, mode: str) -> tuple[list[str], list[str]]:
        issues: list[str] = []
        warnings: list[str] = []
        probe, probe_issues = self._strict_ffprobe_file(path)
        issues.extend(probe_issues)
        if probe is None:
            return issues, warnings

        streams = probe.get("streams") or []
        format_info = probe.get("format") or {}
        dynamic_videos = [
            stream for stream in streams
            if stream.get("codec_type") == "video"
            and parse_int((stream.get("disposition") or {}).get("attached_pic"), 0) == 0
        ]
        attached_pictures = [
            stream for stream in streams
            if stream.get("codec_type") == "video"
            and parse_int((stream.get("disposition") or {}).get("attached_pic"), 0) == 1
        ]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]

        duration = parse_float(format_info.get("duration"), 0.0)
        if duration <= 0 and dynamic_videos:
            duration = max((parse_float(stream.get("duration"), 0.0) for stream in dynamic_videos), default=0.0)
        if duration <= 0:
            issues.append("ffprobe 无法确定有效视频时长。")

        if not dynamic_videos:
            issues.append("没有动态主视频流（attached_pic 封面不计入主视频）。")
        elif mode == "source" and len(dynamic_videos) > 1:
            issues.append(f"包含 {len(dynamic_videos)} 个动态视频流；当前主压制流程会为避免选错画面而跳过。")

        for stream in dynamic_videos:
            index = parse_int(stream.get("index"), -1)
            codec = str(stream.get("codec_name", "")).strip() or "未知"
            width = parse_int(stream.get("width"), 0)
            height = parse_int(stream.get("height"), 0)
            packets = parse_int(stream.get("nb_read_packets"), -1)
            if width <= 0 or height <= 0:
                issues.append(f"视频流 #{index}（{codec}）分辨率无效：{width}×{height}。")
            if packets == 0:
                issues.append(f"视频流 #{index} 没有可读取的数据包。")

        for stream in audio_streams:
            index = parse_int(stream.get("index"), -1)
            packets = parse_int(stream.get("nb_read_packets"), -1)
            if packets == 0:
                issues.append(f"音频流 #{index} 没有可读取的数据包。")

        if mode == "av1":
            format_name = str(format_info.get("format_name", "")).lower()
            if "matroska" not in format_name:
                issues.append(f"容器不是本工具预期的 Matroska：{format_name or '未知'}。")
            if path.suffix.lower() != ".mkv":
                issues.append("文件扩展名不是 .mkv。")
            if len(dynamic_videos) != 1:
                issues.append(f"AV1 成品应只有一个动态主视频流，实际为 {len(dynamic_videos)}。")
            elif dynamic_videos:
                video = dynamic_videos[0]
                codec = str(video.get("codec_name", "")).lower()
                if codec != "av1":
                    issues.append(f"主视频编码不是 AV1：{codec or '未知'}。")
                bit_depth = infer_bit_depth(video)
                if bit_depth < 10:
                    issues.append(f"主视频不是 10-bit：{video.get('pix_fmt', '未知')}。")
            for stream in audio_streams:
                codec = str(stream.get("codec_name", "")).lower()
                if codec != "opus":
                    issues.append(f"音频流 #{parse_int(stream.get('index'), -1)} 不是 Opus：{codec or '未知'}。")
            if attached_pictures:
                issues.append(f"成品中意外保留了 {len(attached_pictures)} 个 attached_pic 封面流。")

            tags = {str(key).casefold(): str(value) for key, value in ((format_info.get("tags") or {}).items())}
            required_tags = {
                "av1tool_source_size": "源文件大小",
                "av1tool_source_mtime_ns": "源文件修改时间",
                "av1tool_source_relative": "源文件相对路径",
                "av1tool_source_fingerprint": "源文件快速指纹",
                "av1tool_fingerprint_version": "指纹版本",
                "av1tool_policy_version": "编码策略版本",
            }
            missing_tags = [label for key, label in required_tags.items() if not tags.get(key, "").strip()]
            if missing_tags:
                issues.append("缺少本工具成品来源标记：" + "、".join(missing_tags) + "。")

        # 即使结构检查已经发现问题，也继续尝试严格解码；这样一份清单能同时给出
        # “格式不符合预期”和“码流是否真正损坏”两类结论，避免用户修完一项后再跑一遍。
        if dynamic_videos:
            decode_issues, decode_warnings = self._strict_decode_file(
                path,
                dynamic_videos,
                audio_streams,
                expected_duration=duration if duration > 0 else None,
            )
            issues.extend(decode_issues)
            warnings.extend(decode_warnings)
        return issues, warnings

    def _strict_ffprobe_file(self, path: Path) -> tuple[Optional[dict[str, Any]], list[str]]:
        """让 ffprobe 完整遍历容器数据包，并返回结构问题。

        ``-count_packets`` 会让 ffprobe 读取到文件尾，而不是只解析文件头；输出仍只包含
        每条流的计数和结构信息，不会像 ``-show_packets`` 那样生成巨大 JSON。
        """
        command = [
            self.ffprobe,
            "-hide_banner",
            "-v", "error",
            "-show_error",
            "-count_packets",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-print_format", "json",
            str(path),
        ]
        try:
            return_code, stdout_text, stderr_text, timed_out = self._run_extra_capture(
                command,
                timeout_seconds=STRICT_PROBE_TIMEOUT_SECONDS,
            )
        except InterruptedError:
            raise
        issues: list[str] = []
        if timed_out:
            return None, [f"ffprobe 超过 {int(STRICT_PROBE_TIMEOUT_SECONDS // 3600)} 小时仍未完成。"]
        if return_code != 0:
            detail = stderr_text.strip() or f"返回码 {return_code}"
            return None, [f"ffprobe 无法完整读取文件：{detail[-1500:]}"]
        if stderr_text.strip():
            issues.append("ffprobe 报告错误信息：" + stderr_text.strip()[-1500:])
        try:
            probe = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            return None, [f"ffprobe JSON 无法解析：{exc}"]
        error_info = probe.get("error")
        if isinstance(error_info, dict) and error_info:
            message = str(error_info.get("string", error_info))
            issues.append(f"ffprobe error 节点：{message}")
        if not probe.get("streams"):
            issues.append("ffprobe 未发现任何媒体流。")
        return probe, issues

    def _run_extra_capture(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> tuple[int, str, str, bool]:
        """运行额外功能中的短输出命令，并支持停止和超时。

        ffprobe 的 JSON 只在结束时输出，不能用主转码的逐行进度读取器；这里用
        communicate 后台线程避免管道堵塞，同时主线程轮询停止事件和总超时。
        """
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_options(),
        )
        # 额外功能中的 ffprobe 可能通过 -count_packets 长时间遍历整个文件，因此也必须
        # 使用与 FFmpeg 相同的 Windows 优先级和 CPU 硬上限。资源控制只影响调度速度，
        # 不改变 ffprobe 的读取范围、结构判定或严格解码标准。
        job_handle = self._safe_apply_windows_resource_limits(process)
        with self.extra_validation_process_lock:
            self.extra_validation_process = process
            self.extra_validation_job_handle = job_handle

        result_box: list[tuple[str, str]] = []
        done = threading.Event()

        def communicate() -> None:
            try:
                stdout_text, stderr_text = process.communicate()
                result_box.append((stdout_text, stderr_text))
            finally:
                done.set()

        reader = threading.Thread(target=communicate, daemon=True)
        reader.start()
        start_time = time.monotonic()
        timed_out = False
        try:
            while not done.wait(0.25):
                if self.extra_validation_stop_event.is_set():
                    self._terminate_extra_validation_process()
                    raise InterruptedError
                if time.monotonic() - start_time > timeout_seconds:
                    timed_out = True
                    self._terminate_extra_validation_process()
                    break
            reader.join(timeout=5)
            if result_box:
                stdout_text, stderr_text = result_box[-1]
            else:
                stdout_text, stderr_text = "", ""
            return process.poll() if process.poll() is not None else -1, stdout_text, stderr_text, timed_out
        finally:
            with self.extra_validation_process_lock:
                if self.extra_validation_process is process:
                    self.extra_validation_process = None
                if self.extra_validation_job_handle == job_handle:
                    self.extra_validation_job_handle = None
            self._close_windows_job_handle(job_handle)

    def _strict_decode_file(
        self,
        path: Path,
        video_streams: list[dict[str, Any]],
        audio_streams: list[dict[str, Any]],
        *,
        expected_duration: Optional[float],
    ) -> tuple[list[str], list[str]]:
        """完整解码动态视频和音频，并把严重错误与时间戳警告分开。

        这里故意不再使用 ``-xerror``。原因是 null 输出器报告重复或非严格递增
        DTS 时，``-xerror`` 会在第一个时间戳警告处提前中止，导致后半段视频根本
        没有接受验证。严格程度改由以下组合保证：

        - ``-err_detect explode``：让解码器对码流错误采取严格策略；
        - ``-max_error_rate 0``：不允许可接受的解码错误比例；
        - 完整收集 ``-v error`` 输出，并把任何非时间戳错误列为严重问题；
        - 比较最终解码位置与容器时长，防止提前结束被误判为通过。

        这样纯 DTS 警告可以继续验证到文件末尾，而真实解码损坏、截断、卡死、
        非零退出和提前结束仍然不会被放行。
        """
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v", "error",
            "-err_detect", "explode",
            "-max_error_rate", "0",
            "-stats_period", "1",
            "-progress", "pipe:1",
            "-i", str(path),
        ]
        for stream in video_streams:
            command += ["-map", f"0:{parse_int(stream.get('index'), 0)}"]
        for stream in audio_streams:
            command += ["-map", f"0:{parse_int(stream.get('index'), 0)}"]
        command += ["-sn", "-dn", "-f", "null", "-"]

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
        job_handle = self._safe_apply_windows_resource_limits(process)
        with self.extra_validation_process_lock:
            self.extra_validation_process = process
            self.extra_validation_job_handle = job_handle

        # 额外验证可能遇到同一时间戳警告成千上万次。这里边读边分类：
        # - DTS 警告只累计数量并保留少量样例；
        # - 其他 stderr 最多保留 4000 行；
        # 从而既能给出准确计数，又不会让无人值守验证无限占用内存。
        timestamp_warning_count = 0
        timestamp_warning_samples: list[str] = []
        other_stderr_lines: list[str] = []

        def collect_strict_stderr() -> None:
            nonlocal timestamp_warning_count
            if process.stderr is None:
                return
            for raw_line in process.stderr:
                clean = raw_line.rstrip()
                if not clean:
                    continue
                if is_strict_timestamp_warning(clean):
                    timestamp_warning_count += 1
                    if len(timestamp_warning_samples) < STRICT_TIMESTAMP_SAMPLE_LIMIT:
                        timestamp_warning_samples.append(clean)
                    continue
                other_stderr_lines.append(clean)
                if len(other_stderr_lines) > 4000:
                    del other_stderr_lines[:1000]

        stderr_thread = threading.Thread(target=collect_strict_stderr, daemon=True)
        stderr_thread.start()
        stdout_queue: queue.Queue[str] = queue.Queue()
        stdout_done = threading.Event()

        def collect_progress() -> None:
            try:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    stdout_queue.put(line)
            finally:
                stdout_done.set()

        stdout_thread = threading.Thread(target=collect_progress, daemon=True)
        stdout_thread.start()
        last_seconds = 0.0
        last_progress_change = time.monotonic()
        watchdog_triggered = False
        try:
            while True:
                if self.extra_validation_stop_event.is_set():
                    self._terminate_extra_validation_process()
                    raise InterruptedError
                try:
                    line = stdout_queue.get(timeout=0.5).strip()
                except queue.Empty:
                    line = ""
                if "=" in line:
                    key, value = line.split("=", 1)
                    current_seconds = last_seconds
                    if key == "out_time":
                        current_seconds = parse_ffmpeg_time(value)
                    elif key == "out_time_ms":
                        try:
                            current_seconds = int(value) / 1_000_000.0
                        except ValueError:
                            current_seconds = last_seconds
                    if current_seconds > last_seconds + 0.05:
                        last_seconds = current_seconds
                        last_progress_change = time.monotonic()

                if process.poll() is None and time.monotonic() - last_progress_change > STRICT_VERIFY_STALL_TIMEOUT_SECONDS:
                    watchdog_triggered = True
                    self._terminate_extra_validation_process()
                    break
                if process.poll() is not None and stdout_done.is_set() and stdout_queue.empty():
                    break

            return_code = process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            issues: list[str] = []
            warnings: list[str] = []
            if watchdog_triggered:
                issues.append(
                    f"严格完整解码在 {format_duration(last_seconds)} 处连续 "
                    f"{int(STRICT_VERIFY_STALL_TIMEOUT_SECONDS)} 秒无进度。"
                )
                return issues, warnings

            serious_lines = list(other_stderr_lines)
            if timestamp_warning_count:
                serious_lines = [
                    line for line in serious_lines
                    if not any(
                        pattern in line.casefold()
                        for pattern in STRICT_TIMESTAMP_DERIVATIVE_PATTERNS
                    )
                ]
            if serious_lines:
                detail = "\n".join(serious_lines).strip()[-3000:]
                issues.append(f"严格完整解码报告严重错误（约 {format_duration(last_seconds)}）：{detail}")

            # 时长检查放在时间戳警告分类之前。只有确实走到文件末尾，报告才可以明确写
            # “已继续检查到文件末尾”；否则必须把提前结束保留为严重问题。
            reached_end = True
            if expected_duration and expected_duration > 0:
                tolerance = max(5.0, expected_duration * 0.01)
                reached_end = last_seconds + tolerance >= expected_duration
                if not reached_end:
                    issues.append(
                        f"严格解码疑似提前结束：解码到 {format_duration(last_seconds)}，"
                        f"容器时长为 {format_duration(expected_duration)}。"
                    )

            if timestamp_warning_count:
                warning_text = format_timestamp_warning(
                    timestamp_warning_count,
                    timestamp_warning_samples,
                    last_seconds,
                )
                if not reached_end:
                    warning_text = warning_text.replace(
                        "已继续检查到文件末尾。",
                        "但本次验证没有到达文件末尾，需结合上方严重问题处理。",
                    )
                warnings.append(warning_text)

            if return_code != 0:
                # 某些 FFmpeg 构建会因为 null 输出端的 DTS 问题返回非零，即使媒体内容
                # 已经完整解码。只有在“无其他错误 + 已到末尾 + 存在明确 DTS 警告”时
                # 才降级；其他非零退出仍然是严重问题。
                timestamp_only_exit = bool(timestamp_warning_count) and not serious_lines and reached_end
                if timestamp_only_exit:
                    warnings.append(
                        f"FFmpeg 最终返回码为 {return_code}，但仅检测到上述时间戳警告，"
                        "且媒体已完整检查到末尾；本项按警告处理。"
                    )
                else:
                    issues.append(f"严格完整解码失败：FFmpeg 返回码 {return_code}。")

            return issues, warnings
        finally:
            with self.extra_validation_process_lock:
                if self.extra_validation_process is process:
                    self.extra_validation_process = None
                if self.extra_validation_job_handle == job_handle:
                    self.extra_validation_job_handle = None
            self._close_windows_job_handle(job_handle)

    def _terminate_extra_validation_process(self) -> None:
        with self.extra_validation_process_lock:
            process = self.extra_validation_process
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

    def _write_strict_validation_report(
        self,
        root: Path,
        mode: str,
        candidates: list[Path],
        findings: list[dict[str, str]],
        stopped: bool,
        target_kind: str,
        selected_target: Path,
        issue_file_count: int,
        warning_file_count: int,
    ) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        has_serious_issues = issue_file_count > 0
        if mode == "source":
            base_name = "源视频严格验证问题清单" if has_serious_issues else "源视频严格验证警告报告"
        else:
            base_name = "AV1成品严格验证问题清单" if has_serious_issues else "AV1成品严格验证警告报告"
        if target_kind == "file":
            if mode == "source":
                report_suffix = "源视频严格验证问题" if has_serious_issues else "源视频严格验证警告"
            else:
                report_suffix = "AV1成品严格验证问题" if has_serious_issues else "AV1成品严格验证警告"
            report_name = f"{selected_target.name}.{report_suffix}.txt"
            report_path = selected_target.parent / report_name
        else:
            report_name = f"{base_name}_{timestamp}.txt"
            report_path = root / report_name
        try:
            test_path = root / f".__av1tool_report_test_{os.getpid()}"
            test_path.write_bytes(b"ok")
            test_path.unlink()
        except OSError:
            report_path = SCRIPT_DIR / report_name

        if target_kind == "file":
            if mode == "source":
                report_title = "源视频严格验证问题" if has_serious_issues else "源视频严格验证警告"
            else:
                report_title = "AV1成品严格验证问题" if has_serious_issues else "AV1成品严格验证警告"
        else:
            report_title = base_name
        lines = [
            report_title,
            "=" * 72,
            f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            (f"验证文件：{selected_target}" if target_kind == "file" else f"验证根目录：{root}"),
            f"候选文件数：{len(candidates)}",
            f"严重问题文件数：{issue_file_count}",
            f"警告文件数：{warning_file_count}",
            f"验证状态：{'用户中止，报告可能不完整' if stopped else '完整结束'}",
            "",
            "验证方式：",
            "1. ffprobe -count_packets 完整遍历容器数据包并检查结构；",
            "2. FFmpeg 使用 -err_detect explode、-max_error_rate 0 完整解码动态视频流和音频流；",
            "   重复或非严格递增 DTS 单独列为时间戳警告，不再中断后续完整解码；",
            "3. AV1 成品额外核对 Matroska、AV1 10-bit、Opus 和 AV1TOOL 来源标记。",
            "",
        ]
        for number, finding in enumerate(findings, start=1):
            lines.extend([
                f"[{number}] {finding['relative']}",
                f"完整路径：{finding['path']}",
            ])
            serious_text = finding.get("issues", "").strip()
            warning_text = finding.get("warnings", "").strip()
            if serious_text:
                lines.append("严重问题：")
                lines.extend(f"  {line}" for line in serious_text.splitlines())
            if warning_text:
                lines.append("警告：")
                lines.extend(f"  {line}" for line in warning_text.splitlines())
            lines.append("")
        try:
            report_path.write_text("\n".join(lines), encoding="utf-8-sig")
        except OSError:
            fallback = SCRIPT_DIR / report_name
            if fallback == report_path:
                raise
            fallback.write_text("\n".join(lines), encoding="utf-8-sig")
            report_path = fallback
        return report_path

    def _choose_ffmpeg_path(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 ffmpeg 可执行文件",
            filetypes=[("FFmpeg", "ffmpeg.exe" if os.name == "nt" else "ffmpeg"), ("所有文件", "*")],
        )
        if path:
            self.ffmpeg_path_var.set(path)

    def _choose_ffprobe_path(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 ffprobe 可执行文件",
            filetypes=[("ffprobe", "ffprobe.exe" if os.name == "nt" else "ffprobe"), ("所有文件", "*")],
        )
        if path:
            self.ffprobe_path_var.set(path)

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
        if self._extra_validation_is_running():
            messagebox.showwarning("严格验证正在运行", "请先停止或等待额外功能中的严格验证完成。")
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

        # 先保存设置并验证临时目录。所有可能直接 return 的检查都必须发生在
        # self.running=True 之前，否则一次输入错误就会让按钮永久停留在“运行中”。
        self._persist_settings()
        temp_dir_text = self.temp_dir_var.get().strip()
        if temp_dir_text:
            try:
                temp_dir = self._resolve_temp_dir_text(temp_dir_text)
                temp_dir.mkdir(parents=True, exist_ok=True)
                test_file = temp_dir / f".__{APP_STEM}_temp_write_test_{os.getpid()}"
                test_file.write_bytes(b"ok")
                test_file.unlink()
            except (OSError, ValueError) as exc:
                messagebox.showerror("临时目录错误", f"临时工作目录不可写：\n{temp_dir_text}\n\n{exc}")
                return

        self.running = True
        self.stop_event.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.optional_button.configure(state=tk.DISABLED)
        self.extra_features_button.configure(state=tk.DISABLED)
        self.current_progress["value"] = 0
        self.overall_progress["value"] = 0
        self.current_text.set("正在检查环境……")
        self.current_detail_text.set("")
        self.overall_text.set("准备任务")
        self.task_tree.delete(*self.task_tree.get_children())
        self.tree_items.clear()

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
        self.log("收到停止请求，正在结束当前媒体工具进程……", logging.WARNING)
        self.current_text.set("正在停止……")
        self.current_detail_text.set("")
        threading.Thread(target=self._terminate_current_process, daemon=True).start()

    def _log_resource_limit_warning_once(self, key: str, message: str) -> None:
        """资源控制失败时只警告一次，避免批量 FFmpeg/ffprobe 子任务重复刷屏。"""
        if key in self.resource_limit_warning_keys:
            return
        self.resource_limit_warning_keys.add(key)
        self.log(message, logging.WARNING)

    @staticmethod
    def _windows_handle_to_int(handle: Any) -> Optional[int]:
        """把 ctypes 返回的 HANDLE 安全转换成可长期保存的整数。

        ``ctypes`` 在不同 Python/Windows 组合中可能把 HANDLE 返回为：
        - 直接的 Python ``int``；
        - 带 ``.value`` 的 ``c_void_p``；
        - 空句柄 ``None``。

        旧代码无条件访问 ``job_handle.value``，当返回值已经是 ``int`` 时会在
        Windows API 调用成功之后抛出 AttributeError，导致程序误以为资源控制失败，
        同时也无法正确保存和关闭 Job Object 句柄。
        """
        if handle is None:
            return None
        value = getattr(handle, "value", handle)
        if value is None:
            return None
        try:
            converted = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return converted if converted != 0 else None

    @staticmethod
    def _media_tool_label_from_process(process: subprocess.Popen[str]) -> str:
        """根据子进程命令判断资源日志中应显示 FFmpeg 还是 ffprobe。"""
        args = getattr(process, "args", None)
        executable: Any = args[0] if isinstance(args, (list, tuple)) and args else args
        name = Path(str(executable or "")).name.casefold()
        return "ffprobe" if "ffprobe" in name else "FFmpeg"

    def _apply_windows_resource_limits(
        self,
        process: subprocess.Popen[str],
    ) -> Optional[int]:
        """把进程优先级和 CPU 硬上限应用到刚启动的 FFmpeg 或 ffprobe，并读回验证。

        这里不会修改媒体工具命令，也不向 SVT-AV1 自动加入 ``lp``：
        - 优先级由 SetPriorityClass 调整；
        - CPU 上限由独立 Job Object 的硬上限实现；
        - AssignProcessToJobObject 成功后，必须再用 IsProcessInJob 确认进程归属；
        - 必须用 QueryInformationJobObject 读回 ControlFlags 与 CpuRate，配置回显
          不能再冒充“限制已经生效”；
        - 任一 Windows API 调用失败都只记录警告并继续执行，不能让可选的资源
          控制功能破坏无人值守批次。

        返回值是必须保持到进程退出的 Job Object 句柄；没有启用或应用失败时返回 None。
        """
        if os.name != "nt":
            return None

        tool_label = self._media_tool_label_from_process(process)
        tool_key = tool_label.casefold().replace(" ", "_").replace("/", "_")

        try:
            process_handle_value = int(getattr(process, "_handle"))
        except (AttributeError, TypeError, ValueError):
            self._log_resource_limit_warning_once(
                f"{tool_key}_missing_process_handle",
                f"无法取得 {tool_label} 的 Windows 进程句柄；本次不应用优先级和 CPU 硬上限。",
            )
            return None

        process_handle = wintypes.HANDLE(process_handle_value)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        priority_name = str(self.settings.get("process_priority", "below_normal"))
        priority_class = WINDOWS_PRIORITY_CLASSES.get(priority_name, WINDOWS_PRIORITY_CLASSES["below_normal"])
        priority_label = PROCESS_PRIORITY_LABELS.get(priority_name, "低于正常")

        # 设置并读回进程优先级。优先级失败不影响后续 CPU Job Object 尝试。
        set_priority_class = kernel32.SetPriorityClass
        set_priority_class.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        set_priority_class.restype = wintypes.BOOL
        get_priority_class = kernel32.GetPriorityClass
        get_priority_class.argtypes = [wintypes.HANDLE]
        get_priority_class.restype = wintypes.DWORD

        priority_confirmed = False
        if not set_priority_class(process_handle, priority_class):
            error_code = ctypes.get_last_error()
            self._log_resource_limit_warning_once(
                f"{tool_key}_priority_{error_code}",
                f"无法设置 {tool_label} 进程优先级（Windows 错误 {error_code}）；继续使用系统默认优先级。",
            )
        else:
            actual_priority = int(get_priority_class(process_handle))
            priority_confirmed = actual_priority == priority_class
            if not priority_confirmed:
                error_code = ctypes.get_last_error()
                self._log_resource_limit_warning_once(
                    f"{tool_key}_priority_verify_{actual_priority}_{error_code}",
                    f"{tool_label} 优先级读回值与请求不一致：请求={priority_label}，"
                    f"实际类=0x{actual_priority:08X}；继续执行，但优先级未确认。",
                )

        if not bool(self.settings.get("cpu_limit_enabled", True)):
            if priority_confirmed:
                success_key = f"{tool_key}_{priority_name}_priority_only"
                message = (
                    f"{tool_label} 进程优先级已确认：PID={process.pid}，"
                    f"优先级={priority_label}；CPU 硬上限关闭。"
                )
                if success_key not in self.resource_limit_success_keys:
                    self.resource_limit_success_keys.add(success_key)
                    self.log(message)
                else:
                    self.logger.debug(message)
            return None

        cpu_percent = int(self.settings.get("cpu_limit_percent", 70))
        cpu_rate = min(max(cpu_percent, 10), 100) * 100

        create_job_object = kernel32.CreateJobObjectW
        create_job_object.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        create_job_object.restype = wintypes.HANDLE
        set_information_job_object = kernel32.SetInformationJobObject
        set_information_job_object.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_information_job_object.restype = wintypes.BOOL
        query_information_job_object = kernel32.QueryInformationJobObject
        query_information_job_object.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_information_job_object.restype = wintypes.BOOL
        assign_process_to_job_object = kernel32.AssignProcessToJobObject
        assign_process_to_job_object.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign_process_to_job_object.restype = wintypes.BOOL
        is_process_in_job = kernel32.IsProcessInJob
        is_process_in_job.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        is_process_in_job.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        job_handle = create_job_object(None, None)
        job_handle_value = self._windows_handle_to_int(job_handle)
        if job_handle_value is None:
            error_code = ctypes.get_last_error()
            self._log_resource_limit_warning_once(
                f"{tool_key}_create_job_{error_code}",
                f"无法创建 {tool_label} CPU 限制作业对象（Windows 错误 {error_code}）；本次仅应用进程优先级。",
            )
            return None

        info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(
            ControlFlags=(
                JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
            ),
            CpuRate=cpu_rate,
        )
        if not set_information_job_object(
            job_handle,
            JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error_code = ctypes.get_last_error()
            close_handle(job_handle)
            self._log_resource_limit_warning_once(
                f"{tool_key}_set_job_rate_{error_code}",
                f"无法设置 {tool_label} CPU 硬上限（Windows 错误 {error_code}）；本次仅应用进程优先级。",
            )
            return None

        if not assign_process_to_job_object(job_handle, process_handle):
            error_code = ctypes.get_last_error()
            close_handle(job_handle)
            self._log_resource_limit_warning_once(
                f"{tool_key}_assign_job_{error_code}",
                f"无法把 {tool_label} 加入 CPU 限制作业对象（Windows 错误 {error_code}）；"
                "可能受到上级 Job Object 限制，本次仅应用进程优先级。",
            )
            return None

        # 第一步验证：当前媒体工具是否确实属于刚创建的 Job Object。
        in_job = wintypes.BOOL(False)
        if not is_process_in_job(process_handle, job_handle, ctypes.byref(in_job)):
            error_code = ctypes.get_last_error()
            self._log_resource_limit_warning_once(
                f"{tool_key}_verify_membership_api_{error_code}",
                f"无法验证 {tool_label} 是否已加入 CPU 限制作业对象（Windows 错误 {error_code}）；"
                "限制可能已生效，但本次无法确认。",
            )
            return job_handle_value
        if not bool(in_job.value):
            close_handle(job_handle)
            self._log_resource_limit_warning_once(
                f"{tool_key}_verify_membership_false",
                f"AssignProcessToJobObject 返回成功，但 IsProcessInJob 确认 {tool_label} 不属于该 Job；"
                "本次 CPU 硬上限未生效。",
            )
            return None

        # 第二步验证：读回 Job Object 的 CPU 控制标志和实际 CpuRate。
        actual_info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        returned_length = wintypes.DWORD(0)
        if not query_information_job_object(
            job_handle,
            JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS,
            ctypes.byref(actual_info),
            ctypes.sizeof(actual_info),
            ctypes.byref(returned_length),
        ):
            error_code = ctypes.get_last_error()
            self._log_resource_limit_warning_once(
                f"{tool_key}_query_job_rate_{error_code}",
                f"{tool_label} 已确认属于 CPU 限制作业对象，但无法读回 CPU 上限（Windows 错误 {error_code}）；"
                "限制可能已生效，本次无法完整确认。",
            )
            return job_handle_value

        required_flags = JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        flags_confirmed = (int(actual_info.ControlFlags) & required_flags) == required_flags
        rate_confirmed = int(actual_info.CpuRate) == cpu_rate
        if not flags_confirmed or not rate_confirmed:
            self._log_resource_limit_warning_once(
                f"{tool_key}_job_rate_mismatch_{int(actual_info.ControlFlags)}_{int(actual_info.CpuRate)}",
                f"{tool_label} 已加入 Job Object，但 CPU 上限读回值与请求不一致："
                f"请求={cpu_percent}% / flags=0x{required_flags:X}，"
                f"实际={int(actual_info.CpuRate) / 100:g}% / flags=0x{int(actual_info.ControlFlags):X}。",
            )
            return job_handle_value

        priority_status = priority_label if priority_confirmed else f"{priority_label}（未确认）"
        success_key = f"{tool_key}_{priority_name}_{int(actual_info.CpuRate)}"
        success_message = (
            f"{tool_label} 资源限制已确认应用：PID={process.pid}，优先级={priority_status}，"
            f"CPU 硬上限={int(actual_info.CpuRate) / 100:g}%"
            f"（ControlFlags=0x{int(actual_info.ControlFlags):X}）。"
        )
        if success_key not in self.resource_limit_success_keys:
            self.resource_limit_success_keys.add(success_key)
            self.log(success_message)
        else:
            self.logger.debug(success_message)
        return job_handle_value

    def _safe_apply_windows_resource_limits(
        self,
        process: subprocess.Popen[str],
    ) -> Optional[int]:
        """资源控制的最外层异常边界。

        ctypes/Windows API 属于附加能力；即使未来某个 Windows 版本、运行容器或
        安全软件让 API 调用出现未预料异常，也必须让 FFmpeg/ffprobe 本身继续运行。
        """
        try:
            return self._apply_windows_resource_limits(process)
        except Exception as exc:
            tool_label = self._media_tool_label_from_process(process)
            self.logger.exception("应用 %s 资源控制时出现内部异常", tool_label)
            self._log_resource_limit_warning_once(
                f"resource_internal_{tool_label}_{type(exc).__name__}",
                f"应用 {tool_label} 资源控制时出现内部异常：{exc}；本次继续使用系统默认调度。",
            )
            return None

    def _run_media_tool_sync(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
        combine_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """同步运行短 FFmpeg/ffprobe 命令，同时应用统一资源控制。

        ``subprocess.run`` 无法在子进程启动后插入 Job Object，因此启动检查和常规
        ffprobe 也必须经过这个包装器。communicate 放在后台线程中，使停止按钮和
        超时判断不会因为管道阻塞而失效。资源限制只影响调度速度，不修改命令参数。
        """
        stderr_target: Any = subprocess.STDOUT if combine_output else subprocess.PIPE
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_options(),
        )
        job_handle = self._safe_apply_windows_resource_limits(process)
        self._register_current_process(process, job_handle)

        result_box: list[tuple[str, str]] = []
        done = threading.Event()

        def communicate() -> None:
            try:
                stdout_text, stderr_text = process.communicate()
                result_box.append((stdout_text or "", stderr_text or ""))
            finally:
                done.set()

        reader = threading.Thread(target=communicate, daemon=True)
        reader.start()
        started_at = time.monotonic()
        try:
            while not done.wait(0.2):
                if self.stop_event.is_set():
                    self._terminate_current_process()
                    raise InterruptedError
                if time.monotonic() - started_at > timeout_seconds:
                    self._terminate_current_process()
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
            reader.join(timeout=5)
            stdout_text, stderr_text = result_box[-1] if result_box else ("", "")
            return subprocess.CompletedProcess(
                command,
                process.returncode if process.returncode is not None else -1,
                stdout_text,
                stderr_text,
            )
        finally:
            self._clear_current_process(process, job_handle)

    @staticmethod
    def _close_windows_job_handle(job_handle: Optional[int]) -> None:
        """关闭已经结束的 FFmpeg 或 ffprobe 对应 Job Object 句柄。"""
        if os.name != "nt" or not job_handle:
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(job_handle))
        except (AttributeError, OSError):
            # 进程已经结束，句柄清理失败不应影响任务结果。
            pass

    def _register_current_process(
        self,
        process: subprocess.Popen[str],
        job_handle: Optional[int],
    ) -> None:
        """原子登记当前媒体工具进程及其 Job Object，供停止按钮和 finally 清理使用。"""
        with self.process_lock:
            self.current_process = process
            self.current_job_handle = job_handle

    def _clear_current_process(
        self,
        process: subprocess.Popen[str],
        job_handle: Optional[int],
    ) -> None:
        """仅清理由本次调用登记的进程，防止未来并发改造时误清其他任务。"""
        with self.process_lock:
            if self.current_process is process:
                self.current_process = None
            if self.current_job_handle == job_handle:
                self.current_job_handle = None
        self._close_windows_job_handle(job_handle)

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

    def _safe_save_worker_state(
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
        *,
        active: bool,
        context: str,
    ) -> bool:
        """尽最大努力保存运行状态，但不让状态文件写入失败拖垮整个批次。

        无人值守环境下，state.json 写入失败通常意味着恢复信息可能不完整，
        但已经完成或正在进行的转码不应该因此全部中止。错误会同时写入滚动日志
        和 GUI 日志，便于用户回来后检查磁盘空间、权限或杀毒软件拦截。
        """
        try:
            self._save_state(
                sources, output_dir, suffix, recursive, move_original, move_failed,
                move_no_gain, temp_dir_text, precheck_enabled, active=active,
            )
            return True
        except Exception as exc:
            self.logger.exception("保存运行状态失败（%s）", context)
            self.log(f"保存运行状态失败（{context}）：{exc}；队列继续运行。", logging.ERROR)
            return False

    def _preserve_unverified_temp_output(self, task: Task, reason: str) -> str:
        """内部异常时保留可能已经完整编码的临时输出。

        正常的编码/验证失败仍按既定规则删除临时文件；只有 Python 内部出现
        未预料异常时才保留。这样类似“编码已完成，但验证器自身出错”的情况
        不会把数小时成果直接删除。文件名包含 .invalid_，旧成品扫描会自动忽略。
        """
        final_path = Path(task.output_path)
        temp_path = final_path.with_name(f"{final_path.stem}.__processing__.mkv")
        try:
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                return ""
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate = final_path.with_name(f"{final_path.stem}.invalid_internal_{timestamp}.mkv")
            counter = 1
            while candidate.exists():
                candidate = final_path.with_name(
                    f"{final_path.stem}.invalid_internal_{timestamp}_{counter}.mkv"
                )
                counter += 1
            os.replace(temp_path, candidate)
            self.log(
                f"{reason}；未验证临时输出已保留为：{candidate}",
                logging.ERROR,
            )
            return str(candidate)
        except OSError as exc:
            self.logger.exception("保留内部异常临时输出失败：%s", temp_path)
            self.log(f"{reason}；保留临时输出失败：{exc}", logging.ERROR)
            return ""

    def _run_task_safely(
        self,
        task: Task,
        *,
        repair_enabled: bool,
        phase_label: str,
    ) -> tuple[str, str]:
        """为每个视频建立独立异常边界。

        预期的 FFmpeg、验证或文件系统错误由 _process_task 转成普通“失败”返回；
        此处只兜底 NameError、类型错误、第三方库异常等程序内部问题。任何单个视频
        的内部异常都只影响该任务，不允许终止后续队列，也绝不自动移动源文件。
        """
        try:
            return self._process_task(task, repair_enabled=repair_enabled)
        except InterruptedError:
            raise
        except Exception as exc:
            self._terminate_current_process()
            self.logger.exception(
                "单任务内部异常：阶段=%s，文件=%s",
                phase_label,
                task.input_path,
            )
            preserved = self._preserve_unverified_temp_output(
                task, f"{phase_label}发生程序内部异常"
            )
            message = f"{phase_label}程序内部异常：{type(exc).__name__}: {exc}"
            if preserved:
                message += f"；未验证输出已保留：{Path(preserved).name}"
            task.failure_move_eligible = False
            task.failure_stage = "程序内部异常"
            task.failure_return_code = None
            task.failure_error = message
            task.failure_ffmpeg_command = ""
            return "失败", message

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
        self.failure_point_probe_enabled = bool(self.failure_point_var.get()) if hasattr(self, "failure_point_var") else bool(self.settings.get("failure_point_probe_enabled", True))
        self.compatibility_fallback_enabled = bool(self.compatibility_var.get()) if hasattr(self, "compatibility_var") else bool(self.settings.get("compatibility_fallback_enabled", False))
        self.two_stage_queue_enabled = bool(self.two_stage_var.get()) if hasattr(self, "two_stage_var") else bool(self.settings.get("two_stage_queue_enabled", True))
        self.failure_probe_pre_seconds = float(self.settings.get("failure_probe_pre_seconds", 30.0))
        self.failure_probe_post_seconds = float(self.settings.get("failure_probe_post_seconds", 90.0))
        self.failure_probe_max_points = int(self.settings.get("failure_probe_max_points", 3))
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
            self._safe_save_worker_state(
                sources, output_dir, suffix, recursive, move_original, move_failed,
                move_no_gain, temp_dir_text, precheck_enabled,
                active=True, context="扫描完成",
            )

            repair_queue: list[Task] = []

            # ---------------- 第一阶段：标准压制 ----------------
            # 批量处理时，最耗时间的不是普通视频，而是少数问题视频的多轮回退。
            # 因此第一阶段只跑标准路径：旧成品检查、无收益记录、收益预估、标准完整压制和验证。
            # 一旦某个文件编码/验证失败，就记录失败点并标记为“待修复”，然后立刻继续下一个任务。
            # 这样可以先把绝大多数能正常通过的视频处理完，不让一个问题文件卡住整个队列。
            if self.two_stage_queue_enabled:
                self.log("开始第一阶段：标准压制。失败项将先标记为待修复，最后集中处理。")
                self.ui_queue.put(("current_text", "第一阶段：标准压制"))
            else:
                self.log("开始标准压制；两阶段队列已关闭，失败文件会立即尝试修复。")
                self.ui_queue.put(("current_text", "标准压制"))
            for task in tasks:
                if self.stop_event.is_set():
                    raise InterruptedError("用户停止")

                # 恢复上次无人值守任务时，已经进入“待修复”的文件直接排入第二阶段，
                # 不再重复一次必然失败的标准完整压制。
                if task.status == "待修复" and self.two_stage_queue_enabled:
                    repair_queue.append(task)
                    self.completed_weight += max(task.weight, 1.0)
                    self.completed_count += 1
                    self._update_task_ui(task)
                    self._update_overall_progress(0.0, 0.0, None)
                    self.log(f"恢复待修复任务，跳过重复标准压制：{Path(task.input_path).name}", logging.WARNING)
                    continue

                if task.status in {"跳过", "无收益"}:
                    self.completed_weight += max(task.weight, 1.0)
                    self.completed_count += 1
                    self._update_task_ui(task)
                    self._update_overall_progress(0.0, 0.0, None)
                    continue

                task.status = "处理中"
                task.message = "第一阶段：标准压制"
                self._update_task_ui(task)
                self._safe_save_worker_state(
                    sources, output_dir, suffix, recursive, move_original, move_failed,
                    move_no_gain, temp_dir_text, precheck_enabled,
                    active=True, context=f"第一阶段开始：{Path(task.input_path).name}",
                )

                result_status, message = self._run_task_safely(
                    task, repair_enabled=False, phase_label="第一阶段标准压制"
                )
                task.status = result_status
                task.message = message

                if result_status == "失败" and self._is_batch_fatal_failure(task):
                    raise RuntimeError(
                        f"检测到会影响全部后续任务的环境错误："
                        f"{Path(task.input_path).name}：{message}"
                    )

                if result_status == "失败" and self._is_repairable_failure(task):
                    if self.two_stage_queue_enabled:
                        result_status, message = self._mark_task_waiting_repair(task, message)
                        repair_queue.append(task)
                        self.log(f"第一阶段待修复：{Path(task.input_path).name}：{message}", logging.WARNING)
                    else:
                        self.log(f"标准压制失败，立即尝试修复：{Path(task.input_path).name}：{message}", logging.WARNING)
                        result_status, message = self._run_task_safely(
                            task, repair_enabled=True, phase_label="立即修复"
                        )
                        task.status = result_status
                        task.message = message
                        if result_status == "失败" and self._is_batch_fatal_failure(task):
                            raise RuntimeError(
                                f"立即修复阶段检测到全局环境错误："
                                f"{Path(task.input_path).name}：{message}"
                            )

                if (
                    result_status == "失败"
                    and self.move_failed_enabled
                    and task.failure_move_eligible
                    and not self.stop_event.is_set()
                ):
                    # 环境性失败一般不会走到这里；若确认为不可自动修复但仍适合归类，才移动到失败目录。
                    self._safe_save_worker_state(
                        sources, output_dir, suffix, recursive, move_original, move_failed,
                        move_no_gain, temp_dir_text, precheck_enabled,
                        active=True, context=f"移动失败源文件前：{Path(task.input_path).name}",
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
                self._safe_save_worker_state(
                    sources, output_dir, suffix, recursive, move_original, move_failed,
                    move_no_gain, temp_dir_text, precheck_enabled,
                    active=True, context=f"第一阶段结束：{Path(task.input_path).name}",
                )

            # ---------------- 第二阶段：集中修复 ----------------
            # 第二阶段只处理第一阶段留下的“待修复”任务。这里才启用失败点定位、无损重封装、
            # CFR 时间戳规范化和可选兼容性回退。这样回退测试不会阻塞普通任务的吞吐。
            if self.two_stage_queue_enabled and repair_queue and not self.stop_event.is_set():
                self.log(f"第一阶段结束，开始第二阶段集中修复：{len(repair_queue)} 个待修复任务。", logging.WARNING)
                self.ui_queue.put(("current_text", f"第二阶段：集中修复 {len(repair_queue)} 个待修复任务"))

            for index, task in enumerate(repair_queue, start=1):
                if self.stop_event.is_set():
                    raise InterruptedError("用户停止")
                if task.status != "待修复":
                    continue

                # 第一阶段已经把该任务计入总体完成。第二阶段是对问题文件的附加修复，
                # 不再把总体进度权重倒退；只更新状态和日志，避免进度条来回跳。
                task.status = "修复中"
                task.message = f"第二阶段集中修复 {index}/{len(repair_queue)}"
                self._update_task_ui(task)
                self.ui_queue.put(("current_text", f"修复待修复任务 {index}/{len(repair_queue)}：{Path(task.input_path).name}"))
                self._safe_save_worker_state(
                    sources, output_dir, suffix, recursive, move_original, move_failed,
                    move_no_gain, temp_dir_text, precheck_enabled,
                    active=True, context=f"第二阶段开始：{Path(task.input_path).name}",
                )

                result_status, message = self._run_task_safely(
                    task, repair_enabled=True, phase_label="第二阶段集中修复"
                )
                task.status = result_status
                task.message = message

                if result_status == "失败" and self._is_batch_fatal_failure(task):
                    raise RuntimeError(
                        f"修复阶段检测到会影响全部后续任务的环境错误："
                        f"{Path(task.input_path).name}：{message}"
                    )

                if (
                    result_status == "失败"
                    and self.move_failed_enabled
                    and task.failure_move_eligible
                    and not self.stop_event.is_set()
                ):
                    self._safe_save_worker_state(
                        sources, output_dir, suffix, recursive, move_original, move_failed,
                        move_no_gain, temp_dir_text, precheck_enabled,
                        active=True, context=f"修复失败移动源文件前：{Path(task.input_path).name}",
                    )
                    moved, move_detail = self._move_original_to_failed(task)
                    if moved:
                        task.message = f"{task.message}；{move_detail}"
                    else:
                        self.log(f"修复失败已记录，但移动源文件失败：{move_detail}", logging.WARNING)

                self._update_task_ui(task)
                self._update_overall_progress(0.0, 0.0, None)
                self._safe_save_worker_state(
                    sources, output_dir, suffix, recursive, move_original, move_failed,
                    move_no_gain, temp_dir_text, precheck_enabled,
                    active=True, context=f"第二阶段结束：{Path(task.input_path).name}",
                )

            self._safe_save_worker_state(
                sources, output_dir, suffix, recursive, move_original, move_failed,
                move_no_gain, temp_dir_text, precheck_enabled,
                active=False, context="批次完成",
            )
            success = sum(1 for t in tasks if t.status == "完成")
            skipped = sum(1 for t in tasks if t.status in {"跳过", "无收益"})
            failed = sum(1 for t in tasks if t.status == "失败")
            pending_repair = sum(1 for t in tasks if t.status == "待修复")
            repaired = sum(1 for t in repair_queue if t.status == "完成")
            summary = (
                f"全部结束：完成 {success}（其中修复成功 {repaired}），"
                f"跳过/无收益 {skipped}，待修复 {pending_repair}，失败 {failed}。"
            )
            self.log(summary)
            self.ui_queue.put(("finished", summary))

        except InterruptedError:
            self.log("任务已停止；未完成队列会在下次启动时提供恢复。", logging.WARNING)
            self._safe_save_worker_state(
                sources, output_dir, suffix, recursive, move_original, move_failed,
                move_no_gain, temp_dir_text, precheck_enabled,
                active=True, context="用户停止",
            )
            self.ui_queue.put(("stopped", "任务已停止"))
        except Exception as exc:
            # 能到这里的应当只剩启动检查、扫描器或队列框架本身的系统级异常。
            # 单个视频内部异常已经由 _run_task_safely 隔离，不会再终止整批任务。
            self.logger.exception("后台系统级异常")
            self.log(f"任务中止：{exc}", logging.ERROR)
            self._safe_save_worker_state(
                sources, output_dir, suffix, recursive, move_original, move_failed,
                move_no_gain, temp_dir_text, precheck_enabled,
                active=True, context="后台系统级异常",
            )
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
        if not self.ffmpeg or not self.ffprobe:
            raise RuntimeError("请先打开“设置”，选择 FFmpeg 和 ffprobe 的可执行文件。")
        for executable, name in ((self.ffmpeg, "FFmpeg"), (self.ffprobe, "ffprobe")):
            result = self._run_media_tool_sync(
                [executable, "-version"],
                timeout_seconds=20,
                combine_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"{name} 无法运行：{result.stdout.strip()}")
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            self.log(f"{name}：{first_line}")

        encoder_result = self._run_media_tool_sync(
            [self.ffmpeg, "-hide_banner", "-encoders"],
            timeout_seconds=30,
            combine_output=True,
        )
        encoder_text = encoder_result.stdout.lower()
        if "libsvtav1" not in encoder_text:
            raise RuntimeError("当前 FFmpeg 不包含 libsvtav1 编码器。")
        if "libopus" not in encoder_text:
            raise RuntimeError("当前 FFmpeg 不包含 libopus 编码器。")

        svt_help = self._run_media_tool_sync(
            [self.ffmpeg, "-hide_banner", "-h", "encoder=libsvtav1"],
            timeout_seconds=30,
            combine_output=True,
        ).stdout.lower()
        if "svtav1-params" not in svt_help:
            raise RuntimeError("当前 FFmpeg 的 libsvtav1 不支持 svtav1-params，请更换较新的完整构建。")

        muxer_result = self._run_media_tool_sync(
            [self.ffmpeg, "-hide_banner", "-muxers"],
            timeout_seconds=30,
            combine_output=True,
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

        priority_label = PROCESS_PRIORITY_LABELS.get(
            str(self.settings.get("process_priority", "below_normal")),
            "低于正常",
        )
        if os.name == "nt" and bool(self.settings.get("cpu_limit_enabled", True)):
            self.log(
                f"FFmpeg / ffprobe 资源控制配置：优先级={priority_label}，"
                f"CPU 硬上限={int(self.settings.get('cpu_limit_percent', 70))}%；"
                "实际是否生效将在媒体工具启动后由 Windows API 读回确认；普通编码不使用 lp 参数。"
            )
        elif os.name == "nt":
            self.log(
                f"FFmpeg / ffprobe 资源控制配置：优先级={priority_label}，CPU 硬上限=关闭；"
                "实际优先级将在媒体工具启动后读回确认；普通编码不使用 lp 参数。"
            )
        else:
            self.log("当前不是 Windows；FFmpeg / ffprobe 进程优先级和 Job Object CPU 硬上限不生效。", logging.WARNING)
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
        result = self._run_media_tool_sync(
            command,
            timeout_seconds=120,
            combine_output=False,
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
            # attached_pic 是容器中的静态封面/缩略图，不是需要转码的动态视频。
            # 旧版为了避免映射错误而跳过整个文件，导致带封面的正常视频无法进入队列。
            # 当前策略是：仅选择 attached_pic=0 的唯一主视频流，并明确忽略全部封面流。
            cover_indexes_text = ", ".join(
                str(parse_int(stream.get("index"), -1)) for stream in attached_pictures
            )
            self.log(
                f"检测到 {len(attached_pictures)} 个 attached_pic 内嵌封面流"
                f"（索引：{cover_indexes_text}），将忽略封面并继续压制主视频：{path.name}",
                logging.INFO,
            )
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
            # 封面流属于非必要静态元数据，输出策略统一忽略，不参与映射和验证。
            cover_indexes=[],
            input_size=stat.st_size,
            input_mtime_ns=stat.st_mtime_ns,
            source_fingerprint=source_fingerprint,
            fingerprint_version=QUICK_FINGERPRINT_VERSION,
            weight=weight,
        )

        self._apply_recovery_task_status(task)

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
        if stage in {
            "编码", "编码输出", "完成验证", "重封装后重试编码",
            "重封装后编码输出", "CFR 时间戳规范化回退",
            "失败点方案整片重跑",
        }:
            task.failure_seconds = max(float(self.last_encoding_seconds), 0.0)
            task.failure_frame = max(int(self.last_encoding_frame), 0)
        return "失败", message

    def _run_encoding_command(
        self,
        progress_task: Task,
        command: list[str],
        log_source: Path,
    ) -> tuple[int, list[str], float]:
        """运行正式编码命令，并用独立看门狗监控“真正无进度”的卡死。

        旧实现直接 ``for line in process.stdout``。一旦 FFmpeg 完全卡住且不再输出
        任何 progress 行，Python 也会永久阻塞在读取操作上，后面的 300 秒判断根本
        没机会执行。这里改为：
        - 单独线程负责阻塞读取 stdout；
        - 工作线程每 0.5 秒醒来一次检查停止请求、进程状态和媒体时间进展；
        - 只有 out_time 连续五分钟完全不变化时才终止，不限制正常慢速编码总时长。
        """
        stderr_lines: list[str] = []
        start_time = time.monotonic()
        current_seconds = 0.0
        current_speed = 0.0
        last_progress_seconds = -1.0
        last_progress_change = start_time
        stall_triggered = False

        # 每次 FFmpeg 编码开始时清零。失败后 _process_task 会读取这些值定位故障点。
        self.last_encoding_seconds = 0.0
        self.last_encoding_frame = 0
        self.last_encoding_progress_at = start_time

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
        job_handle = self._safe_apply_windows_resource_limits(process)
        self._register_current_process(process, job_handle)

        stderr_thread = threading.Thread(
            target=self._collect_stderr,
            args=(process, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        stdout_queue: queue.Queue[str] = queue.Queue()
        stdout_done = threading.Event()

        def collect_progress_stdout() -> None:
            try:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    stdout_queue.put(line)
            finally:
                stdout_done.set()

        stdout_thread = threading.Thread(target=collect_progress_stdout, daemon=True)
        stdout_thread.start()

        progress_data: dict[str, str] = {}
        try:
            while True:
                if self.stop_event.is_set():
                    self._terminate_current_process()
                    raise InterruptedError

                raw_line: Optional[str]
                try:
                    raw_line = stdout_queue.get(timeout=0.5)
                except queue.Empty:
                    raw_line = None

                if raw_line is not None:
                    line = raw_line.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        progress_data[key] = value
                        if key == "out_time":
                            current_seconds = min(
                                parse_ffmpeg_time(value), progress_task.duration
                            )
                            if abs(current_seconds - last_progress_seconds) >= 0.25:
                                last_progress_seconds = current_seconds
                                last_progress_change = time.monotonic()
                                self.last_encoding_seconds = current_seconds
                                self.last_encoding_progress_at = last_progress_change
                        elif key == "out_time_ms":
                            try:
                                ms_seconds = int(value) / 1_000_000.0
                            except ValueError:
                                ms_seconds = current_seconds
                            current_seconds = min(
                                max(ms_seconds, current_seconds), progress_task.duration
                            )
                            if abs(current_seconds - last_progress_seconds) >= 0.25:
                                last_progress_seconds = current_seconds
                                last_progress_change = time.monotonic()
                                self.last_encoding_seconds = current_seconds
                                self.last_encoding_progress_at = last_progress_change
                        elif key == "frame":
                            self.last_encoding_frame = parse_int(
                                value, self.last_encoding_frame
                            )
                        elif key == "speed":
                            current_speed = parse_float(value.rstrip("x"), 0.0)
                        elif key == "progress":
                            percent = min(
                                max(current_seconds / progress_task.duration, 0.0), 1.0
                            )
                            current_eta = (
                                (progress_task.duration - current_seconds) / current_speed
                                if current_speed > 0 else None
                            )
                            self._update_encoding_progress(
                                progress_task, percent, current_speed, current_eta
                            )
                            progress_data.clear()

                now = time.monotonic()
                if (
                    process.poll() is None
                    and now - last_progress_change > ENCODING_STALL_TIMEOUT_SECONDS
                ):
                    stall_triggered = True
                    message = (
                        f"AV1 tool watchdog: encoding progress stalled for "
                        f"{int(ENCODING_STALL_TIMEOUT_SECONDS)} seconds at "
                        f"{current_seconds:.3f}s"
                    )
                    stderr_lines.append(message)
                    self.log(
                        f"编码进度超过 {int(ENCODING_STALL_TIMEOUT_SECONDS)} 秒未前进，"
                        f"按卡住处理：{Path(progress_task.input_path).name}，"
                        f"最后进度 {format_duration(current_seconds)}",
                        logging.WARNING,
                    )
                    self._terminate_current_process()
                    break

                if (
                    process.poll() is not None
                    and stdout_done.is_set()
                    and stdout_queue.empty()
                ):
                    break

            return_code = process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            if stall_triggered and return_code == 0:
                # 理论上被 terminate 的进程不会返回 0；保留兜底，防止平台行为差异
                # 让卡死被误判为成功。
                return_code = -1
        finally:
            self._clear_current_process(process, job_handle)

        stderr_text = "\n".join(stderr_lines)
        if stderr_text:
            self.logger.debug("FFmpeg stderr for %s:\n%s", log_source, stderr_text)
        return return_code, stderr_lines, time.monotonic() - start_time

    def _run_ffmpeg_capture(
        self,
        command: list[str],
        log_label: str,
        timeout_seconds: Optional[float] = None,
    ) -> tuple[int, list[str], float]:
        """运行不需要实时进度展示的短任务，如重封装和局部样本测试。"""
        stderr_lines: list[str] = []
        start_time = time.monotonic()
        timed_out = False
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **subprocess_options(),
        )
        job_handle = self._safe_apply_windows_resource_limits(process)
        self._register_current_process(process, job_handle)

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
                if (
                    timeout_seconds is not None
                    and time.monotonic() - start_time > timeout_seconds
                ):
                    timed_out = True
                    timeout_message = (
                        f"AV1 tool watchdog: command timed out after "
                        f"{timeout_seconds:.1f} seconds: {log_label}"
                    )
                    stderr_lines.append(timeout_message)
                    self.log(f"FFmpeg 命令超时，已终止：{log_label}", logging.WARNING)
                    self._terminate_current_process()
                    break
                time.sleep(0.2)
            return_code = process.wait()
            stderr_thread.join(timeout=2)
            if timed_out and return_code == 0:
                return_code = -1
        finally:
            self._clear_current_process(process, job_handle)

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


    # ---------- 失败点定位与局部回退测试 ----------

    def _failure_probe_range(self, task: Task, failed_seconds: float) -> tuple[float, float]:
        """根据失败时间点计算局部测试范围。

        这里刻意不是“断点续压”：
        - 失败后只在该位置附近编码 1~2 分钟测试片段；
        - 找到能通过这个错误点的方案后，再从头完整压制，保证最终文件结构干净。
        """
        duration = max(float(task.duration), 0.0)
        pre = max(float(self.failure_probe_pre_seconds), 0.0)
        post = max(float(self.failure_probe_post_seconds), 15.0)
        if duration <= 0:
            return 0.0, pre + post
        point = min(max(float(failed_seconds), 0.0), duration)
        start = max(point - pre, 0.0)
        # 如果错误发生在开头，仍然测试一段足够长的片段，覆盖初始化后的第一批帧。
        length = min(pre + post, max(duration - start, 1.0))
        return start, max(length, 1.0)

    def _failure_probe_sample_path(self, task: Task, mode: str) -> Path:
        """生成失败点局部测试的临时输出路径。"""
        source_root = Path(task.source_root)
        temp_root = self._temp_root_for_source_path(source_root)
        try:
            relative = Path(task.source_relative)
            relative_parent = relative.parent if str(relative.parent) != "." else Path()
        except Exception:
            relative_parent = Path()
        stem = Path(task.input_path).stem
        fingerprint = (task.source_fingerprint or "nofp")[:12]
        safe_mode = re.sub(r"[^0-9A-Za-z_.-]+", "_", mode)
        name = f"{stem}.{fingerprint}.av1tooltmp.failureprobe.{safe_mode}.mkv"
        return temp_root / relative_parent / name

    def _ensure_remux_retry_task(
        self,
        task: Task,
        remux_path: Path,
        command_history: list[tuple[str, list[str]]],
    ) -> Optional[Task]:
        """确保存在可用于回退测试的无损重封装临时 MKV。"""
        if remux_path.exists() and remux_path.stat().st_size > 0:
            try:
                return self._task_for_remuxed_input(task, remux_path)
            except Exception:
                self._safe_unlink(remux_path)

        prepare_error = self._prepare_remux_retry_path(task, remux_path)
        if prepare_error:
            self.log(f"失败点诊断无法准备无损重封装：{prepare_error}", logging.WARNING)
            return None

        remux_command = self._build_remux_retry_command(task, remux_path)
        command_history.append(("失败点诊断：自动无损重封装", remux_command))
        self.log(f"失败点诊断重封装命令：{display_command(remux_command)}", gui=False)
        try:
            return_code, stderr_lines, _ = self._run_ffmpeg_capture(
                remux_command,
                f"失败点诊断自动重封装 {task.input_path}",
                timeout_seconds=max(300.0, min(float(task.duration) * 2.0, 1800.0)),
            )
        except OSError as exc:
            self.log(f"失败点诊断无法启动重封装：{exc}", logging.WARNING)
            return None
        if return_code != 0 or not remux_path.exists() or remux_path.stat().st_size <= 0:
            self.log(
                f"失败点诊断重封装失败：{summarize_ffmpeg_failure(return_code, stderr_lines)}",
                logging.WARNING,
            )
            return None
        try:
            return self._task_for_remuxed_input(task, remux_path)
        except Exception as exc:
            self.log(f"失败点诊断重封装结构验证失败：{exc}", logging.WARNING)
            return None

    def _failure_probe_candidates(
        self,
        task: Task,
        remux_task: Optional[Task],
        skipped_modes: set[str],
    ) -> list[dict[str, Any]]:
        """按从轻到重生成局部回退测试候选。"""
        candidates: list[dict[str, Any]] = []
        # 1) 重封装后仍使用原参数。只改变容器/时间戳，不改变画质策略。
        if remux_task is not None:
            candidates.append({
                "mode": "remux_passthrough",
                "label": "无损重封装 + 原参数",
                "task": remux_task,
                "fps_mode": "passthrough",
                "crf": None,
                "preset": "5",
                "svt": "tune=0",
            })

        # 后续 CFR 候选优先在重封装临时文件上测试；没有重封装时回到源文件。
        base_task = remux_task or task
        if task.cfr_fallback_rate:
            if not base_task.cfr_fallback_rate:
                base_task = replace(base_task, cfr_fallback_rate=task.cfr_fallback_rate)
            candidates.append({
                "mode": "cfr",
                "label": f"CFR 时间戳规范化（{task.cfr_fallback_rate} fps）",
                "task": base_task,
                "fps_mode": "cfr",
                "crf": None,
                "preset": "5",
                "svt": "tune=0",
            })
            if self.compatibility_fallback_enabled:
                candidates.extend([
                    {
                        "mode": "cfr_lp4",
                        "label": "CFR + 降低 SVT 并行度 lp=4",
                        "task": base_task,
                        "fps_mode": "cfr",
                        "crf": None,
                        "preset": "5",
                        "svt": "tune=0:lp=4",
                    },
                    {
                        "mode": "cfr_crf_minus1",
                        "label": "CFR + CRF -1",
                        "task": base_task,
                        "fps_mode": "cfr",
                        "crf": max(int(task.crf) - 1, 0),
                        "preset": "5",
                        "svt": "tune=0",
                    },
                    {
                        "mode": "cfr_preset6",
                        "label": "CFR + Preset 6",
                        "task": base_task,
                        "fps_mode": "cfr",
                        "crf": None,
                        "preset": "6",
                        "svt": "tune=0",
                    },
                ])
        elif self.compatibility_fallback_enabled:
            # 没有稳定帧率可用时，仍可测试“只降并行度”的最轻兼容回退。
            candidates.append({
                "mode": "passthrough_lp4",
                "label": "保持时间戳 + 降低 SVT 并行度 lp=4",
                "task": base_task,
                "fps_mode": "passthrough",
                "crf": None,
                "preset": "5",
                "svt": "tune=0:lp=4",
            })
        return [item for item in candidates if str(item["mode"]) not in skipped_modes]

    def _run_failure_point_local_tests(
        self,
        task: Task,
        remux_path: Path,
        temp_path: Path,
        failed_seconds: float,
        skipped_modes: set[str],
        command_history: list[tuple[str, list[str]]],
    ) -> Optional[tuple[Task, list[str], str, str]]:
        """在失败点附近局部测试回退方案。

        返回值是：通过测试的输入 task、用于整片重跑的完整命令、模式ID、人类可读标签。
        如果没有任何方案能通过局部片段，则返回 None。
        """
        if not self.failure_point_probe_enabled:
            return None

        failed_seconds = min(max(float(failed_seconds), 0.0), max(float(task.duration), 0.0))
        start, length = self._failure_probe_range(task, failed_seconds)
        history_header = (
            f"故障点约 {format_duration(failed_seconds)}；"
            f"局部测试范围 {format_duration(start)}~{format_duration(start + length)}"
        )
        task.failure_probe_history.append(history_header)
        self.log(
            f"失败点定位：{Path(task.input_path).name}，" + history_header + "。",
            logging.WARNING,
        )
        self.ui_queue.put(("current_text", f"失败点诊断：{Path(task.input_path).name}"))

        # 准备重封装输入。即使最终不是 remux 模式，后续 CFR 在重封装文件上测试通常也更稳。
        remux_task = self._ensure_remux_retry_task(task, remux_path, command_history)
        candidates = self._failure_probe_candidates(task, remux_task, skipped_modes)
        if not candidates:
            self.log("失败点定位：没有可测试的候选方案。", logging.WARNING)
            return None

        for candidate in candidates:
            if self.stop_event.is_set():
                raise InterruptedError
            mode = str(candidate["mode"])
            label = str(candidate["label"])
            source_task: Task = candidate["task"]
            sample_path = self._failure_probe_sample_path(task, mode)
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            self._safe_unlink(sample_path)
            sample_command = self._build_ffmpeg_command(
                source_task,
                sample_path,
                fps_mode=str(candidate["fps_mode"]),
                seek_start=start,
                segment_duration=length,
                crf_override=candidate["crf"],
                preset_override=str(candidate["preset"]),
                svtav1_params=str(candidate["svt"]),
                fallback_mode="probe-" + mode,
            )
            self.log(f"失败点局部测试 [{label}] 命令：{display_command(sample_command)}", gui=False)
            task.message = f"失败点局部测试：{label}"
            self._update_task_ui(task)
            timeout = max(300.0, min(length * 12.0 + 120.0, 1800.0))
            try:
                return_code, stderr_lines, _ = self._run_ffmpeg_capture(
                    sample_command,
                    f"失败点局部测试 {label} {task.input_path}",
                    timeout_seconds=timeout,
                )
            except OSError as exc:
                self.log(f"失败点局部测试无法启动 [{label}]：{exc}", logging.WARNING)
                self._safe_unlink(sample_path)
                continue
            if return_code == 0 and sample_path.exists() and sample_path.stat().st_size > 0:
                # 只做轻量可读性确认，不做完整输出验证；这里的目标是快速判断能否穿过故障点。
                try:
                    self._probe(sample_path)
                except Exception as exc:
                    self.log(f"失败点局部测试 [{label}] 生成文件不可读：{exc}", logging.WARNING)
                    self._safe_unlink(sample_path)
                    continue
                self._safe_unlink(sample_path)
                full_command = self._build_ffmpeg_command(
                    source_task,
                    temp_path,
                    fps_mode=str(candidate["fps_mode"]),
                    crf_override=candidate["crf"],
                    preset_override=str(candidate["preset"]),
                    svtav1_params=str(candidate["svt"]),
                    fallback_mode=mode,
                )
                task.failure_probe_history.append(f"通过：{label}")
                self.log(f"失败点局部测试通过：{label}；将用该方案从头完整压制。", logging.WARNING)
                return source_task, full_command, mode, label
            failure_summary = summarize_ffmpeg_failure(return_code, stderr_lines)
            task.failure_probe_history.append(f"失败：{label}：{failure_summary[:240]}")
            self.log(
                f"失败点局部测试失败 [{label}]：{failure_summary}",
                logging.WARNING,
            )
            self._safe_unlink(sample_path)
        return None

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

    def _is_batch_fatal_failure(self, task: Task) -> bool:
        """判断错误是否会让后续所有任务继续失败。

        无人值守时，输出磁盘已满仍继续跑完整个队列只会制造大量重复失败；
        这种系统级资源问题应立即停止批次并保留 active 状态。源文件缺失、单文件
        权限或内容损坏仍只影响当前任务，不在这里中止全局。
        """
        text = f"{task.failure_stage}\n{task.failure_error}".lower()
        return task.failure_stage == "磁盘空间检查" or any(
            token in text
            for token in (
                "no space left",
                "disk full",
                "not enough space",
                "磁盘空间不足",
            )
        )

    def _is_repairable_failure(self, task: Task) -> bool:
        """判断第一阶段失败是否应该进入第二阶段集中修复。

        第一阶段的目标是“先把能顺利压制的视频跑完”，所以编码器崩溃、
        编码失败、输出验证失败这类和具体视频内容相关的问题会暂存为“待修复”。
        磁盘空间不足、源文件消失、权限不足、临时文件无法删除等环境问题通常不是
        换回退方案能解决的，因此直接保留为失败，避免第二阶段浪费时间。
        """
        if task.status != "失败":
            return False
        if not task.failure_move_eligible:
            return False
        repairable_stages = {
            "编码",
            "编码输出",
            "完成验证",
            "重封装后重试编码",
            "重封装后编码输出",
            "CFR 时间戳规范化回退",
            "失败点方案整片重跑",
        }
        if task.failure_stage not in repairable_stages:
            return False

        if task.failure_stage == "完成验证":
            # 这些是输出结构/策略不符合要求，切换时间戳、CRF、Preset 或并行度
            # 都不会改变映射结构。继续局部回退只会重复完整压制，因此直接保留为失败。
            deterministic_validation_tokens = (
                "主视频流数量异常",
                "输出视频不是 av1",
                "输出不是 10-bit",
                "分辨率变化",
                "音轨数量变化",
                "发现非 opus",
                "字幕数量变化",
                "附件数量变化",
                "章节数量变化",
                "hdr 标记未保持",
                "hdr 静态元数据未保持",
            )
            lowered = task.failure_error.lower()
            if any(token in lowered for token in deterministic_validation_tokens):
                return False
        return True

    def _mark_task_waiting_repair(self, task: Task, message: str) -> tuple[str, str]:
        """把第一阶段的可修复失败改成“待修复”。

        这里不移动源文件，不写入最终失败说明，只保存错误摘要和失败点。第二阶段会
        读取这些信息，在失败点附近做局部测试，找到方案后再从头正式压制。
        """
        task.status = "待修复"
        task.message = "第一阶段失败，稍后集中修复：" + message[:220]
        return "待修复", task.message

    def _process_task(self, task: Task, repair_enabled: bool = True) -> tuple[str, str]:
        # 第二阶段需要知道第一阶段究竟在哪个阶段、哪个时间点失败。
        # 先保存旧值，再清理本轮错误字段；否则“待修复”一进入 _process_task 就会
        # 丢失失败点，只能再次从头盲压。
        previous_failure_stage = task.failure_stage
        previous_failure_seconds = max(float(task.failure_seconds), 0.0)
        previous_failure_error = task.failure_error

        task.failure_move_eligible = False
        task.failure_stage = ""
        task.failure_return_code = None
        task.failure_moved_to = ""
        task.failure_error = ""
        task.failure_ffmpeg_command = ""
        task.failure_seconds = 0.0
        task.failure_frame = 0
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

        # 收益预估只属于第一阶段前置判断。进入修复阶段后源文件和参数已经
        # 分析过，再抽样一次只会重复消耗时间，并且对“能否修复错误点”没有帮助。
        if self.precheck_enabled and not repair_enabled:
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
        command_history: list[tuple[str, list[str]]] = [("标准方案", command)]

        used_remux_retry = False
        used_failure_point_probe = False
        failure_probe_modes_used: list[str] = []
        skip_legacy_fallbacks = False
        # 已经用来完整压制过的模式不再重复测试，避免“局部通过 → 整片又在后面失败 → 再用同模式从头重跑”的循环。
        attempted_full_modes: set[str] = {"standard"}
        # retry_source_task 指向“当前实际输入”。
        # 正常情况下它就是原 task；若自动无损重封装成功，它会指向重封装后的临时 MKV。
        # 后续 CFR 回退要基于当前实际输入构造命令，不能继续错误地使用已经崩溃的旧命令。
        retry_source_task: Optional[Task] = None
        command_log_source = input_path

        # 两阶段队列的核心优化：第二阶段已经知道第一阶段失败的位置，
        # 因此先在该位置附近局部测试，绝不再次执行一次已知会失败的标准整片压制。
        known_repair_stages = {
            "编码", "编码输出", "完成验证", "重封装后重试编码",
            "重封装后编码输出", "CFR 时间戳规范化回退",
            "失败点方案整片重跑",
        }
        if (
            repair_enabled
            and self.failure_point_probe_enabled
            and previous_failure_stage in known_repair_stages
        ):
            skip_legacy_fallbacks = True
            failed_seconds = previous_failure_seconds
            if failed_seconds <= 0.0 and previous_failure_stage == "完成验证":
                # 验证器若没能提供更精确位置，至少从结尾附近开始诊断，
                # 但不再重复标准整片压制。
                failed_seconds = max(float(task.duration), 0.0)
            self.log(
                f"第二阶段使用第一阶段失败点直接诊断：{input_path.name} | "
                f"阶段 {previous_failure_stage} | 位置 {format_duration(failed_seconds)}",
                logging.WARNING,
            )
            probe_result = self._run_failure_point_local_tests(
                task, remux_path, temp_path, failed_seconds,
                attempted_full_modes, command_history,
            )
            if probe_result is None:
                detail = previous_failure_error or "第一阶段失败点没有可通过的回退方案"
                # _failure_result 会从 last_encoding_seconds 写入最终失败位置；
                # 局部测试使用的是无进度捕获器，因此这里显式保留第一阶段位置。
                self.last_encoding_seconds = failed_seconds
                return self._failure_result(
                    task,
                    f"局部诊断未找到可通过方案；第一阶段错误：{detail[:1000]}",
                    stage="失败点方案整片重跑",
                    move_eligible=True,
                    ffmpeg_command="\n\n".join(
                        f"[{label}]\n{display_command(item)}"
                        for label, item in command_history
                    ),
                )
            retry_source_task, command, mode_id, mode_label = probe_result
            attempted_full_modes.add(mode_id)
            failure_probe_modes_used.append(mode_label)
            used_failure_point_probe = True
            if os.path.normcase(str(Path(retry_source_task.input_path))) == os.path.normcase(str(remux_path)):
                used_remux_retry = True
            command_log_source = Path(retry_source_task.input_path)
            command_history.append((f"第一阶段失败点通过后整片重跑：{mode_label}", command))
            task.message = f"失败点局部测试通过，整片重跑：{mode_label}"
            self._update_task_ui(task)
            self.log(
                f"局部测试已确认方案，跳过重复标准压制并直接整片重跑："
                f"{input_path.name} | {mode_label}",
                logging.WARNING,
            )

        self.log(f"开始编码：{input_path}")
        self.log(f"FFmpeg 命令：{display_command(command)}", gui=False)

        try:
            try:
                return_code, stderr_lines, _ = self._run_encoding_command(
                    task, command, command_log_source
                )
            except OSError as exc:
                return self._failure_result(task, f"无法启动 FFmpeg：{exc}", stage="启动 FFmpeg", move_eligible=False)

            if self.stop_event.is_set():
                self._safe_unlink(temp_path)
                raise InterruptedError

            # 默认启用的增强错误处理：
            # 完整压制失败后，先记录最后进度，在该时间点附近做短片段局部测试。
            # 一旦某个回退方案能通过这个错误点，才用该方案从头完整压制，避免多次盲目完整重跑。
            if repair_enabled and return_code != 0 and self.failure_point_probe_enabled:
                skip_legacy_fallbacks = True
                for _probe_index in range(max(int(self.failure_probe_max_points), 1)):
                    if return_code == 0 or self.stop_event.is_set():
                        break
                    failed_seconds = self.last_encoding_seconds
                    if failed_seconds <= 0.0:
                        # 如果 FFmpeg 在第一帧前就崩溃/卡住，故障点按开头处理。
                        failed_seconds = 0.0
                    self._safe_unlink(temp_path)
                    probe_result = self._run_failure_point_local_tests(
                        task,
                        remux_path,
                        temp_path,
                        failed_seconds,
                        attempted_full_modes,
                        command_history,
                    )
                    if probe_result is None:
                        break
                    retry_source_task, retry_command, mode_id, mode_label = probe_result
                    attempted_full_modes.add(mode_id)
                    failure_probe_modes_used.append(mode_label)
                    used_failure_point_probe = True
                    if os.path.normcase(str(Path(retry_source_task.input_path))) == os.path.normcase(str(remux_path)):
                        used_remux_retry = True
                    retry_source_task_for_log = Path(retry_source_task.input_path)
                    retry_source_task = retry_source_task
                    command_history.append((f"失败点局部测试通过后整片重跑：{mode_label}", retry_command))
                    task.message = f"失败点定位通过，整片重跑：{mode_label}"
                    self._update_task_ui(task)
                    self.ui_queue.put(("current_text", f"整片重跑：{input_path.name}"))
                    self.log(f"失败点局部测试通过，使用方案整片重跑：{input_path.name} | {mode_label}", logging.WARNING)
                    self.log(f"失败点方案整片重跑命令：{display_command(retry_command)}", gui=False)
                    try:
                        return_code, stderr_lines, _ = self._run_encoding_command(
                            task,
                            retry_command,
                            retry_source_task_for_log,
                        )
                    except OSError as exc:
                        return self._failure_result(
                            task,
                            f"失败点方案整片重跑无法启动 FFmpeg：{exc}",
                            stage="失败点方案整片重跑",
                            move_eligible=False,
                            ffmpeg_command="\n\n".join(
                                f"[{label}]\n{display_command(item)}" for label, item in command_history
                            ),
                        )
                    if self.stop_event.is_set():
                        self._safe_unlink(temp_path)
                        raise InterruptedError
                if return_code != 0:
                    self.log("失败点局部测试未能找到可完整通过的方案，停止自动重试。", logging.WARNING)

            if repair_enabled and return_code != 0 and not skip_legacy_fallbacks and (return_code & 0xFFFFFFFF) == 0xC0000005:
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
                        timeout_seconds=max(
                            300.0, min(float(task.duration) * 2.0, 1800.0)
                        ),
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
            if repair_enabled and return_code != 0 and not skip_legacy_fallbacks and task.cfr_fallback_rate and not any(label == "CFR 时间戳规范化重试" for label, _ in command_history):
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
            if used_failure_point_probe:
                retry_note += " | 失败点定位后成功：" + "、".join(failure_probe_modes_used)
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
            if used_failure_point_probe:
                message += "；失败点定位后成功"
            if move_detail:
                message += f"；{move_detail}"
            return "完成", message
        finally:
            self._safe_unlink(remux_path)

    def _build_ffmpeg_command(
        self,
        task: Task,
        temp_path: Path,
        fps_mode: str = "passthrough",
        seek_start: Optional[float] = None,
        segment_duration: Optional[float] = None,
        crf_override: Optional[int] = None,
        preset_override: str = "5",
        svtav1_params: str = "tune=0",
        fallback_mode: str = "",
    ) -> list[str]:
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel", "warning",
            "-stats_period", "0.5",
            "-progress", "pipe:1",
        ]
        if seek_start is not None:
            command += ["-ss", f"{max(float(seek_start), 0.0):.3f}"]
        command += [
            "-i", task.input_path,
            "-map", f"0:{task.video_index}",
        ]

        for plan in task.audio_plans:
            command += ["-map", f"0:{plan.input_index}"]
        for index in task.subtitle_indexes:
            command += ["-map", f"0:{index}"]
        for index in task.attachment_indexes:
            command += ["-map", f"0:{index}"]

        command += [
            "-map_metadata", "0",
            "-map_chapters", "0",
            "-c:v:0", "libsvtav1",
            "-preset", str(preset_override),
            "-crf", str(task.crf if crf_override is None else crf_override),
            "-svtav1-params", svtav1_params,
            "-pix_fmt", "yuv420p10le",
        ]
        if segment_duration is not None:
            command += ["-t", f"{max(float(segment_duration), 0.001):.3f}"]
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

        command += [
            "-metadata", f"AV1TOOL_SOURCE_SIZE={task.input_size}",
            "-metadata", f"AV1TOOL_SOURCE_MTIME_NS={task.input_mtime_ns}",
            "-metadata", f"AV1TOOL_SOURCE_RELATIVE={task.source_relative}",
            "-metadata", f"AV1TOOL_SOURCE_FINGERPRINT={task.source_fingerprint}",
            "-metadata", f"AV1TOOL_FINGERPRINT_VERSION={task.fingerprint_version}",
            "-metadata", f"AV1TOOL_POLICY_VERSION={ENCODING_POLICY_VERSION}",
        ]
        if fallback_mode:
            command += ["-metadata", f"AV1TOOL_FALLBACK_MODE={fallback_mode}"]
        command += [
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

        # attached_pic 封面流不是归档成品的必要内容。
        # 新生成文件会主动忽略封面；已有成品即使保留或缺少封面，也不应因此判定无效。

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
            ok, detail = self._full_decode_verify(path, expected_duration=task.duration)
            if not ok:
                return False, detail

        return True, "验证通过"

    def _full_decode_verify(
        self,
        path: Path,
        expected_duration: Optional[float] = None,
    ) -> tuple[bool, str]:
        """完整解码新成品，确认视频与音频流可以从头读到尾。

        该验证只在编码日志出现可疑信息时触发。它本身也必须适合无人值守：
        - 不使用未定义的外部变量；
        - 通过 ``-progress pipe:1`` 监控解码媒体时间；
        - 仅在连续十分钟完全无进度时终止，正常的慢速完整解码不设总时长上限；
        - 重复/非单调 DTS 仍按既有白名单处理。
        """
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v", "error",
            "-stats_period", "1",
            "-progress", "pipe:1",
            "-i", str(path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-f", "null",
            "-",
        ]
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
            return False, f"无法启动完整解码验证：{exc}"

        job_handle = self._safe_apply_windows_resource_limits(process)
        self._register_current_process(process, job_handle)

        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=self._collect_stderr,
            args=(process, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        stdout_queue: queue.Queue[str] = queue.Queue()
        stdout_done = threading.Event()

        def collect_verify_progress() -> None:
            try:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    stdout_queue.put(line)
            finally:
                stdout_done.set()

        stdout_thread = threading.Thread(target=collect_verify_progress, daemon=True)
        stdout_thread.start()

        last_progress_change = time.monotonic()
        last_seconds = 0.0
        watchdog_triggered = False
        try:
            while True:
                if self.stop_event.is_set():
                    self._terminate_current_process()
                    raise InterruptedError

                raw_line: Optional[str]
                try:
                    raw_line = stdout_queue.get(timeout=0.5)
                except queue.Empty:
                    raw_line = None

                if raw_line is not None:
                    line = raw_line.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        current_seconds = last_seconds
                        if key == "out_time":
                            current_seconds = parse_ffmpeg_time(value)
                        elif key == "out_time_ms":
                            try:
                                current_seconds = int(value) / 1_000_000.0
                            except ValueError:
                                current_seconds = last_seconds
                        if current_seconds > last_seconds + 0.05:
                            last_seconds = current_seconds
                            last_progress_change = time.monotonic()
                            # 供验证失败后的第二阶段局部定位使用。
                            self.last_encoding_seconds = current_seconds

                if (
                    process.poll() is None
                    and time.monotonic() - last_progress_change
                    > VERIFY_STALL_TIMEOUT_SECONDS
                ):
                    watchdog_triggered = True
                    self.log(
                        f"完整解码验证超过 {int(VERIFY_STALL_TIMEOUT_SECONDS)} 秒无进度，"
                        f"已终止：{path.name}，最后位置 {format_duration(last_seconds)}",
                        logging.WARNING,
                    )
                    self._terminate_current_process()
                    break

                if (
                    process.poll() is not None
                    and stdout_done.is_set()
                    and stdout_queue.empty()
                ):
                    break

            return_code = process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            if watchdog_triggered:
                return (
                    False,
                    f"完整解码验证在 {format_duration(last_seconds)} 处连续 "
                    f"{int(VERIFY_STALL_TIMEOUT_SECONDS)} 秒无进度",
                )

            benign_lines = [
                line for line in stderr_lines if is_benign_timestamp_message(line)
            ]
            failure_lines = [
                line for line in stderr_lines if not is_benign_timestamp_message(line)
            ]

            # null 封装器可能把重复 DTS 报成 error，甚至返回非零状态，
            # 但这只说明时间戳不严格单调，不代表 AV1 画面或 Opus 音频无法解码。
            # 只有出现其余解码错误，或无任何可解释信息却返回非零时，才判失败。
            if failure_lines:
                detail = "\n".join(failure_lines).strip()[-1000:]
                return False, (
                    f"完整解码验证失败（约 {format_duration(last_seconds)}）：{detail}"
                )
            if return_code != 0 and not benign_lines:
                return False, f"完整解码验证失败：FFmpeg 返回码 {return_code}"
            if benign_lines:
                self.log(
                    f"完整解码验证忽略 {len(benign_lines)} 条重复/非单调 DTS "
                    f"时间戳信息：{path.name}",
                    logging.WARNING,
                )
                return True, "完整解码通过（忽略非单调 DTS 时间戳信息）"

            if expected_duration and expected_duration > 0:
                # 这里只做诊断日志，不再重复判定时长；正式时长校验已经由 ffprobe 完成。
                self.log(
                    f"完整解码验证通过：{path.name}，解码到 "
                    f"{format_duration(last_seconds)} / "
                    f"{format_duration(expected_duration)}",
                    gui=False,
                )
            return True, "完整解码通过"
        except InterruptedError:
            raise
        except Exception as exc:
            # 验证器本身出现异常时，把错误交给单任务异常边界处理，
            # 不能再像旧版一样终止整个无人值守批次。
            self.logger.exception("完整解码验证内部异常：%s", path)
            raise RuntimeError(f"完整解码验证内部异常：{exc}") from exc
        finally:
            self._clear_current_process(process, job_handle)

    # ---------- 原文件移动、无收益记录与状态恢复 ----------

    def _state_record_key(self, input_path: str | Path) -> str:
        return os.path.normcase(str(Path(input_path).resolve(strict=False)))

    def _apply_recovery_task_status(self, task: Task) -> None:
        """把上次中断时的“待修复/修复中”状态恢复到新扫描出的 Task。

        扫描仍以当前 ffprobe 结果为准，只恢复队列阶段和诊断历史；源文件大小、
        修改时间或快速指纹有任何变化时都不恢复，避免把旧错误状态套到新文件上。
        """
        if not self.recovery_task_records:
            return
        key = self._state_record_key(task.input_path)
        record = self.recovery_task_records.get(key)
        if not isinstance(record, dict):
            return
        previous_status = str(record.get("status", ""))
        if previous_status not in {"待修复", "修复中"}:
            return

        same_size = parse_int(record.get("input_size"), -1) == task.input_size
        same_mtime = parse_int(record.get("input_mtime_ns"), -1) == task.input_mtime_ns
        recorded_fp = str(record.get("source_fingerprint", "")).strip().lower()
        same_fingerprint = not recorded_fp or recorded_fp == task.source_fingerprint.lower()
        if not (same_size and same_mtime and same_fingerprint):
            self.log(
                f"恢复记录与当前源文件不一致，重新走标准流程：{Path(task.input_path).name}",
                logging.WARNING,
            )
            return

        task.status = "待修复"
        task.message = "从上次中断状态恢复，直接进入集中修复阶段"
        task.failure_stage = str(record.get("failure_stage", ""))
        task.failure_error = str(record.get("failure_error", ""))
        task.failure_return_code = record.get("failure_return_code")
        task.failure_ffmpeg_command = str(record.get("failure_ffmpeg_command", ""))
        task.failure_seconds = max(parse_float(record.get("failure_seconds"), 0.0), 0.0)
        task.failure_frame = max(parse_int(record.get("failure_frame"), 0), 0)
        history = record.get("failure_probe_history")
        if isinstance(history, list):
            task.failure_probe_history = [str(item) for item in history][-100:]
        self.log(f"已恢复待修复任务：{Path(task.input_path).name}", logging.WARNING)

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
                f"大致失败位置：{format_duration(task.failure_seconds)}",
                f"最近帧号：{task.failure_frame if task.failure_frame > 0 else '未知'}",
                f"视频参数：{task.width}x{task.height} / {task.fps:.3f} fps / CRF {task.crf} / Preset 5",
                f"编码策略：{ENCODING_POLICY_VERSION}",
                f"源文件大小：{task.input_size} 字节",
                f"快速指纹：{task.source_fingerprint}",
                f"完整日志：{LOG_PATH}",
            ]
            if task.failure_probe_history:
                report_lines += ["", "失败点定位记录："] + [f"- {item}" for item in task.failure_probe_history]
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
            self.recovery_task_records.clear()
            data["active"] = False
            try:
                atomic_write_json(STATE_PATH, data)
            except OSError:
                self.logger.exception("关闭恢复标记失败")
            return

        # 保存上次任务记录，重新扫描后只恢复“待修复/修复中”阶段。
        # 完成、无收益和已有输出仍由现有的成品验证/无收益记录机制处理。
        self.recovery_task_records.clear()
        for raw_task in data.get("tasks") or []:
            if not isinstance(raw_task, dict):
                continue
            input_path = str(raw_task.get("input_path", "")).strip()
            if not input_path:
                continue
            try:
                key = self._state_record_key(input_path)
            except Exception:
                continue
            self.recovery_task_records[key] = raw_task

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
        current_task = next((t for t in self.tasks if t.status in {"处理中", "修复中"}), None)
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
                elif kind == "extra_validation_progress":
                    completed, total, path = payload
                    if hasattr(self, "extra_validation_progress"):
                        percent = (completed / max(total, 1)) * 100.0
                        self.extra_validation_progress["value"] = percent
                        self.extra_validation_status_var.set(f"已检查 {completed} / {total}（{percent:.1f}%）")
                        self.extra_validation_current_var.set(str(path))
                elif kind == "extra_validation_log":
                    self._append_extra_validation_log(str(payload))
                elif kind == "extra_validation_done":
                    self.extra_validation_worker = None
                    self._set_extra_validation_running(False)
                    stopped = bool(payload.get("stopped"))
                    issue_count = int(payload.get("issues", 0))
                    warning_count = int(payload.get("warnings", 0))
                    report = str(payload.get("report", ""))
                    if hasattr(self, "extra_validation_status_var"):
                        if stopped:
                            self.extra_validation_status_var.set("验证已停止")
                        elif issue_count:
                            self.extra_validation_status_var.set(
                                f"验证完成：严重问题 {issue_count} 个，警告 {warning_count} 个"
                            )
                        elif warning_count:
                            self.extra_validation_status_var.set(f"验证完成：发现 {warning_count} 个警告文件")
                        else:
                            self.extra_validation_status_var.set("验证完成：未发现问题")
                        self.extra_validation_current_var.set(report if report else "未生成验证报告")
                    if stopped:
                        self._append_extra_validation_log("验证已由用户停止。")
                    elif (issue_count or warning_count) and report:
                        report_label = "问题清单" if issue_count else "警告报告"
                        self._append_extra_validation_log(f"{report_label}已生成：{report}")
                        if self.extra_features_window is not None and self.extra_features_window.winfo_exists():
                            if issue_count:
                                message = (
                                    f"发现 {issue_count} 个严重问题文件、{warning_count} 个警告文件。"
                                    f"\n\n问题清单：\n{report}"
                                )
                            else:
                                message = (
                                    f"未发现严重解码问题，但发现 {warning_count} 个时间戳警告文件。"
                                    f"\n\n警告报告：\n{report}"
                                )
                            messagebox.showwarning("严格验证完成", message, parent=self.extra_features_window)
                    else:
                        self._append_extra_validation_log("全部文件通过；未发现严重问题或警告，按设计未创建报告文件。")
                        if self.extra_features_window is not None and self.extra_features_window.winfo_exists():
                            messagebox.showinfo(
                                "严格验证完成",
                                "全部文件通过；未发现严重问题或警告，未创建报告文件。",
                                parent=self.extra_features_window,
                            )
                elif kind == "extra_validation_failed":
                    self.extra_validation_worker = None
                    self._set_extra_validation_running(False)
                    if hasattr(self, "extra_validation_status_var"):
                        self.extra_validation_status_var.set("验证异常中止")
                        self.extra_validation_current_var.set(str(payload))
                    self._append_extra_validation_log(f"验证异常中止：{payload}")
                    if self.extra_features_window is not None and self.extra_features_window.winfo_exists():
                        messagebox.showerror("严格验证失败", str(payload), parent=self.extra_features_window)
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
        self.extra_features_button.configure(state=tk.NORMAL)
        self.worker = None

    def _safe_unlink(self, path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            self.logger.exception("删除文件失败：%s", path)

    def _on_close(self) -> None:
        if self.running or self._extra_validation_is_running():
            if not messagebox.askyesno("退出", "当前压制或严格验证仍在运行。确定停止并退出吗？"):
                return
            if self.running:
                self.stop_event.set()
                self._terminate_current_process()
            if self._extra_validation_is_running():
                self.extra_validation_stop_event.set()
                self._terminate_extra_validation_process()
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
    first_run = False
    if not CONFIG_PATH.exists():
        try:
            generate_config_template()
            first_run = True
        except OSError as exc:
            show_startup_message("error", "配置生成失败", f"无法在脚本目录生成配置文件：\n{exc}")
            return 1

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
        if first_run:
            messagebox.showinfo(
                "首次运行",
                f"已在脚本目录生成配置文件：\n{CONFIG_PATH}\n\n请打开“设置”选择 FFmpeg 和 ffprobe 路径。",
                parent=root,
            )
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("启动失败", f"程序初始化失败：\n{exc}", parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
