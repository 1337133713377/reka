"""
Discord : Rich Presence (« Joue à REKA » sur le profil) et micro synchronisé
(le Muet d'une strip de REKA et le micro barré de Discord se suivent).

REKA parle au client Discord installé sur le PC, par son canal local
(« RPC ») : le tube nommé \\\\.\\pipe\\discord-ipc-0…9 sous Windows, un socket
Unix ailleurs. Rien ne passe par un serveur de REKA.

- Rich Presence : il suffit de l'identifiant d'une application Discord
  (Application ID). Aucune autorisation n'est demandée.
- Micro synchronisé : Discord réserve la lecture et le réglage du micro aux
  applications qu'il a approuvées, et au propriétaire d'une application (plus
  ses testeurs). Chacun crée donc sa propre application sur
  discord.com/developers, y ajoute la redirection http://localhost et donne à
  REKA son Application ID et son Client Secret. Discord demande une fois
  d'autoriser REKA ; le jeton obtenu est gardé dans la config.

Trois moteurs, même interface (comme la musique en cours) :
- DiscordClient  : le vrai ; les lectures bloquent, il tourne dans un thread ;
- DemoDiscord    : Discord simulé (--demo, tests) ;
- DiscordBackend : la base, qui ne fait rien (Discord désactivé).

Les résultats remontent par des signaux, livrés dans le thread de l'interface.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from core import config

log = logging.getLogger(__name__)

# Application Discord « REKA » utilisée quand l'utilisateur n'en indique pas :
# son nom est celui affiché après « Joue à ». À renseigner une fois l'application
# créée sur https://discord.com/developers/applications (vide : Rich Presence
# inactive tant qu'un Application ID n'est pas saisi dans les réglages).
REKA_CLIENT_ID = ""
DEVELOPER_PORTAL = "https://discord.com/developers/applications"
DOWNLOAD_URL = "https://github.com/1337133713377/reka"
REDIRECT_URI = "http://localhost"        # à déclarer dans l'application Discord (OAuth2)
TOKEN_URL = "https://discord.com/api/oauth2/token"
SCOPES = ("rpc", "rpc.voice.read", "rpc.voice.write")
LARGE_IMAGE = "reka"                     # image « reka » de l'application (Rich Presence → Art Assets)

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = range(5)
HEADER = struct.Struct("<II")            # code de l'opération, taille du JSON
MAX_FRAME = 1024 * 1024
PIPE_COUNT = 10                          # discord-ipc-0 … discord-ipc-9
PIPE_POLL_S = 0.03                       # tube Windows : pas d'attente interruptible, on scrute
POLL_S = 0.1                             # tour de boucle : lecture, commandes en attente
RETRY_S = 5.0                            # Discord pas lancé : nouvel essai
ERROR_RETRY_S = 30.0                     # Discord a refusé la connexion (identifiant invalide…)
HANDSHAKE_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 5.0
AUTHORIZE_TIMEOUT_S = 300.0              # l'utilisateur doit cliquer dans Discord
HTTP_TIMEOUT_S = 10.0
STOP_JOIN_S = 2.0
TEXT_MIN, TEXT_MAX = 2, 128              # longueur des lignes d'une activité

# Sens de la synchronisation du micro
BOTH, TO_DISCORD, FROM_DISCORD = "both", "to_discord", "from_discord"
DIRECTIONS = {
    BOTH: "Dans les deux sens",
    TO_DISCORD: "REKA → Discord seulement",
    FROM_DISCORD: "Discord → REKA seulement",
}
ECHO_GUARD_S = 0.6                       # valeur périmée de Voicemeeter ignorée après un changement
TOKEN_KEYS = ("access_token", "refresh_token", "token_expires")


class DiscordError(Exception):
    """Erreur présentable telle quelle à l'utilisateur."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class TokenRejected(DiscordError):
    """Discord a refusé le code ou le jeton (à la différence d'une panne de réseau)."""


