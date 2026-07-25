# -*- coding: utf-8 -*-
from __future__ import annotations

import configparser
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import sys
import tempfile
import threading
import tokenize
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
import urllib.error
import urllib.request

try:
    import winreg  # type: ignore
except ImportError:
    winreg = None  # type: ignore


# ============================================================================
# 固定目标与程序配置
# ============================================================================

APP_TITLE = "鸣潮 Steam 状态生成器"
APP_VERSION = "1.0"

TARGET_APP_ID = "3513350"
TARGET_DEPOT_ID = "3513351"
TARGET_ACF_FILENAME = f"appmanifest_{TARGET_APP_ID}.acf"
TARGET_GAME_NAME = "Wuthering Waves"
TARGET_INSTALL_DIR_NAME = "Wuthering Waves"
TARGET_PLACEHOLDER_EXE_NAME = "Wuthering Waves.exe"
TARGET_CLIENT_EXE_NAME = "Client-Win64-Shipping.exe"
TARGET_LANGUAGE = "schinese"

STATUS_API_URL = f"https://api.steamcmd.net/v1/info/{TARGET_APP_ID}"
CONFIG_FILENAME = "WWSteamFakeInstall.ini"

NETWORK_TIMEOUT_SECONDS = 20
NETWORK_RETRY_COUNT = 3
MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
STEAM_SHUTDOWN_TIMEOUT_SECONDS = 12
STEAM_STARTUP_TIMEOUT_SECONDS = 15


# ============================================================================
# 通用工具
# ============================================================================


def get_app_directory() -> Path:
    """返回源码文件或打包后 EXE 所在目录，使配置和默认输出保持便携。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIRECTORY = get_app_directory()
CONFIG_PATH = APP_DIRECTORY / CONFIG_FILENAME


def enable_windows_high_dpi_awareness() -> None:
    """尽早设置 Per-Monitor DPI Awareness V2，并兼容较旧 Windows。"""
    if os.name != "nt":
        return

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_tk_dpi_scaling(root: tk.Tk) -> float:
    """根据当前系统 DPI 调整 Tk 的点到像素换算比例。"""
    if os.name != "nt":
        return 1.0

    dpi = 96
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(root.winfo_id()))
    except Exception:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
        except Exception:
            dpi = 96

    root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
    return max(1.0, dpi / 96.0)


def to_windows_path_string(path: Path | str) -> str:
    """统一为 Windows 反斜杠路径。"""
    return str(path).replace("/", "\\")


def escape_acf_value(value: str) -> str:
    """转义 Valve KeyValues 字符串中的反斜杠和双引号。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def is_valid_numeric_identifier(value: str) -> bool:
    """Build ID 和 Manifest ID 必须是非零十进制整数。"""
    return bool(re.fullmatch(r"[1-9]\d*", str(value).strip()))


def format_timestamp(timestamp: float | None = None) -> str:
    """把 Unix 时间戳转换为本地时间字符串；未传入时使用当前系统时间。"""
    if timestamp is None:
        timestamp = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


class ElevationRequiredError(RuntimeError):
    """当前权限不足以安全管理目标 Steam 主进程。"""


def _shell_execute(verb: str, file: str, parameters: str | None, cwd: str) -> int:
    """调用 Unicode ShellExecuteW。"""
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(None, verb, file, parameters, cwd, 1)
    return int(result or 0)


def is_current_process_elevated() -> bool:
    """判断当前进程是否已获得管理员提升权限。"""
    if os.name != "nt":
        return False

    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    token = ctypes.c_void_p()
    elevation = ctypes.c_uint32(0)
    returned = ctypes.c_uint32(0)
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int

    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return False

    try:
        ok = advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        )
        return bool(ok and elevation.value)
    finally:
        kernel32.CloseHandle(token)


def _build_elevated_restart_command() -> tuple[str, str, str]:
    """生成提升后自动续跑所需的程序、参数和工作目录。"""
    args = [arg for arg in sys.argv[1:] if arg != "--elevated-resume"]
    args.append("--elevated-resume")

    if getattr(sys, "frozen", False):
        executable = str(Path(sys.executable).resolve())
        parameters = subprocess.list2cmdline(args)
    else:
        executable = str(Path(sys.executable).resolve())
        script = str(Path(__file__).resolve())
        parameters = subprocess.list2cmdline([script, *args])

    return executable, parameters, str(APP_DIRECTORY)


def restart_self_elevated() -> None:
    """通过 Windows UAC 以管理员权限重新启动脚本或打包后的 EXE。"""
    if os.name != "nt":
        raise RuntimeError("当前系统不支持 Windows UAC 提权。")
    if is_current_process_elevated():
        return

    executable, parameters, cwd = _build_elevated_restart_command()
    result = _shell_execute("runas", executable, parameters, cwd)
    if result <= 32:
        raise RuntimeError("未能获得管理员权限。可能是用户取消了 UAC。")


# ============================================================================
# 便携配置
# ============================================================================

# INI 的节名和键名全部使用英文，避免中文标识符进入程序内部配置接口。
# 每个键对应的中文说明由程序固定输出，而不是依赖 ConfigParser 保留原注释；
# 这样即使程序多次保存配置，注释也不会丢失或逐渐变得杂乱。
CONFIG_SECTION = "Settings"
CONFIG_SCHEMA: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ManualPathMode",
        "0",
        (
            "路径模式开关。0 表示自动检测 Steam 安装路径和 Steam 库；",
            "1 表示手动指定 ACF 输出目录，并允许 SteamExecutablePath 留空。",
        ),
    ),
    (
        "SteamLibraryPath",
        "",
        (
            "自动检测模式下选中的 Steam 库根目录。",
            "程序会把 ACF 写入该目录下的 steamapps 文件夹。",
        ),
    ),
    (
        "CustomAcfOutputDirectory",
        "",
        (
            "手动路径模式下的 ACF 输出目录。",
            "留空时默认使用本程序所在目录；也可以填写 Steam 库、steamapps 或普通目录。",
        ),
    ),
    (
        "SteamExecutablePath",
        "",
        (
            "Steam 主程序 steam.exe 的完整路径。",
            "自动检测模式下此项必须有效；手动路径模式下留空会跳过关闭和重新启动 Steam。",
        ),
    ),
    (
        "EnableLaunchOptions",
        "0",
        (
            "是否启用第二个“Steam 启动选项”标签页。",
            "0 表示禁用；1 表示启用并允许生成国服客户端启动命令。",
        ),
    ),
    (
        "ClientExecutablePath",
        "",
        (
            "国服 Client-Win64-Shipping.exe 的完整路径。",
            "该路径只用于生成 Steam 启动选项，不参与 ACF 文件生成。",
        ),
    ),
    (
        "UseDx11",
        "0",
        (
            "是否在 Steam 启动选项中加入 -dx11。",
            "0 表示不加入；1 表示强制客户端使用 DirectX 11。",
        ),
    ),
    (
        "SkipSplash",
        "0",
        (
            "是否在 Steam 启动选项中加入 -SkipSplash。",
            "0 表示不加入；1 表示跳过启动阶段的 LOGO 画面。",
        ),
    ),
    (
        "LastBuildId",
        "",
        (
            "最近一次从状态 API 成功获取的 public 分支 Build ID。",
            "该值仅用于界面显示和故障排查，生成时仍会重新联网获取最新状态。",
        ),
    ),
    (
        "LastManifestId",
        "",
        (
            "最近一次成功获取的 Depot 3513351 public Manifest ID。",
            "该值不会在网络失败时被静默当作最新数据使用。",
        ),
    ),
    (
        "LastFetchTime",
        "",
        (
            "最近一次成功生成 ACF 时记录的本地时间。",
            "仅用于提示数据获取时间，不参与 Steam 状态判断。",
        ),
    ),
)

CONFIG_DEFAULTS = {key: default for key, default, _ in CONFIG_SCHEMA}
CONFIG_KEYS = frozenset(CONFIG_DEFAULTS)

# Lite.2 使用中文节名和中文键。新版本只在新的英文配置文件不存在时尝试迁移，
# 迁移完成后不会删除旧文件，便于用户回退旧版本或手工核对设置。
LEGACY_CONFIG_FILENAMES = ("鸣潮Steam状态生成器_轻量版.ini",)
LEGACY_CONFIG_SECTION = "设置"
LEGACY_CONFIG_KEY_MAP = {
    "手动路径模式": "ManualPathMode",
    "Steam库路径": "SteamLibraryPath",
    "ACF保存目录": "CustomAcfOutputDirectory",
    "SteamEXE路径": "SteamExecutablePath",
    "启用启动项生成": "EnableLaunchOptions",
    "客户端EXE路径": "ClientExecutablePath",
    "启动项DX11": "UseDx11",
    "启动项跳过LOGO": "SkipSplash",
    "上次BuildID": "LastBuildId",
    "上次ManifestID": "LastManifestId",
    "上次获取时间": "LastFetchTime",
}


