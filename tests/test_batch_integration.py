"""
Integration tests for the batch bundling path against a live daemon.

These tests connect to a real Kritomatic daemon running inside Krita
(localhost:12346). If no daemon is reachable, the entire test class is
skipped — so `python -m unittest discover` stays green on machines that
don't have Krita running.

What they verify, end to end:
  - the daemon accepts a bundled `{"id": ..., "commands": [...]}` payload
  - the client receives `progress` lines BEFORE the final summary
  - the final summary is `type: batch_complete` with correct counts
  - the single-command path still returns a normal (non-batch) response

They use read-only commands only (get_state, list_layers, etc.), so
running them against a live Krita will not mutate the active document.
"""

import socket as _socket
import sys
import unittest
from pathlib import Path

# Make `src/kritomatic` importable regardless of which Python runs this
# test.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.client import KritaClient


DAEMON_HOST = 'localhost'
DAEMON_PORT = 12346

# Read-only commands. These inspect state without changing anything.
SAFE_COMMANDS = [
    {'type': 'get_state'},
    {'type': 'list_layers'},
    {'type': 'list_brush_presets'},
]


def _daemon_reachable(host=DAEMON_HOST, port=DAEMON_PORT, timeout=0.5):
    """One-shot TCP probe. Returns True iff something is listening."""
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


class TestBatchIntegration(unittest.TestCase):
    """
    Real-socket tests against a running daemon.

    Skipped automatically when the daemon isn't reachable.
    """

    @classmethod
    def setUpClass(cls):
        if not _daemon_reachable():
            raise unittest.SkipTest(
                f"No Kritomatic daemon reachable at {DAEMON_HOST}:{DAEMON_PORT}. "
                f"Start Krita with the plugin enabled to run these tests."
            )

    def setUp(self):
        self.client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
        if not self.client.connect():
            self.skipTest("Failed to open a connection to the daemon")

    def tearDown(self):
        if self.client:
            self.client.close()

    # ------------------------------------------------------------------
    # Bundled batch: one wire message in, streamed progress out
    # ------------------------------------------------------------------

    def test_bundled_batch_returns_batch_complete(self):
        """
        A single bundled payload must produce a final response of type
        `batch_complete` with correct totals. If the daemon were still
        running the old code, or the client were still sending
        single commands, this shape would not appear.
        """
        payload = {
            'id': 'integration-test',
            'commands': SAFE_COMMANDS,
        }

        response = self.client.send_batch(payload)

        self.assertIsNotNone(response, "No response from daemon")
        self.assertEqual(
            response.get('type'), 'batch_complete',
            f"Expected batch_complete, got: {response!r}"
        )
        self.assertEqual(response.get('id'), 'integration-test')
        self.assertEqual(response.get('total'), len(SAFE_COMMANDS))
        self.assertEqual(
            response.get('successful'), len(SAFE_COMMANDS),
            f"Not all safe commands succeeded: {response!r}"
        )
        self.assertEqual(response.get('failed'), 0)

    def test_progress_lines_arrive_before_summary(self):
        """
        Each command must produce one `progress` callback as it
        dispatches, before the final `batch_complete` is returned.
        """
        received = []

        def on_progress(index, result):
            received.append((index, result))

        payload = {
            'id': 'integration-stream',
            'commands': SAFE_COMMANDS,
        }

        summary = self.client.send_batch(payload, on_progress=on_progress)

        self.assertIsNotNone(summary)
        self.assertEqual(summary.get('type'), 'batch_complete')

        self.assertEqual(
            len(received), len(SAFE_COMMANDS),
            f"Expected {len(SAFE_COMMANDS)} progress callbacks, got {len(received)}"
        )

        # Indices must be 0..N-1, in order.
        indices = [idx for idx, _ in received]
        self.assertEqual(indices, list(range(len(SAFE_COMMANDS))))

        # Every streamed result must carry the fields the executor needs.
        for idx, result in received:
            self.assertIn('command', result)
            self.assertIn('status', result)
            self.assertEqual(result['status'], 'success')

    def test_daemon_processes_every_command_in_order(self):
        """
        The final summary's results array must line up with the payload,
        command for command. This catches reordering or dropping bugs.
        """
        payload = {
            'id': 'integration-order',
            'commands': SAFE_COMMANDS,
        }

        response = self.client.send_batch(payload)
        self.assertIsNotNone(response)

        results = response.get('results', [])
        self.assertEqual(len(results), len(SAFE_COMMANDS))

        for i, entry in enumerate(results):
            self.assertEqual(entry.get('index'), i)
            self.assertEqual(entry.get('command'), SAFE_COMMANDS[i]['type'])
            self.assertEqual(entry.get('status'), 'success')

    # ------------------------------------------------------------------
    # Single-command path is untouched
    # ------------------------------------------------------------------

    def test_single_command_response_shape_is_unchanged(self):
        """
        `client.execute` must still return a plain single-command
        response — no `batch_complete` wrapper.
        """
        response = self.client.execute('get_state')

        self.assertIsNotNone(response)
        self.assertEqual(response.get('status'), 'success')
        self.assertEqual(response.get('command'), 'get_state')
        self.assertNotEqual(response.get('type'), 'batch_complete')
        # The single-command branch has no `total`/`results` aggregate.
        self.assertNotIn('results', response)


if __name__ == '__main__':
    unittest.main()
