"""
Écoute au casque : égalisation du casque et son surround virtuel.

Le son part vers le casque par un bus de Voicemeeter (voicemeeter.headphone_bus,
A1 par défaut). reka-host.exe le traite juste avant le périphérique (audio
callback de Voicemeeter, mode « sorties ») :

  - **surround virtuel** : chaque canal (jusqu'au 7.1) devient une enceinte
    placée autour de la tête, plus une pièce virtuelle ; le casque reçoit le
    tout en stéréo ;
  - **égalisation du casque** : filtres paramétriques qui corrigent la courbe
    du casque. Les profils viennent de la base AutoEQ (des milliers de casques
    mesurés, github.com/jaakkopasanen/AutoEq), ou d'un fichier importé au
    format AutoEQ / Equalizer APO (« ParametricEQ.txt »).

Ça marche quel que soit le moteur d'effets choisi : avec le moteur REKA, son
hôte s'en charge ; sinon REKA lance reka-host.exe pour l'écoute seule.

Réglages dans config.json, section « listen » :
  eq.enabled, eq.name, eq.source, eq.preamp, eq.filters [{type, freq, gain, q}]
  surround.enabled, surround.head, surround.room, surround.amount
"""

from __future__ import annotations

import cmath
import logging
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core import config
from core.effects import HOST_REKA
from core.voicemeeter_ctrl import edition

log = logging.getLogger(__name__)

SECTION = "listen"
MAX_FILTERS = 32
MAX_EQ_FILE = 256 * 1024
BUS_CHANNELS = 8                     # canaux d'un bus dans l'audio callback de Voicemeeter

FILTER_TYPES = ("PK", "LS", "HS", "LP", "HP", "NO")
FILTER_LABELS = {"PK": "Cloche", "LS": "Étagère grave", "HS": "Étagère aiguë",
                 "LP": "Passe-bas", "HP": "Passe-haut", "NO": "Coupe-bande"}
# Noms des filtres dans les fichiers AutoEQ / Equalizer APO
_TYPE_ALIASES = {"PK": "PK", "PEQ": "PK", "MODAL": "PK",
                 "LS": "LS", "LSC": "LS", "LSQ": "LS", "LS6DB": "LS", "LS12DB": "LS",
                 "HS": "HS", "HSC": "HS", "HSQ": "HS", "HS6DB": "HS", "HS12DB": "HS",
                 "LP": "LP", "LPQ": "LP", "HP": "HP", "HPQ": "HP", "NO": "NO"}

HEADS = {"small": "Petite", "medium": "Moyenne", "large": "Grande"}
ROOMS = {"none": "Aucune (casque sec)", "living": "Salon", "cinema": "Cinéma"}
_HEAD_INDEX = {"small": 0, "medium": 1, "large": 2}
_ROOM_INDEX = {"none": 0, "living": 1, "cinema": 2}

AUTOEQ_RESULTS = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results"
AUTOEQ_PAGE = "https://github.com/jaakkopasanen/AutoEq"
INDEX_FILE = "INDEX.md"
INDEX_MAX_AGE_S = 7 * 24 * 3600
INDEX_MAX_BYTES = 16 * 1024 * 1024
FETCH_TIMEOUT_S = 20.0
# Sources de mesure, de la plus fiable à la moins fiable (les autres ensuite)
SOURCE_RANK = ("oratory1990", "crinacle", "Rtings", "Innerfidelity", "HypetheSonics",
               "Super Review")


class EqParseError(ValueError):
    """Fichier d'égalisation illisible (message présentable tel quel)."""


# --------------------------------------------------------------------------- #
# Filtres
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EqFilter:
    type: str = "PK"
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 0.707

    def to_json(self) -> dict:
        return {"type": self.type, "freq": round(self.freq, 2), "gain": round(self.gain, 2),
                "q": round(self.q, 3)}

    @classmethod
    def from_json(cls, value) -> EqFilter | None:
        if not isinstance(value, dict):
            return None
        kind = str(value.get("type", "PK")).upper()
        if kind not in FILTER_TYPES:
            return None
        try:
            freq, gain, q = (float(value.get("freq", 1000)), float(value.get("gain", 0)),
                             float(value.get("q", 0.707)))
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (freq, gain, q)):
            return None
        return cls(kind, _clamp(freq, 10.0, 24000.0), _clamp(gain, -30.0, 30.0),
                   _clamp(q, 0.05, 50.0))


