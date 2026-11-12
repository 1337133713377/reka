"""
Version 0.7 : charge CPU / GPU du moteur d'effets, mesure d'un plugin,
glisser-déposer des effets entre strips, thèmes prédéfinis.
"""

import os
import struct
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QDropEvent

from conftest import pump


def names(reaper, strip):
    return [fx.display_name for fx in reaper._tracks[strip].fx]


def drop(source_pill, target_list, y, modifiers=Qt.KeyboardModifier.NoModifier):
    """Dépose la pill `source_pill` dans `target_list`, à la hauteur `y`."""
    mime = source_pill.mime_data()
    event = QDropEvent(QPointF(target_list.width() / 2, y),
                       Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, modifiers)
    target_list.dropEvent(event)
    return event


# ------------------------------------------------------------ charge du moteur
def test_gpu_usage_by_pid():
    from core.engine_load import gpu_usage_by_pid, level

    usage = gpu_usage_by_pid([
        ("pid_100_luid_0x0_0x1_phys_0_eng_0_engtype_3D", 12.0),
        ("pid_100_luid_0x0_0x1_phys_0_eng_1_engtype_3D", 5.0),          # même type : additionné
        ("pid_100_luid_0x0_0x1_phys_0_eng_2_engtype_VideoDecode", 30.0),  # le plus chargé l'emporte
        ("pid_200_luid_0x0_0x1_phys_0_eng_0_engtype_3D", 250.0),         # borné à 100
        ("_Total", 99.0), ("pid_x_engtype_3D", 5.0), ("pid_300_engtype_Copy", -1.0),
    ])
    assert usage == {100: 30.0, 200: 100.0, 300: 0.0}
    assert [level(v, 40, 70) for v in (None, 10, 40, 69.9, 70)] == [0, 0, 1, 1, 2]


def test_process_sampler_on_this_process():
    import psutil

    from core.engine_load import ProcessSampler

    me = psutil.Process(os.getpid())
    sampler = ProcessSampler(lambda: [me])
    assert sampler.sample() is None              # premier relevé : référence seulement
    before = sampler.cpu_seconds()
    end = time.monotonic() + 0.25
    while time.monotonic() < end:                # un peu de calcul à mesurer
        sum(i * i for i in range(2000))
    load = sampler.sample()
    assert load is not None and load.gpu is None and load.processes >= 1
    assert load.cpu_core > 0 and 0 < load.cpu <= 100 and load.memory_mb > 5
    assert sampler.cpu_seconds() > before
    assert "CPU" in load.describe("REAPER") and "GPU" not in load.describe("REAPER")
    assert ProcessSampler(lambda: []).sample() is None and ProcessSampler(lambda: []).cpu_seconds() is None


def test_engine_roots_per_host():
    import psutil

    from core.engine_load import create_monitor, engine_roots

    me = psutil.Process(os.getpid())

    class Processes:
        def process(self, key):
            return me if key == "reaper" else None

    class Host:
        def __init__(self, kind, process_key=None):
            self.kind, self.process_key = kind, process_key

    assert engine_roots(Processes(), Host("reaper", "reaper")) == [me]
    assert engine_roots(Processes(), Host("flstudio", "flstudio")) == []      # ne tourne pas
    assert engine_roots(Processes(), Host("none")) == []
    assert create_monitor(Processes(), Host("none")) is None                   # Voicemeeter seul
    assert create_monitor(Processes(), Host("reaper", "reaper")).can_measure


