option(PL_ALGORITHM_RECOVERY "Build native algorithm recovery" OFF)
option(ALGORITHM_RECOVERY_TESTS "Build algorithm recovery regression checks" OFF)

if(PL_ALGORITHM_RECOVERY OR BUILD_ALL_PLUGINS)
    if(NOT (PL_SEQUENTIAL_SYMBOLIC_EXECUTION OR BUILD_ALL_PLUGINS))
        message(FATAL_ERROR "algorithm_recovery requires PL_SEQUENTIAL_SYMBOLIC_EXECUTION=ON")
    endif()
    hal_add_plugin(algorithm_recovery SHARED
        HEADER include/algorithm_recovery/plugin_algorithm_recovery.h include/algorithm_recovery/memory.h include/algorithm_recovery/folding.h
        SOURCES src/plugin_algorithm_recovery.cpp src/memory.cpp src/folding.cpp
        LINK_LIBRARIES PUBLIC sequential_symbolic_execution nlohmann_json::nlohmann_json)

    add_executable(hal-recover cli/main.cpp)
    target_link_libraries(hal-recover PRIVATE algorithm_recovery)
    install(TARGETS hal-recover RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

    if(ALGORITHM_RECOVERY_TESTS)
        enable_testing()
        add_subdirectory(test)
    endif()
endif()
