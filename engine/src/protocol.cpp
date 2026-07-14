#include "orderbook/protocol.hpp"

namespace orderbook::proto {
namespace {

// Manual big-endian packing: portable across platforms without pulling in
// platform-specific byte-swap headers (macOS's OSByteOrder.h vs. Linux's
// endian.h), and unambiguous about what ends up on the wire.

void put_u8(uint8_t*& p, uint8_t v) { *p++ = v; }
uint8_t get_u8(const uint8_t*& p) { return *p++; }

void put_u32(uint8_t*& p, uint32_t v) {
    for (int shift = 24; shift >= 0; shift -= 8) *p++ = static_cast<uint8_t>((v >> shift) & 0xFF);
}
uint32_t get_u32(const uint8_t*& p) {
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) v = (v << 8) | *p++;
    return v;
}

void put_u64(uint8_t*& p, uint64_t v) {
    for (int shift = 56; shift >= 0; shift -= 8) *p++ = static_cast<uint8_t>((v >> shift) & 0xFF);
}
uint64_t get_u64(const uint8_t*& p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v = (v << 8) | *p++;
    return v;
}

} // namespace

std::optional<size_t> payload_size(uint8_t tag) {
    switch (static_cast<MsgType>(tag)) {
        case MsgType::NewOrder: return kClientOrderMsgSize;
        case MsgType::Cancel: return kCancelMsgSize;
        case MsgType::SubscribeFills: return size_t{0};
        case MsgType::Fill: return kFillWireMsgSize;
        case MsgType::Ack: return kAckMsgSize;
    }
    return std::nullopt;
}

void encode(const ClientOrderMsg& msg, uint8_t* out) {
    uint8_t* p = out;
    put_u64(p, msg.client_order_id);
    put_u8(p, msg.side);
    put_u8(p, msg.order_type);
    put_u64(p, static_cast<uint64_t>(msg.price_ticks));
    put_u32(p, msg.quantity);
}

ClientOrderMsg decode_client_order(const uint8_t* in) {
    const uint8_t* p = in;
    ClientOrderMsg msg{};
    msg.client_order_id = get_u64(p);
    msg.side = get_u8(p);
    msg.order_type = get_u8(p);
    msg.price_ticks = static_cast<int64_t>(get_u64(p));
    msg.quantity = get_u32(p);
    return msg;
}

void encode(const CancelMsg& msg, uint8_t* out) {
    uint8_t* p = out;
    put_u64(p, msg.client_order_id);
}

CancelMsg decode_cancel(const uint8_t* in) {
    const uint8_t* p = in;
    return CancelMsg{get_u64(p)};
}

void encode(const FillWireMsg& msg, uint8_t* out) {
    uint8_t* p = out;
    put_u64(p, msg.resting_order_id);
    put_u64(p, msg.incoming_order_id);
    put_u8(p, msg.resting_side);
    put_u64(p, static_cast<uint64_t>(msg.price_ticks));
    put_u32(p, msg.quantity);
    put_u64(p, msg.timestamp);
}

FillWireMsg decode_fill(const uint8_t* in) {
    const uint8_t* p = in;
    FillWireMsg msg{};
    msg.resting_order_id = get_u64(p);
    msg.incoming_order_id = get_u64(p);
    msg.resting_side = get_u8(p);
    msg.price_ticks = static_cast<int64_t>(get_u64(p));
    msg.quantity = get_u32(p);
    msg.timestamp = get_u64(p);
    return msg;
}

void encode(const AckMsg& msg, uint8_t* out) {
    uint8_t* p = out;
    put_u64(p, msg.client_order_id);
    put_u8(p, msg.request_type);
    put_u8(p, msg.status);
}

AckMsg decode_ack(const uint8_t* in) {
    const uint8_t* p = in;
    AckMsg msg{};
    msg.client_order_id = get_u64(p);
    msg.request_type = get_u8(p);
    msg.status = get_u8(p);
    return msg;
}

} // namespace orderbook::proto
