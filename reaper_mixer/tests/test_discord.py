"""Discord : protocole RPC, Rich Presence, micro synchronisé (Discord simulé)."""

import json
import queue
import time

import pytest

from conftest import pump
from core import discord_rpc
from core.discord_rpc import (BOTH, FROM_DISCORD, HEADER, OP_CLOSE, OP_FRAME, OP_HANDSHAKE, OP_PING,
                              OP_PONG, TO_DISCORD, DemoDiscord, DiscordClient, DiscordError,
                              DiscordSettings, MuteLink, Presence, RpcSession)


def frame(opcode: int, payload: dict) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    return HEADER.pack(opcode, len(data)) + data


class FakeDiscord:
    """Le client Discord, vu du tube : répond aux commandes comme le vrai."""

    def __init__(self, token: str = "jeton", authorize: str | None = "code-1") -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.buffer = b""
        self.commands: list[tuple[str, dict]] = []
        self.pongs: list[dict] = []
        self.token = token              # jeton d'accès accepté par AUTHENTICATE
        self.authorize = authorize      # code renvoyé par AUTHORIZE (None : refus)
        self.mute = False
        self.closed = False

    # -- transport
    def write(self, data: bytes) -> None:
        self.buffer += data
        while len(self.buffer) >= HEADER.size:
            opcode, length = HEADER.unpack_from(self.buffer)
            if len(self.buffer) < HEADER.size + length:
                break
            payload = json.loads(self.buffer[HEADER.size:HEADER.size + length])
            self.buffer = self.buffer[HEADER.size + length:]
            self.handle(opcode, payload)

    def read(self, timeout_s: float) -> bytes:
        if self.closed:
            raise ConnectionError("fermé")
        try:
            return self.inbox.get(timeout=timeout_s)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True

    # -- Discord
    def handle(self, opcode: int, payload: dict) -> None:
        if opcode == OP_HANDSHAKE:
            if not str(payload.get("client_id", "")).startswith("1"):
                self.inbox.put(frame(OP_CLOSE, {"code": 4000, "message": "Invalid Client ID"}))
                return
            self.inbox.put(frame(OP_FRAME, {"cmd": "DISPATCH", "evt": "READY",
                                            "data": {"v": 1, "user": {"username": "zerka"}}}))
        elif opcode == OP_PONG:
            self.pongs.append(payload)
        elif opcode == OP_FRAME:
            cmd, args = payload["cmd"], payload.get("args") or {}
            self.commands.append((cmd, args))
            error, data = None, {}
            if cmd == "AUTHORIZE":
                if self.authorize is None:
                    error = {"code": 5000, "message": "OAuth2 Error: access_denied"}
                else:
                    data = {"code": self.authorize}
            elif cmd == "AUTHENTICATE":
                if args.get("access_token") != self.token:
                    error = {"code": 4009, "message": "Invalid access token"}
                else:
                    data = {"user": {"username": "zerka", "global_name": "Zerka"}}
            elif cmd == "GET_VOICE_SETTINGS":
                data = {"mute": self.mute, "deaf": False}
            elif cmd == "SET_VOICE_SETTINGS":
                self.mute = bool(args["mute"])
                data = {"mute": self.mute}
            answer = {"cmd": cmd, "nonce": payload["nonce"], "data": error or data,
                      "evt": "ERROR" if error else payload.get("evt")}
            self.inbox.put(frame(OP_FRAME, answer))
            if cmd == "SET_VOICE_SETTINGS":
                self.event("VOICE_SETTINGS_UPDATE", {"mute": self.mute})     # écho, comme Discord

    def event(self, evt: str, data: dict) -> None:
        self.inbox.put(frame(OP_FRAME, {"cmd": "DISPATCH", "evt": evt, "data": data, "nonce": None}))

    def user_mutes(self, mute: bool) -> None:
        """Clic sur le micro dans Discord."""
        self.mute = mute
        self.event("VOICE_SETTINGS_UPDATE", {"mute": mute})

    def sent(self, cmd: str) -> list[dict]:
        return [args for name, args in self.commands if name == cmd]


