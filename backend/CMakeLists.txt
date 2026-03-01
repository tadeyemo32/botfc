cmake_minimum_required(VERSION 3.10...3.31)
project(botfc_backend)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_PREFIX_PATH "/opt/homebrew" ${CMAKE_PREFIX_PATH})

# -------------------------------------------------------------
# BotFC Server (macOS Native API Orchestrator)
# -------------------------------------------------------------
# Suppress the FindBoost policy warning for CMake 4+
if(POLICY CMP0167)
  cmake_policy(SET CMP0167 OLD)
endif()

find_package(Boost 1.80 QUIET)
find_package(nlohmann_json QUIET)

if(Boost_FOUND AND nlohmann_json_FOUND)
    include_directories(/opt/homebrew/include)
    include_directories(include/server include/core)
    link_directories(/opt/homebrew/lib)

    add_executable(botfc_server 
        src/server/server_main.cpp 
        src/server/HttpServer.cpp 
        src/server/WsSession.cpp
        src/server/HttpSession.cpp
        src/core/SshDeployer.cpp 
        src/core/Config.cpp 
        src/core/SshService.cpp
    )

    target_link_libraries(botfc_server
        PRIVATE 
        Boost::boost 
        ssh 
        pthread 
        nlohmann_json::nlohmann_json
    )
else()
    message(WARNING "Boost or nlohmann_json not found, skipping botfc_server build.")
endif()

# -------------------------------------------------------------
# BotFC Robot Brain (Python only now)
# -------------------------------------------------------------
# We previously had a C++ cross-compiled brain here, but we
# have fully migrated to Python (botfc_brain.py) so there is 
# no longer a requirement to invoke qibuild/qi_create_bin.
