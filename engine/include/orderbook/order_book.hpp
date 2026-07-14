#pragma once

#include <cstddef>
#include <deque>
#include <map>
#include <optional>
#include <unordered_map>
#include <utility>
#include <vector>

#include "orderbook/order.hpp"

namespace orderbook {

struct PriceLevel {
    int64_t price_ticks;
    std::deque<Order> orders;   // FIFO within a price level = time priority
};

struct BookLevelSnapshot {
    int64_t price_ticks;
    uint32_t total_quantity;
    size_t order_count;
};

struct BookSnapshot {
    std::vector<BookLevelSnapshot> bids;  // best (highest) first
    std::vector<BookLevelSnapshot> asks;  // best (lowest) first
};

class OrderBook {
public:
    // Submits a new order, matching it against resting liquidity where
    // possible. Returns any fills generated. Rejects duplicate order ids.
    std::vector<Fill> submit(Order order);

    // Removes a resting order by id. Returns false if not found (already
    // filled or never existed) rather than throwing.
    bool cancel(uint64_t order_id);

    // True if order_id is currently resting in the book (used by callers,
    // e.g. the socket server, that need to distinguish "rejected as a
    // duplicate" from "accepted but generated no fills").
    bool contains(uint64_t order_id) const;

    std::optional<int64_t> best_bid() const;
    std::optional<int64_t> best_ask() const;

    // Top `depth` price levels on each side, best price first.
    BookSnapshot snapshot(size_t depth) const;

private:
    // Bids: highest price = best, so descending iteration.
    std::map<int64_t, PriceLevel, std::greater<int64_t>> bids_;
    // Asks: lowest price = best, so ascending iteration.
    std::map<int64_t, PriceLevel, std::less<int64_t>> asks_;

    // order_id -> (price, side), so cancel doesn't require scanning every level.
    std::unordered_map<uint64_t, std::pair<int64_t, Side>> order_index_;

    uint64_t next_timestamp_ = 0;

    std::vector<Fill> matchBuy(Order& incoming);
    std::vector<Fill> matchSell(Order& incoming);
};

} // namespace orderbook
