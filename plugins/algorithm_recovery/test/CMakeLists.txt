add_executable(check-recovery-memory memory.cpp)
target_link_libraries(check-recovery-memory PRIVATE algorithm_recovery)
add_executable(check-recovery-observations observations.cpp)
target_link_libraries(check-recovery-observations PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-observations PRIVATE ${RECOVERY_SODIUM_INCLUDE})

find_program(RECOVERY_IVERILOG iverilog)
find_program(RECOVERY_VVP vvp)
find_file(RECOVERY_ICE40_MODEL cells_sim.v PATHS /usr/share/yosys/ice40)
if(RECOVERY_IVERILOG AND RECOVERY_VVP AND RECOVERY_ICE40_MODEL)
    add_executable(check-recovery-trace-options trace_options.cpp)
    target_link_libraries(check-recovery-trace-options PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
    target_include_directories(check-recovery-trace-options PRIVATE ${RECOVERY_SODIUM_INCLUDE})
    add_test(NAME algorithm-recovery-trace-options COMMAND ${CMAKE_COMMAND}
        -DGENERATOR=$<TARGET_FILE:check-recovery-trace-options> -DMODEL=${RECOVERY_ICE40_MODEL}
        -DWORK=${CMAKE_CURRENT_BINARY_DIR}/trace_options -P ${CMAKE_CURRENT_SOURCE_DIR}/trace_options.cmake)
    add_executable(check-recovery-replay replay.cpp)
    target_link_libraries(check-recovery-replay PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
    target_include_directories(check-recovery-replay PRIVATE ${RECOVERY_SODIUM_INCLUDE})
    add_test(NAME algorithm-recovery-replay COMMAND ${CMAKE_COMMAND}
        -DGENERATOR=$<TARGET_FILE:check-recovery-replay> -DCXX=${CMAKE_CXX_COMPILER}
        -DIVERILOG=${RECOVERY_IVERILOG} -DVVP=${RECOVERY_VVP} -DMODEL=${RECOVERY_ICE40_MODEL}
        -DWORK=${CMAKE_CURRENT_BINARY_DIR}/replay -P ${CMAKE_CURRENT_SOURCE_DIR}/replay.cmake)
    add_test(NAME algorithm-recovery-observations-yosys COMMAND ${CMAKE_COMMAND}
        -DGENERATOR=$<TARGET_FILE:check-recovery-observations>
        -DIVERILOG=${RECOVERY_IVERILOG} -DVVP=${RECOVERY_VVP}
        -DMODEL=${RECOVERY_ICE40_MODEL}
        -DWORK=${CMAKE_CURRENT_BINARY_DIR}/ff_primitive
        -P ${CMAKE_CURRENT_SOURCE_DIR}/observations_yosys.cmake)
    add_test(NAME algorithm-recovery-memory-yosys COMMAND ${CMAKE_COMMAND}
        -DGENERATOR=$<TARGET_FILE:check-recovery-memory>
        -DIVERILOG=${RECOVERY_IVERILOG} -DVVP=${RECOVERY_VVP}
        -DMODEL=${RECOVERY_ICE40_MODEL}
        -DWORK=${CMAKE_CURRENT_BINARY_DIR}/ram_primitive
        -P ${CMAKE_CURRENT_SOURCE_DIR}/memory_yosys.cmake)
else()
    message(WARNING "Yosys iCE40 primitive regression unavailable: install iverilog and yosys")
endif()

add_executable(check-recovery-ram-extraction extraction.cpp)
target_link_libraries(check-recovery-ram-extraction PRIVATE algorithm_recovery)

add_executable(check-recovery-folding-fixture folding_fixture.cpp)
target_link_libraries(check-recovery-folding-fixture PRIVATE algorithm_recovery)

add_executable(check-recovery-folding folding.cpp)
target_link_libraries(check-recovery-folding PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-folding COMMAND check-recovery-folding)

add_executable(check-recovery-arithmetic arithmetic.cpp)
target_link_libraries(check-recovery-arithmetic PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-arithmetic COMMAND check-recovery-arithmetic)
add_executable(check-recovery-routing routing.cpp)
target_link_libraries(check-recovery-routing PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-routing COMMAND check-recovery-routing ${CMAKE_CURRENT_BINARY_DIR}/routing)
add_executable(check-recovery-program program.cpp)
target_link_libraries(check-recovery-program PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-program PRIVATE ${RECOVERY_SODIUM_INCLUDE})
add_test(NAME algorithm-recovery-program COMMAND ${CMAKE_COMMAND}
    -DGENERATOR=$<TARGET_FILE:check-recovery-program> -DCXX=${CMAKE_CXX_COMPILER}
    -DWORK=${CMAKE_CURRENT_BINARY_DIR}/program -P ${CMAKE_CURRENT_SOURCE_DIR}/program.cmake)
add_executable(check-recovery-program-fixture program_fixture.cpp)
target_link_libraries(check-recovery-program-fixture PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-program-fixture PRIVATE ${RECOVERY_SODIUM_INCLUDE})
add_executable(check-recovery-replay-fixture replay_fixture.cpp)
target_link_libraries(check-recovery-replay-fixture PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-replay-fixture PRIVATE ${RECOVERY_SODIUM_INCLUDE})
add_executable(check-recovery-routing-fixture routing_fixture.cpp)
target_link_libraries(check-recovery-routing-fixture PRIVATE algorithm_recovery)
add_executable(check-recovery-arithmetic-fixture arithmetic_fixture.cpp)
target_link_libraries(check-recovery-arithmetic-fixture PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-arithmetic-fixture PRIVATE ${RECOVERY_SODIUM_INCLUDE})

add_executable(check-recovery-frontend-fixture frontend_fixture.cpp)
target_link_libraries(check-recovery-frontend-fixture PRIVATE algorithm_recovery)
add_executable(check-recovery-frontend frontend.cpp)
target_link_libraries(check-recovery-frontend PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-frontend COMMAND check-recovery-frontend
    ${CMAKE_SOURCE_DIR}/plugins/gate_libraries/definitions/ice40ultra_hal.hgl ${CMAKE_CURRENT_BINARY_DIR}/frontend)
add_executable(check-recovery-mi-controls mi_controls.cpp)
target_link_libraries(check-recovery-mi-controls PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-mi-controls COMMAND check-recovery-mi-controls
    ${CMAKE_SOURCE_DIR}/plugins/gate_libraries/definitions/ice40ultra_hal.hgl ${CMAKE_CURRENT_BINARY_DIR}/mi_controls)
