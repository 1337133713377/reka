#include "voicemeeter.h"

#include <cstring>

#include "plugin.h"

namespace reka {
namespace {

// Clé de désinstallation commune aux éditions de Voicemeeter
const wchar_t kUninstallKey[] =
    L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VB:Voicemeeter "
    L"{17359A74-1236-5467}";

std::wstring installDir() {
    for (REGSAM view : {KEY_WOW64_32KEY, KEY_WOW64_64KEY}) {
        HKEY key;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, kUninstallKey, 0, KEY_READ | view, &key) !=
            ERROR_SUCCESS)
            continue;
        wchar_t value[MAX_PATH * 2] = {};
        DWORD size = sizeof(value) - sizeof(wchar_t);
        DWORD type = 0;
        LONG status = RegQueryValueExW(key, L"UninstallString", nullptr, &type,
                                       reinterpret_cast<BYTE*>(value), &size);
        RegCloseKey(key);
        if (status != ERROR_SUCCESS) continue;
        std::wstring path(value);
        if (!path.empty() && path.front() == L'"') path = path.substr(1, path.find(L'"', 1) - 1);
        size_t slash = path.find_last_of(L"\\/");
        if (slash != std::wstring::npos) return path.substr(0, slash);
    }
    return L"";
}

}  // namespace

Voicemeeter::~Voicemeeter() { close(); }

bool Voicemeeter::open(const std::string& client, std::string& error) {
    client_ = client.substr(0, 63);
    if (!dll_) {
        // Tests : faux Voicemeeter (native/reka-host/test/fake_voicemeeter.cpp)
        wchar_t custom[MAX_PATH] = {};
        HMODULE dll = nullptr;
        if (GetEnvironmentVariableW(L"REKA_VOICEMEETER_DLL", custom, MAX_PATH) > 0)
            dll = LoadLibraryW(custom);
        if (!dll) {
            std::wstring dir = installDir();
            if (!dir.empty()) dll = LoadLibraryW((dir + L"\\VoicemeeterRemote64.dll").c_str());
        }
        if (!dll)
            dll = LoadLibraryW(L"C:\\Program Files (x86)\\VB\\Voicemeeter\\VoicemeeterRemote64.dll");
        if (!dll) {
            error = "Voicemeeter est introuvable (VoicemeeterRemote64.dll)";
            return false;
        }
        login_ = reinterpret_cast<LoginFn>(GetProcAddress(dll, "VBVMR_Login"));
        logout_ = reinterpret_cast<LogoutFn>(GetProcAddress(dll, "VBVMR_Logout"));
        register_ = reinterpret_cast<RegisterFn>(GetProcAddress(dll, "VBVMR_AudioCallbackRegister"));
        start_ = reinterpret_cast<SimpleFn>(GetProcAddress(dll, "VBVMR_AudioCallbackStart"));
        stop_ = reinterpret_cast<SimpleFn>(GetProcAddress(dll, "VBVMR_AudioCallbackStop"));
        unregister_ =
            reinterpret_cast<SimpleFn>(GetProcAddress(dll, "VBVMR_AudioCallbackUnregister"));
        if (!login_ || !logout_ || !register_ || !start_ || !stop_ || !unregister_) {
            FreeLibrary(dll);   // gardée seulement si elle est complète
            error = "cette version de Voicemeeter n'a pas d'audio callback : mets-la à jour";
            return false;
        }
        dll_ = dll;
    }
    if (!loggedIn_) {
        logLine("connexion à Voicemeeter");
        long status = login_();
        if (status < 0) {
            error = "connexion à Voicemeeter impossible (" + std::to_string(status) + ")";
            return false;
        }
        loggedIn_ = true;
    }
    return true;
}

bool Voicemeeter::startCallback(std::string& error) {
    if (!loggedIn_) {
        error = "pas connecté à Voicemeeter";
        return false;
    }
    if (!registered_) {
        char name[64] = {};
        std::strncpy(name, client_.c_str(), sizeof(name) - 1);
        logLine("enregistrement de l'audio callback");
        long status = register_(kVmModeInput, &Voicemeeter::callback, this, name);
        if (status == 1) {
            error = std::string("l'audio callback de Voicemeeter est déjà utilisé par « ") + name +
                    " » : ferme ce programme";
            return false;
        }
        if (status != 0) {
            error = "enregistrement auprès de Voicemeeter refusé (" + std::to_string(status) + ")";
            return false;
        }
        registered_ = true;
    }
    logLine("démarrage de l'audio callback");
    long status = start_();
    if (status != 0) {
        error = "démarrage de l'audio callback refusé (" + std::to_string(status) +
                ") : Voicemeeter est-il lancé ?";
        return false;
    }
    return true;
}

void Voicemeeter::stopCallback() {
    if (registered_) {
        stop_();
        unregister_();
        registered_ = false;
    }
}

void Voicemeeter::close() {
    stopCallback();
    if (loggedIn_) {
        logout_();
        loggedIn_ = false;
    }
    if (dll_) {
        FreeLibrary(dll_);
        dll_ = nullptr;
    }
}

long __stdcall Voicemeeter::callback(void* user, long command, void* data, long) {
    auto* self = static_cast<Voicemeeter*>(user);
    switch (command) {
        case kVmCommandStarting: {
            auto* info = static_cast<VmAudioInfo*>(data);
            if (self->onStarted && info) self->onStarted(info->samplerate, info->nbSamplePerFrame);
            break;
        }
        case kVmCommandEnding:
            if (self->onStopped) self->onStopped();
            break;
        case kVmCommandChange:
            if (self->onChanged) self->onChanged();
            break;
        case kVmCommandBufferIn:
            if (self->onBuffer) self->onBuffer(static_cast<VmAudioBuffer*>(data));
            break;
        default:
            break;
    }
    return 0;
}

}  // namespace reka
