"""Styles d'interface (réglages → Interface) : formes, palettes, disposition, aperçus."""

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from conftest import pump

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.MouseButton.NoButton
UI_KEYS = ("interface", "keep_colors", "orientation", "density", "animations", "theme", "colors",
           "font_family")


@pytest.fixture
def restore_look(qapp):
    """Remet l'apparence d'origine après le test (l'état des styles est global)."""
    from ui import styles

    yield
    styles.configure({})
    styles.set_theme(styles.DEFAULT_THEME)
    styles.refresh(qapp)


@pytest.fixture
def settings(qapp, mixer, restore_look):
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    yield window, dialog
    for key in UI_KEYS:
        window._cfg["ui"].pop(key, None)
    window.apply_appearance()
    dialog.done(0)


def press(widget, x, y, kind=QEvent.Type.MouseButtonPress, button=LEFT, buttons=LEFT):
    event = QMouseEvent(kind, QPointF(x, y), widget.mapToGlobal(QPointF(x, y)), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def test_interface_definitions():
    from ui import interfaces

    assert list(interfaces.INTERFACES)[:4] == ["reka", "cantabile", "retro", "minimal"]
    assert len(interfaces.INTERFACES) >= 15
    assert interfaces.interface("inconnu").key == interfaces.DEFAULT_INTERFACE
    assert interfaces.choice("aeree", interfaces.DENSITIES, "normale") == "aeree"
    assert interfaces.choice(42, interfaces.DENSITIES, "normale") == "normale"
    ui_cfg = {}
    assert interfaces.apply_interface(ui_cfg, "cantabile")
    assert ui_cfg == {"interface": "cantabile", "orientation": "horizontal", "density": "compacte"}
    assert not interfaces.apply_interface(ui_cfg, "winamp") and ui_cfg["interface"] == "cantabile"
    for iface in interfaces.INTERFACES.values():
        assert iface.orientation in interfaces.ORIENTATIONS and iface.density in interfaces.DENSITIES
        if iface.has_palette:             # une palette de style couvre toutes les couleurs du thème
            from ui import styles
            assert set(styles._THEMED) <= set(styles.interface_palette(iface)), iface.key


def test_palette_follows_interface_unless_colors_are_kept(qapp, restore_look):
    from ui import interfaces, styles

    ui_cfg = {"theme": "orange", "colors": {"fader": "#123456"}}
    styles.configure(ui_cfg)
    styles.set_theme("orange")
    orange = styles.GREEN_NEON
    assert styles.element_color("fader") == "#123456" and styles.INTERFACE.key == "reka"

    ui_cfg["interface"] = "retro"
    assert styles.configure(ui_cfg)
    styles.set_theme("orange")
    assert styles.GREEN_NEON == interfaces.RETRO_PALETTE["GREEN_NEON"]
    assert styles.LCD_TEXT == interfaces.RETRO_PALETTE["LCD_TEXT"]
    assert styles.element_color("fader") is None              # couleurs du style seulement
    assert styles.current_theme() == "orange"                 # le thème reste choisi
    assert styles.FONT_FAMILY == "Tahoma"                     # police du style (police d'origine)
    assert styles.fader_shape() == "chrome" and styles.vu_shape() == "lcd"
    assert styles.rad(14) == 4 and styles.bevelled()

    ui_cfg["keep_colors"] = True                               # mode mixte
    assert styles.configure(ui_cfg)
    styles.set_theme("orange")
    assert styles.GREEN_NEON == orange and styles.element_color("fader") == "#123456"
    assert styles.fader_shape() == "chrome"                    # la forme reste celle du style

    ui_cfg["font_family"] = "Verdana"                          # police choisie : gardée
    styles.configure(ui_cfg)
    assert styles.FONT_FAMILY == "Verdana" and styles.USER_FONT == "Verdana"


def test_stylesheet_changes_with_interface(qapp, restore_look):
    from ui import styles

    styles.configure({})
    styles.set_theme("studio")
    reka = styles.build_stylesheet()
    assert "border-radius: 14px" in reka and "outset" not in reka
    styles.configure({"interface": "retro"})
    styles.set_theme("studio")
    retro = styles.build_stylesheet()
    assert "border-radius: 4px" in retro and "Consolas" in retro    # reliefs, afficheurs LCD
    styles.configure({"interface": "minimal"})
    styles.set_theme("studio")
    assert "border: 1px solid transparent" in styles.build_stylesheet()


def test_preview_does_not_change_the_running_look(qapp, restore_look):
    from ui import interface_preview, styles

    styles.configure({"theme": "violet"})
    styles.set_theme("violet")
    before = (styles.GREEN_NEON, styles.INTERFACE.key, styles.FONT_FAMILY, styles.BG_WINDOW)
    for key in ("cantabile", "retro", "minimal"):
        with styles.previewing({"theme": "violet"}, key):
            assert styles.INTERFACE.key == key and styles.GREEN_NEON != before[0]
        pixmap = interface_preview.render({"theme": "violet"}, key)
        assert not pixmap.isNull() and pixmap.width() > 200
    assert (styles.GREEN_NEON, styles.INTERFACE.key, styles.FONT_FAMILY, styles.BG_WINDOW) == before
    wide = interface_preview.render({}, "cantabile")           # en lignes : plus large que haut
    tall = interface_preview.render({}, "retro")
    assert wide.width() > wide.height() and tall.height() > tall.width()


def test_choosing_an_interface_restyles_the_console(qapp, settings):
    from ui import styles

    window, dialog = settings
    vertical_width = window.width()
    assert [key for key, card in dialog.interface_cards.items() if card.isChecked()] == ["reka"]
    assert not dialog.keep_colors.isEnabled()                  # REKA suit toujours le thème

    dialog.set_interface("cantabile")
    pump(qapp, 0.1)
    ui_cfg = window._cfg["ui"]
    assert (ui_cfg["interface"], ui_cfg["orientation"], ui_cfg["density"]) == (
        "cantabile", "horizontal", "compacte")
    assert dialog.interface_cards["cantabile"].isChecked() and dialog.keep_colors.isEnabled()
    assert dialog.orientation_choice.currentData() == "horizontal"
    assert styles.INTERFACE.key == "cantabile" and styles.GREEN_NEON == "#3d9be9"
    strip = window.strips[0]
    assert strip.orientation() == "horizontal"
    assert strip.fader.orientation() == Qt.Orientation.Horizontal
    assert strip.vu.orientation() == Qt.Orientation.Horizontal
    assert strip.width() > strip.height()                      # une ligne par canal
    assert window.strips[1].y() > strip.y()                    # empilées
    assert window.width() < vertical_width
    assert not dialog.fader_choice.isEnabled()                 # faders dessinés par le style
    window.grab()

    dialog.keep_colors.setChecked(True)                        # mode mixte : thème par-dessus
    assert ui_cfg["keep_colors"] and styles.GREEN_NEON == "#3b82f6"
    assert dialog.appearance_note.isHidden()

    dialog.orientation_choice.setCurrentIndex(dialog.orientation_choice.findData("vertical"))
    pump(qapp, 0.1)
    assert strip.orientation() == "vertical" and strip.height() > strip.width()
    assert window.strips[1].x() > strip.x()

    dialog.set_interface("reka")
    pump(qapp, 0.1)
    assert styles.INTERFACE.key == "reka" and styles.rad(14) == 14
    assert dialog.fader_choice.isEnabled() and strip.fx_title.isVisibleTo(strip)


def test_density_changes_spacing_and_widths(qapp, settings):
    from ui import strip_widget

    window, dialog = settings
    strip = window.strips[0]
    widths = {}
    for density in ("compacte", "normale", "aeree"):
        dialog.density_choice.setCurrentIndex(dialog.density_choice.findData(density))
        pump(qapp, 0.05)
        widths[density] = (strip.width(), strip.layout().spacing(), window.master.width())
    assert widths["compacte"][0] == strip_widget.STRIP_WIDTHS["compacte"]
    assert widths["compacte"] < widths["normale"] < widths["aeree"]


def test_horizontal_fader_follows_the_mouse(qapp, restore_look):
    from ui.fader import FADER_MAX_DB, FADER_MIN_DB, Fader

    fader = Fader(orientation=Qt.Orientation.Horizontal)
    fader.resize(300, fader.height())
    fader.show()
    values = []
    fader.valueChanged.connect(values.append)
    y = fader.height() / 2
    press(fader, 296, y)                                       # tout à droite : +12 dB
    press(fader, 296, y, QEvent.Type.MouseButtonRelease, LEFT, NONE)
    assert values[-1] == pytest.approx(FADER_MAX_DB, abs=0.5)
    press(fader, 4, y)                                         # tout à gauche : -60 dB
    press(fader, 4, y, QEvent.Type.MouseButtonRelease, LEFT, NONE)
    assert values[-1] == pytest.approx(FADER_MIN_DB, abs=0.5)
    fader.setOrientation(Qt.Orientation.Vertical)
    assert fader.width() < fader.minimumHeight() + 60
    fader.grab()
    fader.close()


def test_retro_equalizer_and_animations(qapp, settings):
    from ui import styles

    window, dialog = settings
    master = window.master
    dialog.set_interface("retro")
    pump(qapp, 0.1)
    assert master.visualizer.isVisible() and master.visualizer.running()
    master.set_levels((-6.0, -8.0))
    for _ in range(6):
        master.visualizer.step()
    assert max(master.visualizer.bars()) > 0.2
    window.grab()

    dialog.animations_choice.setCurrentIndex(dialog.animations_choice.findData("reduced"))
    pump(qapp, 0.05)
    assert master.visualizer.running()                       # utile : gardé, plus lent
    assert master.badge._pulse.state() != master.badge._pulse.State.Running
    window.toast.show_message("Bonjour")                     # sans fondu
    assert window.toast._effect.opacity() == 1.0

    dialog.animations_choice.setCurrentIndex(dialog.animations_choice.findData("off"))
    pump(qapp, 0.05)
    assert not master.visualizer.isVisible() and not master.visualizer.running()
    assert not styles.animations("reduced")
    knob = master.knob
    knob.setValue(-20.0)                                     # sans glissement
    assert not knob._timer.isActive()

    dialog.animations_choice.setCurrentIndex(dialog.animations_choice.findData("on"))
    dialog.set_interface("minimal")
    pump(qapp, 0.05)
    assert not master.visualizer.isVisible()
    assert not window.strips[0].fx_title.isVisibleTo(window.strips[0])   # titres masqués


def test_previews_drawn_when_the_tab_is_shown(qapp, settings):
    window, dialog = settings
    assert not any(card.has_preview() for card in dialog.interface_cards.values())
    dialog.show_tab("Interface")
    assert all(card.has_preview() for card in dialog.interface_cards.values())
    first = dialog.interface_cards["retro"]._preview
    dialog.show_tab("Apparence")
    dialog.set_theme("violet")                              # le mode mixte montrerait le violet
    dialog.show_tab("Interface")
    assert dialog.interface_cards["retro"]._preview is not first
    dialog.grab()


def test_interface_survives_restart(qapp, cfg, restore_look):
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from ui import styles
    from ui.main_window import MainWindow

    cfg["ui"].update({"interface": "minimal", "orientation": "horizontal", "density": "aeree"})
    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False)
    window.show()
    pump(qapp, 0.1)
    try:
        assert styles.INTERFACE.key == "minimal" and styles.DENSITY == "aeree"
        assert all(strip.orientation() == "horizontal" for strip in window.strips)
        assert window.width() >= window.minimumWidth()
    finally:
        window.close()