class AuthNeeded(DiscordError):
    """Le micro synchronisé attend un geste de l'utilisateur (réglage, autorisation) :
    inutile de réessayer tout seul."""


class _Interrupted(Exception):
    """Arrêt demandé, ou réglages changés : la connexion est refaite."""


# --------------------------------------------------------------------------- #
# Réglages, activité affichée, état
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DiscordSettings:
    client_id: str = ""
    client_secret: str = ""
    presence: bool = True
    mute_sync: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "DiscordSettings":
        section = cfg.get("discord") or {}
        return cls(client_id=str(section.get("client_id") or "").strip() or REKA_CLIENT_ID,
                   client_secret=str(section.get("client_secret") or "").strip(),
                   presence=bool(section.get("rich_presence", True)),
                   mute_sync=bool(section.get("mute_sync", False)))

    @property
    def enabled(self) -> bool:
        return self.presence or self.mute_sync

    @property
    def valid_id(self) -> bool:
        return self.client_id.isdigit()


def tokens_from_config(cfg: dict) -> dict:
    section = cfg.get("discord") or {}
    return {key: section[key] for key in TOKEN_KEYS if section.get(key)}


def mute_direction(cfg: dict) -> str:
    value = str((cfg.get("discord") or {}).get("mute_direction") or BOTH)
    return value if value in DIRECTIONS else BOTH


def _line(text: str) -> str:
    """Ligne d'activité acceptée par Discord (2 à 128 caractères), ou ""."""
    text = " ".join(str(text).split())[:TEXT_MAX]
    return text if len(text) >= TEXT_MIN else ""


@dataclass(frozen=True)
class Presence:
    """Ce que le profil Discord affiche sous « Joue à REKA »."""

    details: str = "Console de mixage audio"
    state: str = ""
    start: int = 0                # début de la session (secondes Unix) : « depuis 12:34 »

    def activity(self) -> dict:
        activity: dict = {
            "assets": {"large_image": LARGE_IMAGE,
                       "large_text": f"{config.APP_NAME} {config.APP_VERSION}"},
            "buttons": [{"label": f"Télécharger {config.APP_NAME}", "url": DOWNLOAD_URL}],
            "instance": False,
        }
        for key, value in (("details", _line(self.details)), ("state", _line(self.state))):
            if value:
                activity[key] = value
        if self.start > 0:
            activity["timestamps"] = {"start": int(self.start)}
        return activity


@dataclass(frozen=True)
class DiscordStatus:
    connected: bool = False       # Discord trouvé, connexion faite
    voice: bool = False           # micro synchronisé (REKA autorisée)
    message: str = "Discord désactivé."
    problem: bool = False         # à signaler (réglage manquant, refus…)
    user: str = ""


