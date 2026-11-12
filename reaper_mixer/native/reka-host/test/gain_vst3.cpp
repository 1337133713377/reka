// Plugin VST3 de test : un gain (paramètre « Gain » 0 → 1, soit ×0 à ×2),
// en un seul objet (composant, processeur et contrôleur). Il permet de tester
// le chemin VST3 du vrai reka-host.exe : chargement, réglages, paramètres
// repris de REAPER, traitement du son.
#include <atomic>
#include <cstring>

#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vstspeaker.h"

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace {

// Identifiant de la classe (arbitraire, propre au test)
const TUID kClassId = INLINE_UID(0x524B4733, 0x47414E31, 0x52454B41, 0x54455354);
constexpr ParamID kGain = 0;

void copy16(String128 out, const char* text) {
    int i = 0;
    for (; text[i] && i < 127; ++i) out[i] = (char16)text[i];
    out[i] = 0;
}

class Gain final : public IComponent, public IAudioProcessor, public IEditController {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(iid, IComponent::iid) ||
            FUnknownPrivate::iidEqual(iid, IPluginBase::iid) ||
            FUnknownPrivate::iidEqual(iid, FUnknown::iid))
            *obj = static_cast<IComponent*>(this);
        else if (FUnknownPrivate::iidEqual(iid, IAudioProcessor::iid))
            *obj = static_cast<IAudioProcessor*>(this);
        else if (FUnknownPrivate::iidEqual(iid, IEditController::iid))
            *obj = static_cast<IEditController*>(this);
        else {
            *obj = nullptr;
            return kNoInterface;
        }
        addRef();
        return kResultOk;
    }
    uint32 PLUGIN_API addRef() override { return ++refs_; }
    uint32 PLUGIN_API release() override {
        uint32 left = --refs_;
        if (left == 0) delete this;
        return left;
    }

    // IPluginBase
    tresult PLUGIN_API initialize(FUnknown*) override { return kResultOk; }
    tresult PLUGIN_API terminate() override { return kResultOk; }

    // IComponent
    tresult PLUGIN_API getControllerClassId(TUID) override { return kNotImplemented; }
    tresult PLUGIN_API setIoMode(IoMode) override { return kResultOk; }
    int32 PLUGIN_API getBusCount(MediaType type, BusDirection) override {
        return type == kAudio ? 1 : 0;
    }
    tresult PLUGIN_API getBusInfo(MediaType type, BusDirection dir, int32 index,
                                  BusInfo& bus) override {
        if (type != kAudio || index != 0) return kInvalidArgument;
        bus.mediaType = kAudio;
        bus.direction = dir;
        bus.channelCount = 2;
        copy16(bus.name, dir == kInput ? "In" : "Out");
        bus.busType = kMain;
        bus.flags = BusInfo::kDefaultActive;
        return kResultOk;
    }
    tresult PLUGIN_API getRoutingInfo(RoutingInfo&, RoutingInfo&) override { return kNotImplemented; }
    tresult PLUGIN_API activateBus(MediaType, BusDirection, int32, TBool) override { return kResultOk; }
    tresult PLUGIN_API setActive(TBool) override { return kResultOk; }
    // Composant et contrôleur partagent setState / getState (un seul objet)
    tresult PLUGIN_API setState(IBStream* state) override {
        float value = 0;
        int32 read = 0;
        if (!state || state->read(&value, sizeof(value), &read) != kResultOk || read != 4)
            return kResultFalse;
        gain_ = value;
        shown_ = value;
        return kResultOk;
    }
    tresult PLUGIN_API getState(IBStream* state) override {
        float value = gain_;
        return state ? state->write(&value, sizeof(value), nullptr) : kInvalidArgument;
    }

    // IAudioProcessor
    tresult PLUGIN_API setBusArrangements(SpeakerArrangement* ins, int32 nIn,
                                          SpeakerArrangement* outs, int32 nOut) override {
        return nIn == 1 && nOut == 1 && ins[0] == SpeakerArr::kStereo &&
                       outs[0] == SpeakerArr::kStereo
                   ? kResultOk
                   : kResultFalse;
    }
    tresult PLUGIN_API getBusArrangement(BusDirection, int32 index,
                                         SpeakerArrangement& arr) override {
        if (index != 0) return kInvalidArgument;
        arr = SpeakerArr::kStereo;
        return kResultOk;
    }
    tresult PLUGIN_API canProcessSampleSize(int32 size) override {
        return size == kSample32 ? kResultOk : kResultFalse;
    }
    uint32 PLUGIN_API getLatencySamples() override { return 16; }
    tresult PLUGIN_API setupProcessing(ProcessSetup&) override { return kResultOk; }
    tresult PLUGIN_API setProcessing(TBool) override { return kResultOk; }
    tresult PLUGIN_API process(ProcessData& data) override {
        if (IParameterChanges* changes = data.inputParameterChanges) {
            for (int32 i = 0; i < changes->getParameterCount(); ++i) {
                IParamValueQueue* queue = changes->getParameterData(i);
                if (!queue || queue->getParameterId() != kGain) continue;
                int32 offset = 0;
                ParamValue value = 0;
                if (queue->getPoint(queue->getPointCount() - 1, offset, value) == kResultOk)
                    gain_ = (float)value;
            }
        }
        if (data.numInputs < 1 || data.numOutputs < 1) return kResultOk;
        float factor = 2.0f * gain_;
        for (int c = 0; c < 2; ++c)
            for (int32 s = 0; s < data.numSamples; ++s)
                data.outputs[0].channelBuffers32[c][s] = data.inputs[0].channelBuffers32[c][s] * factor;
        return kResultOk;
    }
    uint32 PLUGIN_API getTailSamples() override { return 0; }

    // IEditController
    tresult PLUGIN_API setComponentState(IBStream* state) override {
        float value = 0;
        int32 read = 0;
        if (state && state->read(&value, 4, &read) == kResultOk && read == 4) shown_ = value;
        return kResultOk;
    }
    int32 PLUGIN_API getParameterCount() override { return 1; }
    tresult PLUGIN_API getParameterInfo(int32 index, ParameterInfo& info) override {
        if (index != 0) return kInvalidArgument;
        std::memset(&info, 0, sizeof(info));
        info.id = kGain;
        copy16(info.title, "Gain");
        info.defaultNormalizedValue = 0.5;
        info.flags = ParameterInfo::kCanAutomate;
        return kResultOk;
    }
    tresult PLUGIN_API getParamStringByValue(ParamID, ParamValue, String128 s) override {
        copy16(s, "gain");
        return kResultOk;
    }
    tresult PLUGIN_API getParamValueByString(ParamID, TChar*, ParamValue&) override {
        return kResultFalse;
    }
    ParamValue PLUGIN_API normalizedParamToPlain(ParamID, ParamValue v) override { return v; }
    ParamValue PLUGIN_API plainParamToNormalized(ParamID, ParamValue v) override { return v; }
    ParamValue PLUGIN_API getParamNormalized(ParamID id) override { return id == kGain ? shown_ : 0; }
    tresult PLUGIN_API setParamNormalized(ParamID id, ParamValue value) override {
        if (id == kGain) shown_ = value;
        return kResultOk;
    }
    tresult PLUGIN_API setComponentHandler(IComponentHandler*) override { return kResultOk; }
    IPlugView* PLUGIN_API createView(FIDString) override { return nullptr; }

