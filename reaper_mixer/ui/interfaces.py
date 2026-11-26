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
    # Palette construite à partir de 4 couleurs (fenêtre, colonnes, accent, texte) ;
    # `palette` la complète ou la corrige
    base: tuple[str, str, str, str] | None = None
    font: str | None = None          # police propre (si la police choisie est celle d'origine)
    radius: float = 1.0              # facteur des arrondis (1 = REKA)
    max_radius: float = 99.0         # arrondi maximal (px)
    knob: str = "reka"               # knobs : reka, flat, chrome, arc, cap, hardware,
                                     #   outline, pixel, dots, fluent, ring
    fader: str | None = None         # curseur des faders ; None = style choisi dans Apparence
                                     #   (sinon aussi flat, chrome, line, fluent, outline,
                                     #   pixel, touch, block)
    vu: str | None = None            # VU-mètres ; None = style choisi dans Apparence
                                     #   (sinon aussi flat, lcd, line, outline, pixel)
    borders: str = "solid"           # bordures des panneaux : solid, none, bevel, dashed, thick
    glow: bool = True                # halos et lueurs (knobs, badge, curseurs)
    lcd: bool = False                # afficheurs LCD (noms, valeurs, effets)
    background: str = "glow"         # fond de la fenêtre : glow, flat, brushed, grid, wood,
                                     #   aurora, scanlines, mica
    titles: bool = True              # titres de section (« Effets »)
    scale_labels: bool = True        # chiffres de l'échelle des faders
    visualizer: bool = False         # égaliseur visuel animé dans le Master
    needle: bool = False             # VU-mètre à aiguille dans le Master
    decor: str = ""                  # ornements des panneaux : screws (vis), brackets (coins HUD)
    panel_alpha: float = 1.0         # opacité propre des panneaux (verre dépoli)
    antialias: bool = True           # False : dessin pixelisé
    big: bool = False                # cibles tactiles : boutons et knobs plus grands
    orientation: str = DEFAULT_ORIENTATION   # disposition conseillée, appliquée au choix du style
    density: str = DEFAULT_DENSITY

    @property
    def own_shapes(self) -> bool:
        """Le style dessine ses propres faders et VU (les choix d'Apparence ne s'appliquent pas)."""
        return self.fader is not None or self.vu is not None

    @property
    def has_palette(self) -> bool:
        """Le style a ses propres couleurs (sinon il suit toujours le thème)."""
        return bool(self.palette or self.base)


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
    Interface("analog", "Console analogique",
              "Table de mixage de studio : métal brossé vissé, capuchons de couleur sur les "
              "knobs, VU-mètre à aiguille dans le Master.",
              base=("#141518", "#2a2d32", "#f0b44c", "#ece8df"),
              palette={"VU_BASE": "#7ed957", "LCD_BG": "#1a1206", "LCD_TEXT": "#ffc35c"},
              font="Arial", radius=0.3, max_radius=4, knob="cap", fader="console", vu="led",
              glow=False, background="brushed", decor="screws", needle=True),
    Interface("rack", "Rack 19\"",
              "Modules rackables en aluminium noir, vis aux coins, afficheurs rouges et "
              "boutons de matériel ; un canal par unité de rack.",
              base=("#050506", "#141416", "#ff4433", "#e4e4e6"),
              palette={"VU_BASE": "#3ddc55", "LCD_BG": "#140303", "LCD_TEXT": "#ff4d3a",
                       "KNOB_CAP_TOP": "#3a3a3e", "KNOB_CAP_BOTTOM": "#0c0c0e"},
              font="Arial", radius=0.15, max_radius=2, knob="hardware", fader="flat", vu="led",
              borders="bevel", glow=False, lcd=True, background="flat", decor="screws",
              orientation=HORIZONTAL, density="compacte"),
    Interface("fluent", "Windows 11",
              "Comme une application de Windows 11 : fond Mica, coins arrondis, curseurs et "
              "knobs façon Fluent, accent bleu clair.",
              base=("#1c1c1c", "#2b2b2b", "#60cdff", "#ffffff"),
              palette={"VU_BASE": "#60cdff"},
              font="Segoe UI Variable Text", radius=0.6, max_radius=8, knob="fluent",
              fader="fluent", vu="line", glow=False, background="mica"),
    Interface("touch", "Tactile",
              "Pour écran tactile ou tablette : gros boutons, gros curseurs, knobs plus grands, "
              "rien de superflu.",
              base=("#0e1216", "#1a2027", "#34d399", "#f1f5f9"),
              radius=1.1, max_radius=16, knob="flat", fader="touch", vu="continu", glow=False,
              background="flat", titles=False, scale_labels=False, big=True, density="aeree"),
    Interface("terminal", "Terminal",
              "Écran de terminal : texte vert monospace, cadres en pointillés, curseurs en "
              "blocs, lignes de balayage.",
              base=("#000000", "#030703", "#39ff6a", "#5dff86"),
              palette={"TEXT_SECONDARY": "#2fcf58", "TEXT_MUTED": "#1d8a3a",
                       "VU_BASE": "#39ff6a", "LCD_BG": "#000000", "LCD_TEXT": "#39ff6a",
                       "KNOB_CAP_TOP": "#030703", "KNOB_CAP_BOTTOM": "#030703"},
              font="Consolas", radius=0.0, max_radius=0, knob="dots", fader="block", vu="pixel",
              borders="dashed", glow=False, lcd=True, background="scanlines"),
    Interface("blueprint", "Plan d'architecte",
              "Calque bleu quadrillé : tout est tracé au trait blanc, comme un plan technique.",
              base=("#0b3a78", "#0f4a94", "#ffffff", "#ffffff"),
              palette={"BORDER_COLUMN": "#8fb8ea", "BORDER_SECTION": "#5d8fcf",
                       "TEXT_SECONDARY": "#cfe0f7", "TEXT_MUTED": "#9fc0ea",
                       "VU_BASE": "#ffffff", "GREEN_DIM": "#4f7fc0", "ON_ACCENT": "#0b3a78",
                       "ON_ACCENT_DARK": "#0b3a78"},
              font="Consolas", radius=0.0, max_radius=0, knob="outline", fader="outline",
              vu="outline", glow=False, background="grid", panel_alpha=0.35),
    Interface("pixel", "Pixel 8-bit",
              "Jeu vidéo rétro : gros pixels sans lissage, cadres épais, couleurs de console "
              "8 bits.",
              base=("#1a1c2c", "#29366f", "#ffcd75", "#f4f4f4"),
              palette={"VU_BASE": "#a7f070", "BUTTON_BG": "#3b5dc9", "BUTTON_HOVER": "#41a6f6",
                       "PILL_BG": "#1a1c2c", "BG_SECTION": "#333c57", "BORDER_COLUMN": "#0d0e18",
                       "BORDER_SECTION": "#0d0e18", "PILL_BORDER": "#0d0e18"},
              font="Courier New", radius=0.0, max_radius=0, knob="pixel", fader="pixel",
              vu="pixel", borders="thick", glow=False, background="flat", antialias=False),
    Interface("glass", "Verre",
              "Verre dépoli sur un fond aux couleurs d'aurore : panneaux translucides, bords "
              "lumineux, grands arrondis.",
              base=("#110d2a", "#2a2352", "#7df9ff", "#f5f3ff"),
              palette={"BORDER_COLUMN": "#7068b0", "BORDER_SECTION": "#544c92", "VU_BASE": "#7df9ff"},
              radius=1.3, max_radius=20, knob="reka", fader="rond", vu="continu",
              background="aurora", panel_alpha=0.45),
    Interface("hifi", "Hi-fi 70s",
              "Chaîne hi-fi vintage : façade argentée, coffret en noyer, boutons chromés et "
              "VU-mètre à aiguille rétro-éclairé.",
              base=("#2a1a10", "#d9d4c7", "#c2570c", "#2a2520"),
              palette={"KNOB_CAP_TOP": "#fbfbfb", "KNOB_CAP_BOTTOM": "#80858c",
                       "VU_BASE": "#3f8f3a", "LCD_BG": "#2a1a08", "LCD_TEXT": "#ffb547"},
              font="Georgia", radius=0.3, max_radius=4, knob="chrome", fader="vintage", vu="led",
              borders="bevel", glow=False, background="wood", decor="screws", needle=True),
    Interface("brutal", "Brutaliste",
              "Web brutaliste : blanc cassé, cadres noirs épais, angles droits, typographie "
              "grasse, aucune fioriture.",
              base=("#efece3", "#ffffff", "#0040ff", "#000000"),
              palette={"BORDER_COLUMN": "#000000", "BORDER_SECTION": "#000000",
                       "VU_BASE": "#000000", "KNOB_CAP_TOP": "#000000", "KNOB_CAP_BOTTOM": "#000000",
                       "BUTTON_BG": "#ffffff", "BUTTON_HOVER": "#ffe600"},
              font="Arial Black", radius=0.0, max_radius=0, knob="flat", fader="flat",
              vu="continu", borders="thick", glow=False, background="flat"),
    Interface("hud", "HUD sci-fi",
              "Affichage tête haute de vaisseau : traits cyan, coins en crochets, anneaux "
              "segmentés, grille holographique.",
              base=("#02070b", "#06121a", "#22d3ee", "#cffafe"),
              palette={"BORDER_COLUMN": "#0f4c5c", "BORDER_SECTION": "#0b3a46", "VU_BASE": "#22d3ee"},
              font="Bahnschrift", radius=0.0, max_radius=0, knob="ring", fader="outline",
              vu="segments", decor="brackets", background="grid", panel_alpha=0.75),
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
