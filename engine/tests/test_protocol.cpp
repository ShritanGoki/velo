// Wire-format round-trip tests: encode then decode each message type and
// confirm every field survives, including a byte-order check using values
// with distinguishable bytes in every position.

#include <iostream>
#include <string>

#include "orderbook/protocol.hpp"

using namespace orderbook::proto;

namespace {

int g_failures = 0;
int g_checks = 0;

void check(bool condition, const std::string& message) {
    ++g_checks;
    if (!condition) {
        ++g_failures;
        std::cerr << "FAIL: " << message << "\n";
    }
}

template <typename T>
void check_eq(const T& actual, const T& expected, const std::string& message) {
    ++g_checks;
    if (!(actual == expected)) {
        ++g_failures;
        std::cerr << "FAIL: " << message << " (expected " << expected
                   << ", got " << actual << ")\n";
    }
}

void test_client_order_round_trip() {
    ClientOrderMsg msg{0x0102030405060708ULL, 1, 0, -0x1122334455667788LL, 0xAABBCCDDu};
    uint8_t buf[kClientOrderMsgSize];
    encode(msg, buf);
    ClientOrderMsg decoded = decode_client_order(buf);

    check_eq(decoded.client_order_id, msg.client_order_id, "client_order_id round-trips");
    check_eq(decoded.side, msg.side, "side round-trips");
    check_eq(decoded.order_type, msg.order_type, "order_type round-trips");
    check_eq(decoded.price_ticks, msg.price_ticks, "negative price_ticks round-trips");
    check_eq(decoded.quantity, msg.quantity, "quantity round-trips");

    // Byte-order sanity: first byte on the wire should be the MSB of the id.
    check_eq<int>(buf[0], 0x01, "client_order_id should be encoded big-endian (MSB first)");
    check_eq<int>(buf[7], 0x08, "client_order_id should be encoded big-endian (LSB last)");
}

void test_cancel_round_trip() {
    CancelMsg msg{42};
    uint8_t buf[kCancelMsgSize];
    encode(msg, buf);
    CancelMsg decoded = decode_cancel(buf);
    check_eq(decoded.client_order_id, msg.client_order_id, "cancel client_order_id round-trips");
}

void test_fill_round_trip() {
    FillWireMsg msg{1, 2, /*resting_side=*/1, -500, 60, 999999};
    uint8_t buf[kFillWireMsgSize];
    encode(msg, buf);
    FillWireMsg decoded = decode_fill(buf);

    check_eq(decoded.resting_order_id, msg.resting_order_id, "fill resting_order_id round-trips");
    check_eq(decoded.incoming_order_id, msg.incoming_order_id, "fill incoming_order_id round-trips");
    check_eq(decoded.resting_side, msg.resting_side, "fill resting_side round-trips");
    check_eq(decoded.price_ticks, msg.price_ticks, "fill price_ticks round-trips");
    check_eq(decoded.quantity, msg.quantity, "fill quantity round-trips");
    check_eq(decoded.timestamp, msg.timestamp, "fill timestamp round-trips");
}

void test_ack_round_trip() {
    AckMsg msg{7, static_cast<uint8_t>(MsgType::NewOrder), 1};
    uint8_t buf[kAckMsgSize];
    encode(msg, buf);
    AckMsg decoded = decode_ack(buf);

    check_eq(decoded.client_order_id, msg.client_order_id, "ack client_order_id round-trips");
    check_eq(decoded.request_type, msg.request_type, "ack request_type round-trips");
    check_eq(decoded.status, msg.status, "ack status round-trips");
}

void test_payload_size_lookup() {
    check(payload_size(static_cast<uint8_t>(MsgType::NewOrder)) == kClientOrderMsgSize,
          "NewOrder payload size");
    check(payload_size(static_cast<uint8_t>(MsgType::Cancel)) == kCancelMsgSize, "Cancel payload size");
    check(payload_size(static_cast<uint8_t>(MsgType::SubscribeFills)) == 0,
          "SubscribeFills payload size is legitimately empty");
    check(payload_size(static_cast<uint8_t>(MsgType::Fill)) == kFillWireMsgSize, "Fill payload size");
    check(payload_size(static_cast<uint8_t>(MsgType::Ack)) == kAckMsgSize, "Ack payload size");
    check(!payload_size(0xFF).has_value(), "unknown tag should report nullopt");
}

} // namespace

int main() {
    test_client_order_round_trip();
    test_cancel_round_trip();
    test_fill_round_trip();
    test_ack_round_trip();
    test_payload_size_lookup();

    std::cout << (g_checks - g_failures) << "/" << g_checks << " checks passed\n";
    if (g_failures > 0) {
        std::cout << g_failures << " FAILURE(S)\n";
        return 1;
    }
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