@dataclass(frozen=True)
class HeadphoneEq:
    preamp: float
    filters: tuple[EqFilter, ...]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


_NUMBER = r"([-+]?\d+(?:[.,]\d+)?)"
_FILTER_RE = re.compile(
    r"^\s*filter\s*\d*\s*:\s*(on|off)\s+([a-z]+(?:\s*\d+\s*db)?)"
    rf"(?:\s+fc\s+{_NUMBER}\s*hz)?(?:\s+gain\s+{_NUMBER}\s*db)?(?:\s+q\s+{_NUMBER})?",
    re.IGNORECASE)
_PREAMP_RE = re.compile(rf"^\s*preamp\s*:\s*{_NUMBER}\s*db", re.IGNORECASE)


def _number(text: str | None) -> float | None:
    return None if text is None else float(text.replace(",", "."))


def parse_parametric_eq(text: str) -> HeadphoneEq:
    """Lit un fichier AutoEQ « ParametricEQ.txt » ou une config Equalizer APO :

        Preamp: -6.3 dB
        Filter 1: ON LSC Fc 105 Hz Gain 6.5 dB Q 0.70
        Filter 2: ON PK Fc 2000 Hz Gain -3.2 dB Q 1.41

    Sans préampli, il est calculé pour que la correction ne sature jamais."""
    preamp: float | None = None
    filters: list[EqFilter] = []
    for line in text.splitlines():
        match = _PREAMP_RE.match(line)
        if match:
            preamp = (preamp or 0.0) + float(_number(match.group(1)) or 0.0)
            continue
        match = _FILTER_RE.match(line)
        if not match:
            continue
        state, name, freq, gain, q = match.groups()
        if state.lower() != "on":
            continue
        kind = _TYPE_ALIASES.get(re.sub(r"\s+", "", name).upper())
        if kind is None or _number(freq) is None:
            continue
        if kind in ("PK", "LS", "HS") and _number(gain) is None:
            continue
        default_q = 0.707
        item = EqFilter.from_json({"type": kind, "freq": _number(freq),
                                   "gain": _number(gain) or 0.0,
                                   "q": _number(q) if _number(q) else default_q})
        if item is not None:
            filters.append(item)
    if not filters:
        raise EqParseError("Aucun filtre reconnu : choisis un fichier « ParametricEQ.txt » "
                           "d'AutoEQ ou une config Equalizer APO.")
    if len(filters) > MAX_FILTERS:
        raise EqParseError(f"Trop de filtres ({len(filters)}) : {MAX_FILTERS} au plus.")
    if preamp is None:
        peak = max(response_db(0.0, filters, log_frequencies(200)))
        preamp = -max(0.0, round(peak + 0.05, 1))
    return HeadphoneEq(_clamp(preamp, -30.0, 12.0), tuple(filters))


