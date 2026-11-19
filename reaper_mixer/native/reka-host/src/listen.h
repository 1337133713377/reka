// Écoute au casque : son surround virtuel et égalisation du casque.
//
// Traite le son d'un bus de Voicemeeter (8 canaux, ordre de Windows : avant
// G/D, centre, LFE, arrière G/D, côtés G/D), juste avant qu'il parte vers le
// casque :
//
//   1. surround virtuel (option) : chaque canal devient une enceinte placée
//      autour de la tête (modèle de tête sphérique de Brown et Duda : retard
//      entre les oreilles, ombre de la tête, oreille qui atténue les aigus
//      venant de l'arrière), plus une pièce virtuelle (premières réflexions et
//      réverbération courte). Résultat : deux canaux pour le casque ;
//   2. égalisation du casque (option) : filtres paramétriques (profil AutoEQ
//      ou fichier importé) qui corrigent la courbe du casque, après le préampli.
//
// Aucune dépendance à Windows : testé tel quel sous Linux (test/listen_test.cpp).
#pragma once

#include <array>
#include <cstdint>
#include <vector>

namespace reka {

struct EqBand {
    enum Type { Peak, LowShelf, HighShelf, LowPass, HighPass, Notch };
    Type type = Peak;
    double freq = 1000.0;
    double gainDb = 0.0;
    double q = 0.707;
};

struct ListenSettings {
    bool eq = false;
    double preampDb = 0.0;
    std::vector<EqBand> bands;
    bool surround = false;
    int head = 1;          // 0 petite, 1 moyenne, 2 grande
    int room = 1;          // 0 aucune, 1 salon, 2 cinéma
    double amount = 1.0;   // 0 : son d'origine, 1 : tout en surround virtuel

    bool active() const { return eq || surround; }
};

constexpr int kListenChannels = 8;
constexpr size_t kMaxEqBands = 32;

// Biquad (formules de Robert Bristow-Johnson), calculé en double
struct Biquad {
    double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;

    static Biquad make(const EqBand& band, double rate);
    // Gain (linéaire) à la fréquence f
    double magnitude(double f, double rate) const;
};

struct BiquadState {
    double z1 = 0, z2 = 0;

    inline float run(const Biquad& c, float input) {
        double x = input;
        double y = c.b0 * x + z1;
        z1 = c.b1 * x - c.a1 * y + z2;
        z2 = c.b2 * x - c.a2 * y;
        return (float)y;
    }
};

// Filtre du premier ordre (ombre de la tête, amortissements)
struct OnePole {
    double b0 = 1, b1 = 0, a1 = 0;
    double x1 = 0, y1 = 0;

    inline float run(float input) {
        double y = b0 * input + b1 * x1 - a1 * y1;
        x1 = input;
        y1 = y;
        return (float)y;
    }
};

class ListenProcessor {
public:
    ListenProcessor(const ListenSettings& settings, double rate);

    // Reprend les mémoires des filtres d'un traitement précédent (réglage
    // modifié en cours de lecture : pas de clic)
    void adopt(ListenProcessor& previous);

    // `channels` canaux du bus (1 à 8), traités sur place, n échantillons
    void process(float* const* channels, int count, int n);

    const ListenSettings& settings() const { return settings_; }
    double rate() const { return rate_; }

    // Réponse (dB) de l'égalisation à la fréquence f, préampli compris
    double eqResponseDb(double f) const;

private:
    static constexpr int kLine = 1024;           // ligne à retard d'une enceinte (puissance de 2)

    struct Ear {
        double delay = 0;      // en échantillons (fractionnaire)
        OnePole shadow;        // ombre de la tête
    };
    struct Speaker {
        bool used = false;
        bool lfe = false;
        float gain = 1.0f;
        bool rear = false;
        Biquad rearShelf;      // l'oreille externe atténue les aigus venant de l'arrière
        BiquadState rearState;
        std::array<float, kLine> line{};
        uint32_t write = 0;
        int silent = 0;        // échantillons silencieux d'affilée
        Ear ears[2];
    };
    struct Room {
        bool on = false;
        std::vector<float> early[2];          // lignes des premières réflexions
        uint32_t earlyMask = 0, earlyWrite = 0;
        struct Tap { uint32_t delay; float gain; bool cross; };
        std::vector<Tap> taps;
        OnePole earlyTone[2];
        float earlyGain = 0;
        std::vector<float> late[4];           // réverbération (réseau de 4 lignes)
        uint32_t lateLength[4] = {};
        uint32_t lateWrite[4] = {};
        float lateFeedback[4] = {};
        OnePole lateTone[4];
        uint32_t preDelay = 0;
        float lateGain = 0;
    };

    void setupSurround();
    void setupRoom();
    void surround(float* const* channels, int count, int n);
    void runRoom(float* left, float* right, int n);

    ListenSettings settings_;
    double rate_;
    float preamp_ = 1.0f;
    std::vector<Biquad> eq_;
    std::vector<std::array<BiquadState, kListenChannels>> eqState_;
    std::array<Speaker, kListenChannels> speakers_{};
    Room room_;
    std::vector<float> wet_[2];
    std::vector<float> dry_[2];
    float wetGain_ = 0.6f;
};

}  // namespace reka
