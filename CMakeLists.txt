cmake_minimum_required(VERSION 3.20)
project(bg_math_core LANGUAGES CXX)

set(MAYA_VERSION "2024" CACHE STRING "Target Maya version")
set(DEVKITS_ROOT "C:/Maya_Devkits" CACHE PATH "Maya devkits root")
set(PYBIND11_SOURCE_DIR "" CACHE PATH "pybind11 source directory")

if(PYBIND11_SOURCE_DIR STREQUAL "")
    if(MAYA_VERSION STREQUAL "2027")
        set(_PYBIND11_CANDIDATES
            "${CMAKE_CURRENT_SOURCE_DIR}/pybind11-master"
            "${CMAKE_CURRENT_SOURCE_DIR}/../pybind11-master"
            "${CMAKE_CURRENT_SOURCE_DIR}/../pybind11-2.12.1"
        )
    else()
        set(_PYBIND11_CANDIDATES
            "${CMAKE_CURRENT_SOURCE_DIR}/pybind11-2.12.1"
            "${CMAKE_CURRENT_SOURCE_DIR}/../pybind11-2.12.1"
            "${CMAKE_CURRENT_SOURCE_DIR}/pybind11-master"
            "${CMAKE_CURRENT_SOURCE_DIR}/../pybind11-master"
        )
    endif()
    foreach(_candidate ${_PYBIND11_CANDIDATES})
        if(EXISTS "${_candidate}/CMakeLists.txt")
            set(PYBIND11_SOURCE_DIR "${_candidate}")
            break()
        endif()
    endforeach()
endif()

if(NOT EXISTS "${PYBIND11_SOURCE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "[Bake Groups] pybind11 source directory not found. Pass -DPYBIND11_SOURCE_DIR=...")
endif()

if(MAYA_VERSION STREQUAL "2022")
    set(PY_INCLUDE "${DEVKITS_ROOT}/2022/include/Python37/Python")
    set(PY_LIB "${DEVKITS_ROOT}/2022/lib/python37.lib")
elseif(MAYA_VERSION STREQUAL "2023")
    set(PY_INCLUDE "${DEVKITS_ROOT}/2023/include/Python39/Python")
    set(PY_LIB "${DEVKITS_ROOT}/2023/lib/python39.lib")
elseif(MAYA_VERSION STREQUAL "2024")
    if(EXISTS "${DEVKITS_ROOT}/2024/include/Python310/Python/Python.h")
        set(PY_INCLUDE "${DEVKITS_ROOT}/2024/include/Python310/Python")
        set(PY_LIB "${DEVKITS_ROOT}/2024/lib/python310.lib")
    else()
        set(PY_INCLUDE "C:/Program Files/Autodesk/Maya2024/include/Python310/Python")
        set(PY_LIB "C:/Program Files/Autodesk/Maya2024/lib/python310.lib")
    endif()
elseif(MAYA_VERSION STREQUAL "2025")
    set(PY_INCLUDE "${DEVKITS_ROOT}/2025/include/Python311/Python")
    set(PY_LIB "${DEVKITS_ROOT}/2025/lib/python311.lib")
elseif(MAYA_VERSION STREQUAL "2026")
    set(PY_INCLUDE "${DEVKITS_ROOT}/2026/include/Python311/Python")
    set(PY_LIB "${DEVKITS_ROOT}/2026/lib/python311.lib")
elseif(MAYA_VERSION STREQUAL "2027")
    set(PY_INCLUDE "C:/Program Files/Autodesk/Maya2027/include/Python313/Python")
    set(PY_LIB "C:/Program Files/Autodesk/Maya2027/lib/python313.lib")
else()
    message(FATAL_ERROR "[Bake Groups] Unsupported Maya version: ${MAYA_VERSION}")
endif()

if(NOT EXISTS "${PY_INCLUDE}/Python.h")
    message(FATAL_ERROR "[Bake Groups] Python.h not found: ${PY_INCLUDE}")
endif()

if(NOT EXISTS "${PY_LIB}")
    message(FATAL_ERROR "[Bake Groups] Python library not found: ${PY_LIB}")
endif()

set(PYBIND11_NOPYTHON ON CACHE BOOL "" FORCE)
add_subdirectory("${PYBIND11_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/pybind11")

add_library(bg_math_core MODULE "Bake_Groups/bg_math_core.cpp")
target_include_directories(bg_math_core PRIVATE ${pybind11_INCLUDE_DIRS} "${PY_INCLUDE}")
target_link_libraries(bg_math_core PRIVATE "${PY_LIB}")

set_property(TARGET bg_math_core PROPERTY CXX_STANDARD 14)
set_property(TARGET bg_math_core PROPERTY CXX_STANDARD_REQUIRED ON)
set_property(TARGET bg_math_core PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(OUTPUT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/Bake_Groups/bin/${MAYA_VERSION}")
set_target_properties(bg_math_core PROPERTIES
    PREFIX ""
    SUFFIX ".pyd"
    LIBRARY_OUTPUT_DIRECTORY "${OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_DEBUG "${OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${OUTPUT_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${OUTPUT_DIR}"
)
