// Tests de l'analyseur de spectre (src/spectrum.h), sans Windows ni Voicemeeter :
//   c++ -std=c++20 -O2 -I src test/spectrum_test.cpp -o spectrum_test
// Lancé par tests/test_spectrum.py. Affiche « ok » et renvoie 0 si tout passe.
#include <cmath>
#include <cstdio>
#include <random>
#include <string>
#include <vector>

#include "spectrum.h"

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
constexpr double kPi = 3.14159265358979323846;

// Pousse `seconds` d'un signal, par blocs de 256 comme Voicemeeter
template <typename Signal>
void feed(SpectrumAnalyzer& a, Signal signal, double seconds) {
    const int total = (int)(seconds * kRate), block = 256;
    std::vector<float> buf(block);
    for (int start = 0; start < total; start += block) {
        for (int i = 0; i < block; ++i) buf[(size_t)i] = (float)signal(start + i);
        a.push(buf.data(), buf.data(), block);
    }
}

int loudest(const std::vector<float>& bands) {
    int best = 0;
    for (int b = 1; b < (int)bands.size(); ++b)
        if (bands[(size_t)b] > bands[(size_t)best]) best = b;
    return best;
}

int bandOf(double hz) {
    int best = 0;
    double bestDist = 1e9;
    for (int b = 0; b < SpectrumAnalyzer::kBands; ++b) {
        double d = std::abs(std::log(SpectrumAnalyzer::bandCenter(b, kRate) / hz));
        if (d < bestDist) bestDist = d, best = b;
    }
    return best;
}

}  // namespace

int main() {
    {   // Silence : tout au plancher
        SpectrumAnalyzer a;
        auto bands = a.bands(kRate);
        check((int)bands.size() == SpectrumAnalyzer::kBands, "nombre de bandes");
        for (float b : bands) check(b <= SpectrumAnalyzer::kFloorDb + 0.01f, "silence au plancher");
        check(a.pushed() == 0, "rien reçu");
    }
    for (double hz : {60.0, 440.0, 1000.0, 5000.0, 12000.0}) {
        // Sinusoïde à pleine échelle : sa bande vaut ≈ 0 dB, les bandes lointaines bien moins
        SpectrumAnalyzer a;
        feed(a, [hz](int i) { return std::sin(2 * kPi * hz * i / kRate); }, 0.2);
        auto bands = a.bands(kRate);
        int top = loudest(bands);
        check(std::abs(top - bandOf(hz)) <= 1, "bande la plus forte à " + std::to_string((int)hz) + " Hz");
        check(bands[(size_t)top] > -3.0f && bands[(size_t)top] <= 0.0f,
              "niveau d'une sinusoïde à pleine échelle (" + std::to_string(bands[(size_t)top]) + " dB)");
        for (int b = 0; b < SpectrumAnalyzer::kBands; ++b)
            if (std::abs(b - top) >= 4)
                check(bands[(size_t)b] < bands[(size_t)top] - 40.0f, "fuite loin de la raie");
    }
    {   // -20 dB : la bande vaut ≈ -20 dB
        SpectrumAnalyzer a;
        feed(a, [](int i) { return 0.1 * std::sin(2 * kPi * 1000.0 * i / kRate); }, 0.2);
        auto bands = a.bands(kRate);
        float top = bands[(size_t)loudest(bands)];
        check(top > -23.0f && top < -17.0f, "sinusoïde à -20 dB (" + std::to_string(top) + " dB)");
    }
    {   // Bruit blanc : toutes les bandes au-dessus du plancher, pas de saturation
        SpectrumAnalyzer a;
        std::mt19937 rng(3);
        std::uniform_real_distribution<double> noise(-0.5, 0.5);
        feed(a, [&](int) { return noise(rng); }, 0.2);
        for (float b : a.bands(kRate)) check(b > -70.0f && b < 0.0f, "bruit blanc dans chaque bande");
    }
    {   // Gauche et droite en opposition : le mono s'annule
        SpectrumAnalyzer a;
        std::vector<float> l(256), r(256);
        for (int block = 0; block < 20; ++block) {
            for (int i = 0; i < 256; ++i) {
                l[(size_t)i] = (float)std::sin(2 * kPi * 1000.0 * (block * 256 + i) / kRate);
                r[(size_t)i] = -l[(size_t)i];
            }
            a.push(l.data(), r.data(), 256);
        }
        check(a.bands(kRate)[(size_t)bandOf(1000.0)] < -80.0f, "gauche + droite en opposition");
        check(a.pushed() == 20 * 256, "compte des échantillons reçus");
    }
    {   // Fréquence d'échantillonnage inconnue : rien d'affiché
        SpectrumAnalyzer a;
        feed(a, [](int i) { return std::sin(2 * kPi * 1000.0 * i / kRate); }, 0.1);
        for (float b : a.bands(0.0)) check(b <= SpectrumAnalyzer::kFloorDb + 0.01f, "fréquence nulle");
    }
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
