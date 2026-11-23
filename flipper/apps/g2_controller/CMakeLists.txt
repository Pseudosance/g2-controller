cmake_minimum_required(VERSION 3.13)
project(g2_controller LANGUAGES C)

# Flipper Zero app build with ufbt
# This works both in flipperzero-firmware/applications_user/ and standalone

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Find Flipper Zero SDK
find_package(Furi REQUIRED)
find_package(Gui REQUIRED)
find_package(Ble REQUIRED)
find_package(Notification REQUIRED)
find_package(Storage REQUIRED)

add_executable(g2_controller
    g2_controller.c
)

target_link_libraries(g2_controller PRIVATE
    furi
    gui
    ble
    notification
    storage
)

# Output .fap file
set_target_properties(g2_controller PROPERTIES
    OUTPUT_NAME "g2_controller"
    SUFFIX ".fap"
)
