// Plugin VST2 de test : un gain (paramètre 0 → 1, soit ×0 à ×2).
// Construit avec l'hôte (option REKA_TEST_PLUGIN) pour que les tests
// chargent un vrai plugin : ajout, réglages, copie, chaînes.
#include <cstring>

#include "../src/vst2_abi.h"

namespace {

struct Gain {
    vst2::AEffect effect{};
    vst2::HostCallback host = nullptr;
    float gain = 0.5f;
};

intptr_t VST2_CALLBACK dispatcher(vst2::AEffect* e, int32_t opcode, int32_t, intptr_t, void* ptr,
                                  float) {
    auto* self = static_cast<Gain*>(e->object);
    switch (opcode) {
        case vst2::effClose:
            delete self;
            return 1;
        case vst2::effGetEffectName:
            std::strcpy(static_cast<char*>(ptr), "REKA Test Gain");
            return 1;
        case vst2::effGetVendorString:
            std::strcpy(static_cast<char*>(ptr), "REKA");
            return 1;
        case vst2::effGetVstVersion:
            return 2400;
        default:
            return 0;
    }
}

void VST2_CALLBACK setParameter(vst2::AEffect* e, int32_t index, float value) {
    if (index == 0) static_cast<Gain*>(e->object)->gain = value;
}

float VST2_CALLBACK getParameter(vst2::AEffect* e, int32_t index) {
    return index == 0 ? static_cast<Gain*>(e->object)->gain : 0.0f;
}

void VST2_CALLBACK processReplacing(vst2::AEffect* e, float** in, float** out, int32_t n) {
    float factor = 2.0f * static_cast<Gain*>(e->object)->gain;
    for (int c = 0; c < 2; ++c)
        for (int32_t i = 0; i < n; ++i) out[c][i] = in[c][i] * factor;
}

}  // namespace

extern "C" __declspec(dllexport) vst2::AEffect* VSTPluginMain(vst2::HostCallback host) {
    if (!host || host(nullptr, vst2::audioMasterVersion, 0, 0, nullptr, 0.0f) == 0) return nullptr;
    auto* self = new Gain;
    self->host = host;
    vst2::AEffect& e = self->effect;
    e.magic = vst2::kEffectMagic;
    e.dispatcher = dispatcher;
    e.setParameter = setParameter;
    e.getParameter = getParameter;
    e.numPrograms = 1;
    e.numParams = 1;
    e.numInputs = 2;
    e.numOutputs = 2;
    e.flags = vst2::effFlagsCanReplacing;
    e.initialDelay = 32;
    e.object = self;
    e.uniqueID = 0x524B4731;  // 'RKG1'
    e.version = 1;
    e.processReplacing = processReplacing;
    return &e;
}
