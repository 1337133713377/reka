// Plugin chargé par l'hôte : interface commune aux formats VST2 et VST3.
//
// Fils d'exécution :
//   - fil principal (fenêtres) : chargement, préparation, réglages, interface ;
//   - fil audio (callback de Voicemeeter) : process() seulement.
// Le moteur ne modifie jamais une chaîne pendant que le fil audio la parcourt
// (voir Engine), et un plugin n'est traité qu'une fois préparé.
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace reka {

struct PluginDesc {
    std::string format;  // "VST2" ou "VST3"
    std::string path;    // UTF-8
    std::string uid;     // VST2 : identifiant (plugins « shell ») ; VST3 : classe (hex)
    std::string name;
    std::string vendor;
};

class Plugin {
public:
    virtual ~Plugin() = default;

    const PluginDesc& desc() const { return desc_; }

    // ---- fil principal
    // (Re)prépare le traitement ; false si le plugin refuse ces réglages.
    virtual bool prepare(double sampleRate, int maxBlock) = 0;
    virtual void unprepare() = 0;
    virtual int latency() const = 0;
    virtual bool getState(std::vector<uint8_t>& out) = 0;
    virtual bool setState(const std::vector<uint8_t>& data) = 0;
    virtual bool hasEditor() const = 0;
    // Ouvre l'interface dans `parent` ; renvoie sa taille.
    virtual bool openEditor(HWND parent, int& width, int& height) = 0;
    virtual void closeEditor() = 0;
    virtual void editorIdle() {}

    // ---- fil audio : stéréo, numSamples <= maxBlock de prepare()
    virtual void process(float* const* in, float* const* out, int numSamples) = 0;

    bool isPrepared() const { return prepared_.load(std::memory_order_acquire); }
    double preparedRate() const { return preparedRate_; }
    int preparedBlock() const { return preparedBlock_; }

protected:
    PluginDesc desc_;
    std::atomic<bool> prepared_{false};
    double preparedRate_ = 0;
    int preparedBlock_ = 0;
};

// Liens du plugin vers l'hôte (appelés depuis n'importe quel fil).
struct HostHooks {
    // Un réglage a changé dans l'interface d'un plugin : REKA relira les chaînes.
    static std::atomic<bool> dirty;
    // Taille de la fenêtre d'un plugin à changer (fil principal).
    static void (*resizeEditor)(Plugin* plugin, int width, int height);
    // Latence d'un plugin changée : l'état sera renvoyé à REKA.
    static std::atomic<bool> latencyChanged;
    // Réglages courants du moteur (lus par les plugins VST2 via le callback hôte).
    static std::atomic<double> sampleRate;
    static std::atomic<int> blockSize;
};

std::unique_ptr<Plugin> loadVst2(const PluginDesc& desc, std::string& error);
std::unique_ptr<Plugin> loadVst3(const PluginDesc& desc, std::string& error);
std::unique_ptr<Plugin> loadPlugin(const PluginDesc& desc, std::string& error);

// ---- utilitaires
std::wstring widen(const std::string& utf8);
std::string narrow(const std::wstring& wide);
std::string fileStem(const std::string& path);
std::string base64Encode(const std::vector<uint8_t>& data);
bool base64Decode(const std::string& text, std::vector<uint8_t>& out);
void logLine(const std::string& text);
void setLogFile(const std::wstring& path);

}  // namespace reka
