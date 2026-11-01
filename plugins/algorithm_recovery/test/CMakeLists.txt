add_executable(check-recovery-memory memory.cpp)
target_link_libraries(check-recovery-memory PRIVATE algorithm_recovery)
add_executable(check-recovery-observations observations.cpp)
target_link_libraries(check-recovery-observations PRIVATE algorithm_recovery ${RECOVERY_SODIUM_LIBRARY})
target_include_directories(check-recovery-observations PRIVATE ${RECOVERY_SODIUM_INCLUDE})

find_program(RECOVERY_IVERILOG iverilog)
find_program(RECOVERY_VVP vvp)
find_file(RECOVERY_ICE40_MODEL cells_sim.v PATHS /usr/share/yosys/ice40)
if(RECOVERY_IVERILOG AND RECOVERY_VVP AND RECOVERY_ICE40_MODEL)
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

add_executable(check-recovery-frontend-fixture frontend_fixture.cpp)
target_link_libraries(check-recovery-frontend-fixture PRIVATE algorithm_recovery)
add_executable(check-recovery-frontend frontend.cpp)
target_link_libraries(check-recovery-frontend PRIVATE algorithm_recovery)
add_test(NAME algorithm-recovery-frontend COMMAND check-recovery-frontend
    ${CMAKE_SOURCE_DIR}/plugins/gate_libraries/definitions/ice40ultra_hal.hgl ${CMAKE_CURRENT_BINARY_DIR}/frontend)
