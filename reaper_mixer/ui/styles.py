"""
Palette, thèmes de couleur, polices (Inter embarquée) et feuille de style QSS.

Les widgets dessinés à la main (knob, fader, VU, pills) lisent directement
les couleurs ci-dessous (styles.GREEN_NEON…) à chaque dessin ; le reste passe
par la QSS appliquée à l'application.

Thèmes : la palette de référence est verte. Un thème donne une couleur
d'accent ; toutes les teintes vertes (accent, fonds teintés, bordures, textes
gris-vert) tournent vers sa teinte, en gardant leur luminosité. Le rouge du
mute, le jaune des VU et le blanc ne changent pas.

Couleurs par élément (config ui.colors) : chaque élément (knob Discord,
bouton Muet, VU…) peut avoir sa propre couleur, pour toute la console ou pour
un seul canal ; sinon il suit le thème. Voir element_color().

Style d'interface (config ui.interface, voir ui/interfaces.py) : la forme de
toute la console (arrondis, reliefs, knobs, faders, VU, police, fond). Un
style a sa palette, qui remplace celle du thème, sauf si l'utilisateur garde
ses couleurs (ui.keep_colors) ; les widgets dessinés lisent INTERFACE, rad(),
draw_box(), fader_shape() et vu_shape().
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication

from core import config
from ui import interfaces
from ui.interfaces import Interface

log = logging.getLogger(__name__)

# --- Palette de référence --------------------------------------------------
BG_BLACK = "#000000"
BG_PANEL = "#0a1510"      # Fond strip subtil
BG_PILL = "#0a1510"       # Fond des pills FX
GREEN_NEON = "#00ff88"    # Accent principal
GREEN_BRIGHT = "#00d4aa"  # Hover
GREEN_DIM = "#1a4030"     # Éléments inactifs
GREEN_TEXT = "#00ff88"
GREEN_LABEL = "#4d8870"   # Labels discrets
GREEN_SCALE = "#004d2a"   # Graduation dB
WHITE = "#ffffff"
RED_MUTE = "#ff2d2d"
YELLOW_VU = "#ffcc00"

# --- Compléments relevés sur la maquette ------------------------------------
BG_WINDOW = "#040b07"         # fond général (sous le dégradé)
BG_GLOW = "#001a0f"           # halo radial, centre-droit
BG_COLUMN = "#0e1612"         # colonnes (strips, master)
BG_SECTION = "#070e0a"        # blocs internes (effets, knobs, fader)
BORDER_COLUMN = "#16201b"
BORDER_SECTION = "#111a15"
TEXT_PRIMARY = "#e2e8e5"      # noms, valeurs
TEXT_SECONDARY = "#a3ada8"    # labels
TEXT_MUTED = "#5f6b65"
BUTTON_BG = "#1b2a24"
BUTTON_HOVER = "#223630"
PILL_BG = "#15201b"
PILL_BORDER = "#24332c"
KNOB_CAP_TOP = "#3a4642"      # dégradé du bouton des knobs
KNOB_CAP_BOTTOM = "#1c2421"
RED_DIM = "#3a1416"           # LED de saturation éteinte
TEXT_ON_RED = "#2b0707"
BG_DEEP = "#030b06"           # fonds très sombres (badge, bouton projet, Intellipan)
ON_ACCENT = "#00341c"         # encre sur l'accent (badge FX allumé)
ON_ACCENT_DARK = "#00140a"    # texte des boutons principaux
BADGE_OFF = "#0d1a14"         # badge FX éteint
# Dérivées de la palette (voir _derive) ; un style d'interface peut les fixer
VU_BASE = GREEN_NEON          # couleur des niveaux normaux des VU-mètres
LCD_BG = BG_DEEP              # afficheurs LCD (style « Lecteur 2000 »)
LCD_TEXT = GREEN_NEON
_DERIVED = ("VU_BASE", "LCD_BG", "LCD_TEXT")

# --- Palette « Dark Studio Pro » (thème par défaut, façon DAW) --------------
# Neutres gris-bleu et accent bleu studio, appliqués tels quels (sans rotation).
STUDIO_PALETTE = {
    "BG_WINDOW": "#0f1115", "BG_GLOW": "#141821", "BG_COLUMN": "#161920",
    "BG_SECTION": "#12151b", "BORDER_COLUMN": "#2a2f3d", "BORDER_SECTION": "#232836",
    "BG_PANEL": "#161920", "BG_PILL": "#1c2029", "BG_DEEP": "#0b0d11",
    "GREEN_NEON": "#3b82f6", "GREEN_TEXT": "#3b82f6", "GREEN_BRIGHT": "#60a5fa",
    "GREEN_DIM": "#1e3a68", "GREEN_LABEL": "#94a3b8", "GREEN_SCALE": "#3a4152",
    "TEXT_PRIMARY": "#f8fafc", "TEXT_SECONDARY": "#94a3b8", "TEXT_MUTED": "#5b6577",
    "BUTTON_BG": "#1f2430", "BUTTON_HOVER": "#272d3b", "PILL_BG": "#1c2029",
    "PILL_BORDER": "#2a2f3d", "KNOB_CAP_TOP": "#3a4150", "KNOB_CAP_BOTTOM": "#1b1f28",
    "ON_ACCENT": "#0b1220", "ON_ACCENT_DARK": "#0b1220", "BADGE_OFF": "#1a1e27",
}

# Couleurs fixes des VU-mètres et des boutons d'état (indépendantes du thème)
VU_GREEN = "#22c55e"
VU_YELLOW = "#facc15"
VU_RED = "#ef4444"
MUTE_ON = "#b91c1c"          # rouge sombre
MUTE_ON_HOVER = "#dc2626"
SOLO_ON = "#f59e0b"          # ambre
SOLO_ON_HOVER = "#fbbf24"
TEXT_ON_SOLO = "#1c1203"

# --- Thèmes ----------------------------------------------------------------
DEFAULT_THEME = "studio"
THEMES = {                    # clé (config ui.theme) → (nom affiché, accent)
    "studio": ("Dark Studio Pro", "#3b82f6"),
    "neon": ("Vert néon", "#00ff88"),
    "cyan": ("Cyan", "#00e5ff"),
    "bleu": ("Bleu", "#3d8bff"),
    "violet": ("Violet", "#a66bff"),
    "rose": ("Rose", "#ff4fb8"),
    "rouge": ("Rouge", "#ff3b4f"),
    "orange": ("Orange", "#ff9a1f"),
    "or": ("Or", "#ffd23f"),
    "blanc": ("Blanc", "#e9eef0"),
}
# Couleurs qui suivent le thème (les autres sont fixes)
_THEMED = ("BG_PANEL", "BG_PILL", "GREEN_NEON", "GREEN_BRIGHT", "GREEN_DIM", "GREEN_TEXT",
           "GREEN_LABEL", "GREEN_SCALE", "BG_WINDOW", "BG_GLOW", "BG_COLUMN", "BG_SECTION",
           "BORDER_COLUMN", "BORDER_SECTION", "TEXT_PRIMARY", "TEXT_SECONDARY", "TEXT_MUTED",
           "BUTTON_BG", "BUTTON_HOVER", "PILL_BG", "PILL_BORDER", "KNOB_CAP_TOP",
           "KNOB_CAP_BOTTOM", "BG_DEEP", "ON_ACCENT", "ON_ACCENT_DARK", "BADGE_OFF")
_BASE = {name: globals()[name] for name in _THEMED}
MIN_ACCENT_CONTRAST = 4.5     # contraste minimal accent / fond (lisibilité, WCAG AA)
HOVER_TOWARD_WHITE = 0.22     # survol des boutons principaux : accent éclairci
_current_theme = DEFAULT_THEME

DEFAULT_FONT = "Inter"
FONT_FAMILY = DEFAULT_FONT    # police choisie (config ui.font_family), Inter par défaut
FONT_FALLBACK = "Segoe UI"
FONT_FILES = ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf")

# --- Styles des widgets dessinés à la main ------------------------------------
FADER_STYLES = {              # clé (config ui.fader_style) → nom affiché
    "console": "Console (capuchon carré)",
    "rond": "Rond",
    "fin": "Fin (barre)",
    "vintage": "Vintage (strié)",
}
VU_STYLES = {                 # clé (config ui.vu_style) → nom affiché
    "segments": "LED fines",
    "led": "LED larges",
    "continu": "Continu",
}
DEFAULT_FADER_STYLE = "console"
DEFAULT_VU_STYLE = "segments"
FADER_STYLE = DEFAULT_FADER_STYLE
VU_STYLE = DEFAULT_VU_STYLE

# --- Style d'interface et disposition (config ui.interface…) -----------------
INTERFACE: Interface = interfaces.interface(None)
KEEP_COLORS = False           # mode mixte : couleurs du thème par-dessus le style
DENSITY = interfaces.DEFAULT_DENSITY
ORIENTATION = interfaces.DEFAULT_ORIENTATION
ANIMATIONS = interfaces.DEFAULT_ANIMATIONS
USER_FONT = DEFAULT_FONT      # police choisie (FONT_FAMILY : celle appliquée)
_user_colors: dict[str, str] = {}

# --- Couleurs par élément ---------------------------------------------------
# Clés de config ui.colors : « mute » (toute la console), « mute@2 » (canal 3
# seulement), « vu@master » (Master seulement). La couleur d'un canal passe
# avant celle de toute la console ; sans couleur, l'élément suit le thème.
MASTER_SCOPE = "master"
STRIP_ELEMENTS = ("send0", "send1", "send2", "send3", "fader", "vu", "mute", "fx_badge",
                  "fx_pill", "pad", "apps", "name", "panel")
MASTER_ELEMENTS = ("headphone", "vu", "engines", "media", "panel")
ELEMENT_LABELS = {            # « send0 »… : nom du knob d'envoi (config voicemeeter.routing)
    "fader": "Fader",
    "vu": "VU-mètres",
    "mute": "Bouton Muet (allumé)",
    "fx_badge": "Badge FX",
    "fx_pill": "Effets (pills)",
    "pad": "Intellipan (petit cube)",
    "apps": "Bouton Apps",
    "name": "Nom du canal",
    "panel": "Fond du panneau",
    "headphone": "Knob Casque",
    "engines": "Boutons Voicemeeter / REAPER",
    "media": "Lecteur de musique",
}
COLORS: dict[str, str] = {}   # couleurs choisies (config ui.colors), « #rrggbb »
_panel_opacity = 1.0


def _valid_colors(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    colors = {}
    for key, raw in value.items():
        c = QColor(str(raw)) if isinstance(raw, str) and raw.startswith("#") else QColor()
        if isinstance(key, str) and c.isValid():
            colors[key] = c.name()
    return colors


def configure(ui_cfg: dict) -> bool:
    """Police, styles de widgets, couleurs par élément, style d'interface et
    disposition d'après la config `ui` ; True si quelque chose a changé.
    Appeler ensuite set_theme() : la palette dépend du style d'interface."""
    global FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS, INTERFACE, KEEP_COLORS, DENSITY
    global ORIENTATION, ANIMATIONS, USER_FONT, _user_colors
    before = (FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS, INTERFACE.key, KEEP_COLORS, DENSITY,
              ORIENTATION, ANIMATIONS)
    USER_FONT = str(ui_cfg.get("font_family") or DEFAULT_FONT).strip() or DEFAULT_FONT
    fader = ui_cfg.get("fader_style")
    FADER_STYLE = fader if fader in FADER_STYLES else DEFAULT_FADER_STYLE
    vu = ui_cfg.get("vu_style")
    VU_STYLE = vu if vu in VU_STYLES else DEFAULT_VU_STYLE
    INTERFACE = interfaces.interface(ui_cfg.get("interface"))
    KEEP_COLORS = bool(ui_cfg.get("keep_colors", False))
    DENSITY = interfaces.choice(ui_cfg.get("density"), interfaces.DENSITIES,
                                interfaces.DEFAULT_DENSITY)
    ORIENTATION = interfaces.choice(ui_cfg.get("orientation"), interfaces.ORIENTATIONS,
                                    interfaces.DEFAULT_ORIENTATION)
    ANIMATIONS = interfaces.choice(ui_cfg.get("animations"), interfaces.ANIMATIONS,
                                   interfaces.DEFAULT_ANIMATIONS)
    # Police du style, si l'utilisateur garde la police d'origine
    FONT_FAMILY = INTERFACE.font if INTERFACE.font and USER_FONT == DEFAULT_FONT else USER_FONT
    _user_colors = _valid_colors(ui_cfg.get("colors"))
    COLORS = _user_colors if uses_theme_colors() else {}
    return before != (FONT_FAMILY, FADER_STYLE, VU_STYLE, COLORS, INTERFACE.key, KEEP_COLORS,
                      DENSITY, ORIENTATION, ANIMATIONS)