def format_parametric_eq(eq: HeadphoneEq) -> str:
    """Inverse de parse_parametric_eq (format AutoEQ)."""
    names = {"PK": "PK", "LS": "LSC", "HS": "HSC", "LP": "LPQ", "HP": "HPQ", "NO": "NO"}
    lines = [f"Preamp: {eq.preamp:.1f} dB"]
    for i, f in enumerate(eq.filters, 1):
        lines.append(f"Filter {i}: ON {names[f.type]} Fc {f.freq:g} Hz Gain {f.gain:.1f} dB "
                     f"Q {f.q:.2f}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Réponse en fréquence (courbe affichée) : mêmes filtres que reka-host
# --------------------------------------------------------------------------- #
def _biquad(f: EqFilter, rate: float) -> tuple[float, float, float, float, float]:
    freq = _clamp(f.freq, 10.0, 0.49 * rate)
    a = 10.0 ** (_clamp(f.gain, -30.0, 30.0) / 40.0)
    w0 = 2.0 * math.pi * freq / rate
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * _clamp(f.q, 0.05, 50.0))
    if f.type == "PK":
        b = (1 + alpha * a, -2 * cw, 1 - alpha * a)
        d = (1 + alpha / a, -2 * cw, 1 - alpha / a)
    elif f.type in ("LS", "HS"):
        s = 2 * math.sqrt(a) * alpha
        sign = 1 if f.type == "LS" else -1
        b = (a * ((a + 1) - sign * (a - 1) * cw + s), sign * 2 * a * ((a - 1) - sign * (a + 1) * cw),
             a * ((a + 1) - sign * (a - 1) * cw - s))
        d = ((a + 1) + sign * (a - 1) * cw + s, -sign * 2 * ((a - 1) + sign * (a + 1) * cw),
             (a + 1) + sign * (a - 1) * cw - s)
    elif f.type == "LP":
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
        d = (1 + alpha, -2 * cw, 1 - alpha)
    elif f.type == "HP":
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
        d = (1 + alpha, -2 * cw, 1 - alpha)
    else:  # NO
        b = (1.0, -2 * cw, 1.0)
        d = (1 + alpha, -2 * cw, 1 - alpha)
    return b[0] / d[0], b[1] / d[0], b[2] / d[0], d[1] / d[0], d[2] / d[0]


def log_frequencies(count: int = 160, low: float = 20.0, high: float = 20000.0) -> list[float]:
    return [low * (high / low) ** (i / (count - 1)) for i in range(count)]


def response_db(preamp: float, filters, freqs: list[float], rate: float = 48000.0) -> list[float]:
    """Gain (dB) de la correction à chaque fréquence, préampli compris."""
    coeffs = [_biquad(f, rate) for f in filters]
    result = []
    for freq in freqs:
        z1 = cmath.exp(-1j * 2 * math.pi * freq / rate)
        gain = 1.0
        for b0, b1, b2, a1, a2 in coeffs:
            gain *= abs((b0 + b1 * z1 + b2 * z1 * z1) / (1 + a1 * z1 + a2 * z1 * z1))
        result.append(preamp + 20 * math.log10(max(gain, 1e-12)))
    return result


# --------------------------------------------------------------------------- #
# Réglages
# --------------------------------------------------------------------------- #
def settings(cfg: dict) -> dict:
    """Section « listen » complétée et vérifiée (la config peut venir d'un import)."""
    section = cfg.get(SECTION) if isinstance(cfg.get(SECTION), dict) else {}
    eq = section.get("eq") if isinstance(section.get("eq"), dict) else {}
    sur = section.get("surround") if isinstance(section.get("surround"), dict) else {}
    filters = [f for f in (EqFilter.from_json(v) for v in (eq.get("filters") or [])[:MAX_FILTERS]
                           if isinstance(eq.get("filters"), list)) if f is not None]
    try:
        preamp = _clamp(float(eq.get("preamp", 0.0)), -30.0, 12.0)
    except (TypeError, ValueError):
        preamp = 0.0
    try:
        amount = _clamp(float(sur.get("amount", 1.0)), 0.0, 1.0)
    except (TypeError, ValueError):
        amount = 1.0
    return {
        "eq": {"enabled": bool(eq.get("enabled", False)) and bool(filters),
               "name": str(eq.get("name", "") or "")[:200],
               "source": str(eq.get("source", "") or "")[:200],
               "preamp": preamp, "filters": filters},
        "surround": {"enabled": bool(sur.get("enabled", False)),
                     "head": sur.get("head") if sur.get("head") in HEADS else "medium",
                     "room": sur.get("room") if sur.get("room") in ROOMS else "living",
                     "amount": amount},
    }


def is_active(cfg: dict) -> bool:
    current = settings(cfg)
    return current["eq"]["enabled"] or current["surround"]["enabled"]


def headphone_bus(cfg: dict) -> tuple[str, int]:
    """Bus du casque (« A1 ») et son premier canal dans les sorties de Voicemeeter."""
    ed = edition(cfg)
    name = str(cfg.get("voicemeeter", {}).get("headphone_bus", "A1"))
    if name not in ed.buses:
        name = "A1"
    return name, ed.buses.index(name) * BUS_CHANNELS


