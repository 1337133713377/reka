// Tests du traitement d'écoute (src/listen.cpp), sans Windows ni Voicemeeter :
//   c++ -std=c++20 -O2 -I src test/listen_test.cpp src/listen.cpp -o listen_test
// Lancé par tests/test_listen.py. Affiche « ok » et renvoie 0 si tout passe.
#include <cmath>
#include <cstdio>
#include <random>
#include <string>
#include <vector>

#include "listen.h"

using namespace reka;

namespace {

int failures = 0;

void check(bool ok, const std::string& what) {
    if (!ok) {
        std::printf("ÉCHEC : %s\n", what.c_str());
        ++failures;
    }
}

constexpr double kRate = 48000.0;
constexpr int kBlock = 256;
constexpr double kPi = 3.14159265358979323846;

struct Bus {
    std::vector<std::vector<float>> data;
    float* ptr[8] = {};
    explicit Bus(int n) : data(8, std::vector<float>((size_t)n, 0.0f)) {
        for (int c = 0; c < 8; ++c) ptr[c] = data[(size_t)c].data();
    }
};

// Fait passer `input` (8 canaux, même longueur) par le traitement, bloc par bloc
std::vector<std::vector<float>> run(ListenProcessor& p, const std::vector<std::vector<float>>& input) {
    size_t total = input[0].size();
    std::vector<std::vector<float>> out(8, std::vector<float>(total, 0.0f));
    for (size_t pos = 0; pos < total; pos += kBlock) {
        int n = (int)std::min<size_t>(kBlock, total - pos);
        Bus bus(n);
        for (int c = 0; c < 8; ++c)
            for (int i = 0; i < n; ++i) bus.data[(size_t)c][(size_t)i] = input[(size_t)c][pos + (size_t)i];
        p.process(bus.ptr, 8, n);
        for (int c = 0; c < 8; ++c)
            for (int i = 0; i < n; ++i) out[(size_t)c][pos + (size_t)i] = bus.data[(size_t)c][(size_t)i];
    }
    return out;
}

double rms(const std::vector<float>& x, size_t from) {
    double sum = 0;
    for (size_t i = from; i < x.size(); ++i) sum += (double)x[i] * x[i];
    return std::sqrt(sum / (double)(x.size() - from));
}

std::vector<std::vector<float>> silence(size_t n) {
    return std::vector<std::vector<float>>(8, std::vector<float>(n, 0.0f));
}

size_t firstAbove(const std::vector<float>& x, float level) {
    for (size_t i = 0; i < x.size(); ++i)
        if (std::fabs(x[i]) > level) return i;
    return x.size();
}

double energy(const std::vector<float>& x) {
    double sum = 0;
    for (float v : x) sum += (double)v * v;
    return sum;
}

bool finite(const std::vector<std::vector<float>>& out, float limit) {
    for (const auto& ch : out)
        for (float v : ch)
            if (!std::isfinite(v) || std::fabs(v) > limit) return false;
    return true;
}

void testEqPeak() {
    ListenSettings s;
    s.eq = true;
    s.bands.push_back({EqBand::Peak, 1000.0, 6.0, 1.0});
    ListenProcessor p(s, kRate);
    check(std::fabs(p.eqResponseDb(1000.0) - 6.0) < 0.05, "réponse du filtre à 1 kHz = +6 dB");
    check(std::fabs(p.eqResponseDb(50.0)) < 0.1, "réponse du filtre loin de 1 kHz = 0 dB");
    auto in = silence(48000);
    for (size_t i = 0; i < in[0].size(); ++i) in[0][i] = 0.25f * (float)std::sin(2 * kPi * 1000 * i / kRate);
    auto out = run(p, in);
    double gain = rms(out[0], 24000) / rms(in[0], 24000);
    check(std::fabs(20 * std::log10(gain) - 6.0) < 0.1, "sinus à 1 kHz amplifié de 6 dB");
}

void testPreampAndShelves() {
    ListenSettings s;
    s.eq = true;
    s.preampDb = -6.0;
    s.bands.push_back({EqBand::LowShelf, 105.0, 6.5, 0.7});
    s.bands.push_back({EqBand::HighShelf, 10000.0, -3.1, 0.7});
    ListenProcessor p(s, kRate);
    check(std::fabs(p.eqResponseDb(20.0) - 0.5) < 0.3, "étagère grave + préampli à 20 Hz");
    check(std::fabs(p.eqResponseDb(1000.0) + 6.0) < 0.3, "préampli seul au milieu");
    check(std::fabs(p.eqResponseDb(20000.0) + 9.1) < 0.4, "étagère aiguë + préampli à 20 kHz");
    ListenSettings flat;
    flat.eq = true;
    flat.preampDb = -6.0;
    ListenProcessor q(flat, kRate);
    auto in = silence(1024);
    for (size_t i = 0; i < in[0].size(); ++i) in[0][i] = in[5][i] = 0.5f;
    auto out = run(q, in);
    check(std::fabs(out[0][100] - 0.5f * 0.501187f) < 1e-4, "préampli de -6 dB");
    check(std::fabs(out[5][100] - 0.5f * 0.501187f) < 1e-4, "sans surround : tous les canaux égalisés");
}

void testSurroundDirections() {
    ListenSettings s;
    s.surround = true;
    s.room = 0;
    for (int channel : {0, 4, 6}) {             // avant, arrière et côté gauches
        ListenProcessor p(s, kRate);
        auto in = silence(4096);
        in[(size_t)channel][10] = 1.0f;
        auto out = run(p, in);
        size_t left = firstAbove(out[0], 1e-4f), right = firstAbove(out[1], 1e-4f);
        check(left < right, "canal " + std::to_string(channel) + " : l'oreille gauche entend en premier");
        check(energy(out[0]) > 1.5 * energy(out[1]),
              "canal " + std::to_string(channel) + " : plus fort à gauche");
        for (int c = 2; c < 8; ++c)
            check(energy(out[(size_t)c]) == 0.0, "canaux 3 à 8 vidés (tout part sur le casque)");
    }
    {   // Centre : identique dans les deux oreilles
        ListenProcessor p(s, kRate);
        auto in = silence(4096);
        for (size_t i = 0; i < in[2].size(); ++i) in[2][i] = 0.3f * (float)std::sin(2 * kPi * 440 * i / kRate);
        auto out = run(p, in);
        double diff = 0;
        for (size_t i = 0; i < out[0].size(); ++i) diff = std::max(diff, (double)std::fabs(out[0][i] - out[1][i]));
        check(diff < 1e-6, "centre identique à gauche et à droite");
        check(rms(out[0], 2048) > 0.05, "centre audible");
    }
    {   // Arrière : moins d'aigus que devant
        auto treble = [&](int channel) {
            ListenProcessor p(s, kRate);
            auto in = silence(9600);
            for (size_t i = 0; i < in[0].size(); ++i)
                in[(size_t)channel][i] = 0.3f * (float)std::sin(2 * kPi * 8000 * i / kRate);
            auto out = run(p, in);
            return rms(out[0], 4800);
        };
        check(treble(4) < 0.8 * treble(0), "aigus atténués pour une enceinte arrière");
    }
}

void testAmountZeroIsDownmix() {
    ListenSettings s;
    s.surround = true;
    s.amount = 0.0;
    ListenProcessor p(s, kRate);
    auto in = silence(512);
    for (int c = 0; c < 8; ++c)
        for (size_t i = 0; i < in[0].size(); ++i) in[(size_t)c][i] = 0.1f * (float)(c + 1);
    auto out = run(p, in);
    float left = 0.1f + 0.7071f * (0.3f + 0.4f + 0.5f + 0.7f);
    float right = 0.2f + 0.7071f * (0.3f + 0.4f + 0.6f + 0.8f);
    check(std::fabs(out[0][300] - left) < 1e-4, "intensité 0 : mixage stéréo classique (gauche)");
    check(std::fabs(out[1][300] - right) < 1e-4, "intensité 0 : mixage stéréo classique (droite)");
}

void testSilenceAndStability() {
    for (int room = 0; room < 3; ++room) {
        ListenSettings s;
        s.surround = true;
        s.room = room;
        s.eq = true;
        s.preampDb = -6.3;
        s.bands.push_back({EqBand::LowShelf, 105, 6.5, 0.7});
        s.bands.push_back({EqBand::Peak, 6639, 2.2, 5.82});
        ListenProcessor p(s, kRate);
        auto quiet = run(p, silence(48000));
        check(finite(quiet, 0.0f), "silence → silence (pièce " + std::to_string(room) + ")");
        std::mt19937 rng(7);
        std::uniform_real_distribution<float> noise(-0.5f, 0.5f);
        auto in = silence(10 * 48000);
        for (auto& ch : in)
            for (float& v : ch) v = noise(rng);
        auto out = run(p, in);
        check(finite(out, 8.0f), "bruit sur 8 canaux : stable (pièce " + std::to_string(room) + ")");
        // Après le son, la réverbération s'éteint
        auto tail = run(p, silence(4 * 48000));
        double end = 0;
        for (size_t i = tail[0].size() - 4800; i < tail[0].size(); ++i)
            end = std::max(end, (double)std::fabs(tail[0][i]));
        check(end < 1e-3, "la pièce virtuelle s'éteint (pièce " + std::to_string(room) + ")");
    }
}

void testAdoptKeepsSound() {
    ListenSettings s;
    s.surround = true;
    s.room = 1;
    ListenProcessor a(s, kRate);
    auto in = silence(4800);
    for (size_t i = 0; i < in[0].size(); ++i) in[0][i] = 0.3f * (float)std::sin(2 * kPi * 300 * i / kRate);
    run(a, in);
    s.amount = 0.9;
    ListenProcessor b(s, kRate);
    b.adopt(a);
    Bus bus(64);
    for (int i = 0; i < 64; ++i) bus.data[0][(size_t)i] = 0.3f * (float)std::sin(2 * kPi * 300 * (4800 + i) / kRate);
    b.process(bus.ptr, 8, 64);
    check(std::fabs(bus.data[0][0]) > 1e-4, "réglage changé : le son continue sans repartir de zéro");
}

void testLongBlocks() {
    ListenSettings s;
    s.surround = true;
    s.eq = true;
    s.bands.push_back({EqBand::Peak, 1000.0, 3.0, 1.0});
    ListenProcessor p(s, 96000.0);
    Bus bus(10000);                           // plus long que le tampon interne
    for (int i = 0; i < 10000; ++i) bus.data[1][(size_t)i] = 0.2f;
    p.process(bus.ptr, 8, 10000);
    check(std::isfinite(bus.data[0][9999]) && std::fabs(bus.data[1][9999]) > 0.01f,
          "bloc de 10 000 échantillons traité en entier");
    p.process(bus.ptr, 2, 100);               // bus stéréo seulement
    p.process(bus.ptr, 1, 100);               // mono : pas de surround, pas de plantage
}

}  // namespace

int main() {
    testEqPeak();
    testPreampAndShelves();
    testSurroundDirections();
    testAmountZeroIsDownmix();
    testSilenceAndStability();
    testAdoptKeepsSound();
    testLongBlocks();
    if (failures) {
        std::printf("%d échec(s)\n", failures);
        return 1;
    }
    std::printf("ok\n");
    return 0;
}