def uses_theme_colors(iface: Interface | None = None, keep: bool | None = None) -> bool:
    """La console suit le thème et les couleurs choisies (Apparence, Couleurs) :
    style sans palette propre, ou mode mixte."""
    iface = INTERFACE if iface is None else iface
    return not iface.palette or (KEEP_COLORS if keep is None else keep)


# --- Formes selon le style d'interface ----------------------------------------
def rad(radius: float) -> float:
    """Arrondi d'un élément (pensé pour REKA) dans le style d'interface en cours."""
    return min(radius * INTERFACE.radius, INTERFACE.max_radius)


def fader_shape() -> str:
    """Dessin du curseur des faders : celui du style, sinon celui choisi dans Apparence."""
    return INTERFACE.fader or FADER_STYLE


def vu_shape() -> str:
    return INTERFACE.vu or VU_STYLE


def bevelled() -> bool:
    return INTERFACE.borders == "bevel"


def draw_bevel(p: QPainter, rect: QRectF, sunken: bool = False, strength: float = 1.0) -> None:
    """Relief façon années 2000 : arête claire en haut à gauche, sombre en bas
    à droite (l'inverse si `sunken`)."""
    light = color(WHITE, 0.42 * strength)
    dark = color(BG_BLACK, 0.55 * strength)
    top_left, bottom_right = (dark, light) if sunken else (light, dark)
    r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    p.save()
    p.setBrush(QColor(0, 0, 0, 0))
    p.setPen(QPen(top_left, 1))
    p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left(), r.top()))
    p.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
    p.setPen(QPen(bottom_right, 1))
    p.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.bottom()))
    p.drawLine(QPointF(r.right(), r.bottom()), QPointF(r.left(), r.bottom()))
    p.restore()


