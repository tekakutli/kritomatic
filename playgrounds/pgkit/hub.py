"""pgkit.hub — multi-playground hub server.

    python -m pgkit.hub                  # start manually
    python -m pgkit.hub --shutdown       # ask a running hub to exit

A single HTTP server (default 127.0.0.1:8780) that:

    · accepts WebSocket registrations from playgrounds, panels, and
      launchers on  /__hub_ws
    · proxies  /<name>/*  to  localhost:<registered_port>/*  for both
      HTTP and WebSocket upgrades
    · accepts  GET /__playgrounds  for one-shot CLI listings
    · exposes cooperative shutdown and (opt-in) hard kill over the
      management WebSocket, never over HTTP polling
    · broadcasts a `playgrounds` event to every connected panel
      whenever the registered set changes — no polling anywhere

REGISTRATION IDENTITY
=====================
A `Registration` records the WS socket that created it. `deregister`
takes an optional `sock`: when it matches the current registration's
socket, the registration is removed; when it doesn't, the caller is
an old handler noticing its socket died *after* a newer connection
already took the name, and the removal is a no-op.

GROUPS
======
A registration may carry an optional `group` string (set by
`pgkit.hub_client.ensure_running(group=...)`).  Groups are metadata,
not routing: proxying, `/__playgrounds`, and target selection all
treat each registration independently.  The group is consulted only
by `_kill_playground`, which enriches the kill_result message to
name the group and its member count.

LAUNCHERS
=========
A launcher opens an additional WS with `role="launcher"` and the
same `group` as its playgrounds.  This registration's purpose is to
carry per-group control commands.  It carries an `available` field
(a list of playground names the launcher can start); the field is
pushed by the launcher via `launcher_state` messages and re-broadcast
to panels as part of every `playgrounds` event.

Panels send `launcher_start` / `launcher_stop` / `launcher_restart`
messages over their own management WS; the hub finds the launcher
whose `available` contains the target name and forwards the message.
The launcher replies with `launcher_result`, which the hub broadcasts
to every panel.

Killing any *playground* in a group still shuts down the whole group
(the cooperative `shutdown` message reaches the launcher process,
which exits).  The new launcher_* commands are the way to stop or
start an individual member without touching the rest.

PANELS
======
Two kinds of clients connect with `role="panel"`:

    Registered panels
        The Python-side HubConnection of a running remote_panel
        process. It carries a `name`, `port`, `pid`, and `html_file`
        in its hello frame, and becomes a first-class entry in
        `registrations` with `role="panel"`.

    Listener panels
        The browser-side management WebSocket that
        `remote_panel/rp_client.py` opens. It carries no name and no
        port, only `role="panel"`. It is not registered; it joins
        `panel_socks` for `playgrounds` broadcasts and can send
        `kill`, `shutdown_hub`, and `launcher_*` commands.

ABSOLUTE-PATH FETCHES FROM PANELS
=================================
Panel JS uses `fetch("/krita/import")` and similar absolute paths.
Under the hub, the panel page is at `/<name>/<name>_playground.html`,
but an absolute-path fetch resolves against the hub root.  The
resolver therefore has two rules:

    1. Explicit prefix: `/<registered-name>/<rest>` → forward to
       `/<rest>` on that playground.
    2. Referer fallback: first segment is not a playground, but the
       Referer's first segment is → forward unchanged.

See `Handler._resolve_proxy_target`.
"""

import argparse
import json
import os
import select
import signal
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import ws as _ws


HUB_HOST = "127.0.0.1"
HUB_PORT = 8780


# ============================================================================
#  State
# ============================================================================

