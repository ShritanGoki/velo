#pragma once

#include <cstdint>
#include <memory>

namespace orderbook {

// A single-threaded, poll()-based TCP server wrapping one OrderBook
// instance. See docs/socket-service-design-doc.md for the wire protocol
// and concurrency model.
class OrderBookServer {
public:
    explicit OrderBookServer(uint16_t port);
    ~OrderBookServer();

    OrderBookServer(const OrderBookServer&) = delete;
    OrderBookServer& operator=(const OrderBookServer&) = delete;

    // Binds/listens, then blocks servicing connections until stop() is
    // called (from another thread) or the process is killed.
    void run();

    // Asks run() to return after its current poll() iteration completes.
    void stop();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace orderbook
