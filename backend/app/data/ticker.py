"""Live price feed.

Two sources, deliberately:

* **Websocket** — Breeze pushes ticks as they happen. Near-instant, but the tick
  payload's field names and the identifier it uses for an instrument vary between
  segments and SDK versions, so a surprise here must not take the feed down.
* **REST polling** — `get_quotes` on a timer. Slower and rate-limit-bound, but
  the response shape is stable.

The websocket is treated as an upgrade over polling rather than a replacement.
If a tick arrives that cannot be attributed to a subscribed symbol, it is logged
once and dropped; polling keeps the prices moving regardless. The alternative —
trusting the stream alone — turns any payload change into a silently frozen
ticker, which is worse than a slow one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.data.breeze_client import BreezeClient, BreezeError

logger = logging.getLogger(__name__)

# Keys a tick or quote may use for the traded price, in order of preference.
PRICE_KEYS = ("last", "ltp", "last_traded_price", "close", "ltp_price")
# Keys that may identify the instrument.
SYMBOL_KEYS = ("stock_code", "symbol", "stock_name", "short_name")

# Conservative: Breeze rate-limits, and the whole universe is fetched per pass.
POLL_INTERVAL_SECONDS = 3.0


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass(slots=True)
class Quote:
    """Latest known price for one instrument."""

    symbol: str
    price: float
    previous_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)
    source: str = "poll"

    @property
    def change(self) -> float:
        return self.price - self.previous_close if self.previous_close else 0.0

    @property
    def change_pct(self) -> float:
        return (self.change / self.previous_close * 100) if self.previous_close else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": round(self.price, 2),
            "previous_close": round(self.previous_close, 2),
            "change": round(self.change, 2),
            "change_pct": round(self.change_pct, 2),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "volume": self.volume,
            "updated_at": self.updated_at.isoformat(),
            "source": self.source,
        }


class LiveTicker:
    """Maintains the latest price per symbol from the stream and/or polling."""

    def __init__(
        self,
        client: BreezeClient,
        symbols: list[str],
        on_update: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.client = client
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.on_update = on_update

        self._quotes: dict[str, Quote] = {}
        self._lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stream_active = False
        self._tick_count = 0
        self._unresolved_logged = False
        self._last_error = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        """Begin streaming and polling. Safe to call more than once."""
        if not self.client.is_connected:
            raise BreezeError("Cannot start the ticker before the Breeze session exists")

        self._stop.clear()
        self._start_stream()
        self._start_polling()

        return {
            "streaming": self._stream_active,
            "polling": self._poll_thread is not None and self._poll_thread.is_alive(),
            "symbols": self.symbols,
        }

    def _start_stream(self) -> None:
        if self._stream_active:
            return
        try:
            self.client.start_stream(self._on_tick)
            for symbol in self.symbols:
                self.client.subscribe(symbol)
            self._stream_active = True
            logger.info("Tick stream subscribed for %d symbols", len(self.symbols))
        except BreezeError as exc:
            # Polling covers this; a failed stream is a downgrade, not an outage.
            self._last_error = f"Stream unavailable, polling only: {exc}"
            logger.warning(self._last_error)

    def _start_polling(self) -> None:
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ticker-poll", daemon=True
        )
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream_active:
            for symbol in self.symbols:
                self.client.unsubscribe(symbol)
            self.client.stop_stream()
            self._stream_active = False

    def set_symbols(self, symbols: list[str]) -> None:
        """Replace the watch list, subscribing and unsubscribing as needed."""
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        added = [s for s in wanted if s not in self.symbols]
        removed = [s for s in self.symbols if s not in wanted]
        self.symbols = wanted

        if not self._stream_active:
            return
        for symbol in removed:
            self.client.unsubscribe(symbol)
        for symbol in added:
            try:
                self.client.subscribe(symbol)
            except BreezeError as exc:
                logger.warning("Could not subscribe %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # Websocket
    # ------------------------------------------------------------------
    def _on_tick(self, payload: Any) -> None:
        """Handle one streamed tick.

        Runs on the SDK's thread, so it must never raise — an exception here can
        tear down the stream for every symbol.
        """
        try:
            if not isinstance(payload, dict):
                return

            symbol = self._resolve_symbol(payload)
            price = _as_float(_first(payload, PRICE_KEYS))
            if symbol is None or price is None:
                if not self._unresolved_logged:
                    # Once only: a bad payload shape would otherwise flood the log
                    # at tick rate. One sample is enough to fix the mapping.
                    self._unresolved_logged = True
                    logger.warning(
                        "Dropping unattributable tick (polling still active). "
                        "Sample payload: %r",
                        payload,
                    )
                return

            self._record(
                symbol,
                price=price,
                previous_close=_as_float(payload.get("close")),
                open_=_as_float(payload.get("open")),
                high=_as_float(payload.get("high")),
                low=_as_float(payload.get("low")),
                volume=_as_float(payload.get("volume")) or 0.0,
                source="stream",
            )
            self._tick_count += 1
        except Exception:
            logger.exception("Tick handler failed; stream left running")

    def _resolve_symbol(self, payload: dict[str, Any]) -> str | None:
        """Map a tick to one of our subscribed symbols.

        Breeze may identify an instrument by stock code, by name, or by an
        exchange token like '4.1!2885'. Only confident matches are accepted;
        guessing would attribute a price to the wrong stock, which is worse than
        dropping the tick.
        """
        raw = _first(payload, SYMBOL_KEYS)
        if raw is None:
            # A single-symbol watch list is unambiguous.
            return self.symbols[0] if len(self.symbols) == 1 else None

        candidate = str(raw).strip().upper()
        if candidate in self.symbols:
            return candidate

        # Tolerate decorated forms such as 'NSE:TCS' or 'TCS-EQ'.
        for symbol in self.symbols:
            if symbol in candidate:
                return symbol
        return None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("Ticker poll pass failed")
            self._stop.wait(POLL_INTERVAL_SECONDS)

    def _poll_once(self) -> None:
        updated: list[dict[str, Any]] = []

        for symbol in list(self.symbols):
            if self._stop.is_set():
                return
            try:
                quote = self.client.get_quote(symbol)
            except BreezeError as exc:
                self._last_error = str(exc)
                continue

            price = _as_float(_first(quote, PRICE_KEYS))
            if price is None:
                continue

            # Do not let a poll overwrite a fresher streamed tick.
            existing = self._quotes.get(symbol)
            if existing and existing.source == "stream":
                age = (datetime.now() - existing.updated_at).total_seconds()
                if age < POLL_INTERVAL_SECONDS:
                    continue

            updated.append(
                self._record(
                    symbol,
                    price=price,
                    previous_close=_as_float(quote.get("previous_close"))
                    or _as_float(quote.get("prev_close")),
                    open_=_as_float(quote.get("open")),
                    high=_as_float(quote.get("high")),
                    low=_as_float(quote.get("low")),
                    volume=_as_float(quote.get("total_quantity_traded")) or 0.0,
                    source="poll",
                )
            )

        if updated and self.on_update:
            self.on_update(updated)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _record(
        self,
        symbol: str,
        *,
        price: float,
        previous_close: float | None = None,
        open_: float | None = None,
        high: float | None = None,
        low: float | None = None,
        volume: float = 0.0,
        source: str = "poll",
    ) -> dict[str, Any]:
        with self._lock:
            quote = self._quotes.get(symbol)
            if quote is None:
                quote = Quote(symbol=symbol, price=price)
                self._quotes[symbol] = quote

            quote.price = price
            quote.updated_at = datetime.now()
            quote.source = source
            # Streamed ticks often omit the reference fields; keep the last known.
            if previous_close:
                quote.previous_close = previous_close
            if open_:
                quote.open = open_
            if high:
                quote.high = max(high, quote.high)
            if low:
                quote.low = low if quote.low == 0 else min(low, quote.low)
            if volume:
                quote.volume = volume

            return quote.to_dict()

    def prices(self) -> dict[str, float]:
        with self._lock:
            return {s: q.price for s, q in self._quotes.items()}

    def quotes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [q.to_dict() for q in self._quotes.values()]

    def status(self) -> dict[str, Any]:
        return {
            "streaming": self._stream_active,
            "polling": self._poll_thread is not None and self._poll_thread.is_alive(),
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "ticks_received": self._tick_count,
            "symbols": self.symbols,
            "tracked": len(self._quotes),
            "last_error": self._last_error,
        }
