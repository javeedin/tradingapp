"""Live trading engine.

Drives the same `SignalEngine` / `RiskManager` / `Broker` trio the backtester
uses, on a timer aligned to the candle interval. It is deliberately a *polling*
loop over completed candles rather than a tick-driven one: decisions are made on
closed bars, which is what the backtest replays, so live and test behaviour stay
comparable. Acting on partial bars would make the two diverge in ways no
backtest could predict.

The engine never places an order without a stop, never trades outside market
hours, and stops entering for the day once the loss limit trips.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta
from typing import Any

import pandas as pd

from app.broker.base import Broker, BrokerError
from app.broker.paper import PaperBroker
from app.config import settings
from app.data.breeze_client import BreezeClient, BreezeError, SessionExpiredError
from app.data.store import MarketStore
from app.models import (
    DEFAULT_INTRADAY_PRODUCT,
    ExitReason,
    ProductType,
    Side,
    SignalAction,
)
from app.risk.manager import RiskManager
from app.strategy import indicators
from app.strategy.signals import SignalEngine

logger = logging.getLogger(__name__)

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Bars of history kept in memory per symbol — enough for the 200-EMA warm-up
# with headroom.
CONTEXT_BARS = 400

# How far back to backfill on a cold start, per interval.
BACKFILL_DAYS = {
    "1minute": 10,
    "5minute": 30,
    "30minute": 120,
    "1day": 900,
}


def is_market_open(now: datetime | None = None) -> bool:
    """NSE cash-market hours, weekdays only.

    Does not know about trading holidays — Breeze simply returns no new candles
    on those days, which the loop handles as "nothing to do".
    """
    current = now or datetime.now()
    if current.weekday() >= 5:
        return False
    return MARKET_OPEN <= current.time() <= MARKET_CLOSE


class LiveTrader:
    """Orchestrates data, signals, risk, and execution during market hours."""

    def __init__(
        self,
        broker: Broker | None = None,
        client: BreezeClient | None = None,
        store: MarketStore | None = None,
        engine: SignalEngine | None = None,
        risk: RiskManager | None = None,
        symbols: list[str] | None = None,
        product: ProductType = DEFAULT_INTRADAY_PRODUCT,
        intraday: bool = True,
    ) -> None:
        self.client = client or BreezeClient()
        self.store = store or MarketStore()
        self.engine = engine or SignalEngine()
        self.risk = risk or RiskManager()
        self.broker: Broker = broker or PaperBroker()
        self.symbols = symbols or settings.universe
        self.product = product
        # Squaring off is a strategy choice, independent of the Breeze product.
        self.intraday = intraday
        self.interval = settings.candle_interval
        self.benchmark_code = settings.benchmark_code

        self._running = False
        self._lock = threading.Lock()
        self._last_cycle: datetime | None = None
        self._last_error: str = ""
        self._cycle_count = 0
        self._latest_signals: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def connect(self, session_token: str | None = None) -> None:
        """Establish the Breeze session. Must be re-run each trading day."""
        self.client.connect(session_token)
        self._last_error = ""
        self._log_event("session", "Breeze session established")

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def backfill(self, days: int | None = None) -> dict[str, int]:
        """Download history for the universe and benchmark into the store.

        Resumes from the newest stored bar rather than refetching everything,
        so a restart mid-session is cheap.
        """
        lookback = days or BACKFILL_DAYS.get(self.interval, 30)
        results: dict[str, int] = {}
        now = datetime.now()

        for symbol in [*self.symbols, self.benchmark_code]:
            try:
                latest = self.store.latest_candle_time(symbol, self.interval)
                start = (
                    latest + timedelta(minutes=1)
                    if latest
                    else now - timedelta(days=lookback)
                )
                if start >= now:
                    results[symbol] = 0
                    continue

                exchange = "NSE"
                candles = self.client.get_historical_data(
                    stock_code=symbol,
                    from_date=start,
                    to_date=now,
                    interval=self.interval,
                    exchange_code=exchange,
                )
                results[symbol] = self.store.save_candles(
                    symbol, candles, self.interval, exchange
                )
            except SessionExpiredError:
                raise
            except BreezeError as exc:
                logger.error("Backfill failed for %s: %s", symbol, exc)
                results[symbol] = 0

        total = sum(results.values())
        self._log_event("backfill", f"Backfilled {total} candles across {len(results)} symbols")
        return results

    def _context(self, symbol: str) -> pd.DataFrame:
        return self.store.load_candles(symbol, self.interval, limit=CONTEXT_BARS)

    # ------------------------------------------------------------------
    # Trading cycle
    # ------------------------------------------------------------------
    def run_cycle(self, now: datetime | None = None) -> dict[str, Any]:
        """One decision pass over the universe.

        Serialised by a lock: cycles can outlast their interval when the API is
        slow, and two overlapping passes could double-size a position.
        """
        if not self._lock.acquire(blocking=False):
            return {"skipped": "previous cycle still running"}

        try:
            return self._run_cycle_locked(now or datetime.now())
        finally:
            self._lock.release()

    def _run_cycle_locked(self, now: datetime) -> dict[str, Any]:
        self._cycle_count += 1
        outcome: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "cycle": self._cycle_count,
            "actions": [],
        }

        if not is_market_open(now):
            outcome["skipped"] = "market closed"
            return outcome

        if not self.is_connected:
            outcome["skipped"] = "no Breeze session"
            self._last_error = "Breeze session not established"
            return outcome

        # 1. Pull the latest completed candles.
        try:
            self._refresh_recent()
        except SessionExpiredError as exc:
            self._last_error = str(exc)
            outcome["error"] = str(exc)
            self._log_event("error", f"Session expired: {exc}")
            return outcome
        except BreezeError as exc:
            self._last_error = str(exc)
            outcome["error"] = str(exc)
            return outcome

        # 2. Mark open positions and act on stops/targets.
        outcome["actions"].extend(self._manage_positions(now))

        # 3. Force intraday flat at the cutoff, then stop for the day.
        if self.intraday and self.risk.past_squareoff(now):
            closed = self.broker.close_all(self._last_prices(), ExitReason.SQUAREOFF, now)
            for trade in closed:
                self.risk.record_pnl(trade.net_pnl, now)
                self.store.save_trade(trade, self.broker.mode)
                outcome["actions"].append(
                    {"action": "squareoff", "symbol": trade.symbol, "pnl": trade.net_pnl}
                )
            if closed:
                self._log_event("squareoff", f"Squared off {len(closed)} intraday positions")
            outcome["squareoff"] = True
            self._last_cycle = now
            return outcome

        # 4. Stop opening new risk if the daily loss limit tripped.
        if self.risk.check_daily_limit(self.broker.equity, now):
            outcome["halted"] = self.risk.halted_reason
            self._last_cycle = now
            return outcome

        # 5. Score the universe and act.
        benchmark = self._context(self.benchmark_code)
        bench_frame = benchmark if not benchmark.empty else None

        for symbol in self.symbols:
            try:
                action = self._process_symbol(symbol, bench_frame, now)
                if action:
                    outcome["actions"].append(action)
            except BrokerError as exc:
                logger.error("Order error on %s: %s", symbol, exc)
                outcome["actions"].append({"symbol": symbol, "error": str(exc)})
            except Exception as exc:  # keep one bad symbol from killing the cycle
                logger.exception("Unhandled error processing %s", symbol)
                outcome["actions"].append({"symbol": symbol, "error": repr(exc)})

        self._last_cycle = now
        self._last_error = ""
        return outcome

    def _refresh_recent(self) -> None:
        """Fetch and store candles since the last stored bar."""
        now = datetime.now()
        for symbol in [*self.symbols, self.benchmark_code]:
            latest = self.store.latest_candle_time(symbol, self.interval)
            start = latest - timedelta(minutes=5) if latest else now - timedelta(days=5)
            candles = self.client.get_historical_data(
                stock_code=symbol,
                from_date=start,
                to_date=now,
                interval=self.interval,
            )
            if candles:
                self.store.save_candles(symbol, candles, self.interval)

    def _manage_positions(self, now: datetime) -> list[dict[str, Any]]:
        """Update prices, trail stops, and close anything that hit a level."""
        actions: list[dict[str, Any]] = []

        for symbol, position in self.broker.positions.items():
            frame = self._context(symbol)
            if frame.empty:
                continue

            enriched = indicators.enrich(frame)
            last = enriched.iloc[-1]
            price = float(last["close"])
            position.update_price(price)

            atr_value = float(last.get("atr") or 0.0)
            if atr_value > 0 and self.risk.update_trailing_stop(position, atr_value):
                # A live broker holds the stop at the exchange; move it there too.
                updater = getattr(self.broker, "update_stop", None)
                if callable(updater):
                    updater(symbol, position.stoploss)
                actions.append(
                    {
                        "action": "trail_stop",
                        "symbol": symbol,
                        "new_stop": round(position.stoploss, 2),
                    }
                )

            exit_check = self.risk.check_exit(
                position,
                high=float(last["high"]),
                low=float(last["low"]),
                now=now,
                intraday=self.intraday,
            )
            if exit_check is None:
                continue

            reason, exit_price = exit_check
            trade = self.broker.close_position(symbol, exit_price, reason, now)
            if trade is not None:
                self.risk.record_pnl(trade.net_pnl, now)
                self.store.save_trade(trade, self.broker.mode)
                actions.append(
                    {
                        "action": "exit",
                        "symbol": symbol,
                        "reason": reason.value,
                        "pnl": round(trade.net_pnl, 2),
                    }
                )
                self._log_event(
                    "exit", f"{symbol} closed ({reason.value}) net ₹{trade.net_pnl:,.2f}"
                )

        return actions

    def _process_symbol(
        self, symbol: str, benchmark: pd.DataFrame | None, now: datetime
    ) -> dict[str, Any] | None:
        frame = self._context(symbol)
        if frame.empty or len(frame) < indicators.warmup_period():
            return None

        signal = self.engine.evaluate(frame, symbol, benchmark=benchmark)
        if signal is None:
            return None

        self._latest_signals[symbol] = signal.to_dict()
        self.store.save_signal(signal)

        position = self.broker.get_position(symbol)

        # Existing position: exit when conviction decays or flips.
        if position is not None:
            if self.engine.should_exit(signal, position.side.sign):
                trade = self.broker.close_position(
                    symbol, signal.price, ExitReason.SIGNAL, now
                )
                if trade is not None:
                    self.risk.record_pnl(trade.net_pnl, now)
                    self.store.save_trade(trade, self.broker.mode)
                    self._log_event(
                        "exit", f"{symbol} closed (signal) net ₹{trade.net_pnl:,.2f}"
                    )
                    return {
                        "action": "exit",
                        "symbol": symbol,
                        "reason": "signal",
                        "pnl": round(trade.net_pnl, 2),
                    }
            return None

        if signal.action is SignalAction.HOLD:
            return None

        # New entry.
        side = Side.BUY if signal.action is SignalAction.BUY else Side.SELL
        sizing = self.risk.size_position(
            entry=signal.price,
            atr=signal.atr,
            side=side,
            equity=self.broker.equity,
            available_cash=self.broker.cash,
            open_positions=self.broker.open_position_count,
        )
        if not sizing.approved:
            return {"action": "rejected", "symbol": symbol, "reason": sizing.reason}

        place = self.broker.buy if side is Side.BUY else self.broker.sell
        order = place(
            symbol=symbol,
            quantity=sizing.quantity,
            price=signal.price,
            stoploss=sizing.stoploss,
            target=sizing.target,
            product=self.product,
            timestamp=now,
        )

        self._log_event(
            "entry",
            f"{side.value.upper()} {symbol} x{sizing.quantity} @ ₹{signal.price:,.2f} "
            f"(score {signal.score:+.2f}, stop ₹{sizing.stoploss:,.2f})",
        )

        return {
            "action": "entry",
            "symbol": symbol,
            "side": side.value,
            "quantity": sizing.quantity,
            "price": signal.price,
            "stoploss": round(sizing.stoploss, 2),
            "target": round(sizing.target, 2),
            "score": signal.score,
            "status": order.status.value,
            "message": order.message,
            "sizing": sizing.to_dict(),
        }

    def _last_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol in self.broker.positions:
            frame = self._context(symbol)
            if not frame.empty:
                prices[symbol] = float(frame.iloc[-1]["close"])
        return prices

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def kill_switch(self) -> dict[str, Any]:
        """Flatten everything and stop entering. The panic button."""
        closed = self.broker.close_all(self._last_prices(), ExitReason.KILL_SWITCH)
        for trade in closed:
            self.risk.record_pnl(trade.net_pnl)
            self.store.save_trade(trade, self.broker.mode)

        self.risk.halt("Kill switch activated")
        self._log_event("kill_switch", f"Kill switch: flattened {len(closed)} positions")

        return {
            "closed": len(closed),
            "trades": [t.to_dict() for t in closed],
            "halted": True,
        }

    def resume(self) -> None:
        self.risk.resume()
        self._log_event("resume", "Trading resumed")

    def start_day(self) -> None:
        self.risk.reset_day()
        self._log_event("start_day", "New trading day — counters reset")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def _log_event(self, kind: str, message: str) -> None:
        event = {
            "timestamp": datetime.now().isoformat(),
            "kind": kind,
            "message": message,
        }
        self._events.append(event)
        # Bound the buffer; this is a UI feed, not an audit log (the store holds
        # the durable record of trades and signals).
        if len(self._events) > 300:
            self._events = self._events[-300:]
        logger.info("[%s] %s", kind, message)

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(reversed(self._events))

    @property
    def latest_signals(self) -> list[dict[str, Any]]:
        return sorted(
            self._latest_signals.values(),
            key=lambda s: abs(float(s.get("score", 0))),
            reverse=True,
        )

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.broker.mode,
            "connected": self.is_connected,
            "market_open": is_market_open(),
            "session_age_hours": (
                round(self.client.session_age_hours, 2)
                if self.client.session_age_hours is not None
                else None
            ),
            "symbols": self.symbols,
            "interval": self.interval,
            "product": self.product.value,
            "intraday": self.intraday,
            "cycles": self._cycle_count,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "last_error": self._last_error,
            "account": self.broker.summary(),
            "risk": self.risk.status(self.broker.equity),
        }
