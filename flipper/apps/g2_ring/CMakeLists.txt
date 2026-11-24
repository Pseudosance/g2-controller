cmake_minimum_required(VERSION 3.13)
project(g2_ring LANGUAGES C)

# Flipper Zero app build with ufbt.
# The authoritative build is driven by application.fam; this mirrors the
# g2_controller app for standalone/CMake use.

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

find_package(Furi REQUIRED)
find_package(Gui REQUIRED)
find_package(Ble REQUIRED)

add_executable(g2_ring
    g2_ring.c
)

target_link_libraries(g2_ring PRIVATE
    furi
    gui
    ble
)

set_target_properties(g2_ring PROPERTIES
    OUTPUT_NAME "g2_ring"
    SUFFIX ".fap"
)
