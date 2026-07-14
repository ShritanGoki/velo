// Integration tests for OrderBookServer: runs a real server on a loopback
// TCP port in a background thread and drives it with real client sockets,
// covering the scenarios in docs/socket-service-design-doc.md §7.

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "orderbook/protocol.hpp"
#include "orderbook/server.hpp"

using namespace orderbook;
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
        std::cerr << "FAIL: " << message << " (expected " << expected << ", got " << actual << ")\n";
    }
}

constexpr uint16_t kTestPort = 19191;

int connect_client(uint16_t port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    // The server binds its listening socket inside run(), on a background
    // thread; retry briefly rather than racing against that.
    for (int attempt = 0; attempt < 200; ++attempt) {
        if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0) {
            timeval tv{2, 0};  // 2s recv timeout so a protocol bug fails fast, not hangs
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
            return fd;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    throw std::runtime_error("could not connect to test server");
}

void send_new_order(int fd, uint64_t id, uint8_t side, uint8_t type, int64_t price, uint32_t qty) {
    uint8_t buf[1 + kClientOrderMsgSize];
    buf[0] = static_cast<uint8_t>(MsgType::NewOrder);
    encode(ClientOrderMsg{id, side, type, price, qty}, buf + 1);
    send(fd, buf, sizeof(buf), 0);
}

void send_cancel(int fd, uint64_t id) {
    uint8_t buf[1 + kCancelMsgSize];
    buf[0] = static_cast<uint8_t>(MsgType::Cancel);
    encode(CancelMsg{id}, buf + 1);
    send(fd, buf, sizeof(buf), 0);
}

struct RecvMsg {
    uint8_t tag;
    std::vector<uint8_t> payload;
    bool ok;
};

bool read_exact(int fd, uint8_t* buf, size_t len) {
    size_t got = 0;
    while (got < len) {
        ssize_t n = recv(fd, buf + got, len - got, 0);
        if (n <= 0) return false;  // timeout, disconnect, or error
        got += static_cast<size_t>(n);
    }
    return true;
}

RecvMsg recv_message(int fd) {
    uint8_t tag;
    if (!read_exact(fd, &tag, 1)) return {0, {}, false};
    size_t size = payload_size(tag);
    std::vector<uint8_t> payload(size);
    if (size > 0 && !read_exact(fd, payload.data(), size)) return {0, {}, false};
    return {tag, payload, true};
}

void test_rest_no_cross() {
    OrderBookServer server(kTestPort);
    std::thread server_thread([&] { server.run(); });
    int fd = connect_client(kTestPort);

    send_new_order(fd, 1, /*side=*/0, /*type=*/0, /*price=*/100, /*qty=*/10);
    RecvMsg msg = recv_message(fd);
    check(msg.ok, "should receive a response for a resting order");
    check_eq<uint8_t>(msg.tag, static_cast<uint8_t>(MsgType::Ack), "response should be an Ack");
    AckMsg ack = decode_ack(msg.payload.data());
    check_eq<uint8_t>(ack.status, 0, "resting order should be acked ok");

    close(fd);
    server.stop();
    server_thread.join();
}

void test_crossing_orders_produce_fill() {
    OrderBookServer server(kTestPort + 1);
    std::thread server_thread([&] { server.run(); });
    int fd = connect_client(kTestPort + 1);

    send_new_order(fd, 1, /*side=*/1 /*Sell*/, 0, 100, 50);
    RecvMsg ack1 = recv_message(fd);
    check_eq<uint8_t>(ack1.tag, static_cast<uint8_t>(MsgType::Ack), "first order gets an ack");

    send_new_order(fd, 2, /*side=*/0 /*Buy*/, 0, 100, 50);
    RecvMsg ack2 = recv_message(fd);
    check_eq<uint8_t>(ack2.tag, static_cast<uint8_t>(MsgType::Ack), "second order gets an ack");

    RecvMsg fill = recv_message(fd);
    check_eq<uint8_t>(fill.tag, static_cast<uint8_t>(MsgType::Fill), "crossing orders should produce a fill message");
    if (fill.ok && fill.tag == static_cast<uint8_t>(MsgType::Fill)) {
        FillWireMsg f = decode_fill(fill.payload.data());
        check_eq<uint64_t>(f.resting_order_id, 1, "fill should reference resting order 1");
        check_eq<uint64_t>(f.incoming_order_id, 2, "fill should reference incoming order 2");
        check_eq<uint32_t>(f.quantity, 50, "fill quantity should be 50");
    }

    close(fd);
    server.stop();
    server_thread.join();
}

void test_two_clients_both_get_fill() {
    OrderBookServer server(kTestPort + 2);
    std::thread server_thread([&] { server.run(); });
    int fd_a = connect_client(kTestPort + 2);
    int fd_b = connect_client(kTestPort + 2);

    send_new_order(fd_a, 1, /*side=*/1 /*Sell*/, 0, 100, 50);
    recv_message(fd_a);  // ack

    send_new_order(fd_b, 2, /*side=*/0 /*Buy*/, 0, 100, 50);
    recv_message(fd_b);  // ack for B's own order

    RecvMsg fill_b = recv_message(fd_b);
    check_eq<uint8_t>(fill_b.tag, static_cast<uint8_t>(MsgType::Fill),
                       "the incoming client (B) should receive a fill");

    RecvMsg fill_a = recv_message(fd_a);
    check_eq<uint8_t>(fill_a.tag, static_cast<uint8_t>(MsgType::Fill),
                       "the resting order's owner (A) should also receive a fill");

    close(fd_a);
    close(fd_b);
    server.stop();
    server_thread.join();
}

void test_cancel_round_trip() {
    OrderBookServer server(kTestPort + 3);
    std::thread server_thread([&] { server.run(); });
    int fd = connect_client(kTestPort + 3);

    send_new_order(fd, 1, 0, 0, 100, 10);
    recv_message(fd);  // ack

    send_cancel(fd, 1);
    RecvMsg ack1 = recv_message(fd);
    AckMsg cancel_ack1 = decode_ack(ack1.payload.data());
    check_eq<uint8_t>(cancel_ack1.status, 0, "cancelling a resting order should succeed");

    send_cancel(fd, 1);
    RecvMsg ack2 = recv_message(fd);
    AckMsg cancel_ack2 = decode_ack(ack2.payload.data());
    check_eq<uint8_t>(cancel_ack2.status, 1, "cancelling an already-cancelled order should be rejected");

    close(fd);
    server.stop();
    server_thread.join();
}

void test_malformed_tag_closes_only_that_connection() {
    OrderBookServer server(kTestPort + 4);
    std::thread server_thread([&] { server.run(); });
    int bad_fd = connect_client(kTestPort + 4);
    int good_fd = connect_client(kTestPort + 4);

    uint8_t garbage[1] = {0xEE};
    send(bad_fd, garbage, sizeof(garbage), 0);

    // The good connection should still work fine after the bad one is dropped.
    send_new_order(good_fd, 1, 0, 0, 100, 10);
    RecvMsg msg = recv_message(good_fd);
    check(msg.ok, "a well-behaved connection should be unaffected by another connection's malformed message");
    check_eq<uint8_t>(msg.tag, static_cast<uint8_t>(MsgType::Ack), "good connection still gets a proper ack");

    close(bad_fd);
    close(good_fd);
    server.stop();
    server_thread.join();
}

} // namespace

int main() {
    test_rest_no_cross();
    test_crossing_orders_produce_fill();
    test_two_clients_both_get_fill();
    test_cancel_round_trip();
    test_malformed_tag_closes_only_that_connection();

    std::cout << (g_checks - g_failures) << "/" << g_checks << " checks passed\n";
    if (g_failures > 0) {
        std::cout << g_failures << " FAILURE(S)\n";
        return 1;
    }
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
