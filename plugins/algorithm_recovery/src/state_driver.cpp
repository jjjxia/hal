#include "state_driver.h"
#include "algorithm_recovery/arithmetic.h"
#include "artifacts.h"
#include "program_resources.h"
#include <regex>
#include <sstream>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using artifacts::require;
        std::string quote(const std::string& text) { return Json(text).dump(); }
        std::string identifier(const std::string& text)
        { require(std::regex_match(text, std::regex("[A-Za-z_][A-Za-z_0-9]*")), "invalid generated state identifier"); return text; }
        std::string boolean(const std::string& name)
        { return name == "c_zero" ? "false" : name == "c_one" ? "true" : "now.at(" + quote(name) + ')'; }
    }

    Json export_state_driver(const std::filesystem::path& out, const Json& description, const Json& data, const Json& controls, unsigned max_delta_events)
    {
        auto schema = description; schema["max_delta_events"] = max_delta_events;
        std::map<std::string, Json> functions, control_functions;
        for (const auto& row : data.at("nodes")) require(functions.emplace(row.at("name"), row).second, "duplicate state data function");
        for (const auto& row : controls.at("nodes")) require(control_functions.emplace(row.at("name"), row).second, "duplicate state control function");
        Json copied = Json::array();
        const auto header = [&](const std::string& directory, const Json& model, const std::string& space, const std::string& file, bool arithmetic) {
            const auto source = artifacts::read(out / directory / "recovered.cpp");
            const auto manifest = Json::parse(artifacts::read(out / directory / "program.json"));
            std::string text = "#pragma once\nnamespace " + space + " {\nusing namespace hal_recovered;\n";
            for (size_t i = 0; i < model.at("kernels").size(); ++i)
            {
                const bool word = model.at("kernels")[i].at("implementation") == "PROVED_WORD_ARITHMETIC";
                if (word != arithmetic) continue;
                const auto& record = manifest.at("kernels")[i];
                const auto body = source.substr(record.at("offset").get<size_t>(), record.at("length").get<size_t>());
                require(artifacts::sha256(body) == record.at("sha256"), "state driver copied an unbound function body");
                copied.push_back({{"file", file}, {"offset", text.size()}, {"length", body.size()}, {"sha256", record.at("sha256")},
                    {"origin", directory}, {"kernel", i}}); text += body;
            }
            text += "}\n"; artifacts::write(out / file, text);
        };
        header("data", data, "recovered_data", "arithmetic_kernels.h", true);
        header("data", data, "recovered_data", "exact_data_kernels.h", false);
        header("controls", controls, "recovered_controls", "control_kernels.h", false);
        for (const auto& kernel : controls.at("kernels")) require(kernel.at("implementation") == "EXACT_BITVECTOR_FALLBACK", "unexpected lifted control kernel");
        const auto call = [&](const Json& function, const std::string& space) {
            std::ostringstream expression; expression << space << "::kernel_" << function.at("kernel") << "({"; bool first = true;
            for (const auto& source : function.at("sources"))
            {
                require(source.at("kind") == "input" && source.at("phase") == 0 && source.at("delay") == 0, "state function has an unresolved time binding");
                const auto& binding = schema.at("bindings").at(source.at("name").get<std::string>());
                require(binding.at("width") == source.at("width"), "state function binding width mismatch");
                if (!first) expression << ", "; first = false; const std::string kind = binding.at("kind"), name = binding.at("name");
                if (kind == "state") expression << "state.words.at(" << quote(name) << ')';
                else if (kind == "memory") expression << "Integer(state.memories.at(" << quote(name) << ").read)";
                else { require(kind == "pad", "unsupported internal state binding"); expression << "pads.at(" << quote(name) << ')'; }
            }
            return expression.str() + "})";
        };
        std::ostringstream source;
        source << "// Generated guarded event/state program. All data/control cones come from HAL.\n"
                  "// Unobserved guards use unconditional exact fallbacks; no observed period is assumed.\n"
                  "#include \"recovery_state.h\"\n#include \"arithmetic_kernels.h\"\n"
                  "#include \"exact_data_kernels.h\"\n#include \"control_kernels.h\"\n#include \"state_schema.h\"\n"
                  "using namespace hal_recovered;\n\n"
                  "Controls evaluate_controls(const MachineState& state, const Pads& pads)\n{\n    Controls result;\n";
        for (const auto& [name, function] : control_functions)
            source << "    result[" << quote(name) << "] = " << call(function, "recovered_controls") << " != 0;\n";
        source << "    return result;\n}\n\n";
        for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
        {
            source << "Integer data_" << identifier(word.key()) << "(const MachineState& state, const Pads& pads, const Controls& now)\n{\n";
            for (const auto& variant : word.value().at("variants"))
            {
                source << "    if ("; bool first = true;
                for (const auto& condition : variant.at("when"))
                {
                    if (!first) source << " && "; first = false;
                    const auto expression = boolean(condition.at("control")); source << (condition.at("value") == 0 ? "!(" + expression + ')' : expression);
                }
                require(!first, "empty guarded specialization");
                source << ")\n        return " << call(functions.at(variant.at("function").get<std::string>()), "recovered_data") << ";\n";
            }
            source << "    return " << call(functions.at(word.value().at("fallback").get<std::string>()), "recovered_data") << "; // unconditional HAL fallback\n}\n\n";
        }
        source << "MachineState apply_event(const MachineState& state, const Pads& pads, const Controls& before, const Controls& now)\n{\n"
                  "    MachineState next = state; // all right-hand sides read this event's old state\n";
        for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
        {
            const std::string name = word.key(); const unsigned width = word.value().at("width");
            source << "\n    // " << name << ": " << width << " physical storage bits\n";
            for (const auto& block : word.value().at("blocks"))
            {
                const std::string reset = block.at("reset"), enable = block.at("enable"), mask = block.at("mask");
                const bool synchronous = block.at("synchronous"), set = block.at("set");
                const auto assignment = [&](const std::string& value) {
                    return "update_word(next.words.at(" + quote(name) + "), " + value + ", Integer(" + quote(mask) + "), " + std::to_string(width) + ");";
                };
                const auto reset_value = "Integer(" + quote(set ? mask : "0") + ')';
                if (!synchronous && reset != "c_zero")
                    source << "    if (" << boolean(reset) << ")\n        " << assignment(reset_value) << "\n    else ";
                else source << "    ";
                source << "if (clock_edge(before, now, " << quote(block.at("clock")) << ", " << (block.at("falling") == true ? "true" : "false") << ')';
                if (enable != "c_one") source << " && " << boolean(enable);
                source << ") {\n";
                const auto data_value = "data_" + identifier(name) + "(state, pads, now)";
                if (synchronous && reset != "c_zero")
                    source << "        if (" << boolean(reset) << ") " << assignment(reset_value) << "\n        else " << assignment(data_value) << '\n';
                else source << "        " << assignment(data_value) << '\n';
                source << "    }\n";
            }
        }
        for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram)
        {
            source << "\n    { // " << ram.key() << ": synchronous 256 x 16 RAM, read-before-write\n"
                   << "        const auto& old = state.memories.at(" << quote(ram.key()) << ");\n"
                   << "        auto& memory = next.memories.at(" << quote(ram.key()) << ");\n";
            for (auto port = ram.value().at("ports").begin(); port != ram.value().at("ports").end(); ++port)
                source << "        const unsigned " << identifier(port.key()) << " = " << call(functions.at(port.value().get<std::string>()), "recovered_data") << ".convert_to<unsigned>();\n";
            source << "        if (clock_edge(before, now, " << quote(ram.value().at("read_clock")) << ") && RE && RCLKE)\n"
                      "            memory.read = old.data[RADDR & 255];\n"
                   << "        if (clock_edge(before, now, " << quote(ram.value().at("write_clock")) << ") && WE && WCLKE) {\n"
                      "            const unsigned enabled = (~MASK) & 65535, address = WADDR & 255;\n"
                      "            memory.data[address] = (old.data[address] & (~enabled & 65535)) | (WDATA & enabled);\n"
                      "        }\n    }\n";
        }
        source << "    next.pads = pads;\n    return next;\n}\n\nint main()\n{\n"
                  "    return run_state_machine(machine_schema, evaluate_controls, apply_event);\n}\n";
        artifacts::write(out / "state_machine.cpp", source.str());
        const auto schema_text = schema.dump(); const auto delimiter = "FSM" + artifacts::sha256(schema_text).substr(0, 12);
        require(schema_text.find(")" + delimiter + '"') == std::string::npos, "state schema delimiter collision");
        artifacts::write(out / "state_schema.h", "#pragma once\ninline const char machine_schema[] = R\"" + delimiter + '(' + schema_text + ")" + delimiter + "\";\n");
        artifacts::write(out / "state_schema.json", schema.dump(2) + '\n');
        artifacts::write(out / "recovery_state.h", resources::state_runtime);
        artifacts::write(out / "recovery_bits.h", resources::bits); artifacts::write(out / "json.hpp", resources::json);
        const auto lifted = Json::parse(artifacts::read(out / "arithmetic" / "arithmetic.json"));
        std::map<std::string, Json> fits;
        for (const auto& row : lifted.at("nodes")) if (row.at("status") == "FIT_PROVED") fits.emplace(row.at("id")[0], row.at("fit"));
        Json operations = Json::array(), classes = Json::object();
        for (const auto& [name, encoded] : fits)
        {
            const auto fit = ArithmeticModel::from_json(encoded); std::string kind = "AFFINE_WORD_UPDATE";
            std::map<std::string, Rational> nonzero; for (const auto& [symbol, coefficient] : fit.coefficients) if (coefficient != 0) nonzero.emplace(symbol, coefficient);
            if (fit.mode != "exact") kind = "QUANTIZED_AFFINE_WORD_UPDATE";
            else if (nonzero.empty()) kind = "CONSTANT_LOAD";
            else if (nonzero.size() == 1 && nonzero.begin()->second == 1 && fit.constant == 0) kind = "WORD_COPY";
            else if (nonzero.size() == 1 && nonzero.begin()->second == 1 && fit.constant.denominator() == 1)
            {
                const auto& symbol = nonzero.begin()->first;
                if (symbol.rfind("state:", 0) == 0 && name.rfind("d_" + symbol.substr(6) + '_', 0) == 0) kind = "MODULAR_SELF_INCREMENT";
            }
            classes[kind] = classes.value(kind, 0u) + 1;
            std::ostringstream formula; formula << "wrap" << fit.width << '(';
            if (fit.mode != "exact") formula << fit.mode << '(';
            formula << fit.constant;
            for (const auto& [symbol, coefficient] : nonzero) formula << " + (" << coefficient << ") * " << (fit.unsigned_inputs.count(symbol) ? "unsigned(" : "signed(") << symbol << ')';
            if (fit.mode != "exact") formula << ')'; formula << ')';
            operations.push_back({{"function", name}, {"kernel", functions.at(name).at("kernel")}, {"class", kind}, {"formula", formula.str()}, {"proved_fit", encoded}});
        }
        artifacts::write(out / "algorithm_summary.json", Json{{"classes", classes}, {"operations", operations},
            {"scope", "Algebraic classification of universally proved functions, not inferred application/protocol roles. Guard applicability is in state_machine.cpp and state_schema.json."}}.dump(2) + '\n');
        unsigned variants = 0, unresolved = 0, unknown_initial_bits = 0;
        std::ostringstream view;
        view << "Generated algorithm view: guarded state transitions\n"
                "Read state_machine.cpp for executable control flow; arithmetic_kernels.h for proved word operations.\n"
                "Exact data/control kernels remain executable where no readable arithmetic was proved.\n"
                "Names are recovered HAL storage identities; application/protocol roles are not inferred.\n"
                "No whole-device observed-period premise. Physical resolved inputs: " << schema.at("pads").size() << " pads.\n\n";
        view << "Proved operation classes: " << classes.dump() << "\n";
        for (const auto& operation : operations)
            view << operation.at("function").get<std::string>() << ": " << operation.at("class").get<std::string>() << ' ' << operation.at("formula").get<std::string>() << '\n';
        view << '\n';
        const auto describe = [&](const std::string& name) {
            const auto& function = functions.at(name);
            view << "recovered_data::kernel_" << function.at("kernel") << '(';
            bool first = true; for (const auto& input : function.at("sources")) { if (!first) view << ", "; first = false; view << input.at("name").get<std::string>(); }
            view << ')';
            if (fits.count(name)) view << "\n      proved arithmetic model: " << fits.at(name).dump();
            else view << " [exact bit-vector expression]";
            view << '\n';
        };
        for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
        {
            const auto& info = word.value(); const unsigned width = info.at("width");
            unresolved += info.at("numeric_order_known") == false;
            const Integer known(info.at("initial_known_mask").get<std::string>());
            for (unsigned bit = 0; bit < width; ++bit) unknown_initial_bits += !boost::multiprecision::bit_test(known, bit);
            view << word.key() << " [" << width << " bits; numeric order " << (info.at("numeric_order_known") == true ? "established" : "unresolved, physical storage retained") << "]\n";
            for (const auto& block : info.at("blocks"))
                view << "  mask " << block.at("mask").get<std::string>() << ": " << (block.at("falling") == true ? "falling" : "rising") << '(' << block.at("clock").get<std::string>()
                     << "), enable " << block.at("enable").get<std::string>() << ", " << (block.at("synchronous") == true ? "synchronous " : "asynchronous ")
                     << (block.at("set") == true ? "set " : "reset ") << block.at("reset").get<std::string>() << "; otherwise hold\n";
            for (const auto& variant : info.at("variants")) { ++variants; view << "  when " << variant.at("when").dump() << ": "; describe(variant.at("function")); }
            view << "  otherwise: "; describe(info.at("fallback")); view << '\n';
        }
        for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram)
            view << ram.key() << " [256 x 16 RAM, read-before-write, mask 1 preserves old bit]\n  read clock " << ram.value().at("read_clock").get<std::string>()
                 << "; write clock " << ram.value().at("write_clock").get<std::string>() << "\n  ports " << ram.value().at("ports").dump() << "\n\n";
        view << "Scope: " << schema.at("scope").get<std::string>() << '\n';
        artifacts::write(out / "algorithm_view.txt", view.str());
        Json files = Json::object();
        for (const auto* name : {"state_machine.cpp", "state_schema.h", "state_schema.json", "arithmetic_kernels.h", "exact_data_kernels.h", "control_kernels.h", "recovery_state.h", "recovery_bits.h", "json.hpp", "algorithm_view.txt", "algorithm_summary.json"})
            files[name] = artifacts::file_hash(out / name);
        for (const auto& copy : copied)
        {
            const auto saved = artifacts::read(out / copy.at("file").get<std::string>());
            require(artifacts::sha256(saved.substr(copy.at("offset").get<size_t>(), copy.at("length").get<size_t>())) == copy.at("sha256"), "saved state kernel body differs");
        }
        Json report{{"status", "GUARDED_STATE_PROGRAM_EXPORTED"}, {"execution_kind", "GUARDED_EVENT_TRANSITIONS"}, {"FF_bits", schema.at("FF_bits")},
            {"state_words", schema.at("words").size()}, {"RAM_instances", schema.at("memories").size()}, {"output_pads", schema.at("outputs").size()},
            {"data_functions", schema.at("data_functions")}, {"control_functions", schema.at("control_functions")}, {"max_delta_events", max_delta_events},
            {"guarded_variants", variants}, {"arithmetic_functions", fits.size()}, {"unresolved_order_groups_retained", unresolved}, {"unknown_initial_FF_bits", unknown_initial_bits},
            {"files", files}, {"copied_kernels", copied},
            {"data_manifest_sha256", artifacts::file_hash(out / "data" / "program.json")},
            {"control_manifest_sha256", artifacts::file_hash(out / "controls" / "program.json")}, {"scope", schema.at("scope")}};
        artifacts::write(out / "state_program.json", report.dump(2) + '\n'); return report;
    }
}
