#include "orderbook/server.hpp"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#include "orderbook/order_book.hpp"
#include "orderbook/protocol.hpp"

namespace orderbook {

namespace {

struct Connection {
    int fd;
    std::vector<uint8_t> read_buf;
};

void set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

// Best-effort send of a small, complete message. Messages here are at most
// a few dozen bytes, far under any realistic socket send buffer, so this
// simple retry-on-EAGAIN loop won't meaningfully stall the event loop.
// Real backpressure handling is deferred (see design doc §8/§5 note on
// slow clients).
void send_all(int fd, const uint8_t* data, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = ::send(fd, data + sent, len - sent, 0);
        if (n > 0) {
            sent += static_cast<size_t>(n);
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        break;  // connection broken; drop silently, poll() notices it next pass
    }
}

} // namespace

struct OrderBookServer::Impl {
    uint16_t port;
    int listen_fd = -1;
    std::atomic<bool> stopped{false};
    OrderBook book;
    std::vector<Connection> connections;
    std::unordered_map<uint64_t, int> order_owner_fd;  // order id -> owning connection's fd

    void start_listening();
    void accept_new_connections();
    void handle_readable(Connection& conn);
    void dispatch_new_order(Connection& conn, const proto::ClientOrderMsg& msg);
    void dispatch_cancel(Connection& conn, const proto::CancelMsg& msg);
    void remove_connection(int fd);
    void send_ack(int fd, uint64_t order_id, uint8_t request_type, uint8_t status);
    void send_fill(int fd, const Fill& fill);
};

void OrderBookServer::Impl::start_listening() {
    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) throw std::runtime_error("socket() failed");

    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    if (bind(listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        throw std::runtime_error("bind() failed: " + std::string(strerror(errno)));
    }
    if (listen(listen_fd, /*backlog=*/16) < 0) {
        throw std::runtime_error("listen() failed: " + std::string(strerror(errno)));
    }
    set_nonblocking(listen_fd);
}

void OrderBookServer::Impl::accept_new_connections() {
    for (;;) {
        int fd = accept(listen_fd, nullptr, nullptr);
        if (fd < 0) break;  // EAGAIN/EWOULDBLOCK: no more pending connections
        set_nonblocking(fd);
        connections.push_back(Connection{fd, {}});
    }
}

void OrderBookServer::Impl::send_ack(int fd, uint64_t order_id, uint8_t request_type, uint8_t status) {
    uint8_t buf[1 + proto::kAckMsgSize];
    buf[0] = static_cast<uint8_t>(proto::MsgType::Ack);
    proto::encode(proto::AckMsg{order_id, request_type, status}, buf + 1);
    send_all(fd, buf, sizeof(buf));
}

void OrderBookServer::Impl::send_fill(int fd, const Fill& fill) {
    uint8_t buf[1 + proto::kFillWireMsgSize];
    buf[0] = static_cast<uint8_t>(proto::MsgType::Fill);
    proto::encode(proto::FillWireMsg{fill.resting_order_id, fill.incoming_order_id,
                                      fill.price_ticks, fill.quantity, fill.timestamp},
                  buf + 1);
    send_all(fd, buf, sizeof(buf));
}

void OrderBookServer::Impl::dispatch_new_order(Connection& conn, const proto::ClientOrderMsg& msg) {
    constexpr uint8_t kNewOrderTag = static_cast<uint8_t>(proto::MsgType::NewOrder);

    if (book.contains(msg.client_order_id)) {
        send_ack(conn.fd, msg.client_order_id, kNewOrderTag, /*status=*/1);
        return;
    }

    Order order{msg.client_order_id,
                msg.side == 0 ? Side::Buy : Side::Sell,
                msg.order_type == 0 ? OrderType::Limit : OrderType::Market,
                msg.price_ticks,
                msg.quantity,
                0};

    std::vector<Fill> fills = book.submit(order);
    send_ack(conn.fd, msg.client_order_id, kNewOrderTag, /*status=*/0);

    if (book.contains(msg.client_order_id)) {
        order_owner_fd[msg.client_order_id] = conn.fd;
    }

    for (const Fill& f : fills) {
        send_fill(conn.fd, f);
        auto it = order_owner_fd.find(f.resting_order_id);
        if (it != order_owner_fd.end() && it->second != conn.fd) {
            send_fill(it->second, f);
        }
        if (!book.contains(f.resting_order_id)) {
            order_owner_fd.erase(f.resting_order_id);
        }
    }
}

void OrderBookServer::Impl::dispatch_cancel(Connection& conn, const proto::CancelMsg& msg) {
    bool ok = book.cancel(msg.client_order_id);
    if (ok) order_owner_fd.erase(msg.client_order_id);
    send_ack(conn.fd, msg.client_order_id, static_cast<uint8_t>(proto::MsgType::Cancel), ok ? 0 : 1);
}

void OrderBookServer::Impl::handle_readable(Connection& conn) {
    uint8_t chunk[4096];
    ssize_t n = ::recv(conn.fd, chunk, sizeof(chunk), 0);
    if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
        remove_connection(conn.fd);
        return;
    }
    if (n > 0) {
        conn.read_buf.insert(conn.read_buf.end(), chunk, chunk + n);
    }

