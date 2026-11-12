// Plugins VST3 (.vst3), hébergés avec les interfaces publiques de Steinberg
// (pluginterfaces, licence MIT) : pas besoin du reste du SDK.
#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "plugin.h"
#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/vst/ivstattributes.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "pluginterfaces/vst/vstspeaker.h"

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace reka {
namespace {

// Implémentation de queryInterface / addRef / release
#define REKA_REFCOUNT                                            \
    std::atomic<int32> refs_{1};                                 \
    uint32 PLUGIN_API addRef() override { return ++refs_; }      \
    uint32 PLUGIN_API release() override {                       \
        int32 left = --refs_;                                    \
        if (left == 0) delete this;                              \
        return (uint32)left;                                     \
    }

#define REKA_STATIC_REFCOUNT                                \
    uint32 PLUGIN_API addRef() override { return 1000; }    \
    uint32 PLUGIN_API release() override { return 1000; }

template <typename T>
bool matches(const TUID iid, T* self, void** obj) {
    if (FUnknownPrivate::iidEqual(iid, T::iid)) {
        self->addRef();
        *obj = self;
        return true;
    }
    return false;
}

std::string uidToHex(const TUID uid) {
    static const char digits[] = "0123456789ABCDEF";
    std::string out;
    for (int i = 0; i < 16; ++i) {
        out += digits[((uint8_t)uid[i]) >> 4];
        out += digits[((uint8_t)uid[i]) & 15];
    }
    return out;
}

// ---------------------------------------------------------------- flux mémoire
class MemoryStream final : public IBStream {
public:
    MemoryStream() = default;
    explicit MemoryStream(const uint8_t* data, size_t size) : data_(data, data + size) {}

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IBStream>(iid, this, obj) || matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    tresult PLUGIN_API read(void* buffer, int32 n, int32* done) override {
        int64 left = (int64)data_.size() - pos_;
        int32 count = (int32)std::max<int64>(0, std::min<int64>(n, left));
        if (count > 0) std::memcpy(buffer, data_.data() + pos_, (size_t)count);
        pos_ += count;
        if (done) *done = count;
        return kResultOk;
    }
    tresult PLUGIN_API write(void* buffer, int32 n, int32* done) override {
        if (n < 0) return kInvalidArgument;
        if (pos_ + n > (int64)data_.size()) data_.resize((size_t)(pos_ + n));
        if (n > 0) std::memcpy(data_.data() + pos_, buffer, (size_t)n);
        pos_ += n;
        if (done) *done = n;
        return kResultOk;
    }
    tresult PLUGIN_API seek(int64 pos, int32 mode, int64* result) override {
        int64 base = mode == kIBSeekSet ? 0 : mode == kIBSeekCur ? pos_ : (int64)data_.size();
        int64 target = base + pos;
        if (target < 0) return kInvalidArgument;
        pos_ = target;
        if (result) *result = pos_;
        return kResultOk;
    }
    tresult PLUGIN_API tell(int64* pos) override {
        if (pos) *pos = pos_;
        return kResultOk;
    }

    const std::vector<uint8_t>& data() const { return data_; }
    void rewind() { pos_ = 0; }

private:
    std::vector<uint8_t> data_;
    int64 pos_ = 0;
};

// ---------------------------------------------------- messages (IConnectionPoint)
class AttributeList final : public IAttributeList {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IAttributeList>(iid, this, obj) ||
            matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_REFCOUNT

    tresult PLUGIN_API setInt(AttrID id, int64 value) override {
        ints_[id] = value;
        return kResultOk;
    }
    tresult PLUGIN_API getInt(AttrID id, int64& value) override {
        auto it = ints_.find(id);
        if (it == ints_.end()) return kResultFalse;
        value = it->second;
        return kResultOk;
    }
    tresult PLUGIN_API setFloat(AttrID id, double value) override {
        floats_[id] = value;
        return kResultOk;
    }
    tresult PLUGIN_API getFloat(AttrID id, double& value) override {
        auto it = floats_.find(id);
        if (it == floats_.end()) return kResultFalse;
        value = it->second;
        return kResultOk;
    }
    tresult PLUGIN_API setString(AttrID id, const TChar* string) override {
        std::u16string text;
        for (; string && *string; ++string) text += (char16_t)*string;
        strings_[id] = text;
        return kResultOk;
    }
    tresult PLUGIN_API getString(AttrID id, TChar* string, uint32 sizeInBytes) override {
        auto it = strings_.find(id);
        if (it == strings_.end() || sizeInBytes < sizeof(TChar)) return kResultFalse;
        size_t max = sizeInBytes / sizeof(TChar) - 1;
        size_t n = std::min(max, it->second.size());
        for (size_t i = 0; i < n; ++i) string[i] = (TChar)it->second[i];
        string[n] = 0;
        return kResultOk;
    }
    tresult PLUGIN_API setBinary(AttrID id, const void* data, uint32 sizeInBytes) override {
        auto* bytes = static_cast<const uint8_t*>(data);
        binaries_[id] = std::vector<uint8_t>(bytes, bytes + sizeInBytes);
        return kResultOk;
    }
    tresult PLUGIN_API getBinary(AttrID id, const void*& data, uint32& sizeInBytes) override {
        auto it = binaries_.find(id);
        if (it == binaries_.end()) return kResultFalse;
        data = it->second.data();
        sizeInBytes = (uint32)it->second.size();
        return kResultOk;
    }

private:
    std::map<std::string, int64> ints_;
    std::map<std::string, double> floats_;
    std::map<std::string, std::u16string> strings_;
    std::map<std::string, std::vector<uint8_t>> binaries_;
};

class Message final : public IMessage {
public:
    Message() : attributes_(new AttributeList) {}
    ~Message() { attributes_->release(); }

    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IMessage>(iid, this, obj) || matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_REFCOUNT

    FIDString PLUGIN_API getMessageID() override { return id_.c_str(); }
    void PLUGIN_API setMessageID(FIDString id) override { id_ = id ? id : ""; }
    IAttributeList* PLUGIN_API getAttributes() override { return attributes_; }

private:
    std::string id_;
    AttributeList* attributes_;
};

// ---------------------------------------------------------- hôte (IHostApplication)
class HostApplication final : public IHostApplication {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IHostApplication>(iid, this, obj) ||
            matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    tresult PLUGIN_API getName(String128 name) override {
        const char* text = "REKA";
        int i = 0;
        for (; text[i] && i < 127; ++i) name[i] = (char16)text[i];
        name[i] = 0;
        return kResultOk;
    }
    tresult PLUGIN_API createInstance(TUID cid, TUID iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(cid, IMessage::iid) &&
            FUnknownPrivate::iidEqual(iid, IMessage::iid)) {
            *obj = static_cast<IMessage*>(new Message);
            return kResultOk;
        }
        if (FUnknownPrivate::iidEqual(cid, IAttributeList::iid) &&
            FUnknownPrivate::iidEqual(iid, IAttributeList::iid)) {
            *obj = static_cast<IAttributeList*>(new AttributeList);
            return kResultOk;
        }
        *obj = nullptr;
        return kResultFalse;
    }
};

HostApplication gHost;

// ------------------------------------------------- changements de paramètres
class ParamQueue final : public IParamValueQueue {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IParamValueQueue>(iid, this, obj) ||
            matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    ParamID PLUGIN_API getParameterId() override { return id_; }
    int32 PLUGIN_API getPointCount() override { return count_; }
    tresult PLUGIN_API getPoint(int32 index, int32& offset, ParamValue& value) override {
        if (index < 0 || index >= count_) return kResultFalse;
        offset = offsets_[index];
        value = values_[index];
        return kResultOk;
    }
    tresult PLUGIN_API addPoint(int32 offset, ParamValue value, int32& index) override {
        if (count_ >= kMaxPoints) return kResultFalse;
        offsets_[count_] = offset;
        values_[count_] = value;
        index = count_++;
        return kResultOk;
    }

    void reset(ParamID id) {
        id_ = id;
        count_ = 0;
    }

private:
    static constexpr int32 kMaxPoints = 16;
    ParamID id_ = 0;
    int32 count_ = 0;
    int32 offsets_[kMaxPoints] = {};
    ParamValue values_[kMaxPoints] = {};
};

class ParamChanges final : public IParameterChanges {
public:
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IParameterChanges>(iid, this, obj) ||
            matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    int32 PLUGIN_API getParameterCount() override { return count_; }
    IParamValueQueue* PLUGIN_API getParameterData(int32 index) override {
        return index >= 0 && index < count_ ? &queues_[index] : nullptr;
    }
    IParamValueQueue* PLUGIN_API addParameterData(const ParamID& id, int32& index) override {
        for (int32 i = 0; i < count_; ++i) {
            if (queues_[i].getParameterId() == id) {
                index = i;
                return &queues_[i];
            }
        }
        if (count_ >= kMaxQueues) return nullptr;
        queues_[count_].reset(id);
        index = count_;
        return &queues_[count_++];
    }
    void clear() { count_ = 0; }

private:
    static constexpr int32 kMaxQueues = 128;
    ParamQueue queues_[kMaxQueues];
    int32 count_ = 0;
};

// File sans verrou : interface (fil principal) → fil audio
class ParamFifo {
public:
    bool push(ParamID id, ParamValue value) {
        uint32_t w = write_.load(std::memory_order_relaxed);
        uint32_t next = (w + 1) % kSize;
        if (next == read_.load(std::memory_order_acquire)) return false;
        items_[w] = {id, value};
        write_.store(next, std::memory_order_release);
        return true;
    }
    bool pop(ParamID& id, ParamValue& value) {
        uint32_t r = read_.load(std::memory_order_relaxed);
        if (r == write_.load(std::memory_order_acquire)) return false;
        id = items_[r].id;
        value = items_[r].value;
        read_.store((r + 1) % kSize, std::memory_order_release);
        return true;
    }

private:
    static constexpr uint32_t kSize = 1024;
    struct Item {
        ParamID id;
        ParamValue value;
    } items_[kSize] = {};
    std::atomic<uint32_t> write_{0}, read_{0};
};

class Vst3Plugin;

// --------------------------------------------- liens plugin → hôte (édition, taille)
class ComponentHandler final : public IComponentHandler {
public:
    explicit ComponentHandler(Vst3Plugin* owner) : owner_(owner) {}
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IComponentHandler>(iid, this, obj) ||
            matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    tresult PLUGIN_API beginEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API performEdit(ParamID id, ParamValue value) override;
    tresult PLUGIN_API endEdit(ParamID) override {
        HostHooks::dirty.store(true);
        return kResultOk;
    }
    tresult PLUGIN_API restartComponent(int32 flags) override;

private:
    Vst3Plugin* owner_;
};

class PlugFrame final : public IPlugFrame {
public:
    explicit PlugFrame(Plugin* owner) : owner_(owner) {}
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        if (matches<IPlugFrame>(iid, this, obj) || matches<FUnknown>(iid, (FUnknown*)this, obj))
            return kResultOk;
        *obj = nullptr;
        return kNoInterface;
    }
    REKA_STATIC_REFCOUNT

    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* size) override {
        if (!view || !size) return kInvalidArgument;
        if (HostHooks::resizeEditor)
            HostHooks::resizeEditor(owner_, size->getWidth(), size->getHeight());
        view->onSize(size);
        return kResultOk;
    }

