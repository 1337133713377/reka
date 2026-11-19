#include "listen.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace reka {
namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kSoundSpeed = 343.0;          // m/s
constexpr int kChunk = 4096;                    // bloc traité d'un coup (tampons alloués d'avance)

// Rayon de tête (m) des trois profils : petite, moyenne, grande
constexpr double kHeadRadius[3] = {0.0775, 0.0875, 0.0975};

// Position des enceintes virtuelles, en degrés (+ = à gauche), dans l'ordre
// des canaux de Windows : avant G/D, centre, LFE, arrière G/D, côtés G/D
constexpr double kAzimuth[kListenChannels] = {30, -30, 0, 0, 125, -125, 90, -90};

double wrapDegrees(double angle) {
    angle = std::fmod(angle + 180.0, 360.0);
    if (angle < 0) angle += 360.0;
    return angle - 180.0;
}

OnePole lowPass(double cutoff, double rate) {
    OnePole f;
    double p = std::exp(-2.0 * kPi * std::min(cutoff, 0.45 * rate) / rate);
    f.b0 = 1.0 - p;
    f.b1 = 0.0;
    f.a1 = -p;
    return f;
}

uint32_t nextPow2(uint32_t value) {
    uint32_t size = 1;
    while (size < value) size <<= 1;
    return size;
}

}  // namespace

// ------------------------------------------------------------------ biquads
Biquad Biquad::make(const EqBand& band, double rate) {
    Biquad c;
    double freq = std::clamp(band.freq, 10.0, 0.49 * rate);
    double q = std::clamp(band.q, 0.05, 50.0);
    double gain = std::clamp(band.gainDb, -30.0, 30.0);
    double A = std::pow(10.0, gain / 40.0);
    double w0 = 2.0 * kPi * freq / rate;
    double cw = std::cos(w0), sw = std::sin(w0);
    double alpha = sw / (2.0 * q);
    double b0 = 1, b1 = 0, b2 = 0, a0 = 1, a1 = 0, a2 = 0;
    switch (band.type) {
        case EqBand::Peak:
            b0 = 1 + alpha * A; b1 = -2 * cw; b2 = 1 - alpha * A;
            a0 = 1 + alpha / A; a1 = -2 * cw; a2 = 1 - alpha / A;
            break;
        case EqBand::LowShelf: {
            double s = 2 * std::sqrt(A) * alpha;
            b0 = A * ((A + 1) - (A - 1) * cw + s);
            b1 = 2 * A * ((A - 1) - (A + 1) * cw);
            b2 = A * ((A + 1) - (A - 1) * cw - s);
            a0 = (A + 1) + (A - 1) * cw + s;
            a1 = -2 * ((A - 1) + (A + 1) * cw);
            a2 = (A + 1) + (A - 1) * cw - s;
            break;
        }
        case EqBand::HighShelf: {
            double s = 2 * std::sqrt(A) * alpha;
            b0 = A * ((A + 1) + (A - 1) * cw + s);
            b1 = -2 * A * ((A - 1) + (A + 1) * cw);
            b2 = A * ((A + 1) + (A - 1) * cw - s);
            a0 = (A + 1) - (A - 1) * cw + s;
            a1 = 2 * ((A - 1) - (A + 1) * cw);
            a2 = (A + 1) - (A - 1) * cw - s;
            break;
        }
        case EqBand::LowPass:
            b0 = (1 - cw) / 2; b1 = 1 - cw; b2 = (1 - cw) / 2;
            a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha;
            break;
        case EqBand::HighPass:
            b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = (1 + cw) / 2;
            a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha;
            break;
        case EqBand::Notch:
            b0 = 1; b1 = -2 * cw; b2 = 1;
            a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha;
            break;
    }
    c.b0 = b0 / a0;
    c.b1 = b1 / a0;
    c.b2 = b2 / a0;
    c.a1 = a1 / a0;
    c.a2 = a2 / a0;
    return c;
}

double Biquad::magnitude(double f, double rate) const {
    double w = 2.0 * kPi * f / rate;
    // H(e^jw) = (b0 + b1 z^-1 + b2 z^-2) / (1 + a1 z^-1 + a2 z^-2)
    double c1 = std::cos(w), s1 = std::sin(w), c2 = std::cos(2 * w), s2 = std::sin(2 * w);
    double nr = b0 + b1 * c1 + b2 * c2, ni = -(b1 * s1 + b2 * s2);
    double dr = 1 + a1 * c1 + a2 * c2, di = -(a1 * s1 + a2 * s2);
    return std::sqrt((nr * nr + ni * ni) / (dr * dr + di * di));
}

