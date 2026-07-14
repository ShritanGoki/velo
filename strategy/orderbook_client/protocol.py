"""Binary wire protocol, mirroring engine/include/orderbook/protocol.hpp.

See docs/socket-service-design-doc.md §4 for the framing rules this
implements a second time in Python: 1-byte type tag, then a fixed-size
big-endian payload. Kept in lockstep with the C++ side by
tests/test_protocol.py (Python) round-tripping the same byte layouts as
engine/tests/test_protocol.cpp (C++).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union


class MsgType(IntEnum):
    NEW_ORDER = 0x01        # client -> server
    CANCEL = 0x02           # client -> server
    SUBSCRIBE_FILLS = 0x03  # client -> server, empty payload
    FILL = 0x10             # server -> client
    ACK = 0x11              # server -> client


class Side(IntEnum):
    BUY = 0
    SELL = 1


class OrderType(IntEnum):
    LIMIT = 0
    MARKET = 1


_NEW_ORDER_FMT = "!QBBqI"  # client_order_id, side, order_type, price_ticks, quantity
_CANCEL_FMT = "!Q"         # client_order_id
_FILL_FMT = "!QQBqIQ"      # resting_id, incoming_id, resting_side, price_ticks, quantity, timestamp
_ACK_FMT = "!QBB"          # client_order_id, request_type, status

PAYLOAD_SIZE = {
    MsgType.NEW_ORDER: struct.calcsize(_NEW_ORDER_FMT),
    MsgType.CANCEL: struct.calcsize(_CANCEL_FMT),
    MsgType.SUBSCRIBE_FILLS: 0,
    MsgType.FILL: struct.calcsize(_FILL_FMT),
    MsgType.ACK: struct.calcsize(_ACK_FMT),
}


@dataclass(frozen=True)
class ClientOrderMsg:
    client_order_id: int
    side: Side
    order_type: OrderType
    price_ticks: int
    quantity: int


@dataclass(frozen=True)
class CancelMsg:
    client_order_id: int


@dataclass(frozen=True)
class FillEvent:
    resting_order_id: int
    incoming_order_id: int
    resting_side: Side
    price_ticks: int
    quantity: int
    timestamp: int

    @property
    def incoming_side(self) -> Side:
        return Side.SELL if self.resting_side == Side.BUY else Side.BUY


@dataclass(frozen=True)
class AckEvent:
    client_order_id: int
    request_type: int
    status: int  # 0 = ok, 1 = rejected

    @property
    def ok(self) -> bool:
        return self.status == 0


def payload_size(tag: int) -> Optional[int]:
    try:
        return PAYLOAD_SIZE[MsgType(tag)]
    except ValueError:
        return None


def encode_new_order(msg: ClientOrderMsg) -> bytes:
    body = struct.pack(_NEW_ORDER_FMT, msg.client_order_id, int(msg.side),
                        int(msg.order_type), msg.price_ticks, msg.quantity)
    return bytes([MsgType.NEW_ORDER]) + body


def decode_new_order(payload: bytes) -> ClientOrderMsg:
    client_order_id, side, order_type, price_ticks, quantity = struct.unpack(_NEW_ORDER_FMT, payload)
    return ClientOrderMsg(client_order_id, Side(side), OrderType(order_type), price_ticks, quantity)


def encode_cancel(msg: CancelMsg) -> bytes:
    return bytes([MsgType.CANCEL]) + struct.pack(_CANCEL_FMT, msg.client_order_id)


def decode_cancel(payload: bytes) -> CancelMsg:
    (client_order_id,) = struct.unpack(_CANCEL_FMT, payload)
    return CancelMsg(client_order_id)


def encode_subscribe_fills() -> bytes:
    return bytes([MsgType.SUBSCRIBE_FILLS])


def decode_fill(payload: bytes) -> FillEvent:
    resting_id, incoming_id, resting_side, price_ticks, quantity, timestamp = struct.unpack(_FILL_FMT, payload)
    return FillEvent(resting_id, incoming_id, Side(resting_side), price_ticks, quantity, timestamp)


def decode_ack(payload: bytes) -> AckEvent:
    return AckEvent(*struct.unpack(_ACK_FMT, payload))


def decode_event(tag: int, payload: bytes) -> Union[FillEvent, AckEvent]:
    if tag == MsgType.FILL:
        return decode_fill(payload)
    if tag == MsgType.ACK:
        return decode_ack(payload)
    raise ValueError(f"unexpected server->client tag {tag:#x}")
