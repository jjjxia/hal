#include "process.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

namespace hal::algorithm_recovery::process
{
    namespace
    {
        using Clock = std::chrono::steady_clock;
        void require(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
        std::vector<char*> arguments(const std::vector<std::string>& command)
        {
            require(!command.empty() && !command.front().empty(), "empty process command");
            std::vector<char*> result;
            for (const auto& arg : command) { require(arg.find('\0') == std::string::npos, "embedded NUL in process argument"); result.push_back(const_cast<char*>(arg.c_str())); }
            result.push_back(nullptr); return result;
        }
        void terminate(pid_t& pid)
        {
            if (pid <= 0) return;
            int old_status; pid_t result;
            do { result = waitpid(pid, &old_status, WNOHANG); } while (result < 0 && errno == EINTR);
            if (result == pid || (result < 0 && errno == ECHILD)) { pid = -1; return; }
            kill(pid, SIGKILL); int status;
            while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
            pid = -1;
        }
        int wait(pid_t& pid, unsigned timeout)
        {
            const auto deadline = Clock::now() + std::chrono::milliseconds(timeout);
            while (true)
            {
                int status; const auto result = waitpid(pid, &status, WNOHANG);
                if (result == pid) { pid = -1; return status; }
                if (result < 0 && errno == ECHILD) pid = -1;
                require(result == 0 || (result < 0 && errno == EINTR), "cannot reap child process");
                require(Clock::now() < deadline, "child process timed out");
                const timespec pause{0, 1000000}; nanosleep(&pause, nullptr);
            }
        }
        void ready(int socket, short events, Clock::time_point deadline)
        {
            while (true)
            {
                const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
                require(left > 0, "generated program response timed out");
                pollfd descriptor{socket, events, 0}; const auto result = poll(&descriptor, 1, static_cast<int>(std::min<int64_t>(left, 5000)));
                if (result < 0 && errno == EINTR) continue;
                require(result >= 0, "program socket poll failed");
                if (result) { require(!(descriptor.revents & POLLNVAL), "invalid program socket"); return; }
            }
        }
    }
    void run(const std::vector<std::string>& command, const std::filesystem::path& output, unsigned timeout)
    {
        auto argv = arguments(command); const int log = open(output.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0600);
        require(log >= 0, "cannot open process log: " + output.string());
        auto pid = fork();
        if (pid == 0)
        {
            if (dup2(log, STDOUT_FILENO) < 0 || dup2(log, STDERR_FILENO) < 0) _exit(126);
            close(log); execvp(argv[0], argv.data()); _exit(127);
        }
        close(log); require(pid > 0, "cannot launch process");
        try { const auto status = wait(pid, timeout); require(WIFEXITED(status) && WEXITSTATUS(status) == 0, command.front() + " failed; see " + output.string()); }
        catch (...) { terminate(pid); throw; }
    }
    JsonProcess::JsonProcess(const std::vector<std::string>& command, const std::filesystem::path& output, unsigned timeout) : m_timeout(timeout)
    {
        auto argv = arguments(command); int sockets[2];
        require(socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sockets) == 0, "cannot create program socket");
        const int log = open(output.c_str(), O_CREAT | O_TRUNC | O_WRONLY | O_CLOEXEC, 0600);
        if (log < 0) { close(sockets[0]); close(sockets[1]); throw std::runtime_error("cannot open program stderr log"); }
        m_pid = fork();
        if (m_pid == 0)
        {
            close(sockets[0]);
            if (dup2(sockets[1], STDIN_FILENO) < 0 || dup2(sockets[1], STDOUT_FILENO) < 0 || dup2(log, STDERR_FILENO) < 0) _exit(126);
            close(sockets[1]); close(log); execvp(argv[0], argv.data()); _exit(127);
        }
        close(sockets[1]); close(log);
        if (m_pid <= 0) { close(sockets[0]); throw std::runtime_error("cannot launch generated program"); }
        m_socket = sockets[0];
        if (fcntl(m_socket, F_SETFL, O_NONBLOCK) < 0)
        { close(m_socket); m_socket = -1; terminate(m_pid); throw std::runtime_error("cannot configure program socket"); }
    }
    JsonProcess::~JsonProcess()
    { if (m_socket >= 0) close(m_socket); terminate(m_pid); }
    nlohmann::json JsonProcess::request(const nlohmann::json& value)
    {
        require(m_pid > 0 && m_socket >= 0, "request on closed program");
        const auto deadline = Clock::now() + std::chrono::milliseconds(m_timeout);
        const auto line = value.dump() + '\n'; size_t sent = 0;
        while (sent < line.size())
        {
            ready(m_socket, POLLOUT, deadline);
            const auto count = send(m_socket, line.data() + sent, line.size() - sent, MSG_NOSIGNAL);
            if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            require(count > 0, "generated program closed request channel"); sent += count;
        }
        while (true)
        {
            const auto end = m_pending.find('\n');
            if (end != std::string::npos)
            { auto result = nlohmann::json::parse(m_pending.substr(0, end)); m_pending.erase(0, end + 1); return result; }
            ready(m_socket, POLLIN, deadline); char buffer[65536];
            const auto count = recv(m_socket, buffer, sizeof(buffer), 0);
            if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            require(count > 0, "generated program exited without a complete response");
            m_pending.append(buffer, count); require(m_pending.size() <= 128u * 1024 * 1024, "generated program response exceeds limit");
        }
    }
    void JsonProcess::finish()
    {
        require(m_socket >= 0 && m_pid > 0 && m_pending.empty(), "unexpected program output/state");
        shutdown(m_socket, SHUT_WR); const auto status = wait(m_pid, m_timeout);
        char extra; const auto count = recv(m_socket, &extra, 1, 0);
        require(count == 0, "unexpected trailing generated-program output");
        close(m_socket); m_socket = -1;
        require(WIFEXITED(status) && WEXITSTATUS(status) == 0, "generated program terminated unsuccessfully");
    }
}
