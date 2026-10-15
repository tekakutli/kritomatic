"""
event_bus.py — a process-wide pub/sub bus for daemon state events.

Any part of the daemon can publish to the bus; any part can subscribe.
Publishing is fire-and-forget: a slow subscriber cannot block the
publisher, because publishes go through per-subscriber queues and a
full queue is drained rather than blocking.

Subscribers are woken by a signal socket. Each subscription owns a
`socketpair`; the bus writes one byte to the write end on every publish,
and the subscriber's consumer loop selects on the read end alongside its
transport socket. A subscriber that is idle blocks in `select` with no
timeout; it wakes only when there is an event or when the bus is shut
down.

Delivery guarantees:

  * At most one copy per subscriber per publish. If the queue is full,
    the oldest pending event is dropped to make room for the newest.
    State events are idempotent, so this is the right trade-off: a
    slow subscriber misses intermediate states but always sees the
    newest.

  * Thread-safe. Publishing from any thread is fine.

  * Wake signals are advisory. The queue is the delivery channel; the
    socket byte is a "there may be something to drain" hint. A skipped
    write (already-closed socket, non-blocking EAGAIN) is not a lost
    event — the consumer drains the queue directly.
"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any


class Subscription:
    """One subscriber's channel: a bounded queue of events, plus a
    socketpair whose read end becomes readable whenever the queue is
    non-empty.

    Ownership:

      * The bus writes to `signal_w` on every publish and closes it on
        unsubscribe (which wakes any consumer blocked in select).

      * The consumer selects on `signal_r` and drains both `queue` and
        `signal_r` each time it wakes. It closes both ends when it
        exits; closing an already-closed end is a no-op.
    """

    def __init__(self, queue_size: int):
        self.queue: queue.Queue = queue.Queue(maxsize=queue_size)
        r, w = socket.socketpair()
        r.setblocking(False)
        w.setblocking(False)
        self.signal_r: socket.socket = r
        self.signal_w: socket.socket = w

    def drain_signal(self) -> None:
        """Read and discard everything currently buffered on the read
        end. Called by the consumer after being woken, so the socket
        stops being readable and select does not immediately re-fire.
        """
        while True:
            try:
                chunk = self.signal_r.recv(65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not chunk:
                return

    def close(self) -> None:
        for s in (self.signal_r, self.signal_w):
            try:
                s.close()
            except OSError:
                pass


class EventBus:
    """Pub/sub bus for in-process state events."""

    # Bounded so a dead-but-not-yet-detected subscriber cannot grow
    # memory without limit. 256 is generous: one state event is a few
    # KB, so this caps per-subscriber memory in the low megabytes.
    DEFAULT_QUEUE_SIZE = 256

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE):
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._subscribers: list[Subscription] = []

    def subscribe(self) -> Subscription:
        sub = Subscription(self._queue_size)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Remove `sub` from the list and close its write end.

        Closing the write end makes the read end readable (EOF), which
        wakes any consumer blocked in select. The consumer's own
        cleanup closes both ends; closing an already-closed socket is
        a no-op.
        """
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)
        try:
            sub.signal_w.close()
        except OSError:
            pass

    def publish(self, event_type: str, data: Any) -> None:
        event = {"type": event_type, "data": data}
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                # Drop the oldest and retry once. A subscriber this far
                # behind does not want any event older than the current.
                try:
                    sub.queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    sub.queue.put_nowait(event)
                except queue.Full:
                    pass
            # Wake the consumer. A write failure is not a lost event:
            # the queue put above already happened, and the consumer
            # drains the queue whenever it wakes — either on a
            # previously-pending signal byte, or on the current one if
            # the write succeeded.
            try:
                sub.signal_w.send(b"\x01")
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                # The subscription was torn down concurrently. Nothing
                # to signal.
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