def draw_box(p: QPainter, rect: QRectF, radius: float, sunken: bool = False) -> None:
    """Rectangle arrondi (plume et brosse en cours) à la forme du style : arrondi
    adapté, et relief pour le style « Lecteur 2000 »."""
    r = rad(radius)
    p.drawRoundedRect(rect, r, r)
    if bevelled():
        draw_bevel(p, rect, sunken)


def animations(level: str = "on") -> bool:
    """Les animations de ce niveau sont-elles permises ? « on » : décoratives
    (pulsation, fondu) ; « reduced » : utiles (knob qui glisse, égaliseur)."""
    if ANIMATIONS == "off":
        return False
    return level == "reduced" or ANIMATIONS == "on"


def spacing(px: float) -> int:
    """Écart ou marge à la densité choisie."""
    return max(0, round(px * interfaces.density_factor(DENSITY)))


def color_key(element: str, scope: int | str | None = None) -> str:
    """Clé de ui.colors : « mute » pour toute la console, « mute@2 » pour un canal."""
    return element if scope is None else f"{element}@{scope}"


def element_color(element: str, scope: int | str | None = None,
                  colors: dict | None = None) -> str | None:
    """Couleur choisie pour un élément : celle de son canal (ou du Master), sinon
    celle de toute la console ; None s'il suit le thème. `colors` : une config
    ui.colors à consulter à la place de celle appliquée."""
    colors = COLORS if colors is None else _valid_colors(colors)
    if scope is not None and color_key(element, scope) in colors:
        return colors[color_key(element, scope)]
    return colors.get(element)