private:
    Plugin* owner_;
};

// ---------------------------------------------------------------- module .vst3
using InitDllProc = bool(PLUGIN_API*)();
using GetFactoryProc = IPluginFactory*(PLUGIN_API*)();

std::wstring moduleFile(const std::string& path) {
    std::wstring wide = widen(path);
    DWORD attributes = GetFileAttributesW(wide.c_str());
    if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
        // Bundle : Nom.vst3\Contents\x86_64-win\Nom.vst3
        std::string stem = fileStem(path);
        return wide + L"\\Contents\\x86_64-win\\" + widen(stem) + L".vst3";
    }
    return wide;
}

// ---------------------------------------------------------------- plugin
class Vst3Plugin final : public Plugin {
public:
    Vst3Plugin(const PluginDesc& desc) : handler_(this), frame_(this) { desc_ = desc; }

    ~Vst3Plugin() override {
        closeEditor();
        unprepare();
        if (componentCP_ && controllerCP_) {
            componentCP_->disconnect(controllerCP_);
            controllerCP_->disconnect(componentCP_);
        }
        if (componentCP_) componentCP_->release();
        if (controllerCP_) controllerCP_->release();
        if (controller_) {
            controller_->setComponentHandler(nullptr);
            if (separateController_) controller_->terminate();
            controller_->release();
        }
        if (processor_) processor_->release();
        if (component_) {
            component_->terminate();
            component_->release();
        }
        if (factory_) factory_->release();
        if (module_) {
            auto exitDll = reinterpret_cast<InitDllProc>(GetProcAddress(module_, "ExitDll"));
            if (exitDll) exitDll();
            FreeLibrary(module_);
        }
    }

