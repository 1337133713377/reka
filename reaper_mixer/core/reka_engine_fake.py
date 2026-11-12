"""
Faux reka-host.exe, en Python : même protocole que le vrai (voir
core/reka_host.py et native/reka-host/src/protocol.cpp), sans son ni plugins.

Il sert au mode démo (« REKA.exe --demo --host reka ») et aux tests : c'est
aussi la référence lisible du protocole. Un « plugin » y est un nom tiré du
chemin, et ses « réglages » un petit texte JSON en base64.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from pathlib import Path

PROTOCOL = 1
VERSION = "demo"


def _stem(path: str) -> str:
    return Path(str(path).replace("\\", "/")).stem


class FakeEngine:
    """S'exécute dans un thread ; poll() / terminate() / pid imitent un processus."""

    def __init__(self, port: int, token: str, samplerate: int = 48000, blocksize: int = 256,
                 latency: int = 64) -> None:
        self.port, self.token = port, token
        self.samplerate, self.blocksize, self.latency = samplerate, blocksize, latency
        self.pid = None
        self.strips: list[dict] = []
        self.received: list[dict] = []
        self.missing_paths: set[str] = set()      # chemins « non installés » (tests)
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._exit: int | None = None
        self._thread = threading.Thread(target=self._run, name="fake-reka-host", daemon=True)

    # -------------------------------------------------------- « processus »
    def start(self) -> None:
        self._thread.start()

    def poll(self) -> int | None:
        return self._exit

    def terminate(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def crash(self) -> None:
        """Arrêt brutal (tests de relance)."""
        self.terminate()

    # ------------------------------------------------------------ boucle
    def _run(self) -> None:
        try:
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        except OSError:
            self._exit = 1
            return
        self._sock = sock
        sock.settimeout(None)
        self._write({"type": "hello", "token": self.token, "version": VERSION,
                     "protocol": PROTOCOL})
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = sock.recv(1 << 20)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    self.received.append(message)
                    if not self._handle(message):
                        return
        except OSError:
            pass
        finally:
            self._exit = 0 if self._stop.is_set() else 1
            try:
                sock.close()
            except OSError:
                pass

    def _write(self, message: dict) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendall((json.dumps(message) + "\n").encode())
        except OSError:
            pass

    # ------------------------------------------------------------ commandes
    def _plugin(self, ref: dict, state: str = "", enabled: bool = True) -> dict:
        path = str(ref.get("path", ""))
        name = str(ref.get("name") or _stem(path))
        if not state:
            state = base64.b64encode(json.dumps({"plugin": name, "gain": 0}).encode()).decode()
        return {"format": str(ref.get("format", "VST3")).upper(), "path": path,
                "uid": str(ref.get("uid", "")), "name": name, "vendor": "Démo",
                "enabled": enabled, "state": state, "open": False,
                "missing": path in self.missing_paths}

    def _strip(self, index) -> dict | None:
        if isinstance(index, int) and 0 <= index < len(self.strips):
            return self.strips[index]
        return None

    def _handle(self, message: dict) -> bool:
        cmd, ident = message.get("cmd"), message.get("id")
        ok, error = True, ""
        if cmd == "quit":
            self._stop.set()
            return False
        if cmd == "setup":
            names = [s.get("name", "") for s in message.get("strips", [])]
            self.strips = [{"name": n, "enabled": True, "fx": []} for n in names]
        elif cmd == "load":
            chains = message.get("chains", {}).get("strips", [])
            for strip, chain in zip(self.strips, chains):
                strip["enabled"] = bool(chain.get("enabled", True))
                strip["fx"] = [self._plugin(fx, fx.get("state", ""), fx.get("enabled", True))
                               for fx in chain.get("fx", [])]
        elif cmd == "rename":
            strip = self._strip(message.get("strip"))
            if strip is not None:
                strip["name"] = message.get("name", "")
        elif cmd == "add_fx":
            strip = self._strip(message.get("strip"))
            ref = message.get("plugin") or {}
            if strip is None:
                ok, error = False, "strip inconnue"
            elif ref.get("path") in self.missing_paths:
                ok, error = False, f"Plugin introuvable : {ref.get('path')}"
            else:
                strip["fx"].append(self._plugin(ref))
        elif cmd == "remove_fx":
            strip = self._strip(message.get("strip"))
            index = message.get("index")
            if strip is not None and isinstance(index, int) and 0 <= index < len(strip["fx"]):
                del strip["fx"][index]
        elif cmd == "move_fx":
            src, dst = self._strip(message.get("src")), self._strip(message.get("dst"))
            index, target = message.get("src_index"), message.get("dst_index", 0)
            if src is not None and dst is not None and isinstance(index, int) \
                    and 0 <= index < len(src["fx"]):
                fx = dict(src["fx"][index]) if message.get("copy") else src["fx"].pop(index)
                fx["open"] = False if message.get("copy") else fx["open"]
                dst["fx"].insert(max(0, min(int(target), len(dst["fx"]))), fx)
        elif cmd == "fx_enabled":
            strip = self._strip(message.get("strip"))
            index = message.get("index")
            if strip is not None and isinstance(index, int) and 0 <= index < len(strip["fx"]):
                strip["fx"][index]["enabled"] = bool(message.get("enabled"))
        elif cmd == "chain_enabled":
            strip = self._strip(message.get("strip"))
            if strip is not None:
                strip["enabled"] = bool(message.get("enabled"))
        elif cmd == "toggle_ui":
            strip = self._strip(message.get("strip"))
            index = message.get("index")
            if strip is not None and isinstance(index, int) and 0 <= index < len(strip["fx"]):
                strip["fx"][index]["open"] = not strip["fx"][index]["open"]
        elif cmd == "get_chains":
            self._write({"type": "chains", "chains": self.chains()})
        else:
            ok, error = False, f"commande inconnue : {cmd}"
        self._write({"type": "ack", "id": ident} if ok else
                    {"type": "error", "id": ident, "message": error})
        if cmd not in ("get_chains",):
            self.send_state()
        return True

    def chains(self) -> dict:
        return {"format": 1, "strips": [
            {"enabled": s["enabled"],
             "fx": [{k: fx[k] for k in ("format", "path", "uid", "name", "vendor", "enabled",
                                         "state")} for fx in s["fx"]]}
            for s in self.strips]}

    def send_state(self) -> None:
        self._write({"type": "state",
                     "engine": {"running": True, "samplerate": self.samplerate,
                                "blocksize": self.blocksize, "message": ""},
                     "strips": [{"name": s["name"], "enabled": s["enabled"],
                                 "fx": [{"name": fx["name"], "format": fx["format"],
                                         "enabled": fx["enabled"], "open": fx["open"],
                                         "missing": fx["missing"],
                                         "latency": 0 if fx["missing"] else self.latency}
                                        for fx in s["fx"]]}
                                for s in self.strips]})

    def edit_parameter(self) -> None:
        """Simule un réglage changé dans l'interface d'un plugin."""
        for strip in self.strips:
            for fx in strip["fx"]:
                fx["state"] = base64.b64encode(
                    json.dumps({"plugin": fx["name"], "gain": 6}).encode()).decode()
        self._write({"type": "dirty"})
