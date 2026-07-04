# Limit Order Book — Design Doc (Milestone 1: C++ Core)

## 1. Purpose

A single-instrument (ES futures) limit order book that accepts orders, matches
them against resting liquidity using price-time priority, and reports fills.
This is the foundation every later milestone (sockets, Rust risk engine,
Python strategy layer) sits on top of — so correctness and a clean interface
matter more here than speed, at least for Milestone 1.

## 2. Order Types Supported

| Type | Behavior |
|---|---|
| **Limit** | Rests in the book at a specified price if it doesn't immediately cross. If it crosses, it fills against the best opposing price(s) up to its size, and any unfilled remainder rests. |
| **Market** | Executes immediately against the best available opposing price(s) until fully filled or the book is empty on that side. Never rests. |
| **Cancel** | Removes a specific resting order (by order ID) from the book. No-op if already filled/not found — return a status rather than throwing. |

Not in scope for Milestone 1 (add later if needed): stop orders, iceberg
orders, IOC/FOK time-in-force flags, order modification (cancel+replace is
enough for now).

## 3. Core Data Model

```cpp
enum class Side { Buy, Sell };
enum class OrderType { Limit, Market };

struct Order {
    uint64_t id;
    Side side;
    OrderType type;
    int64_t price_ticks;   // price in integer ticks, NOT double — see §6
    uint32_t quantity;     // remaining quantity
    uint64_t timestamp;    // monotonic counter or nanosecond clock, for time priority
};

struct Fill {
    uint64_t resting_order_id;
    uint64_t incoming_order_id;
    int64_t price_ticks;
    uint32_t quantity;
    uint64_t timestamp;
};
```

## 4. Price Level Storage

Each side of the book is a collection of **price levels**, each holding a
time-ordered queue of resting orders at that price.

```cpp
struct PriceLevel {
    int64_t price_ticks;
    std::deque<Order> orders;   // FIFO within a price level = time priority
};

// Bids: highest price = best, so we want descending iteration
std::map<int64_t, PriceLevel, std::greater<int64_t>> bids;

// Asks: lowest price = best, so ascending iteration
std::map<int64_t, PriceLevel, std::less<int64_t>> asks;
```

**Why `std::map` and not a hash map:** we need ordered iteration (best price
first) and efficient "give me the best price level" access. `std::map` is a
red-black tree — O(log n) insert/erase/lookup, and iterates in sorted order
for free, which is exactly the traversal pattern matching needs (walk price
levels from best outward until the incoming order is filled or exhausted).

**Why `std::deque` inside a level and not `std::vector`:** cancels can happen
anywhere in the queue (not just at the back), and `deque` gives O(1)
push/pop at both ends with reasonable erase-in-middle cost. A `vector` would
force costly shifts on cancel of a non-last order.

**Order ID lookup for cancels:** maintain a side-channel index so cancel
doesn't require scanning every price level:

```cpp
std::unordered_map<uint64_t, std::pair<int64_t /*price*/, Side>> order_index;
```

This gives O(1) "which price level is this order ID in," then O(1) amortized
removal from that level's deque (search within a single level's typically
small queue, or use an iterator stored at insert time to avoid even that
scan).

## 5. Matching Logic

**On incoming Limit or Market order (say, an incoming Buy):**

1. While the incoming order still has quantity remaining AND the best ask
   exists AND (order is Market, OR incoming price >= best ask price):
   - Take the best ask price level (front of the `asks` map)
   - Match against orders in that level's deque, front to back (time priority)
   - For each match: generate a `Fill`, decrement both quantities
   - If a resting order's quantity hits zero, pop it from the deque and
     remove it from `order_index`
   - If the price level's deque becomes empty, erase the price level from
     the map
2. If the incoming order still has quantity left after the loop:
   - If it's a Limit order → insert it as a new resting order at its price
     (create the price level if it doesn't exist)
   - If it's a Market order → the remainder is simply unfilled (no
     resting market orders) — log this as "insufficient liquidity"

Mirror this logic for incoming Sell orders against the `bids` side.

**Partial fill example to test explicitly:** incoming Buy 100 @ 50.25 vs.
resting Sell 60 @ 50.25 → one Fill for 60, incoming order still has 40
remaining, which then either matches the next ask level or rests at 50.25
depending on price.

## 6. Why Integer Ticks, Not Doubles, for Price

Floating-point price comparison (`0.1 + 0.2 != 0.3` territory) is a classic
source of subtle matching bugs — an order that should tie at the same price
might not compare equal, breaking price-level grouping. ES futures trade in
fixed tick increments (0.25 index points = $12.50/tick), so represent price
internally as an integer count of ticks and only convert to a decimal for
display/logging. This also matches how real exchanges represent price
internally.

## 7. API Surface (what other components will call)

```cpp
class OrderBook {
public:
    std::vector<Fill> submit(const Order& order);  // returns any fills generated
    bool cancel(uint64_t order_id);                 // returns success/failure
    std::optional<int64_t> best_bid() const;
    std::optional<int64_t> best_ask() const;
    // snapshot for debugging/logging — top N levels each side
    BookSnapshot snapshot(size_t depth) const;
};
```

Keeping this interface narrow and stable now matters because Milestone 2
wraps this exact class in a socket server — the less it changes later, the
less rework across the whole pipeline.

## 8. Test Cases to Write First (before wiring up sockets/anything else)

1. Limit order with no cross → rests in book, appears in snapshot
2. Limit order that fully crosses one resting order exactly → one fill, both
   orders fully consumed, level removed
3. Limit order that partially fills a resting order → fill for partial
   quantity, resting order's remaining quantity updated, level NOT removed
4. Limit order that walks through multiple price levels → multiple fills at
   different prices, correct fill-price-per-fill (not incoming order's
   limit price)
5. Market order with insufficient liquidity → fills what it can, remainder
   silently dropped (log a warning)
6. Two resting orders at the same price → time priority respected (first in,
   first matched)
7. Cancel a resting order → removed from book, subsequent match doesn't touch
   it, `order_index` entry removed
8. Cancel an order ID that doesn't exist or is already filled → returns
   failure, doesn't throw/crash

## 9. Explicitly Deferred to Later Milestones

- Networking/socket interface (Milestone 2)
- Multithreading/concurrency (single-threaded, deterministic core is
  intentional for Milestone 1 — correctness first, latency benchmarking
  comes once correctness is proven)
- Persistence/replay logs
- Any FIX-protocol-style message format (later depth phase)

## 10. Definition of Done for Milestone 1

- All 8 test cases in §8 pass
- A `main.cpp` that feeds a small hardcoded sequence of orders through the
  book and prints resulting fills + final book snapshot to stdout
- No sockets, no Python, no Rust — pure C++, compiles and runs standalone