    bool load(std::string& error) {
        module_ = LoadLibraryExW(moduleFile(desc_.path).c_str(), nullptr,
                                 LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!module_) {
            error = "chargement impossible (" + std::to_string(GetLastError()) + ")";
            return false;
        }
        auto initDll = reinterpret_cast<InitDllProc>(GetProcAddress(module_, "InitDll"));
        if (initDll && !initDll()) {
            error = "le plugin VST3 a refusé de s'initialiser";
            return false;
        }
        auto getFactory =
            reinterpret_cast<GetFactoryProc>(GetProcAddress(module_, "GetPluginFactory"));
        factory_ = getFactory ? getFactory() : nullptr;
        if (!factory_) {
            error = "ce fichier n'est pas un plugin VST3";
            return false;
        }
        PFactoryInfo factoryInfo;
        if (factory_->getFactoryInfo(&factoryInfo) == kResultOk && desc_.vendor.empty())
            desc_.vendor = factoryInfo.vendor;

        // Classe à charger : celle demandée (uid), sinon le premier effet audio
        IPluginFactory2* factory2 = nullptr;
        factory_->queryInterface(IPluginFactory2::iid, (void**)&factory2);
        int32 count = factory_->countClasses();
        bool found = false;
        TUID cid = {};
        // Passes : 0 = la classe qui porte le nom demandé (plugin repris de REAPER),
        // 1 = un effet, 2 = n'importe quelle classe audio
        std::string wanted = desc_.name;
        for (char& c : wanted) c = (char)std::tolower((unsigned char)c);
        for (int pass = 0; pass < 3 && !found; ++pass) {
            if (pass == 0 && (!desc_.uid.empty() || wanted.empty())) continue;
            for (int32 i = 0; i < count; ++i) {
                PClassInfo info;
                if (factory_->getClassInfo(i, &info) != kResultOk) continue;
                if (std::strcmp(info.category, kVstAudioEffectClass) != 0) continue;
                if (!desc_.uid.empty() && uidToHex(info.cid) != desc_.uid) continue;
                if (pass == 0) {
                    std::string name = info.name;
                    for (char& c : name) c = (char)std::tolower((unsigned char)c);
                    if (name != wanted) continue;
                }
                bool instrument = false;
                if (factory2) {
                    PClassInfo2 info2;
                    if (factory2->getClassInfo2(i, &info2) == kResultOk)
                        instrument = std::strstr(info2.subCategories, "Instrument") != nullptr &&
                                     std::strstr(info2.subCategories, "Fx") == nullptr;
                }
                if (instrument && pass == 1) continue;  // un effet d'abord
                std::memcpy(cid, info.cid, sizeof(TUID));
                desc_.name = info.name;
                found = true;
                break;
            }
        }
        if (factory2) factory2->release();
        if (!found) {
            error = desc_.uid.empty() ? "aucun effet audio dans ce plugin VST3"
                                      : "effet introuvable dans ce plugin VST3";
            return false;
        }
        desc_.uid = uidToHex(cid);

        if (factory_->createInstance(cid, IComponent::iid, (void**)&component_) != kResultOk ||
            !component_) {
            error = "le plugin VST3 n'a pas pu être créé";
            return false;
        }
        if (component_->initialize(&gHost) != kResultOk) {
            error = "le plugin VST3 n'a pas pu s'initialiser";
            return false;
        }
        if (component_->queryInterface(IAudioProcessor::iid, (void**)&processor_) != kResultOk ||
            !processor_) {
            error = "ce plugin VST3 ne traite pas le son";
            return false;
        }
        // Contrôleur : dans le même objet, ou une classe à part
        if (component_->queryInterface(IEditController::iid, (void**)&controller_) != kResultOk ||
            !controller_) {
            controller_ = nullptr;
            TUID controllerCid;
            if (component_->getControllerClassId(controllerCid) == kResultOk &&
                factory_->createInstance(controllerCid, IEditController::iid,
                                         (void**)&controller_) == kResultOk &&
                controller_) {
                separateController_ = true;
                if (controller_->initialize(&gHost) != kResultOk) {
                    controller_->release();
                    controller_ = nullptr;
                }
            }
        }
        if (controller_) {
            controller_->setComponentHandler(&handler_);
            if (separateController_) {
                component_->queryInterface(IConnectionPoint::iid, (void**)&componentCP_);
                controller_->queryInterface(IConnectionPoint::iid, (void**)&controllerCP_);
                if (componentCP_ && controllerCP_) {
                    componentCP_->connect(controllerCP_);
                    controllerCP_->connect(componentCP_);
                }
                // L'interface part des réglages du processeur
                MemoryStream stream;
                if (component_->getState(&stream) == kResultOk) {
                    stream.rewind();
                    controller_->setComponentState(&stream);
                }
            }
        }
        setupBuses();
        return true;
    }

