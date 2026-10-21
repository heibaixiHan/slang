cmake_minimum_required(VERSION 3.10)
project(slangcore CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(slangcore SHARED slang_core.cpp)
target_compile_options(slangcore PRIVATE -O2 -Wall -Wextra)
set_target_properties(slangcore PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/build"
)
