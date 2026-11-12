// reka-host.exe : l'hôte de plugins de REKA.
//
// Lancé par REKA (core/reka_host.py) avec --port et --token : il se connecte à
// REKA en TCP sur 127.0.0.1, se présente, puis reçoit une commande JSON par
// ligne (setup, load, add_fx, remove_fx, move_fx, fx_enabled, chain_enabled,
// toggle_ui, rename, get_chains, quit) et renvoie l'état de ses chaînes.
//
// Fils :
//   - principal : fenêtres (interfaces des plugins), commandes, plugins ;
//   - lecture du socket : découpe les lignes et les confie au fil principal ;
//   - audio (Voicemeeter) : traite les strips, sans jamais attendre : si une
//     chaîne est en cours de modification, le bloc passe sans effets.
//
// Si REKA disparaît (socket fermé), l'hôte s'arrête de lui-même.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#include <ole2.h>
#include <xmmintrin.h>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include "plugin.h"
#include "voicemeeter.h"

using json = nlohmann::json;

namespace reka {
namespace {

constexpr const char* kVersion = "0.1.0";
constexpr int kProtocol = 1;
constexpr UINT WM_APP_LINE = WM_APP + 1;
constexpr UINT WM_APP_DISCONNECTED = WM_APP + 2;
constexpr UINT WM_APP_AUDIO_STARTED = WM_APP + 3;
constexpr UINT WM_APP_AUDIO_STOPPED = WM_APP + 4;
constexpr UINT WM_APP_AUDIO_CHANGED = WM_APP + 5;
constexpr UINT WM_APP_AUDIO_RUNNING = WM_APP + 6;
constexpr UINT_PTR kIdleTimer = 1;
constexpr UINT_PTR kVoicemeeterTimer = 2;
constexpr size_t kMaxFxPerStrip = 32;

// Champ d'une commande, ou null s'il manque (operator[] sur un json constant
// n'accepte pas une clé absente)
json arg(const json& command, const char* key) {
    auto it = command.find(key);
    return it == command.end() ? json() : *it;
}

// ------------------------------------------------------------------ chaînes
struct Slot {
    PluginDesc desc;
    std::unique_ptr<Plugin> plugin;  // nullptr : plugin introuvable sur ce PC
    bool enabled = true;
    std::string savedState;          // réglages (base64) d'un plugin introuvable
    json imported;                   // réglages repris de REAPER (chunk, params), en attente
    HWND window = nullptr;
};

struct Strip {
    std::string name;
    int channel = 0;
    int channels = 2;
    bool enabled = true;
    std::vector<std::unique_ptr<Slot>> fx;
    std::vector<float> left, right;  // tampons de travail du fil audio
};

class Host {
public:
    bool connect(int port, const std::string& token, std::string& error);
    int run();

private:
    // ---- socket
    void readerLoop();
    void send(const json& message);
    void sendState();
    void reply(const json& command, bool ok, const std::string& error = "");

    // ---- commandes
    void handleLine(const std::string& line);
    void handle(const json& command);
    void cmdSetup(const json& command);
    void cmdLoad(const json& command);
    void cmdAdd(const json& command);
    void cmdRemove(const json& command);
    void cmdMove(const json& command);
    void cmdToggleUi(const json& command);
    json chains();

    // ---- plugins
    std::unique_ptr<Slot> makeSlot(const json& fx, std::string& error, bool keepMissing);
    void prepare(Plugin* plugin);
    void prepareAll();
    Strip* strip(const json& value);
    Slot* slot(Strip* strip, const json& value);
    void closeWindow(Slot* slot);
    std::wstring windowTitle(const Strip& strip, const Slot& slot) const;

    // ---- audio
    void startAudio();
    void processBuffer(VmAudioBuffer* buffer);

