"""Wire-format round-trip tests, mirroring engine/tests/test_protocol.cpp.
Milestone 3 test case #1: catches the Python and C++ protocol
implementations drifting apart from each other.
"""
import struct
import unittest

import _pathfix  # noqa: F401

from orderbook_client import protocol
from orderbook_client.protocol import (
    AckEvent, CancelMsg, ClientOrderMsg, FillEvent, MsgType, OrderType, Side,
)


class TestProtocolRoundTrip(unittest.TestCase):
    def test_new_order_round_trip(self):
        msg = ClientOrderMsg(0x0102030405060708, Side.SELL, OrderType.LIMIT, -0x1122334455667788, 0xAABBCCDD)
        encoded = protocol.encode_new_order(msg)

        self.assertEqual(encoded[0], MsgType.NEW_ORDER)
        # Big-endian: first payload byte is the MSB of client_order_id.
        self.assertEqual(encoded[1], 0x01)
        self.assertEqual(encoded[8], 0x08)

        decoded = protocol.decode_new_order(encoded[1:])
        self.assertEqual(decoded, msg)

    def test_cancel_round_trip(self):
        msg = CancelMsg(42)
        encoded = protocol.encode_cancel(msg)
        self.assertEqual(encoded[0], MsgType.CANCEL)
        self.assertEqual(protocol.decode_cancel(encoded[1:]), msg)

    def test_fill_round_trip(self):
        fill = FillEvent(resting_order_id=1, incoming_order_id=2, resting_side=Side.SELL,
                          price_ticks=-500, quantity=60, timestamp=999999)
        payload = struct.pack(protocol._FILL_FMT, fill.resting_order_id, fill.incoming_order_id,
                               int(fill.resting_side), fill.price_ticks, fill.quantity, fill.timestamp)
        self.assertEqual(protocol.decode_fill(payload), fill)
        self.assertEqual(protocol.decode_event(MsgType.FILL, payload), fill)

    def test_subscribe_fills_round_trip(self):
        encoded = protocol.encode_subscribe_fills()
        self.assertEqual(encoded, bytes([MsgType.SUBSCRIBE_FILLS]))

    def test_ack_round_trip(self):
        ack = AckEvent(client_order_id=7, request_type=int(MsgType.NEW_ORDER), status=1)
        payload = struct.pack(protocol._ACK_FMT, ack.client_order_id, ack.request_type, ack.status)
        self.assertEqual(protocol.decode_ack(payload), ack)
        self.assertEqual(protocol.decode_event(MsgType.ACK, payload), ack)
        self.assertFalse(ack.ok)

    def test_payload_size_matches_engine_layout(self):
        # These sizes must match engine/include/orderbook/protocol.hpp exactly.
        self.assertEqual(protocol.payload_size(MsgType.NEW_ORDER), 22)
        self.assertEqual(protocol.payload_size(MsgType.CANCEL), 8)
        self.assertEqual(protocol.payload_size(MsgType.SUBSCRIBE_FILLS), 0)
        self.assertEqual(protocol.payload_size(MsgType.FILL), 37)
        self.assertEqual(protocol.payload_size(MsgType.ACK), 10)
        self.assertIsNone(protocol.payload_size(0xFF))

    def test_decode_event_rejects_client_to_server_tags(self):
        with self.assertRaises(ValueError):
            protocol.decode_event(MsgType.NEW_ORDER, b"\x00" * 22)


if __name__ == "__main__":
    unittest.main()
