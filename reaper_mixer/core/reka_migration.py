"""
Passage de REAPER au moteur intégré de REKA, en gardant ses effets.

Le pont Lua exporte, pour chaque piste qui écoute une entrée de Voicemeeter,
ses effets : fichier du plugin, nom, état actif, « chunk » de réglages (VST2)
et valeur de chaque paramètre (voir write_fxexport dans reka_bridge.lua).
On en fait des chaînes pour reka-host.exe (chains.json) : au premier
chargement, l'hôte rend au plugin son chunk, sinon ses paramètres, puis
enregistre ses réglages dans son propre format.

Ce qui ne peut pas suivre (effets JS de REAPER, CLAP, plugins introuvables)
est signalé ; un plugin introuvable reste dans sa chaîne avec ses réglages.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.reaper_ctrl import ExportedTrack, display_fx_name
from core.reka_host import CHAINS_FORMAT, FORMATS, PluginRef, parse_add_name
from core.vst_scanner import PluginInfo

log = logging.getLogger(__name__)

_FORMATS = {"VST": "VST2", "VSTI": "VST2", "VST2": "VST2", "VST3": "VST3", "VST3I": "VST3"}
_VENDOR = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass
class Migration:
    chains: dict
    kept: list[str] = field(default_factory=list)       # effets repris
    missing: list[str] = field(default_factory=list)    # repris, mais fichier introuvable ici
    skipped: list[str] = field(default_factory=list)    # impossibles à reprendre (JS, CLAP…)

    @property
    def count(self) -> int:
        return len(self.kept) + len(self.missing)

    def summary(self) -> str:
        lines = [f"{self.count} effet{'s' if self.count > 1 else ''} repris avec leurs réglages."]
        if self.missing:
            lines.append("Introuvables sur ce PC (gardés avec leurs réglages, à installer) : "
                         + ", ".join(self.missing) + ".")
        if self.skipped:
            lines.append("Non repris (propres à REAPER, ou format non géré) : "
                         + ", ".join(self.skipped) + ".")
        return "\n".join(lines)


def _plugin_path(ident: str, fmt: str, plugins: list[PluginInfo],
                 extra_dirs: list[Path]) -> str:
    """Chemin du plugin d'après l'identifiant de REAPER (chemin complet, ou
    simple nom de fichier pour les plugins de ses dossiers)."""
    path = ident.replace("/", "\\")
    if fmt == "VST3":
        cut = path.lower().find(".vst3")
        if cut >= 0:
            path = path[:cut + 5]      # …\Plugin.vst3\Contents\x86_64-win\Plugin.vst3 → le bundle
    if Path(path).is_absolute() or re.match(r"^[A-Za-z]:\\", path):
        if Path(path).exists() or not plugins:
            return path
    filename = Path(path.replace("\\", "/")).name.lower()
    for plugin in plugins:
        ref = parse_add_name(plugin.add_name)
        if ref is not None and ref.format == fmt and \
                Path(ref.path.replace("\\", "/")).name.lower() == filename:
            return ref.path
    for folder in extra_dirs:
        candidate = folder / Path(path.replace("\\", "/")).name
        if candidate.exists():
            return str(candidate)
    return path


def build_chains(export: list[ExportedTrack], channels: list[int], plugins: list[PluginInfo],
                 extra_dirs: list[Path] | None = None) -> Migration:
    """Chaînes du moteur REKA pour les strips (désignées par leur premier canal
    d'insert, comme dans REAPER)."""
    by_channel = {track.channel: track for track in export}
    migration = Migration({"format": CHAINS_FORMAT, "strips": []})
    for channel in channels:
        track = by_channel.get(channel)
        strip = {"enabled": track.enabled if track else True, "fx": []}
        for fx in track.fx if track else ():
            label = display_fx_name(fx.name)
            fmt = _FORMATS.get(fx.type.upper().strip())
            if fmt is None or not fx.ident:
                migration.skipped.append(f"{label} ({fx.type or '?'})")
                continue
            path_part, _, uid = fx.ident.partition("<")
            path = _plugin_path(path_part, fmt, plugins, extra_dirs or [])
            entry = {
                "format": fmt,
                "path": path,
                # VST2 : identifiant (utile aux plugins « shell ») ; VST3 : la classe
                # est retrouvée par son nom
                "uid": uid if fmt == "VST2" and uid.isdigit() else "",
                "name": _VENDOR.sub("", label) or Path(path.replace("\\", "/")).stem,
                "enabled": fx.enabled,
                "state": "",
                "chunk": fx.chunk if fmt == "VST2" else "",
                "params": [[name, value] for name, value in fx.params],
            }
            strip["fx"].append(entry)
            (migration.kept if Path(path).exists() else migration.missing).append(label)
        migration.chains["strips"].append(strip)
    log.info("Effets REAPER repris : %d, introuvables : %d, non repris : %d.",
             len(migration.kept), len(migration.missing), len(migration.skipped))
    return migration


def reaper_plugin_dirs(reaper_exe: Path | None, resource_path: Path | None) -> list[Path]:
    """Dossiers des plugins livrés avec REAPER (ReaEQ, ReaComp… : des VST2)."""
    dirs = []
    if reaper_exe is not None:
        dirs.append(reaper_exe.parent / "Plugins" / "FX")
    if resource_path is not None:
        dirs.append(resource_path / "UserPlugins" / "FX")
    return dirs


__all__ = ["Migration", "build_chains", "reaper_plugin_dirs", "FORMATS", "PluginRef"]
