// Plugins VST2 (.dll 64 bits).
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <string>

#include "plugin.h"
#include "vst2_abi.h"

namespace reka {
namespace {

// Identifiant demandé pendant le chargement d'un plugin « shell » (Waves…)
thread_local int32_t gLoadingShellId = 0;

constexpr char kParamsMagic[4] = {'R', 'K', 'P', '2'};  // réglages = valeurs des paramètres
constexpr char kChunkMagic[4] = {'R', 'K', 'C', '2'};   // réglages = « chunk » du plugin

class Vst2Plugin final : public Plugin {
public:
    Vst2Plugin(HMODULE module, vst2::AEffect* effect, const PluginDesc& desc)
        : module_(module), effect_(effect) {
        desc_ = desc;
        effect_->user = this;
        dispatch(vst2::effOpen);
        char text[256] = {};
        if (dispatch(vst2::effGetEffectName, 0, 0, text) && text[0]) desc_.name = text;
        if (desc_.name.empty()) desc_.name = fileStem(desc.path);
        std::memset(text, 0, sizeof(text));
        if (dispatch(vst2::effGetVendorString, 0, 0, text) && text[0]) desc_.vendor = text;
        if (desc_.uid.empty() && effect_->uniqueID) desc_.uid = std::to_string(effect_->uniqueID);
        std::memset(&time_, 0, sizeof(time_));
        time_.tempo = 120.0;
        time_.timeSigNumerator = 4;
        time_.timeSigDenominator = 4;
        time_.flags = vst2::kVstTempoValid | vst2::kVstTimeSigValid | vst2::kVstPpqPosValid |
                      vst2::kVstTransportPlaying;
    }

    ~Vst2Plugin() override {
        closeEditor();
        unprepare();
        dispatch(vst2::effClose);
        effect_ = nullptr;
        if (module_) FreeLibrary(module_);
    }

    bool prepare(double sampleRate, int maxBlock) override {
        unprepare();
        dispatch(vst2::effSetSampleRate, 0, 0, nullptr, (float)sampleRate);
        dispatch(vst2::effSetBlockSize, 0, maxBlock);
        dispatch(vst2::effMainsChanged, 0, 1);
        dispatch(vst2::effStartProcess);
        int ins = std::max(effect_->numInputs, 0), outs = std::max(effect_->numOutputs, 0);
        zeros_.assign((size_t)maxBlock, 0.0f);
        scratch_.assign((size_t)std::max(outs, 1) * maxBlock, 0.0f);
        inPtrs_.assign((size_t)std::max(ins, 2), zeros_.data());
        outPtrs_.resize((size_t)std::max(outs, 1));
        for (size_t c = 0; c < outPtrs_.size(); ++c) outPtrs_[c] = scratch_.data() + c * maxBlock;
        time_.sampleRate = sampleRate;
        time_.samplePos = 0;
        preparedRate_ = sampleRate;
        preparedBlock_ = maxBlock;
        prepared_.store(true, std::memory_order_release);
        return true;
    }

    void unprepare() override {
        if (!prepared_.exchange(false)) return;
        dispatch(vst2::effStopProcess);
        dispatch(vst2::effMainsChanged, 0, 0);
    }

    int latency() const override { return effect_ ? std::max(effect_->initialDelay, 0) : 0; }

    bool getState(std::vector<uint8_t>& out) override {
        out.clear();
        if (effect_->flags & vst2::effFlagsProgramChunks) {
            void* data = nullptr;
            intptr_t size = dispatch(vst2::effGetChunk, 0, 0, &data);
            if (size > 0 && data) {
                out.insert(out.end(), kChunkMagic, kChunkMagic + 4);
                auto* bytes = static_cast<const uint8_t*>(data);
                out.insert(out.end(), bytes, bytes + size);
                return true;
            }
        }
        out.insert(out.end(), kParamsMagic, kParamsMagic + 4);
        int32_t program = (int32_t)dispatch(vst2::effGetProgram);
        append(out, program);
        int32_t count = std::max(effect_->numParams, 0);
        append(out, count);
        for (int32_t i = 0; i < count; ++i) append(out, effect_->getParameter(effect_, i));
        return true;
    }