# --------------------------------------------------------------------------- #
# Micro synchronisé : qui suit qui
# --------------------------------------------------------------------------- #
class MuteLink:
    """Décide, à chaque changement, s'il faut le reporter de l'autre côté.

    `reka_changed` / `discord_changed` renvoient None, ou (cible, muet) avec
    cible « discord » ou « reka ». À la connexion, REKA impose son état (sauf
    dans le sens Discord → REKA seulement) : c'est la console qui fait foi.
    """

    def __init__(self, direction: str = BOTH) -> None:
        self.direction = direction if direction in DIRECTIONS else BOTH
        self.reka: bool | None = None       # Muet de la strip (None : pas encore lu)
        self.discord: bool | None = None    # micro de Discord (None : pas connecté)
        self._expected: tuple[bool, float] | None = None   # valeur appliquée à REKA, échéance

    def reset(self, direction: str | None = None) -> None:
        if direction is not None:
            self.direction = direction if direction in DIRECTIONS else BOTH
        self.reka = self.discord = self._expected = None

    def discord_lost(self) -> None:
        self.discord = None

    def reka_changed(self, mute: bool, now: float | None = None) -> tuple[str, bool] | None:
        now = time.monotonic() if now is None else now
        if self._expected is not None:
            value, deadline = self._expected
            if mute != value and now < deadline:
                return None       # Voicemeeter n'a pas encore appliqué le Muet demandé
            self._expected = None
        if mute == self.reka:
            return None
        first, self.reka = self.reka is None, mute
        if self.discord is None or self.discord == mute:
            return None
        if first:
            return self._align(now)
        if self.direction == FROM_DISCORD:
            return None
        self.discord = mute
        return "discord", mute

    def discord_changed(self, mute: bool, now: float | None = None) -> tuple[str, bool] | None:
        now = time.monotonic() if now is None else now
        if mute == self.discord:
            return None
        first, self.discord = self.discord is None, mute
        if self.reka is None or self.reka == mute:
            return None
        if first:
            return self._align(now)
        if self.direction == TO_DISCORD:
            return None
        return self._to_reka(mute, now)

    def _align(self, now: float) -> tuple[str, bool]:
        """Les deux états viennent d'être connus et diffèrent."""
        if self.direction == FROM_DISCORD:
            return self._to_reka(bool(self.discord), now)
        self.discord = self.reka
        return "discord", bool(self.reka)

    def _to_reka(self, mute: bool, now: float) -> tuple[str, bool]:
        self.reka = mute
        self._expected = (mute, now + ECHO_GUARD_S)
        return "reka", mute