    bool prepare(double sampleRate, int maxBlock) override {
        unprepare();
        ProcessSetup setup{kRealtime, kSample32, maxBlock, sampleRate};
        if (processor_->setupProcessing(setup) != kResultOk) return false;
        if (component_->setActive(true) != kResultOk) return false;
        processor_->setProcessing(true);  // certains plugins renvoient kNotImplemented
        allocate(maxBlock);
        std::memset(&context_, 0, sizeof(context_));
        context_.sampleRate = sampleRate;
        context_.tempo = 120.0;
        context_.timeSigNumerator = 4;
        context_.timeSigDenominator = 4;
        context_.state = ProcessContext::kPlaying | ProcessContext::kTempoValid |
                         ProcessContext::kTimeSigValid | ProcessContext::kProjectTimeMusicValid;
        preparedRate_ = sampleRate;
        preparedBlock_ = maxBlock;
        prepared_.store(true, std::memory_order_release);
        return true;
    }

    void unprepare() override {
        if (!prepared_.exchange(false)) return;
        processor_->setProcessing(false);
        component_->setActive(false);
    }

    int latency() const override { return processor_ ? (int)processor_->getLatencySamples() : 0; }

    bool getState(std::vector<uint8_t>& out) override {
        MemoryStream componentState, controllerState;
        if (component_->getState(&componentState) != kResultOk) return false;
        if (controller_) controller_->getState(&controllerState);
        out.assign({'R', 'K', '3', '1'});
        appendBlock(out, componentState.data());
        appendBlock(out, controllerState.data());
        return true;
    }

