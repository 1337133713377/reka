// Faux VoicemeeterRemote64.dll pour les tests : il appelle l'audio callback
// comme Voicemeeter Potato (34 canaux d'entrée, 64 de sortie : 8 bus de 8
// canaux), avec un signal connu (canal c = (c + 1) / 100), et écrit le
// dernier échantillon rendu par chaque canal dans le fichier
// FAKE_VOICEMEETER_OUT (entrées), et FAKE_VOICEMEETER_OUT_BUS (sorties, si le
// mode « sorties » est demandé). L'hôte le charge via REKA_VOICEMEETER_DLL.
#include <windows.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

namespace {

using Callback = long(__stdcall*)(void*, long, void*, long);

struct Info {
    long samplerate, nbSamplePerFrame;
};
struct Buffer {
    long sr, nbs, nbi, nbo;
    float* r[128];
    float* w[128];
};

Callback gCallback = nullptr;
long gMode = 0;
void* gUser = nullptr;
std::atomic<bool> gRunning{false};
std::thread gThread;
constexpr int kChannels = 34, kBusChannels = 64, kBlock = 256;

// Un bloc d'un mode : signal connu en entrée, -1 en sortie, puis résultat écrit
void block(long command, int channels, std::vector<std::vector<float>>& in,
           std::vector<std::vector<float>>& out, const char* path) {
    Buffer buffer{48000, kBlock, channels, channels, {}, {}};
    for (int c = 0; c < channels; ++c) {
        for (float& s : in[c]) s = (c + 1) / 100.0f;
        for (float& s : out[c]) s = -1.0f;
        buffer.r[c] = in[c].data();
        buffer.w[c] = out[c].data();
    }
    gCallback(gUser, command, &buffer, 0);
    if (!path) return;
    std::string tmp = std::string(path) + ".tmp";
    if (FILE* f = std::fopen(tmp.c_str(), "w")) {
        for (int c = 0; c < channels; ++c) std::fprintf(f, "%d %.6f\n", c, out[c][kBlock - 1]);
        std::fclose(f);
        MoveFileExA(tmp.c_str(), path, MOVEFILE_REPLACE_EXISTING);
    }
}

void run() {
    Info info{48000, kBlock};
    gCallback(gUser, 1, &info, 0);
    std::vector<std::vector<float>> in(kBusChannels, std::vector<float>(kBlock)),
        out(kBusChannels, std::vector<float>(kBlock));
    const char* path = std::getenv("FAKE_VOICEMEETER_OUT");
    const char* busPath = std::getenv("FAKE_VOICEMEETER_OUT_BUS");
    while (gRunning) {
        if (gMode & 1) block(10, kChannels, in, out, path);
        if (gMode & 2) block(11, kBusChannels, in, out, busPath);
        Sleep(5);
    }
    gCallback(gUser, 2, nullptr, 0);
}

}  // namespace

extern "C" {
__declspec(dllexport) long __stdcall VBVMR_Login() { return 0; }
__declspec(dllexport) long __stdcall VBVMR_Logout() { return 0; }
__declspec(dllexport) long __stdcall VBVMR_AudioCallbackRegister(long mode, Callback callback,
                                                                 void* user, char*) {
    gMode = mode;
    gCallback = callback;
    gUser = user;
    return 0;
}
__declspec(dllexport) long __stdcall VBVMR_AudioCallbackStart() {
    if (!gCallback || gRunning) return gCallback ? 0 : -1;
    gRunning = true;
    gThread = std::thread(run);
    return 0;
}
__declspec(dllexport) long __stdcall VBVMR_AudioCallbackStop() {
    if (gRunning) {
        gRunning = false;
        gThread.join();
    }
    return 0;
}
__declspec(dllexport) long __stdcall VBVMR_AudioCallbackUnregister() {
    gCallback = nullptr;
    return 0;
}
}
