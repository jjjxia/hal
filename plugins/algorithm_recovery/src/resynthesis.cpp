#include "algorithm_recovery/program.h"
#include "artifacts.h"

namespace hal::algorithm_recovery
{
    Result<nlohmann::json> resynthesize_program(const std::filesystem::path& directory,
        const std::filesystem::path& arithmetic_path, const std::filesystem::path& output,
        z3::context& ctx, const ProgramOptions& options)
    {
        try
        {
            using Json = nlohmann::json; using artifacts::require;
            const auto checked = check_program_artifacts(directory);
            if (checked.is_error()) return ERR(checked.get_error().get());
            const auto& model = checked.get();
            const auto manifest_text = artifacts::read(directory / "program.json"), cpp = artifacts::read(directory / "recovered.cpp");
            const auto manifest = Json::parse(manifest_text);
            const auto arithmetic_text = artifacts::read(arithmetic_path); auto arithmetic = Json::parse(arithmetic_text);
            require(arithmetic.at("status") == "ARITHMETIC_CANDIDATES_CHECKED", "no checked arithmetic for resynthesis");
            std::map<std::pair<std::string, unsigned>, Json*> annotations;
            for (auto& row : arithmetic.at("nodes"))
                require(annotations.emplace(std::make_pair(row.at("id")[0].get<std::string>(), row.at("id")[1].get<unsigned>()), &row).second,
                        "duplicate resynthesis annotation");
            require(annotations.size() == model.at("nodes").size(), "resynthesis annotation inventory differs");
            std::vector<z3::expr> kernels; std::vector<std::vector<z3::expr>> arguments;
            for (size_t i = 0; i < model.at("kernels").size(); ++i)
            {
                const auto& row = manifest.at("kernels").at(i); std::vector<z3::expr> inputs;
                for (const auto& bits : model.at("kernels").at(i).at("inputs"))
                {
                    const unsigned width = bits; const auto name = "argument_" + std::to_string(inputs.size());
                    inputs.push_back(width ? ctx.bv_const(name.c_str(), width) : ctx.bool_const(name.c_str()));
                }
                kernels.push_back(decode_cpp_function(cpp.substr(row.at("offset").get<size_t>(), row.at("length").get<size_t>()), inputs, ctx));
                arguments.push_back(std::move(inputs));
            }
            FoldGraph graph; graph.available = true; graph.period = model.at("period"); graph.origin = model.at("origin");
            graph.excluded = model.at("excluded").get<std::map<std::string, std::string>>();
            const auto decode_node = [&](const Json& row, std::map<std::string, Source>& sources) {
                const unsigned kernel = row.at("kernel"); auto equation = kernels.at(kernel);
                require(arguments.at(kernel).size() == row.at("sources").size(), "resynthesis source inventory mismatch");
                z3::expr_vector from(ctx), to(ctx);
                for (size_t i = 0; i < row.at("sources").size(); ++i)
                {
                    const auto& source = row.at("sources")[i]; const std::string kind = source.at("kind"), symbol = source.at("symbol");
                    require(kind == "input" || kind == "signal" || kind == "ram", "invalid saved source kind");
                    const auto& original = arguments.at(kernel)[i]; const unsigned width = source.at("width");
                    require(original.is_bv() && original.get_sort().bv_size() == width, "resynthesis source width mismatch");
                    from.push_back(original); to.push_back(ctx.bv_const(symbol.c_str(), width));
                    require(sources.emplace(symbol, Source{kind == "input" ? SourceKind::input : kind == "signal" ? SourceKind::signal : SourceKind::ram,
                                source.at("name"), source.at("phase"), source.at("delay")}).second, "duplicate saved source symbol");
                }
                equation = equation.substitute(from, to);
                require(equation.is_bv() && equation.get_sort().bv_size() == row.at("width"), "resynthesis output width mismatch");
                return equation;
            };
            for (const auto& row : model.at("nodes"))
            {
                const std::string name = row.at("name"); const unsigned phase = row.at("phase"); std::map<std::string, Source> sources;
                auto equation = decode_node(row, sources); auto& annotation = *annotations.at({name, phase});
                require(annotation.at("raw_sha256") == row.at("raw_sha256") && annotation.at("width") == row.at("width"),
                        "arithmetic does not refer to the saved original program node");
                // The independently decoded saved expression can have a different
                // syntactic AST from its already-proved original. Preserve that
                // ancestry before binding the new export to the decoded equation.
                annotation["ancestor_raw_sha256"] = annotation.at("raw_sha256");
                annotation["raw_sha256"] = artifacts::sha256(equation.to_string());
                graph.nodes.push_back({name, phase, equation, std::move(sources), row.at("numeric_order_known")});
                graph.periods.emplace(name, graph.period);
            }
            for (const auto& row : model.at("ram_nodes"))
            {
                std::map<std::string, Source> sources; auto equation = decode_node(row, sources);
                graph.ram_nodes.push_back({row.at("name"), row.at("port"), row.at("phase"), equation, std::move(sources)});
            }
            for (auto memory = model.at("memories").begin(); memory != model.at("memories").end(); ++memory)
            {
                Ice40Ram ram; ram.name = memory.key(); ram.initial = memory.value().at("initial").get<std::array<u16, 256>>();
                ram.read_clock = memory.value().at("read_clock"); ram.write_clock = memory.value().at("write_clock");
                graph.memories.emplace(memory.key(), std::move(ram));
            }
            for (const auto& history : model.at("histories"))
            {
                require(history.size() == 3, "invalid saved history tuple");
                require(graph.histories.insert({SourceKind::signal, history[0], history[1], history[2]}).second, "duplicate saved history");
            }
            const auto exported = export_program(graph, output, options, arithmetic, model.value("execution_kind", std::string("OBSERVED_PERIODIC")));
            if (exported.is_error()) return ERR(exported.get_error().get());
            Json ancestry{{"status", "SAVED_PROGRAM_RESYNTHESIZED"}, {"source_program", std::filesystem::absolute(directory).string()},
                {"source_manifest_sha256", artifacts::sha256(manifest_text)}, {"source_arithmetic", std::filesystem::absolute(arithmetic_path).string()},
                {"source_arithmetic_sha256", artifacts::sha256(arithmetic_text)}, {"program_manifest_sha256", artifacts::file_hash(output / "program.json")},
                {"arithmetic_nodes", exported.get().at("arithmetic_nodes")},
                {"scope", "Saved source decoded after artifact checks; every newly emitted expression proved against it. Preserves the original observed schedule and physical storage. Compilation/replay is a separate check; no new whole-device guarantee."}};
            artifacts::write(output / "resynthesis.json", ancestry.dump(2) + '\n');
            return OK(std::move(ancestry));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