def wait_for(qapp, condition, timeout: float = 4.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump(qapp, 0.02)
        if condition():
            return True
    return False


@pytest.fixture
def fast(monkeypatch):
    for name in ("RETRY_S", "ERROR_RETRY_S"):
        monkeypatch.setattr(discord_rpc, name, 0.05)
    monkeypatch.setattr(discord_rpc, "POLL_S", 0.01)


class Recorder:
    """Signaux reçus d'un moteur Discord."""

    def __init__(self, client) -> None:
        self.statuses, self.mutes, self.tokens = [], [], []
        client.statusChanged.connect(self.statuses.append)
        client.muteChanged.connect(self.mutes.append)
        client.tokensChanged.connect(self.tokens.append)


# --------------------------------------------------------------------------- #
# Protocole
# --------------------------------------------------------------------------- #
def test_session_handshake_request_and_error():
    discord = FakeDiscord()
    session = RpcSession(discord, "123")
    assert session.handshake()["user"]["username"] == "zerka"
    assert session.request("GET_VOICE_SETTINGS") == {"mute": False, "deaf": False}
    with pytest.raises(DiscordError, match="Invalid access token") as error:
        session.request("AUTHENTICATE", {"access_token": "faux"})
    assert error.value.code == 4009


def test_session_split_frames_ping_and_events():
    discord = FakeDiscord()
    session = RpcSession(discord, "123")
    session.handshake()
    data = frame(OP_PING, {"n": 7}) + frame(OP_FRAME, {"cmd": "DISPATCH", "evt": "X", "data": {}})
    discord.inbox.put(data[:5])              # trame coupée en deux lectures
    assert session.poll(0.05) == []
    discord.inbox.put(data[5:])
    assert [f["evt"] for f in session.poll(0.05)] == ["X"]
    assert discord.pongs == [{"n": 7}]
    # Un événement arrivé pendant l'attente d'une réponse n'est pas perdu
    discord.event("VOICE_SETTINGS_UPDATE", {"mute": True})
    session.request("GET_VOICE_SETTINGS")
    assert [f["evt"] for f in session.events] == ["VOICE_SETTINGS_UPDATE"]


def test_session_rejected_client_id_and_timeout():
    with pytest.raises(DiscordError, match="Invalid Client ID"):
        RpcSession(FakeDiscord(), "999").handshake()

    class Silent(FakeDiscord):
        def handle(self, opcode, payload):
            pass

    with pytest.raises(DiscordError, match="ne répond pas"):
        RpcSession(Silent(), "123").handshake(timeout_s=0.05)
    with pytest.raises(discord_rpc._Interrupted):
        RpcSession(Silent(), "123", abort=lambda: True).handshake()


def test_presence_activity():
    activity = Presence(state="Voicemeeter Potato · REAPER", start=1700000000).activity()
    assert activity["details"] == "Console de mixage audio"
    assert activity["state"] == "Voicemeeter Potato · REAPER"
    assert activity["timestamps"] == {"start": 1700000000}
    assert activity["buttons"][0]["url"].startswith("https://github.com/")
    assert activity["assets"]["large_image"] == "reka"
    # Discord refuse une ligne de moins de 2 ou de plus de 128 caractères
    short = Presence(details="x", state="y" * 300).activity()
    assert "details" not in short and len(short["state"]) == 128 and "timestamps" not in short


def test_presence_lines_from_config(cfg):
    presence = discord_rpc.presence_from_config(cfg, "Voicemeeter Potato", 12)
    assert (presence.details, presence.state, presence.start) == ("Vst", "talking", 12)
    cfg["discord"].update(details="", state="x")       # vide ou trop court pour Discord
    presence = discord_rpc.presence_from_config(cfg, "Voicemeeter Potato")
    assert (presence.details, presence.state) == ("Console de mixage audio", "Voicemeeter Potato")
    del cfg["discord"]
    assert discord_rpc.presence_from_config(cfg).details == "Console de mixage audio"


def test_settings_from_config(cfg, monkeypatch):
    settings = DiscordSettings.from_config(cfg)
    assert settings.presence and not settings.mute_sync and settings.enabled
    # Rien à régler : l'application Discord « REKA » est utilisée, sans secret
    assert settings.client_id == discord_rpc.REKA_CLIENT_ID == "1570441366662549534"
    assert settings.valid_id and settings.client_secret == ""
    monkeypatch.setattr(discord_rpc, "REKA_CLIENT_ID", "")
    assert not DiscordSettings.from_config(cfg).valid_id     # sans application : rien n'est envoyé
    cfg["discord"].update(client_id=" 123456 ", client_secret=" s3cret ", rich_presence=False)
    settings = DiscordSettings.from_config(cfg)
    assert (settings.client_id, settings.client_secret) == ("123456", "s3cret")
    assert settings.valid_id and not settings.enabled
    cfg["discord"]["client_id"] = ""
    monkeypatch.setattr(discord_rpc, "REKA_CLIENT_ID", "42")
    assert DiscordSettings.from_config(cfg).client_id == "42"     # application REKA par défaut
    cfg["discord"]["mute_direction"] = "n'importe quoi"
    assert discord_rpc.mute_direction(cfg) == BOTH


# --------------------------------------------------------------------------- #
# Micro synchronisé : qui suit qui
# --------------------------------------------------------------------------- #
def test_mute_link_both_directions():
    link = MuteLink()
    assert link.reka_changed(True, 0.0) is None           # Discord pas encore connecté
    assert link.discord_changed(False, 0.0) == ("discord", True)   # à la connexion : REKA fait foi
    assert link.discord_changed(True, 0.1) is None        # écho de notre réglage
    assert link.reka_changed(False, 1.0) == ("discord", False)
    assert link.reka_changed(False, 1.1) is None          # rien de neuf
    assert link.discord_changed(True, 2.0) == ("reka", True)
    # Voicemeeter renvoie encore l'ancienne valeur un instant : ignorée
    assert link.reka_changed(False, 2.1) is None
    assert link.reka_changed(True, 2.2) is None
    assert link.reka_changed(False, 5.0) == ("discord", False)    # vrai changement, plus tard


def test_mute_link_already_aligned_and_reconnect():
    link = MuteLink()
    link.reka_changed(False, 0.0)
    assert link.discord_changed(False, 0.0) is None
    link.discord_lost()
    assert link.reka_changed(True, 1.0) is None           # Discord fermé : rien à envoyer
    assert link.discord_changed(False, 2.0) == ("discord", True)  # de retour : réaligné


def test_mute_link_one_way():
    link = MuteLink(TO_DISCORD)
    link.reka_changed(False, 0.0)
    link.discord_changed(False, 0.0)
    assert link.discord_changed(True, 1.0) is None        # Discord ne commande pas REKA
    assert link.reka_changed(True, 2.0) is None           # déjà pareil des deux côtés
    assert link.reka_changed(False, 3.0) == ("discord", False)

    link = MuteLink(FROM_DISCORD)
    link.reka_changed(True, 0.0)
    assert link.discord_changed(False, 0.0) == ("reka", False)    # à la connexion : Discord fait foi
    assert link.reka_changed(False, 0.1) is None
    assert link.reka_changed(True, 5.0) is None           # REKA ne commande pas Discord
    assert link.discord_changed(True, 6.0) is None        # déjà pareil
    assert link.discord_changed(False, 7.0) == ("reka", False)

    link.reset(BOTH)
    assert link.direction == BOTH and link.reka is None and link.discord is None
    assert MuteLink("inconnu").direction == BOTH


# --------------------------------------------------------------------------- #
# Client (thread) face à un Discord simulé
# --------------------------------------------------------------------------- #
def make_client(discord, settings, tokens=None, exchange=None):
    exchanges = []

    def default_exchange(params):
        exchanges.append(params)
        return {"access_token": "jeton", "refresh_token": "suivant", "expires_in": 604800}

    client = DiscordClient(settings, tokens, connect=lambda: discord,
                           exchange=exchange or default_exchange)
    return client, Recorder(client), exchanges


def test_client_rich_presence_on_and_off(qapp, fast):
    discord = FakeDiscord()
    client, seen, _ = make_client(discord, DiscordSettings("123"))
    client.set_presence(Presence(state="Voicemeeter Potato"))
    client.start()
    try:
        assert wait_for(qapp, lambda: discord.sent("SET_ACTIVITY"))
        args = discord.sent("SET_ACTIVITY")[0]
        assert args["activity"]["state"] == "Voicemeeter Potato" and args["pid"] > 0
        assert wait_for(qapp, lambda: client.status.connected)
        assert client.status.user == "zerka" and not client.status.voice and not client.status.problem
        assert not discord.sent("AUTHORIZE")          # la Rich Presence ne demande aucune autorisation
        client.set_presence(Presence(state="Voicemeeter Banana"))
        assert wait_for(qapp, lambda: len(discord.sent("SET_ACTIVITY")) == 2)
    finally:
        client.stop()
    assert discord.closed and seen.mutes == []        # connexion fermée : Discord retire l'activité


def test_client_presence_disabled_keeps_mute_sync(qapp, fast):
    discord = FakeDiscord()
    client, _, _ = make_client(discord, DiscordSettings("123", "secret", True, True))
    client.set_presence(Presence())
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.voice and discord.sent("SET_ACTIVITY"))
        # Désactivée dans les réglages : l'activité est retirée, sans reconnexion
        client.configure(DiscordSettings("123", "secret", False, True))
        assert wait_for(qapp, lambda: len(discord.sent("SET_ACTIVITY")) == 2)
        assert "activity" not in discord.sent("SET_ACTIVITY")[1]
        assert not discord.closed and client.status.voice
        client.configure(DiscordSettings("123", "secret", True, True))
        assert wait_for(qapp, lambda: len(discord.sent("SET_ACTIVITY")) == 3)
        assert len(discord.sent("AUTHORIZE")) == 1
    finally:
        client.stop()