def host_message(cfg: dict) -> dict:
    """Réglages envoyés à reka-host.exe (commande « listen »)."""
    current = settings(cfg)
    eq, sur = current["eq"], current["surround"]
    _bus, channel = headphone_bus(cfg)
    return {"eq": eq["enabled"], "preamp": eq["preamp"],
            "bands": [f.to_json() for f in eq["filters"]],
            "surround": sur["enabled"], "head": _HEAD_INDEX[sur["head"]],
            "room": _ROOM_INDEX[sur["room"]], "amount": sur["amount"],
            "channel": channel, "channels": BUS_CHANNELS}


def set_profile(cfg: dict, name: str, source: str, eq: HeadphoneEq, enable: bool = True) -> None:
    section = cfg.setdefault(SECTION, {}).setdefault("eq", {})
    section.update({"name": name, "source": source, "preamp": eq.preamp,
                    "filters": [f.to_json() for f in eq.filters]})
    if enable:
        section["enabled"] = True


def clear_profile(cfg: dict) -> None:
    cfg.setdefault(SECTION, {})["eq"] = {"enabled": False, "name": "", "source": "",
                                         "preamp": 0.0, "filters": []}


def set_option(cfg: dict, part: str, key: str, value) -> None:
    cfg.setdefault(SECTION, {}).setdefault(part, {})[key] = value


def describe(cfg: dict) -> str:
    """Résumé d'une ligne (infobulles)."""
    current = settings(cfg)
    parts = []
    if current["eq"]["enabled"]:
        parts.append(f"EQ « {current['eq']['name'] or 'personnalisée'} »")
    if current["surround"]["enabled"]:
        sur = current["surround"]
        parts.append(f"surround virtuel ({ROOMS[sur['room']].split(' (')[0].lower()}, "
                     f"{round(sur['amount'] * 100)} %)")
    return " + ".join(parts) if parts else "désactivée"


# --------------------------------------------------------------------------- #
# Base AutoEQ
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AutoEqEntry:
    name: str           # « Sennheiser HD 600 »
    path: str           # chemin dans results/, encodé comme dans l'index
    source: str         # qui a mesuré : oratory1990, crinacle…
    rig: str = ""       # banc de mesure, s'il est précisé

    @property
    def label(self) -> str:
        return f"{self.name} — {self.source}" + (f" ({self.rig})" if self.rig else "")

    @property
    def kind(self) -> str:
        folder = urllib.parse.unquote(self.path).split("/")[-2] if "/" in self.path else ""
        if "in-ear" in folder:
            return "intra"
        if "earbud" in folder:
            return "écouteurs"
        return "casque"

    @property
    def url(self) -> str:
        folder = urllib.parse.unquote(self.path)
        last = folder.rsplit("/", 1)[-1]
        return (f"{AUTOEQ_RESULTS}/{urllib.parse.quote(folder)}/"
                f"{urllib.parse.quote(last + ' ParametricEQ.txt')}")


_INDEX_RE = re.compile(r"^- \[(?P<name>.+)\]\(\./(?P<path>.+)\) by (?P<source>.+?)"
                       r"(?: on (?P<rig>.+))?\s*$")