// ------------------------------------------------------------------ réglages
ListenProcessor::ListenProcessor(const ListenSettings& settings, double rate)
    : settings_(settings), rate_(rate > 0 ? rate : 48000.0) {
    settings_.head = std::clamp(settings_.head, 0, 2);
    settings_.room = std::clamp(settings_.room, 0, 2);
    settings_.amount = std::clamp(settings_.amount, 0.0, 1.0);
    settings_.preampDb = std::clamp(settings_.preampDb, -30.0, 12.0);
    if (settings_.bands.size() > kMaxEqBands) settings_.bands.resize(kMaxEqBands);
    preamp_ = (float)std::pow(10.0, settings_.preampDb / 20.0);
    for (const EqBand& band : settings_.bands) eq_.push_back(Biquad::make(band, rate_));
    eqState_.resize(eq_.size());
    for (auto& side : wet_) side.assign(kChunk, 0.0f);
    for (auto& side : dry_) side.assign(kChunk, 0.0f);
    setupSurround();
    setupRoom();
}

void ListenProcessor::setupSurround() {
    const double a = kHeadRadius[settings_.head];
    const double tau = a / (2.0 * kSoundSpeed);   // 1 / (2 ω0), ω0 = c / a
    const double K = 2.0 * rate_;                 // transformée bilinéaire
    for (int s = 0; s < kListenChannels; ++s) {
        Speaker& sp = speakers_[(size_t)s];
        sp.used = true;
        sp.lfe = (s == 3);
        sp.gain = (s == 2 || s == 3) ? 0.7071f : 1.0f;
        const double az = kAzimuth[s];
        double rear = std::max(0.0, -std::cos(az * kPi / 180.0));
        sp.rear = rear > 0.01;
        if (sp.rear) {
            EqBand shelf{EqBand::HighShelf, 3500.0, -10.0 * rear, 0.7};
            sp.rearShelf = Biquad::make(shelf, rate_);
        }
        // Oreille gauche à +90°, droite à -90° ; θ = angle entre la source et l'oreille
        for (int e = 0; e < 2; ++e) {
            double theta = std::fabs(wrapDegrees(az - (e == 0 ? 90.0 : -90.0))) * kPi / 180.0;
            // Ombre de la tête : jusqu'à +6 dB d'aigus côté source, -20 dB derrière la tête
            const double alphaMin = 0.1, thetaMin = 150.0 * kPi / 180.0;
            double alpha = (1 + alphaMin / 2) + (1 - alphaMin / 2) * std::cos(theta / thetaMin * kPi);
            OnePole shadow;
            shadow.b0 = (1 + alpha * tau * K) / (1 + tau * K);
            shadow.b1 = (1 - alpha * tau * K) / (1 + tau * K);
            shadow.a1 = (1 - tau * K) / (1 + tau * K);
            sp.ears[e].shadow = shadow;
            // Retard jusqu'à l'oreille (Brown et Duda), + 2 échantillons pour l'interpolation
            double t = theta < kPi / 2 ? (a / kSoundSpeed) * (1 - std::cos(theta))
                                       : (a / kSoundSpeed) * (theta - kPi / 2 + 1);
            sp.ears[e].delay = std::min(t * rate_ + 2.0, (double)kLine - 4.0);
        }
    }
}

void ListenProcessor::setupRoom() {
    room_ = Room();
    if (!settings_.surround || settings_.room == 0) return;
    const bool cinema = settings_.room == 2;
    const double scale = cinema ? 2.0 : 1.0;
    struct TapMs { double ms; float gain; bool cross; };
    static const TapMs kTaps[] = {{3.1, 0.42f, false}, {5.3, 0.36f, true}, {7.7, 0.30f, false},
                                  {10.9, 0.26f, true}, {14.3, 0.20f, false}, {19.1, 0.16f, true}};
    const double preMs = cinema ? 18.0 : 8.0;
    uint32_t longest = (uint32_t)std::ceil(std::max(19.1 * scale, preMs) * rate_ / 1000.0) + 2;
    uint32_t size = nextPow2(longest + 1);
    for (auto& side : room_.early) side.assign(size, 0.0f);
    room_.earlyMask = size - 1;
    for (const TapMs& tap : kTaps)
        room_.taps.push_back({(uint32_t)std::lround(tap.ms * scale * rate_ / 1000.0), tap.gain,
                              tap.cross});
    room_.preDelay = (uint32_t)std::lround(preMs * rate_ / 1000.0);
    room_.earlyGain = cinema ? 0.30f : 0.35f;
    for (auto& tone : room_.earlyTone) tone = lowPass(cinema ? 6000.0 : 7000.0, rate_);
    static const double kLateMs[4] = {23.3, 29.9, 36.7, 41.3};
    const double rt60 = cinema ? 0.85 : 0.35;
    for (int k = 0; k < 4; ++k) {
        uint32_t length = std::max<uint32_t>(
            8, (uint32_t)std::lround(kLateMs[k] * (cinema ? 1.7 : 1.0) * rate_ / 1000.0));
        room_.late[k].assign(length, 0.0f);
        room_.lateLength[k] = length;
        room_.lateFeedback[k] = (float)std::pow(10.0, -3.0 * length / (rt60 * rate_));
        room_.lateTone[k] = lowPass(cinema ? 4000.0 : 5500.0, rate_);
    }
    room_.lateGain = cinema ? 0.16f : 0.10f;
    room_.on = true;
}

