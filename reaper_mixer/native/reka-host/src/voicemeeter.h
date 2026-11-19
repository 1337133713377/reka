// Branchement sur Voicemeeter par son « audio callback » (API Remote).
//
// En mode « entrées » (VBVMR_AUDIOCALLBACK_IN), Voicemeeter confie à REKA le
// son de chaque entrée, à l'endroit de l'insert : REKA le traite puis le
// rend, dans le même bloc. Pas de pilote ASIO à choisir, pas de tampon en
// plus. Disposition des canaux (identique à l'insert ASIO) : 2 canaux par
// entrée physique, puis 8 par entrée virtuelle.
//
// En mode « sorties » (VBVMR_AUDIOCALLBACK_OUT), c'est le son de chaque bus,
// juste avant son périphérique : 8 canaux par bus (A1, A2…, puis B1…). REKA
// s'en sert pour l'écoute au casque (surround virtuel, égalisation).
#pragma once

#include <functional>
#include <string>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace reka {

// Structures de VoicemeeterRemote.h (VB-Audio)
struct VmAudioInfo {
    long samplerate;
    long nbSamplePerFrame;
};

struct VmAudioBuffer {
    long audiobuffer_sr;
    long audiobuffer_nbs;
    long audiobuffer_nbi;
    long audiobuffer_nbo;
    float* audiobuffer_r[128];
    float* audiobuffer_w[128];
};

constexpr long kVmModeInput = 0x00000001;
constexpr long kVmModeOutput = 0x00000002;
constexpr long kVmCommandStarting = 1;
constexpr long kVmCommandEnding = 2;
constexpr long kVmCommandChange = 3;
constexpr long kVmCommandBufferIn = 10;
constexpr long kVmCommandBufferOut = 11;

class Voicemeeter {
public:
    using StartedFn = std::function<void(double rate, int block)>;
    using StoppedFn = std::function<void()>;
    using BufferFn = std::function<void(VmAudioBuffer* buffer)>;

    ~Voicemeeter();

    // Charge VoicemeeterRemote64.dll et se connecte ; false + message sinon.
    bool open(const std::string& client, std::string& error);
    // Enregistre (modes kVmModeInput / kVmModeOutput) et démarre le
    // callback ; false + message sinon (un autre programme peut déjà
    // l'utiliser).
    bool startCallback(long mode, std::string& error);
    long mode() const { return registered_ ? mode_ : 0; }
    void stopCallback();
    void close();

    // Appelés depuis le fil audio de Voicemeeter
    StartedFn onStarted;
    StoppedFn onStopped;
    StoppedFn onChanged;  // fréquence ou taille de bloc changée : relancer
    BufferFn onBuffer;     // entrées
    BufferFn onBufferOut;  // sorties (bus)

private:
    using LoginFn = long(__stdcall*)();
    using LogoutFn = long(__stdcall*)();
    using CallbackFn = long(__stdcall*)(void* user, long command, void* data, long nnn);
    using RegisterFn = long(__stdcall*)(long mode, CallbackFn callback, void* user,
                                        char clientName[64]);
    using SimpleFn = long(__stdcall*)();

    static long __stdcall callback(void* user, long command, void* data, long nnn);

    HMODULE dll_ = nullptr;
    LoginFn login_ = nullptr;
    LogoutFn logout_ = nullptr;
    RegisterFn register_ = nullptr;
    SimpleFn start_ = nullptr;
    SimpleFn stop_ = nullptr;
    SimpleFn unregister_ = nullptr;
    bool loggedIn_ = false;
    bool registered_ = false;
    long mode_ = 0;
    std::string client_;
};

}  // namespace reka
