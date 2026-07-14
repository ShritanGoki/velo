#include "orderbook/order_book.hpp"

#include <algorithm>

namespace orderbook {

std::vector<Fill> OrderBook::matchBuy(Order& incoming) {
    std::vector<Fill> fills;

    while (incoming.quantity > 0 && !asks_.empty()) {
        auto level_it = asks_.begin();
        PriceLevel& level = level_it->second;

        bool crosses = incoming.type == OrderType::Market ||
                       incoming.price_ticks >= level.price_ticks;
        if (!crosses) break;

        while (incoming.quantity > 0 && !level.orders.empty()) {
            Order& resting = level.orders.front();
            uint32_t traded = std::min(incoming.quantity, resting.quantity);

            fills.push_back(Fill{resting.id, incoming.id, level.price_ticks,
                                  traded, next_timestamp_++});

            incoming.quantity -= traded;
            resting.quantity -= traded;

            if (resting.quantity == 0) {
                order_index_.erase(resting.id);
                level.orders.pop_front();
            }
        }

        if (level.orders.empty()) {
            asks_.erase(level_it);
        }
    }

    return fills;
}

std::vector<Fill> OrderBook::matchSell(Order& incoming) {
    std::vector<Fill> fills;

    while (incoming.quantity > 0 && !bids_.empty()) {
        auto level_it = bids_.begin();
        PriceLevel& level = level_it->second;

        bool crosses = incoming.type == OrderType::Market ||
                       incoming.price_ticks <= level.price_ticks;
        if (!crosses) break;

        while (incoming.quantity > 0 && !level.orders.empty()) {
            Order& resting = level.orders.front();
            uint32_t traded = std::min(incoming.quantity, resting.quantity);

            fills.push_back(Fill{resting.id, incoming.id, level.price_ticks,
                                  traded, next_timestamp_++});

            incoming.quantity -= traded;
            resting.quantity -= traded;

            if (resting.quantity == 0) {
                order_index_.erase(resting.id);
                level.orders.pop_front();
            }
        }

        if (level.orders.empty()) {
            bids_.erase(level_it);
        }
    }

    return fills;
}

std::vector<Fill> OrderBook::submit(Order order) {
    // Reject duplicate order ids outright rather than corrupting order_index_.
    if (order_index_.count(order.id) > 0) {
        return {};
    }

    order.timestamp = next_timestamp_++;

    std::vector<Fill> fills = (order.side == Side::Buy) ? matchBuy(order)
                                                          : matchSell(order);

    if (order.quantity > 0 && order.type == OrderType::Limit) {
        if (order.side == Side::Buy) {
            auto [it, inserted] = bids_.try_emplace(order.price_ticks, PriceLevel{order.price_ticks, {}});
            it->second.orders.push_back(order);
        } else {
            auto [it, inserted] = asks_.try_emplace(order.price_ticks, PriceLevel{order.price_ticks, {}});
            it->second.orders.push_back(order);
        }
        order_index_[order.id] = {order.price_ticks, order.side};
    }
    // Market orders with remaining quantity: insufficient liquidity, remainder
    // is simply dropped (caller can detect this by comparing filled vs. requested).

    return fills;
}

bool OrderBook::cancel(uint64_t order_id) {
    auto idx_it = order_index_.find(order_id);
    if (idx_it == order_index_.end()) return false;

    auto [price, side] = idx_it->second;

    if (side == Side::Buy) {
        auto level_it = bids_.find(price);
        if (level_it == bids_.end()) return false;
        auto& orders = level_it->second.orders;
        auto order_it = std::find_if(orders.begin(), orders.end(),
                                      [order_id](const Order& o) { return o.id == order_id; });
        if (order_it == orders.end()) return false;
        orders.erase(order_it);
        if (orders.empty()) bids_.erase(level_it);
    } else {
        auto level_it = asks_.find(price);
        if (level_it == asks_.end()) return false;
        auto& orders = level_it->second.orders;
        auto order_it = std::find_if(orders.begin(), orders.end(),
                                      [order_id](const Order& o) { return o.id == order_id; });
        if (order_it == orders.end()) return false;
        orders.erase(order_it);
        if (orders.empty()) asks_.erase(level_it);
    }

    order_index_.erase(idx_it);
    return true;
}

bool OrderBook::contains(uint64_t order_id) const {
    return order_index_.count(order_id) > 0;
}

std::optional<int64_t> OrderBook::best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.begin()->first;
}

std::optional<int64_t> OrderBook::best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

BookSnapshot OrderBook::snapshot(size_t depth) const {
    BookSnapshot snap;

    for (const auto& [price, level] : bids_) {
        if (snap.bids.size() >= depth) break;
        uint32_t total = 0;
        for (const auto& o : level.orders) total += o.quantity;
        snap.bids.push_back({price, total, level.orders.size()});
    }

    for (const auto& [price, level] : asks_) {
        if (snap.asks.size() >= depth) break;
        uint32_t total = 0;
        for (const auto& o : level.orders) total += o.quantity;
        snap.asks.push_back({price, total, level.orders.size()});
    }

    return snap;
}

} // namespace orderbook
