#pragma once

// Binary wire protocol for the Milestone 2 socket service. See
// docs/socket-service-design-doc.md §4 for the framing rules: every
// message is a 1-byte type tag followed by a fixed-size payload, all
// multi-byte integers big-endian on the wire.

#include <cstddef>
#include <cstdint>

namespace orderbook::proto {

enum class MsgType : uint8_t {
    NewOrder = 0x01,  // client -> server
    Cancel = 0x02,    // client -> server
    Fill = 0x10,      // server -> client
    Ack = 0x11,       // server -> client
};

struct ClientOrderMsg {
    uint64_t client_order_id;
    uint8_t side;        // 0 = Buy, 1 = Sell
    uint8_t order_type;  // 0 = Limit, 1 = Market
    int64_t price_ticks; // ignored (but present) for Market orders
    uint32_t quantity;
};
inline constexpr size_t kClientOrderMsgSize = 8 + 1 + 1 + 8 + 4;  // 22

struct CancelMsg {
    uint64_t client_order_id;
};
inline constexpr size_t kCancelMsgSize = 8;

struct FillWireMsg {
    uint64_t resting_order_id;
    uint64_t incoming_order_id;
    int64_t price_ticks;
    uint32_t quantity;
    uint64_t timestamp;
};
inline constexpr size_t kFillWireMsgSize = 8 + 8 + 8 + 4 + 8;  // 36

struct AckMsg {
    uint64_t client_order_id;
    uint8_t request_type;  // echoes 0x01 (NewOrder) or 0x02 (Cancel)
    uint8_t status;         // 0 = ok, 1 = rejected
};
inline constexpr size_t kAckMsgSize = 8 + 1 + 1;  // 10

// Fixed payload size for a message type tag, or 0 if the tag is unknown
// (callers should treat 0 as a protocol violation and close the connection).
size_t payload_size(uint8_t tag);

// `out`/`in` must point at buffers of at least k*MsgSize bytes (the tag
// byte is handled separately by the caller, not part of these functions).
void encode(const ClientOrderMsg& msg, uint8_t* out);
void encode(const CancelMsg& msg, uint8_t* out);
void encode(const FillWireMsg& msg, uint8_t* out);
void encode(const AckMsg& msg, uint8_t* out);

ClientOrderMsg decode_client_order(const uint8_t* in);
CancelMsg decode_cancel(const uint8_t* in);
FillWireMsg decode_fill(const uint8_t* in);
AckMsg decode_ack(const uint8_t* in);

} // namespace orderbook::proto