def element_default(element: str) -> str:
    """Couleur d'un élément quand il suit le thème."""
    return {"mute": MUTE_ON, "pad": RED_MUTE, "name": TEXT_PRIMARY, "vu": VU_BASE,
            "panel": BG_COLUMN}.get(element, GREEN_NEON)


def accent(element: str, scope: int | str | None = None) -> str:
    """Couleur principale d'un élément (arc d'un knob, remplissage d'un fader…)."""
    return element_color(element, scope) or element_default(element)


def accent_dim(element: str, scope: int | str | None = None) -> str:
    """Teinte sombre assortie (piste d'un knob, bordure éteinte)."""
    custom = element_color(element, scope)
    return _mix(QColor(custom), QColor(BG_SECTION), 0.7).name() if custom else GREEN_DIM


def accent_bright(element: str, scope: int | str | None = None) -> str:
    """Teinte de survol assortie."""
    custom = element_color(element, scope)
    return _mix(QColor(custom), QColor(WHITE), HOVER_TOWARD_WHITE).name() if custom else GREEN_BRIGHT


def mix(a: str, b: str, amount: float) -> str:
    """Couleur entre `a` (0) et `b` (1)."""
    return _mix(QColor(a), QColor(b), amount).name()


def ink_on(value: str) -> str:
    """Encre lisible sur une couleur de fond (texte d'un bouton allumé)."""
    background = QColor(value)
    dark = QColor(ON_ACCENT_DARK)
    return dark.name() if contrast(background, dark) >= contrast(background, QColor(WHITE)) else WHITE


def mute_style(value: str) -> str:
    """QSS du bouton Muet allumé dans une couleur choisie."""
    hover = _mix(QColor(value), QColor(WHITE), 0.15).name()
    return (f"QPushButton#muteButton:checked {{ background: {value}; border: 1px solid {hover};"
            f" color: {ink_on(value)}; }}"
            f"QPushButton#muteButton:checked:hover {{ background: {hover}; }}")


def panel_style(value: str) -> str:
    """QSS du fond d'une colonne (strip, Master) dans une couleur choisie, avec
    l'opacité des panneaux en cours."""
    return f"QFrame#column {{ background: {_rgba(value, _panel_opacity)}; }}"


