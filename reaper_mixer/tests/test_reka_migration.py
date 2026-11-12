"""
Passage de REAPER au moteur intégré, en gardant ses effets, et plugins joints
aux sauvegardes .reka.
"""

import base64
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from conftest import pump

LUA = shutil.which("lua5.4") or shutil.which("lua54")
HERE = Path(__file__).parent


# ------------------------------------------------------------------ export du pont
@pytest.mark.skipif(LUA is None, reason="Lua 5.4 absent")
def test_bridge_exports_effects_with_settings(qapp, cfg, tmp_path):
    from core.reaper_ctrl import ReaperController

    cfg["reaper"]["resource_path"] = str(tmp_path / "reaper_res")
    ctrl = ReaperController(cfg)
    exports = []
    ctrl.fx_export_ready.connect(exports.append)
    env = ctrl.prepare_launch(Path("reaper.exe"))
    startup = tmp_path / "reaper_res" / "Scripts" / "__startup.lua"
    stop = tmp_path / "stop"
    proc = subprocess.Popen([LUA, str(HERE / "fake_reaper.lua"), str(startup), str(stop)],
                            env={**os.environ, **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_for(condition, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            pump(qapp, 0.05)
            if condition():
                return True
        return False

    try:
        ctrl.start()
        assert ctrl.ping(6.0)
        ctrl.add_fx(0, "VST: ReaLimit (Cockos)")
        ctrl.add_fx(0, "VST3: Pro-Q 4 (FabFilter)")
        ctrl.add_fx(0, "JS: Volume Adjustment")
        ctrl.set_fx_enabled(0, 1, False)
        assert wait_for(lambda: len(ctrl.strip_tracks[0].fx) == 3
                        and not ctrl.strip_tracks[0].fx[1].enabled)
        ctrl.request_fx_export()
        assert wait_for(lambda: exports)
    finally:
        stop.write_text("stop")
        ctrl.close(save_project=False)
        proc.wait(timeout=5)

    tracks = exports[0]
    assert [t.channel for t in tracks] == [0, 2, 4]          # pistes qui écoutent une entrée
    limit, eq, js = tracks[0].fx
    assert (limit.type, limit.ident, limit.chunk) == ("VST", "realimit.dll<1919708532", "AAEC")
    assert limit.params == (("Threshold", 0.25),) and limit.enabled
    assert eq.type == "VST3" and eq.ident.endswith("FabFilter Pro-Q 4.vst3") and not eq.enabled
    # « = » et « ; » dans un nom de paramètre ne cassent pas l'export
    assert eq.params == (("Band 1 Gain", 0.75), ("Output_ Level_dB", 0.5))
    assert js.type == "JS"


# ------------------------------------------------------------------ conversion
def _export():
    from core.reaper_ctrl import ExportedFx, ExportedTrack

    return [
        ExportedTrack(0, True, (
            ExportedFx("VST", "realimit.dll<1919708532", "VST: ReaLimit (Cockos)", True, "AAEC",
                       (("Threshold", 0.25),)),
            ExportedFx("VST3", "C:\\Autre PC\\VST3\\FabFilter Pro-Q 4.vst3\\Contents\\x86_64-win\\"
                       "FabFilter Pro-Q 4.vst3", "VST3: Pro-Q 4 (FabFilter)", False, "",
                       (("Band 1 Gain", 0.75),)),
            ExportedFx("JS", "utility/volume", "JS: Volume Adjustment", True, "", ()),
            ExportedFx("CLAP", "x.clap", "CLAP: Truc", True, "", ()))),
        ExportedTrack(10, False, (
            ExportedFx("VST3", "D:\\Absent.vst3", "VST3: Absent", True, "", ()),)),
    ]


def test_build_chains_from_reaper(tmp_path):
    from core.reka_host import PluginRef, clean_chains
    from core.reka_migration import build_chains
    from core.vst_scanner import PluginInfo

    reaper_fx = tmp_path / "REAPER" / "Plugins" / "FX"
    reaper_fx.mkdir(parents=True)
    (reaper_fx / "realimit.dll").write_bytes(b"x")
    bundle = tmp_path / "VST3" / "FabFilter Pro-Q 4.vst3"
    bundle.mkdir(parents=True)
    plugins = [PluginInfo("FabFilter Pro-Q 4", PluginRef("VST3", str(bundle)).add_name, "VST3", "")]

    migration = build_chains(_export(), [0, 2, 10], plugins, [reaper_fx])
    strips = migration.chains["strips"]
    assert len(strips) == 3 and strips[1] == {"enabled": True, "fx": []}
    limit, eq = strips[0]["fx"]
    assert limit == {"format": "VST2", "path": str(reaper_fx / "realimit.dll"), "uid": "1919708532",
                     "name": "ReaLimit", "enabled": True, "state": "", "chunk": "AAEC",
                     "params": [["Threshold", 0.25]]}
    # VST3 d'un autre chemin : retrouvé par nom de fichier, sans le « Contents\… »
    assert eq["path"] == str(bundle) and eq["format"] == "VST3" and eq["uid"] == ""
    assert eq["name"] == "Pro-Q 4" and eq["enabled"] is False and eq["chunk"] == ""
    assert strips[2]["enabled"] is False and strips[2]["fx"][0]["path"] == "D:\\Absent.vst3"
    assert migration.kept == ["ReaLimit (Cockos)", "Pro-Q 4 (FabFilter)"]
    assert migration.missing == ["Absent"]
    assert migration.skipped == ["Volume Adjustment (JS)", "Truc (CLAP)"]
    assert "3 effets repris" in migration.summary()

    # Les réglages repris passent la vérification des chaînes, puis disparaissent
    # dès que l'hôte a enregistré ses propres réglages
    checked = clean_chains(migration.chains)
    assert checked["strips"][0]["fx"][0]["chunk"] == "AAEC"
    assert checked["strips"][0]["fx"][1]["params"] == [["Band 1 Gain", 0.75]]
    saved = clean_chains({"strips": [{"fx": [dict(limit, state="AAAA")]}]})
    assert "chunk" not in saved["strips"][0]["fx"][0] and "params" not in saved["strips"][0]["fx"][0]
    bad = clean_chains({"strips": [{"fx": [dict(limit, chunk="%%", params=[["a", "x"], ["b", 7]])]}]})
    assert "chunk" not in bad["strips"][0]["fx"][0]
    assert bad["strips"][0]["fx"][0]["params"] == [["b", 1.0]]


def test_console_switches_to_reka_engine(qapp, cfg, appdata, tmp_path, monkeypatch):
    from PySide6.QtCore import QObject, Signal

    from core import reka_host
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui.main_window import MainWindow

    class Reaper(DemoReaper):
        fx_export_ready = Signal(object)

        def request_fx_export(self):
            self.fx_export_ready.emit(_export())

    monkeypatch.setattr(reka_host, "scan_plugins", lambda extra=None: [])
    reaper = Reaper(cfg)
    window = MainWindow(cfg=cfg, vm=DemoVoicemeeter(cfg), reaper=reaper, processes=DemoProcesses(),
                        persist=True, apps=DemoAppVolumes(), media=DemoMedia())
    try:
        results, asked = [], []
        window.migrate_to_reka(lambda *r: results.append(r), confirm=lambda t: asked.append(t) or False)
        assert results == [(True, "", False)] and "Continuer ?" in asked[0]
        assert cfg["effects"]["host"] == "reaper"           # refusé : rien ne change

        results.clear()
        window.migrate_to_reka(lambda *r: results.append(r), confirm=lambda t: True)
        ok, message, restart = results[0]
        assert ok and restart and "effets repris" in message
        assert cfg["effects"]["host"] == "reka"
        saved = json.loads(reka_host.chains_path().read_text())
        assert [fx["name"] for fx in saved["strips"][0]["fx"]] == ["ReaLimit", "Pro-Q 4"]
        assert json.loads((appdata / "REKA" / "config.json").read_text())["effects"]["host"] == "reka"
    finally:
        window.close()


# ------------------------------------------------------------------ plugins dans le .reka
def _chains(path: Path, *plugins: Path) -> Path:
    path.write_text(json.dumps({"format": 1, "strips": [{"enabled": True, "fx": [
        {"format": "VST3" if p.suffix == ".vst3" else "VST2", "path": str(p), "name": p.stem,
         "enabled": True, "state": base64.b64encode(p.stem.encode()).decode()}
        for p in plugins]}]}))
    return path


def test_backup_carries_plugin_files(qapp, cfg, appdata, tmp_path):
    from core import backup, config
    from core.reka_host import CHAINS_FILENAME

    pc1 = tmp_path / "pc1"
    dll = pc1 / "VST" / "Comp.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ comp" * 100)
    bundle = pc1 / "VST3" / "Eq.vst3"
    (bundle / "Contents" / "x86_64-win").mkdir(parents=True)
    (bundle / "Contents" / "x86_64-win" / "Eq.vst3").write_bytes(b"MZ eq")
    (bundle / "Contents" / "Resources").mkdir()
    (bundle / "Contents" / "Resources" / "moduleinfo.json").write_text("{}")
    chains = _chains(tmp_path / "chains.json", dll, bundle)

    cfg["effects"]["host"] = "reka"
    files = backup.plugin_files(chains)
    assert sorted(p.name for p in files) == ["Comp.dll", "Eq.vst3"]
    target = tmp_path / "tout.reka"
    saved = backup.export_backup(cfg, target, chains=chains, plugins=files)
    assert "2 plugins" in saved.describe()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    assert any(n.endswith("Eq.vst3/Contents/x86_64-win/Eq.vst3") for n in names)
    assert any(n.endswith("/Comp.dll") for n in names)

    # Autre PC : les plugins n'y sont pas → installés depuis la sauvegarde
    shutil.rmtree(pc1)
    data = backup.read_backup(target)
    assert len(data.plugins) == 2 and data.plugins_size > 0
    report = backup.import_backup(cfg, data)
    assert sorted(report.plugins) == ["Comp.dll", "Eq.vst3"]
    imported = json.loads((config.app_data_dir() / CHAINS_FILENAME).read_text())
    paths = [Path(fx["path"]) for fx in imported["strips"][0]["fx"]]
    assert all(str(p).startswith(str(config.app_data_dir() / "plugins")) for p in paths)
    assert paths[0].read_bytes() == b"MZ comp" * 100
    assert (paths[1] / "Contents" / "x86_64-win" / "Eq.vst3").read_bytes() == b"MZ eq"
    # Réglages intacts
    assert imported["strips"][0]["fx"][0]["state"] == base64.b64encode(b"Comp").decode()


def test_installed_plugins_win_over_embedded_ones(cfg, appdata, tmp_path):
    from core import backup, config
    from core.reka_host import CHAINS_FILENAME

    dll = tmp_path / "VST" / "Comp.dll"
    dll.parent.mkdir()
    dll.write_bytes(b"MZ")
    chains = _chains(tmp_path / "chains.json", dll)
    target = tmp_path / "x.reka"
    backup.export_backup(cfg, target, chains=chains, plugins=[dll])
    report = backup.import_backup(cfg, backup.read_backup(target))
    assert report.plugins == []                                # déjà installé ici
    imported = json.loads((config.app_data_dir() / CHAINS_FILENAME).read_text())
    assert imported["strips"][0]["fx"][0]["path"] == str(dll)
    assert not (config.app_data_dir() / "plugins").exists()


@pytest.mark.parametrize("evil", ["../../evil.dll", "Comp.dll/../../evil.dll", "C:/evil.dll",
                                  "Comp.dll\\..\\evil.dll", "Comp.dll/con:x"])
def test_hostile_plugin_paths_are_never_extracted(cfg, appdata, tmp_path, evil):
    from core import backup, config

    target = tmp_path / "evil.reka"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(backup.MANIFEST, json.dumps({
            "app": "REKA", "format": 1,
            "plugins": [{"key": "0", "path": "C:/VST/Comp.dll", "name": "Comp.dll"}]}))
        archive.writestr(backup.CONFIG, "{}")
        archive.writestr(backup.CHAINS, json.dumps({"strips": [{"fx": [
            {"format": "VST2", "path": "C:/VST/Comp.dll"}]}]}))
        archive.writestr(f"plugins/0/{evil}", b"MZ evil")
    data = backup.read_backup(target)
    assert data.plugins == ()                                  # plugin ignoré en entier
    backup.import_backup(cfg, data)
    assert not list(tmp_path.rglob("evil.dll"))
    assert not (config.app_data_dir() / "plugins").exists()