class Registration:
    """One peer's registration, held while its WS is open.

    Fields:
        name, port, role, pid, sock, html_file, group,
        connected_at, send_lock

    `available` is only meaningful for role="launcher"; it is the
    list of playground names the launcher can start.  For any other
    role it is None.
    """

    def __init__(self, name, port, role, pid, sock, html_file=None,
                 group=None, available=None):
        self.name = name
        self.port = port
        self.role = role
        self.pid = pid
        self.sock = sock
        self.html_file = html_file
        self.group = group
        self.available = available
        self.connected_at = time.time()
        self.send_lock = threading.Lock()

    def send(self, msg):
        data = json.dumps(msg)
        with self.send_lock:
            _ws.write_text_frame(self.sock, data)

    def to_json(self):
        d = {
            "name":         self.name,
            "port":         self.port,
            "role":         self.role,
            "pid":          self.pid,
            "html_file":    self.html_file,
            "group":        self.group,
            "connected_at": self.connected_at,
        }
        if self.role == "launcher":
            d["available"] = list(self.available or [])
        return d


class Hub:
    def __init__(self):
        self.registrations = {}
        self.panel_socks = set()
        self.lock = threading.Lock()

    def register(self, reg):
        with self.lock:
            old = self.registrations.get(reg.name)
            self.registrations[reg.name] = reg
        if old is not None and old is not reg:
            try:
                old.sock.close()
            except OSError:
                pass
        self.broadcast_playgrounds()

    def deregister(self, name, sock=None):
        with self.lock:
            reg = self.registrations.get(name)
            if reg is None:
                return
            if sock is not None and reg.sock is not sock:
                return
            self.registrations.pop(name, None)
        self.broadcast_playgrounds()

    def find(self, name):
        with self.lock:
            return self.registrations.get(name)

    def has(self, name):
        with self.lock:
            return name in self.registrations

    def find_launcher_for(self, target):
        """Return the launcher whose `available` contains `target`."""
        with self.lock:
            for r in self.registrations.values():
                if r.role == "launcher" and target in (r.available or []):
                    return r
        return None

    def add_panel(self, sock):
        with self.lock:
            self.panel_socks.add(sock)
        try:
            _ws.write_text_frame(sock, json.dumps(self._playgrounds_msg()))
        except OSError:
            self.remove_panel(sock)

    def remove_panel(self, sock):
        with self.lock:
            self.panel_socks.discard(sock)

    def _playgrounds_msg(self):
        with self.lock:
            regs = [r.to_json() for r in self.registrations.values()]
        return {"type": "playgrounds", "playgrounds": regs}

    def broadcast_playgrounds(self):
        msg = self._playgrounds_msg()
        data = json.dumps(msg)
        with self.lock:
            socks = list(self.panel_socks)
        dead = []
        for sock in socks:
            try:
                _ws.write_text_frame(sock, data)
            except OSError:
                dead.append(sock)
        for sock in dead:
            self.remove_panel(sock)

    def broadcast_to_panels(self, msg):
        """Send one message to every connected listener panel."""
        data = json.dumps(msg)
        with self.lock:
            socks = list(self.panel_socks)
        dead = []
        for sock in socks:
            try:
                _ws.write_text_frame(sock, data)
            except OSError:
                dead.append(sock)
        for sock in dead:
            self.remove_panel(sock)


_HUB = Hub()


# ============================================================================
#  PID verification for hard kill
# ============================================================================

def _verify_pid(pid, name):
    if not pid:
        return False
    proc = Path(f"/proc/{pid}/cmdline")
    if proc.exists():
        try:
            cmd = proc.read_bytes() \
                       .replace(b"\x00", b" ") \
                       .decode(errors="replace")
            return (name in cmd) or ("playground" in cmd)
        except OSError:
            return True
    try:
        import subprocess
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True, timeout=2,
        )
        return (name in out) or ("playground" in out)
    except Exception:
        return True