def test_load_meter_in_master(qapp, cfg):
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoLoad, DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.engine_load import EngineLoad, EngineLoadMonitor
    from core.media import DemoMedia
    from ui.main_window import MainWindow
    from ui.settings_dialog import SettingsDialog

    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    monitor = EngineLoadMonitor(DemoLoad(reaper), interval_s=0.05)
    window = MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(), persist=False,
                        apps=DemoAppVolumes(), media=DemoMedia(), load=monitor)
    window.show()
    try:
        pump(qapp, 0.3)
        meter = window.master.load
        assert not meter.isHidden() and [row[0] for row in meter.rows()] == ["CPU", "GPU"]
        assert "REAPER" in meter.toolTip() and "Mémoire" in meter.toolTip()
        height = meter.height()
        # Sans GPU : une seule barre ; charge forte : niveau rouge
        window.master.set_load(EngineLoad(cpu=82.0, cpu_core=656.0, gpu=None, memory_mb=900), "REAPER")
        assert meter.rows() == [("CPU", 82.0, 2)] and meter.height() < height
        meter.grab()                                  # se dessine sans erreur

        # Option « Afficher la charge » (réglages → Apparence)
        dialog = SettingsDialog(cfg, None, reaper, vm, window, persist=False)
        dialog.appearanceChanged.connect(window.apply_appearance)
        dialog.show_load.setChecked(False)
        pump(qapp, 0.15)
        assert cfg["ui"]["show_load"] is False and meter.isHidden()
        dialog.show_load.setChecked(True)
        pump(qapp, 0.3)
        assert not meter.isHidden()
        dialog.done(0)
    finally:
        window.close()
    assert monitor._thread is None                    # arrêté à la fermeture


def test_no_load_meter_without_monitor(qapp, mixer):
    window, _, _ = mixer
    assert window.master.load.isHidden() and window.master.load.rows() == []
    pill = window.strips[0].fx_list._pills[0]
    assert pill.draggable and not pill.measurable     # démo sans mesure : pas d'entrée de menu


def test_measure_plugin_cost(qapp, cfg, monkeypatch):
    """Clic droit → « Mesurer sa charge CPU » : mesure avec puis sans le plugin."""
    import ui.main_window as main_window
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoLoad, DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.engine_load import EngineLoadMonitor
    from core.media import DemoMedia

    monkeypatch.setattr(main_window, "MEASURE_WINDOW_MS", 200)
    monkeypatch.setattr(main_window, "MEASURE_SETTLE_MS", 20)
    vm, reaper = DemoVoicemeeter(cfg), DemoReaper(cfg)
    sampler = DemoLoad(reaper)
    window = main_window.MainWindow(cfg=cfg, vm=vm, reaper=reaper, processes=DemoProcesses(),
                                    persist=False, apps=DemoAppVolumes(), media=DemoMedia(),
                                    load=EngineLoadMonitor(sampler, interval_s=0.05))
    window.show()
    try:
        pump(qapp, 0.1)
        mic = window.strips[0]
        assert mic.fx_list._pills[1].measurable
        window.measure_effect(0, 1)                   # chaîne du micro en bypass : refus
        assert "Active" in window.toast.text() and not window._measuring
        reaper.set_chain_enabled(0, True)
        pump(qapp, 0.05)

        mic.fx_list._pills[1].measureRequested.emit()
        assert window._measuring
        window.measure_effect(0, 0)                   # une seule mesure à la fois
        assert "déjà en cours" in window.toast.text()
        pump(qapp, 0.3)
        assert not reaper._tracks[0].fx[1].enabled    # désactivé pendant le second relevé
        pump(qapp, 0.4)
        assert not window._measuring and reaper._tracks[0].fx[1].enabled   # réactivé
        expected = sampler.plugin_cost("VST3: Pro-Q 4 (FabFilter)")
        assert window._fx_costs["Pro-Q 4 (FabFilter)"] == pytest.approx(expected, abs=0.6)
        assert "Pro-Q 4" in window.toast.text() and "d'un cœur" in window.toast.text()
        assert "Charge mesurée" in mic.fx_list._pills[1].toolTip()
        assert "Charge mesurée" not in mic.fx_list._pills[0].toolTip()

        # REKA fermée pendant une mesure : le plugin n'est pas laissé désactivé
        window.measure_effect(0, 2)
        pump(qapp, 0.3)
        assert not reaper._tracks[0].fx[2].enabled
    finally:
        window.close()
    assert reaper._tracks[0].fx[2].enabled