def test_client_disabled_or_without_application(qapp, fast):
    connections = []
    client = DiscordClient(DiscordSettings("123", presence=False),
                           connect=lambda: connections.append(1))
    client.start()
    try:
        pump(qapp, 0.15)
        assert connections == [] and client.status.message == "Discord désactivé."
        client.configure(DiscordSettings(""))         # Rich Presence sans application
        assert wait_for(qapp, lambda: "aucune application" in client.status.message)
        assert connections == [] and not client.status.problem
        client.configure(DiscordSettings("abc"))
        assert wait_for(qapp, lambda: client.status.problem)
        client.configure(DiscordSettings("123"))      # Discord pas lancé : nouveaux essais
        assert wait_for(qapp, lambda: len(connections) >= 2)
        assert "n'est pas lancé" in client.status.message
    finally:
        client.stop()


def test_client_mute_sync_authorizes_once(qapp, fast):
    discord = FakeDiscord()
    discord.mute = True
    client, seen, exchanges = make_client(discord, DiscordSettings("123", "secret", False, True))
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.voice)
        assert discord.sent("AUTHORIZE") == [{"client_id": "123", "scopes": list(discord_rpc.SCOPES)}]
        assert exchanges == [{"client_id": "123", "client_secret": "secret",
                              "grant_type": "authorization_code", "code": "code-1",
                              "redirect_uri": "http://localhost"}]
        assert discord.sent("AUTHENTICATE") == [{"access_token": "jeton"}]
        assert [name for name, _ in discord.commands if name == "SUBSCRIBE"] == ["SUBSCRIBE"]
        assert seen.mutes == [True]                   # micro de Discord, lu à la connexion
        assert seen.tokens[-1]["access_token"] == "jeton"
        assert seen.tokens[-1]["refresh_token"] == "suivant"
        assert client.status.user == "Zerka" and "micro synchronisé" in client.status.message
        assert not discord.sent("SET_ACTIVITY")       # Rich Presence coupée

        client.set_mute(False)                        # Muet retiré dans REKA
        assert wait_for(qapp, lambda: discord.sent("SET_VOICE_SETTINGS") == [{"mute": False}])
        pump(qapp, 0.1)
        assert seen.mutes == [True]                   # l'écho de Discord n'est pas un changement
        client.set_mute(False)
        pump(qapp, 0.1)
        assert len(discord.sent("SET_VOICE_SETTINGS")) == 1     # déjà dans cet état

        discord.user_mutes(True)                      # clic sur le micro dans Discord
        assert wait_for(qapp, lambda: seen.mutes == [True, True])
    finally:
        client.stop()


