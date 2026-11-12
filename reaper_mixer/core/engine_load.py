"""
Charge du moteur d'effets : CPU, GPU et mémoire du processus qui fait tourner
les plugins (REAPER, FL Studio, ou le moteur audio de Windows pour Equalizer
APO), processus enfants compris (plugins « pontés » dans un autre processus).

REAPER ne donne pas la charge plugin par plugin à ses scripts : on mesure donc
le moteur entier. Pour un plugin précis, measure() / cpu_seconds() permettent
de comparer la charge avec et sans lui (voir MainWindow.measure_effect).

- CPU et mémoire : psutil.
- GPU : compteurs de performance Windows « GPU Engine » (ceux du Gestionnaire
  des tâches), lus par PDH ; absent hors Windows ou sans compteur → None.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import psutil
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

SAMPLE_INTERVAL_S = 2.0
CPU_WARN, CPU_HIGH = 40.0, 70.0      # % de la machine : ambre, rouge
GPU_WARN, GPU_HIGH = 60.0, 85.0
APO_PROCESS = "audiodg.exe"          # moteur audio de Windows (Equalizer APO y tourne)


@dataclass(frozen=True)
class EngineLoad:
    cpu: float                # % de la machine (comme le Gestionnaire des tâches)
    cpu_core: float           # % d'un seul cœur (peut dépasser 100)
    gpu: float | None         # % du GPU, None si Windows ne le donne pas
    memory_mb: float
    processes: int = 1

    def describe(self, engine: str) -> str:
        lines = [f"Charge de {engine} et de ses plugins",
                 f"CPU : {self.cpu:.1f} % de la machine ({self.cpu_core:.0f} % d'un cœur)"]
        if self.gpu is not None:
            lines.append(f"GPU : {self.gpu:.1f} %")
        lines.append(f"Mémoire : {self.memory_mb:.0f} Mo")
        if self.processes > 1:
            lines.append(f"{self.processes} processus (plugins pontés compris)")
        return "\n".join(lines)


def level(value: float | None, warn: float, high: float) -> int:
    """0 = normal, 1 = élevé (ambre), 2 = très élevé (rouge)."""
    if value is None or value < warn:
        return 0
    return 1 if value < high else 2


# --------------------------------------------------------------------------- #
# GPU (compteurs de performance Windows)
# --------------------------------------------------------------------------- #
_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
_GPU_COUNTER = r"\GPU Engine(*)\Utilization Percentage"


class _CounterValue(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong), ("pointer", ctypes.c_void_p)]

    _anonymous_ = ("value",)
    _fields_ = [("CStatus", ctypes.c_ulong), ("value", _Value)]


class _CounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _CounterValue)]


def gpu_usage_by_pid(instances: Iterable[tuple[str, float]]) -> dict[int, float]:
    """Utilisation du GPU par processus, d'après les instances du compteur
    « GPU Engine » (« pid_1234_luid_…_engtype_3D », valeur en %). Comme le
    Gestionnaire des tâches : pour un processus, le type de moteur (3D, vidéo,
    calcul…) le plus chargé."""
    per_engine: dict[tuple[int, str], float] = {}
    for name, value in instances:
        parts = name.split("_")
        if len(parts) < 2 or parts[0] != "pid" or not parts[1].isdigit():
            continue
        engine = name.rsplit("engtype_", 1)[-1] if "engtype_" in name else ""
        key = (int(parts[1]), engine)
        per_engine[key] = per_engine.get(key, 0.0) + max(0.0, value)
    usage: dict[int, float] = {}
    for (pid, _engine), value in per_engine.items():
        usage[pid] = max(usage.get(pid, 0.0), min(100.0, value))
    return usage


class GpuCounters:
    """Lecture du compteur « GPU Engine » de Windows ; `available` est faux hors
    Windows ou si le compteur n'existe pas (machine sans GPU, session distante)."""

    def __init__(self) -> None:
        self._pdh = None
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        if sys.platform != "win32":
            return
        try:
            pdh = ctypes.windll.pdh
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                return
            if pdh.PdhAddEnglishCounterW(self._query, ctypes.c_wchar_p(_GPU_COUNTER), 0,
                                         ctypes.byref(self._counter)) != 0:
                pdh.PdhCloseQuery(self._query)
                return
            pdh.PdhCollectQueryData(self._query)     # premier relevé : sert de référence
            self._pdh = pdh
        except (AttributeError, OSError) as exc:
            log.debug("Compteurs GPU indisponibles : %s", exc)

    @property
    def available(self) -> bool:
        return self._pdh is not None

    def read(self) -> dict[int, float] | None:
        """% de GPU par PID depuis le relevé précédent ; None si indisponible."""
        if self._pdh is None:
            return None
        pdh = self._pdh
        pdh.PdhCollectQueryData(self._query)
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        status = pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_DOUBLE, ctypes.byref(size),
                                                  ctypes.byref(count), None)
        if status & 0xFFFFFFFF != _PDH_MORE_DATA or size.value == 0:
            return {}                                 # aucun processus n'utilise le GPU
        buffer = ctypes.create_string_buffer(size.value)
        status = pdh.PdhGetFormattedCounterArrayW(self._counter, _PDH_FMT_DOUBLE, ctypes.byref(size),
                                                  ctypes.byref(count), buffer)
        if status != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
        return gpu_usage_by_pid((items[i].szName or "", items[i].FmtValue.doubleValue)
                                for i in range(count.value) if items[i].FmtValue.CStatus == 0)

    def close(self) -> None:
        if self._pdh is not None:
            self._pdh.PdhCloseQuery(self._query)
            self._pdh = None


