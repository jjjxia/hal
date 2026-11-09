# Standalone build of recover-program (see README.txt). It links the same libz3 as the
# Python reference implementation so that outputs can be compared byte for byte.
#   cmake -S plugins/algorithm_recovery/program_recovery -B build/program_recovery -DZ3_PACKAGE_ROOT=<site-packages>/z3
#   cmake --build build/program_recovery -j 2
cmake_minimum_required(VERSION 3.16)
project(program_recovery CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

set(Z3_PACKAGE_ROOT "" CACHE PATH "z3 package root containing include/ and lib/ (the Python reference venv's site-packages/z3)")
if(NOT Z3_PACKAGE_ROOT)
    message(FATAL_ERROR "set Z3_PACKAGE_ROOT to the z3 package used by the Python reference (z3-solver 5.3.3)")
endif()
set(JSON_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../deps/nlohmann_json" CACHE PATH "directory containing nlohmann/json.hpp")

find_library(SODIUM_LIB sodium REQUIRED)
add_executable(recover-program
    src/main.cpp src/event_graph.cpp src/python_printer.cpp src/transaction_program.cpp src/expr_eval.cpp src/lift_tables.cpp src/lift_arithmetic.cpp src/lifted_program.cpp src/python_interpreter.cpp src/trace_validation.cpp src/equation_check.cpp src/folded_rtl.cpp)
target_include_directories(recover-program PRIVATE src "${Z3_PACKAGE_ROOT}/include" "${JSON_INCLUDE_DIR}")
target_link_libraries(recover-program PRIVATE "${Z3_PACKAGE_ROOT}/lib/libz3.so" ${SODIUM_LIB})
set_target_properties(recover-program PROPERTIES BUILD_RPATH "${Z3_PACKAGE_ROOT}/lib" INSTALL_RPATH "${Z3_PACKAGE_ROOT}/lib")
target_compile_options(recover-program PRIVATE -Wall -Wextra -Wno-unused-parameter)
