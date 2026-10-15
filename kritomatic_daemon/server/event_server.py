"""
event_server.py — the daemon's event stream socket.

A second TCP server alongside the command server. Where the command
server is request/response (one command in, one reply out, then the
connection may close), the event server is a one-way push channel:
the client connects, receives an initial state snapshot, and then
receives every subsequent state event until it disconnects.

Protocol: newline-delimited JSON. Each event is one line:

    {"type": "state", "data": {...comfy_status payload...}}

The client sends nothing. There is no handshake beyond the initial
snapshot the server sends on connect.

Wake discipline
---------------
The per-client loop blocks in `select` on two descriptors: the client
socket (to detect disconnect) and the subscription's signal socket (to
detect a new event). It has no timeout. There is no poll interval and
no timer anywhere on this path.

When the bus publishes, it writes a byte to the subscription's signal
socket. The loop wakes, drains the pipe, drains the queue, sends each
event, and returns to select. When the client disconnects, the loop
wakes on the client socket, peeks, sees EOF, and exits. When the
server is stopped, `stop()` closes every active subscription's write
end, which makes each consumer's read end readable and wakes it.

Reconnection is the client's responsibility. When Krita restarts, the
connection drops; the client retries and receives a fresh initial
snapshot on the new connection.

Multiple clients can connect at once. Each gets its own subscription
from the event bus and its own initial snapshot.

The port is separate from the command server's (12347 vs 12346) so
the two can be firewalled or proxied independently, and so a client
that only wants events never has to speak the command protocol.
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
    """One-way push channel for daemon state events."""

    StateProvider = Callable[[], Optional[dict]]

    def __init__(self, port: int = 12347,
                 state_provider: Optional[StateProvider] = None):
        super().__init__(daemon=True, name="kritomatic-event-server")
        self.port = port
        self.state_provider = state_provider
        self.running = True
        self._server_socket: Optional[socket.socket] = None
        self._active_subs: set[Subscription] = set()
        self._active_subs_lock = threading.Lock()

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

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
                # Only the accept loop polls, and only for the shutdown
                # flag: Python cannot select on both an accept socket
                # and a "stop" signal without a self-pipe of its own.
                # A 1 s accept timeout is the least intrusive way to
                # notice `stop()` on this thread. Nothing downstream
                # of accept polls.
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
        """Signal shutdown.

        Closes every active subscription's write end, which wakes every
        consumer's select immediately. Also closes the accept socket,
        which wakes the accept loop.
        """
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

    # ------------------------------------------------------------------
    #  Per-client loop
    # ------------------------------------------------------------------

    def _serve_client(self, client: socket.socket) -> None:
        bus = get_event_bus()
        sub = bus.subscribe()

        with self._active_subs_lock:
            self._active_subs.add(sub)

        try:
            # Initial snapshot, before entering select. A client that
            # opened the connection and is waiting for the first byte
            # gets state immediately, without needing a subsequent
            # publish to be woken by.
            initial = self._fetch_initial_state()
            if initial is not None:
                try:
                    self._send(client, {"type": "state", "data": initial})
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
                    # Either the client sent something (unexpected —
                    # the protocol is one-way) or it closed. Peek
                    # without consuming to tell the two apart; a
                    # well-behaved client produces EOF here.
                    try:
                        peek = client.recv(1, socket.MSG_PEEK)
                    except (ConnectionResetError, OSError):
                        return
                    if not peek:
                        return
                    # Client sent a byte. Drain and ignore, so select
                    # does not spin on the same readability.
                    try:
                        client.recv(65536)
                    except (ConnectionResetError, OSError):
                        return

                if sub.signal_r in readable:
                    # Drain the wake signal. The queue is the delivery
                    # channel; the pipe byte was only a hint.
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

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _fetch_initial_state(self) -> Optional[dict]:
        if self.state_provider is None:
            return None
        try:
            return self.state_provider()
        except Exception:
            traceback.print_exc()
            return None

    def _send(self, client: socket.socket, event: dict) -> None:
        payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        client.sendall(payload)
