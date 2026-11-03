#!/usr/bin/env bash
# Build the E6 drivers: e6_fold links fold_observed from the plugin library used for E1-E5 (unchanged);
# e6_fold_local compiles folding_local.cpp (the pre-declared "local" variant) into the driver instead.
set -euo pipefail
H=/home/jiahao/hal; E=$H/algorithm_recovery/generalization/e6; O=$H/build/e6/bin
mkdir -p "$O"
F="-O2 -std=c++17 -I$H/plugins/algorithm_recovery/include -I$H/include -I$H/deps/nlohmann_json -I$H/deps"
L="-L$H/build/lib/hal_plugins -l:algorithm_recovery.so -L$H/build/lib -lhal_core -lz3 -Wl,-rpath,$H/build/lib/hal_plugins:$H/build/lib"
g++ $F "$E/e6_fold.cpp" -o "$O/e6_fold" $L
g++ $F "$E/e6_fold.cpp" "$E/folding_local.cpp" -o "$O/e6_fold_local" $L
md5sum "$H/build/lib/hal_plugins/algorithm_recovery.so" "$H/plugins/algorithm_recovery/src/folding.cpp"
