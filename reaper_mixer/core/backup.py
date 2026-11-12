"""
Export et import de toute la config de REKA dans un seul fichier « .reka ».

Le fichier est une archive zip :
  - manifest.json : version de REKA, date, édition de Voicemeeter, moteur d'effets ;
  - config.json   : thème et couleurs, noms des canaux, routing, état de la
                    console (faders, Muets, envois, Intellipan), options ;
  - background.*  : l'image d'arrière-plan, s'il y en a une ;
  - project.rpp   : le projet REAPER ouvert, donc les chaînes d'effets et les
                    réglages des plugins (les plugins eux-mêmes doivent être
                    installés sur le PC qui importe) ;
  - chains.json   : avec le moteur intégré (REKA), les chaînes d'effets de
                    chaque strip et les réglages de chaque plugin (VST2 / VST3) ;
  - plugins/<n>/…  : en option, les fichiers des plugins eux-mêmes (.dll, ou
                    dossier .vst3 complet) : la sauvegarde se suffit à elle-même.
                    À l'import, ils vont dans %APPDATA%/REKA/plugins, et ne
                    servent que si le plugin n'est pas déjà installé sur le PC.
                    Un plugin est un programme : l'import demande confirmation.

Ce qui est propre au PC n'est pas exporté, et n'est pas touché par un import :
chemins des moteurs, taille de la fenêtre, mise à jour ignorée, et les secrets
Discord (Client Secret, jetons). Un fichier .reka peut donc être partagé.

Un import ne fait confiance à rien : tailles limitées, seuls les fichiers
attendus sont lus (jamais d'extraction dans un chemin venu de l'archive), et
une valeur du mauvais type est ignorée plutôt que de casser le démarrage.
"""

from __future__ import annotations

import copy
import datetime
import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core import config
from core.effects import HOSTS, host_kind
from core.voicemeeter_ctrl import EDITIONS, edition

log = logging.getLogger(__name__)

EXTENSION = ".reka"
FILE_FILTER = f"Sauvegarde REKA (*{EXTENSION})"
FORMAT = 1
MANIFEST, CONFIG, PROJECT, CHAINS = "manifest.json", "config.json", "project.rpp", "chains.json"
BACKGROUND_STEM = "background"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
PREVIOUS_NAME = f"avant-import{EXTENSION}"     # config d'avant le dernier import
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_PROJECT_BYTES = 64 * 1024 * 1024
MAX_CHAINS_BYTES = 64 * 1024 * 1024
PLUGINS_DIR = "plugins"
MAX_PLUGINS = 64
MAX_PLUGIN_FILES = 20000               # fichiers d'un dossier .vst3
MAX_PLUGINS_BYTES = 8 * 1024 ** 3      # tous les plugins, décompressés

# Réglages propres au PC : ni exportés, ni remplacés par un import
LOCAL_KEYS: tuple[tuple[str, str], ...] = (
    ("voicemeeter", "path"),
    ("reaper", "path"), ("reaper", "resource_path"), ("reaper", "template"),
    ("reaper", "project"), ("reaper", "recent_projects"),
    ("flstudio", "path"), ("flstudio", "project"),
    ("apo", "config_dir"),
    ("reka_host", "path"),
    ("vst", "extra_dirs"),
    ("ui", "geometry"), ("ui", "layout_version"),
    ("update", "skipped_build"), ("update", "url"),
    ("discord", "client_secret"), ("discord", "access_token"),
    ("discord", "refresh_token"), ("discord", "token_expires"),
)


class BackupError(Exception):
    """Erreur présentable telle quelle à l'utilisateur."""


@dataclass(frozen=True)
class EmbeddedPlugin:
    """Plugin joint à une sauvegarde (fichiers dans plugins/<key>/)."""

    key: str                          # numéro dans l'archive
    path: str                         # chemin d'origine (sur le PC qui a exporté)
    name: str                         # nom du fichier ou du dossier .vst3
    size: int = 0                     # octets, décompressés
    members: tuple[str, ...] = ()     # fichiers de l'archive, vérifiés


