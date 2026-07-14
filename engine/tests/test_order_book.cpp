// Minimal self-contained test harness (no external test framework dependency)
// covering the 8 cases from docs/orderbook-design-doc.md §8.

#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>

#include "orderbook/order_book.hpp"

using namespace orderbook;

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

Order make(uint64_t id, Side side, OrderType type, int64_t price, uint32_t qty) {
    return Order{id, side, type, price, qty, 0};
}

void test_rests_with_no_cross() {
    OrderBook book;
    auto fills = book.submit(make(1, Side::Buy, OrderType::Limit, 100, 10));
    check(fills.empty(), "no-cross limit order should not generate fills");
    check_eq<int64_t>(*book.best_bid(), 100, "resting order should become best bid");

    auto snap = book.snapshot(5);
    check_eq<size_t>(snap.bids.size(), 1, "snapshot should show one bid level");
    check_eq<uint32_t>(snap.bids[0].total_quantity, 10, "snapshot bid qty should match");
}

void test_exact_full_cross() {
    OrderBook book;
    book.submit(make(1, Side::Sell, OrderType::Limit, 100, 50));
    auto fills = book.submit(make(2, Side::Buy, OrderType::Limit, 100, 50));

    check_eq<size_t>(fills.size(), 1, "exact cross should produce exactly one fill");
    if (!fills.empty()) {
        check_eq<uint32_t>(fills[0].quantity, 50, "fill quantity should be 50");
        check_eq<uint64_t>(fills[0].resting_order_id, 1, "resting order id should be 1");
        check_eq<uint64_t>(fills[0].incoming_order_id, 2, "incoming order id should be 2");
    }
    check(!book.best_ask().has_value(), "ask level should be fully removed");
    check(!book.best_bid().has_value(), "no remainder should rest");
}

void test_partial_fill() {
    OrderBook book;
    book.submit(make(1, Side::Sell, OrderType::Limit, 100, 60));
    auto fills = book.submit(make(2, Side::Buy, OrderType::Limit, 100, 100));

    check_eq<size_t>(fills.size(), 1, "partial fill should still be one fill event");
    if (!fills.empty()) check_eq<uint32_t>(fills[0].quantity, 60, "partial fill quantity should be 60");

    check(book.best_ask().has_value() == false, "resting sell fully consumed, level removed");
    check(book.best_bid().has_value(), "incoming remainder (40) should rest as a bid");
    if (book.best_bid()) check_eq<int64_t>(*book.best_bid(), 100, "remainder rests at incoming price");

    auto snap = book.snapshot(5);
    check_eq<uint32_t>(snap.bids[0].total_quantity, 40, "remaining resting quantity should be 40");
}

void test_walks_multiple_levels() {
    OrderBook book;
    book.submit(make(1, Side::Sell, OrderType::Limit, 100, 10));
    book.submit(make(2, Side::Sell, OrderType::Limit, 101, 10));
    auto fills = book.submit(make(3, Side::Buy, OrderType::Limit, 101, 15));

    check_eq<size_t>(fills.size(), 2, "walking two levels should produce two fills");
    if (fills.size() == 2) {
        check_eq<int64_t>(fills[0].price_ticks, 100, "first fill should be at best (lower) ask price");
        check_eq<uint32_t>(fills[0].quantity, 10, "first fill should fully consume level 100");
        check_eq<int64_t>(fills[1].price_ticks, 101, "second fill should be at next ask price");
        check_eq<uint32_t>(fills[1].quantity, 5, "second fill should be the remaining 5");
    }
    check(!book.best_bid().has_value(), "incoming order should be fully filled, nothing rests");
}

void test_market_order_insufficient_liquidity() {
    OrderBook book;
    book.submit(make(1, Side::Sell, OrderType::Limit, 100, 10));
    auto fills = book.submit(make(2, Side::Buy, OrderType::Market, 0, 100));

    check_eq<size_t>(fills.size(), 1, "market order should fill what liquidity exists");
    if (!fills.empty()) check_eq<uint32_t>(fills[0].quantity, 10, "fill should be capped by available liquidity");
    check(!book.best_ask().has_value(), "ask side should be emptied");
    check(!book.best_bid().has_value(), "unfilled market remainder must not rest in the book");
}

void test_time_priority_same_price() {
    OrderBook book;
    book.submit(make(1, Side::Sell, OrderType::Limit, 100, 10));
    book.submit(make(2, Side::Sell, OrderType::Limit, 100, 10));
    auto fills = book.submit(make(3, Side::Buy, OrderType::Limit, 100, 10));

    check_eq<size_t>(fills.size(), 1, "should match exactly one resting order");
    if (!fills.empty()) {
        check_eq<uint64_t>(fills[0].resting_order_id, 1,
                            "first-in resting order at the same price should match first");
    }
}

void test_cancel_resting_order() {
    OrderBook book;
    book.submit(make(1, Side::Buy, OrderType::Limit, 100, 10));
    bool ok = book.cancel(1);
    check(ok, "cancel of an existing resting order should succeed");
    check(!book.best_bid().has_value(), "book should be empty after cancelling its only order");

    auto fills = book.submit(make(2, Side::Sell, OrderType::Limit, 100, 10));
    check(fills.empty(), "cancelled order must not be matchable afterward");
}

void test_cancel_unknown_order() {
    OrderBook book;
    bool ok = book.cancel(999);
    check(!ok, "cancel of a nonexistent order id should return false, not throw");

    book.submit(make(1, Side::Buy, OrderType::Limit, 100, 10));
    book.submit(make(2, Side::Sell, OrderType::Limit, 100, 10));  // fully fills order 1
    ok = book.cancel(1);
    check(!ok, "cancel of an already-filled order should return false");
}

} // namespace

int main() {
    test_rests_with_no_cross();
    test_exact_full_cross();
    test_partial_fill();
    test_walks_multiple_levels();
    test_market_order_insufficient_liquidity();
    test_time_priority_same_price();
    test_cancel_resting_order();
    test_cancel_unknown_order();

    std::cout << (g_checks - g_failures) << "/" << g_checks << " checks passed\n";
    if (g_failures > 0) {
        std::cout << g_failures << " FAILURE(S)\n";
        return 1;
    }
    std::cout << "ALL TESTS PASSED\n";
    return 0;
}
