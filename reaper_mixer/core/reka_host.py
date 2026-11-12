"""
Moteur d'effets intégré : reka-host.exe, l'hôte de plugins de REKA.

REKA n'a plus besoin de REAPER : reka-host.exe (sources dans native/reka-host)
charge lui-même les plugins VST2 et VST3, et traite le son des strips dans
Voicemeeter grâce à son « audio callback » (API Remote, mode entrées). Pas de
pilote ASIO à choisir, pas de fenêtre à masquer.

Dialogue avec l'hôte : REKA ouvre un port TCP sur 127.0.0.1, lance l'hôte
avec ce port et un jeton à usage unique ; l'hôte s'y connecte et se présente
(« hello » + jeton). Ensuite, une ligne JSON par message :

  REKA → hôte  {"id": 3, "cmd": "add_fx", "strip": 0, "plugin": {...}}
  hôte → REKA  {"type": "ack", "id": 3} / {"type": "error", "id": 3, "message": "…"}
               {"type": "state", ...}   chaînes, plugins, carte son (à chaque changement)
               {"type": "chains", ...}  chaînes complètes, réglages des plugins compris
               {"type": "dirty"}        un réglage a changé dans l'interface d'un plugin

Les chaînes d'effets appartiennent à REKA : elles sont enregistrées dans
%APPDATA%/REKA/chains.json (réglages des plugins en base64) et renvoyées à
l'hôte à chaque lancement. Le même fichier part dans les sauvegardes .reka :
les chaînes d'effets se partagent avec leurs réglages ; seuls les plugins
doivent être installés sur le PC qui importe (REKA les retrouve par nom de
fichier s'ils sont ailleurs).

Si l'hôte s'arrête (plugin qui plante), REKA le relance avec les dernières
chaînes enregistrées ; après plusieurs arrêts rapprochés, il abandonne et
le signale plutôt que de boucler.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject

from core import config
from core.effects import HOST_REKA, EffectsHost, samples_to_ms
from core.reaper_ctrl import FxInfo, TrackInfo
from core.voicemeeter_ctrl import edition, input_channel, strip_map
from core.vst_scanner import PluginInfo

log = logging.getLogger(__name__)

PROTOCOL = 1
ENGINE_EXE = "reka-host.exe"
ENGINE_RESOURCE = f"engine/{ENGINE_EXE}"
CHAINS_FILENAME = "chains.json"
CHAINS_FORMAT = 1
FORMATS = ("VST2", "VST3")

CONNECT_TIMEOUT_S = 15.0      # lancement → « hello » de l'hôte
CHAINS_TIMEOUT_S = 3.0        # attente des chaînes à la fermeture
DIRTY_DELAY_S = 2.0           # réglage changé dans un plugin → chaînes relues
RESTART_DELAY_S = 1.0
MAX_RESTARTS = 3              # arrêts tolérés…
RESTART_WINDOW_S = 60.0       # … sur cette durée

MAX_LINE_BYTES = 96 * 1024 * 1024
MAX_CHAINS_BYTES = 64 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024     # réglages d'un plugin (décodés)
MAX_STRIPS = 16
MAX_FX_PER_STRIP = 32


# --------------------------------------------------------------------------- #
# Plugins : identification et recherche sur le disque
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PluginRef:
    format: str        # VST2 ou VST3
    path: str          # .dll (VST2) ou .vst3
    uid: str = ""      # VST2 : identifiant (plugins « shell ») ; VST3 : classe, en hex
    name: str = ""

    @property
    def add_name(self) -> str:
        """Texte passé à add_fx : « VST3:C:\\…\\Pro-Q 4.vst3|uid »."""
        return f"{self.format}:{self.path}" + (f"|{self.uid}" if self.uid else "")

    def to_json(self) -> dict:
        return {"format": self.format, "path": self.path, "uid": self.uid, "name": self.name}


def parse_add_name(text: str) -> PluginRef | None:
    """Inverse de PluginRef.add_name ; un chemin seul est deviné par son extension."""
    text = (text or "").strip()
    fmt, sep, rest = text.partition(":")
    if sep and fmt.upper() in FORMATS:
        fmt = fmt.upper()
    else:
        rest = text
        fmt = "VST3" if text.lower().endswith(".vst3") else "VST2"
    path, _, uid = rest.partition("|")
    path = path.strip()
    if not path:
        return None
    return PluginRef(fmt, path, uid.strip(), _stem(path))


def _stem(path: str) -> str:
    return Path(path.replace("\\", "/")).stem


def default_vst3_dirs(extra: list[str] | None = None) -> list[Path]:
    from core.vst_scanner import default_vst3_dirs as dirs
    return dirs(extra)


def default_vst2_dirs(extra: list[str] | None = None) -> list[Path]:
    from core.apo_ctrl import default_vst2_dirs as dirs
    return dirs(extra)


def scan_plugins(extra_dirs: list[str] | None = None, limit: int = 4000) -> list[PluginInfo]:
    """Plugins installés, sans les charger (un plugin bancal ne peut rien casser) :
    les .vst3 des dossiers VST3, et les .dll VST2 64 bits des dossiers VST.
    PluginInfo.add_name contient le chemin (voir PluginRef)."""
    from core.apo_ctrl import scan_vst2

    found: dict[str, PluginInfo] = {}
    for root in default_vst3_dirs(extra_dirs):
        if not root.is_dir():
            continue
        for current, subdirs, files in os.walk(root):
            bundles = [d for d in subdirs if d.lower().endswith(".vst3")]
            subdirs[:] = [d for d in subdirs if not d.lower().endswith(".vst3")]
            for entry in bundles + [f for f in files if f.lower().endswith(".vst3")]:
                path = str(Path(current) / entry)
                ref = PluginRef("VST3", path, "", Path(entry).stem)
                found.setdefault(path.lower(), PluginInfo(ref.name, ref.add_name, "VST3", ""))
                if len(found) >= limit:
                    break
    for plugin in scan_vst2(default_vst2_dirs(extra_dirs), limit=limit):
        ref = PluginRef("VST2", plugin.add_name, "", plugin.name)
        found.setdefault(plugin.add_name.lower(),
                         PluginInfo(plugin.name, ref.add_name, "VST2", plugin.vendor))
    plugins = sorted(found.values(), key=lambda p: (p.name.lower(), p.kind))
    log.info("%d plugins VST2 / VST3 trouvés.", len(plugins))
    return plugins


def resolve_paths(doc: dict, plugins: list[PluginInfo]) -> list[str]:
    """Chaînes venues d'un autre PC : un plugin introuvable à son chemin est
    cherché par nom de fichier parmi `plugins`. Modifie `doc` ; renvoie les noms
    des plugins restés introuvables."""
    by_file: dict[tuple[str, str], str] = {}
    for plugin in plugins:
        ref = parse_add_name(plugin.add_name)
        if ref is not None:
            by_file.setdefault((ref.format, Path(ref.path.replace("\\", "/")).name.lower()), ref.path)
    missing = []
    for strip in doc.get("strips", []):
        for fx in strip.get("fx", []):
            path = fx.get("path", "")
            if path and Path(path).exists():
                continue
            other = by_file.get((fx.get("format", ""), Path(path.replace("\\", "/")).name.lower()))
            if other is not None:
                log.info("Plugin « %s » retrouvé : %s", fx.get("name") or path, other)
                fx["path"] = other
            else:
                missing.append(fx.get("name") or _stem(path))
    return missing


# --------------------------------------------------------------------------- #
# Chaînes d'effets (chains.json, et dans les .reka)
# --------------------------------------------------------------------------- #
class ChainsError(ValueError):
    pass


def empty_chains(count: int) -> dict:
    return {"format": CHAINS_FORMAT, "strips": [{"enabled": True, "fx": []} for _ in range(count)]}


def _clean_fx(fx: Any) -> dict | None:
    if not isinstance(fx, dict):
        return None
    fmt = str(fx.get("format", "")).upper()
    path = fx.get("path")
    if fmt not in FORMATS or not isinstance(path, str) or not path.strip() or len(path) > 1024:
        return None
    state = fx.get("state", "")
    if not isinstance(state, str):
        state = ""
    if state:
        if len(state) > MAX_STATE_BYTES * 4 // 3 + 4:
            state = ""
        else:
            try:
                base64.b64decode(state, validate=True)
            except (binascii.Error, ValueError):
                state = ""
    return {
        "format": fmt,
        "path": path.strip(),
        "uid": str(fx.get("uid", ""))[:128],
        "name": str(fx.get("name", "") or _stem(path))[:200],
        "vendor": str(fx.get("vendor", ""))[:200],
        "enabled": bool(fx.get("enabled", True)),
        "state": state,
    }


def clean_chains(value: Any, count: int | None = None) -> dict:
    """Chaînes vérifiées : tout ce qui n'a pas la bonne forme est écarté (le
    fichier peut venir d'une sauvegarde partagée). `count` : nombre de strips
    voulu (complété ou tronqué)."""
    if not isinstance(value, dict) or not isinstance(value.get("strips"), list):
        raise ChainsError("chaînes d'effets illisibles")
    try:
        if int(value.get("format", CHAINS_FORMAT)) > CHAINS_FORMAT:
            raise ChainsError("chaînes d'effets d'une version plus récente de REKA")
    except (TypeError, ValueError) as exc:
        raise ChainsError("chaînes d'effets illisibles") from exc
    strips = []
    for strip in value["strips"][:MAX_STRIPS]:
        if not isinstance(strip, dict):
            strip = {}
        fx = [f for f in (_clean_fx(f) for f in (strip.get("fx") or [])[:MAX_FX_PER_STRIP]
                          if isinstance(strip.get("fx"), list)) if f is not None]
        strips.append({"enabled": bool(strip.get("enabled", True)), "fx": fx})
    if count is not None:
        strips = strips[:count] + [{"enabled": True, "fx": []}
                                   for _ in range(count - len(strips))]
    return {"format": CHAINS_FORMAT, "strips": strips}


def parse_chains(data: bytes, count: int | None = None) -> dict:
    if len(data) > MAX_CHAINS_BYTES:
        raise ChainsError("chaînes d'effets trop volumineuses")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChainsError("chaînes d'effets illisibles") from exc
    return clean_chains(value, count)


def chains_path() -> Path:
    return config.app_data_dir() / CHAINS_FILENAME


def load_chains_file(path: Path, count: int) -> dict:
    try:
        return parse_chains(path.read_bytes(), count)
    except FileNotFoundError:
        return empty_chains(count)
    except (OSError, ChainsError) as exc:
        log.error("Chaînes d'effets non relues (%s) : %s", path, exc)
        try:
            path.replace(path.with_suffix(".abime.json"))   # gardé pour un dépannage
        except OSError:
            pass
        return empty_chains(count)


def save_chains_file(path: Path, doc: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Lancement de l'hôte
# --------------------------------------------------------------------------- #
def engine_executable(cfg: dict) -> Path | None:
    """reka-host.exe : chemin de la config (reka_host.path), sinon celui livré avec REKA."""
    configured = config.resolve_path(cfg.get("reka_host", {}).get("path"))
    if configured is not None:
        return configured if configured.is_file() else None
    bundled = config.resource_path(ENGINE_RESOURCE)
    return bundled if bundled.is_file() else None


class EngineProcess:
    """Interface minimale d'un hôte lancé (subprocess.Popen, ou faux hôte)."""

    pid: int | None = None

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        pass