# --------------------------------------------------------------------------- #
# Processus du moteur
# --------------------------------------------------------------------------- #
def engine_roots(processes, host) -> list[psutil.Process]:
    """Processus qui font tourner les plugins du moteur d'effets choisi."""
    key = getattr(host, "process_key", None)
    if key:
        getter = getattr(processes, "process", None)
        proc = getter(key) if getter is not None else None
        return [proc] if proc is not None else []
    if getattr(host, "kind", "") == "apo":
        return [p for p in psutil.process_iter(["name"])
                if (p.info.get("name") or "").lower() == APO_PROCESS]
    return []


def _family(roots: list[psutil.Process]) -> list[psutil.Process]:
    family: dict[int, psutil.Process] = {}
    for root in roots:
        try:
            family[root.pid] = root
            for child in root.children(recursive=True):
                family.setdefault(child.pid, child)
        except psutil.Error:
            continue
    return list(family.values())


class ProcessSampler:
    """Relevés successifs de la charge d'un groupe de processus."""

    def __init__(self, roots: Callable[[], list[psutil.Process]],
                 gpu: GpuCounters | None = None) -> None:
        self._roots = roots
        self._gpu = gpu
        self._known: dict[int, psutil.Process] = {}   # garde l'état de cpu_percent()
        self._cores = psutil.cpu_count() or 1

    def cpu_seconds(self) -> float | None:
        """Temps processeur cumulé du moteur (secondes) ; None s'il ne tourne pas."""
        total, found = 0.0, False
        for proc in _family(self._roots()):
            try:
                times = proc.cpu_times()
                total += times.user + times.system
                found = True
            except psutil.Error:
                continue
        return total if found else None

    def sample(self) -> EngineLoad | None:
        family = _family(self._roots())
        known: dict[int, psutil.Process] = {}
        cpu, memory, counted = 0.0, 0.0, 0
        for proc in family:
            proc = self._known.get(proc.pid, proc)    # même objet : cpu_percent() compare au relevé d'avant
            try:
                cpu += proc.cpu_percent(None)
                memory += proc.memory_info().rss
            except psutil.Error:
                continue
            known[proc.pid] = proc
            counted += 1
        # Un processus vu pour la première fois compte pour 0 % ce tour-ci : il suffit
        # qu'un seul ait déjà été relevé (un plugin ponté qui démarre ne cache pas la jauge)
        primed = any(pid in self._known for pid in known)
        self._known = known
        usage = self._gpu.read() if self._gpu is not None else None
        if not counted or not primed:
            return None                               # premier relevé : pas encore de mesure
        gpu = None if usage is None else min(100.0, sum(usage.get(pid, 0.0) for pid in known))
        return EngineLoad(cpu=min(100.0, cpu / self._cores), cpu_core=cpu, gpu=gpu,
                          memory_mb=memory / (1024 * 1024), processes=counted)


class EngineLoadMonitor(QObject):
    """Relève la charge du moteur d'effets en arrière-plan (toutes les 2 s)."""

    changed = Signal(object)          # EngineLoad, ou None (moteur arrêté)

    def __init__(self, sampler, interval_s: float = SAMPLE_INTERVAL_S,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sampler = sampler
        self._interval = interval_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.latest: EngineLoad | None = None

    @property
    def can_measure(self) -> bool:
        """Mesure par différence possible (temps processeur cumulé lisible)."""
        return hasattr(self._sampler, "cpu_seconds")

    def cpu_seconds(self) -> float | None:
        return self._sampler.cpu_seconds() if self.can_measure else None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="engine-load", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        failing = False
        while not self._stop.is_set():
            try:
                load = self._sampler.sample()
                failing = False
            except Exception as exc:          # ne doit jamais arrêter la console
                if not failing:
                    log.warning("Charge du moteur d'effets illisible : %s", exc)
                failing, load = True, None
            if load != self.latest:
                self.latest = load
                self.changed.emit(load)
            self._stop.wait(self._interval)


def create_monitor(processes, host) -> EngineLoadMonitor | None:
    """Moniteur du moteur d'effets en cours ; None sans moteur (Voicemeeter seul)."""
    if not getattr(host, "process_key", None) and getattr(host, "kind", "") != "apo":
        return None
    return EngineLoadMonitor(ProcessSampler(lambda: engine_roots(processes, host), GpuCounters()))
