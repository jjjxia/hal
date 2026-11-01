#pragma once
#include <filesystem>
#include <nlohmann/json.hpp>
#include <string>
#include <vector>
#include <sys/types.h>

namespace hal::algorithm_recovery::process
{
    void run(const std::vector<std::string>& command, const std::filesystem::path& log, unsigned timeout_ms);
    class JsonProcess
    {
    public:
        JsonProcess(const std::vector<std::string>& command, const std::filesystem::path& error_log, unsigned timeout_ms);
        ~JsonProcess();
        JsonProcess(const JsonProcess&) = delete;
        JsonProcess& operator=(const JsonProcess&) = delete;
        nlohmann::json request(const nlohmann::json& value);
        void finish();
    private:
        pid_t m_pid = -1;
        int m_socket = -1;
        unsigned m_timeout;
        std::string m_pending;
    };
}