def test_client_reuses_and_refreshes_token(qapp, fast):
    discord = FakeDiscord(token="neuf")
    exchanges = []

    def exchange(params):
        exchanges.append(params["grant_type"])
        return {"access_token": "neuf", "refresh_token": "r2", "expires_in": 60}

    client, seen, _ = make_client(discord, DiscordSettings("123", "secret", True, True),
                                  tokens={"access_token": "périmé", "refresh_token": "r1"},
                                  exchange=exchange)
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.voice)
        assert exchanges == ["refresh_token"] and not discord.sent("AUTHORIZE")
        assert [a["access_token"] for a in discord.sent("AUTHENTICATE")] == ["périmé", "neuf"]
        assert seen.tokens[-1]["refresh_token"] == "r2"
    finally:
        client.stop()

    # Jeton encore bon : ni renouvellement, ni fenêtre d'autorisation
    discord = FakeDiscord(token="neuf")
    client, _, exchanges = make_client(discord, DiscordSettings("123", "secret", True, True),
                                       tokens={"access_token": "neuf"})
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.voice)
        assert exchanges == [] and not discord.sent("AUTHORIZE")
    finally:
        client.stop()


def test_client_refused_authorization_does_not_nag(qapp, fast):
    first = FakeDiscord(authorize=None)
    transports = [first]
    client = DiscordClient(DiscordSettings("123", "secret", True, True),
                           connect=lambda: transports[-1], exchange=lambda params: {})
    client.set_presence(Presence())
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.problem)
        assert client.status.connected and not client.status.voice
        assert "access_denied" in client.status.message and "Autoriser" in client.status.message
        assert first.sent("SET_ACTIVITY")             # la Rich Presence marche quand même

        # Discord redémarre : pas de nouvelle fenêtre d'autorisation sans clic
        second = FakeDiscord(authorize=None)
        transports.append(second)
        first.closed = True
        assert wait_for(qapp, lambda: second.sent("SET_ACTIVITY") and client.status.problem)
        assert not second.sent("AUTHORIZE")

        second.authorize = "code-2"                   # « Autoriser dans Discord »
        third = FakeDiscord()
        transports.append(third)
        client._exchange = lambda params: {"access_token": "jeton", "refresh_token": "r"}
        client.authorize()
        assert wait_for(qapp, lambda: client.status.voice)
        assert third.sent("AUTHORIZE")
    finally:
        client.stop()


