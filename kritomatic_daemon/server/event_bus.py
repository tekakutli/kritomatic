"""
event_bus.py — a process-wide pub/sub bus for daemon state events.

Any part of the daemon can publish to the bus; any part can subscribe.
Two subscription styles:

  * Queue-based subscriptions (`subscribe`/`unsubscribe`) are used by
    the event server, which needs to block in `select` on a signal
    socket and drain per-subscriber queues at its own pace.

  * Listener callbacks (`add_listener`/`remove_listener`) are used by
    in-process consumers — the panel handler — that want each event
    delivered as a function call. Callbacks are invoked synchronously
    on the publishing thread and must not block.

Publishing is fire-and-forget for the queue subscribers: a slow
subscriber cannot block the publisher, because publishes go through
per-subscriber queues and a full queue is drained rather than blocking.

Delivery guarantees:

  * Queue subscribers: at most one copy per subscriber per publish.
    If the queue is full, the oldest pending event is dropped to make
    room for the newest. State events are idempotent, so this is the
    right trade-off.

  * Listener callbacks: exactly once per publish, called in registration
    order. Exceptions from a listener are swallowed so one broken
    consumer cannot break the publish path for the others.

Thread-safe. Publishing from any thread is fine.
"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any, Callable


class Subscription:
    """One queue subscriber's channel: a bounded queue of events, plus
    a socketpair whose read end becomes readable whenever the queue is
    non-empty.
    """

    def __init__(self, queue_size: int):
        self.queue: queue.Queue = queue.Queue(maxsize=queue_size)
        r, w = socket.socketpair()
        r.setblocking(False)
        w.setblocking(False)
        self.signal_r: socket.socket = r
        self.signal_w: socket.socket = w

    def drain_signal(self) -> None:
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


Listener = Callable[[str, Any], None]


class EventBus:
    """Pub/sub bus for in-process state events."""

    DEFAULT_QUEUE_SIZE = 256

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE):
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._subscribers: list = []
        self._listeners: list = []

    # -- queue subscribers --

    def subscribe(self) -> Subscription:
        sub = Subscription(self._queue_size)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)
        try:
            sub.signal_w.close()
        except OSError:
            pass

    # -- listener callbacks --

    def add_listener(self, callback: Listener) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Listener) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def clear_listeners(self) -> None:
        with self._lock:
            self._listeners = []

    # -- publishing --

    def publish(self, event_type: str, data: Any) -> None:
        event = {"type": event_type, "data": data}

        with self._lock:
            subs = list(self._subscribers)
            listeners = list(self._listeners)

        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                try:
                    sub.queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    sub.queue.put_nowait(event)
                except queue.Full:
                    pass
            try:
                sub.signal_w.send(b"\x01")
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                pass

        for cb in listeners:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
