"""pgkit.hub_client — playground-side registration client.

    conn = ensure_running("board", 8770,
                          html_file="board_playground.html")
    # ... continue; conn runs in a background thread, keeps the WS
    #     connection to the hub alive, and exits the process if the
    #     hub sends a cooperative-shutdown request.

    conn = ensure_running("remote_panel", 8772, role="panel",
                          html_file="remote_panel.html")
    # ... same, but registers as `role="panel"` instead of
    #     `role="playground"`. Panels cannot be killed cooperatively
    #     (see hub.py, _kill_playground), and are filtered out of the
    #     playground dropdown in remote_panel/rp_client.py.

    conn = ensure_running("board", 8770,
                          html_file="board_playground.html",
                          group="core")
    # ... same as the first form, but the registration carries the
    #     group name.  Groups are metadata, not routing; see the
    #     GROUPS section in pgkit/hub.py.

    conn = ensure_running("core", 0,
                          role="launcher", group="core",
                          message_handler=on_cmd)
    # ... a launcher's control channel.  The hub stores this as a
    #     registration whose `available` field is a list of names the
    #     launcher can start; panels use it to send launcher_start /
    #     launcher_stop / launcher_restart commands, which the hub
    #     forwards to this WS.  `message_handler` receives those
    #     commands; the launcher replies via `conn.send(...)`.
    #     Playgrounds still register individually, exactly as before.

`html_file` is the playground's own `kit_serve(html_file=...)` value.
It travels to the hub in the `hello` frame and to panels in every
`playgrounds` event, so remote_panel can build an Open URL without a
hardcoded filename map.

`group` is optional and defaults to None.  Standalone launchers pass
nothing; the shared-process launcher (`pgkit/launcher.py`) passes the
group name so every playground it starts joins the same group.  A
`None` group behaves exactly as before this field was added.

`message_handler` is optional.  When set, every WS message the hub
sends that is not `hello_ack` or `shutdown` is passed to it as a dict
(see `HubConnection._handle`).  The launcher uses this to receive
launcher_* commands; playgrounds and panels leave it None.

`HubConnection.send(msg)` sends an arbitrary JSON message back over
the same WS.  Playgrounds never call it; the launcher uses it to push
`launcher_state` (available names) and `launcher_result` (per-command
replies).

Detached hub spawn
==================
If no hub is listening on the configured port, this module spawns
`python -m pgkit.hub` with `start_new_session=True` so killing the
playground's terminal does not kill the hub. Duplicate spawns are
tolerated: whichever hub binds the port first wins; the other exits
silently and its playground's WS client connects to the live one.

Frames are unmasked
===================
This is a private protocol between our own hub and our own
playgrounds. `pgkit.ws.read_frame` accepts unmasked client frames, so
we skip the masking step that RFC 6455 would require for third-party
servers. The hub only ever accepts frames from us.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import ws as _ws


HUB_HOST = "127.0.0.1"
HUB_PORT = 8780


# ============================================================================
#  Public entry point
# ============================================================================

def ensure_running(playground_name, playground_port, *,
                   role="playground",
                   html_file=None,
                   group=None,
                   hub_port=HUB_PORT,
                   connect_timeout=10.0,
                   message_handler=None):
    """Return a live HubConnection registered with the hub.

    See the module docstring for the roles and the extra kwargs.
    """
    conn = HubConnection(playground_name, playground_port,
                         hub_port, role=role, html_file=html_file,
                         group=group, message_handler=message_handler)
    conn.start(timeout=connect_timeout)
    return conn


# ============================================================================
#  Connection
# ============================================================================

class HubConnection:
    def __init__(self, name, port, hub_port, role="playground",
                 html_file=None, group=None, message_handler=None):
        self.name = name
        self.port = port
        self.hub_port = hub_port
        self.role = role
        self.html_file = html_file
        self.group = group
        self.message_handler = message_handler
        self.hub_url = f"http://{HUB_HOST}:{hub_port}/{name}/"
        self.stop = threading.Event()
        self.registered = threading.Event()
        self.thread = None
        self._sock = None
        self._sock_lock = threading.Lock()
        self._log_prefix = f"[hub_client:{name}]"

    def start(self, timeout=10.0):
        self._spawn_hub_if_needed()
        self.thread = threading.Thread(
            target=self._run, name=f"hub-client-{self.name}", daemon=True
        )
        self.thread.start()
        if not self.registered.wait(timeout):
            print(f"{self._log_prefix} registration timed out "
                  f"after {timeout:.0f}s", file=sys.stderr)

    def stop_now(self):
        """Stop the connection loop. Closes the underlying socket so a
        blocked `read_frame` returns immediately."""
        self.stop.set()
        with self._sock_lock:
            sock = self._sock
            self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def send(self, msg):
        """Send one JSON message over the registration WS.

        Returns True if the frame was written, False otherwise.  The
        launcher uses this to push `launcher_state` and
        `launcher_result`; playgrounds and panels never call it.
        """
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            _ws.write_text_frame(sock, json.dumps(msg))
            return True
        except OSError:
            return False

    # ---- hub spawn ---------------------------------------------------

    def _hub_reachable(self):
        try:
            s = socket.create_connection(
                (HUB_HOST, self.hub_port), timeout=0.5
            )
            s.close()
            return True
        except OSError:
            return False

    def _spawn_hub_if_needed(self):
        if self._hub_reachable():
            return
        time.sleep(0.1)
        if self._hub_reachable():
            return

        pgkit_root = Path(__file__).resolve().parent.parent
        log_path = Path.home() / ".pgkit" / "hub.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(pgkit_root) + (os.pathsep + prev if prev else "")
        )
        try:
            with open(log_path, "a") as log:
                subprocess.Popen(
                    [sys.executable, "-m", "pgkit.hub",
                     "--port", str(self.hub_port)],
                    start_new_session=True,
                    stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=env,
                )
            print(f"{self._log_prefix} spawned hub, log at {log_path}",
                  file=sys.stderr)
        except Exception as e:
            print(f"{self._log_prefix} could not spawn hub: {e}",
                  file=sys.stderr)
            return
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._hub_reachable():
                return
            time.sleep(0.1)
        try:
            tail = log_path.read_text(errors="replace").splitlines()[-10:]
            if tail:
                print(f"{self._log_prefix} hub log tail:", file=sys.stderr)
                for line in tail:
                    print(f"  {line}", file=sys.stderr)
        except OSError:
            pass

    # ---- connection loop ---------------------------------------------

    def _run(self):
        backoff = 1.0
        while not self.stop.is_set():
            try:
                self._connect_and_serve()
                backoff = 1.0
            except Exception as e:
                print(f"{self._log_prefix} {e}", file=sys.stderr)
            if self.stop.is_set():
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _connect_and_serve(self):
        sock = socket.create_connection(
            (HUB_HOST, self.hub_port), timeout=10
        )
        with self._sock_lock:
            self._sock = sock
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            upgrade = (
                f"GET /__hub_ws HTTP/1.1\r\n"
                f"Host: {HUB_HOST}:{self.hub_port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            ).encode("ascii")
            sock.sendall(upgrade)

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("hub closed during handshake")
                response += chunk
            first_line = response.split(b"\r\n", 1)[0]
            if b"101" not in first_line:
                raise ConnectionError(
                    f"hub refused upgrade: {first_line!r}"
                )

            hello = {
                "type": "hello",
                "role": self.role,
                "name": self.name,
                "port": self.port,
                "pid":  os.getpid(),
            }
            if self.html_file:
                hello["html_file"] = self.html_file
            if self.group:
                hello["group"] = self.group
            _ws.write_text_frame(sock, json.dumps(hello))

            sock.settimeout(None)
            while not self.stop.is_set():
                opcode, payload = _ws.read_frame(sock)
                if opcode is None or opcode == 0x08:
                    raise ConnectionError("hub closed connection")
                if opcode != 0x01:
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                if not self._handle(msg, sock):
                    return
        finally:
            with self._sock_lock:
                if self._sock is sock:
                    self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _handle(self, msg, sock):
        t = msg.get("type")
        if t == "hello_ack":
            self.registered.set()
            print(f"{self._log_prefix} registered "
                  f"(role={self.role}, hub {HUB_HOST}:{self.hub_port})",
                  file=sys.stderr)
            return True
        if t == "shutdown":
            try:
                _ws.write_text_frame(
                    sock,
                    json.dumps({"type": "shutdown_ack",
                                "request_id": msg.get("request_id", "")}),
                )
            except OSError:
                pass
            threading.Timer(0.2, lambda: os._exit(0)).start()
            self.stop.set()
            return False
        if self.message_handler is not None:
            try:
                self.message_handler(msg)
            except Exception as e:
                print(f"{self._log_prefix} message_handler error: {e}",
                      file=sys.stderr)
        return True
