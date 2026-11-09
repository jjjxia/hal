// Bit-exact replica of CPython's random.Random(int seed).getrandbits(k) (MT19937, init_by_array).
#pragma once
#include "python_format.h"
#include <array>
#include <cstdint>

namespace program_recovery
{
    class PyRandom
    {
    public:
        explicit PyRandom(const BigInt& seed)
        {
            BigInt n = seed < 0 ? BigInt(-seed) : seed;
            std::vector<uint32_t> key;
            if (n == 0) key.push_back(0);
            while (n > 0) { key.push_back(static_cast<uint32_t>(n & 0xffffffffu)); n >>= 32; }
            init_by_array(key);
        }

        BigInt getrandbits(unsigned k)
        {
            if (k == 0) return 0;
            if (k <= 32) return BigInt(genrand_uint32() >> (32 - k));
            BigInt result = 0;
            unsigned shift = 0;
            int remaining = static_cast<int>(k);
            const unsigned words = (k - 1) / 32 + 1;
            for (unsigned i = 0; i < words; ++i, remaining -= 32)
            {
                uint32_t r = genrand_uint32();
                if (remaining < 32) r >>= (32 - remaining);
                result |= BigInt(r) << shift;
                shift += 32;
            }
            return result;
        }

    private:
        static constexpr int N = 624, M = 397;
        std::array<uint32_t, N> mt{};
        int index = N + 1;

        void init_genrand(uint32_t s)
        {
            mt[0] = s;
            for (int i = 1; i < N; ++i) mt[i] = 1812433253u * (mt[i - 1] ^ (mt[i - 1] >> 30)) + static_cast<uint32_t>(i);
            index = N;
        }

        void init_by_array(const std::vector<uint32_t>& key)
        {
            init_genrand(19650218u);
            int i = 1, j = 0;
            const int len = static_cast<int>(key.size());
            for (int k = N > len ? N : len; k; --k)
            {
                mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525u)) + key[j] + static_cast<uint32_t>(j);
                ++i; ++j;
                if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
                if (j >= len) j = 0;
            }
            for (int k = N - 1; k; --k)
            {
                mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941u)) - static_cast<uint32_t>(i);
                ++i;
                if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
            }
            mt[0] = 0x80000000u;
        }

        uint32_t genrand_uint32()
        {
            static const uint32_t mag01[2] = {0x0u, 0x9908b0dfu};
            uint32_t y;
            if (index >= N)
            {
                int kk;
                for (kk = 0; kk < N - M; ++kk)
                {
                    y = (mt[kk] & 0x80000000u) | (mt[kk + 1] & 0x7fffffffu);
                    mt[kk] = mt[kk + M] ^ (y >> 1) ^ mag01[y & 0x1u];
                }
                for (; kk < N - 1; ++kk)
                {
                    y = (mt[kk] & 0x80000000u) | (mt[kk + 1] & 0x7fffffffu);
                    mt[kk] = mt[kk + (M - N)] ^ (y >> 1) ^ mag01[y & 0x1u];
                }
                y = (mt[N - 1] & 0x80000000u) | (mt[0] & 0x7fffffffu);
                mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ mag01[y & 0x1u];
                index = 0;
            }
            y = mt[index++];
            y ^= (y >> 11);
            y ^= (y << 7) & 0x9d2c5680u;
            y ^= (y << 15) & 0xefc60000u;
            y ^= (y >> 18);
            return y;
        }
    };
}
