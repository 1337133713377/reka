// Analyseur de spectre d'une strip (sans Windows : testé par test/spectrum_test.cpp).
//
// Le fil audio pousse le son (mono : moyenne gauche / droite) dans un tampon
// circulaire, sans verrou ni allocation. Le fil principal, une quinzaine de
// fois par seconde, copie les derniers kFftSize échantillons, les fenêtre
// (Hann), calcule une FFT et regroupe l'énergie en bandes logarithmiques
// (30 Hz → 16 kHz), en dB : une sinusoïde à pleine échelle vaut 0 dB.
//
// La copie peut chevaucher une écriture du fil audio : au pire une image du
// spectre mélange deux blocs, ce qui ne se voit pas. Rien n'attend jamais.
#pragma once

#include <algorithm>
#include <atomic>
#include <cmath>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace reka {

class SpectrumAnalyzer {
public:
    static constexpr int kFftSize = 2048;
    static constexpr int kBands = 24;
    static constexpr float kFloorDb = -90.0f;
    static constexpr double kLowHz = 30.0;
    static constexpr double kHighHz = 16000.0;

    SpectrumAnalyzer() : ring_(kFftSize, 0.0f) {}

    // Fil audio : ajoute un bloc (right peut être égal à left pour du mono)
    void push(const float* left, const float* right, int count) {
        size_t write = write_.load(std::memory_order_relaxed);
        for (int i = 0; i < count; ++i) {
            ring_[write] = 0.5f * (left[i] + right[i]);
            write = (write + 1) & (kFftSize - 1);
        }
        write_.store(write, std::memory_order_release);
        pushed_.fetch_add((uint64_t)count, std::memory_order_relaxed);
    }

    // Nombre total d'échantillons reçus (0 : la strip n'a jamais rien reçu)
    uint64_t pushed() const { return pushed_.load(std::memory_order_relaxed); }

    // Fil principal : niveau de chaque bande, en dB (kFloorDb à 0)
    std::vector<float> bands(double sampleRate) {
        std::vector<float> out((size_t)kBands, kFloorDb);
        if (sampleRate <= 0) return out;
        size_t start = write_.load(std::memory_order_acquire);
        std::vector<std::complex<double>> data((size_t)kFftSize);
        double windowSum = 0.0;
        for (int i = 0; i < kFftSize; ++i) {
            double w = 0.5 - 0.5 * std::cos(2.0 * kPi * i / (kFftSize - 1));
            windowSum += w;
            data[(size_t)i] = ring_[(start + (size_t)i) & (kFftSize - 1)] * w;
        }
        fft(data);
        // Amplitude d'une sinusoïde : 2 |X| / somme de la fenêtre → 1 à pleine échelle
        const double scale = 2.0 / windowSum;
        const double binHz = sampleRate / kFftSize;
        const double high = std::min(kHighHz, sampleRate / 2.0);
        for (int b = 0; b < kBands; ++b) {
            double lo = kLowHz * std::pow(high / kLowHz, (double)b / kBands);
            double hi = kLowHz * std::pow(high / kLowHz, (double)(b + 1) / kBands);
            int first = std::max(1, (int)std::floor(lo / binHz));
            int last = std::max(first, std::min(kFftSize / 2 - 1, (int)std::ceil(hi / binHz) - 1));
            double peak = 0.0;              // crête de la bande : une raie isolée garde son niveau
            for (int k = first; k <= last; ++k) peak = std::max(peak, std::abs(data[(size_t)k]) * scale);
            double db = peak > 1e-9 ? 20.0 * std::log10(peak) : kFloorDb;
            out[(size_t)b] = (float)std::clamp(db, (double)kFloorDb, 0.0);
        }
        return out;
    }

    // Fréquence centrale d'une bande (Hz), pour les tests et l'affichage
    static double bandCenter(int band, double sampleRate) {
        const double high = std::min(kHighHz, sampleRate / 2.0);
        return kLowHz * std::pow(high / kLowHz, (band + 0.5) / kBands);
    }

private:
    static constexpr double kPi = 3.14159265358979323846;

    // FFT radix 2 en place (taille : puissance de deux)
    static void fft(std::vector<std::complex<double>>& a) {
        const size_t n = a.size();
        for (size_t i = 1, j = 0; i < n; ++i) {
            size_t bit = n >> 1;
            for (; j & bit; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) std::swap(a[i], a[j]);
        }
        for (size_t len = 2; len <= n; len <<= 1) {
            double angle = -2.0 * kPi / (double)len;
            std::complex<double> step(std::cos(angle), std::sin(angle));
            for (size_t i = 0; i < n; i += len) {
                std::complex<double> w(1.0, 0.0);
                for (size_t k = 0; k < len / 2; ++k) {
                    std::complex<double> u = a[i + k];
                    std::complex<double> v = a[i + k + len / 2] * w;
                    a[i + k] = u + v;
                    a[i + k + len / 2] = u - v;
                    w *= step;
                }
            }
        }
    }

    std::vector<float> ring_;
    std::atomic<size_t> write_{0};
    std::atomic<uint64_t> pushed_{0};
};

}  // namespace reka
