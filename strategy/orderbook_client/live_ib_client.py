"""Real, ib_async-backed IBClient implementation. See docs/live-ibkr-design-doc.md §5.

Only imported when an actual live connection is wanted - the entire
automated test suite uses a fake IBClient instead (tests/test_live_feed.py,
tests/test_live_integration.py) and never imports this module.

ib_async runs on its own asyncio event loop, which has to keep spinning
for tick callbacks to ever fire - nothing about LiveIBKRFeed's plain
queue.get()-based next_price() does that. So this client owns a
dedicated background thread that: connects, qualifies the contract,
subscribes, and then runs that event loop forever via ib.run() - the
same "callback source feeding a queue on its own thread" shape
OrderBookClient's reader thread already uses (orderbook_client/client.py),
just with ib_async's event loop instead of a raw socket on the other end.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Callable

# Defaults to delayed (15-20 min) market data, not real-time: this runs
# against any paper account with zero data-entitlement setup, which is
# the deliberate default per docs/live-ibkr-design-doc.md §5 - real-time
# data is an opt-in argument, not a requirement.
DELAYED_MARKET_DATA_TYPE = 3


def default_es_contract(expiry: str):
    """expiry: YYYYMM or YYYYMMDD, e.g. '202609' - see ib_async.Future's
    lastTradeDateOrContractMonth. No auto-roll: one fixed contract per
    session (docs/live-ibkr-design-doc.md §5, §9)."""
    from ib_async import Future

    return Future("ES", expiry, "CME")


class IBAsyncClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1,
                 market_data_type: int = DELAYED_MARKET_DATA_TYPE, connect_timeout: float = 15.0):
        self._ib = None
        self._connect_error: BaseException | None = None
        self._connected = threading.Event()
        self._subscribe_requested = threading.Event()
        self._subscribed = threading.Event()
        self._pending_contract: Any = None
        self._pending_on_tick: Callable[[float], None] | None = None

        self._thread = threading.Thread(
            target=self._thread_main, args=(host, port, client_id, market_data_type), daemon=True)
        self._thread.start()

        if not self._connected.wait(timeout=connect_timeout):
            raise RuntimeError("timed out connecting to IBKR")
        if self._connect_error is not None:
            raise self._connect_error

    def _thread_main(self, host: str, port: int, client_id: int, market_data_type: int) -> None:
        import asyncio

        from ib_async import IB

        asyncio.set_event_loop(asyncio.new_event_loop())
        self._ib = IB()
        try:
            self._ib.connect(host, port, clientId=client_id)
            self._ib.reqMarketDataType(market_data_type)
        except BaseException as e:  # noqa: BLE001 - surfaced to the constructing thread, not swallowed
            self._connect_error = e
            self._connected.set()
            return
        self._connected.set()

        # Everything above is a one-time synchronous setup phase; from
        # here on, all further ib_async calls (qualify, reqMktData) also
        # happen on this same thread, before ib.run() takes over the loop
        # permanently - calling ib_async methods from another thread once
        # ib.run() owns the loop is not safe.
        self._subscribe_requested.wait()
        qualified = self._ib.qualifyContracts(self._pending_contract)[0]
        ticker = self._ib.reqMktData(qualified, "", False, False)

        on_tick = self._pending_on_tick

        def _on_update(tk) -> None:
            price = _best_price(tk)
            if price is not None:
                on_tick(price)

        ticker.updateEvent += _on_update
        self._subscribed.set()

        self._ib.run()  # pumps the event loop forever, until disconnect()

    def subscribe(self, contract: Any, on_tick: Callable[[float], None]) -> None:
        self._pending_contract = contract
        self._pending_on_tick = on_tick
        self._subscribe_requested.set()
        self._subscribed.wait(timeout=15.0)

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()


def _best_price(ticker) -> "float | None":
    """Prefers the last traded price; falls back to the bid/ask midpoint
    (trade prints can be sparse, especially on delayed data, while
    quotes update more often), then close. None if nothing is available
    yet (e.g. immediately after subscribing, before the first update)."""
    if not math.isnan(ticker.last):
        return ticker.last
    if not math.isnan(ticker.bid) and not math.isnan(ticker.ask):
        return (ticker.bid + ticker.ask) / 2
    if not math.isnan(ticker.close):
        return ticker.close
    return None