void ListenProcessor::adopt(ListenProcessor& previous) {
    if (previous.rate_ != rate_) return;
    if (previous.eq_.size() == eq_.size()) eqState_ = previous.eqState_;
    if (settings_.surround && previous.settings_.surround) {
        for (int s = 0; s < kListenChannels; ++s) {
            Speaker& sp = speakers_[(size_t)s];
            Speaker& old = previous.speakers_[(size_t)s];
            sp.line = old.line;
            sp.write = old.write;
            sp.silent = old.silent;
            sp.rearState = old.rearState;
            for (int e = 0; e < 2; ++e) {
                sp.ears[e].shadow.x1 = old.ears[e].shadow.x1;
                sp.ears[e].shadow.y1 = old.ears[e].shadow.y1;
            }
        }
        if (room_.on && previous.room_.on && previous.settings_.room == settings_.room) {
            room_.early[0].swap(previous.room_.early[0]);
            room_.early[1].swap(previous.room_.early[1]);
            room_.earlyWrite = previous.room_.earlyWrite;
            for (int k = 0; k < 4; ++k) {
                room_.late[k].swap(previous.room_.late[k]);
                room_.lateWrite[k] = previous.room_.lateWrite[k];
            }
        }
    }
}

double ListenProcessor::eqResponseDb(double f) const {
    double gain = settings_.eq ? preamp_ : 1.0;
    if (settings_.eq)
        for (const Biquad& c : eq_) gain *= c.magnitude(f, rate_);
    return 20.0 * std::log10(std::max(gain, 1e-12));
}

// ------------------------------------------------------------------ traitement
void ListenProcessor::process(float* const* channels, int count, int n) {
    count = std::clamp(count, 0, kListenChannels);
    if (count <= 0 || n <= 0) return;
    for (int offset = 0; offset < n; offset += kChunk) {
        int length = std::min(kChunk, n - offset);
        float* part[kListenChannels] = {};
        for (int c = 0; c < count; ++c) part[c] = channels[c] ? channels[c] + offset : nullptr;
        bool stereoOut = false;
        if (settings_.surround && count >= 2 && part[0] && part[1]) {
            surround(part, count, length);
            stereoOut = true;
        }
        if (!settings_.eq) continue;
        int processed = stereoOut ? 2 : count;
        for (int c = 0; c < processed; ++c) {
            float* x = part[c];
            if (!x) continue;
            for (int i = 0; i < length; ++i) {
                float v = x[i] * preamp_;
                for (size_t b = 0; b < eq_.size(); ++b) v = eqState_[b][(size_t)c].run(eq_[b], v);
                x[i] = v;
            }
        }
    }
}

