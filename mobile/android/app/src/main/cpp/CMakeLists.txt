cmake_minimum_required(VERSION 3.22.1)

project("whisperflow_native")

# Set C++ Standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable Vulkan Acceleration if supported by NDK
if(GGML_VULKAN)
    add_compile_definitions(GGML_USE_VULKAN)
endif()

# Native JNI wrapper library
add_library(
    whisperflow_native
    SHARED
    native-lib.cpp
)

find_library(
    log-lib
    log
)

target_link_libraries(
    whisperflow_native
    ${log-lib}
)
