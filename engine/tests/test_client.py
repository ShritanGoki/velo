#!/usr/bin/env python3
"""Standalone smoke-test client for OrderBookServer (Milestone 2).

Connects over a raw TCP socket, speaks the binary protocol described in
docs/socket-service-design-doc.md directly (no library), and prints every
Ack/Fill it receives. Run the server first:

    ./build/orderbook_server 9999

then in another terminal:

    python3 tests/test_client.py 9999
"""
import socket
import struct
import sys

NEW_ORDER = 0x01
CANCEL = 0x02
FILL = 0x10
ACK = 0x11

# Big-endian ("!") to match the server's wire format.
NEW_ORDER_FMT = "!QBBqI"   # client_order_id, side, order_type, price_ticks, quantity
CANCEL_FMT = "!Q"          # client_order_id
FILL_FMT = "!QQqIQ"        # resting_id, incoming_id, price_ticks, quantity, timestamp
ACK_FMT = "!QBB"           # client_order_id, request_type, status

PAYLOAD_SIZE = {
    NEW_ORDER: struct.calcsize(NEW_ORDER_FMT),
    CANCEL: struct.calcsize(CANCEL_FMT),
    FILL: struct.calcsize(FILL_FMT),
    ACK: struct.calcsize(ACK_FMT),
}


def send_new_order(sock, client_order_id, side, order_type, price_ticks, quantity):
    body = struct.pack(NEW_ORDER_FMT, client_order_id, side, order_type, price_ticks, quantity)
    sock.sendall(bytes([NEW_ORDER]) + body)


def send_cancel(sock, client_order_id):
    body = struct.pack(CANCEL_FMT, client_order_id)
    sock.sendall(bytes([CANCEL]) + body)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed the connection")
        buf += chunk
    return buf


def recv_message(sock):
    tag = recv_exact(sock, 1)[0]
    size = PAYLOAD_SIZE.get(tag)
    if size is None:
        raise ValueError(f"unknown message tag {tag:#x}")
    payload = recv_exact(sock, size)
    if tag == FILL:
        return "FILL", struct.unpack(FILL_FMT, payload)
    if tag == ACK:
        return "ACK", struct.unpack(ACK_FMT, payload)
    raise ValueError(f"unexpected server->client tag {tag:#x}")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    sock = socket.create_connection(("127.0.0.1", port))

    # Rest a sell, then cross it with a buy - expect an ack for each plus a fill.
    send_new_order(sock, 1, 1, 0, 100, 50)  # Sell 50 @ 100
    print(recv_message(sock))

    send_new_order(sock, 2, 0, 0, 100, 50)  # Buy 50 @ 100
    print(recv_message(sock))  # ack
    print(recv_message(sock))  # fill

    send_cancel(sock, 999)  # unknown id
    print(recv_message(sock))

    sock.close()


if __name__ == "__main__":
    main()
