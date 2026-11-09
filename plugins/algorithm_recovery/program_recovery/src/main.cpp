// recover-program: certified event graph -> untimed transaction programs (flat, rolled, lifted) and folded RTL.
// The generated programs are Python; running and checking them is done by CPython scripts in
// algorithm_recovery/evaluation/program_checks (validate_programs.py, close_chain.py).
#include "program_report.h"
#include <filesystem>
#include <fstream>
#include <iostream>

namespace program_recovery { int rtl_main(int argc, char** argv); }

namespace
{
    program_recovery::Json load_json(const std::string& path)
    {
        std::ifstream in(path);
        program_recovery::require(bool(in), "cannot read " + path);
        return program_recovery::Json::parse(in);
    }

    void write_text(const std::filesystem::path& path, const std::string& text)
    {
        std::ofstream out(path, std::ios::binary);
        out << text;
        program_recovery::require(bool(out), "write failed " + path.string());
    }

    int usage()
    {
        std::cerr << "usage: recover-program generate GRAPH_JSON CONTRACT_JSON OBSERVE_JSON CASE OUT_DIR\n"
                     "       recover-program rtl GRAPH_JSON OUT_DIR TRACE_DIR [TRACE_DIR ...]\n";
        return 2;
    }

    int generate_main(int argc, char** argv)
    {
        if (argc != 7) return usage();
        const std::string graph = argv[2], contract = argv[3], observe = argv[4], case_name = argv[5], out_arg = argv[6];
        const std::filesystem::path out = out_arg;
        program_recovery::require(!std::filesystem::exists(out), "output directory exists: " + out.string());
        std::filesystem::create_directories(out);
        z3::context ctx;
        program_recovery::EventGraph model(ctx, load_json(graph), load_json(contract), load_json(observe), case_name);
        program_recovery::ProgramBuilder dec(model);
        write_text(out / "program_flat.py", dec.program_flat);
        write_text(out / "program_rolled.py", dec.program_rolled);
        program_recovery::ProgramLifter lifter(dec, out_arg);
        write_text(out / "program.py", lifter.program);
        write_text(out / "interface.json", lifter.interface_json.dump(1));
        const program_recovery::PJ report = program_recovery::program_report(model, dec, lifter);
        write_text(out / "report.json", report.dump(1));
        std::cout << report.dump(1) << '\n';
        return 0;
    }
}

int main(int argc, char** argv)
{
    try
    {
        if (argc < 2) return usage();
        const std::string cmd = argv[1];
        if (cmd == "generate") return generate_main(argc, argv);
        if (cmd == "rtl") return program_recovery::rtl_main(argc, argv);
        return usage();
    }
    catch (const std::exception& e)
    {
        std::cerr << "error: " << e.what() << '\n';
        return 1;
    }
}
