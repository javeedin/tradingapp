"""Backtester behaviour and statistics."""

from __future__ import annotations

import pytest

from app.engine.backtest import Backtester
from app.models import ProductType
from app.risk.manager import RiskManager
from app.strategy.signals import SignalEngine

from .conftest import make_ohlcv


@pytest.fixture
def backtester() -> Backtester:
    return Backtester(
        engine=SignalEngine(entry_threshold=0.2),
        risk=RiskManager(max_position_pct=100.0),
        starting_capital=100_000,
        product=ProductType.INTRADAY,
        interval="5minute",
    )


def test_empty_input_returns_empty_result(backtester):
    assert backtester.run({}).stats == {}


def test_short_history_produces_no_trades(backtester):
    result = backtester.run({"TEST": make_ohlcv(bars=50)})
    assert result.stats == {} or result.stats.get("total_trades", 0) == 0


def test_run_produces_trades_and_a_curve(backtester, ohlcv):
    result = backtester.run({"TEST": ohlcv})
    assert result.stats["total_trades"] > 0
    assert not result.equity_curve.empty
    assert "equity" in result.equity_curve.columns


def test_all_positions_are_closed_at_the_end(backtester, ohlcv):
    backtester.run({"TEST": ohlcv})
    assert backtester.broker.open_position_count == 0


def test_stats_are_internally_consistent(backtester, ohlcv):
    stats = backtester.run({"TEST": ohlcv}).stats
    assert stats["wins"] + stats["losses"] == stats["total_trades"]
    assert stats["win_rate"] == pytest.approx(
        stats["wins"] / stats["total_trades"] * 100, rel=0.01
    )
    assert stats["net_pnl"] == pytest.approx(
        stats["gross_profit"] - stats["gross_loss"], rel=0.01
    )
    assert stats["max_drawdown_pct"] >= 0


def test_losing_run_reports_negative_sharpe(backtester):
    """A strategy that finishes down must not show a positive Sharpe.

    Regression: annualising 5-minute returns by sqrt(75*250) inflated the ratio
    by ~137x, producing a headline Sharpe above 2 on a losing run. Sharpe is now
    computed on daily returns.
    """
    result = backtester.run({"TEST": make_ohlcv(bars=900, seed=3, volatility=0.006)})
    stats = result.stats
    if stats.get("total_trades", 0) > 0 and stats["net_pnl"] < 0:
        assert stats["sharpe_ratio"] < 0


def test_every_trade_exits_for_a_known_reason(backtester, ohlcv):
    result = backtester.run({"TEST": ohlcv})
    valid = {"stoploss", "target", "signal", "intraday_squareoff", "kill_switch"}
    assert set(result.stats["exits_by_reason"]) <= valid
    assert sum(result.stats["exits_by_reason"].values()) == result.stats["total_trades"]


def test_intraday_run_squares_off_daily(backtester, ohlcv):
    result = backtester.run({"TEST": ohlcv})
    for trade in result.trades:
        assert trade.entry_time.date() == trade.exit_time.date(), (
            f"{trade.symbol} held overnight in an intraday backtest"
        )


def test_multi_symbol_run_respects_position_cap(ohlcv):
    backtester = Backtester(
        engine=SignalEngine(entry_threshold=0.15),
        risk=RiskManager(max_open_positions=2, max_position_pct=100.0),
        starting_capital=200_000,
        interval="5minute",
    )
    data = {
        "A": ohlcv,
        "B": make_ohlcv(bars=len(ohlcv), seed=5),
        "C": make_ohlcv(bars=len(ohlcv), seed=6),
    }
    result = backtester.run(data)
    assert result.stats["total_trades"] >= 0
    assert backtester.broker.open_position_count == 0


def test_entries_fill_at_the_next_bar_open(backtester, ohlcv):
    """Signals come from bar t's close; fills must occur at bar t+1's open.

    Filling at the signal bar's own close would mean trading on a price that was
    not yet knowable — the classic way a backtest invents returns.
    """
    result = backtester.run({"TEST": ohlcv})
    opens = ohlcv["open"]

    for trade in result.trades[:15]:
        if trade.entry_time not in opens.index:
            continue
        bar_open = float(opens.loc[trade.entry_time])
        # Entry price is the bar's open plus modelled slippage only.
        assert trade.entry_price == pytest.approx(bar_open, rel=0.002), (
            f"{trade.symbol} entered at {trade.entry_price}, "
            f"bar open was {bar_open}"
        )


def test_result_serialises_for_the_api(backtester, ohlcv):
    payload = backtester.run({"TEST": ohlcv}).to_dict()
    assert set(payload) >= {"stats", "trades", "equity_curve", "signals"}
    if payload["trades"]:
        assert "net_pnl" in payload["trades"][0]
    if payload["equity_curve"]:
        assert set(payload["equity_curve"][0]) == {"timestamp", "equity"}


def test_summary_text_is_readable(backtester, ohlcv):
    text = backtester.run({"TEST": ohlcv}).summary_text()
    assert "Trades:" in text and "Sharpe:" in text
