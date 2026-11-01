// Produce native expected outputs and a testbench for the independently supplied
// Yosys primitive. No Python is used by the implementation or this regression.
#include "algorithm_recovery/memory.h"
#include <bitset>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>

using namespace hal::algorithm_recovery;

int main(int argc, char** argv)
{
    if (argc != 2)
        return 2;
    const std::filesystem::path root(argv[1]);
    std::filesystem::create_directories(root);
    std::mt19937 rng(20261101);
    RamState state;
    for (auto& word : state.memory)
        word = rng() & 65535;
    std::array<RamState, 3> mutants = {state, state, state};
    std::ofstream tb(root / "test.v"), expected(root / "expected.txt");
    std::array<std::ofstream, 3> bad;
    for (unsigned i = 0; i < bad.size(); ++i)
        bad[i].open(root / ("mutant" + std::to_string(i) + ".txt"));
    tb << "module test;\nreg RCLK=0,WCLK=0,RE=0,WE=0,RCLKE=0,WCLKE=0;\n"
          "reg [10:0] RADDR=0,WADDR=0;\nreg [15:0] WDATA=0,MASK=0;\n"
          "wire [15:0] RDATA; integer fd,i;\nSB_RAM40_4K #(\n";
    for (unsigned block = 0; block < 16; ++block)
    {
        std::ostringstream init;
        init << std::hex << std::setfill('0');
        for (int word = 15; word >= 0; --word)
            init << std::setw(4) << state.memory[16 * block + word];
        tb << ".INIT_" << "0123456789ABCDEF"[block] << "(256'h" << init.str() << ")" << (block == 15 ? "\n" : ",\n");
    }
    // A fixed relative output path avoids embedding/escaping arbitrary paths in Verilog.
    tb << ") dut(.*);\ninitial begin\nfd=$fopen(\"observed.txt\",\"w\");\n";
    for (unsigned event = 0; event < 512; ++event)
    {
        RamPorts p;
        // Include ignored high address bits as well as collisions and all masks.
        p.read_address = rng() & 2047;
        p.write_address = event % 3 == 0 ? p.read_address : rng() & 2047;
        p.write_data = rng() & 65535;
        const u16 masks[] = {0, 65535, 0x5555, 0xaaaa, static_cast<u16>(rng())};
        p.mask = masks[event % 5];
        p.read_enable = event % 7 != 1;
        p.read_clock_enable = event % 11 != 1;
        p.write_enable = event % 9 != 1;
        p.write_clock_enable = event % 13 != 1;
        const bool re = event % 4 != 1, we = event % 4 != 2;
        if (event == 0)
        {
            p.read_address = p.write_address = 0;
            p.write_data = state.memory[0] ^ 65535;
        }
        tb << "RCLK=0;WCLK=0;#1;\nRADDR=" << p.read_address << ";WADDR=" << p.write_address
           << ";WDATA=" << p.write_data << ";MASK=" << p.mask << ";RE=" << p.read_enable
           << ";RCLKE=" << p.read_clock_enable << ";WE=" << p.write_enable << ";WCLKE=" << p.write_clock_enable
           << ";#1;RCLK=" << re << ";WCLK=" << we << ";#1;\n$fwrite(fd,\"" << event
           << " %b %b\\n\",RDATA,dut.memory[WADDR[7:0]]);\n";
        step_ram(state, p, re, we);
        auto row = [&](std::ostream& out, const RamState& s) {
            out << event << ' ' << std::bitset<16>(s.read) << ' ' << std::bitset<16>(s.memory[p.write_address & 255]) << '\n';
        };
        row(expected, state);
        for (unsigned i = 0; i < mutants.size(); ++i)
        {
            auto altered = p;
            if (i == 0)
                altered.mask = 0;
            if (i == 1)
                altered.read_enable = altered.read_clock_enable = true;
            step_ram(mutants[i], altered, re, we);
            if (i == 2 && re && p.read_enable && p.read_clock_enable)
                mutants[i].read = mutants[i].memory[p.read_address & 255];
            row(bad[i], mutants[i]);
        }
    }
    tb << "for(i=0;i<256;i=i+1) $fwrite(fd,\"final %0d %b\\n\",i,dut.memory[i]);\n"
          "$fclose(fd);$finish;end endmodule\n";
    for (unsigned address = 0; address < 256; ++address)
    {
        expected << "final " << address << ' ' << std::bitset<16>(state.memory[address]) << '\n';
        for (unsigned i = 0; i < mutants.size(); ++i)
            bad[i] << "final " << address << ' ' << std::bitset<16>(mutants[i].memory[address]) << '\n';
    }
    if (!tb || !expected || !bad[0] || !bad[1] || !bad[2])
        return 1;
    std::cout << "512 events, 20480 bits; read-before-write/mask/read-hold negative controls\n";
}