    bool setState(const std::vector<uint8_t>& data) override {
        if (data.size() < 4 || std::memcmp(data.data(), "RK31", 4) != 0) return false;
        size_t pos = 4;
        std::vector<uint8_t> componentData, controllerData;
        if (!readBlock(data, pos, componentData) || !readBlock(data, pos, controllerData))
            return false;
        MemoryStream componentState(componentData.data(), componentData.size());
        if (component_->setState(&componentState) != kResultOk) return false;
        if (controller_) {
            componentState.rewind();
            controller_->setComponentState(&componentState);
            if (!controllerData.empty()) {
                MemoryStream controllerState(controllerData.data(), controllerData.size());
                controller_->setState(&controllerState);
            }
        }
        return true;
    }

    void setParams(const std::vector<std::pair<std::string, double>>& params) override {
        if (!controller_ || params.empty()) return;
        int32 count = controller_->getParameterCount();
        std::vector<ParameterInfo> infos;
        std::map<std::string, size_t> byTitle;
        for (int32 i = 0; i < count; ++i) {
            ParameterInfo info{};
            if (controller_->getParameterInfo(i, info) != kResultOk) continue;
            std::wstring title;
            for (const char16* c = info.title; *c; ++c) title += (wchar_t)*c;
            byTitle.emplace(narrow(title), infos.size());
            infos.push_back(info);
        }
        // Par nom si la plupart correspondent, sinon par position
        size_t matched = 0;
        for (const auto& p : params) matched += byTitle.count(p.first);
        bool byName = matched * 2 >= std::min(params.size(), infos.size());
        for (size_t i = 0; i < params.size(); ++i) {
            const ParameterInfo* info = nullptr;
            if (byName) {
                auto it = byTitle.find(params[i].first);
                if (it != byTitle.end()) info = &infos[it->second];
            } else if (i < infos.size()) {
                info = &infos[i];
            }
            if (!info || (info->flags & ParameterInfo::kIsReadOnly)) continue;
            controller_->setParamNormalized(info->id, params[i].second);
            fifo_.push(info->id, params[i].second);
        }
        // Un bloc de silence : le processeur prend les valeurs (et son état les garde)
        bool wasPrepared = isPrepared();
        if (!wasPrepared && !prepare(48000.0, 512)) return;
        int n = std::min(preparedBlock_, 512);
        std::vector<float> l((size_t)n), r((size_t)n), ol((size_t)n), orr((size_t)n);
        float* in[2] = {l.data(), r.data()};
        float* out[2] = {ol.data(), orr.data()};
        process(in, out, n);
    }