def font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Police de l'interface (Inter, repli Segoe UI) en taille pixel."""
    f = QFont()
    f.setFamilies([FONT_FAMILY, FONT_FALLBACK])
    f.setPixelSize(pixel_size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def color(value: str, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def load_fonts() -> bool:
    """Charge Inter depuis assets/ ; renvoie False si elle est indisponible."""
    loaded = False
    for name in FONT_FILES:
        path = config.resource_path(f"assets/{name}")
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded = True
        else:
            log.warning("Police introuvable ou illisible : %s", path)
    return loaded


def theme_accent(theme: str | None) -> QColor | None:
    """Couleur d'accent d'un thème (clé de THEMES ou « #rrggbb »), None si invalide."""
    if theme in THEMES:
        return QColor(THEMES[theme][1])
    accent = QColor(str(theme or ""))
    return accent if str(theme or "").startswith("#") and accent.isValid() else None


def luminance(color: QColor) -> float:
    """Luminance relative (WCAG) d'une couleur."""
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * amount),
                  round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def _rotate(value: str, hue_shift: float, saturation: float) -> str:
    h, s, l, _a = QColor(value).getHslF()
    if h < 0:                        # gris pur : pas de teinte à tourner
        return value
    return QColor.fromHslF((h + hue_shift) % 1.0, max(0.0, min(1.0, s * saturation)), l).name()


def theme_palette(theme: str | None) -> tuple[str, dict[str, str]]:
    """(thème retenu, couleurs de sa palette) sans l'appliquer : le thème par
    défaut si `theme` est inconnu. Sert aussi aux aperçus des thèmes prédéfinis."""
    accent = theme_accent(theme)
    if accent is None:
        theme, accent = DEFAULT_THEME, QColor(THEMES[DEFAULT_THEME][1])
    values = dict(_BASE)
    if theme == "studio":
        values.update(STUDIO_PALETTE)
    elif theme != "neon":
        ah, as_, al, _ = accent.getHslF()
        bh, bs, _bl, _ = QColor(_BASE["GREEN_NEON"]).getHslF()
        shift = ah - bh if ah >= 0 else 0.0
        saturation = as_ / bs if ah >= 0 and bs > 0 else 0.0
        values = {name: _rotate(value, shift, saturation) for name, value in _BASE.items()}
        # Accent assez clair pour se lire sur les fonds (un bleu pur #0000ff serait
        # presque invisible) : on l'éclaircit jusqu'au contraste voulu
        background = QColor(values["BG_COLUMN"])
        while contrast(accent, background) < MIN_ACCENT_CONTRAST and al < 0.95:
            al = min(0.95, al + 0.02)
            accent = QColor.fromHslF(max(ah, 0.0), as_, al)
        values["GREEN_NEON"] = values["GREEN_TEXT"] = accent.name()
        # Survol des boutons principaux : dérivé de l'accent (le texte sombre
        # reste lisible), et non de la teinte voisine de la palette verte
        values["GREEN_BRIGHT"] = _mix(accent, QColor("#ffffff"), HOVER_TOWARD_WHITE).name()
        if contrast(accent, QColor(values["ON_ACCENT"])) < 4.5:   # « FX » du badge allumé
            values["ON_ACCENT"] = values["ON_ACCENT_DARK"]
    return theme, _derive(values)


def _derive(values: dict[str, str]) -> dict[str, str]:
    """Couleurs dérivées que la palette ne donne pas (VU, afficheurs LCD)."""
    values = dict(values)
    values.setdefault("VU_BASE", values["GREEN_NEON"])
    values.setdefault("LCD_BG", values["BG_DEEP"])
    values.setdefault("LCD_TEXT", values["GREEN_NEON"])
    return values


def look_palette(theme: str | None, iface: Interface | None = None,
                 keep_colors: bool | None = None) -> tuple[str, dict[str, str]]:
    """(thème retenu, palette appliquée) : celle du thème, ou celle du style
    d'interface s'il en a une et que l'utilisateur ne garde pas ses couleurs."""
    iface = INTERFACE if iface is None else iface
    theme, values = theme_palette(theme)
    if not uses_theme_colors(iface, keep_colors):
        values = {**{k: v for k, v in values.items() if k not in _DERIVED}, **iface.palette}
    return theme, _derive(values)


def set_theme(theme: str | None) -> str:
    """Recolore la palette d'après le thème ; renvoie le thème appliqué (le
    thème par défaut si `theme` est inconnu). Appeler ensuite refresh()."""
    global _current_theme
    theme, values = look_palette(theme)
    globals().update(values)
    _current_theme = theme
    return theme


# --- Thèmes prédéfinis --------------------------------------------------------
# Un thème prédéfini règle d'un coup la couleur d'accent (ui.theme), les couleurs
# par élément (ui.colors) et le style des faders et des VU-mètres. L'arrière-plan,
# la police et l'opacité des panneaux ne sont pas touchés.
@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    description: str
    theme: str                                   # clé de THEMES ou « #rrggbb »
    colors: dict[str, str] = field(default_factory=dict)
    fader_style: str = DEFAULT_FADER_STYLE
    vu_style: str = DEFAULT_VU_STYLE

    def swatches(self) -> list[str]:
        """Couleurs qui résument le thème (aperçu) : l'accent puis ses couleurs propres."""
        colors = [theme_palette(self.theme)[1]["GREEN_NEON"]]
        for value in self.colors.values():
            if value.lower() not in (c.lower() for c in colors):
                colors.append(value)
        return colors[:5]


_CHANNEL_COLORS = ("#22c55e", "#f59e0b", "#5865f2", "#06b6d4", "#ec4899", "#a855f7")
PRESETS: dict[str, Preset] = {preset.key: preset for preset in (
    Preset("studio", "Dark Studio Pro", "Sobre, gris-bleu, façon station audio.", "studio"),
    Preset("neon", "Néon", "Le vert néon d'origine de REKA.", "neon"),
    Preset("synthwave", "Synthwave", "Violet, rose et cyan, années 80.", "#b266ff",
           {"send0": "#ff4fb8", "send1": "#00e5ff", "send2": "#b266ff", "send3": "#ffd23f",
            "fader": "#ff4fb8", "vu": "#00e5ff", "fx_badge": "#ff4fb8",
            "headphone": "#00e5ff", "engines": "#ff4fb8", "media": "#00e5ff"},
           "rond", "continu"),
    Preset("braise", "Braise", "Orange et rouge, chaleureux.", "orange",
           {"fader": "#ff6a3d", "vu": "#ffb347", "fx_badge": "#ff6a3d", "headphone": "#ff6a3d",
            "pad": "#ffd23f"},
           "vintage", "led"),
    Preset("glace", "Glace", "Cyan et blanc givré.", "cyan",
           {"fader": "#e9f7ff", "name": "#e9f7ff", "headphone": "#e9f7ff", "vu": "#7dd3fc"},
           "fin", "segments"),
    Preset("matrix", "Matrix", "Noir et vert terminal.", "#39ff14",
           {"name": "#7dff6a", "pad": "#39ff14"},
           "fin", "led"),
    Preset("ornoir", "Or noir", "Doré sur fond sombre.", "or",
           {"name": "#ffe9a8", "fx_pill": "#ffd23f"},
           "vintage", "continu"),
    Preset("sakura", "Sakura", "Rose pastel et blanc.", "#ff8fc7",
           {"fader": "#ffffff", "vu": "#ffb3d9", "name": "#ffe3f1", "headphone": "#ffffff"},
           "rond", "segments"),
    Preset("ocean", "Océan", "Bleus et turquoise.", "bleu",
           {"send0": "#22d3ee", "send1": "#3d8bff", "send2": "#818cf8", "send3": "#2dd4bf",
            "fader": "#3d8bff", "vu": "#22d3ee"},
           "console", "continu"),
    Preset("rubis", "Rubis", "Rouge profond ; le Muet passe à l'ambre pour rester visible.", "rouge",
           {"mute": "#ffb020", "pad": "#ffffff"},
           "console", "segments"),
    Preset("arcade", "Arcade", "Une couleur par canal, pour s'y retrouver d'un coup d'œil.", "studio",
           {f"{element}@{index}": value for index, value in enumerate(_CHANNEL_COLORS)
            for element in ("fader", "name", "fx_badge", "apps")},
           "console", "led"),
    Preset("mono", "Monochrome", "Blanc et gris, sans couleur.", "blanc", {}, "fin", "continu"),
)}


def apply_preset(ui_cfg: dict, key: str) -> bool:
    """Écrit un thème prédéfini dans la config `ui` (à appliquer ensuite comme
    tout changement d'apparence) ; False si la clé est inconnue."""
    preset = PRESETS.get(key)
    if preset is None:
        return False
    ui_cfg["theme"] = preset.theme
    ui_cfg["colors"] = dict(preset.colors)
    ui_cfg["fader_style"] = preset.fader_style
    ui_cfg["vu_style"] = preset.vu_style
    return True


def current_preset(ui_cfg: dict) -> str | None:
    """Thème prédéfini que la config `ui` reproduit exactement, sinon None."""
    theme = ui_cfg.get("theme", DEFAULT_THEME)
    colors = _valid_colors(ui_cfg.get("colors"))
    fader = ui_cfg.get("fader_style") or DEFAULT_FADER_STYLE
    vu = ui_cfg.get("vu_style") or DEFAULT_VU_STYLE
    for preset in PRESETS.values():
        if ((preset.theme, preset.fader_style, preset.vu_style) == (theme, fader, vu)
                and _valid_colors(preset.colors) == colors):
            return preset.key
    return None


def current_theme() -> str:
    return _current_theme


# État de l'apparence, sauvegardé le temps d'un aperçu (previewing)
_LOOK_STATE = (*_THEMED, *_DERIVED, "FONT_FAMILY", "FADER_STYLE", "VU_STYLE", "COLORS",
               "INTERFACE", "KEEP_COLORS", "DENSITY", "ORIENTATION", "ANIMATIONS", "USER_FONT",
               "_user_colors", "_current_theme", "_panel_opacity")


@contextmanager
def previewing(ui_cfg: dict, interface_key: str | None = None, keep_colors: bool | None = None):
    """Apparence d'une config `ui` (avec un autre style d'interface, ou le mode
    mixte changé) le temps du bloc, puis retour à celle en cours. Sert aux
    aperçus : un widget dessiné et capturé dans le bloc a le look demandé
    (lui donner aussi build_stylesheet() comme feuille de style)."""
    saved = {name: globals()[name] for name in _LOOK_STATE}
    try:
        cfg = dict(ui_cfg)
        if interface_key is not None:
            cfg["interface"] = interface_key
        if keep_colors is not None:
            cfg["keep_colors"] = keep_colors
        configure(cfg)
        set_theme(cfg.get("theme", DEFAULT_THEME))
        yield
    finally:
        globals().update(saved)


def _rgba(value: str, alpha: float) -> str:
    c = QColor(value)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {max(0.0, min(1.0, alpha)):.3f})"


