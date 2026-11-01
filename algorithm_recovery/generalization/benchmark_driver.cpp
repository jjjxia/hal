// Evaluation preparation/scoring only. No grouping, order or algorithm hints
// from this program are supplied to the recovery implementation.
#include "seqcordic_reference.h"
#include <nlohmann/json.hpp>
#include <sodium.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <random>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Json = nlohmann::json;
namespace fs = std::filesystem;
static void need(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
static std::string read(const fs::path& path) {
    std::ifstream f(path, std::ios::binary); need(bool(f), "cannot read " + path.string());
    std::ostringstream s; s << f.rdbuf(); return s.str();
}
static void write(const fs::path& path, const std::string& data) {
    std::ofstream f(path, std::ios::binary); need(bool(f), "cannot write " + path.string());
    f << data; need(bool(f), "write failed " + path.string());
}
static std::string sha256(const std::string& data) {
    unsigned char digest[crypto_hash_sha256_BYTES]; char hex[2 * crypto_hash_sha256_BYTES + 1];
    crypto_hash_sha256(digest, reinterpret_cast<const unsigned char*>(data.data()), data.size());
    sodium_bin2hex(hex, sizeof(hex), digest, sizeof(digest)); return hex;
}
static bool identifier(const std::string& s) {
    return std::regex_match(s, std::regex("[A-Za-z_][A-Za-z0-9_]*"));
}
static std::string vlstr(const std::string& s) { return Json(s).dump(); }
static std::string bitexpr(const Json& bit) {
    if (bit.is_number_integer()) return "n_" + std::to_string(bit.get<unsigned>());
    need(bit.is_string(), "invalid connection bit");
    auto v = bit.get<std::string>();
    need(v.size() == 1 && std::string("01xz").find(v) != std::string::npos, "invalid constant bit");
    return "1'b" + v;
}
static std::string bus(const Json& bits) {
    need(bits.is_array() && !bits.empty(), "empty connection");
    if (bits.size() == 1) return bitexpr(bits[0]);
    std::string s = "{";
    for (size_t j = bits.size(); j-- > 0;) { if (j + 1 != bits.size()) s += ","; s += bitexpr(bits[j]); }
    return s + "}";
}
static std::string parameter(const Json& value) {
    need(value.is_string(), "mapped primitive parameter must be Yosys binary string");
    auto s = value.get<std::string>();
    need(!s.empty() && s.find_first_not_of("01xz") == std::string::npos, "nonbinary primitive parameter");
    return std::to_string(s.size()) + "'b" + s;
}
struct PortBit { std::string name, direction, anonymous; unsigned offset; Json bit; int packed; };

static void prepare(const std::string& name, const fs::path& input, const fs::path& directory,
                    const fs::path& coefficients) {
    need(name == "slowfil_srl" || name == "seqcordic", "unknown case");
    fs::create_directories(directory);
    const fs::path out = fs::absolute(directory);
    need(!fs::exists(out / "recovered.json"), "refusing to overwrite prepared benchmark");
    auto raw_text = read(input); auto raw = Json::parse(raw_text);
    const auto& original = raw.at("modules").at(name);
    Json module = {{"ports", Json::object()}, {"cells", Json::object()}, {"netnames", Json::object()}};
    Json metadata = {{"case", name}, {"raw_json_sha256", sha256(raw_text)}, {"port_bits", Json::array()},
                     {"cell_map", Json::object()}, {"expected_outputs", Json::array()}};
    std::vector<PortBit> ports; std::set<unsigned> nets, inputs;
    unsigned pindex = 0, out_count = 0;
    for (const auto& item : original.at("ports").items()) {
        need(identifier(item.key()), "unsupported original port identifier");
        const std::string direction = item.value().at("direction");
        need(direction == "input" || direction == "output", "inout/unsupported port");
        unsigned offset = 0;
        for (const auto& bit : item.value().at("bits")) {
            if (bit.is_number_integer()) nets.insert(bit.get<unsigned>()); else bitexpr(bit);
            if (direction == "input") {
                need(bit.is_number_integer(), "constant input");
                need(inputs.insert(bit.get<unsigned>()).second, "aliased input");
            }
            PortBit p{item.key(), direction, "p_" + std::to_string(pindex++), offset++, bit, -1};
            if (direction == "output") p.packed = static_cast<int>(out_count++);
            module["ports"][p.anonymous] = {{"direction", direction}, {"bits", Json::array({bit})}};
            ports.push_back(p);
        }
    }
    need(inputs.size() > 0 && inputs.size() <= 64 && out_count > 0 && out_count <= 64, "unsupported testbench port width");
    unsigned cindex = 0;
    for (const auto& item : original.at("cells").items()) {
        auto cell = item.value(); const std::string type = cell.at("type");
        need(type == "SB_LUT4" || type == "SB_CARRY" ||
             std::regex_match(type, std::regex("SB_DFF(N)?(E)?(R|S|SR|SS)?")), "unsupported primitive " + type);
        if (!cell.contains("parameters")) cell["parameters"] = Json::object();
        for (const auto& v : cell.at("parameters").items()) { need(identifier(v.key()), "parameter identifier"); parameter(v.value()); }
        for (const auto& pin : cell.at("connections").items()) {
            need(identifier(pin.key()), "cell pin identifier");
            for (const auto& bit : pin.value()) {
                if (bit.is_number_integer()) nets.insert(bit.get<unsigned>()); else bitexpr(bit);
            }
        }
        // Remove synthesis provenance/name metadata; keep all functional fields.
        cell.erase("attributes"); cell.erase("hide_name");
        const std::string anon = "g_" + std::to_string(cindex++);
        metadata["cell_map"][anon] = item.key(); module["cells"][anon] = cell;
    }
    for (const auto& item : original.at("netnames").items())
        for (const auto& bit : item.value().at("bits")) if (bit.is_number_integer()) nets.insert(bit.get<unsigned>());
    for (unsigned n : nets) module["netnames"]["n_" + std::to_string(n)] = {{"bits", Json::array({n})}};
    // Preserve any bit-level initialization attribute without original bus names.
    for (const auto& item : original.at("netnames").items()) {
        const auto& net = item.value();
        if (!net.contains("attributes") || !net.at("attributes").contains("init")) continue;
        const auto init = net.at("attributes").at("init").get<std::string>();
        need(init.size() == net.at("bits").size(), "init width mismatch");
        for (size_t j = 0; j < init.size(); ++j) if (net.at("bits")[j].is_number_integer()) {
            auto& n = module["netnames"]["n_" + std::to_string(net.at("bits")[j].get<unsigned>())];
            const std::string v(1, init[init.size() - 1 - j]);
            if (n.contains("attributes")) need(n["attributes"]["init"] == v, "conflicting aliased init");
            n["attributes"] = {{"init", v}};
        }
    }
    const std::string normalized = Json({{"modules", {{"recovered", module}}}}).dump(2) + "\n";
    write(out / "recovered.json", normalized);
    metadata["source_sha256"] = sha256(normalized);
    std::ostringstream v; v << "module recovered(";
    for (size_t i = 0; i < ports.size(); ++i) { if (i) v << ","; v << ports[i].anonymous; }
    v << ");\n";
    for (const auto& p : ports) v << p.direction << " wire " << p.anonymous << ";\n";
    for (unsigned n : nets) v << "wire n_" << n << ";\n";
    for (const auto& p : ports) {
        if (p.direction == "input") v << "assign " << bitexpr(p.bit) << "=" << p.anonymous << ";\n";
        else v << "assign " << p.anonymous << "=" << bitexpr(p.bit) << ";\n";
    }
    for (const auto& item : module["cells"].items()) {
        const auto& cell = item.value(); v << cell.at("type").get<std::string>();
        if (!cell["parameters"].empty()) {
            v << " #("; bool first = true;
            for (const auto& par : cell["parameters"].items()) { if (!first) v << ","; first = false; v << "." << par.key() << "(" << parameter(par.value()) << ")"; }
            v << ")";
        }
        v << " " << item.key() << " ("; bool first = true;
        for (const auto& pin : cell.at("connections").items()) { if (!first) v << ","; first = false; v << "." << pin.key() << "(" << bus(pin.value()) << ")"; }
        v << ");\n";
    }
    v << "endmodule\n"; write(out / "recovered.v", v.str());

    std::vector<unsigned> input_order(inputs.begin(), inputs.end());
    std::map<unsigned, unsigned> input_position;
    for (unsigned i = 0; i < input_order.size(); ++i) input_position[input_order[i]] = i;
    for (auto& p : ports) {
        if (p.direction == "input") p.packed = input_position.at(p.bit.get<unsigned>());
        metadata["port_bits"].push_back({{"name", p.name}, {"offset", p.offset}, {"direction", p.direction},
                                       {"physical_bit", p.bit}, {"anonymous_port", p.anonymous}, {"packed_index", p.packed}});
    }
    need(original["ports"].contains("i_clk") && original["ports"].at("i_clk").at("bits").size() == 1, "missing clock");
    const auto clock_bit = original["ports"]["i_clk"]["bits"][0].get<unsigned>();
    std::map<std::string, std::uint64_t> values;
    for (const auto& port : original["ports"].items()) if (port.value()["direction"] == "input") values[port.key()] = 0;
    Json events = Json::array(); std::ostringstream stimulus_hex;
    auto cycle = [&]() {
        for (unsigned high = 0; high < 2; ++high) {
            values.at("i_clk") = high; std::uint64_t packed = 0; Json row = Json::array();
            for (const auto& p : ports) if (p.direction == "input")
                packed |= ((values.at(p.name) >> p.offset) & 1u) << static_cast<unsigned>(p.packed);
            for (unsigned i = 0; i < input_order.size(); ++i) row.push_back(static_cast<int>((packed >> i) & 1u));
            events.push_back(row); stimulus_hex << std::hex << packed << '\n';
        }
    };
    values.at("i_reset") = 1; for (unsigned k = 0; k < 4; ++k) cycle(); values.at("i_reset") = 0;
    if (name == "slowfil_srl") {
        values.at("i_ce") = 1; for (unsigned k = 0; k < 8; ++k) cycle();
        values.at("i_ce") = 0; for (unsigned k = 0; k < 16; ++k) cycle();
    }
    const unsigned first_event = events.size(), period = name == "slowfil_srl" ? 16 : 17;
    std::mt19937 rng(20261101); std::array<int, 7> history{};
    const std::array<int, 7> taps{{3, -5, 7, 11, -13, 17, 19}};
    std::uint16_t neg_train = 0, pos_train = 0, neg_hold = 0, pos_hold = 0;
    const unsigned transactions = name == "slowfil_srl" ? 129 : 128;
    std::int64_t prior_fir = 0;
    for (unsigned n = 0; n < transactions; ++n) {
        Json expected = Json::object(); unsigned output_offset;
        if (name == "slowfil_srl") {
            int sample = 0;
            if (n < 32) { const int a[] = {127, 0, 0, 0, 0, 0, 0, 0, -128, 0, 1, -1, 85, -85, 126, -127}; sample = a[n % 16]; }
            else if (n < 128) sample = static_cast<int>(rng() & 255u) - 128;
            values.at("i_sample") = static_cast<std::uint64_t>(sample) & 255u;
            expected["o_result"] = static_cast<std::uint64_t>(prior_fir) & ((1u << 19) - 1u);
            expected["o_ce"] = 1; output_offset = 3;
            for (unsigned j = 6; j > 0; --j) { history[j] = history[j - 1]; }
            history[0] = sample;
            prior_fir = 0; for (unsigned j = 0; j < 7; ++j) prior_fir += std::int64_t(taps[j]) * history[j];
        } else {
            const int xs[] = {4095, -4096, 0, 0, 1024, -1024, 1, -4096};
            const int ys[] = {0, 0, 4095, -4096, 1024, 1536, -1, 4095};
            const unsigned offsets[] = {0, 1, 65535, 131071};
            const unsigned index = n < 32 ? n : (n * 5) % 32;
            int x = xs[n % 8], y = ys[(n + (n >= 32 ? 3 : 0)) % 8];
            unsigned phase = (index / 4) * (1u << 17) + offsets[index % 4];
            if (n >= 64) { x = static_cast<int>(rng() & 8191u) - 4096; y = static_cast<int>(rng() & 8191u) - 4096; phase = rng() & 0xfffffu; }
            values.at("i_xval") = static_cast<std::uint64_t>(x) & 8191u;
            values.at("i_yval") = static_cast<std::uint64_t>(y) & 8191u;
            values.at("i_phase") = phase; values.at("i_aux") = n & 1u;
            auto result = netnotary_benchmark::seqcordic_reference(x, y, phase);
            auto& neg = n < 64 ? neg_train : neg_hold; auto& pos = n < 64 ? pos_train : pos_hold;
            neg |= result.negative_phase_mask; pos |= static_cast<std::uint16_t>((~result.negative_phase_mask) & 0x3fffu);
            expected = {{"o_xval", result.x}, {"o_yval", result.y}, {"o_aux", n & 1u}, {"o_done", 1}, {"o_busy", 0}};
            output_offset = 16;
        }
        const unsigned begin = events.size();
        // FIR reports the PREVIOUS sample, so classify by its producer, not
        // by the triggering transaction. The drain returns heldout sample127.
        const int producer = name == "slowfil_srl" ? static_cast<int>(n) - 1 : static_cast<int>(n);
        metadata["expected_outputs"].push_back({{"transaction", n}, {"producer_sample", producer},
            {"event", begin + 2 * output_offset + 1},
            {"split", producer < 0 ? "prefix" : (producer < 64 ? "training" : "heldout")}, {"values", expected}});
        for (unsigned k = 0; k < period; ++k) {
            values.at(name == "slowfil_srl" ? "i_ce" : "i_stb") = k == 0 ? 1 : 0; cycle();
        }
    }
    Json window = Json::array(); const unsigned training_events = 64 * period * 2;
    for (unsigned i = first_event; i < first_event + training_events; ++i) window.push_back(i);
    Json stimulus = {{"schema", "PHYSICAL_PAD_STIMULUS_V1"}, {"source_sha256", sha256(normalized)},
        {"input_bits", input_order}, {"events", events}, {"windows", {{"training", window}}},
        {"description", "External protocol only; original port names used solely by evaluation adapter. First 64 transactions train; remaining 64 are excluded from extraction. Explicit prefix and optional FIR drain retained."}};
    auto stimtext = stimulus.dump(2) + "\n"; write(out / "stimulus.json", stimtext); write(out / "stimulus.hex", stimulus_hex.str());
    metadata["stimulus_sha256"] = sha256(stimtext); metadata["events"] = events.size(); metadata["training_events"] = training_events;
    metadata["training_begin"] = first_event; metadata["training_end_exclusive"] = first_event + training_events;
    metadata["heldout_begin"] = first_event + training_events; metadata["heldout_end_exclusive"] = first_event + 128 * period * 2;
    metadata["period_cycles"] = period; metadata["transaction_count"] = 128; metadata["drain_transactions"] = transactions - 128;
    metadata["prefix_cycles"] = first_event / 2; metadata["input_bits"] = input_order; metadata["output_width"] = out_count;
    metadata["clock_physical_bit"] = clock_bit;
    metadata["phase_sign_coverage"] = {{"training_negative", neg_train}, {"training_positive", pos_train}, {"heldout_negative", neg_hold}, {"heldout_positive", pos_hold}};
    metadata["reference_scope"] = name == "slowfil_srl"
        ? "Signed seven-tap sum; CE at sample edge +3 reports prior sample sum. Reset4, zero-CE flush8, idle16; one sample/16 clocks. Last zero transaction drains sample127. No searched alignment."
        : "Original source-derived bit-vector transaction reference, shifts1..14 and original rounding; launch plus16 edges. No ideal trig substitution.";
    if (name == "slowfil_srl") { metadata["taps"] = taps; metadata["coefficients_sha256"] = sha256(read(coefficients)); }

    std::ostringstream tb; tb << "`timescale 1ns/1ps\nmodule test;\n";
    for (const auto& item : original["ports"].items()) {
        const auto width = item.value().at("bits").size();
        tb << (item.value()["direction"] == "input" ? "reg" : "wire") << " [" << width - 1 << ":0] r_" << item.key() << ";\n";
    }
    for (const auto& p : ports) if (p.direction == "output") tb << "wire d_" << p.anonymous << ";\n";
    tb << name;
    if (name == "slowfil_srl") tb << " #(.LGNTAPS(3),.NTAPS(7),.IW(8),.TW(8),.OW(19),.FIXED_TAPS(1),.INITIAL_COEFFS(" << vlstr(fs::absolute(coefficients).string()) << "))";
    tb << " reference ("; bool first = true;
    for (const auto& item : original["ports"].items()) { if (!first) tb << ","; first = false; tb << "." << item.key() << "(r_" << item.key() << ")"; }
    tb << ");\nrecovered dut ("; first = true;
    for (const auto& p : ports) {
        if (!first) { tb << ","; }
        first = false;
        tb << "." << p.anonymous << "(";
        if (p.direction == "input") tb << "r_" << p.name << "[" << p.offset << "]"; else tb << "d_" << p.anonymous;
        tb << ")";
    }
    tb << ");\nwire [" << out_count - 1 << ":0] reference_output,dut_output;\n";
    for (const auto& p : ports) if (p.direction == "output")
        tb << "assign reference_output[" << p.packed << "]=r_" << p.name << "[" << p.offset << "]; assign dut_output[" << p.packed << "]=d_" << p.anonymous << ";\n";
    tb << "reg [" << input_order.size() - 1 << ":0] stimulus[0:" << events.size() - 1 << "];\n"
       << "integer i,k,fd,receipt; integer known_count=0,unknown_count=0;\n"
       << "task compare_outputs; begin for(k=0;k<" << out_count << ";k=k+1) begin\n"
       << "if(reference_output[k]===1'b0 || reference_output[k]===1'b1) begin known_count=known_count+1;\n"
       << "if(reference_output[k]!==dut_output[k]) $fatal(1,\"RTL/gate mismatch event %0d bit %0d\",i,k);\n"
       << "end else unknown_count=unknown_count+1; end end endtask\ninitial begin\n";
    for (const auto& item : original["ports"].items()) if (item.value()["direction"] == "input") tb << "r_" << item.key() << "=0;\n";
    tb << "$readmemh(" << vlstr((out / "stimulus.hex").string()) << ",stimulus);\n"
       << "fd=$fopen(" << vlstr((out / "reference_outputs.txt").string()) << ",\"w\");\n"
       << "for(i=0;i<" << events.size() << ";i=i+1) begin\n";
    for (const auto& p : ports) if (p.direction == "input" && p.name != "i_clk") tb << "r_" << p.name << "[" << p.offset << "]=stimulus[i][" << p.packed << "];\n";
    tb << "#1; compare_outputs;\nr_i_clk[0]=stimulus[i][" << input_position.at(clock_bit) << "];\n#1; compare_outputs;\n"
       << "$fwrite(fd,\"%0d %b %b\\n\",i,reference_output,dut_output);\nend\n$fclose(fd);\n"
       << "receipt=$fopen(" << vlstr((out / "reference_equivalence.json").string()) << ",\"w\");\n";
    const std::string receipt_format = "{\"status\":\"RTL_ANONYMOUS_GATE_PASS\",\"events\":"
        + std::to_string(events.size())
        + ",\"known_output_observations\":%0d,\"unknown_output_observations\":%0d}\n";
    tb << "$fwrite(receipt," << vlstr(receipt_format) << ",known_count,unknown_count);\n"
       << "$fclose(receipt); $finish;\nend\nendmodule\n";
    write(out / "reference_tb.v", tb.str());
    metadata["reference_tb_sha256"] = sha256(tb.str());
    write(out / "evaluation_contract.json", metadata.dump(2) + "\n");
    std::cout << Json({{"case", name}, {"events", events.size()}, {"training_events", training_events}, {"status", "PREPARED_NOT_RUN"}}).dump() << '\n';
}

static void score(const std::string& name, const fs::path& directory) {
    const auto out = fs::absolute(directory); const auto c = Json::parse(read(out / "evaluation_contract.json"));
    need(c.at("case") == name, "case mismatch");
    need(c.at("source_sha256") == sha256(read(out / "recovered.json")), "changed netlist");
    need(c.at("stimulus_sha256") == sha256(read(out / "stimulus.json")), "changed stimulus");
    const auto receipt = Json::parse(read(out / "reference_equivalence.json"));
    need(receipt.at("status") == "RTL_ANONYMOUS_GATE_PASS", "reference gate check failed");
    std::ifstream f(out / "reference_outputs.txt"); need(bool(f), "missing output capture");
    std::vector<std::string> observed; unsigned event; std::string reference, gate;
    while (f >> event >> reference >> gate) {
        need(event == observed.size(), "nonconsecutive output capture");
        need(reference.size() == c.at("output_width").get<size_t>() && gate.size() == reference.size(), "output capture width");
        for (size_t i = 0; i < reference.size(); ++i) if (reference[i] == '0' || reference[i] == '1') need(reference[i] == gate[i], "captured gate mismatch");
        observed.push_back(reference);
    }
    need(observed.size() == c.at("events").get<size_t>(), "incomplete output capture");
    unsigned checked = 0, checked_bits = 0; Json counts = Json::object();
    for (const auto& item : c.at("expected_outputs")) {
        const auto e = item.at("event").get<unsigned>(); const auto split = item.at("split").get<std::string>();
        for (const auto& p : c.at("port_bits")) if (p.at("direction") == "output") {
            const auto port = p.at("name").get<std::string>();
            if (!item.at("values").contains(port)) continue;
            const unsigned index = p.at("packed_index"), offset = p.at("offset");
            const char actual = observed.at(e).at(observed.at(e).size() - 1 - index);
            const auto expected = (item.at("values").at(port).get<std::uint64_t>() >> offset) & 1u;
            need(actual == char('0' + expected), "functional mismatch at event " + std::to_string(e) + " port " + port + " bit " + std::to_string(offset));
            ++checked_bits;
        }
        counts[split] = counts.value(split, 0u) + 1; ++checked;
    }
    Json result = {{"status", "ORIGINAL_RTL_GATE_FUNCTIONAL_REFERENCE_PASS"}, {"case", name}, {"transactions", checked},
        {"transaction_counts", counts}, {"checked_output_bits", checked_bits}, {"source_sha256", c.at("source_sha256")},
        {"stimulus_sha256", c.at("stimulus_sha256")}, {"reference_scope", c.at("reference_scope")},
        {"phase_sign_coverage", c.at("phase_sign_coverage")},
        {"scope", "Evaluation-side RTL/gate and transaction-reference checks only. Not a recovery result or universal certificate."}};
    write(out / "score.json", result.dump(2) + "\n"); std::cout << result.dump() << '\n';
}

int main(int argc, char** argv) {
    try {
        need(sodium_init() >= 0, "sodium initialization failed");
        need(argc >= 4, "usage: benchmark_driver prepare CASE RAW_JSON OUT_DIR [coeffs_path] | score CASE OUT_DIR");
        const std::string mode = argv[1];
        if (mode == "prepare") {
            need(argc == 5 || argc == 6, "prepare argument count");
            prepare(argv[2], argv[3], argv[4], argc == 6 ? fs::path(argv[5]) : fs::path(argv[4]) / "taps.hex");
        } else { need(mode == "score" && argc == 4, "unknown command"); score(argv[2], argv[3]); }
        return 0;
    } catch (const std::exception& e) { std::cerr << "benchmark_driver: " << e.what() << '\n'; return 1; }
}