    // ---- fenêtres
    static LRESULT CALLBACK messageProc(HWND, UINT, WPARAM, LPARAM);
    static LRESULT CALLBACK editorProc(HWND, UINT, WPARAM, LPARAM);
    static void resizeEditor(Plugin* plugin, int width, int height);
    void onTimer(UINT_PTR id);

    SOCKET socket_ = INVALID_SOCKET;
    std::mutex sendMutex_;
    std::mutex linesMutex_;
    std::deque<std::string> lines_;
    HWND messages_ = nullptr;
    std::string client_ = "REKA";

    std::mutex chainLock_;  // modifie les chaînes (le fil audio n'attend jamais)
    std::vector<Strip> strips_;

    Voicemeeter voicemeeter_;
    bool voicemeeterOpen_ = false;
    std::string audioMessage_ = "en attente de Voicemeeter";
    std::atomic<bool> running_{false};
    std::atomic<double> rate_{0};
    std::atomic<int> block_{0};
    ULONGLONG lastDirty_ = 0;

public:
    static Host* instance;
};

Host* Host::instance = nullptr;

// ------------------------------------------------------------------ socket
bool Host::connect(int port, const std::string& token, std::string& error) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        error = "WSAStartup";
        return false;
    }
    socket_ = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (socket_ == INVALID_SOCKET) {
        error = "socket";
        return false;
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons((u_short)port);
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    if (::connect(socket_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        error = "connexion à REKA impossible";
        return false;
    }
    int one = 1;
    setsockopt(socket_, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&one), sizeof(one));
    send({{"type", "hello"}, {"token", token}, {"version", kVersion}, {"protocol", kProtocol}});
    return true;
}

void Host::send(const json& message) {
    std::string text = message.dump(-1, ' ', false, json::error_handler_t::replace) + "\n";
    std::lock_guard<std::mutex> lock(sendMutex_);
    const char* data = text.data();
    size_t left = text.size();
    while (left > 0 && socket_ != INVALID_SOCKET) {
        int sent = ::send(socket_, data, (int)std::min<size_t>(left, 1 << 20), 0);
        if (sent <= 0) return;
        data += sent;
        left -= (size_t)sent;
    }
}

void Host::readerLoop() {
    std::string buffer;
    char chunk[65536];
    for (;;) {
        int n = recv(socket_, chunk, sizeof(chunk), 0);
        if (n <= 0) break;
        buffer.append(chunk, (size_t)n);
        size_t end;
        bool posted = false;
        while ((end = buffer.find('\n')) != std::string::npos) {
            {
                std::lock_guard<std::mutex> lock(linesMutex_);
                lines_.push_back(buffer.substr(0, end));
            }
            buffer.erase(0, end + 1);
            posted = true;
        }
        if (posted) PostMessageW(messages_, WM_APP_LINE, 0, 0);
    }
    PostMessageW(messages_, WM_APP_DISCONNECTED, 0, 0);
}

void Host::reply(const json& command, bool ok, const std::string& error) {
    json id = command.contains("id") ? arg(command, "id") : json();
    if (ok)
        send({{"type", "ack"}, {"id", id}});
    else
        send({{"type", "error"}, {"id", id}, {"message", error}});
}

void Host::sendState() {
    json strips = json::array();
    for (const Strip& s : strips_) {
        json fx = json::array();
        for (const auto& slot : s.fx) {
            fx.push_back({{"name", slot->plugin ? slot->plugin->desc().name : slot->desc.name},
                          {"format", slot->desc.format},
                          {"enabled", slot->enabled},
                          {"open", slot->window != nullptr},
                          {"missing", slot->plugin == nullptr},
                          {"latency", slot->plugin ? slot->plugin->latency() : -1}});
        }
        strips.push_back({{"name", s.name}, {"enabled", s.enabled}, {"fx", fx}});
    }
    send({{"type", "state"},
          {"engine",
           {{"running", running_.load()},
            {"samplerate", rate_.load()},
            {"blocksize", block_.load()},
            {"message", running_ ? "" : audioMessage_}}},
          {"strips", strips}});
}

// ------------------------------------------------------------------ commandes
void Host::handleLine(const std::string& line) {
    json command;
    try {
        command = json::parse(line);
    } catch (const std::exception&) {
        logLine("ligne illisible ignorée");
        return;
    }
    if (!command.is_object()) return;
    try {
        handle(command);
    } catch (const std::exception& e) {
        logLine(std::string("commande en échec : ") + e.what());
        reply(command, false, std::string("commande invalide : ") + e.what());
    }
}

void Host::handle(const json& command) {
    std::string cmd = command.value("cmd", "");
    if (cmd == "quit") {
        PostQuitMessage(0);
        return;
    }
    if (cmd == "get_chains") {
        send({{"type", "chains"}, {"id", command.value("id", 0)}, {"chains", chains()}});
        return;
    }
    if (cmd == "setup") {
        cmdSetup(command);
    } else if (cmd == "load") {
        cmdLoad(command);
    } else if (cmd == "add_fx") {
        cmdAdd(command);
        return;  // répond lui-même
    } else if (cmd == "remove_fx") {
        cmdRemove(command);
    } else if (cmd == "move_fx") {
        cmdMove(command);
        return;
    } else if (cmd == "toggle_ui") {
        cmdToggleUi(command);
        return;
    } else if (cmd == "fx_enabled") {
        Strip* s = strip(arg(command, "strip"));
        Slot* fx = slot(s, arg(command, "index"));
        if (fx) {
            std::lock_guard<std::mutex> lock(chainLock_);
            fx->enabled = command.value("enabled", true);
        }
    } else if (cmd == "chain_enabled") {
        if (Strip* s = strip(arg(command, "strip"))) {
            std::lock_guard<std::mutex> lock(chainLock_);
            s->enabled = command.value("enabled", true);
        }
    } else if (cmd == "rename") {
        if (Strip* s = strip(arg(command, "strip"))) {
            s->name = command.value("name", s->name);
            for (auto& fx : s->fx)
                if (fx->window) SetWindowTextW(fx->window, windowTitle(*s, *fx).c_str());
        }
    } else {
        reply(command, false, "commande inconnue : " + cmd);
        return;
    }
    reply(command, true);
    sendState();
}

void Host::cmdSetup(const json& command) {
    client_ = command.value("client", client_);
    std::vector<Strip> strips;
    for (const json& item : command.value("strips", json::array())) {
        Strip s;
        s.name = item.value("name", "");
        s.channel = std::max(0, item.value("channel", 0));
        s.channels = std::clamp(item.value("channels", 2), 1, 8);
        strips.push_back(std::move(s));
    }
    {
        std::lock_guard<std::mutex> lock(chainLock_);
        for (Strip& s : strips_)
            for (auto& fx : s.fx) closeWindow(fx.get());
        strips_ = std::move(strips);
        int block = std::max(block_.load(), 4096);
        for (Strip& s : strips_) {
            s.left.assign((size_t)block, 0.0f);
            s.right.assign((size_t)block, 0.0f);
        }
    }
    startAudio();
}

std::unique_ptr<Slot> Host::makeSlot(const json& fx, std::string& error, bool keepMissing) {
    auto slot = std::make_unique<Slot>();
    slot->desc.format = fx.value("format", "");
    slot->desc.path = fx.value("path", "");
    slot->desc.uid = fx.value("uid", "");
    slot->desc.name = fx.value("name", "");
    slot->desc.vendor = fx.value("vendor", "");
    if (slot->desc.name.empty()) slot->desc.name = fileStem(slot->desc.path);
    slot->enabled = fx.value("enabled", true);
    slot->savedState = fx.value("state", "");
    if (slot->savedState.empty()) {
        if (fx.contains("chunk")) slot->imported["chunk"] = fx["chunk"];
        if (fx.contains("params")) slot->imported["params"] = fx["params"];
    }
    logLine("chargement : " + slot->desc.path);
    slot->plugin = loadPlugin(slot->desc, error);
    if (!slot->plugin) {
        logLine("échec : " + error);
        if (!keepMissing) return nullptr;
        return slot;  // gardé (et ses réglages aussi) pour un autre PC
    }
    slot->desc.name = slot->plugin->desc().name;
    slot->desc.vendor = slot->plugin->desc().vendor;
    slot->desc.uid = slot->plugin->desc().uid;
    if (!slot->savedState.empty()) {
        std::vector<uint8_t> state;
        if (base64Decode(slot->savedState, state) && !slot->plugin->setState(state))
            logLine("réglages refusés par " + slot->desc.name);
        slot->savedState.clear();
    } else if (!slot->imported.is_null()) {
        // Réglages repris de REAPER : le chunk du plugin, sinon ses paramètres
        std::vector<uint8_t> chunk;
        bool applied = false;
        if (slot->imported.contains("chunk") && slot->imported["chunk"].is_string() &&
            base64Decode(slot->imported["chunk"].get<std::string>(), chunk) && !chunk.empty())
            applied = slot->plugin->setChunk(chunk);
        if (!applied && slot->imported.contains("params") && slot->imported["params"].is_array()) {
            std::vector<std::pair<std::string, double>> params;
            for (const json& p : slot->imported["params"])
                if (p.is_array() && p.size() == 2 && p[1].is_number())
                    params.emplace_back(p[0].is_string() ? p[0].get<std::string>() : "",
                                        p[1].get<double>());
            slot->plugin->setParams(params);
        }
        logLine("réglages repris de REAPER : " + slot->desc.name);
        slot->imported = json();
    }
    prepare(slot->plugin.get());
    return slot;
}

void Host::prepare(Plugin* plugin) {
    double rate = rate_.load();
    int block = block_.load();
    if (!plugin || rate <= 0 || block <= 0) return;
    if (plugin->isPrepared() && plugin->preparedRate() == rate && plugin->preparedBlock() >= block)
        return;
    if (!plugin->prepare(rate, block)) logLine("préparation refusée : " + plugin->desc().name);
}

void Host::prepareAll() {
    // Le traitement est suspendu le temps de préparer (le son passe sans effets)
    std::lock_guard<std::mutex> lock(chainLock_);
    int block = block_.load();
    for (Strip& s : strips_) {
        if ((int)s.left.size() < block) {
            s.left.assign((size_t)block, 0.0f);
            s.right.assign((size_t)block, 0.0f);
        }
        for (auto& fx : s.fx) prepare(fx->plugin.get());
    }
}

void Host::cmdLoad(const json& command) {
    const json& chains = command.value("chains", json::object());
    const json& strips = chains.value("strips", json::array());
    for (size_t i = 0; i < strips_.size() && i < strips.size(); ++i) {
        std::vector<std::unique_ptr<Slot>> fresh;
        for (const json& fx : strips[i].value("fx", json::array())) {
            if (fresh.size() >= kMaxFxPerStrip) break;
            std::string error;
            if (auto slot = makeSlot(fx, error, true)) fresh.push_back(std::move(slot));
        }
        std::vector<std::unique_ptr<Slot>> old;
        for (auto& fx : strips_[i].fx) closeWindow(fx.get());
        {
            std::lock_guard<std::mutex> lock(chainLock_);
            old.swap(strips_[i].fx);
            strips_[i].fx = std::move(fresh);
            strips_[i].enabled = strips[i].value("enabled", true);
        }
        // Les anciens plugins sont détruits hors du verrou
    }
}

void Host::cmdAdd(const json& command) {
    Strip* s = strip(arg(command, "strip"));
    if (!s) return reply(command, false, "strip inconnue");
    if (s->fx.size() >= kMaxFxPerStrip) return reply(command, false, "trop d'effets sur cette strip");
    std::string error;
    auto slot = makeSlot(command.value("plugin", json::object()), error, false);
    if (!slot) {
        std::string name = arg(command, "plugin").value("name", "");
        return reply(command, false, "« " + name + " » n'a pas pu être chargé : " + error);
    }
    {
        std::lock_guard<std::mutex> lock(chainLock_);
        s->fx.push_back(std::move(slot));
    }
    reply(command, true);
    sendState();
}

void Host::cmdRemove(const json& command) {
    Strip* s = strip(arg(command, "strip"));
    Slot* fx = slot(s, arg(command, "index"));
    if (!fx) return;
    closeWindow(fx);
    std::unique_ptr<Slot> removed;
    {
        std::lock_guard<std::mutex> lock(chainLock_);
        size_t index = arg(command, "index").get<size_t>();
        removed = std::move(s->fx[index]);
        s->fx.erase(s->fx.begin() + (long)index);
    }
}

void Host::cmdMove(const json& command) {
    Strip* src = strip(arg(command, "src"));
    Strip* dst = strip(arg(command, "dst"));
    Slot* fx = slot(src, arg(command, "src_index"));
    if (!fx || !dst) return reply(command, false, "effet introuvable");
    size_t srcIndex = arg(command, "src_index").get<size_t>();
    size_t target = (size_t)std::max(0, command.value("dst_index", 0));
    if (command.value("copy", false)) {
        if (dst->fx.size() >= kMaxFxPerStrip)
            return reply(command, false, "trop d'effets sur cette strip");
        // Nouvelle instance du même plugin, avec les mêmes réglages
        json description = {{"format", fx->desc.format}, {"path", fx->desc.path},
                            {"uid", fx->desc.uid}, {"name", fx->desc.name},
                            {"vendor", fx->desc.vendor}, {"enabled", fx->enabled}};
        if (fx->plugin) {
            std::vector<uint8_t> state;
            if (fx->plugin->getState(state)) description["state"] = base64Encode(state);
        } else {
            description["state"] = fx->savedState;
            if (fx->imported.is_object())
                for (auto& [key, value] : fx->imported.items()) description[key] = value;
        }
        std::string error;
        auto copy = makeSlot(description, error, true);
        std::lock_guard<std::mutex> lock(chainLock_);
        target = std::min(target, dst->fx.size());
        dst->fx.insert(dst->fx.begin() + (long)target, std::move(copy));
    } else {
        std::lock_guard<std::mutex> lock(chainLock_);
        std::unique_ptr<Slot> moved = std::move(src->fx[srcIndex]);
        src->fx.erase(src->fx.begin() + (long)srcIndex);
        target = std::min(target, dst->fx.size());
        dst->fx.insert(dst->fx.begin() + (long)target, std::move(moved));
        Slot* placed = dst->fx[target].get();
        if (placed->window) SetWindowTextW(placed->window, windowTitle(*dst, *placed).c_str());
    }
    reply(command, true);
    sendState();
}

json Host::chains() {
    json strips = json::array();
    for (const Strip& s : strips_) {
        json fx = json::array();
        for (const auto& slot : s.fx) {
            std::string state = slot->savedState;
            if (slot->plugin) {
                std::vector<uint8_t> bytes;
                if (slot->plugin->getState(bytes)) state = base64Encode(bytes);
            }
            json entry = {{"format", slot->desc.format}, {"path", slot->desc.path},
                          {"uid", slot->desc.uid}, {"name", slot->desc.name},
                          {"vendor", slot->desc.vendor}, {"enabled", slot->enabled},
                          {"state", state}};
            // Plugin introuvable : ses réglages repris de REAPER attendent toujours
            if (state.empty() && slot->imported.is_object())
                for (auto& [key, value] : slot->imported.items()) entry[key] = value;
            fx.push_back(entry);
        }
        strips.push_back({{"enabled", s.enabled}, {"fx", fx}});
    }
    return {{"format", 1}, {"strips", strips}};
}

Strip* Host::strip(const json& value) {
    if (!value.is_number_integer()) return nullptr;
    int index = value.get<int>();
    return index >= 0 && index < (int)strips_.size() ? &strips_[(size_t)index] : nullptr;
}

Slot* Host::slot(Strip* s, const json& value) {
    if (!s || !value.is_number_integer()) return nullptr;
    int index = value.get<int>();
    return index >= 0 && index < (int)s->fx.size() ? s->fx[(size_t)index].get() : nullptr;
}

// ------------------------------------------------------------------ interfaces
std::wstring Host::windowTitle(const Strip& s, const Slot& slot) const {
    std::string name = slot.plugin ? slot.plugin->desc().name : slot.desc.name;
    return widen(name + " — " + s.name + " (REKA)");
}

void Host::cmdToggleUi(const json& command) {
    Strip* s = strip(arg(command, "strip"));
    Slot* fx = slot(s, arg(command, "index"));
    if (!fx) return reply(command, false, "effet introuvable");
    if (fx->window) {
        closeWindow(fx);
        reply(command, true);
        sendState();
        return;
    }
    if (!fx->plugin) return reply(command, false, "« " + fx->desc.name + " » n'est pas installé sur ce PC");
    if (!fx->plugin->hasEditor())
        return reply(command, false, "« " + fx->desc.name + " » n'a pas d'interface");
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    HWND window = CreateWindowExW(0, L"RekaPluginWindow", windowTitle(*s, *fx).c_str(), style,
                                  CW_USEDEFAULT, CW_USEDEFAULT, 640, 400, nullptr, nullptr,
                                  GetModuleHandleW(nullptr), nullptr);
    if (!window) return reply(command, false, "fenêtre impossible à créer");
    SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(fx));
    fx->window = window;
    int width = 640, height = 400;
    if (!fx->plugin->openEditor(window, width, height)) {
        fx->window = nullptr;
        DestroyWindow(window);
        return reply(command, false, "l'interface de « " + fx->desc.name + " » ne s'ouvre pas");
    }
    resizeEditor(fx->plugin.get(), width, height);
    ShowWindow(window, SW_SHOWNORMAL);
    SetForegroundWindow(window);
    reply(command, true);
    sendState();
}

