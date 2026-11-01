option(PL_ALGORITHM_RECOVERY "Build native algorithm recovery" OFF)
option(ALGORITHM_RECOVERY_TESTS "Build algorithm recovery regression checks" OFF)

if(PL_ALGORITHM_RECOVERY OR BUILD_ALL_PLUGINS)
    find_path(RECOVERY_SODIUM_INCLUDE sodium.h)
    find_library(RECOVERY_SODIUM_LIBRARY sodium)
    if(NOT RECOVERY_SODIUM_INCLUDE OR NOT RECOVERY_SODIUM_LIBRARY)
        message(FATAL_ERROR "algorithm_recovery requires libsodium for artifact SHA256 identities")
    endif()
    if(NOT (PL_SEQUENTIAL_SYMBOLIC_EXECUTION OR BUILD_ALL_PLUGINS))
        message(FATAL_ERROR "algorithm_recovery requires PL_SEQUENTIAL_SYMBOLIC_EXECUTION=ON")
    endif()
    foreach(dependency PL_DATAFLOW PL_MODULE_IDENTIFICATION PL_BITORDER_PROPAGATION PL_BOOLEAN_INFLUENCE)
        if(NOT (${dependency} OR BUILD_ALL_PLUGINS))
            message(FATAL_ERROR "algorithm_recovery requires ${dependency}=ON")
        endif()
    endforeach()
    hal_add_plugin(algorithm_recovery SHARED
        HEADER include/algorithm_recovery/plugin_algorithm_recovery.h include/algorithm_recovery/memory.h include/algorithm_recovery/folding.h include/algorithm_recovery/frontend.h include/algorithm_recovery/grouping.h include/algorithm_recovery/observations.h include/algorithm_recovery/guided.h include/algorithm_recovery/evaluation.h include/algorithm_recovery/arithmetic.h include/algorithm_recovery/lifting.h
        SOURCES src/plugin_algorithm_recovery.cpp src/memory.cpp src/folding.cpp src/frontend.cpp src/grouping.cpp src/observations.cpp src/guided.cpp src/evaluation.cpp src/arithmetic.cpp src/lifting.cpp
        LINK_LIBRARIES PUBLIC sequential_symbolic_execution nlohmann_json::nlohmann_json dataflow module_identification bitorder_propagation PRIVATE ${RECOVERY_SODIUM_LIBRARY})
    target_include_directories(algorithm_recovery PRIVATE ${RECOVERY_SODIUM_INCLUDE})
    target_sources(algorithm_recovery PRIVATE src/routing.cpp include/algorithm_recovery/routing.h)

    add_executable(hal-recover cli/main.cpp)
    target_link_libraries(hal-recover PRIVATE algorithm_recovery)
    install(TARGETS hal-recover RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

    if(ALGORITHM_RECOVERY_TESTS)
        enable_testing()
        add_subdirectory(test)
    endif()
endif()
