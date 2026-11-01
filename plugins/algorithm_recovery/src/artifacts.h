#pragma once
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sodium.h>

namespace hal::algorithm_recovery::artifacts
{
    inline void require(bool condition, const std::string& why) { if (!condition) throw std::runtime_error(why); }
    inline std::string read(const std::filesystem::path& path)
    {
        std::ifstream in(path, std::ios::binary); require(bool(in), "cannot read " + path.string());
        std::ostringstream data; data << in.rdbuf(); require(!in.bad(), "read failed " + path.string()); return data.str();
    }
    inline void write(const std::filesystem::path& path, const std::string& text)
    {
        std::ofstream out(path, std::ios::binary); out << text; require(bool(out), "write failed " + path.string());
    }
    inline std::string sha256(const std::string& text)
    {
        require(sodium_init() >= 0, "SHA256 initialization failed");
        unsigned char digest[crypto_hash_sha256_BYTES]; char hex[crypto_hash_sha256_BYTES * 2 + 1];
        crypto_hash_sha256(digest, reinterpret_cast<const unsigned char*>(text.data()), text.size());
        sodium_bin2hex(hex, sizeof(hex), digest, sizeof(digest)); return hex;
    }
    inline std::string file_hash(const std::filesystem::path& path) { return sha256(read(path)); }
}
