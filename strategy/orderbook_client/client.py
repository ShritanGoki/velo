"""OrderBookClient: the Python side of the Milestone 2 wire protocol.

See docs/synthetic-loop-design-doc.md §6. A background thread owns the
socket's read side and decodes complete messages off it exactly like the
C++ server does on its side (tag byte, then that tag's fixed payload
size); the main thread only ever drains the resulting queue via
poll_events(), so the strategy loop stays simple and synchronous even
though the socket itself is async from its perspective.
"""
from __future__ import annotations

import queue
import socket
import threading
from typing import List, Optional, Union

from . import protocol
from .protocol import AckEvent, FillEvent, OrderType, Side

Event = Union[AckEvent, FillEvent]


class OrderBookClient:
    def __init__(self, host: str, port: int):
        self._sock = socket.create_connection((host, port))
        self._events: "queue.Queue[Event]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def submit_limit(self, order_id: int, side: Side, price_ticks: int, qty: int) -> None:
        self._send(protocol.encode_new_order(
            protocol.ClientOrderMsg(order_id, side, OrderType.LIMIT, price_ticks, qty)))

    def submit_market(self, order_id: int, side: Side, qty: int) -> None:
        self._send(protocol.encode_new_order(
            protocol.ClientOrderMsg(order_id, side, OrderType.MARKET, 0, qty)))

    def cancel(self, order_id: int) -> None:
        self._send(protocol.encode_cancel(protocol.CancelMsg(order_id)))

    def poll_events(self, timeout: Optional[float] = None) -> List[Event]:
        """Drains whatever events are already queued. If timeout is given,
        waits up to that long for at least one event before draining the
        rest non-blocking."""
        events: List[Event] = []
        if timeout is not None:
            try:
                events.append(self._events.get(timeout=timeout))
            except queue.Empty:
                return events
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._reader.join(timeout=2.0)

    def _send(self, buf: bytes) -> None:
        self._sock.sendall(buf)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _read_loop(self) -> None:
        while True:
            tag_byte = self._recv_exact(1)
            if tag_byte is None:
                return
            size = protocol.payload_size(tag_byte[0])
            if size is None:
                return  # protocol violation; nothing to resynchronize to
            payload = self._recv_exact(size)
            if payload is None:
                return
            self._events.put(protocol.decode_event(tag_byte[0], payload))
