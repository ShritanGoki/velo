# Real-Time Futures Trading Simulator

A multi-language trading system that connects to real, live ES (E-mini S&P
500) futures market data and runs it through a custom-built exchange, a
real-time risk engine, and a research/strategy layer — combining C++, Rust,
and Python, each chosen for the job it's actually best at in a real trading
system.

## Why This Project Exists

Every electronic market is underpinned by three things: a venue where orders
match into trades (an exchange), infrastructure that monitors risk and
market conditions in real time, and a research environment where strategies
get tested before they touch real capital. This project builds a simplified,
working version of all three, wired together, driven by real market data.

The goal isn't "a script that trades." It's a demonstration of how trading
infrastructure is actually decomposed by concern — execution, risk, and
research are different problems requiring different tools — and of building
each piece to a genuinely high standard rather than a toy level.

## Architecture

```
IBKR Paper Trading Account (live ES futures quotes, via ib_insync)
        │
        ▼
Python Strategy Layer  ──── orders ────►  C++ Matching Engine
   (reacts to live prices,                (limit order book,
    generates signals)                     price-time priority,
        ▲                                  simulated fills)
        │                                        │
        │                                     fills/quotes
        │                                        ▼
        └──────────────────────────  Rust Risk & Pricing Engine
                                      (P&L, position limits,
                                       Greeks, risk halts)
                                              │
                                              ▼
                                   Python Analytics / Dashboard
                                   (Sharpe, drawdown, trade log,
                                    risk-event log)
```

Components communicate over sockets (upgrading to ZeroMQ if needed) using a
lightweight binary protocol rather than JSON, since latency is a real design
concern, not just an implementation detail.

## Component Responsibilities

### C++ — Matching Engine
The simulated exchange. Maintains a live limit order book for ES futures,
matches incoming orders against resting liquidity using price-time priority,
and streams fills back out. Single-threaded and deterministic by design at
first — correctness before speed — with latency/throughput benchmarking
added once correctness is proven.

### Rust — Risk & Pricing Engine
Consumes the live fill/quote stream and continuously tracks position size,
unrealized/realized P&L, and volatility, enforcing configurable risk limits
(e.g., max exposure) with the ability to halt or force-cancel orders on
breach. Later extended with a vectorized (SIMD) options pricing module
(Black-Scholes + Greeks) to support a delta-hedging strategy, plus a
FIX-style message parser as a throughput benchmark. Rust is used here
specifically because this component must be both fast and unable to crash
mid-session — a genuine fit, not a resume-buzzword choice.

### Python — Strategy & Research Layer
Where trading strategies are written, backtested, and evaluated. Consumes
live or historical ES data, sends orders into the matching engine, and
produces the actual "is this any good" analysis: Sharpe ratio, max drawdown,
trade-level statistics. Includes a delta-hedging strategy that uses the Rust
pricing engine's live Greeks to rebalance an ES futures position against an
ES options position — the piece that ties all three languages together
through an actual quant concept rather than just data-passing.

## Data Sources

- **Historical replay (early milestones):** `yfinance` continuous ES futures
  bars, used to prove the pipeline works against real (if delayed/lower
  resolution) market history before adding live complexity.
- **Live data (later milestone):** Interactive Brokers paper trading account
  via `ib_insync` — real live ES futures quotes, zero capital risk, real
  (simulated) order routing available if wanted later.

Real order routing to a live exchange with real capital is explicitly out of
scope — the project stops at "real live data, simulated/paper execution,"
which is the standard way this kind of system gets demoed without
regulatory/financial liability.

## Milestone Roadmap

Each milestone is meant to be a complete, demoable checkpoint — the project
should be presentable at any milestone, not just at the very end.

| # | Milestone | What's added | Status |
|---|---|---|---|
| 1 | **Core order book** | Standalone C++ limit order book, hardcoded orders, prints fills | Done |
| 2 | **Engine as a live service** | Matching engine wrapped in a socket server, accepts orders over the wire | Done |
| 3 | **End-to-end loop (synthetic data)** | Python strategy sends real orders to the engine over sockets; full pipeline works with synthetic/random prices | Done |
| 4 | **Risk engine joins the pipeline** | Rust service consumes fills, tracks P&L/position, logs risk state | Done |
| 5 | **Real historical data** | Synthetic prices replaced with real ES history via `yfinance`; first real backtest report | Done |
| 6 | **Live IBKR data** | Historical replay replaced with live paper-trading feed | Done |
| 7 | **Depth: SIMD options pricing** | Vectorized Black-Scholes + Greeks in Rust, benchmarked vs. naive Python/Rust implementations | Planned |
| 8 | **Depth: delta-hedging strategy** | Python strategy rebalances ES futures against ES options using live Greeks; backtested with hedge-effectiveness metrics | Planned |
| 9 | **Depth: FIX-style parser** | Simulated FIX-format message parsing in Rust, throughput-benchmarked | Planned |
| 10 | **Polish** | Dashboard, README/benchmarks writeup, CI badges | Planned |

## Tech Stack

- **Languages:** C++, Rust, Python
- **C++:** matching engine, order book, latency/throughput benchmarks
- **Rust:** risk engine, SIMD options pricing, FIX-style parser, `rayon`/`tokio` for concurrency
- **Python:** strategy layer, backtesting, `pandas`/`NumPy`/`Matplotlib` analytics, `ib_insync` for live data
- **IPC:** raw TCP sockets or ZeroMQ, binary protocol
- **Data:** `yfinance` (historical), Interactive Brokers paper account (live)
- **Tooling:** GitHub Actions CI (build + test on push), unit tests per component

## Design Principles

1. **Correctness before speed.** Every component gets a simple, tested,
   single-threaded version working before any performance optimization.
2. **Every milestone is demoable.** No milestone leaves the system in a
   broken or partial state — each is a real, working checkpoint.
3. **Every "hero" feature has a number attached.** Not just "fast" — a
   specific throughput or latency figure, benchmarked and reproducible.
4. **Simulated capital, real data.** The system reacts to genuine live
   market conditions without financial or regulatory risk.

## Repository Structure

```
trading-sim/
├── engine/          # C++ matching engine
├── risk/            # Rust risk & pricing engine
├── strategy/        # Python strategy layer
├── docs/            # design docs, benchmark write-ups
│   └── orderbook-design-doc.md
└── README.md
```

## Current Status

Milestones 1–6 are done — see `docs/orderbook-design-doc.md`,
`docs/socket-service-design-doc.md`, `docs/synthetic-loop-design-doc.md`,
`docs/risk-engine-design-doc.md`, `docs/historical-backtest-design-doc.md`,
and `docs/live-ibkr-design-doc.md`. Milestone 7 (SIMD options pricing in
Rust) is designed but not yet implemented — see
`docs/options-pricing-design-doc.md`.
