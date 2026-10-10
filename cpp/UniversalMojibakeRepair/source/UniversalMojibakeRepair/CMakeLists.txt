# 这是原生 Win32 工程的统一构建定义。
# 所有路径都相对于当前工程，不依赖编译器的安装绝对路径。
# Windows 正式构建由同目录 build.bat 驱动；Linux 可以用 g++ 单独回归测试格式算法。
cmake_minimum_required(VERSION 3.20)
project(UniversalMojibakeRepair VERSION 0.2.0 LANGUAGES CXX RC)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 使用 WIN32 子系统：双击应用程序不弹额外黑色控制台。
add_executable(UniversalMojibakeRepair WIN32
    src/Main.cpp
    src/EncodingEngine.cpp
    src/EncodingEngine.h
    src/TransformEngine.cpp
    src/TransformEngine.h
    src/HtmlEntities.inc
    src/Resource.rc
    src/Resource.h
)
target_compile_definitions(UniversalMojibakeRepair PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
    target_compile_options(UniversalMojibakeRepair PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(UniversalMojibakeRepair PRIVATE -Wall -Wextra -Wpedantic)
endif()
# Comdlg32：Windows 文件选择对话框；Shell32：TXT 拖放及命令行解析。
target_link_libraries(UniversalMojibakeRepair PRIVATE comdlg32 shell32)

# 回归测试与 GUI 分离运行，不需要创建窗口、访问文件或使用 Windows 代码页。
# 默认随程序一起构建，BAT 会在复制 EXE 前强制运行测试。
enable_testing()
add_executable(TransformRegression tools/test_transform.cpp src/TransformEngine.cpp)
if(MSVC)
    target_compile_options(TransformRegression PRIVATE /W4 /utf-8)
else()
    target_compile_options(TransformRegression PRIVATE -Wall -Wextra -Wpedantic)
endif()
add_test(NAME TextFormatRegression COMMAND TransformRegression)