    bool hasEditor() const override { return controller_ != nullptr; }

    bool openEditor(HWND parent, int& width, int& height) override {
        if (!controller_) return false;
        closeEditor();
        view_ = controller_->createView(ViewType::kEditor);
        if (!view_) return false;
        if (view_->isPlatformTypeSupported(kPlatformTypeHWND) != kResultTrue) {
            view_->release();
            view_ = nullptr;
            return false;
        }
        view_->setFrame(&frame_);
        ViewRect rect;
        if (view_->getSize(&rect) != kResultOk || rect.getWidth() <= 0) rect = ViewRect(0, 0, 640, 400);
        if (view_->attached(parent, kPlatformTypeHWND) != kResultOk) {
            view_->setFrame(nullptr);
            view_->release();
            view_ = nullptr;
            return false;
        }
        width = rect.getWidth();
        height = rect.getHeight();
        return true;
    }

    void closeEditor() override {
        if (!view_) return;
        view_->removed();
        view_->setFrame(nullptr);
        view_->release();
        view_ = nullptr;
    }

    void process(float* const* in, float* const* out, int n) override {
        // Réglages changés dans l'interface depuis le dernier bloc
        changes_.clear();
        ParamID id;
        ParamValue value;
        while (fifo_.pop(id, value)) {
            int32 index = 0;
            if (IParamValueQueue* queue = changes_.addParameterData(id, index)) {
                int32 point = 0;
                if (queue->getPointCount() > 0) {
                    // garde la dernière valeur seulement
                    static_cast<ParamQueue*>(queue)->reset(id);
                }
                queue->addPoint(0, value, point);
            }
        }
        // Entrée principale : la strip ; les autres bus : du silence
        for (size_t b = 0; b < inBuses_.size(); ++b) {
            Bus& bus = inBuses_[b];
            for (int c = 0; c < bus.channels; ++c)
                bus.pointers[c] = (b == 0 && c < 2) ? in[c] : zeros_.data();
        }
        ProcessData data;
        data.processMode = kRealtime;
        data.symbolicSampleSize = kSample32;
        data.numSamples = n;
        data.numInputs = (int32)inBuffers_.size();
        data.numOutputs = (int32)outBuffers_.size();
        data.inputs = inBuffers_.empty() ? nullptr : inBuffers_.data();
        data.outputs = outBuffers_.empty() ? nullptr : outBuffers_.data();
        data.inputParameterChanges = &changes_;
        data.outputParameterChanges = &outChanges_;
        data.processContext = &context_;
        outChanges_.clear();
        if (outBuses_.empty() || outBuses_[0].channels == 0 ||
            processor_->process(data) != kResultOk) {
            std::memcpy(out[0], in[0], sizeof(float) * n);
            std::memcpy(out[1], in[1], sizeof(float) * n);
        } else {
            const Bus& main = outBuses_[0];
            std::memcpy(out[0], main.pointers[0], sizeof(float) * n);
            std::memcpy(out[1], main.pointers[main.channels > 1 ? 1 : 0], sizeof(float) * n);
        }
        context_.projectTimeSamples += n;
        context_.continousTimeSamples += n;
        context_.projectTimeMusic =
            (double)context_.projectTimeSamples / context_.sampleRate * context_.tempo / 60.0;
    }