void Host::closeWindow(Slot* slot) {
    if (!slot || !slot->window) return;
    HWND window = slot->window;
    slot->window = nullptr;
    if (slot->plugin) slot->plugin->closeEditor();
    SetWindowLongPtrW(window, GWLP_USERDATA, 0);
    DestroyWindow(window);
}

void Host::resizeEditor(Plugin* plugin, int width, int height) {
    if (!instance || width <= 0 || height <= 0) return;
    for (Strip& s : instance->strips_) {
        for (auto& fx : s.fx) {
            if (fx->plugin.get() != plugin || !fx->window) continue;
            RECT rect{0, 0, width, height};
            DWORD style = (DWORD)GetWindowLongPtrW(fx->window, GWL_STYLE);
            AdjustWindowRectEx(&rect, style, FALSE, 0);
            SetWindowPos(fx->window, nullptr, 0, 0, rect.right - rect.left, rect.bottom - rect.top,
                         SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
            return;
        }
    }
}

LRESULT CALLBACK Host::editorProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) {
        auto* slot = reinterpret_cast<Slot*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (slot && instance) {
            instance->closeWindow(slot);
            instance->sendState();
        } else {
            DestroyWindow(window);
        }
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

// ------------------------------------------------------------------ audio
void Host::startAudio() {
    std::string error;
    if (!voicemeeterOpen_) {
        voicemeeter_.onStarted = [this](double rate, int block) {
            rate_.store(rate);
            block_.store(block);
            HostHooks::sampleRate.store(rate);
            HostHooks::blockSize.store(block);
            PostMessageW(messages_, WM_APP_AUDIO_STARTED, 0, 0);
        };
        voicemeeter_.onStopped = [this]() {
            running_.store(false);
            PostMessageW(messages_, WM_APP_AUDIO_STOPPED, 0, 0);
        };
        voicemeeter_.onChanged = [this]() { PostMessageW(messages_, WM_APP_AUDIO_CHANGED, 0, 0); };
        voicemeeter_.onBuffer = [this](VmAudioBuffer* buffer) { processBuffer(buffer); };
        voicemeeterOpen_ = voicemeeter_.open(client_, error);
    }
    if (voicemeeterOpen_ && voicemeeter_.startCallback(error)) {
        audioMessage_ = "en attente du son de Voicemeeter";
        KillTimer(messages_, kVoicemeeterTimer);
        logLine("audio callback de Voicemeeter démarré");
        return;
    }
    audioMessage_ = error;
    logLine("Voicemeeter : " + error + " (nouvel essai dans 3 s)");
    SetTimer(messages_, kVoicemeeterTimer, 3000, nullptr);
    sendState();
}

void Host::processBuffer(VmAudioBuffer* buffer) {
    // Pas de nombres dénormalisés : certains plugins s'effondrent sinon
    _mm_setcsr(_mm_getcsr() | 0x8040);
    const int n = buffer->audiobuffer_nbs;
    const int channels = std::min<int>(buffer->audiobuffer_nbi, 128);
    // Le son de chaque entrée doit être rendu à Voicemeeter, traité ou non
    for (int c = 0; c < channels; ++c) {
        float* r = buffer->audiobuffer_r[c];
        float* w = buffer->audiobuffer_w[c];
        if (r && w && r != w) std::memcpy(w, r, sizeof(float) * (size_t)n);
    }
    if (!running_.load(std::memory_order_relaxed) && !running_.exchange(true))
        PostMessageW(messages_, WM_APP_AUDIO_RUNNING, 0, 0);  // premier bloc : REKA est prévenu
    if (!chainLock_.try_lock()) return;  // chaîne en cours de modification : bloc sans effets
    for (Strip& s : strips_) {
        if (!s.enabled || s.fx.empty() || s.channel >= channels) continue;
        if ((int)s.left.size() < n) continue;
        float* left = buffer->audiobuffer_w[s.channel];
        float* right = (s.channels > 1 && s.channel + 1 < channels)
                           ? buffer->audiobuffer_w[s.channel + 1]
                           : left;
        if (!left || !right) continue;
        float* in[2] = {left, right};
        float* out[2] = {s.left.data(), s.right.data()};
        for (auto& fx : s.fx) {
            Plugin* plugin = fx->plugin.get();
            if (!fx->enabled || !plugin || !plugin->isPrepared() || n > plugin->preparedBlock())
                continue;
            plugin->process(in, out, n);
            std::memcpy(left, out[0], sizeof(float) * (size_t)n);
            if (right != left) std::memcpy(right, out[1], sizeof(float) * (size_t)n);
        }
    }
    chainLock_.unlock();
}

// ------------------------------------------------------------------ boucle
void Host::onTimer(UINT_PTR id) {
    if (id == kVoicemeeterTimer) {
        startAudio();
        return;
    }
    for (Strip& s : strips_)
        for (auto& fx : s.fx)
            if (fx->window && fx->plugin) fx->plugin->editorIdle();
    ULONGLONG now = GetTickCount64();
    if (HostHooks::dirty.load() && now - lastDirty_ > 500) {
        HostHooks::dirty.store(false);
        lastDirty_ = now;
        send({{"type", "dirty"}});
    }
    if (HostHooks::latencyChanged.exchange(false)) sendState();
}

LRESULT CALLBACK Host::messageProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    Host* self = instance;
    if (!self) return DefWindowProcW(window, message, wparam, lparam);
    switch (message) {
        case WM_APP_LINE: {
            std::deque<std::string> lines;
            {
                std::lock_guard<std::mutex> lock(self->linesMutex_);
                lines.swap(self->lines_);
            }
            for (const std::string& line : lines) self->handleLine(line);
            return 0;
        }
        case WM_APP_DISCONNECTED:
            logLine("REKA s'est déconnecté : arrêt");
            PostQuitMessage(0);
            return 0;
        case WM_APP_AUDIO_STARTED:
            logLine("son de Voicemeeter : " + std::to_string((int)self->rate_.load()) + " Hz, " +
                    std::to_string(self->block_.load()) + " échantillons");
            self->prepareAll();
            self->sendState();
            return 0;
        case WM_APP_AUDIO_RUNNING:
            logLine("le son de Voicemeeter passe par REKA");
            self->sendState();
            return 0;
        case WM_APP_AUDIO_STOPPED:
            self->audioMessage_ = "Voicemeeter a arrêté le son (redémarrage du moteur audio ?)";
            self->sendState();
            return 0;
        case WM_APP_AUDIO_CHANGED:
            logLine("réglages audio de Voicemeeter changés : relance du callback");
            self->voicemeeter_.stopCallback();
            self->running_.store(false);
            self->startAudio();
            return 0;
        case WM_TIMER:
            self->onTimer(wparam);
            return 0;
        default:
            return DefWindowProcW(window, message, wparam, lparam);
    }
}