@dataclass(frozen=True)
class Backup:
    """Contenu d'un fichier .reka, lu et vérifié."""

    config: dict
    version: str = ""
    created: str = ""
    kind: str = ""                    # édition de Voicemeeter de la sauvegarde
    host: str = ""                    # moteur d'effets
    project_name: str = ""
    background: tuple[str, bytes] | None = None    # (extension, image)
    project: bytes | None = None
    chains: dict | None = None        # chaînes d'effets du moteur REKA (vérifiées)
    plugins: tuple[EmbeddedPlugin, ...] = ()   # fichiers de plugins joints
    source: Path | None = None        # le fichier .reka (pour extraire les plugins)

    @property
    def plugins_size(self) -> int:
        return sum(p.size for p in self.plugins)

    @property
    def effect_count(self) -> int:
        return sum(len(s.get("fx", [])) for s in (self.chains or {}).get("strips", []))

    def describe(self) -> str:
        parts = [f"REKA {self.version}" if self.version else "REKA"]
        if self.created:
            parts.append(f"du {self.created[:10]}")
        engines = [EDITIONS[self.kind].label.split(" (")[0]] if self.kind in EDITIONS else []
        if self.host in HOSTS:
            engines.append(HOSTS[self.host].label)
        text = " ".join(parts)
        if engines:
            text += f" — {' + '.join(engines)}"
        count = self.effect_count
        extras = [label for label, present in (("image d'arrière-plan", self.background is not None),
                                               ("projet REAPER", self.project is not None),
                                               (f"{count} effet{'s' if count > 1 else ''} REKA",
                                                count > 0),
                                               (f"{len(self.plugins)} plugin"
                                                f"{'s' if len(self.plugins) > 1 else ''} "
                                                f"({_size(self.plugins_size)})",
                                                bool(self.plugins)))
                  if present]
        return text + (f" (avec {' et '.join(extras)})" if extras else "")


def _size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} Go"
    return f"{max(1, round(size / 1024 ** 2))} Mo"


@dataclass
class ImportReport:
    notes: list[str] = field(default_factory=list)   # ce que l'utilisateur doit savoir
    project: str = ""                                # projet REAPER importé (nom du fichier)
    chains: bool = False                             # chaînes d'effets REKA importées
    plugins: list[str] = field(default_factory=list) # plugins installés depuis la sauvegarde
    previous: Path | None = None                     # sauvegarde de la config d'avant


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def portable_config(cfg: dict) -> dict:
    """Copie de la config sans ce qui est propre au PC (chemins, secrets)."""
    portable = copy.deepcopy(cfg)
    for section, key in LOCAL_KEYS:
        if isinstance(portable.get(section), dict):
            portable[section].pop(key, None)
    return portable


def background_file(cfg: dict) -> Path | None:
    path = config.resolve_path(cfg.get("ui", {}).get("background_image"),
                               base=config.app_data_dir())
    if path is None or not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return path