    void onEdit(ParamID id, ParamValue value) {
        fifo_.push(id, value);
        HostHooks::dirty.store(true);
    }

private:
    struct Bus {
        int channels = 0;
        std::vector<float> memory;
        std::vector<float*> pointers;
    };

    void setupBuses() {
        int32 nIn = component_->getBusCount(kAudio, kInput);
        int32 nOut = component_->getBusCount(kAudio, kOutput);
        std::vector<SpeakerArrangement> ins((size_t)nIn), outs((size_t)nOut);
        for (int32 i = 0; i < nIn; ++i) processor_->getBusArrangement(kInput, i, ins[i]);
        for (int32 i = 0; i < nOut; ++i) processor_->getBusArrangement(kOutput, i, outs[i]);
        // Bus principaux en stéréo (refusé : on garde ce que le plugin propose)
        if (nIn > 0) ins[0] = SpeakerArr::kStereo;
        if (nOut > 0) outs[0] = SpeakerArr::kStereo;
        if (processor_->setBusArrangements(ins.empty() ? nullptr : ins.data(), nIn,
                                           outs.empty() ? nullptr : outs.data(),
                                           nOut) != kResultOk) {
            for (int32 i = 0; i < nIn; ++i) processor_->getBusArrangement(kInput, i, ins[i]);
            for (int32 i = 0; i < nOut; ++i) processor_->getBusArrangement(kOutput, i, outs[i]);
        }
        for (int32 i = 0; i < nIn; ++i) component_->activateBus(kAudio, kInput, i, i == 0);
        for (int32 i = 0; i < nOut; ++i) component_->activateBus(kAudio, kOutput, i, i == 0);
        int32 nEventIn = component_->getBusCount(kEvent, kInput);
        for (int32 i = 0; i < nEventIn; ++i) component_->activateBus(kEvent, kInput, i, false);
        inBuses_.assign((size_t)nIn, Bus{});
        outBuses_.assign((size_t)nOut, Bus{});
        for (int32 i = 0; i < nIn; ++i) inBuses_[i].channels = SpeakerArr::getChannelCount(ins[i]);
        for (int32 i = 0; i < nOut; ++i)
            outBuses_[i].channels = SpeakerArr::getChannelCount(outs[i]);
    }