def parse_index(text: str) -> list[AutoEqEntry]:
    entries = []
    for line in text.splitlines():
        match = _INDEX_RE.match(line)
        if match and ".." not in match.group("path"):
            entries.append(AutoEqEntry(match.group("name").strip(), match.group("path").strip(),
                                       match.group("source").strip(),
                                       (match.group("rig") or "").strip()))
    return entries


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def search(entries: list[AutoEqEntry], query: str, limit: int = 60) -> list[AutoEqEntry]:
    """Casques dont le nom contient tous les mots cherchés, les plus probables en
    premier : nom exact, puis mots trouvés en début de mot (« hd 600 » trouve
    « Sennheiser HD 600 » avant « Denon AH-D600 »), puis meilleures sources."""
    words = _normalize(query).split()
    if not words:
        return []
    wanted = " ".join(words)
    compact = "".join(words)
    found = []
    for entry in entries:
        name = _normalize(entry.name)
        parts = name.split()
        squeezed = "".join(parts)
        starts, offset = set(), 0
        for part in parts:
            starts.add(offset)
            offset += len(part)
        at = squeezed.find(compact)
        if name == wanted:
            score = 0
        elif at >= 0 and at in starts:
            score = 1                    # « hd600 » au début d'un mot du nom
        elif all(any(p.startswith(w) for p in parts) for w in words):
            score = 2                    # chaque mot commence un mot du nom
        elif at >= 0 or all(w in name for w in words):
            score = 3                    # trouvé, mais au milieu d'un mot
        else:
            continue
        source = (SOURCE_RANK.index(entry.source) if entry.source in SOURCE_RANK
                  else len(SOURCE_RANK))
        found.append(((score, len(name), entry.name.lower(), source), entry))
    found.sort(key=lambda item: item[0])
    return [entry for _key, entry in found[:limit]]


def _fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"{config.APP_NAME}"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310
        data = response.read(limit + 1)
    if len(data) > limit:
        raise OSError("fichier trop volumineux")
    return data


def cache_dir() -> Path:
    path = config.app_data_dir() / "autoeq"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_index(refresh: bool = False, fetch=_fetch) -> list[AutoEqEntry]:
    """Index AutoEQ : copie locale (moins d'une semaine), sinon téléchargé ; hors
    ligne, l'ancienne copie sert encore. Lève OSError si rien n'est disponible."""
    path = cache_dir() / INDEX_FILE
    fresh = path.is_file() and time.time() - path.stat().st_mtime < INDEX_MAX_AGE_S
    if fresh and not refresh:
        entries = parse_index(path.read_text(encoding="utf-8", errors="replace"))
        if entries:
            return entries
    try:
        data = fetch(f"{AUTOEQ_RESULTS}/{INDEX_FILE}", INDEX_MAX_BYTES)
        entries = parse_index(data.decode("utf-8", errors="replace"))
        if not entries:
            raise OSError("index AutoEQ vide")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return entries
    except (OSError, ValueError) as exc:
        if path.is_file():
            log.warning("Index AutoEQ non mis à jour (%s) : copie locale utilisée.", exc)
            entries = parse_index(path.read_text(encoding="utf-8", errors="replace"))
            if entries:
                return entries
        raise OSError(f"base AutoEQ injoignable ({exc})") from exc


def load_profile(entry: AutoEqEntry, fetch=_fetch) -> HeadphoneEq:
    try:
        data = fetch(entry.url, MAX_EQ_FILE)
    except (OSError, ValueError) as exc:
        raise OSError(f"profil de « {entry.name} » injoignable ({exc})") from exc
    return parse_parametric_eq(data.decode("utf-8", errors="replace"))


def load_file(path: Path) -> HeadphoneEq:
    try:
        if path.stat().st_size > MAX_EQ_FILE:
            raise EqParseError("Fichier trop volumineux pour une égalisation.")
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise EqParseError(f"Lecture impossible : {exc}") from exc
    return parse_parametric_eq(text)


class AutoEqLibrary(QObject):
    """Recherche dans la base AutoEQ, en arrière-plan (réseau)."""

    index_ready = Signal(object)          # list[AutoEqEntry]
    profile_ready = Signal(object, object)  # AutoEqEntry, HeadphoneEq
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, fetch=_fetch) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self._entries: list[AutoEqEntry] | None = None
        self._busy = threading.Lock()

    @property
    def entries(self) -> list[AutoEqEntry] | None:
        return self._entries

    def request_index(self) -> None:
        if self._entries is not None:
            self.index_ready.emit(self._entries)
            return
        self._spawn(self._load_index)

    def request_profile(self, entry: AutoEqEntry) -> None:
        self._spawn(lambda: self._load_profile(entry))

    def _spawn(self, work) -> None:
        threading.Thread(target=work, name="autoeq", daemon=True).start()

    def _load_index(self) -> None:
        with self._busy:
            if self._entries is None:
                try:
                    self._entries = load_index(fetch=self._fetch)
                    log.info("Base AutoEQ : %d profils.", len(self._entries))
                except OSError as exc:
                    log.warning("Base AutoEQ : %s", exc)
                    self.failed.emit(f"Recherche impossible : {exc}. Vérifie ta connexion, ou "
                                     "importe un fichier « ParametricEQ.txt ».")
                    return
        self.index_ready.emit(self._entries)

    def _load_profile(self, entry: AutoEqEntry) -> None:
        try:
            eq = load_profile(entry, fetch=self._fetch)
        except (OSError, EqParseError) as exc:
            log.warning("Profil AutoEQ : %s", exc)
            self.failed.emit(f"Profil non chargé : {exc}")
            return
        self.profile_ready.emit(entry, eq)


