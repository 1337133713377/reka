// Utilitaires : UTF-8 ↔ UTF-16, base64, journal, chargement d'un plugin.
#include <cstdio>
#include <ctime>
#include <mutex>

#include "plugin.h"

namespace reka {

std::atomic<bool> HostHooks::dirty{false};
std::atomic<bool> HostHooks::latencyChanged{false};
std::atomic<double> HostHooks::sampleRate{48000.0};
std::atomic<int> HostHooks::blockSize{512};
void (*HostHooks::resizeEditor)(Plugin*, int, int) = nullptr;

std::wstring widen(const std::string& utf8) {
    if (utf8.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), nullptr, 0);
    std::wstring out(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.data(), (int)utf8.size(), out.data(), n);
    return out;
}

std::string narrow(const std::wstring& wide) {
    if (wide.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, wide.data(), (int)wide.size(), nullptr, 0, nullptr,
                                nullptr);
    std::string out(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.data(), (int)wide.size(), out.data(), n, nullptr,
                        nullptr);
    return out;
}

std::string fileStem(const std::string& path) {
    size_t slash = path.find_last_of("/\\");
    std::string name = slash == std::string::npos ? path : path.substr(slash + 1);
    size_t dot = name.find_last_of('.');
    return dot == std::string::npos ? name : name.substr(0, dot);
}

static const char kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

std::string base64Encode(const std::vector<uint8_t>& data) {
    std::string out;
    out.reserve((data.size() + 2) / 3 * 4);
    size_t i = 0;
    for (; i + 2 < data.size(); i += 3) {
        uint32_t v = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        out += kB64[(v >> 18) & 63];
        out += kB64[(v >> 12) & 63];
        out += kB64[(v >> 6) & 63];
        out += kB64[v & 63];
    }
    if (i + 1 == data.size()) {
        uint32_t v = data[i] << 16;
        out += kB64[(v >> 18) & 63];
        out += kB64[(v >> 12) & 63];
        out += "==";
    } else if (i + 2 == data.size()) {
        uint32_t v = (data[i] << 16) | (data[i + 1] << 8);
        out += kB64[(v >> 18) & 63];
        out += kB64[(v >> 12) & 63];
        out += kB64[(v >> 6) & 63];
        out += '=';
    }
    return out;
}

bool base64Decode(const std::string& text, std::vector<uint8_t>& out) {
    int table[256];
    for (int& t : table) t = -1;
    for (int k = 0; k < 64; ++k) table[(unsigned char)kB64[k]] = k;
    out.clear();
    out.reserve(text.size() / 4 * 3);
    uint32_t buffer = 0;
    int bits = 0;
    for (char c : text) {
        if (c == '=') break;
        int v = table[(unsigned char)c];
        if (v < 0) return false;
        buffer = (buffer << 6) | (uint32_t)v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back((uint8_t)((buffer >> bits) & 0xFF));
        }
    }
    return true;
}

static std::mutex gLogMutex;
static FILE* gLog = nullptr;

void setLogFile(const std::wstring& path) {
    std::lock_guard<std::mutex> lock(gLogMutex);
    if (gLog) fclose(gLog);
    // Le journal du lancement précédent est gardé (utile après un plantage)
    MoveFileExW(path.c_str(), (path + L".1").c_str(), MOVEFILE_REPLACE_EXISTING);
    gLog = _wfopen(path.c_str(), L"w");
}

void logLine(const std::string& text) {
    std::lock_guard<std::mutex> lock(gLogMutex);
    if (!gLog) return;
    std::time_t now = std::time(nullptr);
    char stamp[32];
    std::strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", std::localtime(&now));
    std::fprintf(gLog, "%s %s\n", stamp, text.c_str());
    std::fflush(gLog);
}

std::unique_ptr<Plugin> loadPlugin(const PluginDesc& desc, std::string& error) {
    if (desc.format == "VST3") return loadVst3(desc, error);
    if (desc.format == "VST2") return loadVst2(desc, error);
    error = "format de plugin inconnu : " + desc.format;
    return nullptr;
}

}  // namespace reka