# ------------------------------------------------------------ glisser-déposer
def test_drag_plugin_to_another_strip(qapp, mixer):
    window, _, reaper = mixer
    mic, music = window.strips[0], window.strips[1]
    before = names(reaper, 0)
    assert len(before) == 6 and names(reaper, 1) == []

    # Survol : trait d'insertion affiché, déposable
    pill = mic.fx_list._pills[1]
    assert pill.strip == 0 and pill.index == 1 and "Glisser" in pill.toolTip()
    event = drop(pill, music.fx_list, 4)
    pump(qapp, 0.05)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.MoveAction
    assert names(reaper, 1) == [before[1]] and names(reaper, 0) == before[:1] + before[2:]
    assert len(mic.fx_list._pills) == 5 and len(music.fx_list._pills) == 1
    assert "déplacé sur" in window.toast.text()

    # Ctrl maintenu : copie, insérée avant le plugin déjà là
    event = drop(mic.fx_list._pills[0], music.fx_list, 2, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.05)
    assert event.dropAction() == Qt.DropAction.CopyAction
    assert names(reaper, 1) == [before[0], before[1]] and len(names(reaper, 0)) == 5
    # … et après lui si on vise le bas de la liste
    drop(mic.fx_list._pills[4], music.fx_list, 150)
    pump(qapp, 0.05)
    assert names(reaper, 1) == [before[0], before[1], before[5]]


def test_drag_plugin_reorders_chain(qapp, mixer):
    from ui.strip_widget import SLOT_PITCH

    window, _, reaper = mixer
    fx_list = window.strips[0].fx_list
    a, b, c, d, e, f = names(reaper, 0)
    assert fx_list.gap_at(0) == 0 and fx_list.gap_at(SLOT_PITCH * 0.6) == 1 and fx_list.gap_at(9999) == 6

    drop(fx_list._pills[0], fx_list, SLOT_PITCH * 3)          # 1er → entre le 3e et le 4e
    pump(qapp, 0.05)
    assert names(reaper, 0) == [b, c, a, d, e, f]
    drop(fx_list._pills[5], fx_list, 0)                       # dernier → tout en haut
    pump(qapp, 0.05)
    assert names(reaper, 0) == [f, b, c, a, d, e]
    for y in (SLOT_PITCH * 1, SLOT_PITCH * 2):                # déposé sur sa propre place : rien
        drop(fx_list._pills[1], fx_list, y)
        pump(qapp, 0.05)
        assert names(reaper, 0) == [f, b, c, a, d, e]
    assert not fx_list._drop_marker.isVisible()


