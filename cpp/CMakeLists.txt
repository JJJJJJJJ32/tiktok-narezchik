cmake_minimum_required(VERSION 3.15)
project(silence_finder CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(silence_finder silence_finder.cpp)

if(MSVC)
    target_compile_options(silence_finder PRIVATE /O2)
else()
    target_compile_options(silence_finder PRIVATE -O2)
endif()

if(MINGW)
    target_link_options(silence_finder PRIVATE -static)
endif()

set_target_properties(silence_finder PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/bin")
