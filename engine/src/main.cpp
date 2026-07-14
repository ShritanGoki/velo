#include <iostream>

#include "orderbook/order_book.hpp"

using namespace orderbook;

namespace {

const char* side_str(Side s) { return s == Side::Buy ? "BUY" : "SELL"; }

void print_fills(const std::vector<Fill>& fills) {
    for (const auto& f : fills) {
        std::cout << "  FILL: resting#" << f.resting_order_id
                   << " x incoming#" << f.incoming_order_id
                   << " qty=" << f.quantity
                   << " price_ticks=" << f.price_ticks << "\n";
    }
    if (fills.empty()) std::cout << "  (no fills)\n";
}

void print_snapshot(const OrderBook& book) {
    BookSnapshot snap = book.snapshot(5);
    std::cout << "  Book snapshot (asks descending to bids):\n";
    for (auto it = snap.asks.rbegin(); it != snap.asks.rend(); ++it) {
        std::cout << "    ASK " << it->price_ticks << " x " << it->total_quantity
                   << " (" << it->order_count << " orders)\n";
    }
    for (const auto& b : snap.bids) {
        std::cout << "    BID " << b.price_ticks << " x " << b.total_quantity
                   << " (" << b.order_count << " orders)\n";
    }
}

} // namespace

int main() {
    OrderBook book;
    uint64_t next_id = 1;

    auto place = [&](Side side, OrderType type, int64_t price_ticks, uint32_t qty) {
        Order o{next_id++, side, type, price_ticks, qty, 0};
        std::cout << "SUBMIT #" << o.id << " " << side_str(side) << " "
                   << (type == OrderType::Limit ? "LIMIT" : "MARKET")
                   << " qty=" << qty << " price_ticks=" << price_ticks << "\n";
        print_fills(book.submit(o));
        return o.id;
    };

    // Rest some liquidity on both sides.
    place(Side::Sell, OrderType::Limit, 201, 60);
    place(Side::Sell, OrderType::Limit, 202, 40);
    place(Side::Buy, OrderType::Limit, 199, 50);

    // Incoming buy that fully consumes the best ask level (exact match).
    place(Side::Buy, OrderType::Limit, 201, 60);

    // Incoming buy that partially fills, then walks a level.
    place(Side::Sell, OrderType::Limit, 202, 30);
    uint64_t walker = place(Side::Buy, OrderType::Limit, 203, 50);
    (void)walker;

    // Market order with insufficient liquidity.
    place(Side::Sell, OrderType::Market, 0, 1000);

    // Cancel demo.
    uint64_t resting_bid = place(Side::Buy, OrderType::Limit, 195, 25);
    std::cout << "CANCEL #" << resting_bid << " -> "
              << (book.cancel(resting_bid) ? "ok" : "failed") << "\n";
    std::cout << "CANCEL #" << resting_bid << " again -> "
              << (book.cancel(resting_bid) ? "ok" : "failed") << "\n";

    std::cout << "\nFinal state:\n";
    print_snapshot(book);

    return 0;
}
