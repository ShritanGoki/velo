#pragma once

#include <cstdint>

namespace orderbook {

enum class Side { Buy, Sell };
enum class OrderType { Limit, Market };

struct Order {
    uint64_t id;
    Side side;
    OrderType type;
    int64_t price_ticks;   // price in integer ticks; ignored for Market orders
    uint32_t quantity;     // remaining quantity
    uint64_t timestamp;    // assigned by OrderBook on submit, for time priority
};

struct Fill {
    uint64_t resting_order_id;
    uint64_t incoming_order_id;
    int64_t price_ticks;
    uint32_t quantity;
    uint64_t timestamp;
};

} // namespace orderbook