def build_stylesheet(panel_opacity: float = 1.0) -> str:
    """QSS de l'application, aux couleurs du thème en cours. `panel_opacity`
    < 1 laisse voir l'arrière-plan (image personnalisée) à travers les
    colonnes et leurs blocs."""
    global _panel_opacity
    _panel_opacity = panel_opacity
    return (_stylesheet()
            .replace("@COLUMN_BG@", _rgba(BG_COLUMN, panel_opacity))
            .replace("@SECTION_BG@", _rgba(BG_SECTION, panel_opacity)))


def refresh(app: QApplication, panel_opacity: float = 1.0) -> None:
    """Ré-applique palette et QSS (après un changement de thème ou d'opacité)."""
    _apply_palette(app)
    if app.font().family() != FONT_FAMILY:
        app.setFont(font(11))
    stylesheet = build_stylesheet(panel_opacity)
    if app.styleSheet() != stylesheet:   # re-styler toute l'app coûte : seulement si besoin
        app.setStyleSheet(stylesheet)


def set_panel_opacity(app: QApplication, panel_opacity: float) -> None:
    refresh(app, panel_opacity)


def apply_theme(app: QApplication, panel_opacity: float = 1.0, theme: str | None = None) -> None:
    """Charge les polices et applique thème, palette et QSS à toute l'application."""
    load_fonts()
    app.setFont(font(11))
    set_theme(theme)
    _apply_palette(app)
    app.setStyleSheet(build_stylesheet(panel_opacity))


def _apply_palette(app: QApplication) -> None:
    palette = app.palette()
    for role, value in (
        (QPalette.ColorRole.Window, BG_WINDOW),
        (QPalette.ColorRole.Base, BG_SECTION),
        (QPalette.ColorRole.AlternateBase, BG_COLUMN),
        (QPalette.ColorRole.WindowText, TEXT_PRIMARY),
        (QPalette.ColorRole.Text, TEXT_PRIMARY),
        (QPalette.ColorRole.ButtonText, TEXT_PRIMARY),
        (QPalette.ColorRole.Button, BUTTON_BG),
        (QPalette.ColorRole.Highlight, GREEN_DIM),
        (QPalette.ColorRole.HighlightedText, WHITE),
        (QPalette.ColorRole.ToolTipBase, BG_COLUMN),
        (QPalette.ColorRole.ToolTipText, TEXT_PRIMARY),
        (QPalette.ColorRole.PlaceholderText, TEXT_MUTED),
    ):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)


def _px(value: float) -> str:
    return f"{value:g}px"


def _stylesheet() -> str:
    def r(radius: float) -> str:
        return _px(rad(radius))

    return _base_stylesheet(r) + _interface_stylesheet()