# --------------------------------------------------------------------------- #
# Pilotage de reka-host.exe
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ListenStatus:
    active: bool          # au moins une des deux options est allumée
    ok: bool              # le son passe bien par le traitement
    text: str


class ListenController(QObject):
    """Envoie les réglages d'écoute au moteur REKA s'il est le moteur d'effets,
    sinon à un reka-host.exe lancé pour l'écoute seule (et arrêté quand plus
    rien n'est allumé)."""

    status_changed = Signal(object)       # ListenStatus

    def __init__(self, cfg: dict, effects_host=None, launcher=None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self._launcher = launcher
        self._shared = effects_host if (getattr(effects_host, "kind", None) == HOST_REKA
                                        and hasattr(effects_host, "set_listen")) else None
        self._own = None                  # RekaHostController pour l'écoute seule
        self._started = False
        self._closed = False
        if self._shared is not None:
            self._shared.connection_changed.connect(self._emit_status)

    @property
    def host(self):
        return self._shared or self._own

    def start(self) -> None:
        self._started = True
        self.apply()

    def apply(self) -> None:
        """Réglages changés (ou démarrage) : transmis au moteur."""
        if not self._started or self._closed:
            return
        message = host_message(self._cfg)
        if self._shared is not None:
            self._shared.set_listen(message)
        elif message["eq"] or message["surround"]:
            if self._own is None:
                from core.reka_host import RekaHostController
                self._own = RekaHostController(self._cfg, launcher=self._launcher,
                                               listen_only=True)
                self._own.connection_changed.connect(self._emit_status)
                self._own.command_failed.connect(lambda text: log.warning("Écoute : %s", text))
                self._own.set_listen(message)
                self._own.start()
                log.info("Écoute au casque : moteur lancé (%s).", describe(self._cfg))
            else:
                self._own.set_listen(message)
        elif self._own is not None:
            self._stop_own()
        self._emit_status()

    def close(self) -> None:
        self._closed = True
        self._stop_own()

    def _stop_own(self) -> None:
        own, self._own = self._own, None
        if own is not None:
            try:
                own.connection_changed.disconnect(self._emit_status)
            except (RuntimeError, TypeError):
                pass
            own.close(save_project=False)
            log.info("Écoute au casque : moteur arrêté.")

    def status(self) -> ListenStatus:
        current = settings(self._cfg)
        active = current["eq"]["enabled"] or current["surround"]["enabled"]
        bus, _channel = headphone_bus(self._cfg)
        if not active:
            return ListenStatus(False, True, "Désactivée.")
        host = self.host
        if host is None:
            return ListenStatus(True, False, "En attente du démarrage de REKA.")
        status = host.engine_status
        if getattr(host, "gave_up", False):
            return ListenStatus(True, False, "Le moteur d'écoute s'est arrêté plusieurs fois : "
                                "relance REKA.")
        if not host.is_connected:
            return ListenStatus(True, False, status.message or "Démarrage du moteur d'écoute…")
        if not status.running:
            return ListenStatus(True, False,
                                status.message or "Voicemeeter n'envoie pas encore le son.")
        return ListenStatus(True, True, f"Active sur le bus {bus} : {describe(self._cfg)}.")

    def _emit_status(self, *_args) -> None:
        self.status_changed.emit(self.status())