def plugin_files(chains: Path | None) -> list[Path]:
    """Fichiers (ou dossiers .vst3) des plugins des chaînes, présents sur ce PC."""
    if chains is None or not chains.is_file():
        return []
    try:
        doc = json.loads(chains.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: dict[str, Path] = {}
    for strip in doc.get("strips", []) if isinstance(doc, dict) else []:
        for fx in strip.get("fx", []) if isinstance(strip, dict) else []:
            path = Path(str(fx.get("path", ""))) if isinstance(fx, dict) else None
            if path is not None and path.exists():
                found.setdefault(os.path.normcase(str(path)), path)
    return list(found.values())[:MAX_PLUGINS]


def _plugin_entries(path: Path) -> list[tuple[Path, str]]:
    """(fichier, chemin relatif) de tous les fichiers d'un plugin."""
    if path.is_file():
        return [(path, path.name)]
    entries = []
    for current, _dirs, files in os.walk(path):
        for name in files:
            file = Path(current) / name
            entries.append((file, f"{path.name}/{file.relative_to(path).as_posix()}"))
            if len(entries) > MAX_PLUGIN_FILES:
                raise BackupError(f"« {path.name} » contient trop de fichiers pour une sauvegarde.")
    return entries


def export_backup(cfg: dict, target: Path, project: Path | None = None,
                  chains: Path | None = None, plugins: list[Path] | None = None) -> Backup:
    """Écrit la sauvegarde dans `target` (remplacé d'un coup, une fois complet).
    `project` : le projet REAPER à joindre (déjà enregistré), ou None.
    `chains` : les chaînes d'effets du moteur REKA (chains.json), ou None.
    `plugins` : fichiers ou dossiers .vst3 des plugins à joindre."""
    target = Path(target)
    background = background_file(cfg)
    if project is not None and not (project.is_file() and project.suffix.lower() == ".rpp"
                                    and project.stat().st_size <= MAX_PROJECT_BYTES):
        project = None
    if chains is not None and not (chains.is_file() and chains.stat().st_size <= MAX_CHAINS_BYTES):
        chains = None
    embedded = []
    total = 0
    for i, plugin in enumerate((plugins or [])[:MAX_PLUGINS]):
        entries = _plugin_entries(plugin)
        total += sum(file.stat().st_size for file, _ in entries)
        if total > MAX_PLUGINS_BYTES:
            raise BackupError("Les plugins sont trop volumineux pour une sauvegarde (8 Go au plus).")
        embedded.append((str(i), plugin, entries))
    manifest = {
        "app": config.APP_NAME, "format": FORMAT, "version": config.APP_VERSION,
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kind": edition(cfg).kind, "host": host_kind(cfg),
        "background": background.suffix.lower() if background is not None else "",
        "project": project.stem if project is not None else "",
        "plugins": [{"key": key, "path": str(plugin), "name": plugin.name}
                    for key, plugin, _ in embedded],
    }
    tmp = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            archive.writestr(CONFIG, json.dumps(portable_config(cfg), indent=2, ensure_ascii=False))
            if background is not None:
                archive.write(background, BACKGROUND_STEM + background.suffix.lower())
            if project is not None:
                archive.write(project, PROJECT)
            if chains is not None:
                archive.write(chains, CHAINS)
            for key, _plugin, entries in embedded:
                for file, relative in entries:
                    archive.write(file, f"{PLUGINS_DIR}/{key}/{relative}")
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"Sauvegarde impossible : {exc}") from exc
    log.info("Config exportée : %s", target)
    return read_backup(target)


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #
def _member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise BackupError(f"« {name} » est trop gros pour une sauvegarde REKA.")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise BackupError(f"« {name} » est trop gros pour une sauvegarde REKA.")
    return data


def _json_member(archive: zipfile.ZipFile, name: str) -> dict:
    data = _member(archive, name, MAX_CONFIG_BYTES)
    if data is None:
        raise BackupError("Ce fichier n'est pas une sauvegarde REKA.")
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise BackupError("Sauvegarde REKA illisible (fichier abîmé).") from exc
    if not isinstance(value, dict):
        raise BackupError("Sauvegarde REKA illisible (fichier abîmé).")
    return value


def read_backup(path: Path) -> Backup:
    """Lit et vérifie un fichier .reka ; lève BackupError s'il n'est pas utilisable."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _json_member(archive, MANIFEST)
            if manifest.get("app") != config.APP_NAME:
                raise BackupError("Ce fichier n'est pas une sauvegarde REKA.")
            try:
                file_format = int(manifest.get("format", 0))
            except (TypeError, ValueError):
                file_format = 0
            if file_format > FORMAT:
                raise BackupError("Cette sauvegarde vient d'une version plus récente de REKA : "
                                  "mets REKA à jour avant de l'importer.")
            cfg = _json_member(archive, CONFIG)
            background = None
            suffix = str(manifest.get("background") or "").lower()
            if suffix in IMAGE_SUFFIXES:
                image = _member(archive, BACKGROUND_STEM + suffix, MAX_IMAGE_BYTES)
                if image:
                    background = (suffix, image)
            project = _member(archive, PROJECT, MAX_PROJECT_BYTES)
            chains_data = _member(archive, CHAINS, MAX_CHAINS_BYTES)
            plugins = _embedded_plugins(archive, manifest.get("plugins"))
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise BackupError("Ce fichier n'est pas une sauvegarde REKA lisible.") from exc
    if project is not None and not project.lstrip().startswith(b"<REAPER_PROJECT"):
        project = None
    chains = None
    if chains_data is not None:
        from core.reka_host import ChainsError, parse_chains
        try:
            chains = parse_chains(chains_data)
        except ChainsError as exc:
            log.warning("Chaînes d'effets de la sauvegarde ignorées : %s", exc)
    return Backup(config=cfg, version=str(manifest.get("version") or ""),
                  created=str(manifest.get("created") or ""),
                  kind=str(manifest.get("kind") or ""), host=str(manifest.get("host") or ""),
                  project_name=str(manifest.get("project") or ""),
                  background=background, project=project, chains=chains,
                  plugins=plugins, source=Path(path))


_SAFE_PART = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")


def _safe_relative(name: str) -> bool:
    """Chemin relatif d'archive sans détour possible (pas de « .. », de lecteur…)."""
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        return False
    parts = name.split("/")
    return all(part not in ("", ".", "..") and _SAFE_PART.match(part)
               and part.rstrip(" .") == part for part in parts)