def test_drag_routed_through_window(qapp, mixer):
    """Comme la plateforme : les événements de glisser arrivent à la fenêtre, Qt
    les route jusqu'à la liste d'effets, même au-dessus d'une pill."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QDragMoveEvent

    from ui.strip_widget import SLOT_PITCH

    window, _, reaper = mixer
    fx_list = window.strips[0].fx_list
    a, b, c, d, e, f = names(reaper, 0)
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    mime = fx_list._pills[0].mime_data()
    # Moitié basse de la 3e pill : insertion juste après elle
    pos = fx_list.mapTo(window, QPoint(fx_list.width() // 2, 2 * SLOT_PITCH + 12))
    move = QDragMoveEvent(pos, actions, mime, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(window.windowHandle(), move)
    assert move.isAccepted() and fx_list._drop_marker.isVisible() and fx_list._drop_gap == 3
    event = QDropEvent(QPointF(pos), actions, mime, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    qapp.sendEvent(window.windowHandle(), event)
    pump(qapp, 0.05)
    assert event.isAccepted() and names(reaper, 0) == [b, c, a, d, e, f]
    assert not fx_list._drop_marker.isVisible()


def test_drop_refused_when_host_cannot_move(qapp, mixer):
    from PySide6.QtCore import QMimeData

    window, _, reaper = mixer
    mic, music = window.strips[0], window.strips[1]
    before = names(reaper, 0)
    other = QMimeData()
    other.setText("pas un plugin")
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.MoveAction, other, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    music.fx_list.dropEvent(event)
    assert not event.isAccepted()
    for strip in window.strips:                               # moteur sans déplacement (FL Studio…)
        strip.set_fx_abilities(False, False)
    assert not mic.fx_list._pills[0].draggable
    event = drop(mic.fx_list._pills[0], music.fx_list, 4)
    pump(qapp, 0.05)
    assert not event.isAccepted() and names(reaper, 0) == before and names(reaper, 1) == []


def _dll(path: Path) -> Path:
    data = bytearray(256)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", 0x80)
    data[0x80:0x84] = b"PE\0\0"
    data[0x84:0x86] = struct.pack("<H", 0x8664)
    path.write_bytes(bytes(data))
    return path


def test_apo_move_keeps_plugin_settings(qapp, cfg, tmp_path):
    """Equalizer APO : un plugin déplacé garde ses réglages (ChunkData)."""
    from core.apo_ctrl import ApoController, Endpoint

    config_dir = tmp_path / "EqualizerAPO" / "config"
    config_dir.mkdir(parents=True)
    cfg["effects"]["host"] = "apo"
    cfg["apo"]["config_dir"] = str(config_dir)
    endpoints = [Endpoint("Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
                          "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}", False),
                 Endpoint("Voicemeeter AUX Input (VB-Audio Voicemeeter AUX VAIO)",
                          "{bbbbbbbb-cccc-dddd-eeee-ffffffffffff}", False)]
    host = ApoController(cfg, endpoints=endpoints)
    errors = []
    host.command_failed.connect(errors.append)
    host.start()
    assert host.can_move_fx
    eq, comp = _dll(tmp_path / "ReaEQ.dll"), _dll(tmp_path / "Comp.dll")
    for plugin in (eq, comp, eq):                 # Appels : EQ, Comp, EQ (deux fois le même)
        host.add_fx(2, str(plugin))
    path = config_dir / "reka.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [i for i, line in enumerate(lines) if line.startswith("VSTPlugin")]
    for row, chunk in zip(rows, ("EQ1", "COMP", "EQ2")):
        lines[row] += f' ChunkData "{chunk}"'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def chunks(device_guid):
        text, found, inside = path.read_text(encoding="utf-8"), [], False
        for line in text.splitlines():
            if line.startswith("Device:"):
                inside = device_guid in line
            elif inside and "VSTPlugin" in line:
                found.append(line.rsplit(" ", 1)[-1].strip('"') if "ChunkData" in line else "")
        return found

    assert chunks("aaaaaaaa") == ["EQ1", "COMP", "EQ2"]
    host.move_fx(2, 2, 2, 0)                      # dernier EQ en tête : chacun garde ses réglages
    assert chunks("aaaaaaaa") == ["EQ2", "EQ1", "COMP"]
    host.move_fx(2, 2, 3, 0)                      # Comp → Bureau (autre périphérique)
    assert chunks("aaaaaaaa") == ["EQ2", "EQ1"] and chunks("bbbbbbbb") == ["COMP"]
    assert [f.display_name for f in host.strip_tracks[3].fx] == ["Comp"]
    host.move_fx(2, 0, 3, 1, copy=True)           # copie de l'EQ : mêmes réglages des deux côtés
    assert chunks("aaaaaaaa") == ["EQ2", "EQ1"] and chunks("bbbbbbbb") == ["COMP", "EQ2"]
    assert cfg["apo"]["strips"][3]["plugins"][1]["path"] == str(eq)

    host.move_fx(2, 0, 0, 0)                      # micro sans périphérique : refus expliqué
    assert errors and "périphérique" in errors[-1] and chunks("aaaaaaaa") == ["EQ2", "EQ1"]
    host.move_fx(2, 9, 3, 0)                      # index invalide : ignoré
    assert chunks("bbbbbbbb") == ["COMP", "EQ2"]


# ------------------------------------------------------------ thèmes prédéfinis
def test_presets_are_valid():
    from PySide6.QtGui import QColor

    from ui import styles

    assert len(styles.PRESETS) >= 10 and styles.DEFAULT_THEME in styles.PRESETS
    elements = set(styles.STRIP_ELEMENTS) | set(styles.MASTER_ELEMENTS)
    for key, preset in styles.PRESETS.items():
        assert preset.key == key and preset.name and preset.description
        assert styles.theme_accent(preset.theme) is not None, key
        assert preset.fader_style in styles.FADER_STYLES and preset.vu_style in styles.VU_STYLES
        for name, value in preset.colors.items():
            assert name.split("@")[0] in elements and QColor(value).isValid(), (key, name)
        swatches = preset.swatches()
        assert 1 <= len(swatches) <= 5 and all(QColor(c).isValid() for c in swatches)
        # Accent lisible sur le fond des colonnes
        palette = styles.theme_palette(preset.theme)[1]
        assert styles.contrast(QColor(palette["GREEN_NEON"]), QColor(palette["BG_COLUMN"])) >= 3, key
    ui_cfg = {"colors": {"mute": "#123456"}}
    assert styles.current_preset(ui_cfg) is None and not styles.apply_preset(ui_cfg, "inconnu")
    assert styles.apply_preset(ui_cfg, "synthwave") and styles.current_preset(ui_cfg) == "synthwave"
    assert ui_cfg["theme"] == "#b266ff" and ui_cfg["fader_style"] == "rond" and "mute" not in ui_cfg["colors"]
    ui_cfg["colors"]["mute"] = "#123456"
    assert styles.current_preset(ui_cfg) is None
    assert styles.current_preset({}) == styles.DEFAULT_THEME       # config neuve : thème par défaut
    assert styles.theme_palette("n'importe quoi")[0] == styles.DEFAULT_THEME


def test_preset_cards_in_settings(qapp, mixer):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui import styles
    from ui.settings_dialog import SettingsDialog

    window, vm, reaper = mixer
    dialog = SettingsDialog(window._cfg, None, reaper, vm, window, persist=False)
    dialog.appearanceChanged.connect(window.apply_appearance)
    ui_cfg = window._cfg["ui"]
    try:
        assert set(dialog.preset_cards) == set(styles.PRESETS)
        assert dialog.preset_cards["studio"].isChecked() and "Dark Studio Pro" in dialog.preset_label.text()

        dialog.preset_cards["braise"].click()
        assert ui_cfg["theme"] == "orange" and styles.current_theme() == "orange"
        assert ui_cfg["fader_style"] == "vintage" and styles.FADER_STYLE == "vintage"
        assert styles.element_color("fader") == "#ff6a3d" and styles.VU_STYLE == "led"
        assert dialog.preset_cards["braise"].isChecked() and not dialog.preset_cards["studio"].isChecked()
        assert dialog.fader_choice.currentData() == "vintage" and dialog.vu_choice.currentData() == "led"
        assert dialog.theme_swatches["orange"].isChecked()
        dialog.preset_cards["braise"].grab()

        # Thème « une couleur par canal »
        dialog.preset_cards["arcade"].click()
        assert styles.element_color("fader", 0) != styles.element_color("fader", 1)
        assert styles.element_color("fader") is None and styles.current_theme() == "studio"

        # Une retouche à la main : plus aucun thème prédéfini coché
        dialog.set_element_color("mute", None, "#00aa55")
        assert not any(card.isChecked() for card in dialog.preset_cards.values())
        assert "personnalisés" in dialog.preset_label.text()

        # Couleurs personnalisées : confirmation avant de les remplacer
        def answer(button):
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox) and "remplace" in box.text()
            box.button(button).click()

        QTimer.singleShot(100, lambda: answer(QMessageBox.StandardButton.No))
        dialog.preset_cards["neon"].click()
        assert ui_cfg["colors"].get("mute") == "#00aa55" and not dialog.preset_cards["neon"].isChecked()
        QTimer.singleShot(100, lambda: answer(QMessageBox.StandardButton.Yes))
        dialog.preset_cards["neon"].click()
        assert ui_cfg["colors"] == {} and ui_cfg["theme"] == "neon" and dialog.preset_cards["neon"].isChecked()
    finally:
        dialog.apply_preset("studio", confirm=False)      # palette d'origine pour les tests suivants
        dialog.done(0)
    assert styles.current_theme() == "studio" and styles.FADER_STYLE == styles.DEFAULT_FADER_STYLE
