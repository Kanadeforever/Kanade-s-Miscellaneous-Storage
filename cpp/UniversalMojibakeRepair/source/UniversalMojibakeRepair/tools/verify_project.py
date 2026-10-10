"""检查发布包目录、文件编码、换行和批处理安全行尾。

这个脚本只读文件，不会写回或更改用户源码。
可以在 Windows 上运行 python tools\\verify_project.py，
也可以在其他安装 Python 3 的系统上运行以复核打包规范。
"""

from pathlib import Path
import sys


def main() -> int:
    # 从验证脚本的所在位置向上查找包根目录，而不是依赖用户打开 CMD 的当前目录。
    # 这能保证用户从桌面双击工具、或者从任意目录调用时都检查同一份工程。
    root = Path(__file__).resolve().parents[3]
    expected_dirs = ("docs", "source", "release", ".build")
    errors: list[str] = []

    # 根目录只允许四个交付目录；Git 的 .git 元数据属于本地开发过程，不进入压缩包。
    for name in expected_dirs:
        if not (root / name).is_dir():
            errors.append(f"缺少必要目录：{name}")
    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.name not in expected_dirs:
            errors.append(f"根目录出现额外项目：{child.name}")

    required_files = (
        "docs/完整接档说明.md", "docs/使用说明.md", "docs/测试说明.md",
        "docs/原程序逆向分析.md", "docs/构建说明.md",
        "source/UniversalMojibakeRepair/build.bat",
        "source/UniversalMojibakeRepair/readme.md",
        "source/UniversalMojibakeRepair/CMakeLists.txt",
        "source/UniversalMojibakeRepair/config/AppIcon.ico",
        "source/UniversalMojibakeRepair/src/TransformEngine.cpp",
        "source/UniversalMojibakeRepair/src/TransformEngine.h",
        "source/UniversalMojibakeRepair/src/HtmlEntities.inc",
        "source/UniversalMojibakeRepair/src/EncodingEngine.cpp",
        "source/UniversalMojibakeRepair/src/EncodingEngine.h",
        "source/UniversalMojibakeRepair/src/Main.cpp",
        "source/UniversalMojibakeRepair/src/Resource.rc",
        "source/UniversalMojibakeRepair/src/Resource.h",
        "source/UniversalMojibakeRepair/tools/test_transform.cpp",
    )
    for name in required_files:
        if not (root / name).is_file():
            errors.append(f"缺少工程文件：{name}")

    text_extensions = {".cpp", ".h", ".inc", ".rc", ".md", ".py", ".bat", ".txt"}
    for file in root.rglob("*"):
        # 不处理 .git 中的二进制对象和临时编译文件，也不把 ICO 当成可解码的文本。
        if not file.is_file() or ".git" in file.parts or ".build" in file.parts:
            continue
        if file.suffix.lower() not in text_extensions and file.name != "CMakeLists.txt":
            continue
        content = file.read_bytes()
        if content.startswith(b"\xef\xbb\xbf"):
            errors.append(f"UTF-8 文件不允许 BOM：{file.relative_to(root)}")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"文件不是 UTF-8：{file.relative_to(root)}")
            continue
        # 任何单独的 LF 或 CR 都说明该文件不是统一的 Windows CRLF 换行。
        if b"\n" in content.replace(b"\r\n", b"") or b"\r" in content.replace(b"\r\n", b""):
            errors.append(f"发现非 CRLF 换行：{file.relative_to(root)}")
        if file.name.lower() == "build.bat":
            for index, line in enumerate(decoded.splitlines(), start=1):
                clean = line.lstrip().lower()
                if clean.startswith("rem ") or clean.startswith("echo "):
                    if not line.endswith("  "):
                        errors.append(f"BAT 文本行尾缺少两个半角空格：{index}")

    # Windows 目录名称允许包含中文，而 NMake 与链接器的某些路径处理存在兼容性风险。
    # 需要先得到真实工程根目录，再将其映射为临时盘符；CMake 的源码、构建和输出路径
    # 都通过映射盘符访问，从而让 PDB、响应文件和中间文件路径只包含 ASCII 字符。
    # 退出时撤销自己创建的盘符，不能误删用户已经存在的其他映射。
    build_script = (root / "source/UniversalMojibakeRepair/build.bat").read_text(encoding="utf-8")
    if 'cd /d "%~dp0"' not in build_script or r'pushd "..\.."' not in build_script:
        errors.append("BAT 必须由脚本所在位置获取工程目录与根目录")
    if 'set "ROOT=%CD%"' not in build_script:
        errors.append("BAT 必须获取不带末尾反斜杠的真实根目录")
    for fragment in (
        'subst %%D: "%ROOT%"',
        'if not defined SUBST_DRIVE goto mount_failed',
        r'set "PROJECT=%SUBST_DRIVE%\source\UniversalMojibakeRepair"',
        r'set "BUILD_DIR=%SUBST_DRIVE%\.build\msvc-x64"',
        r'set "OUTPUT_DIR=%SUBST_DRIVE%\release"',
        'subst %SUBST_DRIVE% /D',
        'cd /d "%PROJECT%"',
        'cd /d "%ROOT%"',
    ):
        if fragment not in build_script:
            errors.append(f"BAT 缺少中文路径隔离步骤：{fragment}")
    if 'set "PROJECT=%~dp0"' in build_script or 'set "PROJECT=%CD%"' in build_script:
        errors.append("BAT 不允许将真实目录直接传入 CMake -S")
    if build_script.count('call :cleanup_mount') < 2:
        errors.append("BAT 成功和失败路径都必须撤销临时盘符")
    if build_script.find('subst %%D: "%ROOT%"') > build_script.find('"%CMAKE_EXE%" -S'):
        errors.append("BAT 必须先映射临时盘符，再开始 CMake 配置")

    # CMake 的 MSVC 编译测试不仅调用 cl.exe，还会使用 SDK 中的 rc.exe 和 mt.exe。
    # 若没有在调用 CMake 之前进行这两项检查，安装不完整时就会得到难以理解的链接器报错。
    for tool in ("cl.exe", "nmake.exe", "rc.exe", "mt.exe"):
        if f"where {tool} >nul 2>nul" not in build_script:
            errors.append(f"BAT 缺少构建前工具检查：{tool}")
    cmake_start = build_script.find('"%CMAKE_EXE%" -S')
    sdk_check = build_script.find(":tools_ready")
    if cmake_start < 0 or sdk_check < 0 or cmake_start < sdk_check:
        errors.append("BAT 必须在调用 CMake 之前完成 Windows SDK 检查")
    if "CMAKE_MT-NOTFOUND" not in build_script or 'rmdir /s /q "%BUILD_DIR%"' not in build_script:
        errors.append("BAT 必须清理可能包含 CMAKE_MT-NOTFOUND 的失败构建缓存")

    # 统一输出全部错误，便于一次解决，而不是每遇到一个错误就中断。
    if errors:
        for error in errors:
            print("[错误]", error)
        return 1
    print("[通过] 四目录、必要文件、UTF-8 无 BOM、CRLF 与 BAT 行尾全部符合规范。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
