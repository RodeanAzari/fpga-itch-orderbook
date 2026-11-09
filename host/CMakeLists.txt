cmake_minimum_required(VERSION 3.20)
project(orderbook_host CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)  # for clang-tidy
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

if(CMAKE_BUILD_TYPE STREQUAL "Debug")
  add_compile_options(-fsanitize=address,undefined -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

find_package(ZLIB REQUIRED)

add_library(mold STATIC mold.cpp)
target_include_directories(mold PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(mold PUBLIC ZLIB::ZLIB)
target_compile_options(mold PRIVATE -Wall -Wextra -Werror)

add_executable(replay replay.cpp)
target_link_libraries(replay PRIVATE mold)
target_compile_options(replay PRIVATE -Wall -Wextra -Werror)

add_executable(capture capture.cpp)
target_compile_options(capture PRIVATE -Wall -Wextra -Werror)

# GoogleTest, pinned by tag and hash (docs/toolchain.md).
include(FetchContent)
FetchContent_Declare(googletest
  URL https://github.com/google/googletest/archive/refs/tags/v1.17.0.tar.gz
  URL_HASH SHA256=65fab701d9829d38cb77c14acdc431d2108bfdbf8979e40eb8ae567edf10b27c
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
add_executable(test_mold test_mold.cpp)
target_link_libraries(test_mold PRIVATE mold GTest::gtest_main)
target_compile_options(test_mold PRIVATE -Wall -Wextra -Werror)
include(GoogleTest)
gtest_discover_tests(test_mold)
