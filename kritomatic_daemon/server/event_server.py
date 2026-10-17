"""
event_server.py — the daemon's event stream socket.

A second TCP server alongside the command server. Where the command
server is request/response (one command in, one reply out, then the
connection may close), the event server is a one-way push channel:
the client connects, receives an initial snapshot (state + panel),
and then receives every subsequent event until it disconnects.

Protocol: newline-delimited JSON. Each event is one line:

    {"type": "state", "data": {...comfy_status payload...}}
    {"type": "panel", "data": {...panel description...}}
    {"type": "run",   "data": {...run progress...}}

The client sends nothing. There is no handshake beyond the initial
snapshots the server sends on connect.

Reconnection is the client's responsibility. When Krita restarts, the
connection drops; the client retries and receives fresh initial
snapshots on the new connection.
"""

from __future__ import annotations

import json
import queue
import select
import socket
import threading
import traceback
from typing import Callable, Optional

from .event_bus import Subscription, get_event_bus


class EventServer(threading.Thread):

    StateProvider = Callable[[], Optional[dict]]
    PanelProvider = Callable[[], Optional[dict]]

    def __init__(self, port: int = 12347,
                 state_provider: Optional[StateProvider] = None,
                 panel_provider: Optional[PanelProvider] = None):
        super().__init__(daemon=True, name="kritomatic-event-server")
        self.port = port
        self.state_provider = state_provider
        self.panel_provider = panel_provider
        self.running = True
        self._server_socket: Optional[socket.socket] = None
        self._active_subs: set = set()
        self._active_subs_lock = threading.Lock()

    # -- lifecycle --

    def run(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("localhost", self.port))
        server.listen(8)
        server.settimeout(1.0)
        self._server_socket = server
        print(f"✓ Kritomatic event server listening on port {self.port}")

        while self.running:
            try:
                client, _addr = server.accept()
                t = threading.Thread(
                    target=self._serve_client,
                    args=(client,),
                    name="kritomatic-event-client",
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                if self.running:
                    continue
                break
            except Exception:
                if self.running:
                    traceback.print_exc()

        try:
            server.close()
        except Exception:
            pass

    def stop(self) -> None:
        self.running = False
        with self._active_subs_lock:
            subs = list(self._active_subs)
        for sub in subs:
            try:
                sub.signal_w.close()
            except OSError:
                pass
        try:
            if self._server_socket is not None:
                self._server_socket.close()
        except Exception:
            pass

    # -- per-client loop --

    def _serve_client(self, client: socket.socket) -> None:
        bus = get_event_bus()
        sub = bus.subscribe()

        with self._active_subs_lock:
            self._active_subs.add(sub)

        try:
            # Initial snapshots, before entering select.
            initial_state = self._fetch_initial_state()
            if initial_state is not None:
                try:
                    self._send(client, {"type": "state", "data": initial_state})
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            initial_panel = self._fetch_initial_panel()
            if initial_panel is not None:
                try:
                    self._send(client, {"type": "panel", "data": initial_panel})
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            while self.running:
                try:
                    readable, _, _ = select.select(
                        [client, sub.signal_r], [], [],
                    )
                except (OSError, ValueError):
                    return

                if not self.running:
                    return

                if client in readable:
                    try:
                        peek = client.recv(1, socket.MSG_PEEK)
                    except (ConnectionResetError, OSError):
                        return
                    if not peek:
                        return
                    try:
                        client.recv(65536)
                    except (ConnectionResetError, OSError):
                        return

                if sub.signal_r in readable:
                    sub.drain_signal()
                    while True:
                        try:
                            event = sub.queue.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            self._send(client, event)
                        except (BrokenPipeError,
                                ConnectionResetError,
                                OSError):
                            return

        except Exception:
            traceback.print_exc()
        finally:
            try:
                bus.unsubscribe(sub)
            except Exception:
                pass
            with self._active_subs_lock:
                self._active_subs.discard(sub)
            try:
                sub.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    # -- helpers --

    def _fetch_initial_state(self) -> Optional[dict]:
        if self.state_provider is None:
            return None
        try:
            return self.state_provider()
        except Exception:
            traceback.print_exc()
            return None

    def _fetch_initial_panel(self) -> Optional[dict]:
        if self.panel_provider is None:
            return None
        try:
            return self.panel_provider()
        except Exception:
            traceback.print_exc()
            return None

    def _send(self, client: socket.socket, event: dict) -> None:
        payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        client.sendall(payload)
