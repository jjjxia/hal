#include "algorithm_recovery/program.h"
#include "algorithm_recovery/evaluation.h"
#include "artifacts.h"
#include "program_resources.h"
#include "structured_schedule.h"
#include <iostream>
#include <limits>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using artifacts::require;
        unsigned width(const z3::expr& value) { return value.is_bool() ? 0 : value.get_sort().bv_size(); }
        struct Kernel
        {
            std::string body;
            std::vector<unsigned> inputs;
            unsigned output;
            size_t offset = 0;
            bool arithmetic = false;
        };
        struct Compiled
        {
            unsigned kernel;
            std::vector<std::pair<std::string, unsigned>> inputs;
            z3::expr canonical;
            std::string proof;
        };
        struct Obligation { unsigned kernel; z3::expr original; };
    }

    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output, const ProgramOptions& options)
    {
        return export_program(graph, output, options, nullptr);
    }

    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output,
                                          const ProgramOptions& options, const Json& arithmetic)
    {
        return export_program(graph, output, options, arithmetic, "OBSERVED_PERIODIC");
    }

    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output,
                                          const ProgramOptions& options, const Json& arithmetic, const std::string& execution_kind)
    {
        try
        {
            require(execution_kind == "OBSERVED_PERIODIC" || execution_kind == "COMBINATIONAL_FUNCTIONS", "invalid executable semantics");
            if (execution_kind == "COMBINATIONAL_FUNCTIONS")
                require(graph.available && graph.period == 1 && graph.histories.empty() && graph.memories.empty() && graph.ram_nodes.empty(),
                        "combinational function library contains state or schedule assumptions");
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "program output directory must be empty");
            std::filesystem::create_directories(out);
            if (!graph.available)
            {
                require(graph.nodes.empty() && graph.ram_nodes.empty(), "absent graph contains executable nodes");
                Json report = {{"status", "NO_GRAPH"}, {"period", graph.period}};
                artifacts::write(out / "program.json", report.dump(2) + '\n'); return OK(std::move(report));
            }
            require(graph.period > 0 && graph.period <= std::numeric_limits<unsigned>::max(), "unexecutable graph period");
            std::filesystem::create_directories(out / "proofs");
            std::vector<Kernel> kernels;
            std::map<std::string, unsigned> kernel_ids;
            std::map<std::string, Compiled> compiled;
            std::map<std::string, Obligation> obligations;
            std::map<std::pair<std::string, unsigned>, const Json*> annotations;
            std::set<std::pair<std::string, std::string>> checked_fit_proofs;
            size_t arithmetic_nodes = 0;
            if (!arithmetic.is_null())
            {
                require(arithmetic.at("status") == "ARITHMETIC_CANDIDATES_CHECKED", "unexpected arithmetic stage");
                for (const auto& row : arithmetic.at("nodes"))
                    require(annotations.emplace(std::make_pair(row.at("id")[0].get<std::string>(), row.at("id")[1].get<unsigned>()), &row).second,
                            "duplicate arithmetic annotation");
                require(annotations.size() == graph.nodes.size(), "arithmetic/graph inventory mismatch");
            }
            Json model = {{"period", graph.period}, {"origin", graph.origin}, {"nodes", Json::array()}, {"ram_nodes", Json::array()},
                {"execution_kind", execution_kind},
                {"kernels", Json::array()}, {"memories", Json::object()}, {"histories", Json::array()}, {"excluded", graph.excluded},
                {"coordinates", "raw physical storage; adopted arithmetic executes with explicit signedness and proved coordinate conversions"}};
            const auto compile = [&](const z3::expr& equation, const Json* lifted) -> const Compiled& {
                const std::string cache_key = std::to_string(equation.id()) + (lifted ?
                    Json{{"fit", lifted->at("fit")}, {"input_permutations", lifted->value("input_permutations", Json())},
                         {"output_permutation", lifted->value("output_permutation", Json())}}.dump() : "raw");
                auto found = compiled.find(cache_key); if (found != compiled.end()) return found->second;
                BitVectorProgram program(equation); std::vector<unsigned> widths;
                z3::expr_vector from(equation.ctx()), to(equation.ctx());
                for (const auto& [symbol, bits] : program.inputs())
                {
                    const auto name = "argument_" + std::to_string(widths.size());
                    from.push_back(bits ? equation.ctx().bv_const(symbol.c_str(), bits) : equation.ctx().bool_const(symbol.c_str()));
                    to.push_back(bits ? equation.ctx().bv_const(name.c_str(), bits) : equation.ctx().bool_const(name.c_str()));
                    widths.push_back(bits);
                }
                auto canonical = equation; canonical = canonical.substitute(from, to);
                const auto canonical_body = lifted ? arithmetic_cpp_function("kernel", *lifted, program.inputs()) : program.cpp_function("kernel");
                const auto kernel_key = artifacts::sha256(canonical_body + Json(widths).dump());
                unsigned id;
                if (kernel_ids.count(kernel_key)) id = kernel_ids.at(kernel_key);
                else
                {
                    id = kernels.size(); kernel_ids.emplace(kernel_key, id);
                    const auto name = "kernel_" + std::to_string(id);
                    kernels.push_back({lifted ? arithmetic_cpp_function(name, *lifted, program.inputs()) : program.cpp_function(name), widths, width(equation), 0, bool(lifted)});
                }
                // A broken emitter must not share a proof merely because two
                // different original expressions produced the same C++ body.
                const auto proof = artifacts::sha256(kernel_key + canonical.to_string());
                obligations.emplace(proof, Obligation{id, canonical});
                return compiled.emplace(cache_key, Compiled{id, program.inputs(), canonical, proof}).first->second;
            };
            std::map<std::pair<std::string, unsigned>, std::pair<unsigned, size_t>> writes;
            for (size_t i = 0; i < graph.nodes.size(); ++i)
            {
                const auto& node = graph.nodes[i];
                require(node.phase < graph.period && node.equation.is_bv(), "invalid exported word/phase");
                require(writes.emplace(std::make_pair(node.name, node.phase), std::make_pair(width(node.equation), i)).second, "duplicate exported write");
            }
            std::map<std::pair<std::string, unsigned>, unsigned> boundary_widths;
            const auto row = [&](const auto& node, size_t position, bool ram, const Json* lifted) {
                const auto& function = compile(node.equation, lifted); Json sources = Json::array();
                require(node.sources.size() == function.inputs.size(), "export source inventory mismatch");
                for (const auto& [symbol, bits] : function.inputs)
                {
                    require(bits > 0, "non-bitvector graph boundary");
                    const auto& source = node.sources.at(symbol);
                    require(source.phase < graph.period, "source phase outside exported frame");
                    const std::string kind = source.kind == SourceKind::signal ? "signal" : source.kind == SourceKind::input ? "input" : "ram";
                    if (source.kind == SourceKind::signal)
                    {
                        const auto& target = writes.at({source.name, source.phase}); require(target.first == bits, "signal source width mismatch");
                        if (source.delay) require(graph.histories.count(source), "missing exported history entry");
                        else require(source.phase < node.phase || (source.phase == node.phase && (ram || target.second < position)), "forward/cyclic zero-delay source");
                    }
                    else
                    {
                        require(source.delay == 0 && source.phase == node.phase, "invalid instantaneous source binding");
                        if (source.kind == SourceKind::ram) require(bits == 16 && graph.memories.count(source.name), "unknown/wrong-width RAM source");
                        else
                        {
                            const auto [it, inserted] = boundary_widths.emplace(std::make_pair(source.name, source.phase), bits);
                            require(inserted || it->second == bits, "inconsistent boundary width");
                        }
                    }
                    sources.push_back({{"symbol", symbol}, {"kind", kind}, {"name", source.name}, {"phase", source.phase}, {"delay", source.delay}, {"width", bits}});
                }
                return Json{{"phase", node.phase}, {"kernel", function.kernel}, {"width", width(node.equation)}, {"sources", sources},
                    {"implementation", lifted ? "PROVED_WORD_ARITHMETIC" : "EXACT_BITVECTOR_FALLBACK"},
                    {"raw_sha256", artifacts::sha256(node.equation.to_string())}, {"proof", function.proof}};
            };
            for (size_t i = 0; i < graph.nodes.size(); ++i)
            {
                const auto& node = graph.nodes[i]; const Json* lifted = nullptr;
                if (!arithmetic.is_null())
                {
                    const auto& candidate = *annotations.at({node.name, node.phase});
                    require(candidate.at("raw_sha256") == artifacts::sha256(node.equation.to_string()) && candidate.at("width") == width(node.equation),
                            "arithmetic annotation does not bind this graph node");
                    if (candidate.at("status") == "FIT_PROVED")
                    {
                        require(candidate.at("numeric_order_known") == true, "unordered arithmetic annotation");
                        const auto& proof = candidate.at("proof");
                        const std::pair<std::string, std::string> identity{proof.at("file"), proof.at("sha256")};
                        if (checked_fit_proofs.insert(identity).second)
                            require(artifacts::file_hash(identity.first) == identity.second, "arithmetic proof identity changed");
                        lifted = &candidate; ++arithmetic_nodes;
                    }
                }
                auto entry = row(node, i, false, lifted);
                entry["name"] = node.name; entry["numeric_order_known"] = node.numeric_order_known; model["nodes"].push_back(std::move(entry));
            }
            const std::map<std::string, unsigned> ports{{"RADDR", 11}, {"WADDR", 11}, {"WDATA", 16}, {"MASK", 16}, {"RE", 1}, {"RCLKE", 1}, {"WE", 1}, {"WCLKE", 1}};
            std::set<std::tuple<std::string, std::string, unsigned>> ram_entries;
            for (const auto& node : graph.ram_nodes)
            {
                require(node.phase < graph.period && node.equation.is_bv() && width(node.equation) == ports.at(node.port), "invalid exported RAM port/phase");
                require(graph.memories.count(node.memory) && ram_entries.emplace(node.memory, node.port, node.phase).second, "unknown/duplicate RAM binding");
                auto entry = row(node, 0, true, nullptr); entry["name"] = node.memory; entry["port"] = node.port; model["ram_nodes"].push_back(std::move(entry));
            }
            for (const auto& [name, memory] : graph.memories)
            {
                for (unsigned phase = 0; phase < graph.period; ++phase)
                {
                    for (const auto& [port, bits] : ports) require(ram_entries.count({name, port, phase}), "missing exported RAM port");
                    for (auto clock : {memory.read_clock, memory.write_clock})
                    {
                        const auto [it, added] = boundary_widths.emplace(std::make_pair("event_rise_" + std::to_string(clock), phase), 1);
                        require(added || it->second == 1, "clock edge/input width conflict");
                    }
                }
                model["memories"][name] = {{"initial", memory.initial}, {"read_clock", memory.read_clock}, {"write_clock", memory.write_clock}};
            }
            for (const auto& source : graph.histories)
            {
                require(source.kind == SourceKind::signal && source.delay && writes.count({source.name, source.phase}), "invalid history declaration");
                auto prior = source; --prior.delay;
                require(!prior.delay || graph.histories.count(prior), "history delays not closed under shift");
                model["histories"].push_back({source.name, source.phase, source.delay});
            }
            model["inputs"] = Json::array();
            for (const auto& [key, bits] : boundary_widths) model["inputs"].push_back({key.first, key.second, bits});
            std::string cpp = "// Synthesized executable word arithmetic with exact bitvector fallbacks.\n// State uses physical coordinates; see program.json for proof scope.\n#include \"recovery_runtime.h\"\nusing namespace hal_recovered;\n\n";
            for (unsigned i = 0; i < kernels.size(); ++i)
            {
                auto& kernel = kernels[i]; kernel.offset = cpp.size(); cpp += kernel.body;
                model["kernels"].push_back({{"function", "kernel_" + std::to_string(i)}, {"inputs", kernel.inputs}, {"width", kernel.output},
                    {"implementation", kernel.arithmetic ? "PROVED_WORD_ARITHMETIC" : "EXACT_BITVECTOR_FALLBACK"}});
            }
            const auto schedule = export_structured_schedule(model, out);
            cpp += "\n#include \"recovered_schedule.h\"\n\n";
            const auto model_text = model.dump(); const auto delimiter = "REC" + artifacts::sha256(model_text).substr(0, 12);
            require(model_text.find(")" + delimiter + '"') == std::string::npos, "raw-string delimiter collision");
            cpp += "const char model_text[] = R\"" + delimiter + "(";
            const auto model_offset = cpp.size(); cpp += model_text + ")" + delimiter + "\";\n";
            cpp += "int main()\n{\n    return run_json_lines(model_text, {";
            for (unsigned i = 0; i < kernels.size(); ++i) { if (i) cpp += ", "; cpp += "kernel_" + std::to_string(i); }
            cpp += "}, execute_recovered_schedule);\n}\n";
            artifacts::write(out / "recovered.cpp", cpp);
            artifacts::write(out / "recovery_bits.h", resources::bits);
            artifacts::write(out / "recovery_runtime.h", resources::runtime);
            artifacts::write(out / "json.hpp", resources::json);

            // Decode bytes read back from the saved executable source. Never
            // prove the pre-emission IR as a substitute for emitted functions.
            const auto saved = artifacts::read(out / "recovered.cpp");
            require(Json::parse(saved.substr(model_offset, model_text.size())) == model, "saved program bindings changed");
            Json proofs = Json::object(), kernel_rows = Json::array();
            for (unsigned i = 0; i < kernels.size(); ++i)
                kernel_rows.push_back({{"offset", kernels[i].offset}, {"length", kernels[i].body.size()},
                    {"sha256", artifacts::sha256(saved.substr(kernels[i].offset, kernels[i].body.size()))}});
            for (const auto& [id, obligation] : obligations)
            {
                const auto& kernel = kernels.at(obligation.kernel); std::vector<z3::expr> inputs;
                auto& ctx = obligation.original.ctx();
                for (unsigned i = 0; i < kernel.inputs.size(); ++i)
                {
                    const auto name = "argument_" + std::to_string(i); const auto bits = kernel.inputs[i];
                    inputs.push_back(bits ? ctx.bv_const(name.c_str(), bits) : ctx.bool_const(name.c_str()));
                }
                const auto decoded = decode_cpp_function(saved.substr(kernel.offset, kernel.body.size()), inputs, ctx);
                z3::solver solver(ctx); solver.set("timeout", options.proof_timeout_ms);
                if (options.proof_resource_limit) solver.set("rlimit", options.proof_resource_limit);
                solver.add(decoded != obligation.original);
                const auto file = std::filesystem::path("proofs") / (id + ".smt2"); artifacts::write(out / file, solver.to_smt2());
                const auto status = solver.check();
                require(status == z3::unsat, "saved C++ expression not proved equivalent: " + id + " " + (status == z3::sat ? "SAT" : "UNKNOWN " + solver.reason_unknown()));
                proofs[id] = {{"kernel", obligation.kernel}, {"query", file.string()}, {"sha256", artifacts::file_hash(out / file)}, {"result", "unsat"}};
            }
            Json files = Json::object();
            for (const auto* name : {"recovered.cpp", "recovery_bits.h", "recovery_runtime.h", "json.hpp", "recovered_schedule.h", "schedule.json"})
                files[name] = artifacts::file_hash(out / name);
            Json report = {{"status", "SAVED_CPP_EXPRESSIONS_PROVED"}, {"period", graph.period}, {"origin", graph.origin}, {"nodes", graph.nodes.size()},
                {"execution_kind", execution_kind},
                {"arithmetic_nodes", arithmetic_nodes},
                {"structured_schedule", {{"status", schedule.at("status")}, {"ff_update_rules", schedule.at("ff_update_rules")},
                    {"ram_update_rules", schedule.at("ram_update_rules")}}},
                {"ram_nodes", graph.ram_nodes.size()}, {"kernels", kernel_rows}, {"proofs", proofs}, {"files", files},
                {"model_offset", model_offset}, {"model_length", model_text.size()}, {"model_sha256", artifacts::sha256(model_text)},
                {"scope", "Universal equality of every saved C++ expression under the fixed runtime operation semantics, with exact source bindings. Runtime helpers, graph execution, C++ compiler and Boost remain trusted/tested code; no whole-device or unconditional schedule proof."}};
            artifacts::write(out / "program.json", report.dump(2) + '\n');
            std::cout << "PROGRAM " << graph.nodes.size() << " FF nodes, " << graph.ram_nodes.size() << " RAM nodes, " << kernels.size()
                      << " C++ functions, " << proofs.size() << " proofs" << std::endl;
            return OK(std::move(report));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }

    Result<nlohmann::json> check_program_artifacts(const std::filesystem::path& directory)
    {
        try
        {
            const auto report = Json::parse(artifacts::read(directory / "program.json"));
            require(report.at("status") == "SAVED_CPP_EXPRESSIONS_PROVED", "no proved standalone program");
            for (auto it = report.at("files").begin(); it != report.at("files").end(); ++it)
            {
                const std::filesystem::path file(it.key()); require(!file.is_absolute() && file == file.filename(), "invalid source artifact name");
                require(artifacts::file_hash(directory / file) == it.value(), "program source/runtime identity changed: " + it.key());
            }
            for (const auto* file : {"recovered.cpp", "recovery_bits.h", "recovery_runtime.h", "json.hpp"}) require(report.at("files").contains(file), "missing source identity");
            const auto cpp = artifacts::read(directory / "recovered.cpp");
            const auto model_text = cpp.substr(report.at("model_offset").get<size_t>(), report.at("model_length").get<size_t>());
            require(artifacts::sha256(model_text) == report.at("model_sha256"), "program binding identity changed");
            const auto model = Json::parse(model_text);
            if (report.contains("structured_schedule"))
            {
                require(report.at("files").contains("recovered_schedule.h") && report.at("files").contains("schedule.json"), "missing structured schedule identities");
                const auto schedule = Json::parse(artifacts::read(directory / "schedule.json"));
                require(schedule.at("status") == "EXACT_SCHEDULE_EXPANSION_CHECKED" && schedule.at("model_sha256") == report.at("model_sha256") &&
                    schedule.at("source_sha256") == report.at("files").at("recovered_schedule.h"), "structured schedule/model binding differs");
            }
            require(model.at("nodes").size() == report.at("nodes") && model.at("ram_nodes").size() == report.at("ram_nodes") &&
                model.at("kernels").size() == report.at("kernels").size(), "program inventory mismatch");
            for (const auto& row : report.at("kernels"))
                require(artifacts::sha256(cpp.substr(row.at("offset").get<size_t>(), row.at("length").get<size_t>())) == row.at("sha256"), "kernel identity changed");
            for (const auto& proof : report.at("proofs"))
            {
                const std::filesystem::path file(proof.at("query").get<std::string>());
                require(file.parent_path() == "proofs" && file.extension() == ".smt2", "invalid proof artifact name");
                require(proof.at("result") == "unsat" && artifacts::file_hash(directory / file) == proof.at("sha256"), "saved expression proof identity changed");
            }
            for (const auto* collection : {"nodes", "ram_nodes"}) for (const auto& node : model.at(collection))
                require(report.at("proofs").at(node.at("proof").get<std::string>()).at("kernel") == node.at("kernel"), "missing expression proof binding");
            return OK(model);
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