class PortableConfig:
    """
    管理程序目录中的便携 INI 配置。

    实现重点：
        1. 对外只暴露英文键，避免 GUI 和业务逻辑继续依赖旧中文键名。
        2. 保存时按固定架构重写整份 INI，确保详细中文注释始终存在。
        3. 使用同目录临时文件、fsync 和 os.replace，避免程序中断后留下半份配置。
        4. 首次运行新版本时可读取 Lite.2 的旧中文配置，但不会修改或删除旧文件。
    """

    def __init__(
        self,
        path: Path,
        legacy_paths: tuple[Path, ...] | None = None,
    ) -> None:
        """
        建立配置对象并立即加载。

        legacy_paths 主要用于自检和未来兼容扩展；正常运行时省略该参数，程序会在
        程序目录中查找已声明的旧版配置文件。
        """
        self.path = path
        self.legacy_paths = (
            tuple(APP_DIRECTORY / name for name in LEGACY_CONFIG_FILENAMES)
            if legacy_paths is None
            else tuple(legacy_paths)
        )
        self.values: dict[str, str] = dict(CONFIG_DEFAULTS)
        self.migrated_from: Path | None = None
        self.load()

    @staticmethod
    def _sanitize_value(value: str) -> str:
        """移除换行，防止路径或外部文本破坏 INI 的一键一行结构。"""
        return str(value).replace("\r", " ").replace("\n", " ").strip()

    @staticmethod
    def _read_parser(path: Path) -> configparser.ConfigParser:
        """以 UTF-8/UTF-8 BOM 兼容模式读取 INI，并保持键名大小写。"""
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        with path.open("r", encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        return parser

    def _load_current_config(self) -> bool:
        """读取新格式配置；文件不存在或无法解析时返回 False。"""
        if not self.path.is_file():
            return False
        try:
            parser = self._read_parser(self.path)
        except (OSError, UnicodeError, configparser.Error):
            return False
        if not parser.has_section(CONFIG_SECTION):
            return False

        for key in CONFIG_KEYS:
            if parser.has_option(CONFIG_SECTION, key):
                self.values[key] = self._sanitize_value(
                    parser.get(CONFIG_SECTION, key, fallback=CONFIG_DEFAULTS[key])
                )
        return True

    def _load_legacy_config(self) -> bool:
        """在新配置不存在时迁移 Lite.2 中文键；旧文件始终保留。"""
        for legacy_path in self.legacy_paths:
            if not legacy_path.is_file():
                continue
            try:
                parser = self._read_parser(legacy_path)
            except (OSError, UnicodeError, configparser.Error):
                continue
            if not parser.has_section(LEGACY_CONFIG_SECTION):
                continue

            imported_any = False
            for legacy_key, current_key in LEGACY_CONFIG_KEY_MAP.items():
                if parser.has_option(LEGACY_CONFIG_SECTION, legacy_key):
                    self.values[current_key] = self._sanitize_value(
                        parser.get(LEGACY_CONFIG_SECTION, legacy_key, fallback="")
                    )
                    imported_any = True
            if imported_any:
                self.migrated_from = legacy_path
                return True
        return False

    def load(self) -> None:
        """恢复默认值后读取新配置；若新配置不存在，再尝试迁移旧配置。"""
        self.values = dict(CONFIG_DEFAULTS)
        if self._load_current_config():
            return
        self._load_legacy_config()

    def get(self, key: str, default: str = "") -> str:
        """读取已声明的英文配置键；未知键返回调用方给定的默认值。"""
        if key not in CONFIG_KEYS:
            return default
        return self.values.get(key, default).strip()

    def get_bool(self, key: str, default: bool = False) -> bool:
        """兼容 0/1、true/false、yes/no 和 on/off 的布尔值写法。"""
        value = self.get(key, "1" if default else "0").lower()
        return value in ("1", "true", "yes", "on")

    def set(self, key: str, value: str) -> None:
        """只允许写入架构中声明的英文键，避免拼写错误产生无效配置项。"""
        if key not in CONFIG_KEYS:
            raise KeyError(f"未知配置键：{key}")
        self.values[key] = self._sanitize_value(value)

    def _render(self) -> str:
        """按固定顺序生成带详细中文注释的 UTF-8 INI 文本。"""
        lines = [
            "; WWSteamFakeInstall 便携配置文件",
            "; 本文件由程序自动维护。键名使用英文，中文注释说明每个选项的用途。",
            "; 可以在程序关闭时手工修改；布尔值建议使用 0（关闭）或 1（启用）。",
            "; 路径无需额外加引号，程序会自动处理空格。",
            "",
            f"[{CONFIG_SECTION}]",
        ]
        for key, default, comments in CONFIG_SCHEMA:
            lines.append("")
            lines.extend(f"; {comment}" for comment in comments)
            value = self.values.get(key, default)
            lines.append(f"{key}={self._sanitize_value(value)}")
        lines.append("")
        return "\n".join(lines)

    def save(self) -> None:
        """
        原子保存配置。

        临时文件与正式 INI 位于同一目录，因此 os.replace 在 Windows 上可以完成
        同卷原子替换。写入前调用 fsync，尽量降低系统异常中断时配置丢失的风险。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self._render().encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if temp_path.read_bytes() != content:
                raise OSError("临时配置文件回读验证失败。")
            os.replace(temp_path, self.path)
            if self.path.read_bytes() != content:
                raise OSError("正式配置文件回读验证失败。")
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


# ============================================================================
# Steam 路径和库自动检测
# ============================================================================


def _read_registry_string(root: Any, subkey: str, value_name: str) -> str:
    """安全读取 Windows 注册表字符串；非 Windows 或读取失败时返回空串。"""
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
        return str(value).strip()
    except OSError:
        return ""


def detect_steam_executable(saved_path: str = "") -> Path | None:
    """按已保存路径、注册表和常见目录依次检测 steam.exe。"""
    candidates: list[Path] = []

    saved = saved_path.strip().strip('"')
    if saved:
        candidates.append(Path(saved))

    if os.name == "nt" and winreg is not None:
        steam_exe = _read_registry_string(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Valve\Steam",
            "SteamExe",
        )
        if steam_exe:
            candidates.append(Path(steam_exe))

        steam_path = _read_registry_string(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Valve\Steam",
            "SteamPath",
        )
        if steam_path:
            candidates.append(Path(steam_path) / "steam.exe")

        for key_name in (
            r"SOFTWARE\WOW6432Node\Valve\Steam",
            r"SOFTWARE\Valve\Steam",
        ):
            install_path = _read_registry_string(
                winreg.HKEY_LOCAL_MACHINE,
                key_name,
                "InstallPath",
            )
            if install_path:
                candidates.append(Path(install_path) / "steam.exe")

    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\Steam\steam.exe"),
            Path(r"C:\Program Files\Steam\steam.exe"),
            Path(r"D:\Steam\steam.exe"),
            Path(r"D:\Program Files (x86)\Steam\steam.exe"),
            Path(r"D:\Program Files\Steam\steam.exe"),
            Path(r"E:\Steam\steam.exe"),
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(str(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file() and candidate.name.lower() == "steam.exe":
            return candidate.resolve()
    return None


def _parse_library_folders_vdf(vdf_path: Path) -> list[Path]:
    """从 libraryfolders.vdf 中提取所有有效 Steam 库根目录。"""
    if not vdf_path.is_file():
        return []

    try:
        raw = vdf_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    results: list[Path] = []
    for match in re.finditer(r'"path"\s+"((?:\\.|[^"\\])*)"', raw, re.IGNORECASE):
        escaped = match.group(1)
        value = escaped.replace("\\\\", "\\").replace('\\"', '"')
        path = Path(value)
        if path.is_dir():
            results.append(path.resolve())
    return results


def detect_steam_libraries(steam_exe: Path | None) -> list[Path]:
    """根据 Steam 安装目录和 libraryfolders.vdf 返回库根目录列表。"""
    if steam_exe is None:
        return []

    steam_root = steam_exe.parent.resolve()
    candidates = [steam_root]
    candidates.extend(
        _parse_library_folders_vdf(steam_root / "steamapps" / "libraryfolders.vdf")
    )

    results: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = os.path.normcase(os.path.abspath(str(path)))
        if normalized in seen:
            continue
        seen.add(normalized)
        if path.is_dir():
            results.append(path.resolve())
    return results


def normalize_steam_library_and_steamapps(user_path: str) -> tuple[Path, Path]:
    """
    自动检测模式下，将库根目录、steamapps、common 或 common 下的游戏目录
    统一解析为：(Steam 库根目录, steamapps 目录)。
    """
    raw = user_path.strip().strip('"')
    if not raw:
        raise ValueError("尚未选择 Steam 库位置。")

    selected = Path(raw).expanduser()
    name = selected.name.lower()

    if name == "steamapps":
        steamapps = selected
        library = selected.parent
    elif name == "common" and selected.parent.name.lower() == "steamapps":
        steamapps = selected.parent
        library = steamapps.parent
    elif (
        selected.parent.name.lower() == "common"
        and selected.parent.parent.name.lower() == "steamapps"
    ):
        steamapps = selected.parent.parent
        library = steamapps.parent
    else:
        library = selected
        steamapps = selected / "steamapps"

    if library.exists() and not library.is_dir():
        raise ValueError("所选 Steam 库路径不是文件夹。")

    return library.resolve(strict=False), steamapps.resolve(strict=False)


# ============================================================================
# 保存目录处理
# ============================================================================


def resolve_acf_output_directory(path_text: str) -> tuple[Path, Path | None]:
    """
    返回：(实际写入 ACF 的目录, 可识别的 steamapps 目录)。

    支持选择：
        - 任意普通目录：ACF 直接写入该目录；
        - Steam 库根目录：写入其 steamapps；
        - steamapps：直接写入；
        - steamapps\\common 或其中某个游戏目录：回推到 steamapps；
        - 留空：写入程序目录。
    """
    raw = path_text.strip().strip('"')
    selected = Path(raw).expanduser() if raw else APP_DIRECTORY

    # 兼容配置中误保存了完整 ACF 文件路径。
    if selected.name.lower() == TARGET_ACF_FILENAME.lower():
        selected = selected.parent

    selected_name = selected.name.lower()
    steamapps: Path | None = None

    if selected_name == "steamapps":
        output = selected
        steamapps = selected
    elif selected_name == "common" and selected.parent.name.lower() == "steamapps":
        output = selected.parent
        steamapps = selected.parent
    elif (
        selected.parent.name.lower() == "common"
        and selected.parent.parent.name.lower() == "steamapps"
    ):
        output = selected.parent.parent
        steamapps = selected.parent.parent
    elif (selected / "steamapps").is_dir():
        output = selected / "steamapps"
        steamapps = output
    else:
        output = selected

    if output.exists() and not output.is_dir():
        raise ValueError("ACF 保存位置不是文件夹。")

    return output.resolve(strict=False), (
        steamapps.resolve(strict=False) if steamapps is not None else None
    )


# ============================================================================
# 最新状态获取
# ============================================================================


def _json_scalar_to_string(value: Any) -> str:
    """只接受 API 中可安全转换的标量值，拒绝列表、对象和布尔值。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def parse_latest_state(payload: dict[str, Any]) -> tuple[str, str]:
    """解析 steamcmd.net API，兼容 Manifest 为字符串或带 gid 的对象。"""
    if payload.get("status") != "success":
        raise ValueError("状态 API 未返回 success。")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("状态 API 缺少 data 对象。")

    app_data = data.get(TARGET_APP_ID)
    if not isinstance(app_data, dict):
        raise ValueError(f"状态 API 缺少 AppID {TARGET_APP_ID}。")

    depots = app_data.get("depots")
    if not isinstance(depots, dict):
        raise ValueError("状态 API 缺少 depots 对象。")

    branches = depots.get("branches")
    public_branch = branches.get("public") if isinstance(branches, dict) else None
    build_id = (
        _json_scalar_to_string(public_branch.get("buildid"))
        if isinstance(public_branch, dict)
        else ""
    )

    depot = depots.get(TARGET_DEPOT_ID)
    manifests = depot.get("manifests") if isinstance(depot, dict) else None
    public_manifest = manifests.get("public") if isinstance(manifests, dict) else None
    if isinstance(public_manifest, dict):
        manifest_id = _json_scalar_to_string(public_manifest.get("gid"))
    else:
        manifest_id = _json_scalar_to_string(public_manifest)

    if not is_valid_numeric_identifier(build_id):
        raise ValueError("状态 API 返回的 Build ID 无效。")
    if not is_valid_numeric_identifier(manifest_id):
        raise ValueError("状态 API 返回的 Manifest ID 无效。")

    return build_id, manifest_id


def fetch_latest_state(
    log_callback: Callable[[str], None] | None = None,
    opener: Callable[..., Any] | None = None,
) -> tuple[str, str]:
    """从公开 API 获取最新 public Build/Manifest，并进行有限次数重试。"""
    if opener is None:
        opener = urllib.request.urlopen

    last_error: Exception | None = None
    for attempt in range(1, NETWORK_RETRY_COUNT + 1):
        if log_callback:
            log_callback(f"正在获取最新状态，第 {attempt}/{NETWORK_RETRY_COUNT} 次。")

        request = urllib.request.Request(
            STATUS_API_URL,
            headers={
                "User-Agent": f"WutheringWavesSteamStateGeneratorLite/{APP_VERSION}",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )

        try:
            with opener(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(raw) > MAX_API_RESPONSE_BYTES:
                raise ValueError("状态 API 返回内容异常过大，已停止解析。")
            payload = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("状态 API 返回内容不是 JSON 对象。")
            return parse_latest_state(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            urllib.error.URLError,
        ) as exc:
            last_error = exc
            if attempt < NETWORK_RETRY_COUNT:
                time.sleep(float(attempt))

    detail = str(last_error) if last_error else "未知错误"
    raise RuntimeError(f"无法获取鸣潮最新状态：{detail}")


# ============================================================================
# Steam 主进程处理
# ============================================================================


def _no_console_creation_flags() -> int:
    """在 Windows 下隐藏 tasklist/taskkill 的临时控制台窗口；其他平台返回 0。"""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _normalize_executable_path(path: Path | str) -> str:
    """把可执行文件路径转换为适合 Windows 大小写不敏感比较的绝对形式。"""
    return os.path.normcase(os.path.abspath(to_windows_path_string(path)))


def _query_process_image_path(pid: int) -> Path | None:
    """使用 Windows API 查询进程实际 EXE 路径。"""
    if os.name != "nt":
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None

    try:
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def _query_process_elevation(pid: int) -> bool | None:
    """查询目标进程是否使用提升令牌；查询失败时返回 None。"""
    if os.name != "nt":
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    advapi32.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.OpenProcessToken.restype = ctypes.c_int
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetTokenInformation.restype = ctypes.c_int

    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None

    token = ctypes.c_void_p()
    try:
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        elevation = ctypes.c_uint32(0)
        returned = ctypes.c_uint32(0)
        ok = advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        )
        if not ok:
            return None
        return bool(elevation.value)
    finally:
        if token.value:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)


def enumerate_steam_processes() -> list[tuple[int, Path | None]]:
    """只枚举进程名精确等于 steam.exe 的 PID。"""
    if os.name != "nt":
        return []

    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=_no_console_creation_flags(),
        check=False,
    )

    processes: list[tuple[int, Path | None]] = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 2 or row[0].strip().lower() != "steam.exe":
            continue
        pid_text = row[1].strip()
        if pid_text.isdigit():
            pid = int(pid_text)
            processes.append((pid, _query_process_image_path(pid)))
    return processes