    bool setState(const std::vector<uint8_t>& data) override {
        if (data.size() < 4) return false;
        if (std::memcmp(data.data(), kChunkMagic, 4) == 0) {
            std::vector<uint8_t> chunk(data.begin() + 4, data.end());
            dispatch(vst2::effSetChunk, 0, (intptr_t)chunk.size(), chunk.data());
            return true;
        }
        if (std::memcmp(data.data(), kParamsMagic, 4) != 0) return false;
        size_t pos = 4;
        int32_t program = 0, count = 0;
        if (!read(data, pos, program) || !read(data, pos, count)) return false;
        if (program >= 0 && program < effect_->numPrograms)
            dispatch(vst2::effSetProgram, 0, program);
        count = std::min(count, effect_->numParams);
        for (int32_t i = 0; i < count; ++i) {
            float value = 0;
            if (!read(data, pos, value)) return false;
            effect_->setParameter(effect_, i, value);
        }
        return true;
    }

    bool setChunk(const std::vector<uint8_t>& data) override {
        if (!(effect_->flags & vst2::effFlagsProgramChunks) || data.empty()) return false;
        std::vector<uint8_t> chunk(data);
        dispatch(vst2::effSetChunk, 0, (intptr_t)chunk.size(), chunk.data());
        return true;
    }

    void setParams(const std::vector<std::pair<std::string, double>>& params) override {
        // Même ordre que dans le plugin (REAPER ajoute ses propres paramètres à la fin)
        int32_t count = std::min<int32_t>((int32_t)params.size(), effect_->numParams);
        for (int32_t i = 0; i < count; ++i)
            effect_->setParameter(effect_, i, (float)params[(size_t)i].second);
    }

    bool hasEditor() const override { return effect_->flags & vst2::effFlagsHasEditor; }

    bool openEditor(HWND parent, int& width, int& height) override {
        if (!hasEditor()) return false;
        vst2::ERect* rect = nullptr;
        dispatch(vst2::effEditGetRect, 0, 0, &rect);  // certains plugins veulent ce premier appel
        if (!dispatch(vst2::effEditOpen, 0, 0, parent)) {
            // Beaucoup de plugins renvoient 0 même quand tout va bien
        }
        editorOpen_ = true;
        rect = nullptr;
        dispatch(vst2::effEditGetRect, 0, 0, &rect);
        width = rect ? rect->right - rect->left : 640;
        height = rect ? rect->bottom - rect->top : 400;
        if (width <= 0 || height <= 0) width = 640, height = 400;
        return true;
    }

    void closeEditor() override {
        if (!editorOpen_) return;
        editorOpen_ = false;
        dispatch(vst2::effEditClose);
    }

    void editorIdle() override {
        if (editorOpen_) dispatch(vst2::effEditIdle);
    }

    void process(float* const* in, float* const* out, int n) override {
        int ins = std::max(effect_->numInputs, 0), outs = std::max(effect_->numOutputs, 0);
        if (outs == 0) {  // rien à sortir : le son passe tel quel
            std::memcpy(out[0], in[0], sizeof(float) * n);
            std::memcpy(out[1], in[1], sizeof(float) * n);
            return;
        }
        for (int c = 0; c < ins; ++c) inPtrs_[c] = c < 2 ? in[c] : zeros_.data();
        if (effect_->flags & vst2::effFlagsCanReplacing && effect_->processReplacing) {
            effect_->processReplacing(effect_, inPtrs_.data(), outPtrs_.data(), n);
        } else {
            for (int c = 0; c < outs; ++c) std::memset(outPtrs_[c], 0, sizeof(float) * n);
            effect_->process(effect_, inPtrs_.data(), outPtrs_.data(), n);
        }
        std::memcpy(out[0], outPtrs_[0], sizeof(float) * n);
        std::memcpy(out[1], outPtrs_[outs > 1 ? 1 : 0], sizeof(float) * n);
        time_.samplePos += n;
        time_.ppqPos = time_.samplePos / time_.sampleRate * time_.tempo / 60.0;
    }

    // Opcodes propres à ce plugin ; false = réponse générique de l'hôte.
    bool onHost(int32_t opcode, int32_t index, intptr_t value, intptr_t& result) {
        switch (opcode) {
            case vst2::audioMasterAutomate:
            case vst2::audioMasterEndEdit:
                HostHooks::dirty.store(true);
                result = 1;
                return true;
            case vst2::audioMasterBeginEdit:
                result = 1;
                return true;
            case vst2::audioMasterGetTime:
                result = (intptr_t)&time_;
                return true;
            case vst2::audioMasterSizeWindow:
                if (HostHooks::resizeEditor) HostHooks::resizeEditor(this, index, (int)value);
                result = 1;
                return true;
            case vst2::audioMasterIOChanged:
                HostHooks::latencyChanged.store(true);
                result = 1;
                return true;
            default:
                return false;
        }
    }

private:
    intptr_t dispatch(int32_t opcode, int32_t index = 0, intptr_t value = 0, void* ptr = nullptr,
                      float opt = 0.0f) const {
        return effect_ ? effect_->dispatcher(effect_, opcode, index, value, ptr, opt) : 0;
    }

