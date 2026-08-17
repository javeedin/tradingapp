"""Event-driven backtester.

Runs the exact `SignalEngine`, `RiskManager`, and `PaperBroker` used in live
trading, so a strategy cannot behave differently in test than in production.

Two decisions keep the results honest:

* **Next-bar execution.** A signal computed from bar *t*'s close is filled at
  bar *t+1*'s open. Filling at the close of the bar that generated the signal
  means trading on a price you could not have known yet — the single most common
  way backtests report returns that never materialise.
* **Stops resolve before targets.** When one bar's range spans both levels, OHLC
  data cannot say which came first, so the loss is assumed.

Even with both, results here remain optimistic: there is no modelling of
liquidity, queue position, partial fills, gap-through-stop slippage, or the
statutory charges beyond brokerage.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.broker.paper import PaperBroker
from app.config import settings
from app.models import ExitReason, ProductType, Side, SignalAction, Trade
from app.risk.manager import RiskManager
from app.strategy import indicators
from app.strategy.signals import SignalEngine

logger = logging.getLogger(__name__)

# NSE trading days per year, used to annualise the daily Sharpe ratio.
TRADING_DAYS_PER_YEAR = 250


@dataclass(slots=True)
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: dict[str, Any] = field(default_factory=dict)
    signals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stats": self.stats,
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": (
                [
                    {"timestamp": ts.isoformat(), "equity": round(float(eq), 2)}
                    for ts, eq in self.equity_curve["equity"].items()
                ]
                if not self.equity_curve.empty
                else []
            ),
            "signals": self.signals,
        }

    def summary_text(self) -> str:
        s = self.stats
        if not s:
            return "No results — the backtest produced no trades."
        return (
            f"Trades: {s['total_trades']} | "
            f"Win rate: {s['win_rate']}% | "
            f"Net P&L: ₹{s['net_pnl']:,.2f} | "
            f"Return: {s['total_return_pct']}% | "
            f"Max DD: {s['max_drawdown_pct']}% | "
            f"Sharpe: {s['sharpe_ratio']} | "
            f"Profit factor: {s['profit_factor']}"
        )


class Backtester:
    """Replays historical bars through the live decision path."""

    def __init__(
        self,
        engine: SignalEngine | None = None,
        risk: RiskManager | None = None,
        starting_capital: float | None = None,
        product: ProductType = ProductType.INTRADAY,
        interval: str | None = None,
    ) -> None:
        self.engine = engine or SignalEngine()
        self.risk = risk or RiskManager()
        self.starting_capital = (
            starting_capital if starting_capital is not None else settings.starting_capital
        )
        self.product = product
        self.interval = interval or settings.candle_interval
        self.broker = PaperBroker(self.starting_capital)

    # ------------------------------------------------------------------
    def run(
        self,
        data: dict[str, pd.DataFrame],
        benchmark: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Replay `data` (symbol -> OHLCV frame) and return the results."""
        if not data:
            return BacktestResult(stats={})

        self.broker.reset()
        self.risk.reset_day()

        enriched = {
            symbol: indicators.enrich(frame)
            for symbol, frame in data.items()
            if frame is not None and not frame.empty
        }
        if not enriched:
            return BacktestResult(stats={})

        bench = (
            indicators.enrich(benchmark)
            if benchmark is not None and not benchmark.empty
            else None
        )

        timeline = sorted({ts for frame in enriched.values() for ts in frame.index})
        warmup = indicators.warmup_period()
        if len(timeline) <= warmup:
            logger.warning(
                "Only %d bars supplied; %d are needed for indicator warm-up",
                len(timeline),
                warmup,
            )
            return BacktestResult(stats={})

        equity_points: list[tuple[datetime, float]] = []
        recorded_signals: list[dict[str, Any]] = []
        # Signals from bar t, to be executed at bar t+1's open.
        pending: dict[str, Any] = {}
        current_day = None

        for index, timestamp in enumerate(timeline):
            if index < warmup:
                continue

            if current_day != timestamp.date():
                current_day = timestamp.date()
                self.risk.reset_day(timestamp)

            bars = self._bars_at(enriched, timestamp)
            if not bars:
                continue

            # 1. Fill anything queued from the previous bar, at this bar's open.
            self._execute_pending(pending, bars, timestamp)
            pending.clear()

            # 2. Mark positions and resolve stops/targets against this bar.
            self.broker.update_prices({s: b["close"] for s, b in bars.items()})
            closed = self.broker.check_stops(
                {s: (b["high"], b["low"]) for s, b in bars.items()}, timestamp
            )
            for trade in closed:
                self.risk.record_pnl(trade.net_pnl, timestamp)

            # 3. Force intraday positions flat at the cutoff.
            if self.product.is_intraday and self.risk.past_squareoff(timestamp):
                for trade in self.broker.close_all(
                    {s: b["close"] for s, b in bars.items()},
                    ExitReason.SQUAREOFF,
                    timestamp,
                ):
                    self.risk.record_pnl(trade.net_pnl, timestamp)
                equity_points.append((timestamp, self.broker.equity))
                continue

            # 4. Tighten trailing stops.
            for symbol, position in self.broker.positions.items():
                if symbol in bars:
                    self.risk.update_trailing_stop(position, bars[symbol].get("atr", 0.0))

            # 5. Score each symbol and queue orders for the next bar.
            if not self.risk.check_daily_limit(self.broker.equity, timestamp):
                bench_window = self._benchmark_window(bench, timestamp)
                for symbol in bars:
                    decision = self._evaluate(
                        enriched[symbol], symbol, timestamp, bench_window
                    )
                    if decision is None:
                        continue
                    recorded_signals.append(decision.to_dict())
                    if decision.is_actionable or self.broker.has_position(symbol):
                        pending[symbol] = decision

            equity_points.append((timestamp, self.broker.equity))

        # Flatten anything still open at the end of the data.
        final_bars = self._bars_at(enriched, timeline[-1])
        self.broker.close_all(
            {s: b["close"] for s, b in final_bars.items()},
            ExitReason.SQUAREOFF,
            timeline[-1],
        )
        # Record equity *after* the final flatten, so the curve reflects the
        # closing costs of the last trades rather than stopping just before them.
        equity_points.append((timeline[-1], self.broker.equity))

        curve = pd.DataFrame(equity_points, columns=["timestamp", "equity"]).set_index(
            "timestamp"
        )
        curve = curve[~curve.index.duplicated(keep="last")]

        return BacktestResult(
            trades=self.broker.trades,
            equity_curve=curve,
            stats=self._compute_stats(self.broker.trades, curve),
            signals=recorded_signals,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _bars_at(
        enriched: dict[str, pd.DataFrame], timestamp: datetime
    ) -> dict[str, pd.Series]:
        bars: dict[str, pd.Series] = {}
        for symbol, frame in enriched.items():
            if timestamp in frame.index:
                bars[symbol] = frame.loc[timestamp]
        return bars

    @staticmethod
    def _benchmark_window(
        bench: pd.DataFrame | None, timestamp: datetime
    ) -> pd.DataFrame | None:
        if bench is None:
            return None
        window = bench.loc[:timestamp]
        return window if not window.empty else None

    def _evaluate(
        self,
        frame: pd.DataFrame,
        symbol: str,
        timestamp: datetime,
        bench_window: pd.DataFrame | None,
    ):
        # Slice strictly up to the current bar — never beyond.
        window = frame.loc[:timestamp]
        if len(window) < 2:
            return None
        return self.engine.evaluate(window, symbol, benchmark=bench_window, enrich=False)

    def _execute_pending(
        self,
        pending: dict[str, Any],
        bars: dict[str, pd.Series],
        timestamp: datetime,
    ) -> None:
        """Act on the previous bar's signals, filling at this bar's open."""
        for symbol, signal in pending.items():
            bar = bars.get(symbol)
            if bar is None:
                continue

            fill_price = float(bar["open"])
            if fill_price <= 0:
                continue

            position = self.broker.get_position(symbol)

            if position is not None:
                # Exit when conviction has decayed or flipped against us.
                if self.engine.should_exit(signal, position.side.sign):
                    trade = self.broker.close_position(
                        symbol, fill_price, ExitReason.SIGNAL, timestamp
                    )
                    if trade is not None:
                        self.risk.record_pnl(trade.net_pnl, timestamp)
                continue

            if signal.action is SignalAction.HOLD:
                continue

            side = Side.BUY if signal.action is SignalAction.BUY else Side.SELL
            sizing = self.risk.size_position(
                entry=fill_price,
                atr=signal.atr,
                side=side,
                equity=self.broker.equity,
                available_cash=self.broker.cash,
                open_positions=self.broker.open_position_count,
            )
            if not sizing.approved:
                continue

            place = self.broker.buy if side is Side.BUY else self.broker.sell
            place(
                symbol=symbol,
                quantity=sizing.quantity,
                price=fill_price,
                stoploss=sizing.stoploss,
                target=sizing.target,
                product=self.product,
                timestamp=timestamp,
            )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    def _compute_stats(
        self, trades: list[Trade], curve: pd.DataFrame
    ) -> dict[str, Any]:
        if not trades:
            return {
                "total_trades": 0,
                "net_pnl": 0.0,
                "total_return_pct": 0.0,
                "win_rate": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "profit_factor": 0.0,
                "note": "No trades were generated — try lowering SIGNAL_ENTRY_THRESHOLD.",
            }

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        gross_profit = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        net_pnl = sum(t.net_pnl for t in trades)
        total_costs = sum(t.costs for t in trades)

        win_rate = len(wins) / len(trades) * 100
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0

        # Expectancy: average rupees per trade given the win/loss mix.
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        max_dd, sharpe = self._curve_metrics(curve)

        by_reason: dict[str, int] = {}
        for t in trades:
            by_reason[t.exit_reason.value] = by_reason.get(t.exit_reason.value, 0) + 1

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "total_costs": round(total_costs, 2),
            "starting_capital": round(self.starting_capital, 2),
            "ending_equity": round(self.starting_capital + net_pnl, 2),
            "total_return_pct": round(net_pnl / self.starting_capital * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 0.0,
            "best_trade": round(max(t.net_pnl for t in trades), 2),
            "worst_trade": round(min(t.net_pnl for t in trades), 2),
            "avg_holding_minutes": round(
                sum(t.holding_period_minutes for t in trades) / len(trades), 1
            ),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "exits_by_reason": by_reason,
        }

    def _curve_metrics(self, curve: pd.DataFrame) -> tuple[float, float]:
        """Max drawdown (%) and annualised Sharpe from the equity curve.

        Sharpe is computed on **daily** returns, not on raw bars. Annualising
        5-minute returns by sqrt(75 x 250) multiplies by ~137, which turns a
        negligible positive drift into a headline Sharpe above 2 even when the
        strategy finishes down — the arithmetic mean of bar returns stays
        positive while the compounded curve falls (volatility drag). Resampling
        to daily first is the standard convention and removes that distortion.
        """
        if curve.empty or len(curve) < 2:
            return 0.0, 0.0

        equity = curve["equity"].astype(float)

        # Drawdown is measured on the full-resolution curve — intraday troughs
        # are real drawdowns and should not be smoothed away by resampling.
        running_peak = equity.cummax()
        drawdown = (equity - running_peak) / running_peak.replace(0, np.nan) * 100
        max_dd = abs(float(drawdown.min())) if not drawdown.isna().all() else 0.0

        daily_equity = (
            equity.resample("1D").last().dropna()
            if isinstance(equity.index, pd.DatetimeIndex)
            else equity
        )
        if len(daily_equity) < 3:
            # Too short to say anything meaningful about risk-adjusted return.
            return max_dd, 0.0

        returns = daily_equity.pct_change().dropna()
        if returns.empty or returns.std(ddof=1) == 0:
            return max_dd, 0.0

        sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
        return max_dd, sharpe if math.isfinite(sharpe) else 0.0
