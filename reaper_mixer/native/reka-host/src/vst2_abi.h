// Interface binaire des plugins VST 2.4, telle que les plugins l'exposent.
//
// Steinberg ne distribue plus le SDK VST2 : comme d'autres hôtes libres, REKA
// décrit lui-même la structure AEffect et les codes d'opération dont il a
// besoin (disposition mémoire et valeurs fixées par les plugins existants).
#pragma once

#include <cstdint>

namespace vst2 {

#if defined(_WIN32)
#define VST2_CALLBACK __cdecl
#else
#define VST2_CALLBACK
#endif

struct AEffect;

using HostCallback = intptr_t(VST2_CALLBACK*)(AEffect* effect, int32_t opcode, int32_t index,
                                              intptr_t value, void* ptr, float opt);
using DispatcherProc = intptr_t(VST2_CALLBACK*)(AEffect* effect, int32_t opcode, int32_t index,
                                                intptr_t value, void* ptr, float opt);
using ProcessProc = void(VST2_CALLBACK*)(AEffect* effect, float** inputs, float** outputs,
                                         int32_t sampleFrames);
using ProcessDoubleProc = void(VST2_CALLBACK*)(AEffect* effect, double** inputs,
                                               double** outputs, int32_t sampleFrames);
using SetParameterProc = void(VST2_CALLBACK*)(AEffect* effect, int32_t index, float parameter);
using GetParameterProc = float(VST2_CALLBACK*)(AEffect* effect, int32_t index);
using PluginEntry = AEffect*(VST2_CALLBACK*)(HostCallback host);

constexpr int32_t kEffectMagic = 0x56737450;  // 'VstP'

#pragma pack(push, 8)
struct AEffect {
    int32_t magic;
    DispatcherProc dispatcher;
    ProcessProc process;  // obsolète (accumulation)
    SetParameterProc setParameter;
    GetParameterProc getParameter;
    int32_t numPrograms;
    int32_t numParams;
    int32_t numInputs;
    int32_t numOutputs;
    int32_t flags;
    intptr_t resvd1;
    intptr_t resvd2;
    int32_t initialDelay;  // latence en échantillons
    int32_t realQualities;
    int32_t offQualities;
    float ioRatio;
    void* object;
    void* user;  // réservé à l'hôte
    int32_t uniqueID;
    int32_t version;
    ProcessProc processReplacing;
    ProcessDoubleProc processDoubleReplacing;
    char future[56];
};

struct ERect {
    int16_t top, left, bottom, right;
};

struct VstTimeInfo {
    double samplePos;
    double sampleRate;
    double nanoSeconds;
    double ppqPos;
    double tempo;
    double barStartPos;
    double cycleStartPos;
    double cycleEndPos;
    int32_t timeSigNumerator;
    int32_t timeSigDenominator;
    int32_t smpteOffset;
    int32_t smpteFrameRate;
    int32_t samplesToNextClock;
    int32_t flags;
};
#pragma pack(pop)

// Drapeaux de AEffect::flags
constexpr int32_t effFlagsHasEditor = 1 << 0;
constexpr int32_t effFlagsCanReplacing = 1 << 4;
constexpr int32_t effFlagsProgramChunks = 1 << 5;
constexpr int32_t effFlagsIsSynth = 1 << 8;

// Opcodes hôte → plugin
enum : int32_t {
    effOpen = 0,
    effClose = 1,
    effSetProgram = 2,
    effGetProgram = 3,
    effGetParamName = 8,
    effSetSampleRate = 10,
    effSetBlockSize = 11,
    effMainsChanged = 12,
    effEditGetRect = 13,
    effEditOpen = 14,
    effEditClose = 15,
    effEditIdle = 19,
    effGetChunk = 23,
    effSetChunk = 24,
    effGetPlugCategory = 35,
    effGetEffectName = 45,
    effGetVendorString = 47,
    effGetProductString = 48,
    effCanDo = 51,
    effGetVstVersion = 58,
    effStartProcess = 71,
    effStopProcess = 72,
};

// Opcodes plugin → hôte
enum : int32_t {
    audioMasterAutomate = 0,
    audioMasterVersion = 1,
    audioMasterCurrentId = 2,
    audioMasterIdle = 3,
    audioMasterGetTime = 7,
    audioMasterIOChanged = 13,
    audioMasterSizeWindow = 15,
    audioMasterGetSampleRate = 16,
    audioMasterGetBlockSize = 17,
    audioMasterGetInputLatency = 18,
    audioMasterGetOutputLatency = 19,
    audioMasterGetCurrentProcessLevel = 23,
    audioMasterGetAutomationState = 24,
    audioMasterGetVendorString = 32,
    audioMasterGetProductString = 33,
    audioMasterGetVendorVersion = 34,
    audioMasterCanDo = 37,
    audioMasterGetLanguage = 38,
    audioMasterUpdateDisplay = 42,
    audioMasterBeginEdit = 43,
    audioMasterEndEdit = 44,
};

// Drapeaux de VstTimeInfo::flags
constexpr int32_t kVstTransportPlaying = 1 << 1;
constexpr int32_t kVstPpqPosValid = 1 << 9;
constexpr int32_t kVstTempoValid = 1 << 10;
constexpr int32_t kVstTimeSigValid = 1 << 13;

}  // namespace vst2