    template <typename T>
    static void append(std::vector<uint8_t>& out, T value) {
        auto* bytes = reinterpret_cast<const uint8_t*>(&value);
        out.insert(out.end(), bytes, bytes + sizeof(T));
    }

    template <typename T>
    static bool read(const std::vector<uint8_t>& data, size_t& pos, T& value) {
        if (pos + sizeof(T) > data.size()) return false;
        std::memcpy(&value, data.data() + pos, sizeof(T));
        pos += sizeof(T);
        return true;
    }

    HMODULE module_ = nullptr;
    vst2::AEffect* effect_ = nullptr;
    bool editorOpen_ = false;
    std::vector<float> zeros_, scratch_;
    std::vector<float*> inPtrs_, outPtrs_;
    vst2::VstTimeInfo time_;
};

intptr_t VST2_CALLBACK hostCallback(vst2::AEffect* effect, int32_t opcode, int32_t index,
                                    intptr_t value, void* ptr, float opt) {
    (void)opt;
    intptr_t result = 0;
    if (effect && effect->user &&
        static_cast<Vst2Plugin*>(effect->user)->onHost(opcode, index, value, result))
        return result;
    switch (opcode) {
        case vst2::audioMasterVersion:
            return 2400;
        case vst2::audioMasterCurrentId:
            return gLoadingShellId;
        case vst2::audioMasterGetSampleRate:
            return (intptr_t)HostHooks::sampleRate.load();
        case vst2::audioMasterGetBlockSize:
            return HostHooks::blockSize.load();
        case vst2::audioMasterGetVendorString:
            if (ptr) std::strcpy(static_cast<char*>(ptr), "REKA");
            return 1;
        case vst2::audioMasterGetProductString:
            if (ptr) std::strcpy(static_cast<char*>(ptr), "REKA Host");
            return 1;
        case vst2::audioMasterGetVendorVersion:
            return 1;
        case vst2::audioMasterGetCurrentProcessLevel:
            return 0;
        case vst2::audioMasterCanDo:
            if (ptr) {
                const char* what = static_cast<const char*>(ptr);
                if (!std::strcmp(what, "sizeWindow") || !std::strcmp(what, "supplyIdle") ||
                    !std::strcmp(what, "shellCategory"))
                    return 1;
            }
            return 0;
        default:
            return 0;
    }
}

}  // namespace

std::unique_ptr<Plugin> loadVst2(const PluginDesc& desc, std::string& error) {
    HMODULE module = LoadLibraryExW(widen(desc.path).c_str(), nullptr,
                                    LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) {
        error = "chargement impossible (" + std::to_string(GetLastError()) + ")";
        return nullptr;
    }
    auto entry = reinterpret_cast<vst2::PluginEntry>(GetProcAddress(module, "VSTPluginMain"));
    if (!entry) entry = reinterpret_cast<vst2::PluginEntry>(GetProcAddress(module, "main"));
    if (!entry) {
        FreeLibrary(module);
        error = "ce fichier n'est pas un plugin VST2";
        return nullptr;
    }
    gLoadingShellId = desc.uid.empty() ? 0 : (int32_t)std::strtol(desc.uid.c_str(), nullptr, 10);
    vst2::AEffect* effect = entry(hostCallback);
    gLoadingShellId = 0;
    if (!effect || effect->magic != vst2::kEffectMagic) {
        FreeLibrary(module);
        error = "le plugin VST2 ne s'est pas initialisé";
        return nullptr;
    }
    if (effect->flags & vst2::effFlagsIsSynth) {
        effect->dispatcher(effect, vst2::effClose, 0, 0, nullptr, 0.0f);
        FreeLibrary(module);
        error = "c'est un instrument, pas un effet";
        return nullptr;
    }
    return std::make_unique<Vst2Plugin>(module, effect, desc);
}

}  // namespace reka
