"""
kritomatic_daemon.py — plugin entry point and startup orchestration.

Wires the command server, the event server, and the ComfyUI
auto-pull together, and holds the handler box that reload_daemon
swaps out.

AUTO-PULL AT STARTUP
====================
Krita cannot know whether ComfyUI will be running before the user
starts it.  The auto-pull thread therefore tries to reach ComfyUI
at startup with a bounded retry schedule, restores whatever
workflow is currently published in ComfyUI's browser, and gives up
if nothing connects within the schedule.

The schedule is 30 attempts at 2-second intervals — up to 60
seconds of trying — which covers the common case where a user opens
Krita first and ComfyUI a moment later.  It does not retry forever:
a user who never starts ComfyUI should not accumulate an unbounded
backlog of connection attempts.

After the auto-pull returns (success or give-up), the ComfyUI
client's own watch loop takes over.  That loop is inside
comfyui_client.py and has its own reconnect policy; it is not
governed by anything here.  A connection that was established at
startup and later lost is handled by that loop, not by the code in
this file.

STDOUT DISCIPLINE
=================
A failure loop that logs every attempt produces 30 identical lines
in the Krita terminal when ComfyUI is simply not running.  That is
noise, not information, and it makes a normal startup look like a
stuck daemon.

The auto-pull therefore logs only on transitions:

    - when the failure mode first appears: the message once, plus
      one line announcing the retry window, so the user knows the
      silence that follows is deliberate;

    - when the failure mode *changes* (a different message than the
      one currently being suppressed): the new message once, so a
      distinct problem is visible;

    - on success: one line naming the restored workflow, or one
      line noting that no workflow was replayed;

    - on give-up: one line noting that the schedule expired.

The same failure repeated 29 times produces one line, not 29.

Typical output on a fail-out with ComfyUI absent:

    [kritomatic] auto-pull: connecting to ComfyUI
    [kritomatic] auto-pull: Could not reach ComfyUI at 127.0.0.1:8188: HTTP GET http://127.0.0.1:8188/object_info: [Errno 111] Connection refused
    [kritomatic] auto-pull: will keep retrying silently for up to 60s, then give up
    ...
    [kritomatic] auto-pull: gave up after 30 attempts (60s)

Typical output when ComfyUI comes up mid-retry:

    [kritomatic] auto-pull: connecting to ComfyUI
    [kritomatic] auto-pull: Could not reach ComfyUI at 127.0.0.1:8188: ...
    [kritomatic] auto-pull: will keep retrying silently for up to 60s, then give up
    ...
    [kritomatic] auto-pull: restored ComfyUI Web (ComfyUI Web, sig=...)

Four lines instead of thirty-two.  The intervening silence is
meaningful — it is what "we are retrying" looks like.
"""

from krita import *
from PyQt6.QtCore import QObject
from .server.socket_server import WebSocketServer
from .server.event_server import EventServer
from .handlers.base import CommandHandler
from .decorators import command
from .registry import get_command_registry
import threading
import time
import traceback


class _HandlerBox(QObject):
    """
    Container for the current command handler, pinned to the GUI thread.

    Because this is a QObject created during plugin load (on the main
    thread), Qt resolves the connection from the server's
    `command_received` signal as a QueuedConnection when the signal is
    emitted from a client-receiver thread. `dispatch` therefore runs on
    the main thread.

    From the main thread, `dispatch` decides which thread the handler
    itself runs on:

      * Non-comfy batches (and every single non-comfy command) call
        `handle_command` synchronously, so all of their Krita API calls
        happen on the main thread. This is required for Krita's vector
        shapes: an inline-wrapped text shape owns a QTextDocument
        internally, and creating, modifying, or serialising one from a
        non-GUI thread races Krita's own repaint pass and segfaults the
        process (a QTextDocument thread-affinity violation, observed as
        "Parent is QTextDocument(...), parent's thread is ...,
        current thread is ..." followed by SIGSEGV in `doc.saveAs` or
        during the next composite).

      * Comfy batches spawn a worker thread as before. They spend
        almost all their time in network I/O and would freeze the GUI
        otherwise; the Krita calls they do make (paint layers,
        setPixelData, refreshProjection) do not touch text shapes, so
        they remain safe from a worker thread.

    Reloading still swaps `box.handler` in place, so the signal
    connection is never touched.
    """

    def __init__(self, handler):
        super().__init__()
        self.handler = handler

    def dispatch(self, command, client_socket):
        h = self.handler
        if h is None:
            return
        if self._needs_worker_thread(command):
            threading.Thread(
                target=h.handle_command,
                args=(command, client_socket),
                name="kritomatic-handler",
                daemon=True,
            ).start()
        else:
            h.handle_command(command, client_socket)

    @staticmethod
    def _needs_worker_thread(command):
        """True iff the batch contains a `comfy_*` command."""
        if not isinstance(command, dict):
            return False
        cmds = command.get("commands")
        if isinstance(cmds, list):
            return any(
                isinstance(c, dict)
                and str(c.get("type", "")).startswith("comfy_")
                for c in cmds
            )
        return str(command.get("type", "")).startswith("comfy_")