int Host::run() {
    HINSTANCE module = GetModuleHandleW(nullptr);
    WNDCLASSW messages{};
    messages.lpfnWndProc = &Host::messageProc;
    messages.hInstance = module;
    messages.lpszClassName = L"RekaHostMessages";
    RegisterClassW(&messages);
    WNDCLASSW editors{};
    editors.lpfnWndProc = &Host::editorProc;
    editors.hInstance = module;
    editors.hCursor = LoadCursor(nullptr, IDC_ARROW);
    editors.hbrBackground = (HBRUSH)GetStockObject(BLACK_BRUSH);
    editors.lpszClassName = L"RekaPluginWindow";
    editors.hIcon = LoadIconW(module, MAKEINTRESOURCEW(1));
    RegisterClassW(&editors);
    messages_ = CreateWindowExW(0, L"RekaHostMessages", L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr,
                                module, nullptr);
    HostHooks::resizeEditor = &Host::resizeEditor;
    SetTimer(messages_, kIdleTimer, 30, nullptr);

    std::thread reader([this] { readerLoop(); });
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    // Arrêt : plus de son vers les plugins, puis fermeture de tout
    voicemeeter_.close();
    for (Strip& s : strips_)
        for (auto& fx : s.fx) closeWindow(fx.get());
    strips_.clear();
    shutdown(socket_, SD_BOTH);
    closesocket(socket_);
    socket_ = INVALID_SOCKET;
    reader.join();
    WSACleanup();
    return 0;
}

}  // namespace
}  // namespace reka

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int port = 0;
    std::string token, client = "REKA";
    for (int i = 1; i + 1 < argc; i += 2) {
        std::wstring key = argv[i];
        std::wstring value = argv[i + 1];
        if (key == L"--port") port = _wtoi(value.c_str());
        else if (key == L"--token") token = reka::narrow(value);
        else if (key == L"--client") client = reka::narrow(value);
        else if (key == L"--log") reka::setLogFile(value);
    }
    LocalFree(argv);
    reka::logLine(std::string("reka-host ") + reka::kVersion + " (" + client + ")");
    if (port <= 0 || token.empty()) {
        reka::logLine("arguments manquants : --port et --token");
        return 2;
    }
    OleInitialize(nullptr);
    reka::Host host;
    reka::Host::instance = &host;
    std::string error;
    if (!host.connect(port, token, error)) {
        reka::logLine(error);
        return 1;
    }
    int code = host.run();
    reka::Host::instance = nullptr;
    OleUninitialize();
    return code;
}