    for (;;) {
        if (conn.read_buf.empty()) return;
        uint8_t tag = conn.read_buf[0];
        size_t payload = proto::payload_size(tag);
        if (payload == 0) {
            std::cerr << "orderbook::server: unknown message tag " << int(tag)
                      << ", closing connection\n";
            remove_connection(conn.fd);
            return;
        }
        if (conn.read_buf.size() < 1 + payload) return;  // wait for more bytes

        const uint8_t* body = conn.read_buf.data() + 1;
        switch (static_cast<proto::MsgType>(tag)) {
            case proto::MsgType::NewOrder:
                dispatch_new_order(conn, proto::decode_client_order(body));
                break;
            case proto::MsgType::Cancel:
                dispatch_cancel(conn, proto::decode_cancel(body));
                break;
            default:
                std::cerr << "orderbook::server: unexpected client->server tag " << int(tag)
                          << ", closing connection\n";
                remove_connection(conn.fd);
                return;
        }
        conn.read_buf.erase(conn.read_buf.begin(), conn.read_buf.begin() + 1 + payload);
    }
}

void OrderBookServer::Impl::remove_connection(int fd) {
    close(fd);
    connections.erase(std::remove_if(connections.begin(), connections.end(),
                                      [fd](const Connection& c) { return c.fd == fd; }),
                       connections.end());
}

OrderBookServer::OrderBookServer(uint16_t port) : impl_(std::make_unique<Impl>()) {
    impl_->port = port;
}

OrderBookServer::~OrderBookServer() {
    if (impl_->listen_fd >= 0) close(impl_->listen_fd);
    for (auto& c : impl_->connections) close(c.fd);
}

void OrderBookServer::stop() { impl_->stopped = true; }

void OrderBookServer::run() {
    impl_->start_listening();

    while (!impl_->stopped) {
        std::vector<pollfd> fds;
        fds.push_back({impl_->listen_fd, POLLIN, 0});
        for (auto& c : impl_->connections) fds.push_back({c.fd, POLLIN, 0});

        int n = poll(fds.data(), fds.size(), /*timeout_ms=*/100);
        if (n <= 0) continue;

        if (fds[0].revents & POLLIN) impl_->accept_new_connections();

        for (size_t i = 1; i < fds.size(); ++i) {
            if (!(fds[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            int fd = fds[i].fd;
            auto it = std::find_if(impl_->connections.begin(), impl_->connections.end(),
                                    [fd](const Connection& c) { return c.fd == fd; });
            if (it == impl_->connections.end()) continue;  // already removed this pass
            impl_->handle_readable(*it);
        }
    }
}

} // namespace orderbook