def _filter_target_steam_processes(
    steam_exe: Path,
    processes: list[tuple[int, Path | None]],
) -> tuple[list[int], list[int], list[tuple[int, Path]]]:
    """
    按实际映像路径把 steam.exe 分为目标、未知和外部三组。

    只允许终止目标组。无法查询路径的进程进入未知组并触发提权或停止操作；
    路径不同的同名进程进入外部组，只记录日志，绝不结束。
    """
    expected = _normalize_executable_path(steam_exe)
    matched: list[int] = []
    unknown: list[int] = []
    foreign: list[tuple[int, Path]] = []

    for pid, image_path in processes:
        if image_path is None:
            unknown.append(pid)
        elif _normalize_executable_path(image_path) == expected:
            matched.append(pid)
        else:
            foreign.append((pid, image_path))
    return matched, unknown, foreign


def steam_management_requires_elevation(steam_exe: Path) -> bool:
    """目标 Steam 权限高于本工具时返回 True。"""
    if os.name != "nt" or is_current_process_elevated():
        return False

    processes = enumerate_steam_processes()
    if not processes:
        return False

    matched, unknown, _ = _filter_target_steam_processes(steam_exe, processes)
    if unknown:
        return True

    for pid in matched:
        elevated = _query_process_elevation(pid)
        if elevated is None or elevated:
            return True
    return False


def terminate_steam_process(
    steam_exe: Path,
    log_callback: Callable[[str], None] | None = None,
) -> bool:
    """只结束路径与用户设置完全一致的 steam.exe。"""
    if os.name != "nt":
        return False
    if steam_exe.name.lower() != "steam.exe" or not steam_exe.is_file():
        raise ValueError("Steam.exe 路径无效。")

    deadline = time.monotonic() + STEAM_SHUTDOWN_TIMEOUT_SECONDS
    logged_foreign: set[int] = set()
    ended_any = False

    while True:
        matched, unknown, foreign = _filter_target_steam_processes(
            steam_exe, enumerate_steam_processes()
        )

        for pid, image_path in foreign:
            if pid not in logged_foreign and log_callback:
                log_callback(f"忽略同名但路径不同的进程：PID {pid}，{image_path}")
                logged_foreign.add(pid)

        if unknown:
            detail = "、".join(str(pid) for pid in unknown)
            if not is_current_process_elevated():
                raise ElevationRequiredError(
                    f"当前权限无法确认 steam.exe 的实际路径，PID：{detail}"
                )
            raise RuntimeError(
                f"提升后仍无法确认 steam.exe 的实际路径，PID：{detail}。为避免误伤已停止。"
            )

        if not matched:
            if log_callback:
                log_callback("目标 Steam 主进程未运行或已经完全退出。")
            return ended_any

        ended_any = True
        if log_callback:
            log_callback(
                "正在关闭路径完全匹配的 Steam 主进程："
                + "、".join(str(pid) for pid in matched)
            )

        for pid in matched:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                errors="replace",
                creationflags=_no_console_creation_flags(),
                check=False,
            )
            if result.returncode != 0:
                remaining = {item_pid for item_pid, _ in enumerate_steam_processes()}
                if pid not in remaining:
                    continue
                error_text = (result.stderr or result.stdout or "未知错误").strip()
                if not is_current_process_elevated():
                    raise ElevationRequiredError(
                        f"当前权限无法结束 Steam（PID {pid}）：{error_text}"
                    )
                raise RuntimeError(f"无法结束 Steam（PID {pid}）：{error_text}")

        if time.monotonic() >= deadline:
            remaining = _filter_target_steam_processes(
                steam_exe, enumerate_steam_processes()
            )[0]
            raise RuntimeError(
                "无法完全结束目标 Steam 主进程。仍在运行的 PID："
                + "、".join(str(pid) for pid in remaining)
            )
        time.sleep(0.25)


