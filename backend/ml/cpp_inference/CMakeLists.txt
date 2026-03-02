cmake_minimum_required(VERSION 3.10)
project(BotFCCustomInference)

set(CMAKE_CXX_STANDARD 17)

add_executable(inference_daemon inference_daemon.cpp)