def test_client_missing_secret_and_token_errors(qapp, fast):
    discord = FakeDiscord()
    client, _, _ = make_client(discord, DiscordSettings("123", "", False, True))
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.problem)
        assert "Client Secret" in client.status.message and not discord.sent("AUTHORIZE")
    finally:
        client.stop()

    def rejected(params):
        raise discord_rpc.TokenRejected("Discord a refusé le jeton : Client Secret incorrect.")

    discord = FakeDiscord()
    client, _, _ = make_client(discord, DiscordSettings("123", "faux", False, True),
                               exchange=rejected)
    client.start()
    try:
        assert wait_for(qapp, lambda: client.status.problem)
        assert "Client Secret incorrect" in client.status.message
        pump(qapp, 0.2)
        assert len(discord.sent("AUTHORIZE")) == 1    # pas de fenêtre en boucle
    finally:
        client.stop()


def test_client_forgets_tokens_of_another_application(qapp, fast):
    client, seen, _ = make_client(FakeDiscord(), DiscordSettings("123", "s", False, False),
                                  tokens={"access_token": "a"})
    client.configure(DiscordSettings("124", "s", False, False))
    assert seen.tokens == [{}]


# --------------------------------------------------------------------------- #
# Console : le Muet d'une strip suit le micro de Discord
# --------------------------------------------------------------------------- #
@pytest.fixture
def console(qapp, cfg):
    from core.app_volume import DemoAppVolumes
    from core.demo import DemoProcesses, DemoReaper, DemoVoicemeeter
    from core.media import DemoMedia
    from ui.main_window import MainWindow

    cfg["discord"].update(client_id="123", mute_sync=True, mute_strip=1)
    vm, discord = DemoVoicemeeter(cfg), DemoDiscord(DiscordSettings.from_config(cfg))
    window = MainWindow(cfg=cfg, vm=vm, reaper=DemoReaper(cfg), processes=DemoProcesses(),
                        persist=False, apps=DemoAppVolumes(), media=DemoMedia(), discord=discord)
    window.show()
    pump(qapp, 0.1)
    yield window, vm, discord
    window.close()