    void allocate(int maxBlock) {
        zeros_.assign((size_t)maxBlock, 0.0f);
        inBuffers_.assign(inBuses_.size(), AudioBusBuffers{});
        outBuffers_.assign(outBuses_.size(), AudioBusBuffers{});
        for (size_t b = 0; b < inBuses_.size(); ++b) {
            inBuses_[b].pointers.assign((size_t)std::max(inBuses_[b].channels, 1), zeros_.data());
            inBuffers_[b].numChannels = inBuses_[b].channels;
            inBuffers_[b].channelBuffers32 = inBuses_[b].pointers.data();
        }
        for (size_t b = 0; b < outBuses_.size(); ++b) {
            Bus& bus = outBuses_[b];
            int channels = std::max(bus.channels, 1);
            bus.memory.assign((size_t)channels * maxBlock, 0.0f);
            bus.pointers.resize((size_t)channels);
            for (int c = 0; c < channels; ++c) bus.pointers[c] = bus.memory.data() + c * maxBlock;
            outBuffers_[b].numChannels = bus.channels;
            outBuffers_[b].channelBuffers32 = bus.pointers.data();
        }
    }

    static void appendBlock(std::vector<uint8_t>& out, const std::vector<uint8_t>& block) {
        uint32_t size = (uint32_t)block.size();
        auto* bytes = reinterpret_cast<const uint8_t*>(&size);
        out.insert(out.end(), bytes, bytes + 4);
        out.insert(out.end(), block.begin(), block.end());
    }

    static bool readBlock(const std::vector<uint8_t>& data, size_t& pos,
                          std::vector<uint8_t>& block) {
        uint32_t size = 0;
        if (pos + 4 > data.size()) return false;
        std::memcpy(&size, data.data() + pos, 4);
        pos += 4;
        if (pos + size > data.size()) return false;
        block.assign(data.begin() + (long)pos, data.begin() + (long)(pos + size));
        pos += size;
        return true;
    }

    HMODULE module_ = nullptr;
    IPluginFactory* factory_ = nullptr;
    IComponent* component_ = nullptr;
    IAudioProcessor* processor_ = nullptr;
    IEditController* controller_ = nullptr;
    bool separateController_ = false;
    IConnectionPoint* componentCP_ = nullptr;
    IConnectionPoint* controllerCP_ = nullptr;
    IPlugView* view_ = nullptr;
    ComponentHandler handler_;
    PlugFrame frame_;
    ParamFifo fifo_;
    ParamChanges changes_, outChanges_;
    ProcessContext context_{};
    std::vector<Bus> inBuses_, outBuses_;
    std::vector<AudioBusBuffers> inBuffers_, outBuffers_;
    std::vector<float> zeros_;
};

tresult PLUGIN_API ComponentHandler::performEdit(ParamID id, ParamValue value) {
    owner_->onEdit(id, value);
    return kResultOk;
}

tresult PLUGIN_API ComponentHandler::restartComponent(int32 flags) {
    if (flags & kLatencyChanged) HostHooks::latencyChanged.store(true);
    if (flags & kParamValuesChanged) HostHooks::dirty.store(true);
    return kResultOk;
}

}  // namespace

std::unique_ptr<Plugin> loadVst3(const PluginDesc& desc, std::string& error) {
    auto plugin = std::make_unique<Vst3Plugin>(desc);
    if (!plugin->load(error)) return nullptr;
    return plugin;
}

}  // namespace reka
