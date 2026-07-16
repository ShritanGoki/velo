#!/usr/bin/env python3
"""Naive Python Black-Scholes pricer - the honest "what would this cost
without any of this project's C++/Rust work" baseline for the three-way
benchmark comparison in docs/options-pricing-design-doc.md §6.

Deliberately plain: no NumPy, no vectorization, a Python for-loop calling
math.erf per option - this is what "just write it in Python" looks like,
which is exactly the point of comparing against it.

Usage:
    python3 bench/naive_black_scholes.py [count]
"""
from __future__ import annotations

import math
import sys
import time


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def price(spot: float, strike: float, rate: float, vol: float, time_to_expiry: float, is_call: bool) -> float:
    phi = 1.0 if is_call else -1.0
    sqrt_t = math.sqrt(time_to_expiry)
    vol_sqrt_t = vol * sqrt_t

    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * time_to_expiry) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t

    discount = math.exp(-rate * time_to_expiry)
    return phi * (spot * norm_cdf(phi * d1) - strike * discount * norm_cdf(phi * d2))


def make_batch(count: int) -> list:
    batch = []
    for i in range(count):
        batch.append((
            80.0 + (i % 40),      # spot
            100.0,                 # strike
            0.03,                   # rate
            0.2,                    # vol
            0.25 + (i % 8) * 0.125,  # time_to_expiry
            i % 2 == 0,             # is_call
        ))
    return batch


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4096
    batch = make_batch(count)

    # A few warmup passes so we're not timing Python's own startup/JIT-less
    # warmup noise, then several timed repeats, reporting the best (least
    # noisy) of them - standard practice for a plain wall-clock benchmark
    # without a statistics package doing this for us the way Criterion does
    # on the Rust side.
    for _ in range(3):
        for spot, strike, rate, vol, t, is_call in batch:
            price(spot, strike, rate, vol, t, is_call)

    best_elapsed = float("inf")
    repeats = 10
    for _ in range(repeats):
        start = time.perf_counter()
        for spot, strike, rate, vol, t, is_call in batch:
            price(spot, strike, rate, vol, t, is_call)
        elapsed = time.perf_counter() - start
        best_elapsed = min(best_elapsed, elapsed)

    options_per_sec = count / best_elapsed
    us_per_option = best_elapsed * 1e6 / count

    print(f"batch size:  {count}")
    print(f"best of:     {repeats} repeats")
    print(f"elapsed:     {best_elapsed * 1000:.3f} ms")
    print(f"throughput:  {options_per_sec:,.0f} options/sec")
    print(f"avg latency: {us_per_option:.3f} us/option")


if __name__ == "__main__":
    main()