def start_steam_process(
    steam_exe: Path,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """启动用户设置的 Steam.exe，并确认对应路径的进程出现。"""
    if os.name != "nt":
        return
    if steam_exe.name.lower() != "steam.exe" or not steam_exe.is_file():
        raise ValueError("Steam.exe 路径无效。")

    matched, unknown, _ = _filter_target_steam_processes(steam_exe, enumerate_steam_processes())
    if unknown:
        detail = "、".join(str(pid) for pid in unknown)
        if not is_current_process_elevated():
            raise ElevationRequiredError(
                f"当前权限无法确认已经运行的 steam.exe，PID：{detail}"
            )
        raise RuntimeError(
            f"提升后仍无法确认已经运行的 steam.exe，PID：{detail}。"
        )
    if matched:
        if log_callback:
            log_callback("目标 Steam 已经运行，无需重复启动。")
        return

    creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    used_shell_elevation = False
    try:
        subprocess.Popen(
            [str(steam_exe)],
            cwd=str(steam_exe.parent),
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 740:
            result = _shell_execute(
                "runas", str(steam_exe), None, str(steam_exe.parent)
            )
            if result <= 32:
                raise RuntimeError("Steam 需要管理员权限，但 UAC 启动被取消或失败。") from exc
            used_shell_elevation = True
            if log_callback:
                log_callback("Steam 被设置为以管理员权限运行，已通过 UAC 启动。")
        else:
            raise RuntimeError(f"无法启动 Steam：{exc}") from exc

    deadline = time.monotonic() + STEAM_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        matched, unknown, _ = _filter_target_steam_processes(
            steam_exe, enumerate_steam_processes()
        )
        if matched:
            if log_callback:
                log_callback("Steam 主客户端已启动。")
            return
        if unknown and used_shell_elevation:
            if log_callback:
                log_callback("Steam 已以管理员权限启动，当前进程无法读取其映像路径。")
            return
        if unknown:
            if not is_current_process_elevated():
                raise ElevationRequiredError(
                    "Steam 启动后出现当前权限无法确认的 steam.exe 进程。"
                )
            raise RuntimeError("Steam 启动后出现无法确认路径的 steam.exe 进程。")
        time.sleep(0.25)

    raise RuntimeError("已执行 Steam 启动命令，但未在等待时间内检测到目标进程。")


# ============================================================================
# ACF 固定模板与原子写入
# ============================================================================


def build_acf_content(
    steam_exe: Path | None,
    build_id: str,
    manifest_id: str,
) -> str:
    """根据最新状态生成固定鸣潮 ACF；Steam.exe 可以为空。"""
    if steam_exe is not None:
        if steam_exe.name.lower() != "steam.exe" or not steam_exe.is_file():
            raise ValueError("Steam.exe 路径无效。")
        launcher_path = escape_acf_value(to_windows_path_string(steam_exe))
    else:
        launcher_path = ""

    if not is_valid_numeric_identifier(build_id):
        raise ValueError("Build ID 无效。")
    if not is_valid_numeric_identifier(manifest_id):
        raise ValueError("Manifest ID 无效。")

    lines = [
        '"AppState"',
        "{",
        f'\t"appid"\t\t"{TARGET_APP_ID}"',
        '\t"universe"\t\t"1"',
        f'\t"LauncherPath"\t\t"{launcher_path}"',
        f'\t"name"\t\t"{TARGET_GAME_NAME}"',
        '\t"StateFlags"\t\t"4"',
        f'\t"installdir"\t\t"{TARGET_INSTALL_DIR_NAME}"',
        '\t"LastUpdated"\t\t"0"',
        '\t"LastPlayed"\t\t"0"',
        '\t"SizeOnDisk"\t\t"0"',
        '\t"StagingSize"\t\t"0"',
        f'\t"buildid"\t\t"{build_id}"',
        '\t"LastOwner"\t\t"0"',
        '\t"DownloadType"\t\t"1"',
        '\t"UpdateResult"\t\t"0"',
        '\t"BytesToDownload"\t\t"0"',
        '\t"BytesDownloaded"\t\t"0"',
        '\t"BytesToStage"\t\t"0"',
        '\t"BytesStaged"\t\t"0"',
        '\t"TargetBuildID"\t\t"0"',
        '\t"AutoUpdateBehavior"\t\t"1"',
        '\t"AllowOtherDownloadsWhileRunning"\t\t"0"',
        '\t"ScheduledAutoUpdate"\t\t"0"',
        '\t"InstalledDepots"',
        "\t{",
        f'\t\t"{TARGET_DEPOT_ID}"',
        "\t\t{",
        f'\t\t\t"manifest"\t\t"{manifest_id}"',
        '\t\t\t"size"\t\t"0"',
        "\t\t}",
        "\t}",
        '\t"SharedDepots"',
        "\t{",
        '\t\t"228989"\t\t"228980"',
        "\t}",
        '\t"UserConfig"',
        "\t{",
        f'\t\t"language"\t\t"{TARGET_LANGUAGE}"',
        "\t}",
        '\t"MountedConfig"',
        "\t{",
        f'\t\t"language"\t\t"{TARGET_LANGUAGE}"',
        "\t}",
        "}",
        "",
    ]
    return "\r\n".join(lines)


def validate_generated_acf(
    content: str,
    steam_exe: Path | None,
    build_id: str,
    manifest_id: str,
) -> None:
    """
    在写盘前验证 ACF 与固定模板逐字符一致，并再次检查关键业务字段。

    逐字符比较用于防止模板拼接、换行或转义逻辑被意外改坏；关键字段检查则让
    报错更明确，便于定位 AppID、Build ID、Manifest ID 或 LastOwner 的缺失。
    """
    expected = build_acf_content(steam_exe, build_id, manifest_id)
    if content != expected:
        raise ValueError("生成的 ACF 内容与固定模板不一致。")

    required = (
        f'"appid"\t\t"{TARGET_APP_ID}"',
        f'"buildid"\t\t"{build_id}"',
        '\t"LastOwner"\t\t"0"',
        f'"manifest"\t\t"{manifest_id}"',
        '"StateFlags"\t\t"4"',
    )
    for fragment in required:
        if fragment not in content:
            raise ValueError(f"生成的 ACF 缺少必要内容：{fragment}")


def atomic_replace_text(target: Path, content: str) -> None:
    """在目标同目录写临时文件，回读后原子覆盖正式文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")

    if target.exists():
        try:
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            pass

    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

        if temp_path.read_bytes() != encoded:
            raise OSError("临时 ACF 回读结果与待写入内容不一致。")

        os.replace(temp_path, target)
        if target.read_bytes() != encoded:
            raise OSError("正式 ACF 写入后的回读验证失败。")
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def ensure_placeholder_game_executable(steamapps: Path) -> tuple[Path, bool]:
    """仅在识别出 steamapps 时创建零字节占位 EXE；已有文件绝不覆盖。"""
    placeholder = steamapps / "common" / TARGET_INSTALL_DIR_NAME / TARGET_PLACEHOLDER_EXE_NAME
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    if placeholder.exists():
        if placeholder.is_dir():
            raise OSError(f"占位 EXE 路径被同名文件夹占用：{placeholder}")
        return placeholder, False

    with placeholder.open("xb"):
        pass
    return placeholder, True


# ============================================================================
# Steam 启动选项
# ============================================================================


def validate_client_executable(path_text: str, must_exist: bool = True) -> Path:
    """验证用户选择的是目标客户端文件名，并按调用场景决定是否检查实际存在。"""
    path = Path(path_text.strip().strip('"'))
    if path.name.lower() != TARGET_CLIENT_EXE_NAME.lower():
        raise ValueError(f"必须选择 {TARGET_CLIENT_EXE_NAME}。")
    if must_exist and not path.is_file():
        raise ValueError("所选国服客户端 EXE 不存在。")
    return path


def build_launch_option(exe_path: Path, use_dx11: bool, skip_splash: bool) -> str:
    """根据两个复选框组合生成唯一一条 Steam 启动命令。"""
    quoted = f'"{to_windows_path_string(exe_path)}"'
    args: list[str] = []
    if use_dx11:
        args.append("-dx11")
    if skip_splash:
        args.append("-SkipSplash")
    args.append("%command%")
    return f"{quoted} {' '.join(args)}"


# ============================================================================
# GUI
# ============================================================================


class MainWindow:
    """
    管理紧凑双标签页 GUI、配置同步、后台任务和主线程事件分发。

    该类不在工作线程中直接操作 Tk 控件。后台任务只向 self.events 写入结构化事件，
    主线程通过 after 定时取出事件，从而避免 Tkinter 跨线程调用导致随机崩溃。
    """

    def __init__(self, root: tk.Tk) -> None:
        """加载便携配置、建立状态变量、创建界面，并处理提权后的自动续跑。"""
        self.root = root
        self.config = PortableConfig(CONFIG_PATH)
        self.busy = False
        self.closing = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        dpi_scale = float(getattr(self.root, "_app_dpi_scale", 1.0))
        screen_w = max(800, self.root.winfo_screenwidth())
        screen_h = max(600, self.root.winfo_screenheight())
        width = min(int(740 * dpi_scale), int(screen_w * 0.90))
        height = min(int(560 * dpi_scale), int(screen_h * 0.88))
        min_width = min(int(650 * dpi_scale), int(screen_w * 0.82))
        min_height = min(int(470 * dpi_scale), int(screen_h * 0.78))
        self.root.minsize(min_width, min_height)
        self.root.geometry(f"{width}x{height}")

        # 两种路径模式分别保存各自的目录，切换时不互相覆盖。
        self.manual_mode_var = tk.BooleanVar(
            value=self.config.get_bool("ManualPathMode", False)
        )
        self.auto_library_path = self.config.get("SteamLibraryPath")
        self.manual_save_path = self.config.get("CustomAcfOutputDirectory")
        self.path_var = tk.StringVar(
            value=self.manual_save_path
            if self.manual_mode_var.get()
            else self.auto_library_path
        )
        self._last_manual_mode = bool(self.manual_mode_var.get())

        self.steam_exe_var = tk.StringVar(value=self.config.get("SteamExecutablePath"))
        self.enable_launch_var = tk.BooleanVar(
            value=self.config.get_bool("EnableLaunchOptions", False)
        )
        self.client_exe_var = tk.StringVar(value=self.config.get("ClientExecutablePath"))
        self.dx11_var = tk.BooleanVar(value=self.config.get_bool("UseDx11", False))
        self.skip_splash_var = tk.BooleanVar(
            value=self.config.get_bool("SkipSplash", False)
        )

        self.build_var = tk.StringVar(value=self.config.get("LastBuildId") or "尚未获取")
        self.manifest_var = tk.StringVar(
            value=self.config.get("LastManifestId") or "尚未获取"
        )
        self.fetch_time_var = tk.StringVar(
            value=self.config.get("LastFetchTime") or "尚未获取"
        )
        self.command_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self._create_styles()
        self._create_ui()
        self._refresh_path_mode_ui()
        if not self.manual_mode_var.get():
            self._auto_detect_steam_environment(log_detection=True)
        self._refresh_launch_tab_state()
        self._refresh_launch_option()

        if self.config.migrated_from is not None:
            migrated_from = self.config.migrated_from
            try:
                self._save_current_config(show_error=False)
                self._append_log(
                    f"已从旧版配置迁移设置：{migrated_from.name}；"
                    f"新配置已保存为 {CONFIG_FILENAME}。"
                )
            except Exception as exc:
                # 迁移数据已经加载到内存，只有新 INI 落盘失败；保留旧配置可供下次重试。
                self._append_log(f"已读取旧版配置，但新配置保存失败：{exc}")

        self.manual_mode_var.trace_add("write", lambda *_: self._on_path_mode_changed())
        self.client_exe_var.trace_add("write", lambda *_: self._refresh_launch_option())
        self.dx11_var.trace_add("write", lambda *_: self._on_launch_option_flags_changed())
        self.skip_splash_var.trace_add("write", lambda *_: self._on_launch_option_flags_changed())
        self.enable_launch_var.trace_add("write", lambda *_: self._on_launch_options_enabled_changed())

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._process_worker_events)
        if "--elevated-resume" in sys.argv:
            self.root.after(350, self._resume_generation_after_elevation)

    def _create_styles(self) -> None:
        """配置 Windows 友好的 ttk 主题、标题字体和主要操作按钮尺寸。"""
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 7))

    def _create_ui(self) -> None:
        """创建双标签页主框架；窗口尺寸与 Lite.1 保持一致。"""
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 7)
        )

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.generate_tab = ttk.Frame(self.notebook, padding=10)
        self.launch_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.generate_tab, text="生成设置")
        self.notebook.add(self.launch_tab, text="Steam 启动选项")

        self._create_generation_tab()
        self._create_launch_options_tab()

        ttk.Label(outer, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )

    def _create_generation_tab(self) -> None:
        """创建路径模式、最新状态、生成按钮和运行日志区域。"""
        tab = self.generate_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(4, weight=1)

        settings = ttk.LabelFrame(tab, text="输出设置", style="Section.TLabelframe", padding=8)
        settings.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        settings.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            settings,
            text="手动指定 ACF 保存位置（Steam.exe 可选）",
            variable=self.manual_mode_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.path_label = ttk.Label(settings, text="Steam 库位置：")
        self.path_label.grid(row=1, column=0, sticky="w", padx=(0, 7), pady=3)
        self.path_combo = ttk.Combobox(settings, textvariable=self.path_var)
        self.path_combo.grid(row=1, column=1, sticky="ew", pady=3)
        self.path_button = ttk.Button(settings, text="选择", command=self._choose_output_directory)
        self.path_button.grid(row=1, column=2, padx=(7, 0), pady=3)

        self.steam_label = ttk.Label(settings, text="Steam.exe：")
        self.steam_label.grid(row=2, column=0, sticky="w", padx=(0, 7), pady=3)
        ttk.Entry(settings, textvariable=self.steam_exe_var).grid(
            row=2, column=1, sticky="ew", pady=3
        )
        ttk.Button(settings, text="选择", command=self._choose_steam_executable).grid(
            row=2, column=2, padx=(7, 0), pady=3
        )

        ttk.Checkbutton(
            settings,
            text="启用 Steam 启动项生成",
            variable=self.enable_launch_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 2))

        self.mode_note = ttk.Label(settings, text="", wraplength=660)
        self.mode_note.grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        latest = ttk.LabelFrame(tab, text="最新状态", style="Section.TLabelframe", padding=8)
        latest.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        latest.columnconfigure(1, weight=1)
        ttk.Label(latest, text="Build ID：").grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(latest, textvariable=self.build_var).grid(row=0, column=1, sticky="w")
        ttk.Label(latest, text="Manifest ID：").grid(row=1, column=0, sticky="w", padx=(0, 7))
        ttk.Label(latest, textvariable=self.manifest_var).grid(row=1, column=1, sticky="w")
        ttk.Label(latest, text="获取时间：").grid(row=2, column=0, sticky="w", padx=(0, 7))
        ttk.Label(latest, textvariable=self.fetch_time_var).grid(row=2, column=1, sticky="w")

        self.generate_button = ttk.Button(
            tab,
            text="获取最新状态并生成 ACF",
            style="Primary.TButton",
            command=self._start_generation,
        )
        self.generate_button.grid(row=2, column=0, sticky="ew", pady=(0, 7))

        log_frame = ttk.LabelFrame(tab, text="运行日志", style="Section.TLabelframe", padding=6)
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=7,
            wrap="word",
            state="disabled",
            font=("Microsoft YaHei UI", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _create_launch_options_tab(self) -> None:
        """创建国服客户端路径、参数复选框和单行启动命令区域。"""
        tab = self.launch_tab
        tab.columnconfigure(1, weight=1)

        ttk.Label(tab, text="国服客户端 EXE：").grid(
            row=0, column=0, sticky="w", padx=(0, 7), pady=4
        )
        ttk.Entry(tab, textvariable=self.client_exe_var).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        ttk.Button(tab, text="选择", command=self._choose_client_executable).grid(
            row=0, column=2, padx=(7, 0), pady=4
        )

        options = ttk.LabelFrame(tab, text="参数组合", padding=8)
        options.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(7, 8))
        ttk.Checkbutton(options, text="使用 DX11（-dx11）", variable=self.dx11_var).pack(
            side="left", padx=(0, 18)
        )
        ttk.Checkbutton(
            options,
            text="跳过 LOGO（-SkipSplash）",
            variable=self.skip_splash_var,
        ).pack(side="left")

        ttk.Label(tab, text="最终启动选项：").grid(
            row=2, column=0, sticky="w", padx=(0, 7), pady=4
        )
        ttk.Entry(tab, textvariable=self.command_var, state="readonly").grid(
            row=2, column=1, sticky="ew", pady=4
        )
        ttk.Button(tab, text="复制", command=self._copy_launch_option).grid(
            row=2, column=2, padx=(7, 0), pady=4
        )

        note = (
            "将生成的一条命令复制到 Steam 游戏属性中的“启动选项”。"
            "两个参数可独立选择，也可以同时启用。"
        )
        ttk.Label(tab, text=note, wraplength=620).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

    def _store_current_path_for_active_mode(self) -> None:
        """切换模式前保存当前路径，避免自动模式与手动模式互相覆盖。"""
        value = self.path_var.get().strip()
        if self._last_manual_mode:
            self.manual_save_path = value
        else:
            self.auto_library_path = value

    def _on_path_mode_changed(self) -> None:
        """切换自动/手动路径策略，并分别保存两种模式各自的目录。"""
        if self.busy:
            # 运行中不允许改变生成策略，恢复原状态。
            self.manual_mode_var.set(self._last_manual_mode)
            return

        self._store_current_path_for_active_mode()
        current = bool(self.manual_mode_var.get())
        self._last_manual_mode = current
        self.path_var.set(
            self.manual_save_path if current else self.auto_library_path
        )
        self._refresh_path_mode_ui()
        if not current:
            self._auto_detect_steam_environment(log_detection=True)
        self._save_current_config(show_error=False)

    def _refresh_path_mode_ui(self) -> None:
        """只改变标签和说明文字，不改变 Lite.1 的紧凑布局结构。"""
        manual = bool(self.manual_mode_var.get())
        if manual:
            self.path_label.configure(text="ACF 保存目录：")
            self.steam_label.configure(text="Steam.exe（可选）：")
            self.mode_note.configure(
                text=(
                    "手动模式：保存目录可为任意位置，留空时使用程序目录；"
                    "Steam.exe 留空时只生成 ACF，不关闭或启动 Steam。"
                )
            )
        else:
            self.path_label.configure(text="Steam 库位置：")
            self.steam_label.configure(text="Steam.exe：")
            self.mode_note.configure(
                text=(
                    "自动检测模式：程序自动检测 Steam.exe 与 Steam 库；"
                    "生成前关闭 Steam，写入完成后重新启动。"
                )
            )

    def _auto_detect_steam_environment(self, log_detection: bool = False) -> None:
        """默认模式下按 v3.2 主线方式检测 Steam.exe 和所有库目录。"""
        detected = detect_steam_executable(self.steam_exe_var.get())
        if detected is None:
            self.path_combo["values"] = []
            if log_detection:
                self._append_log("未自动检测到 Steam.exe，请手动选择。")
            return

        self.steam_exe_var.set(str(detected))
        libraries = detect_steam_libraries(detected)
        library_values = [str(path) for path in libraries]
        self.path_combo["values"] = library_values

        current = self.path_var.get().strip()
        current_valid = False
        if current:
            try:
                current_library, _ = normalize_steam_library_and_steamapps(current)
                current_key = os.path.normcase(os.path.abspath(str(current_library)))
                current_valid = any(
                    os.path.normcase(os.path.abspath(value)) == current_key
                    for value in library_values
                )
            except ValueError:
                current_valid = False

        if not current_valid and libraries:
            self.path_var.set(str(libraries[0]))
            self.auto_library_path = str(libraries[0])

        if log_detection:
            self._append_log(f"已检测到 Steam：{detected}")
            if libraries:
                self._append_log(f"已检测到 {len(libraries)} 个 Steam 库。")
            else:
                self._append_log("未从 libraryfolders.vdf 检测到 Steam 库。")

    def _choose_output_directory(self) -> None:
        """根据当前路径模式选择普通输出目录或 Steam 库，并立即保存配置。"""
        initial = self.path_var.get().strip()
        if initial and not Path(initial).is_dir():
            initial = ""

        manual = bool(self.manual_mode_var.get())
        selected = filedialog.askdirectory(
            parent=self.root,
            title=(
                "选择 ACF 保存目录、Steam 库或 steamapps"
                if manual
                else "选择 Steam 库、steamapps 或 common 目录"
            ),
            initialdir=initial or (str(APP_DIRECTORY) if manual else None),
            mustexist=False,
        )
        if not selected:
            return

        try:
            if manual:
                self.path_var.set(selected)
                self.manual_save_path = selected
            else:
                library, _ = normalize_steam_library_and_steamapps(selected)
                self.path_var.set(str(library))
                self.auto_library_path = str(library)
        except ValueError as exc:
            messagebox.showerror("路径错误", str(exc), parent=self.root)
            return
        self._save_current_config(show_error=True)

    def _choose_steam_executable(self) -> None:
        """选择实际 steam.exe；自动模式下同时刷新可用 Steam 库列表。"""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=(
                "选择可选的 steam.exe"
                if self.manual_mode_var.get()
                else "选择 steam.exe"
            ),
            filetypes=[
                ("Steam 客户端", "steam.exe"),
                ("可执行文件", "*.exe"),
                ("所有文件", "*"),
            ],
        )
        if not selected:
            return
        path = Path(selected)
        if path.name.lower() != "steam.exe" or not path.is_file():
            messagebox.showerror("文件错误", "必须选择实际存在的 steam.exe。", parent=self.root)
            return

        resolved = path.resolve()
        self.steam_exe_var.set(str(resolved))
        if not self.manual_mode_var.get():
            libraries = detect_steam_libraries(resolved)
            self.path_combo["values"] = [str(item) for item in libraries]
            if libraries and not self.path_var.get().strip():
                self.path_var.set(str(libraries[0]))
                self.auto_library_path = str(libraries[0])
        self._save_current_config(show_error=True)

    def _choose_client_executable(self) -> None:
        """选择国服客户端，仅用于生成启动选项，不改变 ACF 输出逻辑。"""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=f"选择 {TARGET_CLIENT_EXE_NAME}",
            filetypes=[
                ("鸣潮客户端", TARGET_CLIENT_EXE_NAME),
                ("可执行文件", "*.exe"),
                ("所有文件", "*"),
            ],
        )
        if not selected:
            return
        try:
            path = validate_client_executable(selected)
        except ValueError as exc:
            messagebox.showerror("文件错误", str(exc), parent=self.root)
            return
        self.client_exe_var.set(str(path.resolve()))
        self._save_current_config(show_error=True)
        self._append_log(f"已选择国服客户端：{path.resolve()}")

    def _on_launch_options_enabled_changed(self) -> None:
        """根据第一页复选框启用或禁用第二个标签页，并持久化选择。"""
        self._refresh_launch_tab_state()
        self._save_current_config(show_error=False)

    def _on_launch_option_flags_changed(self) -> None:
        """DX11 或跳过 LOGO 状态改变时重新生成唯一一条启动命令。"""
        self._refresh_launch_option()
        self._save_current_config(show_error=False)

    def _refresh_launch_tab_state(self) -> None:
        """禁用启动项功能时锁定第二页，并把当前视图切回生成设置页。"""
        enabled = bool(self.enable_launch_var.get())
        state = "normal" if enabled else "disabled"
        self.notebook.tab(self.launch_tab, state=state)
        if not enabled and self.notebook.select() == str(self.launch_tab):
            self.notebook.select(self.generate_tab)

    def _refresh_launch_option(self) -> None:
        """客户端路径有效时生成命令；无效时清空结果，避免复制过期命令。"""
        try:
            path = validate_client_executable(self.client_exe_var.get())
            command = build_launch_option(
                path,
                use_dx11=bool(self.dx11_var.get()),
                skip_splash=bool(self.skip_splash_var.get()),
            )
        except ValueError:
            command = ""
        self.command_var.set(command)

    def _copy_launch_option(self) -> None:
        """把当前唯一启动命令写入系统剪贴板，并在状态栏反馈结果。"""
        command = self.command_var.get().strip()
        if not command:
            messagebox.showerror(
                "无法复制",
                f"请先选择实际存在的 {TARGET_CLIENT_EXE_NAME}。",
                parent=self.root,
            )
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.root.update_idletasks()
        self.status_var.set("已复制 Steam 启动选项")

    def _get_optional_steam_executable(self) -> Path | None:
        """手动路径模式专用：Steam.exe 留空时跳过进程管理。"""
        text = self.steam_exe_var.get().strip().strip('"')
        if not text:
            return None
        path = Path(text)
        if path.name.lower() != "steam.exe" or not path.is_file():
            raise ValueError("已填写的 Steam.exe 路径无效；可以清空该项以跳过 Steam 管理。")
        return path.resolve()

    def _get_required_steam_executable(self) -> Path:
        """自动检测模式专用：Steam.exe 必须有效，缺失时再次尝试自动检测。"""
        text = self.steam_exe_var.get().strip().strip('"')
        if text:
            path = Path(text)
            if path.name.lower() == "steam.exe" and path.is_file():
                return path.resolve()

        detected = detect_steam_executable(text)
        if detected is None:
            raise ValueError("未检测到有效的 Steam.exe，请手动选择 Steam.exe。")
        self.steam_exe_var.set(str(detected))
        return detected.resolve()

    def _validate_generation_inputs(self) -> tuple[Path, Path | None, Path | None]:
        """
        把 GUI 输入归一化为 ACF 输出目录、可选 steamapps 和可选 Steam.exe。

        自动模式要求 Steam.exe 与 Steam 库有效；手动模式允许任意目录和空 Steam.exe。
        返回值在启动后台线程前已经验证，工作线程不再读取 Tk 变量。
        """
        if self.manual_mode_var.get():
            output_dir, steamapps = resolve_acf_output_directory(self.path_var.get())
            steam_exe = self._get_optional_steam_executable()
            return output_dir, steamapps, steam_exe

        steam_exe = self._get_required_steam_executable()
        library, steamapps = normalize_steam_library_and_steamapps(self.path_var.get())
        self.auto_library_path = str(library)
        return steamapps, steamapps, steam_exe

    def _save_current_config(self, show_error: bool = False) -> None:
        """把当前界面状态写入英文键 INI；需要时向用户显示写入失败。"""
        self._store_current_path_for_active_mode()
        self.config.set("ManualPathMode", "1" if self.manual_mode_var.get() else "0")
        self.config.set("SteamLibraryPath", self.auto_library_path)
        self.config.set("CustomAcfOutputDirectory", self.manual_save_path)
        self.config.set("SteamExecutablePath", self.steam_exe_var.get().strip())
        self.config.set("EnableLaunchOptions", "1" if self.enable_launch_var.get() else "0")
        self.config.set("ClientExecutablePath", self.client_exe_var.get().strip())
        self.config.set("UseDx11", "1" if self.dx11_var.get() else "0")
        self.config.set("SkipSplash", "1" if self.skip_splash_var.get() else "0")
        try:
            self.config.save()
        except OSError as exc:
            if show_error:
                messagebox.showwarning(
                    "配置保存失败",
                    f"无法写入便携配置：\n{CONFIG_PATH}\n\n{exc}",
                    parent=self.root,
                )

    def _start_generation(self, skip_confirmation: bool = False) -> None:
        """
        校验输入、处理覆盖确认与 UAC，然后启动单一后台生成任务。

        提权续跑时 skip_confirmation=True，避免同一次操作再次弹出覆盖确认；目标路径、
        Steam 路径和模式均从刚刚保存的便携配置恢复。
        """
        if self.busy:
            return

        try:
            output_dir, steamapps, steam_exe = self._validate_generation_inputs()
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self.root)
            return

        target = output_dir / TARGET_ACF_FILENAME
        if not skip_confirmation and target.exists():
            confirmed = messagebox.askyesno(
                "确认覆盖",
                "检测到目标 ACF 已存在：\n\n"
                f"{target}\n\n"
                "继续后会获取最新 Build/Manifest，并使用固定模板静默覆盖。\n"
                "旧 ACF 不会被读取或沿用。是否继续？",
                parent=self.root,
                default=messagebox.NO,
            )
            if not confirmed:
                return

        self._save_current_config(show_error=True)

        if steam_exe is not None and steam_management_requires_elevation(steam_exe):
            confirmed = messagebox.askyesno(
                "需要管理员权限",
                "检测到目标 Steam 正在以管理员权限运行。\n\n"
                "程序需要通过 Windows UAC 以管理员权限重新启动自身，"
                "才能结束并恢复启动该 Steam。是否继续？",
                parent=self.root,
                default=messagebox.YES,
            )
            if not confirmed:
                return
            try:
                restart_self_elevated()
            except RuntimeError as exc:
                messagebox.showerror("提权失败", str(exc), parent=self.root)
                return
            self.closing = True
            self.root.destroy()
            return

        self.busy = True
        self.generate_button.configure(state="disabled", text="正在生成...")
        self.status_var.set("正在获取最新状态")
        self._append_log(f"目标 ACF：{target}")
        if steam_exe is None:
            self._append_log("未设置 Steam.exe：将跳过关闭和重新启动 Steam。")
        else:
            self._append_log(f"将管理 Steam 主程序：{steam_exe}")

        def worker() -> None:
            """在线程中完成取数、模板验证、Steam 管理、原子写入和恢复启动。"""
            try:
                build_id, manifest_id = fetch_latest_state(log_callback=self._queue_worker_log)
                self._queue_worker_log(f"最新 Build ID：{build_id}")
                self._queue_worker_log(f"最新 Manifest ID：{manifest_id}")

                content = build_acf_content(steam_exe, build_id, manifest_id)
                validate_generated_acf(content, steam_exe, build_id, manifest_id)

                placeholder: Path | None = None
                placeholder_created = False
                if steamapps is not None:
                    placeholder, placeholder_created = ensure_placeholder_game_executable(steamapps)
                else:
                    self._queue_worker_log(
                        "保存位置不是可识别的 steamapps，已跳过创建 Steam 占位 EXE。"
                    )

                steam_was_running = False
                if steam_exe is not None:
                    steam_was_running = terminate_steam_process(
                        steam_exe, log_callback=self._queue_worker_log
                    )

                try:
                    atomic_replace_text(target, content)
                except Exception as operation_error:
                    recovery_error: Exception | None = None
                    if steam_exe is not None and steam_was_running:
                        try:
                            self._queue_worker_log("ACF 写入失败，正在恢复启动 Steam。")
                            start_steam_process(steam_exe, log_callback=self._queue_worker_log)
                        except Exception as exc:
                            recovery_error = exc
                    if recovery_error is not None:
                        raise RuntimeError(
                            f"{operation_error}\n此外，恢复启动 Steam 失败：{recovery_error}"
                        ) from operation_error
                    raise

                self._queue_worker_log("ACF 已完成原子写入和回读验证。")
                steam_start_error = ""
                if steam_exe is not None:
                    try:
                        start_steam_process(steam_exe, log_callback=self._queue_worker_log)
                    except Exception as exc:
                        steam_start_error = str(exc)
                        self._queue_worker_log(
                            f"ACF 已生成，但 Steam 自动启动失败：{steam_start_error}"
                        )

                fetched_at = format_timestamp()
                self.events.put(
                    (
                        "success",
                        (
                            target,
                            placeholder,
                            placeholder_created,
                            build_id,
                            manifest_id,
                            fetched_at,
                            steam_exe is not None,
                            steam_start_error,
                        ),
                    )
                )
            except ElevationRequiredError as exc:
                self.events.put(("need_elevation", exc))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=worker, name="ACF生成线程", daemon=True).start()

    def _resume_generation_after_elevation(self) -> None:
        """提升后的实例确认权限有效后，自动继续上一次已经确认的生成操作。"""
        if self.closing or self.busy:
            return
        if not is_current_process_elevated():
            self._append_log("未获得管理员权限，已取消自动继续。")
            return
        self._append_log("已通过 Windows UAC 获得管理员权限，继续执行已确认操作。")
        self._start_generation(skip_confirmation=True)

    def _request_elevation_during_operation(self, error: Exception) -> None:
        """运行中遇到访问拒绝时回到主线程询问 UAC，并安全关闭旧实例。"""
        self.busy = False
        self.generate_button.configure(state="normal", text="获取最新状态并生成 ACF")
        self.status_var.set("需要管理员权限")
        self._append_log(f"需要管理员权限：{error}")

        confirmed = messagebox.askyesno(
            "需要管理员权限",
            "Steam 的权限状态在执行过程中发生变化。\n\n"
            "是否通过 Windows UAC 以管理员权限重新启动程序并自动继续？",
            parent=self.root,
            default=messagebox.YES,
        )
        if not confirmed:
            return
        self._save_current_config(show_error=True)
        try:
            restart_self_elevated()
        except RuntimeError as exc:
            messagebox.showerror("提权失败", str(exc), parent=self.root)
            return
        self.closing = True
        self.root.destroy()

    def _queue_worker_log(self, message: str) -> None:
        """工作线程只入队日志，不直接访问 Tk 文本框。"""
        self.events.put(("log", message))

    def _process_worker_events(self) -> None:
        """在 Tk 主线程中批量处理日志、成功、失败和提权事件。"""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if self.closing:
                    continue
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "success":
                    self._handle_generation_success(*payload)
                elif kind == "need_elevation":
                    self._request_elevation_during_operation(payload)
                elif kind == "error":
                    self._handle_generation_failure(payload)
        except queue.Empty:
            pass

        if not self.closing:
            self.root.after(80, self._process_worker_events)

    def _handle_generation_success(
        self,
        target: Path,
        placeholder: Path | None,
        placeholder_created: bool,
        build_id: str,
        manifest_id: str,
        fetched_at: str,
        managed_steam: bool,
        steam_start_error: str,
    ) -> None:
        """更新界面与配置，并根据 Steam 是否被管理显示准确的完成提示。"""
        self.busy = False
        self.generate_button.configure(state="normal", text="获取最新状态并生成 ACF")

        self.build_var.set(build_id)
        self.manifest_var.set(manifest_id)
        self.fetch_time_var.set(fetched_at)

        self.config.set("LastBuildId", build_id)
        self.config.set("LastManifestId", manifest_id)
        self.config.set("LastFetchTime", fetched_at)
        self._save_current_config(show_error=False)

        self._append_log(f"ACF 已生成：{target}")
        if placeholder is not None:
            if placeholder_created:
                self._append_log(
                    f"已创建零字节 Steam 占位 EXE：{placeholder}。"
                    "该文件只用于满足 Steam 启动项，不是真实游戏程序。"
                )
            else:
                self._append_log(f"Steam 占位 EXE 已存在，保持原样且未覆盖：{placeholder}")

        if not managed_steam:
            self.status_var.set("ACF 生成完成，未管理 Steam")
            messagebox.showinfo(
                "生成完成",
                "已经获取最新状态并生成鸣潮 ACF。\n\n"
                f"Build ID：{build_id}\n"
                f"Manifest ID：{manifest_id}\n\n"
                f"文件：{target}\n\n"
                "未设置 Steam.exe，因此没有关闭或启动 Steam。",
                parent=self.root,
            )
        elif steam_start_error:
            self.status_var.set("ACF 已生成，Steam 自动启动失败")
            messagebox.showwarning(
                "ACF 已生成",
                "已经获取最新状态并生成鸣潮 ACF。\n\n"
                f"Build ID：{build_id}\n"
                f"Manifest ID：{manifest_id}\n\n"
                f"文件：{target}\n\n"
                f"Steam 自动启动失败：\n{steam_start_error}",
                parent=self.root,
            )
        else:
            self.status_var.set("ACF 生成完成，Steam 已启动")
            messagebox.showinfo(
                "生成完成",
                "已经获取最新状态并生成鸣潮 ACF。\n\n"
                f"Build ID：{build_id}\n"
                f"Manifest ID：{manifest_id}\n\n"
                f"文件：{target}\n\nSteam 已自动启动。",
                parent=self.root,
            )

    def _handle_generation_failure(self, error: Exception) -> None:
        """恢复按钮状态，把后台异常同时写入日志和错误对话框。"""
        self.busy = False
        self.generate_button.configure(state="normal", text="获取最新状态并生成 ACF")
        self.status_var.set("执行失败")
        self._append_log(f"执行失败：{error}")
        messagebox.showerror("执行失败", str(error), parent=self.root)

    def _append_log(self, message: str) -> None:
        """追加带本地时间的简体中文日志，并自动滚动到最新一行。"""
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        """运行中拒绝关闭窗口；空闲时先保存配置，再销毁 Tk 根窗口。"""
        if self.busy:
            messagebox.showinfo(
                "操作正在进行",
                "当前正在生成 ACF，请等待操作完成后再关闭窗口。",
                parent=self.root,
            )
            return
        self.closing = True
        self._save_current_config(show_error=False)
        self.root.destroy()


# ============================================================================
# 内置自检
# ============================================================================


def _run_self_test() -> None:
    """执行不依赖网络和真实 Steam 的解析、路径、模板、配置与静态约束测试。"""
    sample_string = {
        "status": "success",
        "data": {
            TARGET_APP_ID: {
                "depots": {
                    "branches": {"public": {"buildid": "24234978"}},
                    TARGET_DEPOT_ID: {
                        "manifests": {"public": "7315697900308454873"}
                    },
                }
            }
        },
    }
    assert parse_latest_state(sample_string) == (
        "24234978",
        "7315697900308454873",
    )

    sample_gid = {
        "status": "success",
        "data": {
            TARGET_APP_ID: {
                "depots": {
                    "branches": {"public": {"buildid": 24234978}},
                    TARGET_DEPOT_ID: {
                        "manifests": {
                            "public": {"gid": "7315697900308454873"}
                        }
                    },
                }
            }
        },
    }
    assert parse_latest_state(sample_gid) == (
        "24234978",
        "7315697900308454873",
    )

    command_path = Path("H:/Game/Client-Win64-Shipping.exe")
    assert build_launch_option(command_path, False, False).endswith(
        'Client-Win64-Shipping.exe" %command%'
    )
    assert "-dx11" in build_launch_option(command_path, True, False)
    assert "-SkipSplash" in build_launch_option(command_path, False, True)
    assert "-dx11 -SkipSplash" in build_launch_option(command_path, True, True)

    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        arbitrary = base / "output"
        output, steamapps = resolve_acf_output_directory(str(arbitrary))
        assert output == arbitrary.resolve(strict=False)
        assert steamapps is None

        library = base / "SteamLibrary"
        (library / "steamapps").mkdir(parents=True)
        output2, steamapps2 = resolve_acf_output_directory(str(library))
        assert output2 == (library / "steamapps").resolve()
        assert steamapps2 == output2

        normalized_library, normalized_steamapps = normalize_steam_library_and_steamapps(
            str(library)
        )
        assert normalized_library == library.resolve()
        assert normalized_steamapps == (library / "steamapps").resolve()

        fake_steam = library / "steam.exe"
        fake_steam.write_bytes(b"")

        extra_library = base / "ExtraLibrary"
        (extra_library / "steamapps").mkdir(parents=True)
        vdf_text = (
            '"libraryfolders"\n{\n\t"1"\n\t{\n'
            f'\t\t"path"\t\t"{str(extra_library).replace(chr(92), chr(92) * 2)}"\n'
            "\t}\n}\n"
        )
        (library / "steamapps" / "libraryfolders.vdf").write_text(
            vdf_text,
            encoding="utf-8",
        )
        detected_libraries = detect_steam_libraries(fake_steam)
        assert library.resolve() in detected_libraries
        assert extra_library.resolve() in detected_libraries

        content_with_steam = build_acf_content(
            fake_steam,
            "24234978",
            "7315697900308454873",
        )
        validate_generated_acf(
            content_with_steam,
            fake_steam,
            "24234978",
            "7315697900308454873",
        )
        assert '"LastOwner"\t\t"0"' in content_with_steam
        assert "steam.exe" in content_with_steam.lower()

        content_without_steam = build_acf_content(
            None,
            "24234978",
            "7315697900308454873",
        )
        validate_generated_acf(
            content_without_steam,
            None,
            "24234978",
            "7315697900308454873",
        )
        assert '"LauncherPath"\t\t""' in content_without_steam
        assert '"LastOwner"\t\t"0"' in content_without_steam

        target = arbitrary / TARGET_ACF_FILENAME
        atomic_replace_text(target, content_without_steam)
        assert target.read_bytes() == content_without_steam.encode("utf-8")

        # 验证新 INI 始终使用英文节名和英文键，并在每次保存后保留中文说明。
        config_path = base / CONFIG_FILENAME
        config = PortableConfig(config_path, legacy_paths=())
        config.set("ManualPathMode", "1")
        config.set("CustomAcfOutputDirectory", str(arbitrary))
        config.set("UseDx11", "1")
        config.save()
        config_text = config_path.read_text(encoding="utf-8")
        assert "[Settings]" in config_text
        assert "ManualPathMode=1" in config_text
        assert "CustomAcfOutputDirectory=" in config_text
        assert "; 路径模式开关。" in config_text
        assert "; 国服 Client-Win64-Shipping.exe 的完整路径。" in config_text
        assert "[设置]" not in config_text
        assert "手动路径模式=" not in config_text

        reloaded = PortableConfig(config_path, legacy_paths=())
        assert reloaded.get_bool("ManualPathMode")
        assert reloaded.get("CustomAcfOutputDirectory") == str(arbitrary)
        assert reloaded.get_bool("UseDx11")
        reloaded.save()
        assert "; 路径模式开关。" in config_path.read_text(encoding="utf-8")
        try:
            reloaded.set("UnknownKey", "value")
        except KeyError:
            pass
        else:
            raise AssertionError("未知配置键必须被拒绝。")

        # 验证 Lite.2 中文键可以迁移到新英文键，但旧文件不会被删除。
        legacy_path = base / "legacy.ini"
        legacy_path.write_text(
            "[设置]\n"
            "手动路径模式=1\n"
            f"ACF保存目录={arbitrary}\n"
            "启动项DX11=1\n",
            encoding="utf-8",
        )
        migrated_path = base / "migrated.ini"
        migrated = PortableConfig(migrated_path, legacy_paths=(legacy_path,))
        assert migrated.migrated_from == legacy_path
        assert migrated.get_bool("ManualPathMode")
        assert migrated.get("CustomAcfOutputDirectory") == str(arbitrary)
        assert migrated.get_bool("UseDx11")
        migrated.save()
        assert legacy_path.is_file()
        assert "ManualPathMode=1" in migrated_path.read_text(encoding="utf-8")

    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = tuple(
        chr(codepoint)
        for codepoint in (0x2705, 0x274C, 0x1F310, 0x1F680, 0x26A0, 0xFE0F)
    )
    assert not any(symbol in source for symbol in forbidden)

    # Python NAME token 中不允许出现中文，确保所有代码标识符已经统一为英文。
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            assert re.search(r"[\u3400-\u9fff]", token.string) is None, token.string

    removed_account_token = "Steam" + "ID64"
    assert removed_account_token not in source
    removed_login_file = "loginusers" + ".vdf"
    assert removed_login_file not in source
    assert '\\t"LastOwner"\\t\\t"0"' in source
    assert "ttk.Notebook" in source
    assert "width = min(int(740 * dpi_scale)" in source
    assert "手动指定 ACF 保存位置（Steam.exe 可选）" in source
    assert 'get_bool("ManualPathMode", False)' in source
    assert "def detect_steam_executable(" in source
    assert "def detect_steam_libraries(" in source
    assert "def _get_required_steam_executable(self) -> Path:" in source
    assert "def _get_optional_steam_executable(self) -> Path | None:" in source
    assert 'CONFIG_FILENAME = "WWSteamFakeInstall.ini"' in source
    assert 'CONFIG_SECTION = "Settings"' in source
    removed_path_var = "save_" + "dir_var"
    assert removed_path_var not in source
    assert "启用 Steam 启动项生成" in source
    removed_fourth_key = "DX11" + "并跳过LOGO"
    assert removed_fourth_key not in source
    assert "def build_launch_option(exe_path: Path, use_dx11: bool, skip_splash: bool)" in source

    print("自检通过")


def main() -> None:
    """程序入口：自检模式直接退出，正常模式启用高 DPI 并启动 Tk 主循环。"""
    if "--self-test" in sys.argv:
        _run_self_test()
        return

    enable_windows_high_dpi_awareness()
    root = tk.Tk()
    root._app_dpi_scale = apply_tk_dpi_scaling(root)  # type: ignore[attr-defined]
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
