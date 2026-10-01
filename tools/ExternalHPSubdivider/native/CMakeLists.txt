cmake_minimum_required(VERSION 3.20)

project(bg_obj_subdivider VERSION 0.4.0 LANGUAGES C CXX)

add_executable(bg_obj_subdivider
    src/main.cpp
    third_party/ufbx/ufbx.c
    third_party/ufbx_write/ufbx_write.c
)

target_include_directories(bg_obj_subdivider PRIVATE third_party/ufbx third_party/ufbx_write)
target_compile_features(bg_obj_subdivider PRIVATE cxx_std_17)
target_compile_definitions(bg_obj_subdivider PRIVATE
    _CRT_SECURE_NO_WARNINGS
    NOMINMAX
)

if(MSVC)
    set_property(TARGET bg_obj_subdivider PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    target_compile_options(bg_obj_subdivider PRIVATE
        $<$<COMPILE_LANGUAGE:C,CXX>:/utf-8>
        $<$<COMPILE_LANGUAGE:C,CXX>:/W4>
    )
endif()

install(TARGETS bg_obj_subdivider RUNTIME DESTINATION .)
