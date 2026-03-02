cmake_minimum_required(VERSION 3.10)
project(BotFCCustomBridge)

set(CMAKE_CXX_STANDARD 17)

add_library(custom_inference SHARED custom_inference.cpp)
set_target_properties(custom_inference PROPERTIES POSITION_INDEPENDENT_CODE ON)
