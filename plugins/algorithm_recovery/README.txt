Native algorithm recovery (migration in progress)
================================================

This plugin is being ported from algorithm_recovery/invfold and the separately
preserved firmware adapter. Its current command is RAM extraction, not the
complete netlist-to-algorithm pipeline. See algorithm_recovery/CPP_MIGRATION_STATE.txt.

Build from the HAL repository root:

  cmake -S . -B build -DPL_ALGORITHM_RECOVERY=ON \
    -DPL_SEQUENTIAL_SYMBOLIC_EXECUTION=ON -DPL_Z3_UTILS=ON \
    -DALGORITHM_RECOVERY_TESTS=ON
  cmake --build build --target hal-recover check-recovery-memory \
    check-recovery-ram-extraction -j 4

Extract RAM metadata and symbolic port equations from an imported HAL netlist:

  HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib" \
    build/bin/hal_plugins/hal-recover extract-ram input.hal output.json

The native plugin calls HAL SSE directly. No Python interpreter, script, original
RTL names, grouping table, or observed address schedule is invoked by this path.
HAL's shared plugin infrastructure itself links Python libraries; this is not a
claim that the entire HAL distribution has no Python build/link dependencies.

Supported memory: SB_RAM40_4K, READ_MODE=WRITE_MODE=0, primary-input rising-edge
clocks, concrete hexadecimal bit_vector INIT parameters, LUT/carry cones with
FF/RAM output cutpoints. Unsupported modes, missing pins/parameters, unknown
INIT bits, and unsupported cone cells return explicit errors. Other architectures
are not covered by this primitive adapter. All eight port functions remain
symbolic. Pin lists and expression net_ID_0 symbols refer to native HAL net IDs.
No claim of logical RAM bank recovery is made.

step_ram implements synchronous held read data, independent clock enables,
inverted per-bit write mask and read-before-write simultaneous edges. RamState's
initial read=0 is one explicit two-state choice for the uninitialized output
register, not inferred hardware initialization. The cell's storage INIT is
extracted exactly; no trace contents substitute for it.

Independent primitive regression (requires iverilog and Yosys cells_sim.v):

  ctest --test-dir build/plugins/algorithm_recovery --output-on-failure

This test runs 512 events with arbitrary nonzero INIT, independent read/write
edges, held reads, collisions, full/partial masks and high address bits. It
compares native outputs and final storage with the actual Yosys primitive, and
requires three deliberately wrong transition rules to disagree.

Real-case migration regression (local artifacts are intentionally not tracked):

  HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib" \
    build/bin/hal_plugins/check-recovery-ram-extraction \
    ordered.hal native_ram.json recovered.json

The last two inputs are test evidence only. This regression checks exact pins,
clocks and INIT, then proves equality of each native port expression against
the previously validated Python adapter's expression with Z3. SAT and UNKNOWN
both fail the equality check. It also checks rejection of unsupported modes
and unknown INIT. This is migration equivalence of RAM extraction, not a new
independent whole-device proof. The .hal inputs were produced by the previous
Python frontend; raw recovered.json import is still to be migrated.
