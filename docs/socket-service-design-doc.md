# Matching Engine as a Live Service — Design Doc (Milestone 2: Socket Server)

## 1. Purpose

Wrap the Milestone 1 `OrderBook` (see `docs/orderbook-design-doc.md`) in a TCP
socket server so external processes — starting with the Milestone 3 Python
strategy layer — can submit orders and receive fills over the wire instead
of linking against the C++ code directly. This is the seam plan.md's
architecture diagram draws between the "C++ Matching Engine" box and
everything around it, so the wire format and connection model chosen here
are load-bearing for every later milestone.

`OrderBook`'s public interface (§7 of the order book doc) does not change.
This milestone adds a networking layer on top of it; it does not touch
matching logic.

## 2. Scope

**In scope:**
- A TCP server that accepts client connections, decodes order/cancel
  requests off the wire, calls into `OrderBook`, and writes back
  fills/acks.
- A binary wire protocol (no JSON — see plan.md's IPC note on latency
  being a real design concern).
- Handling multiple concurrent client connections without corrupting book
  state (there is exactly one `OrderBook` instance; all requests funnel
  through it in order).

**Out of scope for this milestone** (see §9):
- The actual Python client (Milestone 3).
- ZeroMQ (only adopted later if raw sockets prove insufficient).
- Authentication/encryption — this runs on localhost against a paper
  system, not a real venue.
- Multi-instrument routing — still one instrument, one book, one port.

## 3. Concurrency Model