# --------------------------------------------------------------------------- #
# Transport : tube nommé (Windows) ou socket Unix
# --------------------------------------------------------------------------- #
class PipeTransport:
    """Tube nommé de Discord. Une lecture y bloquerait sans fin : on regarde
    d'abord ce qui est arrivé (PeekNamedPipe)."""

    def __init__(self, path: str) -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        self._file = open(path, "r+b", buffering=0)
        self._handle = wintypes.HANDLE(msvcrt.get_osfhandle(self._file.fileno()))
        self._available = wintypes.DWORD(0)
        self._byref = ctypes.byref
        self._peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
        self._peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._peek.restype = wintypes.BOOL

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[self._file.write(view) or 0:]

    def read(self, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        while True:
            if not self._peek(self._handle, None, 0, None, self._byref(self._available), None):
                raise ConnectionError("Discord a fermé la connexion")
            if self._available.value:
                return self._file.read(self._available.value) or b""
            if time.monotonic() >= deadline:
                return b""
            time.sleep(PIPE_POLL_S)

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass


class SocketTransport:
    """Socket Unix de Discord (Linux, macOS : développement)."""

    def __init__(self, path: str) -> None:
        import socket

        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._socket.connect(path)
        except OSError:
            self._socket.close()
            raise

    def write(self, data: bytes) -> None:
        self._socket.settimeout(REQUEST_TIMEOUT_S)
        self._socket.sendall(data)

    def read(self, timeout_s: float) -> bytes:
        self._socket.settimeout(max(0.001, timeout_s))
        try:
            data = self._socket.recv(65536)
        except TimeoutError:
            return b""
        if not data:
            raise ConnectionError("Discord a fermé la connexion")
        return data

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass


def ipc_paths() -> list[str]:
    """Emplacements possibles du canal local de Discord."""
    if sys.platform == "win32":
        return [rf"\\.\pipe\discord-ipc-{n}" for n in range(PIPE_COUNT)]
    roots = [os.environ.get(name) for name in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")]
    roots = list(dict.fromkeys([root for root in roots if root] + ["/tmp"]))
    folders = [os.path.join(root, sub) for root in roots
               for sub in ("", "app/com.discordapp.Discord", "snap.discord")]
    return [os.path.join(folder, f"discord-ipc-{n}") for folder in folders for n in range(PIPE_COUNT)]


def open_transport():
    """Connexion au premier client Discord qui répond, ou None s'il ne tourne pas."""
    factory = PipeTransport if sys.platform == "win32" else SocketTransport
    for path in ipc_paths():
        try:
            return factory(path)
        except (OSError, ImportError, AttributeError):
            continue
    return None


# --------------------------------------------------------------------------- #
# Protocole RPC
# --------------------------------------------------------------------------- #
class RpcSession:
    """Une connexion à Discord : poignée de main, commandes et événements."""

    def __init__(self, transport, client_id: str,
                 abort: Callable[[], bool] = lambda: False) -> None:
        self._transport = transport
        self._client_id = client_id
        self._abort = abort
        self._buffer = bytearray()
        self.events: list[dict] = []      # trames reçues en attendant une réponse

    def _send(self, opcode: int, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._transport.write(HEADER.pack(opcode, len(data)) + data)

    def poll(self, timeout_s: float) -> list[dict]:
        """Trames de données arrivées. Répond aux pings ; une fermeture par
        Discord (identifiant inconnu…) lève DiscordError."""
        self._buffer += self._transport.read(timeout_s)
        frames = []
        while len(self._buffer) >= HEADER.size:
            opcode, length = HEADER.unpack_from(self._buffer)
            if length > MAX_FRAME:
                raise DiscordError("Réponse de Discord illisible.")
            if len(self._buffer) < HEADER.size + length:
                break
            body = bytes(self._buffer[HEADER.size:HEADER.size + length])
            del self._buffer[:HEADER.size + length]
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                payload = {}
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
            elif opcode == OP_CLOSE:
                raise DiscordError(str(payload.get("message") or "connexion fermée par Discord"),
                                   _int(payload.get("code")))
            elif opcode == OP_FRAME:
                frames.append(payload)
        return frames

    def handshake(self, timeout_s: float = HANDSHAKE_TIMEOUT_S) -> dict:
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self._client_id})
        return _data(self._wait(lambda frame: frame.get("evt") == "READY", timeout_s))

    def request(self, cmd: str, args: dict | None = None, evt: str | None = None,
                timeout_s: float = REQUEST_TIMEOUT_S) -> dict:
        nonce = uuid.uuid4().hex
        payload: dict = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            payload["evt"] = evt
        self._send(OP_FRAME, payload)
        frame = self._wait(lambda frame: frame.get("nonce") == nonce, timeout_s)
        data = _data(frame)
        if frame.get("evt") == "ERROR":
            raise DiscordError(str(data.get("message") or "erreur inconnue"), _int(data.get("code")))
        return data

    def _wait(self, match: Callable[[dict], bool], timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            if self._abort():
                raise _Interrupted()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DiscordError("Discord ne répond pas.")
            found = None
            for frame in self.poll(min(POLL_S, remaining)):
                if found is None and match(frame):
                    found = frame
                else:
                    self.events.append(frame)
            if found is not None:
                return found


def _data(frame: dict) -> dict:
    data = frame.get("data")
    return data if isinstance(data, dict) else {}


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def exchange_token(params: dict[str, str]) -> dict:
    """Échange un code d'autorisation (ou un jeton de renouvellement) contre un
    jeton d'accès, auprès de Discord."""
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(params).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                 "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"})
    try:
        with urllib.request.build_opener().open(request, timeout=HTTP_TIMEOUT_S) as response:
            data = json.loads(response.read(MAX_FRAME).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(MAX_FRAME).decode("utf-8"))
        except (OSError, ValueError):
            detail = {}
        error = str(detail.get("error") or exc.code) if isinstance(detail, dict) else str(exc.code)
        hints = {"invalid_client": "Client Secret ou Application ID incorrect",
                 "invalid_grant": f"ajoute la redirection {REDIRECT_URI} à ton application "
                                  "Discord (OAuth2), puis autorise de nouveau"}
        raise TokenRejected(f"Discord a refusé le jeton : {hints.get(error, error)}.") from exc
    except (OSError, ValueError) as exc:
        raise DiscordError(f"Discord est injoignable ({exc}).") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise TokenRejected("Discord n'a pas renvoyé de jeton.")
    return data


