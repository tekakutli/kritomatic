import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ExportServer:
    """
    Local HTTP server.  Two roles:

      /events   SSE stream — arms/disarms the extension as --watch
                state changes.  One-shot: on arm the extension fires
                immediately if the user is on a deepseek tab, or on
                the next navigation to one.

      /export   POST endpoint — the extension pushes the extracted
                payload.  Consumed exactly once by wait_for_result().

      /status   plain JSON for debugging.

    The /poll endpoint used by earlier revisions is gone — the
    extension now opens a persistent EventSource against /events,
    which eliminates the up-to-3-second polling delay and correctly
    handles the "extension was already connected when the CLI
    started" case (the SSE handler pushes "arm" on connect if the
    server is armed).
    """

    def __init__(self, port: int = 8765, watch_mode: bool = False):
        self.port = port
        self.watch_mode = watch_mode
        self.result_queue: "queue.Queue[dict]" = queue.Queue()

        # armed = "watch mode wants a capture"; flips False on first
        # successful /export and stays False until this CLI exits.
        self.armed = watch_mode

        # One Queue per connected EventSource client.  Broadcasts
        # append to every queue; the /events handler drains its own.
        self.sse_clients: list["queue.Queue[dict]"] = []

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._httpd = None
        self._thread = None

    # ---- SSE plumbing ---------------------------------------------

    def _add_sse_client(self) -> "queue.Queue[dict]":
        q: "queue.Queue[dict]" = queue.Queue(maxsize=100)
        with self._lock:
            self.sse_clients.append(q)
            armed_now = self.armed
        # If the server is already armed when a client connects (e.g.
        # the extension reloaded, or the CLI started before the
        # extension's EventSource reconnected), push arm immediately.
        # This is what makes the "extension opens after CLI starts"
        # case work without polling.
        if armed_now:
            q.put_nowait({"event": "arm"})
        return q

    def _remove_sse_client(self, q: "queue.Queue[dict]") -> None:
        with self._lock:
            try:
                self.sse_clients.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event_name: str) -> None:
        with self._lock:
            clients = list(self.sse_clients)
        for q in clients:
            try:
                q.put_nowait({"event": event_name})
            except queue.Full:
                # Client is not draining — likely a stale connection
                # that hasn't been reaped yet.  Drop the event for
                # this client; the next heartbeat write will discover
                # the disconnect and remove it.
                pass

    # ---- lifecycle ------------------------------------------------

    def start(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

            def _send_json(self, code: int, obj: dict) -> None:
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods",
                                 "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers",
                                 "Content-Type")
                self.end_headers()

            def do_GET(self):
                if self.path == "/events":
                    self._handle_events()
                elif self.path == "/status":
                    with parent._lock:
                        armed = parent.armed
                        n_clients = len(parent.sse_clients)
                    self._send_json(200, {
                        "ok": True,
                        "armed": armed,
                        "sse_clients": n_clients,
                    })
                else:
                    self._send_json(404, {"error": "not found"})

            def _handle_events(self):
                q = parent._add_sse_client()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    last_heartbeat = time.monotonic()
                    HEARTBEAT_INTERVAL_S = 15

                    while not parent._stop_event.is_set():
                        try:
                            msg = q.get(timeout=1.0)
                            event_name = msg.get("event", "message")
                            self.wfile.write(
                                f"event: {event_name}\ndata: {{}}\n\n"
                                .encode("utf-8")
                            )
                            self.wfile.flush()
                        except queue.Empty:
                            pass

                        now = time.monotonic()
                        if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                            try:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                last_heartbeat = now
                            except (BrokenPipeError, ConnectionResetError,
                                    OSError):
                                break
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    parent._remove_sse_client(q)

            def do_POST(self):
                if self.path == "/export":
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw)
                    except Exception as e:
                        self._send_json(400, {"error": f"bad json: {e}"})
                        return
                    with parent._lock:
                        was_armed = parent.armed
                        parent.armed = False
                    parent.result_queue.put(data)
                    if was_armed:
                        # Broadcast after releasing the lock — no
                        # deadlock with any client reconnecting.
                        parent._broadcast("disarm")
                    self._send_json(200, {"ok": True})
                else:
                    self._send_json(404, {"error": "not found"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        # Handlers block on SSE streaming — daemonize so process exit
        # doesn't wait for them.
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._thread.start()

    def wait_for_result(self, timeout=None) -> dict:
        return self.result_queue.get(timeout=timeout)

    def stop(self) -> None:
        self._stop_event.set()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None