def _base_stylesheet(r) -> str:
    return f"""
* {{
    font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
    outline: none;
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
}}
QMainWindow, QDialog {{
    background: {BG_WINDOW};
}}

/* ---- colonnes et blocs ---- */
QFrame#column {{
    background: @COLUMN_BG@;
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(14)};
}}
QFrame#section {{
    background: @SECTION_BG@;
    border: 1px solid {BORDER_SECTION};
    border-radius: {r(10)};
}}
QLabel {{
    background: transparent;
    border: none;
}}
QLabel#sectionTitle {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#knobLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 500;
}}
QLabel#latencyLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
    font-weight: 600;
}}
QLabel#latencyLabel[slow="true"] {{
    color: {SOLO_ON};
}}
QLabel#faderValue {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QLabel#faderValue[muted="true"] {{
    color: {TEXT_MUTED};
}}
QLabel#stripName {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    padding: 0px;
}}
QLineEdit#stripNameEdit {{
    background: {BG_SECTION};
    border: 1px solid {GREEN_DIM};
    border-radius: {r(5)};
    color: {TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 600;
    padding: 1px 4px;
    selection-background-color: {GREEN_DIM};
}}
QLabel#masterTitle {{
    color: {TEXT_PRIMARY};
    font-size: 18px;
    font-weight: 600;
}}
QLabel#masterValue {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#masterLabel {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
    font-weight: 500;
}}

/* ---- bouton Muet ---- */
QPushButton#muteButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(7)};
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#muteButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton#muteButton:checked {{
    background: {MUTE_ON};
    border: 1px solid {MUTE_ON_HOVER};
    color: {WHITE};
}}
QPushButton#muteButton:checked:hover {{
    background: {MUTE_ON_HOVER};
}}

/* ---- bouton Solo ---- */
QPushButton#soloButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(7)};
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#soloButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton#soloButton:checked {{
    background: {SOLO_ON};
    border: 1px solid {SOLO_ON_HOVER};
    color: {TEXT_ON_SOLO};
}}
QPushButton#soloButton:checked:hover {{
    background: {SOLO_ON_HOVER};
}}

/* ---- zone d'effets ---- */
QScrollArea#tabScroll, QScrollArea#tabScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollArea#fxScroll, QScrollArea#fxScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 2px 0;
    border: none;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0 2px;
    border: none;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {BORDER_COLUMN};
    border-radius: {r(3)};
}}
QScrollBar::handle:vertical {{
    min-height: 24px;
}}
QScrollBar::handle:horizontal {{
    min-width: 24px;
}}
QScrollBar::handle:hover, QScrollBar::handle:pressed {{
    background: {GREEN_DIM};
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    width: 0;
    height: 0;
}}

/* ---- dialogues ---- */
QDialog QLabel#dialogTitle {{
    color: {TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 600;
}}
QDialog QLabel#hint {{
    color: {TEXT_SECONDARY};
}}
QDialog QLabel#warning {{
    color: {YELLOW_VU};
}}
QDialog QLabel#ok {{
    color: {GREEN_NEON};
}}
QGroupBox {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(10)};
    margin-top: 18px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {TEXT_SECONDARY};
}}
QLineEdit, QComboBox {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(6)};
    padding: 5px 8px;
    selection-background-color: {GREEN_DIM};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {GREEN_DIM};
}}
QComboBox QAbstractItemView {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    selection-background-color: {GREEN_DIM};
}}
QListWidget {{
    background: {BG_SECTION};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(8)};
    padding: 4px;
}}
QListWidget::item {{
    padding: 5px 6px;
    border-radius: {r(5)};
}}
QListWidget::item:hover {{
    background: {BUTTON_BG};
}}
QListWidget::item:selected {{
    background: {GREEN_DIM};
    color: {WHITE};
}}
QPushButton {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(7)};
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {BUTTON_HOVER};
    border-color: {GREEN_DIM};
}}
QPushButton:pressed {{
    background: {GREEN_DIM};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QPushButton#primary {{
    background: {GREEN_NEON};
    border: none;
    color: {ON_ACCENT_DARK};
}}
QPushButton#primary:hover {{
    background: {GREEN_BRIGHT};
}}
QPushButton#primary:disabled {{
    background: {GREEN_DIM};
    color: {TEXT_MUTED};
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: {r(4)};
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QCheckBox::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QMenu {{
    background: {BG_COLUMN};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(8)};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px 6px 12px;
    border-radius: {r(5)};
}}
QMenu::item:selected {{
    background: {GREEN_DIM};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER_COLUMN};
    margin: 4px 6px;
}}
QToolTip {{
    background: {BG_COLUMN};
    color: {TEXT_PRIMARY};
    border: 1px solid {GREEN_DIM};
    border-radius: {r(4)};
    padding: 4px 6px;
}}
QMessageBox {{
    background: {BG_WINDOW};
}}

/* ---- onglets, curseurs, choix (réglages) ---- */
QTabWidget::pane {{
    border: none;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_SECONDARY};
    padding: 7px 12px;
    margin-right: 3px;
    border: 1px solid transparent;
    border-radius: {r(7)};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    background: {BUTTON_BG};
    border-color: {GREEN_DIM};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BUTTON_BG};
    border-radius: {r(2)};
}}
QSlider::sub-page:horizontal {{
    background: {GREEN_DIM};
    border-radius: {r(2)};
}}
QSlider::handle:horizontal {{
    background: {GREEN_NEON};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: {r(7)};
}}
QSlider::groove:vertical {{
    width: 4px;
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(2)};
}}
QSlider::add-page:vertical {{
    background: {GREEN_NEON};
    border-radius: {r(2)};
}}
QSlider::sub-page:vertical {{
    background: {BUTTON_BG};
    border-radius: {r(2)};
}}
QSlider::handle:vertical {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f8fafc, stop:0.48 #cbd5e1,
                                stop:0.5 {GREEN_NEON}, stop:0.56 #cbd5e1, stop:1 #94a3b8);
    border: 1px solid {BG_DEEP};
    height: 26px;
    width: 18px;
    margin: 0 -8px;
    border-radius: {r(4)};
}}
QSlider::handle:vertical:hover {{
    border-color: {GREEN_NEON};
}}
QSlider::handle:horizontal:hover {{
    background: {GREEN_BRIGHT};
}}
QRadioButton {{
    spacing: 8px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: {r(7)};
    border: 1px solid {GREEN_DIM};
    background: {BG_SECTION};
}}
QRadioButton::indicator:checked {{
    background: {GREEN_NEON};
    border-color: {GREEN_NEON};
}}
QLabel#credits {{
    color: {TEXT_SECONDARY};
}}

/* ---- volume par application ---- */
QLabel#appMixerTitle {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#appMixerHint {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
}}
QLabel#appName {{
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}
QLabel#appName:disabled {{
    color: {TEXT_MUTED};
}}
QLabel#appPercent {{
    color: {TEXT_SECONDARY};
    font-size: 10px;
}}

/* ---- mises à jour ---- */
QProgressBar {{
    background: {BUTTON_BG};
    border: 1px solid {BORDER_COLUMN};
    border-radius: {r(5)};
    height: 10px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: {GREEN_NEON};
    border-radius: {r(4)};
}}
"""


