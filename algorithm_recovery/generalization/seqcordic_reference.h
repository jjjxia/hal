#pragma once

#include <cstdint>

// Evaluation-only transaction reference. Never an input to recovery.
// Source: ZipCPU/cordic, commit 8ede08a1865d87a357a3a2c188206e843dd9bde6,
// rtl/seqcordic.v:83-84 (input expansion), 124-182 (octants), 198-213
// (angle constants), 253-289 (iteration), 298-323 (rounding/output).
// bench/cpp/cordic_tb.cpp:146-158 and rtl/seqcordic.h:49 specify 17 edges.
//
// Contract: after reset, launch one request with signed 13-bit x/y and raw
// unsigned 20-bit phase, then keep stb low for the following 16 rising edges.
// The output at edge 16 (launch is edge 0) captures OLD xv/yv while the RTL
// computes shift 15. Therefore the observable result includes shifts 1..14,
// not all 16 iterations suggested by the generated header's NSTAGES value.
// This reference concerns valid transaction outputs only, not idle/reset state.
// The input arguments are masked exactly as the physical input ports are.
namespace netnotary_benchmark {

struct SeqCordicResult {
    std::uint16_t x; // Raw 13-bit output; bit 12 is the sign bit.
    std::uint16_t y;
    // Bit j-1 records the phase sign before iteration j, for j in [1,14].
    // OR masks over requests to measure negative coverage; OR (~mask & 0x3fff)
    // to measure positive coverage. This is scoring information only.
    std::uint16_t negative_phase_mask;
};

inline std::uint32_t seqcordic_asr16(std::uint32_t bits, unsigned shift) {
    bits &= 0xffffu;
    if (shift == 0) return bits;
    if (shift >= 16) return (bits & 0x8000u) ? 0xffffu : 0u;
    const std::uint32_t fill = (bits & 0x8000u)
        ? (0xffffu << (16u - shift)) : 0u;
    return ((bits >> shift) | fill) & 0xffffu;
}

inline SeqCordicResult seqcordic_reference(std::int32_t input_x,
                                         std::int32_t input_y,
                                         std::uint32_t input_phase) {
    constexpr std::uint32_t angles[14] = {
        0x12e40u, 0x09fb3u, 0x05111u, 0x028b0u, 0x0145du, 0x00a2fu,
        0x00517u, 0x0028bu, 0x00145u, 0x000a2u, 0x00051u, 0x00028u,
        0x00014u, 0x0000au
    };
    const auto expand = [](std::int32_t value) -> std::uint32_t {
        const std::uint32_t raw = static_cast<std::uint32_t>(value) & 0x1fffu;
        return (raw << 2) | ((raw & 0x1000u) ? 0x8000u : 0u);
    };
    const std::uint32_t ex = expand(input_x), ey = expand(input_y);
    std::uint32_t x = ex, y = ey, phase = input_phase & 0xfffffu;
    switch (phase >> 17) {
    case 1: case 2:
        x = (0u - ey) & 0xffffu; y = ex;
        phase = (phase - 0x40000u) & 0xfffffu;
        break;
    case 3: case 4:
        x = (0u - ex) & 0xffffu; y = (0u - ey) & 0xffffu;
        phase = (phase - 0x80000u) & 0xfffffu;
        break;
    case 5: case 6:
        x = ey; y = (0u - ex) & 0xffffu;
        phase = (phase - 0xc0000u) & 0xfffffu;
        break;
    default: break;
    }
    std::uint16_t negative_mask = 0;
    for (unsigned j = 1; j <= 14; ++j) {
        const std::uint32_t sx = seqcordic_asr16(x, j);
        const std::uint32_t sy = seqcordic_asr16(y, j);
        if (phase & 0x80000u) {
            negative_mask |= static_cast<std::uint16_t>(1u << (j - 1));
            x = (x + sy) & 0xffffu;
            y = (y - sx) & 0xffffu;
            phase = (phase + angles[j - 1]) & 0xfffffu;
        } else {
            x = (x - sy) & 0xffffu;
            y = (y + sx) & 0xffffu;
            phase = (phase - angles[j - 1]) & 0xfffffu;
        }
    }
    const auto rounded_output = [](std::uint32_t bits) -> std::uint16_t {
        const std::uint32_t bias = (bits & 8u) ? 4u : 3u;
        return static_cast<std::uint16_t>(((bits + bias) & 0xffffu) >> 3);
    };
    return {rounded_output(x), rounded_output(y), negative_mask};
}

} // namespace netnotary_benchmark
