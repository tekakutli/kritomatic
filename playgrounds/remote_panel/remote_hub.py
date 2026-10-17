"""
pgkit.remote_hub — server-side WebSocket hub.

A `Hub` owns a list of connected clients and broadcasts messages
among them.  Its only protocol-level knowledge is the `hello`
handshake: it assigns each client an id, remembers its role
(`browser` or `remote`), and re-broadcasts presence.

Everything else is forwarded verbatim.  The hub does not maintain
playground state; the browser does.  A `patch` sent by one client
is relayed to every other client and applied by whoever consumes
it.  A `command` sent by a remote client is relayed to the
browser, which applies it and sends a `command_result` back; the
hub relays that too.

FORWARDED MESSAGE TYPES
=======================
The hub relays any of:

    patch, command, command_result, state, state_request, schema

`schema` carries the field metadata the browser advertises alongside
its state dump; see pgkit/remote_client.py.  A remote client that
does not understand `schema` receives it and ignores it, exactly as
it would ignore any other unrecognized `type`.
"""

import json
import threading

from . import ws as _ws
from . import PROTOCOL_VERSION


_FORWARDED_TYPES = (
    "patch",
    "command",
    "command_result",
    "state",
    "state_request",
    "schema",
)


class Client:
    """One connected WebSocket peer."""

    def __init__(self, sock, peer=None):
        self.sock      = sock
        self.peer      = peer
        self.id        = "c-%x" % id(self)
        self.role      = None
        self.send_lock = threading.Lock()

    def send(self, msg):
        data = json.dumps(msg)
        with self.send_lock:
            _ws.write_text_frame(self.sock, data)

    def recv(self):
        opcode, payload = _ws.read_frame(self.sock)
        if opcode is None:
            return None
        if opcode == 0x08:
            return None
        if opcode == 0x01:
            try:
                return payload.decode("utf-8")
            except Exception:
                return ""
        return ""


class Hub:
    def __init__(self):
        self.clients = []
        self.lock    = threading.Lock()

    def add(self, client):
        with self.lock:
            self.clients.append(client)
        self._broadcast_presence()

    def remove(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
        self._broadcast_presence()

    def broadcast(self, msg, exclude=None):
        dead = []
        with self.lock:
            targets = [c for c in self.clients if c is not exclude]
        for c in targets:
            try:
                c.send(msg)
            except Exception:
                dead.append(c)
        for c in dead:
            self.remove(c)

    def _broadcast_presence(self):
        with self.lock:
            browser = sum(1 for c in self.clients if c.role == "browser")
            remote  = sum(1 for c in self.clients if c.role == "remote")
        self.broadcast({
            "type":    "presence",
            "browser": browser,
            "remote":  remote,
        })


def handle_message(hub, client, msg):
    """Interpret one JSON message from `client` and dispatch."""
    t = msg.get("type")

    if t == "hello":
        client.role = msg.get("role") or "remote"
        client.send({
            "type":     "hello_ack",
            "clientId": client.id,
            "protocol": PROTOCOL_VERSION,
        })
        if client.role == "browser":
            client.send({"type": "state_request"})
        else:
            hub.broadcast({"type": "state_request"}, exclude=client)
        hub._broadcast_presence()
        return

    if t in _FORWARDED_TYPES:
        hub.broadcast(msg, exclude=client)
        return