def test_console_mute_follows_discord(qapp, console):
    window, vm, discord = console
    assert discord.status.voice and discord.sent == []        # déjà pareil : rien à envoyer
    assert (discord.presence.details, discord.presence.state) == ("Vst", "talking")
    assert discord.presence.start > 0

    window.strips[1].mute.click()                 # Muet dans REKA → micro barré dans Discord
    pump(qapp, 0.05)
    assert vm._strips[1].mute and discord.mute and discord.sent == [True]

    discord.toggle(False)                         # micro rouvert dans Discord → Muet retiré
    pump(qapp, 0.05)
    assert not vm._strips[1].mute and not window.strips[1].mute.isChecked()
    assert discord.sent == [True]                 # pas de renvoi en boucle

    window.strips[0].mute.click()                 # une autre strip : Discord n'est pas concerné
    pump(qapp, 0.05)
    assert discord.sent == [True]


def test_console_discord_settings_apply_live(qapp, console):
    window, vm, discord = console
    cfg = window._cfg
    cfg["discord"]["mute_strip"] = 0              # strip 0 : coupée dans la démo
    window.apply_discord()
    pump(qapp, 0.05)
    assert discord.sent == [True] and discord.mute            # REKA fait foi

    cfg["discord"]["mute_direction"] = FROM_DISCORD
    window.apply_discord()
    discord.toggle(False)
    pump(qapp, 0.05)
    assert not vm._strips[0].mute
    window.strips[0].mute.click()                 # REKA ne commande plus Discord
    pump(qapp, 0.05)
    assert vm._strips[0].mute and not discord.mute and discord.sent == [True]

    cfg["discord"]["mute_sync"] = False           # option coupée : plus rien ne suit
    window.apply_discord()
    discord.toggle(True)
    window.strips[1].mute.click()
    pump(qapp, 0.05)
    assert discord.sent == [True] and not discord.status.voice


def test_console_saves_tokens(qapp, console, monkeypatch):
    window, _, discord = console
    discord.tokensChanged.emit({"access_token": "a", "refresh_token": "r", "token_expires": 9})
    assert window._cfg["discord"]["access_token"] == "a"
    assert window._cfg["discord"]["token_expires"] == 9
    discord.tokensChanged.emit({})
    assert window._cfg["discord"]["access_token"] == "" and window._cfg["discord"]["token_expires"] == 0


