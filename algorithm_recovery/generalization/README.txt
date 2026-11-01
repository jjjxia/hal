Independent-source benchmark preparation and scoring
===================================================
PLAN.txt is the frozen case selection, priors, budgets and follow-up plan.
RESULTS.txt records both completed trials, original failures, generic repairs,
finite execution checks and remaining high-level recovery limitations.
This directory contains evaluation adapters, not new recovery algorithms.

From /home/jiahao/hal:
  c++ -std=c++17 -O1 -Wall -Wextra -Ideps/nlohmann_json \
    algorithm_recovery/generalization/benchmark_driver.cpp -lsodium \
    -o build/bin/benchmark-driver
  bash algorithm_recovery/generalization/run_two.sh build/generalization_NEW

Requires built hal-recover, Yosys, Icarus, Git, jq, C++17, libsodium, and network
access for pinned upstream clones. The two synthesis/recovery cases execute
serially, each under a 180-minute wall timeout and 16 GiB address-space ceiling.
Do not overlap this runner with the existing firmware queue. Case receipts and
all source checkouts, synthesized netlists, generated programs, proof queries
and logs stay under ignored build/. No external source tree is vendored in Git.

Inputs are original upstream parameterized RTL, not our constructed biquad.
The adapter's cell/net/port renaming keeps the original primitive graph and
retains physical IDs; score-side names/port grouping are not recovery inputs.
Native HAL reconstructs grouping/order from the normalized gate model.
Synthesis and normalization are checked by comparing every known RTL output
against the normalized gate simulation, including initialization observations.
A separate exact transaction reference checks 64 training and 64 held-out
samples; FIR additionally has one initial prefix output and a drain transaction.
The supplied external protocol is explicitly recorded as a prior. It is not an
internal-state cut, word-order annotation or claim of protocol certification.

Artifacts per case:
  input/evaluation_contract.json   adapter identities, exact split and oracle
  input/reference_equivalence.json original RTL vs anonymous gate finite check
  input/score.json                 transaction-level reference result
  groups/                         automatic HAL groups/order/MI
  trace/                          original-gate physical observations
  analysis/fold_report.json        raw event graph availability/coverage
  analysis/state_machine/          generated guarded whole-state C++
  analysis/state_replay/replay.json continuous compiled C++ vs original gate
  receipt.json                    actual stage, exit and resource limits

A successful transaction oracle validates benchmark preparation, not recovery.
A successful state replay validates finite execution, not a universal e2e
certificate or automatic recognition of FIR/CORDIC. Report all stages separately.
An import/configuration failure remains a frontend failure, not an InvFold
counterexample. Preserve original failed receipts before any generic repair.