def test_every_interface_draws_the_console(qapp, settings):
    from ui import decor, interfaces, strip_widget, styles
    from ui.master_widget import BIG_HEADPHONE_KNOB, HEADPHONE_KNOB

    window, dialog = settings
    strip, master = window.strips[0], window.master
    for key, iface in interfaces.INTERFACES.items():
        dialog.set_interface(key)
        pump(qapp, 0.02)
        assert styles.INTERFACE.key == key
        window.grab()                                   # chaque dessin passe sans erreur
        assert master.needle.isVisible() == iface.needle, key
        big = strip.knobs[0].diameter() == strip_widget.BIG_ROUTING_KNOB
        assert big == iface.big and (master.knob.diameter() == BIG_HEADPHONE_KNOB) == iface.big
        assert (strip.mute.height() == strip_widget.BIG_MUTE_HEIGHT) == iface.big, key
        margins = strip.layout().contentsMargins()
        if iface.decor == "screws" and strip.orientation() == "vertical":
            assert margins.top() >= decor.SCREW_MARGIN, key   # les vis logent dans la marge
        if iface.has_palette:
            assert styles.GREEN_NEON == styles.interface_palette(iface)["GREEN_NEON"], key
    dialog.set_interface("reka")
    assert master.knob.diameter() == HEADPHONE_KNOB and not master.needle.isVisible()


