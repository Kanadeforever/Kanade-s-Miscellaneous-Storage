cmake_minimum_required(VERSION 3.20)
project(IconMasterLite VERSION 1.2.1 LANGUAGES CXX RC)

add_executable(IconMasterLite WIN32
    src/main.cpp
    res/app.rc
)

target_compile_features(IconMasterLite PRIVATE cxx_std_17)
target_compile_definitions(IconMasterLite PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(IconMasterLite PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/res")

# Keep a copy beside the generated project/build directory as well. This makes
# the RC icon path robust regardless of whether rc.exe resolves file names
# relative to the .rc source directory or its current working directory.
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/res/IconMasterLite.ico"
    "${CMAKE_CURRENT_BINARY_DIR}/IconMasterLite.ico"
    COPYONLY
)

if(MSVC)
    set_property(TARGET IconMasterLite PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    target_compile_options(IconMasterLite PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:/utf-8>
        $<$<COMPILE_LANGUAGE:CXX>:/W4>
        $<$<COMPILE_LANGUAGE:CXX>:/permissive->
    )
else()
    target_compile_options(IconMasterLite PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-Wall>
        $<$<COMPILE_LANGUAGE:CXX>:-Wextra>
        $<$<COMPILE_LANGUAGE:CXX>:-Wpedantic>
    )
endif()

target_link_libraries(IconMasterLite PRIVATE windowscodecs ole32 shell32 comctl32 user32 gdi32)

set_target_properties(IconMasterLite PROPERTIES
    OUTPUT_NAME "IconMasterLite"
)
