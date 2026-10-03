"""
Verifies that `kritomatic batch run` sends ONE bundled message to the
daemon instead of N single-command messages.

Before the fix this test FAILS with:
    AssertionError: Expected 1 bundled message, got 3.

After the fix it PASSES. It will also fail again if a future refactor
reintroduces the per-command loop.
"""

import json
import sys
import unittest
from pathlib import Path

# Make `src/kritomatic` importable regardless of which Python runs this
# test. The repo layout is:
#     <repo>/src/kritomatic/...
#     <repo>/tests/test_batch_bundling.py
# so we climb one level from tests/ and descend into src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.client import KritaClient
from kritomatic.batch.executor import BatchExecutor


class FakeSocket:
    """
    Socket-like object that:
      - records every JSON message the client writes
      - synthesizes a daemon response in the streaming (NDJSON) format:
        one 'progress' line per command, then one 'batch_complete' line.
    """

    def __init__(self):
        self.sent_messages = []
        self._incoming = b''

    def send(self, data):
        text = data.decode('utf-8')
        for line in text.splitlines():
            if not line.strip():
                continue
            self.sent_messages.append(json.loads(line))
        # Synthesize a response for the most recent message.
        self._synthesize_response(self.sent_messages[-1])
        return len(data)

    def recv(self, n):
        chunk = self._incoming[:n]
        self._incoming = self._incoming[n:]
        return chunk

    def settimeout(self, _):
        pass

    def close(self):
        pass

    def _synthesize_response(self, payload):
        if 'commands' in payload:
            n = len(payload['commands'])
            for i, cmd in enumerate(payload['commands']):
                progress = {
                    'type': 'progress',
                    'index': i,
                    'total': n,
                    'result': {
                        'index': i,
                        'command': cmd.get('type'),
                        'status': 'success',
                        'message': f"ran {cmd.get('type')}",
                        'data': None,
                    },
                }
                self._incoming += (json.dumps(progress) + '\n').encode('utf-8')
            final = {
                'type': 'batch_complete',
                'status': 'batch_complete',
                'id': payload.get('id'),
                'total': n,
                'successful': n,
                'failed': 0,
                'results': [],
            }
            self._incoming += (json.dumps(final) + '\n').encode('utf-8')
        else:
            resp = {
                'status': 'success',
                'command': payload.get('type'),
                'message': 'ok',
                'data': None,
            }
            self._incoming += (json.dumps(resp) + '\n').encode('utf-8')


def make_client():
    client = KritaClient()
    client.socket = FakeSocket()
    client.connected = True
    client._recv_buffer = b''
    return client


class TestBatchBundling(unittest.TestCase):

    def test_batch_is_sent_as_one_message(self):
        client = make_client()
        executor = BatchExecutor(client=client)

        batch = {
            'id': 'bundling-test',
            'commands': [
                {'type': 'create_layer', 'name': 'A'},
                {'type': 'create_layer', 'name': 'B'},
                {'type': 'create_layer', 'name': 'C'},
            ],
        }
        results = executor.execute(batch)
        sent = client.socket.sent_messages

        # THE assertion: exactly one wire message for the whole batch.
        self.assertEqual(
            len(sent), 1,
            f"Expected 1 bundled message, got {len(sent)}. "
            f"The CLI is still sending one command per time."
        )

        # And it is a real bundle.
        self.assertIn('commands', sent[0])
        self.assertEqual(len(sent[0]['commands']), 3)

        # And the batch ran to completion.
        self.assertEqual(len(results), 3)
        self.assertEqual(
            [r['command'] for r in results],
            ['create_layer', 'create_layer', 'create_layer'],
        )

    def test_single_command_still_sends_one_message(self):
        """Sanity check: the non-batch path is untouched."""
        client = make_client()
        client.execute('set_brush_size', value=42)
        self.assertEqual(len(client.socket.sent_messages), 1)
        self.assertNotIn('commands', client.socket.sent_messages[0])


if __name__ == '__main__':
    unittest.main()
