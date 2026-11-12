// Faux VoicemeeterRemote64.dll pour les tests : il appelle l'audio callback
// comme Voicemeeter Potato (34 canaux d'entrée), avec un signal connu (canal
// c = (c + 1) / 100), et écrit le premier échantillon rendu par chaque canal
// dans le fichier FAKE_VOICEMEETER_OUT. L'hôte le charge via
// REKA_VOICEMEETER_DLL.
#include <windows.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
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
void* gUser = nullptr;
std::atomic<bool> gRunning{false};
std::thread gThread;
constexpr int kChannels = 34, kBlock = 256;

void run() {
    Info info{48000, kBlock};
    gCallback(gUser, 1, &info, 0);
    std::vector<std::vector<float>> in(kChannels, std::vector<float>(kBlock)),
        out(kChannels, std::vector<float>(kBlock));
    const char* path = std::getenv("FAKE_VOICEMEETER_OUT");
    while (gRunning) {
        Buffer buffer{48000, kBlock, kChannels, 0, {}, {}};
        for (int c = 0; c < kChannels; ++c) {
            for (float& s : in[c]) s = (c + 1) / 100.0f;
            for (float& s : out[c]) s = -1.0f;
            buffer.r[c] = in[c].data();
            buffer.w[c] = out[c].data();
        }
        gCallback(gUser, 10, &buffer, 0);
        if (path) {
            std::string tmp = std::string(path) + ".tmp";
            if (FILE* f = std::fopen(tmp.c_str(), "w")) {
                for (int c = 0; c < kChannels; ++c) std::fprintf(f, "%d %.6f\n", c, out[c][kBlock - 1]);
                std::fclose(f);
                MoveFileExA(tmp.c_str(), path, MOVEFILE_REPLACE_EXISTING);
            }
        }
        Sleep(5);
    }
    gCallback(gUser, 2, nullptr, 0);
}

}  // namespace

extern "C" {
__declspec(dllexport) long __stdcall VBVMR_Login() { return 0; }
__declspec(dllexport) long __stdcall VBVMR_Logout() { return 0; }
__declspec(dllexport) long __stdcall VBVMR_AudioCallbackRegister(long, Callback callback,
                                                                 void* user, char*) {
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
