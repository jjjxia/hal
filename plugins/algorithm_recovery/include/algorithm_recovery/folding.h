#pragma once
#include "algorithm_recovery/memory.h"
#include <set>
#include <tuple>

namespace hal::algorithm_recovery
{
    enum class Write { hold, full, mixed_or_unknown };
    struct WordWindow
    {
        std::vector<std::string> bits; // physical cutpoint symbols, LSB first
        std::vector<Write> writes;
        std::vector<z3::expr> equations; // effective D including reset, time-normalized
        bool numeric_order_known = false;
    };
    struct MemoryWindow
    {
        Ice40Ram model;
        std::vector<std::string> read_bits; // symbols used in the equations
    };
    struct FoldInput
    {
        u64 start = 0;
        u32 length = 0; // contiguous observed extraction window
        std::map<std::string, WordWindow> words;
        std::map<std::string, std::vector<std::string>> inputs;
        std::map<std::string, MemoryWindow> memories;
    };
    enum class SourceKind { signal, input, ram };
    struct Source
    {
        SourceKind kind;
        std::string name;
        u32 phase = 0, delay = 0;
        auto key() const { return std::make_tuple(kind, name, phase, delay); }
        bool operator==(const Source& other) const { return key() == other.key(); }
        bool operator<(const Source& other) const { return key() < other.key(); }
    };
    struct FoldNode
    {
        std::string name;
        u32 phase;
        z3::expr equation;
        std::map<std::string, Source> sources;
        bool numeric_order_known = false;
    };
    struct RamNode
    {
        std::string memory, port;
        u32 phase;
        z3::expr equation;
        std::map<std::string, Source> sources;
    };
    struct FoldGraph
    {
        // An absent graph is a supported outcome, never a fabricated period=1.
        bool available = false;
        u64 period = 0, origin = 0;
        std::map<std::string, u32> periods;
        std::map<std::string, std::string> excluded;
        std::vector<FoldNode> nodes;
        std::vector<RamNode> ram_nodes;
        std::map<std::string, Ice40Ram> memories;
        std::set<Source> histories;
    };

    // Detect periods over the entire window, retain one real prefix period, then
    // check last-write bindings across three actual repetitions. No extrapolated
    // writes, inferred numeric bit order, or trace-address memory oracle.
    Result<FoldGraph> fold_observed(const FoldInput& input, z3::context& ctx);

    using FrameValues = std::map<std::pair<std::string, u32>, z3::expr>;
    using History = std::map<Source, z3::expr>;
    struct FrameResult
    {
        FrameValues values;
        History history;
        std::map<std::string, RamState> memories;
        std::vector<std::map<std::string, std::pair<u16, u16>>> reads;
    };
    // Power-up storage from extracted INIT; this is not the warmed state at the
    // graph's origin. initial_read explicitly chooses the uninitialized output.
    std::map<std::string, RamState> initial_memories(const FoldGraph& graph, u16 initial_read = 0);
    // All values are concrete fixed-width bit vectors. The native Z3 evaluator
    // preserves arbitrarily wide words. This is raw graph execution; arithmetic
    // lifting and standalone code generation are separate migration steps.
    Result<FrameResult> run_frame(const FoldGraph& graph, const History& history,
                                 const FrameValues& inputs, const std::map<std::string, RamState>& memories,
                                 z3::context& ctx);
}
