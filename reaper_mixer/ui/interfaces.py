"""
Styles d'interface (réglages → Interface) : la forme de toute la console.

Les thèmes de l'onglet Apparence changent les couleurs ; un style d'interface
change le reste : arrondis, bordures et reliefs des panneaux, dessin des
knobs, des faders et des VU-mètres, police, fond de la fenêtre, afficheurs.
Chaque style a aussi sa palette, utilisée sauf si l'utilisateur garde ses
propres couleurs par-dessus (« mode mixte », config ui.keep_colors).

S'y ajoutent trois réglages de disposition, indépendants du style :
  - densité      : compacte, normale ou aérée (marges, écarts, largeur des strips)
  - orientation  : strips verticales (console) ou horizontales (rack, en lignes)
  - animations   : activées, réduites ou désactivées

Ce module ne fait que décrire ; styles.py applique (styles.INTERFACE…).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_INTERFACE = "reka"

VERTICAL, HORIZONTAL = "vertical", "horizontal"
ORIENTATIONS = {VERTICAL: "Verticale (console)", HORIZONTAL: "Horizontale (rack, en lignes)"}
DEFAULT_ORIENTATION = VERTICAL

DENSITIES = {                  # clé (config ui.density) → (nom affiché, facteur des écarts)
    "compacte": ("Compacte", 0.7),
    "normale": ("Normale", 1.0),
    "aeree": ("Aérée", 1.4),
}
DEFAULT_DENSITY = "normale"

ANIMATIONS = {                 # clé (config ui.animations) → nom affiché
    "on": "Activées",
    "reduced": "Réduites (sans pulsation ni fondu)",
    "off": "Désactivées",
}
DEFAULT_ANIMATIONS = "on"


@dataclass(frozen=True)
class Interface:
    key: str
    name: str
    description: str
    palette: dict[str, str] = field(default_factory=dict)   # couleurs propres du style
    font: str | None = None          # police propre (si la police choisie est celle d'origine)
    radius: float = 1.0              # facteur des arrondis (1 = REKA)
    max_radius: float = 99.0         # arrondi maximal (px)
    knob: str = "reka"               # dessin des knobs : reka, flat, chrome, arc
    fader: str | None = None         # curseur des faders ; None = style choisi dans Apparence
    vu: str | None = None            # VU-mètres ; None = style choisi dans Apparence
    borders: str = "solid"           # bordures des panneaux : solid, none, bevel
    glow: bool = True                # halos et lueurs (knobs, badge, curseurs)
    lcd: bool = False                # afficheurs LCD (noms, valeurs, effets)
    background: str = "glow"         # fond de la fenêtre : glow, flat, brushed
    titles: bool = True              # titres de section (« Effets »)
    scale_labels: bool = True        # chiffres de l'échelle des faders
    visualizer: bool = False         # égaliseur visuel animé dans le Master
    orientation: str = DEFAULT_ORIENTATION   # disposition conseillée, appliquée au choix du style
    density: str = DEFAULT_DENSITY

    @property
    def own_shapes(self) -> bool:
        """Le style dessine ses propres faders et VU (les choix d'Apparence ne s'appliquent pas)."""
        return self.fader is not None or self.vu is not None


# Couleurs du style « Cantabile » : gris unis, plats, accent bleu de sélection
CANTABILE_PALETTE = {
    "BG_WINDOW": "#1c1c1e", "BG_GLOW": "#1c1c1e", "BG_COLUMN": "#2a2a2d",
    "BG_SECTION": "#232326", "BORDER_COLUMN": "#3d3d42", "BORDER_SECTION": "#36363a",
    "BG_PANEL": "#2a2a2d", "BG_PILL": "#333337", "BG_DEEP": "#19191b",
    "GREEN_NEON": "#3d9be9", "GREEN_TEXT": "#3d9be9", "GREEN_BRIGHT": "#66b2f0",
    "GREEN_DIM": "#24496b", "GREEN_LABEL": "#9b9ba1", "GREEN_SCALE": "#55555b",
    "TEXT_PRIMARY": "#e8e8ea", "TEXT_SECONDARY": "#a2a2a8", "TEXT_MUTED": "#6c6c72",
    "BUTTON_BG": "#38383c", "BUTTON_HOVER": "#444449", "PILL_BG": "#333337",
    "PILL_BORDER": "#45454a", "KNOB_CAP_TOP": "#4c4c51", "KNOB_CAP_BOTTOM": "#4c4c51",
    "ON_ACCENT": "#0b1622", "ON_ACCENT_DARK": "#0b1622", "BADGE_OFF": "#2e2e32",
    "VU_BASE": "#4cb050", "LCD_BG": "#19191b", "LCD_TEXT": "#e8e8ea",
}

# Couleurs du style « Lecteur 2000 » : métal bleuté, afficheurs LCD verts
RETRO_PALETTE = {
    "BG_WINDOW": "#151a21", "BG_GLOW": "#2c3644", "BG_COLUMN": "#3b4554",
    "BG_SECTION": "#2a323e", "BORDER_COLUMN": "#6b7a90", "BORDER_SECTION": "#1a2029",
    "BG_PANEL": "#3b4554", "BG_PILL": "#08130a", "BG_DEEP": "#08130a",
    "GREEN_NEON": "#8cff5a", "GREEN_TEXT": "#8cff5a", "GREEN_BRIGHT": "#b4ff94",
    "GREEN_DIM": "#2e5a26", "GREEN_LABEL": "#b8c3d1", "GREEN_SCALE": "#8794a8",
    "TEXT_PRIMARY": "#eef2f6", "TEXT_SECONDARY": "#bcc6d3", "TEXT_MUTED": "#7d899b",
    "BUTTON_BG": "#55616f", "BUTTON_HOVER": "#65727f", "PILL_BG": "#08130a",
    "PILL_BORDER": "#1d2a1d", "KNOB_CAP_TOP": "#f2f4f7", "KNOB_CAP_BOTTOM": "#6f7b8b",
    "ON_ACCENT": "#0a1a05", "ON_ACCENT_DARK": "#0a1a05", "BADGE_OFF": "#2b323d",
    "VU_BASE": "#5cf03a", "LCD_BG": "#08130a", "LCD_TEXT": "#8cff5a",
}

# Couleurs du style « Minimal » : aplats très sombres, un seul accent corail
MINIMAL_PALETTE = {
    "BG_WINDOW": "#0b0b0c", "BG_GLOW": "#0b0b0c", "BG_COLUMN": "#141416",
    "BG_SECTION": "#141416", "BORDER_COLUMN": "#202024", "BORDER_SECTION": "#141416",
    "BG_PANEL": "#141416", "BG_PILL": "#1b1b1e", "BG_DEEP": "#0f0f11",
    "GREEN_NEON": "#ff6b3d", "GREEN_TEXT": "#ff6b3d", "GREEN_BRIGHT": "#ff9472",
    "GREEN_DIM": "#3d2219", "GREEN_LABEL": "#85858b", "GREEN_SCALE": "#2a2a2f",
    "TEXT_PRIMARY": "#ececee", "TEXT_SECONDARY": "#88888e", "TEXT_MUTED": "#525258",
    "BUTTON_BG": "#1d1d20", "BUTTON_HOVER": "#26262a", "PILL_BG": "#1b1b1e",
    "PILL_BORDER": "#1b1b1e", "KNOB_CAP_TOP": "#1d1d20", "KNOB_CAP_BOTTOM": "#1d1d20",
    "ON_ACCENT": "#1f0b04", "ON_ACCENT_DARK": "#1f0b04", "BADGE_OFF": "#1d1d20",
    "VU_BASE": "#ececee", "LCD_BG": "#0f0f11", "LCD_TEXT": "#ececee",
}

INTERFACES: dict[str, Interface] = {i.key: i for i in (
    Interface("reka", "REKA", "Le style d'origine : panneaux arrondis, knobs lumineux, halo de couleur."),
    Interface("cantabile", "Cantabile",
              "Logiciel de scène : gris unis et plats, angles nets, canaux en lignes façon rack.",
              CANTABILE_PALETTE, font="Segoe UI", radius=0.2, max_radius=3, knob="flat",
              fader="flat", vu="flat", glow=False, background="flat",
              orientation=HORIZONTAL, density="compacte"),
    Interface("retro", "Lecteur 2000",
              "Lecteur audio des années 2000 : métal en relief, afficheurs LCD verts, "
              "boutons chromés et égaliseur animé.",
              RETRO_PALETTE, font="Tahoma", radius=0.3, max_radius=4, knob="chrome",
              fader="chrome", vu="lcd", borders="bevel", lcd=True, background="brushed",
              visualizer=True),
    Interface("minimal", "Minimal",
              "DAW moderne : aplats sans bordure, faders en simple barre, texte réduit au "
              "strict nécessaire, beaucoup d'espace.",
              MINIMAL_PALETTE, radius=0.6, max_radius=8, knob="arc", fader="line", vu="line",
              borders="none", glow=False, background="flat", titles=False, scale_labels=False,
              density="aeree"),
)}


def interface(key: str | None) -> Interface:
    """Style d'interface d'une clé de config ; celui d'origine si elle est inconnue."""
    return INTERFACES.get(str(key or ""), INTERFACES[DEFAULT_INTERFACE])


def choice(value, choices: dict, default: str) -> str:
    """Valeur de config ramenée à une clé connue."""
    return value if isinstance(value, str) and value in choices else default


def density_factor(key: str | None) -> float:
    return DENSITIES[choice(key, DENSITIES, DEFAULT_DENSITY)][1]


def apply_interface(ui_cfg: dict, key: str) -> bool:
    """Écrit un style d'interface dans la config `ui`, avec sa disposition conseillée
    (orientation, densité) ; False si la clé est inconnue."""
    chosen = INTERFACES.get(key)
    if chosen is None:
        return False
    ui_cfg["interface"] = key
    ui_cfg["orientation"] = chosen.orientation
    ui_cfg["density"] = chosen.density
    return True