private:
    virtual ~Gain() = default;
    std::atomic<uint32> refs_{1};
    float gain_ = 0.5f;      // processeur
    double shown_ = 0.5;     // contrôleur
};

class Factory final : public IPluginFactory {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(iid, IPluginFactory::iid) ||
            FUnknownPrivate::iidEqual(iid, FUnknown::iid)) {
            *obj = this;
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }
    tresult PLUGIN_API getFactoryInfo(PFactoryInfo* info) override {
        std::memset(info, 0, sizeof(*info));
        std::strcpy(info->vendor, "REKA");
        info->flags = PFactoryInfo::kUnicode;
        return kResultOk;
    }
    int32 PLUGIN_API countClasses() override { return 1; }
    tresult PLUGIN_API getClassInfo(int32 index, PClassInfo* info) override {
        if (index != 0) return kInvalidArgument;
        std::memset(info, 0, sizeof(*info));
        std::memcpy(info->cid, kClassId, sizeof(TUID));
        info->cardinality = PClassInfo::kManyInstances;
        std::strcpy(info->category, kVstAudioEffectClass);
        std::strcpy(info->name, "REKA Test Gain 3");
        return kResultOk;
    }
    tresult PLUGIN_API createInstance(FIDString cid, FIDString iid, void** obj) override {
        if (!FUnknownPrivate::iidEqual(cid, kClassId)) {
            *obj = nullptr;
            return kInvalidArgument;
        }
        auto* plugin = new Gain;
        tresult result = plugin->queryInterface(iid, obj);
        plugin->release();
        return result;
    }
};

Factory gFactory;

}  // namespace

extern "C" {
__declspec(dllexport) bool PLUGIN_API InitDll() { return true; }
__declspec(dllexport) bool PLUGIN_API ExitDll() { return true; }
__declspec(dllexport) IPluginFactory* PLUGIN_API GetPluginFactory() { return &gFactory; }
}