void ListenProcessor::surround(float* const* channels, int count, int n) {
    float* wetL = wet_[0].data();
    float* wetR = wet_[1].data();
    float* dryL = dry_[0].data();
    float* dryR = dry_[1].data();
    std::fill(wetL, wetL + n, 0.0f);
    std::fill(wetR, wetR + n, 0.0f);
    // Son d'origine ramené en stéréo (pour le dosage « intensité »)
    std::memcpy(dryL, channels[0], sizeof(float) * (size_t)n);
    std::memcpy(dryR, channels[1], sizeof(float) * (size_t)n);
    for (int s = 2; s < count; ++s) {
        const float* x = channels[s];
        if (!x) continue;
        const float k = 0.7071f;
        const bool toLeft = (s == 2 || s == 3 || s == 4 || s == 6);
        const bool toRight = (s == 2 || s == 3 || s == 5 || s == 7);
        for (int i = 0; i < n; ++i) {
            if (toLeft) dryL[i] += k * x[i];
            if (toRight) dryR[i] += k * x[i];
        }
    }

    // Chaque canal devient une enceinte virtuelle
    for (int s = 0; s < count; ++s) {
        const float* x = channels[s];
        Speaker& sp = speakers_[(size_t)s];
        if (!x || !sp.used) continue;
        bool silent = true;
        for (int i = 0; i < n && silent; ++i) silent = std::fabs(x[i]) < 1e-9f;
        if (silent && sp.silent >= kLine) continue;   // rien dans la ligne : rien à calculer
        sp.silent = silent ? std::min(sp.silent + n, 1 << 30) : 0;
        if (sp.lfe) {
            for (int i = 0; i < n; ++i) {
                wetL[i] += sp.gain * x[i];
                wetR[i] += sp.gain * x[i];
            }
            continue;
        }
        const uint32_t mask = kLine - 1;
        const double dl = sp.ears[0].delay, dr = sp.ears[1].delay;
        const uint32_t il = (uint32_t)dl, ir = (uint32_t)dr;
        const float fl = (float)(dl - il), fr = (float)(dr - ir);
        for (int i = 0; i < n; ++i) {
            float v = x[i] * sp.gain;
            if (sp.rear) v = sp.rearState.run(sp.rearShelf, v);
            sp.line[sp.write & mask] = v;
            uint32_t w = sp.write;
            float l = sp.line[(w - il) & mask] * (1 - fl) + sp.line[(w - il - 1) & mask] * fl;
            float r = sp.line[(w - ir) & mask] * (1 - fr) + sp.line[(w - ir - 1) & mask] * fr;
            wetL[i] += sp.ears[0].shadow.run(l);
            wetR[i] += sp.ears[1].shadow.run(r);
            ++sp.write;
        }
    }
    for (int i = 0; i < n; ++i) {
        wetL[i] *= wetGain_;
        wetR[i] *= wetGain_;
    }
    if (room_.on) runRoom(wetL, wetR, n);

    // Dosage, puis le casque reçoit tout sur ses deux canaux
    const float k = (float)settings_.amount;
    for (int i = 0; i < n; ++i) {
        channels[0][i] = k * wetL[i] + (1 - k) * dryL[i];
        channels[1][i] = k * wetR[i] + (1 - k) * dryR[i];
    }
    for (int s = 2; s < count; ++s)
        if (channels[s]) std::fill(channels[s], channels[s] + n, 0.0f);
}

void ListenProcessor::runRoom(float* left, float* right, int n) {
    Room& r = room_;
    const uint32_t mask = r.earlyMask;
    float* earlyL = r.early[0].data();
    float* earlyR = r.early[1].data();
    for (int i = 0; i < n; ++i) {
        const float inL = left[i], inR = right[i];
        const uint32_t w = r.earlyWrite;
        earlyL[w & mask] = inL;
        earlyR[w & mask] = inR;
        // Premières réflexions (murs proches), un peu assourdies
        float erL = 0, erR = 0;
        for (const Room::Tap& tap : r.taps) {
            float fromL = earlyL[(w - tap.delay) & mask], fromR = earlyR[(w - tap.delay) & mask];
            erL += tap.gain * (tap.cross ? fromR : fromL);
            erR += tap.gain * (tap.cross ? fromL : fromR);
        }
        erL = r.earlyTone[0].run(erL);
        erR = r.earlyTone[1].run(erR);
        // Réverbération : 4 lignes bouclées par une matrice de Hadamard
        const float in = 0.5f * (earlyL[(w - r.preDelay) & mask] + earlyR[(w - r.preDelay) & mask]);
        float o[4];
        for (int k = 0; k < 4; ++k) o[k] = r.late[k][r.lateWrite[k]];
        const float h[4] = {0.5f * (o[0] + o[1] + o[2] + o[3]), 0.5f * (o[0] - o[1] + o[2] - o[3]),
                            0.5f * (o[0] + o[1] - o[2] - o[3]), 0.5f * (o[0] - o[1] - o[2] + o[3])};
        static const float kSign[4] = {1.0f, -1.0f, 1.0f, -1.0f};
        for (int k = 0; k < 4; ++k) {
            r.late[k][r.lateWrite[k]] = r.lateTone[k].run(h[k] * r.lateFeedback[k]) + kSign[k] * in;
            if (++r.lateWrite[k] >= r.lateLength[k]) r.lateWrite[k] = 0;
        }
        left[i] = inL + r.earlyGain * erL + r.lateGain * (o[0] + o[2]);
        right[i] = inR + r.earlyGain * erR + r.lateGain * (o[1] + o[3]);
        ++r.earlyWrite;
    }
}

}  // namespace reka