def _embedded_plugins(archive: zipfile.ZipFile, listed) -> tuple[EmbeddedPlugin, ...]:
    """Plugins annoncés par le manifeste, vérifiés contre le contenu de l'archive."""
    if not isinstance(listed, list):
        return ()
    infos = archive.infolist()
    plugins, total = [], 0
    for item in listed[:MAX_PLUGINS]:
        if not isinstance(item, dict):
            continue
        key, name = str(item.get("key", "")), str(item.get("name", ""))
        if not key.isdigit() or not _safe_relative(name) or "/" in name:
            continue
        prefix = f"{PLUGINS_DIR}/{key}/"
        members = [i for i in infos if i.filename.startswith(prefix) and not i.is_dir()]
        relative = [i.filename[len(prefix):] for i in members]
        if not members or len(members) > MAX_PLUGIN_FILES or not all(
                _safe_relative(r) and (r == name or r.startswith(name + "/")) for r in relative):
            log.warning("Plugin « %s » de la sauvegarde ignoré (contenu inattendu).", name)
            continue
        size = sum(i.file_size for i in members)
        total += size
        if total > MAX_PLUGINS_BYTES:
            raise BackupError("Les plugins de cette sauvegarde sont trop volumineux.")
        plugins.append(EmbeddedPlugin(key, str(item.get("path", ""))[:1024], name, size,
                                      tuple(i.filename for i in members)))
    return tuple(plugins)


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def _same_kind(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def merge_checked(defaults: dict, imported: dict) -> dict:
    """Les défauts, remplacés par les valeurs importées du même type (une
    valeur d'un autre type est ignorée). Les clés inconnues des défauts sont
    gardées telles quelles : couleurs par élément, état de la console…"""
    merged = copy.deepcopy(defaults)
    for key, value in imported.items():
        default = defaults.get(key)
        if isinstance(default, dict):
            if isinstance(value, dict):
                merged[key] = merge_checked(default, value)
        elif key not in defaults or default is None or _same_kind(default, value):
            merged[key] = copy.deepcopy(value)
    return merged


def _project_name(stem: str) -> str:
    """Nom de fichier libre, dans le dossier de REKA, pour le projet importé."""
    stem = stem.replace("\\", "/").rsplit("/", 1)[-1]        # jamais un chemin
    stem = re.sub(r"[^\w \-]", "", stem, flags=re.UNICODE).strip()[:40]
    if not stem or stem.lower().startswith("session"):
        stem = "Projet"
    folder = config.app_data_dir()
    for n in range(1, 1000):
        name = f"{stem} (importé{'' if n == 1 else f' {n}'}).rpp"
        if not (folder / name).exists():
            return name
    raise BackupError("Trop de projets importés : fais le ménage dans le dossier de REKA.")


def install_plugins(backup: Backup, chains: dict | None, folder: Path) -> list[str]:
    """Extrait les plugins joints dans `folder`/plugins et fait pointer les
    chaînes dessus, seulement pour les plugins absents de ce PC. Renvoie les
    noms des plugins utilisés depuis la sauvegarde."""
    if not backup.plugins or backup.source is None:
        return []
    root = (folder / PLUGINS_DIR).resolve()
    wanted: dict[str, list[dict]] = {}
    for strip in (chains or {}).get("strips", []):
        for fx in strip.get("fx", []):
            if not Path(fx.get("path", "")).exists():
                wanted.setdefault(os.path.normcase(fx.get("path", "")).lower(), []).append(fx)
    used = []
    with zipfile.ZipFile(backup.source) as archive:
        for plugin in backup.plugins:
            users = wanted.get(os.path.normcase(plugin.path).lower())
            if not users:
                continue        # installé sur ce PC (ou plus utilisé) : on garde celui du PC
            target = (root / f"{Path(plugin.name).stem}-{plugin.key}").resolve()
            if not str(target).startswith(str(root)):
                continue
            prefix = f"{PLUGINS_DIR}/{plugin.key}/"
            for member in plugin.members:
                destination = (target / member[len(prefix):]).resolve()
                if not str(destination).startswith(str(target) + os.sep):
                    raise BackupError("Sauvegarde refusée : chemin de plugin invalide.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as out:
                    shutil.copyfileobj(source, out, 1 << 20)
            for fx in users:
                fx["path"] = str(target / plugin.name)
            used.append(plugin.name)
    return used


def import_backup(cfg: dict, backup: Backup, installed_kinds: Iterable[str] | None = None,
                  keep_previous: bool = True) -> ImportReport:
    """Remplace la config en mémoire (`cfg`, modifié sur place) par celle de la
    sauvegarde ; l'appelant l'enregistre puis redémarre REKA.

    `installed_kinds` : éditions de Voicemeeter installées sur ce PC ; si celle
    de la sauvegarde n'en fait pas partie, l'édition actuelle est gardée (et le
    projet REAPER, câblé pour l'autre édition, n'est pas importé).
    """
    report = ImportReport()
    folder = config.app_data_dir()
    new = merge_checked(config.default_config(), backup.config)
    for section, key in LOCAL_KEYS:
        current = cfg.get(section)
        if isinstance(current, dict) and key in current:
            new.setdefault(section, {})[key] = copy.deepcopy(current[key])
    new["setup_done"] = bool(cfg.get("setup_done", True))

    current_kind, wanted_kind = edition(cfg).kind, edition(new).kind
    installed = list(installed_kinds) if installed_kinds is not None else []
    keep_edition = bool(installed) and wanted_kind not in installed and wanted_kind != current_kind
    if keep_edition:
        new.setdefault("voicemeeter", {})["kind"] = current_kind
        report.notes.append(
            f"{EDITIONS[wanted_kind].label.split(' (')[0]} n'est pas installé sur ce PC : "
            f"REKA reste sur {EDITIONS[current_kind].label.split(' (')[0]}.")

    if keep_previous:
        try:
            current_chains = folder / CHAINS
            export_backup(cfg, folder / PREVIOUS_NAME,
                          chains=current_chains if current_chains.is_file() else None)
            report.previous = folder / PREVIOUS_NAME
        except BackupError as exc:
            log.warning("Config d'avant l'import non sauvegardée : %s", exc)

    try:
        for old in folder.glob(f"{BACKGROUND_STEM}.*"):
            old.unlink(missing_ok=True)
        image_name = ""
        if backup.background is not None:
            suffix, image = backup.background
            image_name = BACKGROUND_STEM + suffix
            (folder / image_name).write_bytes(image)
        new.setdefault("ui", {})["background_image"] = image_name

        if backup.project is not None:
            if keep_edition:
                report.notes.append("Le projet REAPER de la sauvegarde est câblé pour l'autre "
                                    "édition de Voicemeeter : il n'a pas été importé.")
            else:
                report.project = _project_name(backup.project_name)
                (folder / report.project).write_bytes(backup.project)
                new.setdefault("reaper", {})["project"] = report.project
                report.notes.append(f"Projet REAPER importé : « {Path(report.project).stem} ». "
                                    "Ses plugins doivent être installés sur ce PC.")

        if backup.chains is not None:
            from core.reka_host import CHAINS_FILENAME, save_chains_file
            chains = copy.deepcopy(backup.chains)
            report.plugins = install_plugins(backup, chains, folder)
            if report.plugins:
                report.notes.append("Plugins installés depuis la sauvegarde : "
                                    + ", ".join(report.plugins) + ".")
            target = folder / CHAINS_FILENAME
            if target.is_file():           # chaînes d'avant l'import : gardées à côté
                target.replace(folder / f"avant-import-{CHAINS_FILENAME}")
            save_chains_file(target, chains)
            report.chains = True
            count = backup.effect_count
            report.notes.append(f"Chaînes d'effets importées ({count} effet{'s' if count > 1 else ''}"
                                ", réglages compris). Les plugins doivent être installés sur ce PC.")
    except OSError as exc:
        raise BackupError(f"Import impossible : {exc}") from exc

    cfg.clear()
    cfg.update(new)
    log.info("Config importée (%s).", backup.describe())
    return report