# --------------------------------------------------------------------------- #
# Moteurs
# --------------------------------------------------------------------------- #
class DiscordBackend(QObject):
    """Interface commune, et moteur « Discord désactivé » (ne fait rien)."""

    statusChanged = Signal(object)    # DiscordStatus
    muteChanged = Signal(bool)        # micro de Discord (à la connexion, puis à chaque changement)
    tokensChanged = Signal(object)    # dict à ranger dans la config (vide : jetons oubliés)
    available = False

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._status = DiscordStatus()

    @property
    def status(self) -> DiscordStatus:
        return self._status

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def configure(self, settings: DiscordSettings) -> None:
        pass

    def set_presence(self, presence: Presence | None) -> None:
        pass

    def set_mute(self, mute: bool) -> None:
        pass

    def authorize(self) -> None:
        """Redemande l'autorisation à Discord (après un refus)."""

    def _set_status(self, status: DiscordStatus) -> None:
        if status != self._status:
            self._status = status
            self.statusChanged.emit(status)


class DemoDiscord(DiscordBackend):
    """Discord simulé : toujours connecté, micro qu'on bascule avec `toggle`."""

    available = True

    def __init__(self, settings: DiscordSettings | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings or DiscordSettings()
        self._started = False
        self.mute = False
        self.presence: Presence | None = None
        self.sent: list[bool] = []        # Muets demandés par REKA

    def start(self) -> None:
        self._started = True
        self._refresh()

    def stop(self) -> None:
        self._started = False

    def configure(self, settings: DiscordSettings) -> None:
        self._settings = settings
        if self._started:
            self._refresh()

    def set_presence(self, presence: Presence | None) -> None:
        self.presence = presence

    def set_mute(self, mute: bool) -> None:
        if self._status.voice and mute != self.mute:
            self.mute = mute
            self.sent.append(mute)

    def toggle(self, mute: bool) -> None:
        """Comme un clic sur le micro dans Discord."""
        self.mute = mute
        if self._status.voice:
            self.muteChanged.emit(mute)

    def _refresh(self) -> None:
        settings = self._settings
        if not settings.enabled:
            self._set_status(DiscordStatus())
            return
        was_voice = self._status.voice
        self._set_status(DiscordStatus(True, settings.mute_sync,
                                       "Discord simulé (démo)" + (" — micro synchronisé."
                                                                  if settings.mute_sync else "."),
                                       user="Démo"))
        if settings.mute_sync and not was_voice:
            self.muteChanged.emit(self.mute)


class DiscordClient(DiscordBackend):
    available = True

    def __init__(self, settings: DiscordSettings | None = None, tokens: dict | None = None,
                 connect: Callable[[], object] = open_transport,
                 exchange: Callable[[dict], dict] = exchange_token,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._connect = connect
        self._exchange = exchange
        self._lock = threading.Lock()
        self._settings = settings or DiscordSettings()
        self._generation = 0              # augmente quand la connexion doit être refaite
        self._presence: Presence | None = None
        self._mute_wanted: bool | None = None
        self._tokens = dict(tokens or {})
        self._declined = False            # autorisation refusée : on ne redemande pas tout seul
        self._mute_known: bool | None = None   # (thread) micro de Discord
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------ thread de l'interface
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="discord-rpc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_JOIN_S)
            self._thread = None

    def configure(self, settings: DiscordSettings) -> None:
        forgotten = False
        with self._lock:
            old, self._settings = self._settings, settings
            if (old.client_id, old.client_secret) != (settings.client_id, settings.client_secret):
                self._declined = False
                if old.client_id != settings.client_id and self._tokens:
                    self._tokens, forgotten = {}, True    # jeton d'une autre application
            if (old.client_id, old.client_secret, old.mute_sync, old.enabled) != \
                    (settings.client_id, settings.client_secret, settings.mute_sync, settings.enabled):
                self._generation += 1
        if forgotten:
            self.tokensChanged.emit({})
        self._wake.set()

    def set_presence(self, presence: Presence | None) -> None:
        with self._lock:
            self._presence = presence

    def set_mute(self, mute: bool) -> None:
        with self._lock:
            self._mute_wanted = bool(mute)

    def authorize(self) -> None:
        with self._lock:
            self._declined = False
            self._generation += 1
        self._wake.set()

    # ---------------------------------------------------------------- thread
    def _snapshot(self) -> tuple[DiscordSettings, int]:
        with self._lock:
            return self._settings, self._generation

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            settings, generation = self._snapshot()
            if not settings.enabled:
                self._set_status(DiscordStatus())
                self._wake.wait()
                continue
            if not settings.valid_id:
                self._set_status(DiscordStatus(
                    message="Indique l'Application ID de ton application Discord (des chiffres)."
                    if settings.client_id or settings.mute_sync else
                    "Rich Presence en attente : aucune application Discord indiquée.",
                    problem=bool(settings.client_id or settings.mute_sync)))
                self._wake.wait()
                continue
            transport, delay = None, RETRY_S
            try:
                transport = self._connect()
                if transport is None:
                    self._set_status(DiscordStatus(
                        message="Discord n'est pas lancé : REKA réessaie toute seule."))
                else:
                    session = RpcSession(transport, settings.client_id,
                                         lambda: self._stale(generation))
                    self._serve(session, session.handshake(), generation)
            except _Interrupted:
                delay = 0.0
                if not self._stop.is_set():
                    self._set_status(DiscordStatus(message="Connexion à Discord…"))
            except DiscordError as exc:
                log.warning("Discord a refusé la connexion : %s", exc)
                self._set_status(DiscordStatus(message=f"Discord a refusé la connexion : {exc}",
                                               problem=True))
                delay = ERROR_RETRY_S
            except OSError as exc:
                log.info("Connexion à Discord perdue : %s", exc)
                self._set_status(DiscordStatus(message="Discord s'est fermé : REKA réessaie toute seule."))
            except Exception:
                log.exception("Erreur inattendue du lien Discord")
                self._set_status(DiscordStatus(message="Erreur du lien Discord (voir le journal).",
                                               problem=True))
                delay = ERROR_RETRY_S
            finally:
                self._mute_known = None
                if transport is not None:
                    transport.close()
            if delay and not self._stop.is_set():
                self._wake.wait(delay)

    def _stale(self, generation: int) -> bool:
        return self._stop.is_set() or self._snapshot()[1] != generation

    def _serve(self, session: RpcSession, ready: dict, generation: int) -> None:
        settings = self._snapshot()[0]
        user = _user_name(ready.get("user"))
        log.info("Connecté à Discord%s.", f" ({user})" if user else "")
        sent: Presence | None = None
        sent = self._send_presence(session, sent)
        voice, message, problem = False, "Connecté à Discord.", False
        retry_at = None                   # panne passagère (réseau) : nouvel essai plus tard
        if settings.mute_sync:
            try:
                account = self._authenticate(session, settings)
                session.request("SUBSCRIBE", {}, evt="VOICE_SETTINGS_UPDATE")
                current = session.request("GET_VOICE_SETTINGS")
                user = _user_name(account.get("user")) or user
                voice, message = True, "Connecté à Discord — micro synchronisé."
                with self._lock:
                    self._mute_wanted = None      # demandes d'avant la connexion : périmées
                self._mute_known = bool(current.get("mute"))
                self.muteChanged.emit(self._mute_known)
            except DiscordError as exc:
                log.warning("Micro synchronisé indisponible : %s", exc)
                message, problem = f"Micro non synchronisé : {exc}", True
                if not isinstance(exc, AuthNeeded):
                    retry_at = time.monotonic() + ERROR_RETRY_S
        self._set_status(DiscordStatus(True, voice, message, problem, user))

        while True:
            if self._stale(generation) or (retry_at is not None and time.monotonic() >= retry_at):
                raise _Interrupted()
            sent = self._send_presence(session, sent)
            if voice:
                self._send_mute(session)
            frames, session.events = session.events + session.poll(POLL_S), []
            for frame in frames:
                if frame.get("evt") == "VOICE_SETTINGS_UPDATE" and voice:
                    data = _data(frame)
                    if "mute" in data and bool(data["mute"]) != self._mute_known:
                        self._mute_known = bool(data["mute"])
                        self.muteChanged.emit(self._mute_known)

    def _send_presence(self, session: RpcSession, sent: Presence | None) -> Presence | None:
        with self._lock:
            wanted = self._presence if self._settings.presence else None
        if wanted == sent:
            return sent
        args: dict = {"pid": os.getpid()}
        if wanted is not None:
            args["activity"] = wanted.activity()
        try:
            session.request("SET_ACTIVITY", args)
        except DiscordError as exc:       # limite de débit… : la connexion reste bonne
            log.warning("Activité Discord non mise à jour : %s", exc)
        return wanted

    def _send_mute(self, session: RpcSession) -> None:
        with self._lock:
            wanted, self._mute_wanted = self._mute_wanted, None
        if wanted is None or wanted == self._mute_known:
            return
        try:
            session.request("SET_VOICE_SETTINGS", {"mute": wanted})
            self._mute_known = wanted
        except DiscordError as exc:
            log.warning("Micro de Discord non modifié : %s", exc)

    def _authenticate(self, session: RpcSession, settings: DiscordSettings) -> dict:
        if not settings.client_secret:
            raise AuthNeeded("indique le Client Secret de ton application Discord.")
        base = {"client_id": settings.client_id, "client_secret": settings.client_secret}
        with self._lock:
            tokens, declined = dict(self._tokens), self._declined
        if tokens.get("access_token"):
            try:
                return session.request("AUTHENTICATE", {"access_token": tokens["access_token"]})
            except DiscordError as exc:
                log.info("Jeton Discord refusé (%s) : renouvellement.", exc)
        if tokens.get("refresh_token"):
            try:
                access = self._store_tokens(self._exchange(
                    {**base, "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}))
                return session.request("AUTHENTICATE", {"access_token": access})
            except TokenRejected as exc:
                log.info("Renouvellement refusé (%s) : nouvelle autorisation.", exc)
                self._store_tokens({})
        if declined:
            raise AuthNeeded("autorisation nécessaire — clique sur « Autoriser dans Discord ».")
        self._set_status(DiscordStatus(True, False, "Autorise REKA dans la fenêtre que Discord "
                                                    "vient d'ouvrir…"))
        try:
            answer = session.request("AUTHORIZE", {"client_id": settings.client_id,
                                                   "scopes": list(SCOPES)},
                                     timeout_s=AUTHORIZE_TIMEOUT_S)
            access = self._store_tokens(self._exchange(
                {**base, "grant_type": "authorization_code", "code": str(answer.get("code") or ""),
                 "redirect_uri": REDIRECT_URI}))
            return session.request("AUTHENTICATE", {"access_token": access})
        except DiscordError as exc:
            with self._lock:
                self._declined = True
            detail = str(exc).rstrip(". ")
            raise AuthNeeded(f"{detail[:1].lower()}{detail[1:]}. Corrige si besoin, puis clique "
                             "sur « Autoriser dans Discord ».") from exc

    def _store_tokens(self, answer: dict) -> str:
        tokens = {}
        if answer.get("access_token"):
            tokens = {"access_token": str(answer["access_token"]),
                      "refresh_token": str(answer.get("refresh_token") or ""),
                      "token_expires": int(time.time()) + _int(answer.get("expires_in"))}
        with self._lock:
            self._tokens = tokens
        self.tokensChanged.emit(dict(tokens))
        return tokens.get("access_token", "")


def _user_name(user) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("global_name") or user.get("username") or "")


def create_discord_backend(cfg: dict) -> DiscordBackend:
    """Le lien réel avec le client Discord du PC (jamais d'exception)."""
    return DiscordClient(DiscordSettings.from_config(cfg), tokens_from_config(cfg))
