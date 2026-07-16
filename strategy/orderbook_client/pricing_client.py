"""Client for pricing_server's wire protocol. See
docs/delta-hedging-design-doc.md §3, §5.

pricing_server is a small, separate Rust process from orderbook_server -
a genuinely different service (pricing, not order matching) with its own
tag namespace and its own port, deliberately not reusing the order
protocol's Ack/Cancel machinery since there's nothing here to
acknowledge or cancel: every request gets exactly one synchronous reply.
"""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

PRICE_REQUEST_TAG = 0x20
PRICE_RESPONSE_TAG = 0x21

# Big-endian ("!"), same convention as orderbook_client/protocol.py.
_REQUEST_FMT = "!qqdddB"   # spot_ticks, strike_ticks, rate, vol, time_to_expiry, is_call
_RESPONSE_FMT = "!dddddd"   # price, delta, gamma, vega, theta, rho

REQUEST_SIZE = struct.calcsize(_REQUEST_FMT)
RESPONSE_SIZE = struct.calcsize(_RESPONSE_FMT)


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


class PricingClient:
    def __init__(self, host: str, port: int):
        self._sock = socket.create_connection((host, port))

    def price(self, spot_ticks: int, strike_ticks: int, rate: float, vol: float,
              time_to_expiry: float, is_call: bool) -> Greeks:
        body = struct.pack(_REQUEST_FMT, spot_ticks, strike_ticks, rate, vol,
                            time_to_expiry, 1 if is_call else 0)
        self._sock.sendall(bytes([PRICE_REQUEST_TAG]) + body)

        tag = self._recv_exact(1)
        if tag[0] != PRICE_RESPONSE_TAG:
            raise ValueError(f"unexpected tag from pricing_server: {tag[0]:#x}")
        payload = self._recv_exact(RESPONSE_SIZE)
        return Greeks(*struct.unpack(_RESPONSE_FMT, payload))

    def close(self) -> None:
        self._sock.close()

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("pricing_server closed the connection")
            buf += chunk
        return buf
