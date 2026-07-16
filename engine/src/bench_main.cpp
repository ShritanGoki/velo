// In-process OrderBook throughput benchmark - no sockets, no protocol
// overhead, just the matching engine itself. Measures orders/sec for a
// workload that generates a realistic mix of resting and crossing
// orders, so the book is doing real matching work, not just appending
// to a queue.
#include <chrono>
#include <cstdint>
#include <iostream>
#include <random>

#include "orderbook/order_book.hpp"

using namespace orderbook;

namespace {

// A tight price band around a moving mid-price so a meaningful fraction
// of orders actually cross resting liquidity, instead of just resting
// forever and never exercising the matching loop.
struct Workload {
    std::vector<Order> orders;
};

Workload generate_workload(size_t count, uint64_t seed) {
    std::mt19937_64 rng(seed);
    std::uniform_int_distribution<int> side_dist(0, 1);
    std::uniform_int_distribution<int> offset_dist(-5, 5);
    std::uniform_int_distribution<uint32_t> qty_dist(1, 20);

    Workload wl;
    wl.orders.reserve(count);

    int64_t mid = 10000;
    for (size_t i = 0; i < count; ++i) {
        mid += offset_dist(rng) % 3;  // slow drift so the book keeps moving
        Side side = side_dist(rng) == 0 ? Side::Buy : Side::Sell;
        int64_t price = mid + offset_dist(rng);
        wl.orders.push_back(Order{static_cast<uint64_t>(i + 1), side, OrderType::Limit,
                                   price, qty_dist(rng), 0});
    }
    return wl;
}

} // namespace

int main(int argc, char** argv) {
    size_t count = 1'000'000;
    if (argc > 1) count = static_cast<size_t>(std::stoull(argv[1]));

    Workload wl = generate_workload(count, /*seed=*/42);

    OrderBook book;
    size_t total_fills = 0;

    auto start = std::chrono::steady_clock::now();
    for (const Order& o : wl.orders) {
        total_fills += book.submit(o).size();
    }
    auto end = std::chrono::steady_clock::now();

    double elapsed_sec = std::chrono::duration<double>(end - start).count();
    double orders_per_sec = static_cast<double>(count) / elapsed_sec;
    double ns_per_order = elapsed_sec * 1e9 / static_cast<double>(count);

    std::cout << "orders submitted:   " << count << "\n";
    std::cout << "fills generated:    " << total_fills << "\n";
    std::cout << "elapsed:            " << elapsed_sec << " s\n";
    std::cout << "throughput:         " << static_cast<uint64_t>(orders_per_sec) << " orders/sec\n";
    std::cout << "avg latency/order:  " << ns_per_order << " ns\n";

    return 0;
}