Launcher = Callable[[int, str], Any]


def launch_executable(exe: Path, port: int, token: str, client: str = "REKA") -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_file = config.app_data_dir() / "reka-host.log"
    return subprocess.Popen([str(exe), "--port", str(port), "--token", token,
                             "--client", client, "--log", str(log_file)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, cwd=str(exe.parent))


# --------------------------------------------------------------------------- #
# État renvoyé par l'hôte
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EngineStatus:
    running: bool = False        # audio callback de Voicemeeter actif
    samplerate: float = 0.0
    blocksize: int = 0
    message: str = ""            # problème à afficher (Voicemeeter absent…)


def parse_state(message: dict, names: list[str]) -> tuple[EngineStatus, list[TrackInfo]]:
    engine = message.get("engine") if isinstance(message.get("engine"), dict) else {}
    try:
        status = EngineStatus(bool(engine.get("running", False)),
                              float(engine.get("samplerate", 0) or 0),
                              int(engine.get("blocksize", 0) or 0),
                              str(engine.get("message", "") or ""))
    except (TypeError, ValueError):
        status = EngineStatus(message="état du moteur illisible")
    tracks = []
    strips = message.get("strips") if isinstance(message.get("strips"), list) else []
    for i, name in enumerate(names):
        strip = strips[i] if i < len(strips) and isinstance(strips[i], dict) else {}
        fx_list = []
        for fx in strip.get("fx") or []:
            if not isinstance(fx, dict):
                continue
            label = str(fx.get("name") or "?")
            if fx.get("missing"):
                label += " (introuvable)"
            fmt = str(fx.get("format") or "").upper()
            try:
                latency = int(fx.get("latency", -1))
            except (TypeError, ValueError):
                latency = -1
            fx_list.append(FxInfo(f"{fmt}: {label}" if fmt else label,
                                  bool(fx.get("enabled", True)) and not fx.get("missing"),
                                  bool(fx.get("open", False)), latency))
        tracks.append(TrackInfo(name, bool(strip.get("enabled", True)), tuple(fx_list), index=i))
    return status, tracks


class LineReader:
    """Lignes d'un socket, avec délai d'attente (socket.makefile ne supporte pas
    qu'une lecture expire). Une ligne trop longue coupe la liaison."""

    def __init__(self, sock: socket.socket, limit: int = MAX_LINE_BYTES) -> None:
        self._sock = sock
        self._limit = limit
        self._buffer = bytearray()

    def readline(self, timeout_s: float | None) -> bytes | None:
        """Une ligne (sans le saut de ligne), None si rien n'est arrivé à temps ;
        lève EOFError quand la connexion est fermée."""
        while True:
            end = self._buffer.find(b"\n")
            if end >= 0:
                line = bytes(self._buffer[:end])
                del self._buffer[:end + 1]
                return line
            if len(self._buffer) > self._limit:
                raise EOFError("ligne trop longue")
            self._sock.settimeout(timeout_s)
            try:
                chunk = self._sock.recv(1 << 20)
            except socket.timeout:
                return None
            except OSError as exc:
                raise EOFError(str(exc)) from exc
            if not chunk:
                raise EOFError("connexion fermée")
            self._buffer += chunk


# --------------------------------------------------------------------------- #
# Contrôleur
# --------------------------------------------------------------------------- #
class RekaHostController(EffectsHost):
    kind = HOST_REKA
    manages_fx = True
    can_move_fx = True
    empty_hint = ""

    def __init__(self, cfg: dict, parent: QObject | None = None,
                 launcher: Launcher | None = None, chains_file: Path | None = None) -> None:
        super().__init__(cfg, parent)
        ed = edition(cfg)
        strips = strip_map(cfg)
        self._strips = [(input_channel(s, ed.physical_strips), 2 if s < ed.physical_strips else 8)
                        for s in strips]
        self._count = len(strips)
        self._launcher = launcher
        self._chains_file = chains_file or chains_path()
        self._doc = load_chains_file(self._chains_file, self._count)
        self._keep_file = True            # False après un import : le fichier importé est gardé
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._server: socket.socket | None = None
        self._process = None
        self._token = ""
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = False
        self._ready = threading.Event()
        self._tracks: list[TrackInfo] | None = None
        self._status = EngineStatus()
        self._chains_event = threading.Event()
        self._dirty_at: float | None = None
        self._crashes: list[float] = []
        self._gave_up = False
        self._missing_reported: set[str] = set()
        self._plugins_cache: list[PluginInfo] | None = None
        self._reader: LineReader | None = None
        self.version = "REKA"

    # ------------------------------------------------------------ état
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def chains_file(self) -> Path:
        return self._chains_file

    @property
    def chains(self) -> dict:
        """Dernières chaînes connues (réglages des plugins compris)."""
        with self._lock:
            return json.loads(json.dumps(self._doc))

    @property
    def engine_status(self) -> EngineStatus:
        return self._status

    @property
    def strip_tracks(self) -> list[TrackInfo | None]:
        if self._tracks is not None:
            return list(self._tracks)
        return self._tracks_from_doc()

    def engine_process(self):
        """Processus de l'hôte (pour la mesure de charge CPU), ou None."""
        process = self._process
        pid = getattr(process, "pid", None)
        if pid is None or (hasattr(process, "poll") and process.poll() is not None):
            return None
        try:
            import psutil
            return psutil.Process(pid)
        except Exception:
            return None

    def status(self, connected: bool) -> tuple[bool, str, str]:
        if self._gave_up:
            return False, "REKA arrêté", \
                "Le moteur d'effets s'est arrêté plusieurs fois de suite : un plugin plante " \
                "peut-être. Retire-le, puis relance REKA."
        if not connected:
            return False, "REKA déconnecté", \
                self._status.message or "Le moteur d'effets ne répond pas : relance REKA."
        if not self._status.running:
            return False, "REKA en attente", \
                self._status.message or "Voicemeeter n'envoie pas encore le son au moteur."
        return True, "REKA connecté", \
            f"{self._status.samplerate:.0f} Hz, {self._status.blocksize} échantillons par bloc"

    def latency_report(self) -> str:
        worst = max(((self.strip_latency_ms(i) or 0.0, i) for i in range(self._count)),
                    default=(0.0, 0))
        text = ("Le moteur REKA traite le son dans Voicemeeter : aucun tampon ASIO ajouté")
        if worst[0] >= 0.05:
            text += f", seulement {worst[0]:.1f} ms de plugins sur « {self._names()[worst[1]]} »"
        return text + "."

    def strip_latency_ms(self, strip: int) -> float | None:
        tracks, rate = self._tracks or [], self._status.samplerate
        if strip >= len(tracks) or rate <= 0:
            return None
        track = tracks[strip]
        if not track.fx_enabled:
            return 0.0
        return samples_to_ms(sum(max(0, fx.latency) for fx in track.fx if fx.enabled), rate)

    def fx_latency_ms(self, strip: int, fx_index: int) -> float | None:
        tracks, rate = self._tracks or [], self._status.samplerate
        if strip >= len(tracks) or rate <= 0:
            return None
        fx = tracks[strip].fx
        if not 0 <= fx_index < len(fx) or fx[fx_index].latency < 0:
            return None
        return samples_to_ms(fx[fx_index].latency, rate)

    def installed_plugins(self, refresh: bool = False) -> list[PluginInfo]:
        if self._plugins_cache is None or refresh:
            self._plugins_cache = scan_plugins(self._cfg.get("vst", {}).get("extra_dirs", []))
        return list(self._plugins_cache)

    # ------------------------------------------------------------ démarrage
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.tracks_changed.emit(self.strip_tracks)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reka-host", daemon=True)
        self._thread.start()

    def ping(self, timeout_s: float) -> bool:
        return self._ready.wait(timeout_s)

    def close(self, save_project: bool = True) -> None:
        if save_project and self._connected:
            self._chains_event.clear()
            if self._send({"cmd": "get_chains"}) and not self._chains_event.wait(CHAINS_TIMEOUT_S):
                log.warning("Le moteur REKA n'a pas renvoyé ses chaînes d'effets à temps.")
        self._stop.set()
        self._send({"cmd": "quit"})
        sock, server = self._sock, self._server
        for s in (sock, server):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._terminate_process()

    def write_chains(self) -> None:
        """Enregistre les dernières chaînes connues dans chains.json."""
        if self._keep_file:
            try:
                save_chains_file(self._chains_file, self.chains)
            except OSError as exc:
                log.error("Chaînes d'effets non enregistrées : %s", exc)

    def keep_imported_chains(self) -> None:
        """Un import vient de remplacer chains.json : ne plus l'écraser."""
        self._keep_file = False

    # ------------------------------------------------------------ commandes
    def rename_track(self, strip: int, name: str) -> None:
        self._send({"cmd": "rename", "strip": strip, "name": name})

    def add_fx(self, strip: int, fx_name: str) -> None:
        ref = parse_add_name(fx_name)
        if ref is None:
            self.command_failed.emit("Plugin non reconnu.")
            return
        if len(self._doc_strip(strip).get("fx", [])) >= MAX_FX_PER_STRIP:
            self.command_failed.emit(f"{MAX_FX_PER_STRIP} effets au plus par strip.")
            return
        self._command({"cmd": "add_fx", "strip": strip, "plugin": ref.to_json()})

    def remove_fx(self, strip: int, fx_index: int) -> None:
        self._command({"cmd": "remove_fx", "strip": strip, "index": fx_index})

    def move_fx(self, src_strip: int, src_index: int, dst_strip: int, dst_index: int,
                copy: bool = False) -> None:
        self._command({"cmd": "move_fx", "src": src_strip, "src_index": src_index,
                       "dst": dst_strip, "dst_index": dst_index, "copy": bool(copy)})

    def set_fx_enabled(self, strip: int, fx_index: int, enabled: bool) -> None:
        self._command({"cmd": "fx_enabled", "strip": strip, "index": fx_index,
                       "enabled": bool(enabled)})

    def set_chain_enabled(self, strip: int, enabled: bool) -> None:
        self._command({"cmd": "chain_enabled", "strip": strip, "enabled": bool(enabled)})

    def toggle_fx_ui(self, strip: int, fx_index: int) -> None:
        self._command({"cmd": "toggle_ui", "strip": strip, "index": fx_index}, save=False)

    def save_project(self) -> None:
        """Relit les chaînes (réglages des plugins) et les enregistre."""
        self._send({"cmd": "get_chains"})

    def request_installed_fx(self) -> None:
        self.installed_fx_ready.emit(None)

    # ------------------------------------------------------------ interne
    def _names(self) -> list[str]:
        names = list(self._cfg.get("ui", {}).get("strip_names", []))
        return (names + [f"Strip {i + 1}" for i in range(len(names), self._count)])[:self._count]

    def _doc_strip(self, strip: int) -> dict:
        with self._lock:
            strips = self._doc.get("strips", [])
            return strips[strip] if 0 <= strip < len(strips) else {}

    def _tracks_from_doc(self) -> list[TrackInfo | None]:
        names = self._names()
        with self._lock:
            strips = list(self._doc.get("strips", []))
        return [TrackInfo(names[i], bool(strip.get("enabled", True)),
                          tuple(FxInfo(f"{fx['format']}: {fx.get('name') or _stem(fx['path'])}",
                                       bool(fx.get("enabled", True)), False)
                                for fx in strip.get("fx", [])), index=i)
                for i, strip in enumerate(strips)]

    def _command(self, message: dict, save: bool = True) -> None:
        if not self._send(message):
            self.command_failed.emit("Le moteur d'effets REKA n'est pas prêt : réessaie dans "
                                     "un instant.")
            return
        if save:
            self._send({"cmd": "get_chains"})

    def _send(self, message: dict) -> bool:
        sock = self._sock
        if sock is None:
            return False
        if not self._connected and message.get("cmd") not in ("setup", "load", "quit"):
            return False
        with self._send_lock:
            self._seq += 1
            message = {"id": self._seq, **message}
            data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            try:
                sock.sendall(data)
            except OSError as exc:
                log.debug("Envoi au moteur REKA impossible : %s", exc)
                return False
        return True

    # -------------------------------------------------- thread de connexion
    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._session()
            except Exception:
                log.exception("Erreur dans la liaison avec le moteur REKA")
            self._set_connected(False)
            self._terminate_process()
            if self._stop.is_set():
                break
            now = time.monotonic()
            self._crashes = [t for t in self._crashes if now - t < RESTART_WINDOW_S] + [now]
            if len(self._crashes) > MAX_RESTARTS:
                self._gave_up = True
                log.error("Moteur REKA arrêté %d fois en %.0f s : abandon.",
                          len(self._crashes), RESTART_WINDOW_S)
                self.command_failed.emit("Le moteur d'effets s'est arrêté plusieurs fois : un "
                                         "plugin plante peut-être. Retire-le puis relance REKA.")
                self.connection_changed.emit(False)
                break
            if now - started > 1.0:
                log.warning("Moteur REKA arrêté : relance.")
            self._stop.wait(RESTART_DELAY_S)

    def _session(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(0.5)
            port = server.getsockname()[1]
            self._token = secrets.token_hex(16)
            if not self._launch(port):
                self._stop.wait(5.0)
                return
            sock = self._accept(server)
        finally:
            try:
                server.close()
            except OSError:
                pass
            self._server = None
        if sock is None:
            return
        self._sock = sock
        try:
            self._read_loop(sock)
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _launch(self, port: int) -> bool:
        try:
            if self._launcher is not None:
                self._process = self._launcher(port, self._token)
            else:
                exe = engine_executable(self._cfg)
                if exe is None:
                    self._status = EngineStatus(message=f"{ENGINE_EXE} est introuvable : "
                                                "réinstalle REKA.")
                    log.error("%s introuvable.", ENGINE_EXE)
                    self.connection_changed.emit(False)
                    return False
                self._process = launch_executable(exe, port, self._token)
        except OSError as exc:
            log.error("Lancement du moteur REKA impossible : %s", exc)
            self._status = EngineStatus(message=f"Lancement impossible : {exc}")
            return False
        log.info("Moteur REKA lancé (port %d).", port)
        return True

    def _accept(self, server: socket.socket) -> socket.socket | None:
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                log.error("Le moteur REKA s'est arrêté avant de se connecter.")
                return None
            try:
                sock, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return None
            reader = LineReader(sock)
            try:
                line = reader.readline(5.0)
                hello = json.loads(line.decode("utf-8")) if line else {}
            except (EOFError, UnicodeDecodeError, ValueError):
                hello = {}
            if not isinstance(hello, dict):
                hello = {}
            if hello.get("type") == "hello" and secrets.compare_digest(
                    str(hello.get("token", "")), self._token):
                self.version = f"REKA {hello.get('version', '')}".strip()
                if int(hello.get("protocol", 0) or 0) != PROTOCOL:
                    log.warning("Moteur REKA : protocole %s (attendu %d).",
                                hello.get("protocol"), PROTOCOL)
                self._reader = reader
                return sock
            log.warning("Connexion refusée au port du moteur REKA (jeton invalide).")
            sock.close()
        log.error("Le moteur REKA ne s'est pas connecté.")
        return None

    def _read_loop(self, sock: socket.socket) -> None:
        reader = self._reader
        names = self._names()
        self._send({"cmd": "setup", "client": config.APP_NAME,
                    "strips": [{"name": names[i], "channel": ch, "channels": n}
                               for i, (ch, n) in enumerate(self._strips)]})
        doc = self.chains
        missing = resolve_paths(doc, self.installed_plugins()) if any(
            s["fx"] for s in doc["strips"]) else []
        new = [m for m in missing if m not in self._missing_reported]
        if new:
            self._missing_reported.update(new)
            self.command_failed.emit("Plugin introuvable sur ce PC : " + ", ".join(new)
                                     + ". Ses réglages sont gardés.")
        self._send({"cmd": "load", "chains": doc})
        while not self._stop.is_set():
            if self._dirty_at is not None and time.monotonic() >= self._dirty_at:
                self._dirty_at = None
                self._send({"cmd": "get_chains"})
            try:
                line = reader.readline(0.25)
            except EOFError:
                return
            if line is None:
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                log.debug("Ligne illisible du moteur REKA ignorée.")
                continue
            if isinstance(message, dict):
                self._handle(message)

    def _handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "state":
            status, tracks = parse_state(message, self._names())
            if status != self._status:
                if status.message and status.message != self._status.message:
                    log.warning("Moteur REKA : %s", status.message)
                self._status = status
                self.connection_changed.emit(self._connected)
            if tracks != self._tracks:
                self._tracks = tracks
                self.tracks_changed.emit(list(tracks))
            self._set_connected(True)
            self._ready.set()
        elif kind == "chains":
            try:
                doc = clean_chains(message.get("chains"), self._count)
            except ChainsError as exc:
                log.error("Chaînes du moteur REKA ignorées : %s", exc)
                return
            with self._lock:
                self._doc = doc
            if self._keep_file:
                try:
                    save_chains_file(self._chains_file, doc)
                except OSError as exc:
                    log.error("Chaînes d'effets non enregistrées : %s", exc)
            self._chains_event.set()
        elif kind == "dirty":
            self._dirty_at = time.monotonic() + DIRTY_DELAY_S
        elif kind == "error":
            text = str(message.get("message") or "erreur du moteur d'effets")
            log.error("Moteur REKA : %s", text)
            self.command_failed.emit(text)
        elif kind == "log":
            log.info("Moteur REKA : %s", message.get("message", ""))

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            log.info("Moteur REKA %s.", "connecté" if connected else "déconnecté")
            if not connected:
                self._ready.clear()
            self.connection_changed.emit(connected)

    def _terminate_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                deadline = time.monotonic() + 2.0
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if process.poll() is None:
                    process.terminate()
        except Exception as exc:
            log.debug("Arrêt du moteur REKA : %s", exc)


def demo_launcher(engine_cls=None) -> Launcher:
    """Lanceur du faux hôte (démo, tests) : il tourne dans un thread de REKA."""
    from core.reka_engine_fake import FakeEngine

    def launch(port: int, token: str):
        engine = (engine_cls or FakeEngine)(port, token)
        engine.start()
        return engine
    return launch


if __name__ == "__main__":       # dépannage : liste des plugins trouvés
    logging.basicConfig(level=logging.INFO)
    for plugin in scan_plugins(sys.argv[1:]):
        print(f"{plugin.kind:5} {plugin.name:40} {plugin.add_name}")