# ============================================================================
#  HTTP / WS handler
# ============================================================================

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---- dispatch -----------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path
        is_ws = self.headers.get("Upgrade", "").lower() == "websocket"

        if not is_ws and path == "/__playgrounds":
            self._send_json(_HUB._playgrounds_msg())
            return

        if is_ws and path == "/__hub_ws":
            self._serve_hub_ws()
            return

        if is_ws:
            self._proxy_ws(path)
            return

        self._proxy_http(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/__shutdown":
            self._send_json({"success": True})
            threading.Timer(0.2, _shutdown_hub).start()
            return
        self._proxy_http(path)

    def do_PUT(self):    self._proxy_http(urlparse(self.path).path)
    def do_DELETE(self): self._proxy_http(urlparse(self.path).path)

    # ---- helpers ------------------------------------------------------

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _split_name(self, path):
        parts = path.lstrip("/").split("/", 1)
        name = parts[0] if parts else ""
        rest = "/" + parts[1] if len(parts) > 1 else "/"
        return name, rest

    def _resolve_proxy_target(self, path):
        name, rest = self._split_name(path)
        if name and _HUB.has(name):
            return name, rest
        if name and not name.startswith("__"):
            referer = self.headers.get("Referer", "") or ""
            if referer:
                try:
                    ref_path = urlparse(referer).path
                    ref_parts = ref_path.lstrip("/").split("/", 1)
                    ref_name = ref_parts[0] if ref_parts else ""
                    if ref_name and _HUB.has(ref_name):
                        return ref_name, path
                except Exception:
                    pass
        return None

    def _unknown_target_json(self, path):
        name, _ = self._split_name(path)
        self._send_json(
            {"success": False, "message": f"unknown playground: {name}"},
            404,
        )

    # ---- management WS -----------------------------------------------

    def _serve_hub_ws(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return

        self.wfile.write(_ws.handshake_response(key))
        self.wfile.flush()

        sock = self.connection
        sock.settimeout(None)

        role = None
        registered_name = None

        try:
            opcode, payload = _ws.read_frame(sock)
            if opcode != 0x01:
                return
            try:
                hello = json.loads(payload.decode("utf-8"))
            except Exception:
                return

            role = hello.get("role")
            name = hello.get("name")
            port = int(hello.get("port") or 0)
            pid  = int(hello.get("pid") or 0)
            html_file = hello.get("html_file") or None
            group     = hello.get("group") or None

            if role == "playground":
                if not name or not port:
                    return
                reg = Registration(name, port, role, pid, sock,
                                   html_file=html_file, group=group)
                _HUB.register(reg)
                registered_name = name
                try:
                    reg.send({"type": "hello_ack", "name": name})
                except OSError:
                    return
                self._read_playground_loop(sock, name)

            elif role == "launcher":
                if not name:
                    return
                reg = Registration(name, port, role, pid, sock,
                                   html_file=html_file, group=group,
                                   available=[])
                _HUB.register(reg)
                registered_name = name
                try:
                    reg.send({"type": "hello_ack", "name": name})
                except OSError:
                    return
                self._read_launcher_loop(sock, name)

            elif role == "panel":
                if name and port:
                    reg = Registration(name, port, role, pid, sock,
                                       html_file=html_file, group=group)
                    _HUB.register(reg)
                    registered_name = name
                    try:
                        reg.send({"type": "hello_ack", "name": name})
                    except OSError:
                        return
                    self._read_playground_loop(sock, name)
                else:
                    _HUB.add_panel(sock)
                    self._read_panel_loop(sock)

        except Exception:
            pass
        finally:
            if registered_name:
                _HUB.deregister(registered_name, sock=sock)
            elif role == "panel":
                _HUB.remove_panel(sock)
            try:
                sock.close()
            except OSError:
                pass

    def _read_playground_loop(self, sock, name):
        while True:
            try:
                opcode, _payload = _ws.read_frame(sock)
            except OSError:
                return
            if opcode is None or opcode == 0x08:
                return

    def _read_launcher_loop(self, sock, name):
        while True:
            try:
                opcode, payload = _ws.read_frame(sock)
            except OSError:
                return
            if opcode is None or opcode == 0x08:
                return
            if opcode != 0x01:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            t = msg.get("type")
            if t == "launcher_state":
                reg = _HUB.find(name)
                if reg is None or reg.sock is not sock:
                    return
                with _HUB.lock:
                    reg.available = list(msg.get("available") or [])
                _HUB.broadcast_playgrounds()
            elif t == "launcher_result":
                _HUB.broadcast_to_panels(msg)

    def _read_panel_loop(self, sock):
        while True:
            try:
                opcode, payload = _ws.read_frame(sock)
            except OSError:
                return
            if opcode is None or opcode == 0x08:
                return
            if opcode != 0x01:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue

            t = msg.get("type")

            if t == "kill":
                target = msg.get("name") or ""
                hard   = bool(msg.get("hard", False))
                result = _kill_playground(target, hard)
                try:
                    _ws.write_text_frame(
                        sock,
                        json.dumps({
                            "type":   "kill_result",
                            "name":   target,
                            **result,
                        }),
                    )
                except OSError:
                    return

            elif t == "shutdown_hub":
                try:
                    _ws.write_text_frame(
                        sock, json.dumps({"type": "shutdown_ack"})
                    )
                except OSError:
                    pass
                threading.Timer(0.5, _shutdown_hub).start()
                return

            elif t in ("launcher_start", "launcher_stop", "launcher_restart"):
                target = msg.get("name") or ""
                request_id = msg.get("request_id") or ""
                launcher = _HUB.find_launcher_for(target)
                if launcher is None:
                    try:
                        _ws.write_text_frame(sock, json.dumps({
                            "type": "launcher_result",
                            "request_id": request_id,
                            "name": target,
                            "success": False,
                            "message": (f"no launcher manages {target!r} "
                                        f"(is the playground running "
                                        f"standalone?)"),
                        }))
                    except OSError:
                        return
                    continue
                try:
                    launcher.send({
                        "type": t,
                        "name": target,
                        "request_id": request_id,
                    })
                except OSError:
                    try:
                        _ws.write_text_frame(sock, json.dumps({
                            "type": "launcher_result",
                            "request_id": request_id,
                            "name": target,
                            "success": False,
                            "message": "launcher unreachable",
                        }))
                    except OSError:
                        return

    # ---- HTTP proxy --------------------------------------------------

    def _proxy_http(self, path):
        parsed = urlparse(self.path)
        query = parsed.query
        resolved = self._resolve_proxy_target(path)
        if resolved is None:
            self._unknown_target_json(path)
            return
        name, forward_path = resolved
        reg = _HUB.find(name)
        if reg is None:
            self._unknown_target_json(path)
            return

        try:
            backend = socket.create_connection(
                (HUB_HOST, reg.port), timeout=10
            )
        except OSError as e:
            self._send_json(
                {"success": False,
                 "message": f"playground {name} unreachable: {e}"},
                502,
            )
            return

        try:
            headers = dict(self.headers.items())
            headers["Host"] = f"{HUB_HOST}:{reg.port}"
            headers["Connection"] = "close"
            forward_with_query = (
                forward_path + ("?" + query if query else "")
            )
            req = f"{self.command} {forward_with_query} HTTP/1.1\r\n"
            for k, v in headers.items():
                req += f"{k}: {v}\r\n"
            req += "\r\n"
            backend.sendall(req.encode("utf-8"))

            content_len = int(self.headers.get("Content-Length", "0") or "0")
            if content_len > 0:
                remaining = content_len
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    backend.sendall(chunk)
                    remaining -= len(chunk)

            while True:
                chunk = backend.recv(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            try:
                backend.close()
            except OSError:
                pass
            self.close_connection = True

    # ---- WS proxy ----------------------------------------------------

    def _proxy_ws(self, path):
        parsed = urlparse(self.path)
        query = parsed.query
        resolved = self._resolve_proxy_target(path)
        if resolved is None:
            self.send_error(404, f"unknown playground: {path}")
            return
        name, forward_path = resolved
        reg = _HUB.find(name)
        if reg is None:
            self.send_error(404, f"unknown playground: {name}")
            return

        try:
            backend = socket.create_connection(
                (HUB_HOST, reg.port), timeout=10
            )
        except OSError as e:
            self.send_error(502, f"playground {name} unreachable: {e}")
            return

        try:
            headers = dict(self.headers.items())
            headers["Host"] = f"{HUB_HOST}:{reg.port}"
            forward_with_query = (
                forward_path + ("?" + query if query else "")
            )
            req = f"GET {forward_with_query} HTTP/1.1\r\n"
            for k, v in headers.items():
                req += f"{k}: {v}\r\n"
            req += "\r\n"
            backend.sendall(req.encode("utf-8"))

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = backend.recv(4096)
                if not chunk:
                    return
                response += chunk

            self.connection.sendall(response)

            client = self.connection
            client.setblocking(False)
            backend.setblocking(False)

            while True:
                try:
                    r, _, _ = select.select([client, backend], [], [], None)
                except (OSError, ValueError):
                    return
                for s in r:
                    try:
                        data = s.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    other = backend if s is client else client
                    try:
                        other.sendall(data)
                    except OSError:
                        return
        except Exception:
            pass
        finally:
            try:
                backend.close()
            except OSError:
                pass
            self.close_connection = True


# ============================================================================
#  Kill
# ============================================================================

def _kill_playground(name, hard):
    reg = _HUB.find(name)
    if reg is None:
        return {"success": False, "message": f"no such playground: {name}"}

    if reg.role == "panel":
        return {"success": False,
                "message": f"{name} is a panel; "
                           f"kill the PID from the shell if it hangs"}

    group = reg.group
    group_count = 0
    group_msg = ""
    if group is not None:
        with _HUB.lock:
            group_count = sum(
                1 for r in _HUB.registrations.values()
                if r.group == group
            )
        group_msg = f" (group '{group}', {group_count} playgrounds)"

    def _ok(verb):
        if group is not None:
            return {"success": True,
                    "message": f"{verb} group '{group}' "
                               f"({group_count} playgrounds)"}
        return {"success": True, "message": f"{name} {verb}"}

    try:
        reg.send({"type": "shutdown", "request_id": uuid.uuid4().hex})
    except OSError as e:
        if hard and reg.pid and _verify_pid(reg.pid, name):
            try:
                os.kill(reg.pid, signal.SIGTERM)
                time.sleep(0.5)
                _HUB.deregister(name)
                return _ok("force-killed")
            except OSError as e2:
                return {"success": False,
                        "message": f"send failed: {e}; hard failed: {e2}"}
        return {"success": False, "message": f"send failed: {e}"}

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _HUB.has(name):
            return _ok("shut down")
        time.sleep(0.1)

    if hard and reg.pid and _verify_pid(reg.pid, name):
        try:
            os.kill(reg.pid, signal.SIGTERM)
            time.sleep(0.5)
            if _HUB.has(name):
                _HUB.deregister(name)
            return _ok("force-killed")
        except OSError as e:
            return {"success": False, "message": f"hard kill failed: {e}"}

    return {"success": False,
            "message": f"{name}{group_msg} did not shut down within 5s "
                       f"(use hard=1 to escalate)"}


def _shutdown_hub():
    with _HUB.lock:
        regs = list(_HUB.registrations.values())
        panels = list(_HUB.panel_socks)
    for reg in regs:
        try:
            reg.send({"type": "shutdown", "request_id": uuid.uuid4().hex})
        except OSError:
            pass
    for sock in panels:
        try:
            sock.close()
        except OSError:
            pass
    time.sleep(0.5)
    os._exit(0)


# ============================================================================
#  Entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(prog="pgkit.hub")
    parser.add_argument("--host", default=HUB_HOST)
    parser.add_argument("--port", type=int, default=HUB_PORT)
    parser.add_argument("--shutdown", action="store_true",
                        help="Ask a running hub to exit.")
    args = parser.parse_args()

    if args.shutdown:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://{args.host}:{args.port}/__shutdown",
                method="POST", data=b"{}",
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"Hub on {args.host}:{args.port} shutdown requested.")
        except Exception as e:
            print(f"No hub on {args.host}:{args.port} ({e}).")
        return

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"pgkit hub listening on http://{args.host}:{args.port}")
    print(f"  registrations : ws://{args.host}:{args.port}/__hub_ws")
    print(f"  playgrounds   : http://{args.host}:{args.port}/__playgrounds")
    print(f"  proxied       : /<name>/* → localhost:<port>/*")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