def test_needle_meter_follows_the_level(qapp, restore_look):
    from ui.master_widget import NeedleMeter

    meter = NeedleMeter()
    meter.resize(120, meter.height())
    assert meter.values() == [-20.0, -20.0]
    for _ in range(30):
        meter.set_levels((-18.0, -60.0))                # 0 VU à gauche, silence à droite
    left, right = meter.values()
    assert left == pytest.approx(0.0, abs=0.1) and right == -20.0
    assert NeedleMeter._ratio(-20) == pytest.approx(0.0) and NeedleMeter._ratio(3) == pytest.approx(1.0)
    meter.grab()


def test_glass_panels_and_pixel_drawing(qapp, restore_look):
    from PySide6.QtGui import QPainter, QPixmap

    from ui import styles

    styles.configure({"interface": "glass"})
    styles.set_theme("studio")
    assert "rgba(" in styles.build_stylesheet(1.0)          # panneaux translucides
    pixmap = QPixmap(10, 10)
    p = QPainter(pixmap)
    styles.smooth(p)
    assert p.testRenderHint(QPainter.RenderHint.Antialiasing)
    styles.configure({"interface": "pixel"})
    styles.smooth(p)
    assert not p.testRenderHint(QPainter.RenderHint.Antialiasing)   # gros pixels nets
    p.end()