def _interface_stylesheet() -> str:
    """Ajouts du style d'interface en cours (après la QSS de base : ils l'emportent)."""
    if INTERFACE.borders == "bevel":
        return _bevel_stylesheet()
    if INTERFACE.borders == "none":
        return _flat_stylesheet()
    return ""


def _bevel_edges(base: str, sunken: bool = False, width: int = 2) -> str:
    """Bordure en relief (haut, droite, bas, gauche)."""
    light, dark = mix(base, WHITE, 0.32), mix(base, BG_BLACK, 0.55)
    top_left, bottom_right = (dark, light) if sunken else (light, dark)
    return (f"border: {width}px solid; "
            f"border-color: {top_left} {bottom_right} {bottom_right} {top_left};")


def _chrome(base: str) -> str:
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {mix(base, WHITE, 0.38)}, "
            f"stop:0.5 {base}, stop:1 {mix(base, BG_BLACK, 0.25)})")


def _lcd_font() -> str:
    return '"Consolas", "Lucida Console", "Courier New", monospace'


def _bevel_stylesheet() -> str:
    """« Lecteur 2000 » : panneaux en relief, boutons chromés, afficheurs LCD."""
    return f"""
QFrame#column {{
    {_bevel_edges(BG_COLUMN)}
}}
QFrame#section {{
    {_bevel_edges(BG_SECTION, sunken=True)}
}}
QPushButton, QPushButton#muteButton, QPushButton#soloButton {{
    background: {_chrome(BUTTON_BG)};
    {_bevel_edges(BUTTON_BG)}
}}
QPushButton:hover, QPushButton#muteButton:hover, QPushButton#soloButton:hover {{
    background: {_chrome(BUTTON_HOVER)};
}}
QPushButton:pressed, QPushButton#muteButton:pressed {{
    {_bevel_edges(BUTTON_BG, sunken=True)}
}}
QPushButton#muteButton:checked {{
    background: {_chrome(MUTE_ON)};
    {_bevel_edges(MUTE_ON, sunken=True)}
    color: {WHITE};
}}
QPushButton#soloButton:checked {{
    background: {_chrome(SOLO_ON)};
    {_bevel_edges(SOLO_ON, sunken=True)}
}}
QPushButton#primary {{
    background: {_chrome(GREEN_DIM)};
    {_bevel_edges(GREEN_DIM)}
    color: {LCD_TEXT};
}}
QLabel#stripName, QLabel#faderValue, QLabel#masterValue {{
    background: {LCD_BG};
    color: {LCD_TEXT};
    font-family: {_lcd_font()};
    {_bevel_edges(BG_SECTION, sunken=True, width=1)}
    padding: 1px 4px;
}}
QLabel#faderValue[muted="true"] {{
    color: {mix(LCD_TEXT, LCD_BG, 0.6)};
}}
QLabel#stripName {{
    font-size: 12px;
    font-weight: 700;
}}
QLabel#sectionTitle, QLabel#knobLabel, QLabel#masterLabel {{
    font-weight: 700;
}}
QLabel#masterTitle {{
    font-size: 16px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    background: {_chrome(BUTTON_BG)};
    {_bevel_edges(BUTTON_BG)}
}}
QGroupBox {{
    {_bevel_edges(BG_COLUMN)}
}}
"""


def _flat_stylesheet() -> str:
    """« Minimal » : aplats sans bordure, texte plus léger."""
    return f"""
QFrame#column {{
    border: 1px solid transparent;
}}
QFrame#section {{
    border: 1px solid transparent;
}}
QPushButton, QPushButton#muteButton, QPushButton#soloButton {{
    border: 1px solid transparent;
}}
QPushButton:hover, QPushButton#muteButton:hover, QPushButton#soloButton:hover {{
    border-color: transparent;
}}
QLabel#stripName {{
    font-weight: 500;
    color: {TEXT_SECONDARY};
}}
QLabel#knobLabel {{
    color: {TEXT_MUTED};
    font-weight: 400;
}}
QLabel#faderValue {{
    font-weight: 500;
}}
QLabel#masterTitle {{
    font-weight: 500;
}}
QGroupBox {{
    border-color: transparent;
}}
"""
