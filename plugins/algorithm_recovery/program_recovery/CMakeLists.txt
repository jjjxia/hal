# recover-program (see README.txt), built together with the plugin into bin/hal_plugins/.
# It does not link HAL. It links the libz3 of the pip package z3-solver 5.3.3, which the program checks
# (evaluation/program_checks) also use. HAL's z3 4.8.12 simplifies to much larger terms
# (dct8_c10_m1: 20,901 instead of 9,076 lines of program). Configure HAL with
#   -DPROGRAM_RECOVERY_Z3_ROOT=<venv>/lib/python3.X/site-packages/z3
set(PROGRAM_RECOVERY_Z3_ROOT "" CACHE PATH "site-packages/z3 of z3-solver 5.3.3 (contains include/ and lib/libz3.so)")
if(NOT EXISTS "${PROGRAM_RECOVERY_Z3_ROOT}/include/z3++.h" OR NOT EXISTS "${PROGRAM_RECOVERY_Z3_ROOT}/lib/libz3.so.5.3")
    message(FATAL_ERROR "algorithm_recovery: set PROGRAM_RECOVERY_Z3_ROOT to site-packages/z3 of z3-solver 5.3.3 "
                        "(pip install z3-solver==5.3.3.0); got '${PROGRAM_RECOVERY_Z3_ROOT}'")
endif()

add_executable(recover-program
    src/main.cpp src/event_graph.cpp src/python_printer.cpp src/transaction_program.cpp src/expr_eval.cpp
    src/lift_tables.cpp src/lift_arithmetic.cpp src/lifted_program.cpp src/program_report.cpp src/folded_rtl.cpp)
set_target_properties(recover-program PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON
    BUILD_RPATH "${PROGRAM_RECOVERY_Z3_ROOT}/lib" INSTALL_RPATH "${PROGRAM_RECOVERY_Z3_ROOT}/lib")
# The z3 5.3.3 headers come first, ahead of HAL's system z3 headers.
target_include_directories(recover-program BEFORE PRIVATE src "${PROGRAM_RECOVERY_Z3_ROOT}/include" ${RECOVERY_SODIUM_INCLUDE})
target_link_libraries(recover-program PRIVATE "${PROGRAM_RECOVERY_Z3_ROOT}/lib/libz3.so" nlohmann_json::nlohmann_json ${RECOVERY_SODIUM_LIBRARY})
target_compile_options(recover-program PRIVATE -Wall -Wextra -Wno-unused-parameter)
install(TARGETS recover-program RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