Design Principle 1 from plan.md ("correctness before speed... single-
threaded version working before any performance optimization") carries
over directly: the server is a **single-threaded event loop** using
`poll()` over all client file descriptors. No per-connection threads, no
locking around `OrderBook`.

- One listening socket, `poll()`-ed alongside all connected client
  sockets.
- On read-ready: decode as many complete messages as are buffered, feed
  each to `OrderBook` in arrival order, write responses back
  synchronously before moving to the next fd.
- Because everything happens on one thread, `OrderBook` needs no internal
  synchronization — matching correctness proof from Milestone 1 carries
  over unchanged.
- A slow/blocked client can delay other clients' processing under this
  model; that's an accepted latency cost for now and the first thing to
  revisit if/when this milestone gets a throughput benchmark pass.

## 4. Wire Protocol

Every message is: **1-byte message type tag, followed by a fixed-size
payload determined by that tag.** No length prefix is needed because
every message type has a fixed, known size — this keeps framing trivial
(read 1 byte, know exactly how many more bytes to read, no partial-
message bookkeeping beyond "did I get N bytes yet").

All multi-byte integers are sent in **big-endian ("network") byte order**,
converted with `htobe64`/`be64toh`/`htobe32`/`be32toh` (portable via
`<arpa/inet.h>` / `<endian.h>` on Linux, `<libkern/OSByteOrder.h>` shims on
macOS). This is the one piece of real protocol hygiene worth building
correctly from day one, since getting it wrong silently corrupts every
price/quantity field rather than crashing loudly.

### 4.1 Message Types

| Tag | Name | Direction | Payload |
|---|---|---|---|
| `0x01` | `NewOrder` | client → server | `ClientOrderMsg` |
| `0x02` | `Cancel` | client → server | `CancelMsg` |
| `0x10` | `FillMsg` | server → client | one per `Fill` generated |
| `0x11` | `AckMsg` | server → client | result of `NewOrder`/`Cancel` |

### 4.2 Payload Layouts

```
ClientOrderMsg (33 bytes):
    uint64_t client_order_id;   // caller-assigned, echoed back in acks/fills
    uint8_t  side;              // 0 = Buy, 1 = Sell
    uint8_t  order_type;        // 0 = Limit, 1 = Market
    int64_t  price_ticks;       // ignored (but present) for Market orders
    uint32_t quantity;

CancelMsg (8 bytes):
    uint64_t client_order_id;

FillMsg (36 bytes):
    uint64_t resting_order_id;
    uint64_t incoming_order_id;
    int64_t  price_ticks;
    uint32_t quantity;
    uint64_t timestamp;

AckMsg (10 bytes):
    uint64_t client_order_id;
    uint8_t  request_type;      // echoes 0x01 or 0x02
    uint8_t  status;            // 0 = ok, 1 = rejected (dup id / unknown id / malformed)
```

Fixed-width fields only, no strings, no padding surprises — every struct
above is packed explicitly on the wire (serialize field-by-field into a
byte buffer; do not rely on `sizeof(struct)`/compiler layout, since padding
and alignment are not part of this contract).

## 5. Server ↔ OrderBook Integration

```cpp
class OrderBookServer {
public:
    explicit OrderBookServer(uint16_t port);
    void run();  // blocks: poll loop, forever (or until stop() from another
                 // signal-safe path)
    void stop();

private:
    OrderBook book_;
    int listen_fd_;
    std::vector<ClientConnection> clients_;

    void accept_new_connections();
    void handle_readable(ClientConnection& client);
    void dispatch(ClientConnection& client, const ClientOrderMsg& msg);
    void dispatch(ClientConnection& client, const CancelMsg& msg);
};
```

- `client_order_id` from the wire is used directly as the `Order.id` passed
  into `OrderBook::submit`. Since `OrderBook` already rejects duplicate ids
  (see Milestone 1's `submit`), a colliding id across two different clients
  surfaces as an ordinary rejected-order ack — correct behavior for a
  single-book, single-port service, and something to revisit only if this
  server ever fronts multiple simultaneous strategy clients that don't
  coordinate ids.
- Fills are **broadcast only to the client whose order generated them**, on
  each side. Concretely: whichever client submitted the `incoming` order
  gets `FillMsg`s where their order plays whichever role it had, and
  whichever client submitted the resting order (which may be a different
  connection) gets its own copy of the same fills addressed to it. This
  means each fill may be written to up to two connections. If the resting
  order's owning connection has already disconnected, that write is
  silently dropped (there's no one to tell).
- Every `NewOrder`/`Cancel` gets exactly one `AckMsg` back, even if it also
  produces `FillMsg`s — acks confirm receipt/validity, fills report
  economic outcome, and a client needs both distinguished (an order can be
  "accepted" but generate zero fills, e.g. it just rests).

## 6. Error Handling

| Condition | Behavior |
|---|---|
| Unknown message type tag | Log and close that connection (protocol violation — no way to resynchronize framing without a length prefix, so don't try). |
| Duplicate `client_order_id` on `NewOrder` | `AckMsg` with `status = 1`, no book mutation (matches `OrderBook::submit`'s existing dup-id rejection). |
| `Cancel` for unknown/already-filled id | `AckMsg` with `status = 1` (mirrors `OrderBook::cancel` returning `false`). |
| Client disconnects mid-message | Discard the partial buffered bytes for that fd, drop the connection, no crash. |
| `poll()`/accept failure | Log and continue serving existing connections; do not tear down the whole server for one bad fd. |

## 7. Test Cases to Write First

1. Single client: submit a resting limit order over the socket, verify one
   `AckMsg(status=ok)` comes back, no `FillMsg`.
2. Single client: submit two orders that cross, verify `AckMsg` for both
   plus the expected `FillMsg`(s), with correct byte layout on the wire
   (round-trip encode/decode test independent of sockets, too — protocol
   serialization should be unit-testable without opening a real socket).
3. Two clients: client A rests a limit order, client B sends a crossing
   order; verify **both** A and B receive a `FillMsg` for the trade, each
   correctly attributing resting vs. incoming order ids.
4. Cancel round-trip: submit, cancel, verify `AckMsg(status=ok)`; cancel
   again, verify `AckMsg(status=rejected)`.
5. Malformed/unknown message tag: verify the server closes that connection
   without affecting other connected clients or crashing the process.
6. Client disconnect mid-session: verify the server keeps running and
   other clients are unaffected.
7. Byte-order test: encode a message with distinguishable high/low bytes
   in each field, decode it, verify no field got byte-swapped or
   truncated.

## 8. Explicitly Deferred to Later Milestones

- Python client implementation (Milestone 3) — this milestone only proves
  the server side against a hand-written C++ or script-based test client.
- ZeroMQ migration (only if raw-socket framing/backpressure becomes a
  proven bottleneck).
- Multi-instrument support, authentication, TLS.
- Latency/throughput benchmarking of the network path (correctness first,
  per Design Principle 1 — numbers come once the protocol is proven
  correct).

## 9. Definition of Done for Milestone 2

- All 7 test cases in §7 pass.
- A small standalone test client (can be a throwaway C++ binary or even a
  raw Python `socket` script) can connect, submit orders, and print fills
  received from a running `OrderBookServer` — demonstrating the full
  socket round-trip end to end.
- `OrderBook`'s Milestone 1 test suite still passes unmodified — this
  milestone adds a layer around it, it doesn't change matching behavior.
