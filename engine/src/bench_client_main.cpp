// End-to-end socket benchmark: a real TCP client hitting a running
// orderbook_server, measuring the full wire round-trip cost (framing,
// syscalls, the OS network stack on loopback) on top of the matching
// engine itself. Two numbers, because they answer different questions:
//   - sequential: submit one order, wait for its ack, repeat - the
//     per-request latency a single synchronous client actually
//     experiences (this is what OrderBookClient/strategy do today).
//   - pipelined: fire all orders without waiting, then drain all acks -
//     the server's maximum sustainable throughput, unconstrained by one
//     client waiting on each round trip.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>

#include "orderbook/protocol.hpp"

using namespace orderbook::proto;

namespace {

int connect_to(const char* host, uint16_t port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) throw std::runtime_error("socket() failed");

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        throw std::runtime_error("invalid host address");
    }
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        throw std::runtime_error(std::string("connect() failed: ") + strerror(errno));
    }
    return fd;
}

void send_new_order(int fd, uint64_t id, uint8_t side, int64_t price, uint32_t qty) {
    uint8_t buf[1 + kClientOrderMsgSize];
    buf[0] = static_cast<uint8_t>(MsgType::NewOrder);
    encode(ClientOrderMsg{id, side, /*order_type=*/0, price, qty}, buf + 1);
    if (send(fd, buf, sizeof(buf), 0) != static_cast<ssize_t>(sizeof(buf))) {
        throw std::runtime_error("send() failed");
    }
}

bool read_exact(int fd, uint8_t* buf, size_t len) {
    size_t got = 0;
    while (got < len) {
        ssize_t n = recv(fd, buf + got, len - got, 0);
        if (n <= 0) return false;
        got += static_cast<size_t>(n);
    }
    return true;
}

// Reads and discards exactly one server->client message (Ack or Fill),
// returning its tag. Orders in this benchmark never cross (see
// generate_price below), so only Acks are ever expected, but this
// handles either correctly.
uint8_t read_one_message(int fd) {
    uint8_t tag;
    if (!read_exact(fd, &tag, 1)) throw std::runtime_error("connection closed unexpectedly");
    std::optional<size_t> size = payload_size(tag);
    if (!size.has_value()) throw std::runtime_error("unknown message tag from server");
    std::vector<uint8_t> payload(*size);
    if (*size > 0 && !read_exact(fd, payload.data(), *size)) {
        throw std::runtime_error("connection closed mid-message");
    }
    return tag;
}

double bench_sequential(int fd, size_t count) {
    auto start = std::chrono::steady_clock::now();
    for (size_t i = 0; i < count; ++i) {
        // Ever-increasing, never-crossing prices: this benchmark measures
        // wire/socket overhead, not matching-engine work (bench_main
        // already covers that in isolation).
        send_new_order(fd, i + 1, /*side=*/0, /*price=*/10000 + static_cast<int64_t>(i), 10);
        read_one_message(fd);
    }
    auto end = std::chrono::steady_clock::now();
    return std::chrono::duration<double>(end - start).count();
}

double bench_pipelined(int fd, size_t count, size_t id_offset) {
    auto start = std::chrono::steady_clock::now();
    for (size_t i = 0; i < count; ++i) {
        send_new_order(fd, id_offset + i + 1, /*side=*/0,
                        /*price=*/20000 + static_cast<int64_t>(i), 10);
    }
    for (size_t i = 0; i < count; ++i) {
        read_one_message(fd);
    }
    auto end = std::chrono::steady_clock::now();
    return std::chrono::duration<double>(end - start).count();
}

void report(const char* label, size_t count, double elapsed_sec) {
    double per_sec = static_cast<double>(count) / elapsed_sec;
    double us_per_op = elapsed_sec * 1e6 / static_cast<double>(count);
    std::cout << label << ":\n";
    std::cout << "  elapsed:    " << elapsed_sec << " s\n";
    std::cout << "  throughput: " << static_cast<uint64_t>(per_sec) << " orders/sec\n";
    std::cout << "  avg:        " << us_per_op << " us/order\n";
}

} // namespace

int main(int argc, char** argv) {
    const char* host = "127.0.0.1";
    uint16_t port = 9999;
    size_t count = 100000;
    if (argc > 1) port = static_cast<uint16_t>(std::atoi(argv[1]));
    if (argc > 2) count = static_cast<size_t>(std::stoull(argv[2]));

    int fd = connect_to(host, port);

    double seq_elapsed = bench_sequential(fd, count);
    report("sequential (submit, wait for ack, repeat)", count, seq_elapsed);

    double pipe_elapsed = bench_pipelined(fd, count, /*id_offset=*/count);
    report("pipelined (fire all, then drain all acks)", count, pipe_elapsed);

    close(fd);
    return 0;
}