def test_settings_discord_tab(qapp, console):
    from ui.settings_dialog import SettingsDialog

    window, vm, discord = console
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False,
                            discord=discord)
    dialog.discordChanged.connect(window.apply_discord)
    changes = []
    dialog.discordChanged.connect(lambda: changes.append(1))
    assert dialog.discord_presence.isChecked() and dialog.discord_mute.isChecked()
    assert dialog.discord_strip.currentData() == 1 and dialog.discord_strip.count() == vm.strip_count
    assert "micro synchronisé" in dialog.discord_status.text()
    assert dialog.discord_status.objectName() == "ok" and not dialog.discord_authorize.isEnabled()
    assert dialog.discord_secret.echoMode() == dialog.discord_secret.EchoMode.Password

    # Les deux lignes affichées sur le profil : modifiables, appliquées tout de suite
    assert (dialog.discord_details.text(), dialog.discord_state.text()) == ("Vst", "talking")
    dialog.discord_details.setText("  En live  ")
    dialog.discord_details.editingFinished.emit()
    assert window._cfg["discord"]["details"] == "En live" and discord.presence.details == "En live"
    dialog.discord_state.setText("")              # vide : les moteurs de la session
    dialog.discord_state.editingFinished.emit()
    assert discord.presence.state == "Voicemeeter Potato · REAPER"
    changes.clear()

    dialog.discord_presence.setChecked(False)     # Rich Presence désactivable
    assert window._cfg["discord"]["rich_presence"] is False and len(changes) == 1
    dialog.discord_direction.setCurrentIndex(dialog.discord_direction.findData(TO_DISCORD))
    assert window._cfg["discord"]["mute_direction"] == TO_DISCORD
    assert window._mute_link.direction == TO_DISCORD
    dialog.discord_mute.setChecked(False)
    pump(qapp, 0.05)
    assert not discord.status.voice and dialog.discord_status.text() == "Discord désactivé."
    assert dialog.discord_status.objectName() == "hint"
    dialog.discord_presence.setChecked(True)
    pump(qapp, 0.05)
    assert "Discord simulé" in dialog.discord_status.text() and discord.status.connected
    dialog.discord_id.setText(" 456 ")
    dialog.done(0)                                # champ en cours de saisie pris en compte
    assert window._cfg["discord"]["client_id"] == "456"
    assert DiscordSettings.from_config(window._cfg).client_id == "456"

    # Sans moteur Discord : onglet affiché, bouton inactif
    dialog = SettingsDialog(window._cfg, None, window._reaper, vm, window, persist=False)
    assert "pas disponible" in dialog.discord_status.text()
    dialog.done(0)


# --------------------------------------------------------------------------- #
# Transport réel (socket Unix ; le tube nommé de Windows a la même interface)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not hasattr(__import__("socket"), "AF_UNIX") or __import__("sys").platform == "win32",
                    reason="socket Unix")
def test_socket_transport_end_to_end(tmp_path, monkeypatch):
    import socket
    import threading

    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert discord_rpc.ipc_paths()[0] == str(tmp_path / "discord-ipc-0")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(tmp_path / "discord-ipc-1"))      # second client Discord : trouvé aussi
    server.listen(1)

    def serve() -> None:
        connection, _ = server.accept()
        discord = FakeDiscord()
        with connection:
            while True:
                data = connection.recv(65536)
                if not data:
                    return
                discord.write(data)
                while not discord.inbox.empty():
                    connection.sendall(discord.inbox.get())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    transport = discord_rpc.open_transport()
    assert isinstance(transport, discord_rpc.SocketTransport)
    session = RpcSession(transport, "123")
    assert session.handshake()["user"]["username"] == "zerka"
    assert session.request("SET_VOICE_SETTINGS", {"mute": True}) == {"mute": True}
    events = session.events + session.poll(0.05)      # l'écho VOICE_SETTINGS_UPDATE
    assert [event["evt"] for event in events] == ["VOICE_SETTINGS_UPDATE"]
    assert transport.read(0.02) == b""                # plus rien : délai écoulé, pas d'erreur
    transport.close()
    thread.join(timeout=2)
    server.close()


def test_no_discord_running(tmp_path, monkeypatch):
    if __import__("sys").platform != "win32":
        for name in ("TMPDIR", "TMP", "TEMP"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setattr(discord_rpc, "ipc_paths",
                            lambda: [str(tmp_path / f"discord-ipc-{n}") for n in range(3)])
        assert discord_rpc.open_transport() is None
    assert len(discord_rpc.ipc_paths()) >= 3