class KritomaticDaemon(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.event_server = None
        self.handler_box = _HandlerBox(CommandHandler())
        self._auto_pull_started = False

    def setup(self):
        pass

    def createActions(self, window):
        self.start_servers()

    def start_servers(self):
        if not self.server:
            self.server = WebSocketServer()
            self.server.command_received.connect(self.handler_box.dispatch)
            self.server.start()
            print("✓ Kritomatic Daemon loaded")

        if not self.event_server:
            self.event_server = EventServer(
                port=12347,
                state_provider=self._current_status,
                panel_provider=self._current_panel,
            )
            self.event_server.start()

        if not self._auto_pull_started:
            self._auto_pull_started = True
            threading.Thread(
                target=self._auto_pull_on_startup,
                name="kritomatic-autopull",
                daemon=True,
            ).start()

    def _auto_pull_on_startup(self):
        """Pull the currently-published browser workflow, once, at startup.

        30 attempts at 2s intervals (plus the 1s startup delay)
        covers "user starts ComfyUI within ~60 seconds of Krita".
        After a successful subscribe the method waits up to 5s for
        the replay to actually reach `_active_workflow_name`,
        because the replay is asynchronous and can be slow under
        load.

        Logging is transition-based — see the module docstring,
        STDOUT DISCIPLINE, for the exact reporting contract.  The
        state that makes it work is `last_failure`: the failure
        message of the last attempt whose log line was emitted.
        Every attempt still *runs* (the schedule is unchanged); only
        the printing is suppressed when the message repeats.
        """
        time.sleep(1.0)

        handler = self.handler_box.handler
        if handler is None:
            return
        comfy = None
        try:
            comfy = handler.handlers.get('comfyui')
        except Exception:
            pass
        if comfy is None:
            return

        MAX_ATTEMPTS   = 30
        RETRY_DELAY_S  = 2.0
        give_up_after_s = int(MAX_ATTEMPTS * RETRY_DELAY_S)

        print("[kritomatic] auto-pull: connecting to ComfyUI")

        # Message of the last failure we logged.  `None` means "no
        # failure has been logged yet".  A new failure message that
        # differs from this is a transition and gets its own log
        # line; an identical repeat is suppressed.
        last_failure = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                result = comfy.comfy_connect({
                    "watch": True,
                    "check": True,
                    "subscribe_timeout": 5.0,
                })
            except Exception as e:
                # comfy_connect normally catches its own errors and
                # returns a result dict.  If it ever raises, fold the
                # exception into the same failure path so the logging
                # state machine does not need to know which branch
                # raised.
                result = {
                    "success": False,
                    "message": f"connect raised: {e}",
                }

            if (result.get("success")
                    and (result.get("data") or {}).get("subscribed", False)):
                for _ in range(5):
                    time.sleep(1.0)
                    status = comfy.comfy_status({})
                    status_data = (status.get("data") or {}) \
                        if status.get("success") else {}
                    workflow = status_data.get("workflow")
                    if workflow:
                        print(f"[kritomatic] auto-pull: restored "
                              f"{workflow}")
                        return
                print("[kritomatic] auto-pull: subscribed, "
                      "no workflow replayed "
                      "(no browser publish cached in ComfyUI)")
                return

            msg = result.get("message", "unknown")

            if last_failure != msg:
                print(f"[kritomatic] auto-pull: {msg}")
                if last_failure is None:
                    # First failure.  Announce the retry window once
                    # so the subsequent silence reads as "still
                    # trying", not "the daemon hung".
                    print(f"[kritomatic] auto-pull: will keep retrying "
                          f"silently for up to {give_up_after_s}s, "
                          f"then give up")
                last_failure = msg

            time.sleep(RETRY_DELAY_S)

        print(f"[kritomatic] auto-pull: gave up after {MAX_ATTEMPTS} "
              f"attempts ({give_up_after_s}s)")

    def _current_status(self):
        handler = self.handler_box.handler
        if handler is None:
            return None
        try:
            result = handler.execute('comfy_status', {})
            if isinstance(result, dict) and result.get('success'):
                return result.get('data', {})
        except Exception:
            pass
        return None

    def _current_panel(self):
        handler = self.handler_box.handler
        if handler is None:
            return None
        try:
            result = handler.execute('panel_description', {})
            if isinstance(result, dict) and result.get('success'):
                return result.get('data', {})
        except Exception:
            pass
        return None
